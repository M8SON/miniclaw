"""
Orchestrator - Central coordinator for MiniClaw.

Connects the voice interface, skill system, container execution,
and Claude API into a single loop:

  Voice In → Whisper → Claude (with skill tools) → Container Execution → Claude → Kokoro TTS → Voice Out

This replaces the monolithic voice_assistant.py with a modular system
where capabilities are defined by skill files and executed in containers.
"""

import logging
import os
from pathlib import Path

import anthropic

from core.skill_loader import SkillLoader
from core.container_manager import ContainerManager
from core.conversation_state import ConversationState
from core.memory_provider import MemoryProvider
from core.prompt_builder import PromptBuilder
from core.skill_selector import SkillSelector
from core.tool_loop import ToolLoop

logger = logging.getLogger(__name__)


def _parse_float(value: str | None, default: float) -> float:
    """Parse a float env var, falling back to default on invalid values."""
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid float value %r — using default %.1f", value, default)
        return default


class Orchestrator:
    """
    Main coordinator for MiniClaw.

    Responsibilities:
      - Load and manage skills
      - Maintain conversation history with Claude
      - Route tool calls to the container manager
      - Handle the tool-use loop (multiple rounds if needed)
    """

    def __init__(
        self,
        anthropic_api_key: str,
        model: str = "claude-sonnet-4-5-20250929",
        skill_paths: list[Path] | None = None,
        container_memory: str = "256m",
        conversation_max_messages: int | None = 24,
        conversation_max_tokens: int | None = 6000,
        memory_max_tokens: int | None = 2000,
        memory_recall_max_tokens: int | None = 600,
        skill_prompt_max_tokens: int | None = 4000,
        skill_select_top_k: int = 2,
    ):
        # Claude client
        self.client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.model = model

        # Load skills
        self.skill_loader = SkillLoader(search_paths=skill_paths)
        self.skills = self.skill_loader.load_all()

        # Semantic skill selector — indexes skills at startup
        self.skill_selector = SkillSelector(top_k=skill_select_top_k)
        self.skill_selector.index(self.skills)

        # Container manager
        self.container_manager = ContainerManager(memory_limit=container_memory)

        # Conversation state
        self.conversation_state = ConversationState(
            max_messages=conversation_max_messages,
            max_tokens=conversation_max_tokens,
        )

        # Prompt context providers
        self.memory_provider = MemoryProvider(
            max_tokens=memory_max_tokens,
            recall_max_tokens=memory_recall_max_tokens,
        )
        self.prompt_builder = PromptBuilder(
            memory_provider=self.memory_provider,
            max_skill_tokens=skill_prompt_max_tokens,
            skill_selector=self.skill_selector,
        )
        self.tool_loop = ToolLoop(
            client=self.client,
            model=self.model,
            skill_loader=self.skill_loader,
            container_manager=self.container_manager,
            conversation_state=self.conversation_state,
            memory_provider=self.memory_provider,
        )

        # Startup context (date/time/weather) stored separately so
        # per-request prompts can append it after semantic skill selection.
        self._startup_context: str = ""

        # Static prompt for internal calls (greet, close_session) that
        # have no user_message to drive semantic selection.
        self.system_prompt = self._build_system_prompt()

        # Tiered intelligence — optional, gated by OLLAMA_ENABLED env var.
        # When disabled, all requests go through Claude's ToolLoop unchanged.
        self._tier_router = None
        self._ollama_tool_loop = None
        if os.getenv("OLLAMA_ENABLED", "false").lower() == "true":
            from core.tier_router import TierRouter
            from core.ollama_tool_loop import OllamaToolLoop
            _patterns_path = Path(__file__).parent.parent / "config" / "intent_patterns.yaml"
            _claude_only = {
                s.strip() for s in os.getenv("CLAUDE_ONLY_SKILLS", "install_skill").split(",")
            }
            self._tier_router = TierRouter(
                patterns_path=_patterns_path,
                skill_selector=self.skill_selector,
                claude_only_skills=_claude_only,
            )
            self._ollama_tool_loop = OllamaToolLoop(
                host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
                model=os.getenv("OLLAMA_MODEL", "phi4-mini"),
                skill_loader=self.skill_loader,
                container_manager=self.container_manager,
                conversation_state=self.conversation_state,
                memory_provider=self.memory_provider,
                timeout_seconds=_parse_float(os.getenv("OLLAMA_TIMEOUT_SECONDS"), default=8.0),
            )
            logger.info(
                "Tiered routing enabled: ollama_model=%s, claude_only=%s",
                os.getenv("OLLAMA_MODEL", "phi4-mini"),
                _claude_only,
            )

        logger.info(
            "Orchestrator ready: model=%s, skills=%d, selector=%s",
            self.model,
            len(self.skills),
            "active" if self.skill_selector.available else "unavailable",
        )

    def _build_system_prompt(self, user_message: str | None = None) -> str:
        """Build the system prompt, optionally scoped to a user message."""
        prompt = self.prompt_builder.build(
            skills=self.skills,
            skipped_skills=self.skill_loader.skipped_skills,
            invalid_skills=self.skill_loader.invalid_skills,
            user_message=user_message,
        )
        if self._startup_context:
            prompt += f"\n--- Current Context ---\n{self._startup_context}\n"
        return prompt

    def process_message(self, user_message: str) -> str:
        """Process a user message through the tiered intelligence stack."""
        if self._tier_router is None:
            # OLLAMA_ENABLED=false — Claude-only path, unchanged.
            system_prompt = self._build_system_prompt(user_message=user_message)
            return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)

        route = self._tier_router.route(user_message)
        logger.debug("TierRouter: %s → tier=%s", user_message[:60], route.tier)

        if route.tier == "direct":
            return self._execute_direct(route, user_message)

        system_prompt = self._build_system_prompt(user_message=user_message)

        if route.tier == "claude":
            return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)

        # Ollama tier
        from core.ollama_tool_loop import EscalateSignal, EscalateWithContext
        result = self._ollama_tool_loop.run(
            user_message=user_message, system_prompt=system_prompt
        )
        if result is EscalateSignal:
            logger.info("OllamaToolLoop escalated → Claude (no tools ran)")
            return self.tool_loop.run(
                user_message=user_message, system_prompt=system_prompt
            )
        if isinstance(result, EscalateWithContext):
            logger.info(
                "OllamaToolLoop escalated with %d tool(s) → Claude finalize",
                len(result.tool_activity),
            )
            return self._claude_finalize_ollama_turn(
                user_message, result.tool_activity, system_prompt
            )
        return result

    def _claude_finalize_ollama_turn(
        self,
        user_message: str,
        tool_activity: list[dict],
        system_prompt: str,
    ) -> str:
        """
        Finalize a turn where Ollama ran tools but couldn't produce a response.

        Commits the user message and tool activity to ConversationState in
        Anthropic format, then asks Claude to summarize the results without
        re-executing any tools.
        """
        if not tool_activity:
            logger.warning(
                "_claude_finalize_ollama_turn: called with empty tool_activity — falling back to Claude"
            )
            return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)

        # OllamaToolLoop does NOT write to ConversationState on escalation paths,
        # so we are responsible for the full turn: user message → tool_use → tool_result.
        # Commit the user message
        self.conversation_state.append_user_text(user_message)

        # Commit the tool_use assistant turn (synthetic Anthropic format)
        tool_use_blocks = [
            {
                "type": "tool_use",
                "id": f"ollama_{i}",
                "name": activity["name"],
                "input": activity["args"],
            }
            for i, activity in enumerate(tool_activity)
        ]
        self.conversation_state.append_assistant_content(tool_use_blocks)

        # Commit the tool_result user turn
        tool_result_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": f"ollama_{i}",
                "content": activity["result"],
            }
            for i, activity in enumerate(tool_activity)
        ]
        self.conversation_state.append_tool_results(tool_result_blocks)

        # Ask Claude to produce a final spoken response — no tools offered
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt,
            messages=self.conversation_state.select_messages_for_prompt(),
        )

        response_text = " ".join(
            block.text for block in response.content if block.type == "text"
        )
        if response_text:
            self.conversation_state.append_assistant_content(
                [{"type": "text", "text": response_text}]
            )
        self.conversation_state.prune()

        logger.info(
            "_claude_finalize_ollama_turn: finalized with %d tool(s)", len(tool_activity)
        )
        return response_text or "Done."

    def _execute_direct(self, route, user_message: str) -> str:
        """Execute a dispatch-pattern route without any LLM involvement."""
        if route.action == "close_session":
            return self.close_session()

        if route.skill:
            skill = self.skills.get(route.skill)
            if skill:
                result = self.container_manager.execute_skill(skill, route.args)
                return result or "Done."

        # Dispatch resolution failed — build prompt lazily and fall back to Claude
        logger.warning(
            "_execute_direct: could not resolve skill=%r, falling back to Claude",
            route.skill,
        )
        system_prompt = self._build_system_prompt(user_message=user_message)
        return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)

    def reload_skills(self):
        """Re-scan skill directories and rebuild the system prompt with any new skills."""
        self.skills = self.skill_loader.load_all()
        self.skill_selector.index(self.skills)
        self.system_prompt = self._build_system_prompt()
        logger.info("Skills reloaded: %d skills active", len(self.skills))

    def greet(self) -> str:
        """Generate a contextual opening greeting based on startup context and memory."""
        return self.tool_loop.run(
            user_message=(
                "You have just started up. Based on the current time, day, and anything "
                "you know about Mason from memory, say a brief natural greeting. "
                "One or two sentences. Do not end with a question."
            ),
            system_prompt=self.system_prompt,
        )

    def inject_startup_context(self, context: str) -> None:
        """Append date/time/weather context to the system prompt before the first turn."""
        if context.strip():
            self._startup_context = context
            self.system_prompt = self._build_system_prompt()

    def close_session(self) -> str:
        """
        End the current session: save anything worth remembering, then say goodbye.

        Sends a final internal message so Claude can call save_memory if the
        conversation contained anything worth keeping, then returns a spoken goodbye.
        """
        if not self.conversation_state.messages:
            return "Goodbye!"

        return self.tool_loop.run(
            user_message=(
                "The user is ending this conversation. "
                "If anything worth remembering came up — a preference, a project detail, "
                "something to keep in mind for next time — use save_memory to save it now. "
                "Then say a brief, warm goodbye."
            ),
            system_prompt=self.system_prompt,
        )

    def reset_conversation(self):
        """Clear conversation history."""
        self.conversation_state.clear()
        logger.info("Conversation history cleared")

    def list_skills(self) -> list[dict]:
        """Return a summary of loaded skills for diagnostics."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "format": s.execution_config.get("type", "docker"),
                "dir": s.skill_dir,
            }
            for s in self.skills.values()
        ]
