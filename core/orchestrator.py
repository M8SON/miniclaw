"""
Orchestrator - Central coordinator for Kaizen.

Connects the voice interface, skill system, container execution,
and Claude API into a single loop:

  Voice In → Whisper → Claude (with skill tools) → Container Execution → Claude → Kokoro TTS → Voice Out

This replaces the monolithic voice_assistant.py with a modular system
where capabilities are defined by skill files and executed in containers.
"""

import contextlib
import logging
import os
import queue as _queue
from pathlib import Path

import anthropic

from core import profiling
from core.skill_loader import SkillLoader
from core.container_manager import ContainerManager
from core.conversation_state import ConversationState
from core.memory_provider import MemoryProvider
from core.prompt_builder import PromptBuilder
from core.session_archive import SessionArchive
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
    Main coordinator for Kaizen.

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
        archive: SessionArchive | None = None,
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

        # --- scheduler hooks ---
        self.scheduled_fire_queue: _queue.Queue = _queue.Queue()
        self.pending_next_wake_announcements: list[str] = []
        # Injected from main.py — None in text/test mode means "don't speak".
        self.speak_callback = None
        self.is_conversation_active = lambda: False
        self.scheduler_log_path: Path = Path.home() / ".kaizen" / "scheduler.log"

        # Static prompt for internal calls (greet, close_session) that
        # have no user_message to drive semantic selection.
        self.system_prompt = self._build_system_prompt()

        # Tiered intelligence — optional, gated by MICRO_TIER_ENABLED env var.
        # When disabled, all requests go through the full ToolLoop unchanged.
        # When enabled, trivial requests route to a Haiku micro-tier with a
        # slim prompt and top-K filtered tools (~few hundred input tokens,
        # sub-second on cloud); Sonnet handles complex / ambiguous turns.
        self._tier_router = None
        self._micro_loop = None
        if os.getenv("MICRO_TIER_ENABLED", "false").lower() == "true":
            from core.tier_router import TierRouter
            _patterns_path = Path(__file__).parent.parent / "config" / "intent_patterns.yaml"
            _claude_only = {
                s.strip() for s in os.getenv("CLAUDE_ONLY_SKILLS", "install-skill").split(",")
            }
            self._tier_router = TierRouter(
                patterns_path=_patterns_path,
                skill_selector=self.skill_selector,
                claude_only_skills=_claude_only,
            )
            micro_model = os.getenv("MICRO_TIER_MODEL", "claude-haiku-4-5")
            self._micro_loop = ToolLoop(
                client=self.client,
                model=micro_model,
                skill_loader=self.skill_loader,
                container_manager=self.container_manager,
                conversation_state=self.conversation_state,
                memory_provider=self.memory_provider,
                skill_selector=self.skill_selector,  # top-K filter
                max_tokens=512,                       # micro tier — keep it tight
                max_rounds=5,                         # tools should resolve fast
            )
            logger.info(
                "Tiered routing enabled: micro_model=%s, claude_only=%s",
                micro_model, _claude_only,
            )

        logger.info(
            "Orchestrator ready: model=%s, skills=%d, selector=%s",
            self.model,
            len(self.skills),
            "active" if self.skill_selector.available else "unavailable",
        )

        # Session archive (optional — None means "no archive").
        self.archive = archive
        self._current_session_id: int | None = None

    def start_session(self, mode: str) -> None:
        """Begin a new archived conversation arc. Idempotent — second call ends
        any existing session first."""
        if self.archive is None:
            return
        if self._current_session_id is not None:
            self.end_session()
        sid = self.archive.start_session(mode)
        self._current_session_id = sid if sid else None

    def end_session(self) -> None:
        """Finalize the current archived session and reset state."""
        if self.archive is None or self._current_session_id is None:
            return
        try:
            self.archive.end_session(self._current_session_id)
        except Exception:
            logger.exception("end_session failed")
        self._current_session_id = None

    def _archive_callback(
        self, user_message: str, tool_activity: list[dict], response_text: str
    ) -> None:
        """Append a completed turn to the archive. No-op if archive disabled."""
        if self.archive is None or self._current_session_id is None:
            return
        try:
            sid = self._current_session_id
            self.archive.append_turn(sid, "user", user_message)
            for activity in tool_activity:
                summary = self._format_tool_summary(activity)
                self.archive.append_turn(sid, "tool", summary, tool_name=activity["name"])
            if response_text:
                self.archive.append_turn(sid, "assistant", response_text)
        except Exception:
            logger.exception("_archive_callback failed")

    def _format_tool_summary(self, activity: dict) -> str:
        """Render a tool call as a one-line summary for the archive."""
        import json as _json
        try:
            input_str = _json.dumps(activity.get("input") or {}, separators=(",", ":"))
        except (TypeError, ValueError):
            input_str = str(activity.get("input"))
        result = str(activity.get("result", ""))
        if len(input_str) > 80:
            input_str = input_str[:77] + "..."
        if len(result) > 120:
            result = result[:117] + "..."
        return f"{activity.get('name','?')}({input_str}) -> {result}"

    def _build_system_prompt(self, user_message: str | None = None) -> str:
        """Build the system prompt as a single string (legacy callers)."""
        stable, dynamic = self._build_system_prompt_split(user_message=user_message)
        return stable + dynamic

    def _build_system_prompt_split(
        self, user_message: str | None = None
    ) -> tuple[str, str]:
        """Build the system prompt as (stable, dynamic) for prompt caching.

        stable carries persona + memory + skipped/invalid + self-update + startup
        context — byte-stable across turns in a session. dynamic carries the
        selector-driven skill context, which changes per user message.
        """
        stable, dynamic = self.prompt_builder.build_cacheable_parts(
            skills=self.skills,
            skipped_skills=self.skill_loader.skipped_skills,
            invalid_skills=self.skill_loader.invalid_skills,
            user_message=user_message,
        )
        if self._startup_context:
            stable += f"\n--- Current Context ---\n{self._startup_context}\n"
        return stable, dynamic

    def drain_pending_announcements(self) -> list[str]:
        """Return queued next_wake announcements in FIFO order, clearing them."""
        drained = list(self.pending_next_wake_announcements)
        self.pending_next_wake_announcements.clear()
        return drained

    def process_scheduled_fire(self, fire) -> str | None:
        """
        Execute a scheduled fire through the tool loop and dispatch its
        output based on delivery mode. Never raises — a crash here must
        not take down the voice loop.
        """
        entry = fire.entry
        try:
            system_prompt = self._build_system_prompt(user_message=entry.prompt)
            output = self.tool_loop.run(
                user_message=entry.prompt,
                system_prompt=system_prompt,
            )
        except Exception:
            logger.exception("scheduled fire %s failed during tool loop", entry.id)
            return None

        delivery = entry.delivery
        if delivery == "immediate" and self.is_conversation_active():
            delivery = "next_wake"  # concurrency downgrade

        if delivery == "immediate":
            if self.speak_callback is not None:
                try:
                    self.speak_callback(output)
                except Exception:
                    logger.exception("speak_callback failed for schedule %s", entry.id)
            else:
                logger.info("[sched %s immediate, no speak_callback] %s", entry.id, output)
                return output
        elif delivery == "next_wake":
            self.pending_next_wake_announcements.append(output)
        elif delivery == "silent":
            try:
                self.scheduler_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.scheduler_log_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"[{fire.fired_at.isoformat()}] {entry.id}: {output}\n")
            except Exception:
                logger.exception("failed writing silent-schedule log for %s", entry.id)
        else:
            logger.warning("unknown delivery mode %r for schedule %s", delivery, entry.id)
        return None

    def process_message(self, user_message: str, on_chunk=None, on_ack_success=None) -> str:
        """Process a user message through the tiered intelligence stack.

        on_chunk: optional Callable[[str], None]. When provided, both the
        full Claude (Sonnet) path and the Haiku micro-tier path stream
        text deltas through it as they arrive — both reuse ToolLoop and
        therefore inherit its streaming support. Direct routes (skill
        dispatches, session-close patterns) deliver the assembled string
        as a single delta so the caller's TTS feed has a uniform shape
        regardless of which tier handled the turn.
        """
        # Reuse the outer profiling.turn() if the voice loop already opened
        # one; otherwise own the scope so text-mode turns still produce a
        # [TIMING-SUMMARY] line.
        outer = profiling._current_turn.get()
        ctx = contextlib.nullcontext() if outer is not None else profiling.turn()
        with ctx:
            return self._process_message(user_message, on_chunk=on_chunk, on_ack_success=on_ack_success)

    def _process_message(self, user_message: str, on_chunk=None, on_ack_success=None) -> str:
        if self._tier_router is None:
            stable, dynamic = self._build_system_prompt_split(user_message=user_message)
            return self.tool_loop.run(
                user_message=user_message,
                system_prompt=stable,
                system_prompt_dynamic=dynamic,
                archive_callback=self._archive_callback,
                on_chunk=on_chunk,
            )

        route = self._tier_router.route(user_message)
        logger.info("TierRouter: %s → tier=%s", user_message[:60], route.tier)

        if route.tier == "direct":
            result = self._execute_direct(route, user_message, on_ack_success=on_ack_success)
            if on_chunk is not None and result:
                on_chunk(result)
            return result

        if route.tier == "claude":
            stable, dynamic = self._build_system_prompt_split(user_message=user_message)
            return self.tool_loop.run(
                user_message=user_message,
                system_prompt=stable,
                system_prompt_dynamic=dynamic,
                archive_callback=self._archive_callback,
                on_chunk=on_chunk,
            )

        # Micro tier — Haiku with a slim system prompt and top-K filtered tools.
        # Reuses the same ToolLoop machinery as the full Claude path, so
        # streaming, conversation state, archive, and tool execution all work
        # identically. On error / unexpected response, fall through to Sonnet.
        micro_system_prompt = self.prompt_builder.build_for_micro_tier()
        try:
            return self._micro_loop.run(
                user_message=user_message,
                system_prompt=micro_system_prompt,
                archive_callback=self._archive_callback,
                on_chunk=on_chunk,
            )
        except Exception:
            logger.exception("Micro tier failed → escalating to Claude")
            stable, dynamic = self._build_system_prompt_split(user_message=user_message)
            return self.tool_loop.run(
                user_message=user_message,
                system_prompt=stable,
                system_prompt_dynamic=dynamic,
                archive_callback=self._archive_callback,
                on_chunk=on_chunk,
            )

    def _execute_direct(self, route, user_message: str, on_ack_success=None) -> str:
        """Execute a dispatch-pattern route without any LLM involvement.

        If `on_ack_success` is provided and the skill returns a string in
        MUSIC_CONTROL_ACK_SUCCESS, the callback is invoked (the voice layer
        plays an ack chime) and `""` is returned to signal "TTS handled by
        side channel, don't speak."""
        if route.action == "close_session":
            return self.close_session()

        if route.skill:
            skill = self.skills.get(route.skill)
            if skill:
                result = self.container_manager.execute_skill(skill, route.args)
                from core.container_manager import MUSIC_CONTROL_ACK_SUCCESS
                if result in MUSIC_CONTROL_ACK_SUCCESS and on_ack_success is not None:
                    on_ack_success()
                    return ""
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
        """Generate a contextual opening greeting based on startup context.

        Calls Claude directly with a slim system prompt — no skill bodies,
        no persisted memories, no tools — since the greeting only needs
        persona + date/time/weather to produce one warm sentence. Going
        through tool_loop with the full prompt cost ~6k input tokens per
        cold start for ~20 tokens of output."""
        system_prompt = self.prompt_builder.build_for_greeting(self._startup_context)
        user_message = (
            "You have just started up. Based on the current time and day, "
            "say a brief natural greeting. One or two sentences. "
            "Do not end with a question."
        )
        with profiling.stage("llm_claude"):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=200,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
        text = " ".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        return text or "Hello."

    def inject_startup_context(self, context: str) -> None:
        """Append date/time/weather context to the system prompt before the first turn."""
        if context.strip():
            self._startup_context = context
            self.system_prompt = self._build_system_prompt()

    def close_session(self) -> str:
        """
        End the current session: save anything worth remembering, then say goodbye.

        Sends a final internal message so Claude can call save-memory if the
        conversation contained anything worth keeping, then returns a spoken goodbye.
        """
        if not self.conversation_state.messages:
            self.end_session()
            return "Goodbye!"

        close_message = (
            "The user is ending this conversation. "
            "If anything worth remembering came up — a preference, a project detail, "
            "something to keep in mind for next time — use save-memory to save it now. "
            "Then say a brief, warm goodbye."
        )
        stable, dynamic = self._build_system_prompt_split(user_message=close_message)
        response = self.tool_loop.run(
            user_message=close_message,
            system_prompt=stable,
            system_prompt_dynamic=dynamic,
            archive_callback=self._archive_callback,
        )
        self.end_session()
        return response

    def reset_conversation(self):
        """Clear conversation history and end any open archive session."""
        self.end_session()
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
