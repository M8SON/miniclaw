"""
Tool loop for MiniClaw.

Owns the Anthropic request cycle, tool execution, and response extraction for
one user message.
"""

import json
import logging

import anthropic

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
        max_rounds: int = 10,
    ):
        self.client = client
        self.model = model
        self.skill_loader = skill_loader
        self.container_manager = container_manager
        self.conversation_state = conversation_state
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

        tool_definitions = self.skill_loader.get_tool_definitions()
        rounds = 0

        while rounds < self.max_rounds:
            rounds += 1

            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_prompt,
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

    def _extract_text(self, response) -> str:
        """Extract text content from Claude's response."""
        parts = []
        for block in response.content:
            if block.type == "text":
                parts.append(block.text)
        return " ".join(parts)
