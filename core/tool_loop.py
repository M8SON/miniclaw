"""
Tool loop for MiniClaw.

Owns the Anthropic request cycle, tool execution, and response extraction for
one user message.
"""

import json
import logging
import re

import anthropic

_REMEMBER_RE = re.compile(
    r"\n?##\s*remember:\n+topic:\s*(.+?)\n+content:\s*(.+?)(?=\n##|\Z)",
    re.IGNORECASE | re.DOTALL,
)

CHECKPOINT_INTERVAL = 15

CHECKPOINT_NUDGE = (
    "[CHECKPOINT — {n} tool calls in this turn]\n"
    "Step back briefly: in the calls so far, did any skill route on a phrasing\n"
    "that isn't in its SKILL.md? Did you correct a misroute? If so, call\n"
    "update_skill_hints now before continuing the user's request."
)

logger = logging.getLogger(__name__)


class ToolLoop:
    """Execute the Claude tool-use loop for a single user message."""

    def __init__(
        self,
        client,
        model: str,
        skill_loader,
        container_manager,
        conversation_state,
        memory_provider=None,
        max_rounds: int = 10,
    ):
        self.client = client
        self.model = model
        self.skill_loader = skill_loader
        self.container_manager = container_manager
        self.conversation_state = conversation_state
        self.memory_provider = memory_provider
        self.max_rounds = max_rounds

    def run(
        self,
        user_message: str,
        system_prompt: str,
        archive_callback=None,
    ) -> str:
        """
        Process a user message through Claude with tool support.

        archive_callback: optional Callable[[str, list[dict], str], None].
        Called once per completed turn with (user_message, tool_activity,
        response_text). tool_activity is a list of {"name", "input", "result"}
        dicts, one per tool call this turn (in order). Fires before prune.
        """
        if hasattr(self.container_manager, "start_turn"):
            self.container_manager.start_turn()
        self.conversation_state.append_user_text(user_message)
        effective_system_prompt = self._augment_system_prompt(
            system_prompt=system_prompt,
            user_message=user_message,
        )

        tool_definitions = self.skill_loader.get_tool_definitions()
        tool_activity: list[dict] = []
        rounds = 0
        last_nudged_at = 0

        while rounds < self.max_rounds:
            rounds += 1

            # Build per-round system prompt — checkpoint nudge if we just
            # crossed a multiple of CHECKPOINT_INTERVAL since last nudge.
            tool_count = len(tool_activity)
            current_checkpoint = (tool_count // CHECKPOINT_INTERVAL) * CHECKPOINT_INTERVAL
            if (
                current_checkpoint > last_nudged_at
                and current_checkpoint > 0
                and self._any_opted_in_skill()
            ):
                round_system = (
                    effective_system_prompt
                    + "\n\n"
                    + CHECKPOINT_NUDGE.format(n=current_checkpoint)
                )
                last_nudged_at = current_checkpoint
            else:
                round_system = effective_system_prompt

            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=round_system,
                messages=self.conversation_state.select_messages_for_prompt(),
                tools=tool_definitions if tool_definitions else anthropic.NOT_GIVEN,
            )

            if response.stop_reason == "tool_use":
                tool_results = self._handle_tool_calls(response, tool_activity)
                self.conversation_state.append_assistant_content(response.content)
                self.conversation_state.append_tool_results(tool_results)
                continue

            response_text = self._extract_text(response)
            self.conversation_state.append_assistant_content(response.content)

            logger.info(
                "Response ready: %d rounds, %d input / %d output tokens",
                rounds,
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            if archive_callback is not None:
                try:
                    archive_callback(user_message, tool_activity, response_text)
                except Exception:
                    logger.exception("archive_callback failed")
            self.conversation_state.prune()
            return response_text

        logger.warning("Max tool rounds reached (%d)", self.max_rounds)
        if archive_callback is not None:
            try:
                archive_callback(user_message, tool_activity, "")
            except Exception:
                logger.exception("archive_callback failed")
        self.conversation_state.prune()
        return "I ran into an issue processing that request. Could you try again?"

    def _any_opted_in_skill(self) -> bool:
        for s in self.skill_loader.skills.values():
            fm = getattr(s, "frontmatter", None) or {}
            allow = (
                fm.get("metadata", {}).get("miniclaw", {})
                  .get("self_update", {}).get("allow_body")
            )
            if allow is True:
                return True
        return False

    def _augment_system_prompt(self, system_prompt: str, user_message: str) -> str:
        """Attach live memory recall relevant to the current user message."""
        if not self.memory_provider:
            return system_prompt

        recalled = self.memory_provider.recall_for_message(user_message)
        if not recalled:
            return system_prompt

        return (
            f"{system_prompt}\n"
            "\n--- Relevant Memory Recall ---\n"
            "Use this as supporting memory for the current turn. Verify details against it "
            "before making claims about prior preferences, projects, or past events.\n"
            f"{recalled}\n"
        )

    def _handle_tool_calls(self, response, tool_activity: list[dict]) -> list[dict]:
        """Execute tool calls from Claude's response, appending to tool_activity."""
        tool_results = []

        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_input = block.input
            logger.info("Tool call: %s(%s)", tool_name, json.dumps(tool_input)[:200])

            skill = self.skill_loader.get_skill(tool_name)
            if skill:
                result = self.container_manager.execute_skill(skill, tool_input)
                result = self._extract_and_save_remember(result)
            else:
                result = f"Unknown tool: {tool_name}"

            tool_activity.append({
                "name": tool_name,
                "input": tool_input,
                "result": result,
            })

            logger.info("Tool result: %s", result[:200])
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                }
            )

        return tool_results

    def _extract_and_save_remember(self, result: str) -> str:
        """Strip ## remember: blocks from skill output and file them to the memory vault."""
        if not self.memory_provider or "## remember:" not in result.lower():
            return result

        cleaned = result
        for match in _REMEMBER_RE.finditer(result):
            topic = match.group(1).strip()
            content = match.group(2).strip()
            if topic and content:
                filename = self.memory_provider.save_note(topic, content)
                if filename:
                    logger.info("Skill filed memory: %s", filename)
            cleaned = cleaned.replace(match.group(0), "")

        return cleaned.strip() or "Skill completed with no output"

    def _extract_text(self, response) -> str:
        """Extract text content from Claude's response."""
        parts = []
        for block in response.content:
            if block.type == "text":
                parts.append(block.text)
        return " ".join(parts)
