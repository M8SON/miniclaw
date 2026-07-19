"""
Prompt builder for Kaizen.

Assembles the system prompt from static assistant policy, persisted memories,
and skill instructions.
"""

import json
import os

from core.memory_provider import MemoryProvider


def persona_name_from_env() -> str:
    """Derive the assistant's persona name from the active openWakeWord model.

    Mirrors main._display_wake_word so the banner the user sees and the
    persona Claude introduces itself as stay in sync. Examples:
      WAKE_WORD_MODEL=hey_jarwis   -> "Jarvis"
      WAKE_WORD_MODEL=alexa        -> "Alexa"
      WAKE_WORD_MODEL=hey_mycroft  -> "Mycroft"
    """
    raw = os.getenv("WAKE_WORD_MODEL", "hey_jarvis").replace("_", " ")
    tokens = raw.strip().split()
    if not tokens:
        return "Jarvis"
    return tokens[-1].capitalize()


SELF_UPDATE_GUIDANCE = """
Self-improving skills are enabled. Use update_skill_hints when:

  1. NOVEL SUCCESSFUL PHRASING: a user said something the skill's
     SKILL.md doesn't mention as a trigger phrase, and the skill
     ran cleanly. Add the phrasing as an example.

  2. ROUTING MISS YOU CORRECTED: you initially called skill X, the
     user clarified or the result didn't fit, and you re-routed to
     skill Y. After the user's request is satisfied via skill Y,
     add a hint to Y about the original phrasing.

Constraints:

  - Additions are short markdown bullets (one line).
  - Only call update_skill_hints once per skill per turn.
  - If the phrasing is already covered by existing SKILL.md content,
    don't call.
  - Never call on bundled skills whose routing is security-relevant
    (install-skill, set-env-var, save-memory).
  - Provide a rationale field naming the user phrasing or pattern
    that motivated the addition, in 15 words or fewer.

When in doubt, don't call. Auto-learned hints accumulate; bad ones
take effort to clean up.
""".strip()


