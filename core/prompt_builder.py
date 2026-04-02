"""
Prompt builder for MiniClaw.

Assembles the system prompt from static assistant policy, persisted memories,
and skill instructions.
"""

import json

from core.memory_provider import MemoryProvider


class PromptBuilder:
    """Build the full system prompt used for Claude requests."""

    BASE_PROMPT = (
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
        "- Your input comes from a speech-to-text system and may contain "
        "transcription errors. If a request seems garbled, unclear, or does "
        "not make sense as spoken language, repeat back what you heard and "
        "ask for clarification before acting. For example: 'I heard confirm "
        "point, did you mean confirm restart?' or 'I caught something about "
        "X but I am not sure, could you repeat that?'\n"
    )

    def __init__(
        self,
        memory_provider: MemoryProvider | None = None,
        max_skill_tokens: int | None = 4000,
    ):
        self.memory_provider = memory_provider or MemoryProvider()
        self.max_skill_tokens = max_skill_tokens

    def build(self, skills: dict, skipped_skills: dict, invalid_skills: dict | None = None) -> str:
        """
        Build the system prompt including memories and skill instructions.

        Each skill's markdown body is appended so Claude understands when and
        how to use each tool, not just the tool schema.
        """
        prompt = self.BASE_PROMPT

        memories = self.memory_provider.load_for_prompt()
        if memories:
            prompt += f"\n--- Remembered from past conversations ---\n{memories}\n"

        skill_context = self._render_skill_context(skills)
        if skill_context:
            prompt += skill_context

        if skipped_skills:
            prompt += "\n--- Unavailable Skills (installed but missing requirements) ---\n"
            for name, info in skipped_skills.items():
                prompt += f"\n- {name}: {info['description']} — {info['reason']}\n"
            prompt += (
                "\nIf the user asks for something handled by an unavailable skill, "
                "tell them what is needed to enable it rather than saying you cannot help.\n"
            )

        if invalid_skills:
            prompt += "\n--- Invalid Skills (installed but misconfigured) ---\n"
            for name, info in invalid_skills.items():
                description = info.get("description", "")
                reason = info.get("reason", "invalid configuration")
                summary = f"{name}: {description} — {reason}" if description else f"{name}: {reason}"
                prompt += f"\n- {summary}\n"
            prompt += (
                "\nIf the user asks for one of these skills, explain that it is installed "
                "but misconfigured and needs to be fixed before it can run.\n"
            )

        return prompt

    def _render_skill_context(self, skills: dict) -> str:
        """Render available skill instructions within the configured budget."""
        if not skills:
            return ""

        full_blocks = {
            skill.name: f"\n### {skill.name}\n{skill.instructions}\n"
            for skill in skills.values()
        }
        full_body = "".join(full_blocks.values())
        if not self._exceeds_budget(full_body, self.max_skill_tokens):
            return "\n--- Available Skills ---\n" + full_body

        compact_blocks = {
            skill.name: self._compact_skill_block(skill.name, skill.description)
            for skill in skills.values()
        }
        minimal_blocks = {
            skill.name: self._minimal_skill_block(skill.name, skill.description)
            for skill in skills.values()
        }

        rendered_blocks = []
        retained_tokens = 0

        for skill in skills.values():
            full_block = full_blocks[skill.name]
            compact_block = compact_blocks[skill.name]
            minimal_block = minimal_blocks[skill.name]

            chosen_block = self._choose_skill_block(
                retained_tokens=retained_tokens,
                full_block=full_block,
                compact_block=compact_block,
                minimal_block=minimal_block,
            )
            rendered_blocks.append(chosen_block)
            retained_tokens += self._estimate_tokens(chosen_block)

        intro = (
            "\n--- Available Skills ---\n"
            "\nSome skills are summarized compactly to stay within the prompt budget.\n"
        )
        return intro + "".join(rendered_blocks)

    def _choose_skill_block(
        self,
        retained_tokens: int,
        full_block: str,
        compact_block: str,
        minimal_block: str,
    ) -> str:
        """Choose the richest skill block that still fits the remaining budget."""
        if not self._would_exceed_budget(retained_tokens, full_block):
            return full_block
        if not self._would_exceed_budget(retained_tokens, compact_block):
            return compact_block
        return minimal_block

    def _compact_skill_block(self, name: str, description: str) -> str:
        """Render a shortened skill description when full instructions do not fit."""
        return (
            f"\n### {name}\n"
            f"Description: {description}\n"
            "Use this tool when the request matches this capability. "
            "Rely on the tool schema for exact inputs.\n"
        )

    def _minimal_skill_block(self, name: str, description: str) -> str:
        """Render the smallest fallback so every skill remains represented."""
        return f"\n- {name}: {description}\n"

    def _would_exceed_budget(self, retained_tokens: int, block: str) -> bool:
        """Return True if adding a block would exceed the skill-context budget."""
        if self.max_skill_tokens is None or self.max_skill_tokens <= 0:
            return False
        return retained_tokens + self._estimate_tokens(block) > self.max_skill_tokens

    def _exceeds_budget(self, text: str, budget: int | None) -> bool:
        """Return True if text exceeds the configured approximate token budget."""
        if budget is None or budget <= 0:
            return False
        return self._estimate_tokens(text) > budget

    def _estimate_tokens(self, text: str) -> int:
        """Approximate token count from serialized text length."""
        serialized = json.dumps(text, ensure_ascii=False)
        return max(1, len(serialized) // 4)
