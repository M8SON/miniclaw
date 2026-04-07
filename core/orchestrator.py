"""
Orchestrator - Central coordinator for MiniClaw.

Connects the voice interface, skill system, container execution,
and Claude API into a single loop:

  Voice In → Whisper → Claude (with skill tools) → Container Execution → Claude → Kokoro TTS → Voice Out

This replaces the monolithic voice_assistant.py with a modular system
where capabilities are defined by skill files and executed in containers.
"""

import logging
from pathlib import Path

import anthropic

from core.skill_loader import SkillLoader
from core.container_manager import ContainerManager
from core.conversation_state import ConversationState
from core.memory_provider import MemoryProvider
from core.prompt_builder import PromptBuilder
from core.tool_loop import ToolLoop

logger = logging.getLogger(__name__)


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
    ):
        # Claude client
        self.client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.model = model

        # Load skills
        self.skill_loader = SkillLoader(search_paths=skill_paths)
        self.skills = self.skill_loader.load_all()

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
        )
        self.tool_loop = ToolLoop(
            client=self.client,
            model=self.model,
            skill_loader=self.skill_loader,
            container_manager=self.container_manager,
            conversation_state=self.conversation_state,
            memory_provider=self.memory_provider,
        )

        # System prompt - tells Claude what it is and how to use skills
        self.system_prompt = self.prompt_builder.build(
            skills=self.skills,
            skipped_skills=self.skill_loader.skipped_skills,
            invalid_skills=self.skill_loader.invalid_skills,
        )

        logger.info(
            "Orchestrator ready: model=%s, skills=%d",
            self.model,
            len(self.skills),
        )

    def process_message(self, user_message: str) -> str:
        """Process a user message through Claude with tool support."""
        return self.tool_loop.run(user_message=user_message, system_prompt=self.system_prompt)

    def reload_skills(self):
        """Re-scan skill directories and rebuild the system prompt with any new skills."""
        self.skills = self.skill_loader.load_all()
        self.system_prompt = self.prompt_builder.build(
            skills=self.skills,
            skipped_skills=self.skill_loader.skipped_skills,
            invalid_skills=self.skill_loader.invalid_skills,
        )
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
            self.system_prompt += f"\n--- Current Context ---\n{context}\n"

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
