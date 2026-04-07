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

    def run(self, user_message: str, system_prompt: str) -> str:
        """
        Process a user message through Claude with tool support.

        Handles the full tool-use loop:
          1. Send message + tools to Claude
          2. If Claude wants to use a tool, execute it
          3. Send result back to Claude
          4. Repeat until Claude produces a final text response
        """
        self.conversation_state.append_user_text(user_message)
        effective_system_prompt = self._augment_system_prompt(
            system_prompt=system_prompt,
            user_message=user_message,
        )

        tool_definitions = self.skill_loader.get_tool_definitions()
        rounds = 0

        while rounds < self.max_rounds:
            rounds += 1

            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=effective_system_prompt,
                messages=self.conversation_state.select_messages_for_prompt(),
                tools=tool_definitions if tool_definitions else anthropic.NOT_GIVEN,
            )

            if response.stop_reason == "tool_use":
                tool_results = self._handle_tool_calls(response)
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
            self.conversation_state.prune()
            return response_text

        logger.warning("Max tool rounds reached (%d)", self.max_rounds)
        self.conversation_state.prune()
        return "I ran into an issue processing that request. Could you try again?"

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

    def _handle_tool_calls(self, response) -> list[dict]:
        """Execute tool calls from Claude's response."""
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