class PromptBuilder:
    """Build the full system prompt used for Claude requests."""

    ALWAYS_FULL_SKILLS = {"set-env-var", "save-memory", "install-skill"}

    BASE_PROMPT_TEMPLATE = (
        "Your name is {persona}. You are Mason's personal voice assistant, running on a Raspberry Pi. "
        "You have a warm and direct personality. You value truth above everything else — never flatter, "
        "never soften a hard answer just to be agreeable, and never tell Mason what he wants to hear "
        "at the expense of what is actually true. If something is wrong, say so plainly. "
        "If you don't know something, say so rather than guessing. Warmth means you care; "
        "it does not mean you sugarcoat.\n\n"
        "Guidelines:\n"
        "- Never use asterisks, emojis, or markdown formatting\n"
        "- Speak naturally and conversationally — responses will be read aloud\n"
        "- Default to short, one or two sentence answers — short and sweet. "
        "Only give a longer, detailed answer when Mason explicitly asks you to "
        "explain, elaborate, go deeper, or tell him more\n"
        "- When using tools, say what you are doing in plain language\n"
        "- Summarize tool results conversationally — no raw data dumps\n"
        "- Your input comes from a speech-to-text system and may contain "
        "transcription errors. If a request seems garbled, unclear, or does "
        "not make sense as spoken language, repeat back what you heard and "
        "ask for clarification before acting. For example: 'I heard confirm "
        "point, did you mean confirm restart?' or 'I caught something about "
        "X but I am not sure, could you repeat that?'\n"
        "- If you learn something genuinely worth remembering about Mason — a preference, "
        "an ongoing project, something he asked you to keep in mind, or a useful fact about "
        "his life or work — save it using the save-memory skill without waiting to be asked. "
        "Do not save passing remarks or one-off requests. Only save what would be useful "
        "to recall in a future session.\n"
    )

    MICRO_TIER_TEMPLATE = (
        "You are {persona}, Mason's voice assistant. "
        "Reply briefly in plain spoken sentences. Use tools when the request matches one. "
        "If a request seems garbled, ask for clarification before acting. "
        "No markdown or asterisks."
    )

    def __init__(
        self,
        memory_provider: MemoryProvider | None = None,
        max_skill_tokens: int | None = 4000,
        skill_selector=None,
        persona_name: str | None = None,
    ):
        self.memory_provider = memory_provider or MemoryProvider()
        self.max_skill_tokens = max_skill_tokens
        self._skill_selector = skill_selector
        self.persona_name = persona_name or persona_name_from_env()
        self.BASE_PROMPT = self.BASE_PROMPT_TEMPLATE.format(persona=self.persona_name)

    def build_for_greeting(self, startup_context: str = "") -> str:
        """Slim system prompt for the cold-start greeting.

        The greeting only needs persona + date/time/weather (~10 tokens of
        the user's day) to produce one warm sentence. Adding skill bodies,
        memories, and self-update guidance for that turn was burning ~6k
        input tokens per startup with no impact on the output. Excludes
        every section the greeting doesn't use; callers pick a low
        max_tokens at the API layer.
        """
        prompt = self.BASE_PROMPT
        if startup_context.strip():
            prompt += f"\n--- Current Context ---\n{startup_context}\n"
        return prompt

    def build_for_micro_tier(self) -> str:
        """Slim system prompt for the Haiku micro-tier.

        Excludes memory, skill markdown bodies, and the heavy persona
        scaffolding. Tools are delivered via the API's tools parameter
        (already top-K filtered by the caller); duplicating skill bodies
        in the prompt is pure overhead. Includes the persona name so the
        voice still says "I'm Jarvis" when needed."""
        return self.MICRO_TIER_TEMPLATE.format(persona=self.persona_name)

    def build_cacheable_parts(
        self,
        skills: dict,
        skipped_skills: dict,
        invalid_skills: dict | None = None,
        user_message: str | None = None,
    ) -> tuple[str, str]:
        """Return (stable_prefix, dynamic_suffix) for Anthropic prompt caching.

        stable_prefix is byte-stable across turns within a session: persona,
        vault memory, unavailable/invalid skill lists, self-update guidance.
        Callers may append additional stable content (e.g. startup context).

        dynamic_suffix carries per-turn variance — the selector-driven skill
        context — and must NOT be cached.
        """
        stable = self.BASE_PROMPT

        memories = self.memory_provider.load_for_prompt()
        if memories:
            stable += f"\n--- Remembered from past conversations ---\n{memories}\n"

        if skipped_skills:
            stable += "\n--- Unavailable Skills (installed but missing requirements) ---\n"
            for name, info in skipped_skills.items():
                stable += f"\n- {name}: {info['description']} — {info['reason']}\n"
            stable += (
                "\nIf the user asks for something handled by an unavailable skill, "
                "tell them what is needed to enable it rather than saying you cannot help.\n"
            )

        if invalid_skills:
            stable += "\n--- Invalid Skills (installed but misconfigured) ---\n"
            for name, info in invalid_skills.items():
                description = info.get("description", "")
                reason = info.get("reason", "invalid configuration")
                summary = f"{name}: {description} — {reason}" if description else f"{name}: {reason}"
                stable += f"\n- {summary}\n"
            stable += (
                "\nIf the user asks for one of these skills, explain that it is installed "
                "but misconfigured and needs to be fixed before it can run.\n"
            )

        stable = self.add_self_update_guidance(stable, skills=skills)

        dynamic = self._render_skill_context(skills, user_message=user_message) or ""

        return stable, dynamic

    def build(
        self,
        skills: dict,
        skipped_skills: dict,
        invalid_skills: dict | None = None,
        user_message: str | None = None,
    ) -> str:
        """Build the full system prompt as a single string.

        Equivalent to concatenating the two parts from `build_cacheable_parts`.
        Kept for callers that don't need caching (greet path, internal calls).
        """
        stable, dynamic = self.build_cacheable_parts(
            skills, skipped_skills, invalid_skills, user_message=user_message
        )
        return stable + dynamic

    def add_self_update_guidance(self, prompt: str, *, skills: dict) -> str:
        """Append standing self-update guidance if any loaded skill has allow_body: true."""
        any_opted_in = any(
            (
                getattr(s, "frontmatter", {}) or {}
            ).get("metadata", {}).get("kaizen", {}).get("self_update", {}).get("allow_body") is True
            for s in skills.values()
        )
        if not any_opted_in:
            return prompt
        return prompt + "\n\n--- Self-update guidance ---\n" + SELF_UPDATE_GUIDANCE

    def _render_with_selector(self, skills: dict, user_message: str) -> str:
        """
        Render skill context using semantic selection.

        Skills in the selected set (plus ALWAYS_FULL_SKILLS) get full
        instructions. All others get a single compact line.
        """
        selected = self._skill_selector.select(user_message)
        expand_names = selected | self.ALWAYS_FULL_SKILLS

        full_blocks = []
        compact_lines = []

        for skill in skills.values():
            if skill.name in expand_names:
                full_blocks.append(f"\n### {skill.name}\n{skill.instructions}\n")
            else:
                compact_lines.append(f"- {skill.name}: {skill.description}")

        result = "\n--- Available Skills ---\n"
        result += "".join(full_blocks)
        if compact_lines:
            result += "\nOther available skills (ask to use them):\n"
            result += "\n".join(compact_lines) + "\n"
        return result

    def _render_skill_context(self, skills: dict, user_message: str | None = None) -> str:
        """Render available skill instructions within the configured budget."""
        if not skills:
            return ""

        # Use semantic selection when selector is active and we have a user message
        if (
            user_message
            and self._skill_selector is not None
            and self._skill_selector.available
        ):
            return self._render_with_selector(skills, user_message)

        full_blocks = {
            skill.name: f"\n### {skill.name}\n{skill.instructions}\n"
            for skill in skills.values()
        }
        full_body = "".join(full_blocks.values())
        if not self._exceeds_budget(full_body, self.max_skill_tokens):
            return "\n--- Available Skills ---\n" + full_body

        rendered_blocks = []
        retained_tokens = 0

        for skill in skills.values():
            if skill.name not in self.ALWAYS_FULL_SKILLS:
                continue

            full_block = full_blocks[skill.name]
            rendered_blocks.append(full_block)
            retained_tokens += self._estimate_tokens(full_block)

        compact_blocks = {
            skill.name: self._compact_skill_block(skill.name, skill.description)
            for skill in skills.values()
        }
        minimal_blocks = {
            skill.name: self._minimal_skill_block(skill.name, skill.description)
            for skill in skills.values()
        }

        for skill in skills.values():
            if skill.name in self.ALWAYS_FULL_SKILLS:
                continue

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
