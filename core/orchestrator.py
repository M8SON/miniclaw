"""
Orchestrator - Central coordinator for MiniClaw.

Connects the voice interface, skill system, container execution,
and Claude API into a single loop:

  Voice In → Whisper → Claude (with skill tools) → Container Execution → Claude → Piper TTS → Voice Out

This replaces the monolithic voice_assistant.py with a modular system
where capabilities are defined by skill files and executed in containers.
"""

import os
import sys
import json
import logging
import anthropic
from pathlib import Path
from dotenv import load_dotenv

from core.skill_loader import SkillLoader
from core.container_manager import ContainerManager

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

    MAX_TOOL_ROUNDS = 10

    def __init__(
        self,
        anthropic_api_key: str,
        model: str = "claude-sonnet-4-5-20250929",
        skill_paths: list[Path] | None = None,
        container_memory: str = "256m",
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
        self.conversation_history: list[dict] = []

        # System prompt - tells Claude what it is and how to use skills
        self.system_prompt = self._build_system_prompt()

        logger.info(
            "Orchestrator ready: model=%s, skills=%d",
            self.model,
            len(self.skills),
        )

    def _build_system_prompt(self) -> str:
        """
        Build the system prompt including skill instructions.

        Each skill's markdown body is appended so Claude understands
        *when* and *how* to use each tool, not just the tool schema.
        """
        base_prompt = (
            "You are a helpful voice assistant running on a Raspberry Pi. "
            "You have access to various tools provided as skills. "
            "Keep responses concise and conversational since they will be "
            "spoken aloud via text-to-speech.\n\n"
            "Guidelines:\n"
            "- Never use asterisks, emojis, or markdown formatting\n"
            "- Speak naturally and conversationally\n"
            "- Keep responses concise for spoken delivery\n"
            "- When using tools, explain what you are doing naturally\n"
            "- Summarize tool results conversationally\n"
        )

        if self.skills:
            base_prompt += "\n--- Available Skills ---\n"
            for skill in self.skills.values():
                base_prompt += f"\n### {skill.name}\n{skill.instructions}\n"

        return base_prompt

    def process_message(self, user_message: str) -> str:
        """
        Process a user message through Claude with tool support.

        Handles the full tool-use loop:
          1. Send message + tools to Claude
          2. If Claude wants to use a tool, execute it via container
          3. Send result back to Claude
          4. Repeat until Claude produces a final text response

        Args:
            user_message: Transcribed text from the user

        Returns:
            Claude's final text response (ready for TTS)
        """
        self.conversation_history.append(
            {"role": "user", "content": user_message}
        )

        tool_definitions = self.skill_loader.get_tool_definitions()
        rounds = 0

        while rounds < self.MAX_TOOL_ROUNDS:
            rounds += 1

            # Call Claude
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self.system_prompt,
                messages=self.conversation_history,
                tools=tool_definitions if tool_definitions else anthropic.NOT_GIVEN,
            )

            # Check if Claude wants to use tools
            if response.stop_reason == "tool_use":
                tool_results = self._handle_tool_calls(response)

                # Add assistant response and tool results to history
                self.conversation_history.append(
                    {"role": "assistant", "content": response.content}
                )
                self.conversation_history.append(
                    {"role": "user", "content": tool_results}
                )
            else:
                # Final response - extract text
                response_text = self._extract_text(response)

                self.conversation_history.append(
                    {"role": "assistant", "content": response.content}
                )

                logger.info(
                    "Response ready: %d rounds, %d input / %d output tokens",
                    rounds,
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )
                return response_text

        logger.warning("Max tool rounds reached (%d)", self.MAX_TOOL_ROUNDS)
        return "I ran into an issue processing that request. Could you try again?"

    def _handle_tool_calls(self, response) -> list[dict]:
        """Execute tool calls from Claude's response via containers."""

        tool_results = []

        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_input = block.input

            logger.info("Tool call: %s(%s)", tool_name, json.dumps(tool_input)[:200])

            # Look up the skill
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

    def reset_conversation(self):
        """Clear conversation history."""
        self.conversation_history = []
        logger.info("Conversation history cleared")

    def list_skills(self) -> list[dict]:
        """Return a summary of loaded skills for diagnostics."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "format": "native",
                "dir": s.skill_dir,
            }
            for s in self.skills.values()
        ]
