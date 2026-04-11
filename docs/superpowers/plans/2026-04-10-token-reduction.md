# Token Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce per-request input tokens for simple commands from ~6,266 to ~2,500–3,500 via semantic skill selection and dashboard SKILL.md trim.

**Architecture:** New `SkillSelector` class embeds skill descriptions using chromadb's built-in embedding function (already installed) and ranks them by cosine similarity to the user message. `PromptBuilder` expands only the top-K relevant skills in full; others collapse to one-liners. Dashboard SKILL.md gets an editorial pass to remove redundant prose.

**Tech Stack:** Python, numpy (already in requirements.txt), chromadb's `DefaultEmbeddingFunction` (already installed — no new packages), pytest.

---

## File Structure

| File | Change |
|---|---|
| `skills/dashboard/SKILL.md` | Trim from ~1,316 tokens to ~550 tokens |
| `core/skill_selector.py` | **Create** — SkillSelector class |
| `core/prompt_builder.py` | Add `skill_selector` param + `user_message` param to `build()` |
| `core/orchestrator.py` | Create SkillSelector at init, build per-request prompt in `process_message()` |
| `main.py` | Add `--skill-select` flag, pass `SKILL_SELECT_TOP_K` to Orchestrator |
| `tests/test_skill_selector.py` | **Create** — unit tests for SkillSelector |
| `tests/test_prompt_builder_selector.py` | **Create** — unit tests for PromptBuilder with selector |

---

## Task 1: Trim dashboard SKILL.md

**Files:**
- Modify: `skills/dashboard/SKILL.md`

- [ ] **Step 1: Replace dashboard SKILL.md with trimmed version**

Replace the entire file with this content (down from 122 lines / ~1,316 tokens to ~72 lines / ~550 tokens — all behavioral rules preserved, only redundant prose and excess examples removed):

```markdown
---
name: dashboard
description: Show a visual dashboard on the connected monitor, or close it. Displays news, weather, stocks, and music.
---

# Dashboard Skill

## When to use

- "show me the dashboard", "pull up the display", "open the screen"
- "show me the news / weather / stocks / what's playing"
- "switch to conflict news", "update my news feed", "show me Middle East news"
- "close the display", "turn off the screen"

Do NOT use this skill to play audio — use the soundcloud skill.

## Before calling

Check memory for location and news preferences. Use them silently if found. Ask only if missing.

- **Location missing + weather or local news requested:** ask what city they're in, then save with `save_memory` (topic: "location").
- **News preferences missing + not specified in request:** ask what kind of news (local, world, OSINT/conflict, or a mix), then save with `save_memory` (topic: "dashboard news preferences"). Skip if request already specifies topic.
- If user says "update my news feed", ask regardless of memory.

## Building the news config

**`news_sources`** — RSS feed groups:
- `"osint"` — Bellingcat, The War Zone
- `"world"` — Al Jazeera
- `"local_vt"` — VTDigger, Seven Days (Burlington/Vermont only)

**`gdelt_queries`** — free dynamic news queries. Always include a location query when city is known. Add topic queries from preferences or request.

- Burlington, VT → `"Burlington Vermont"`
- Conflict/geopolitics → `"conflict military geopolitics"`
- Climate → `"climate environment"`
- User specifies a topic → build a precise query string for it

## Inputs

```yaml
type: object
properties:
  action:
    type: string
    enum: [open, close]
  panels:
    type: array
    items:
      enum: [news, weather, stocks, music]
  location:
    type: string
    description: city name for weather and local GDELT query
  news_sources:
    type: array
    items:
      enum: [osint, world, local_vt]
  gdelt_queries:
    type: array
    items:
      type: string
  timeout_minutes:
    type: integer
    default: 10
required:
  - action
```

## Panel selection

- news / headlines / what's happening → `["news"]`
- weather → `["weather"]`
- stocks / market → `["stocks"]`
- music / what's playing → `["music"]`
- dashboard / everything / unspecified → `["news", "weather", "stocks", "music"]`
- Combinations: "news and weather" → `["news", "weather"]`

## Live topic updates

If the dashboard is open, `action: "open"` with new parameters updates content in place.

- "show me Middle East news" → `gdelt_queries: ["Middle East conflict news"]` (no `news_sources`)
- "switch to local news" → `gdelt_queries: ["Burlington Vermont"]`, `news_sources: ["local_vt"]`
- "show me Toyota news" → `gdelt_queries: ["Toyota new model 2026"]` (no `news_sources`)

**Rule:** Include `news_sources` only when the user wants a named feed category. For topic-specific updates, omit `news_sources` — this clears RSS so only on-topic results appear. Be specific with queries: "Toyota Camry 2026 hybrid" beats "Toyota news".

## How to respond

- Opening: "Dashboard is up with [content summary]."
- Live update: "Switched to [topic]."
- Closing: "Display closed."
Keep responses short — the user is looking at the screen, not listening for detail.
```

- [ ] **Step 2: Verify token reduction**

```bash
wc -c skills/dashboard/SKILL.md
```

Expected: under 2,400 bytes (was 5,264).

- [ ] **Step 3: Commit**

```bash
git add skills/dashboard/SKILL.md
git commit -m "perf: trim dashboard SKILL.md from ~1316 to ~550 tokens"
```

---

## Task 2: Implement SkillSelector

**Files:**
- Create: `core/skill_selector.py`
- Create: `tests/test_skill_selector.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_skill_selector.py`:

```python
"""Tests for SkillSelector — semantic skill relevance ranking."""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import numpy as np


def _make_mock_ef(skill_dim_map: dict[str, int], n_dims: int):
    """
    Return a mock DefaultEmbeddingFunction.

    Each skill gets a one-hot vector at its assigned dimension.
    A query containing a skill's keyword returns the matching one-hot vector.
    """
    def ef(texts):
        embeddings = []
        for text in texts:
            vec = np.zeros(n_dims, dtype=np.float32)
            t = text.lower()
            for keyword, dim in skill_dim_map.items():
                if keyword in t:
                    vec[dim] = 1.0
                    break
            # avoid all-zero: fall back to dim 0 with tiny weight
            if vec.sum() == 0:
                vec[0] = 0.01
            embeddings.append(vec.tolist())
        return embeddings

    mock = MagicMock(side_effect=ef)
    return mock


class FakeSkill:
    def __init__(self, name, description):
        self.name = name
        self.description = description


FAKE_SKILLS = {
    "soundcloud": FakeSkill("soundcloud", "Play music and audio from SoundCloud"),
    "weather": FakeSkill("weather", "Get current weather for a city"),
    "dashboard": FakeSkill("dashboard", "Show a visual news and weather dashboard"),
    "web_search": FakeSkill("web_search", "Search the web for information"),
}

# Map keyword → embedding dimension
DIM_MAP = {
    "soundcloud": 0,
    "music": 0,
    "song": 0,
    "weather": 1,
    "temperature": 1,
    "dashboard": 2,
    "news": 2,
    "search": 3,
    "web": 3,
}


class TestSkillSelector(unittest.TestCase):

    def _make_selector(self, top_k=1):
        """Create a SkillSelector with a mocked embedding function."""
        from core.skill_selector import SkillSelector

        selector = SkillSelector.__new__(SkillSelector)
        selector.top_k = top_k
        selector._ef = _make_mock_ef(DIM_MAP, n_dims=4)
        selector._skill_names = []
        selector._embeddings = None
        selector.index(FAKE_SKILLS)
        return selector

    def test_selects_most_relevant_skill(self):
        from core.skill_selector import SkillSelector
        selector = self._make_selector(top_k=1)
        result = selector.select("play a song")
        self.assertIn("soundcloud", result)

    def test_selects_top_k_skills(self):
        from core.skill_selector import SkillSelector
        selector = self._make_selector(top_k=2)
        result = selector.select("play a song")
        self.assertEqual(len(result), 2)

    def test_weather_query_selects_weather(self):
        from core.skill_selector import SkillSelector
        selector = self._make_selector(top_k=1)
        result = selector.select("what is the weather today")
        self.assertIn("weather", result)

    def test_returns_empty_set_when_unavailable(self):
        from core.skill_selector import SkillSelector
        selector = SkillSelector.__new__(SkillSelector)
        selector.top_k = 2
        selector._ef = None
        selector._skill_names = []
        selector._embeddings = None
        self.assertEqual(selector.select("play a song"), set())

    def test_available_false_when_not_indexed(self):
        from core.skill_selector import SkillSelector
        selector = SkillSelector.__new__(SkillSelector)
        selector._ef = MagicMock()
        selector._embeddings = None
        selector._skill_names = []
        self.assertFalse(selector.available)

    def test_available_true_after_index(self):
        from core.skill_selector import SkillSelector
        selector = self._make_selector(top_k=1)
        self.assertTrue(selector.available)

    def test_index_resets_on_reload(self):
        from core.skill_selector import SkillSelector
        selector = self._make_selector(top_k=1)
        new_skills = {
            "homebridge": FakeSkill("homebridge", "Control smart home devices"),
        }
        selector.index(new_skills)
        self.assertEqual(selector._skill_names, ["homebridge"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/daedalus/linux/miniclaw
python -m pytest tests/test_skill_selector.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'core.skill_selector'`

- [ ] **Step 3: Implement `core/skill_selector.py`**

```python
"""
Skill Selector - Semantic skill relevance ranking.

Embeds skill descriptions using chromadb's built-in embedding function
(onnxruntime-based, no extra packages needed) and ranks them by cosine
similarity to the incoming user message.

PromptBuilder uses this to expand only relevant skills in full — all
others collapse to compact one-liners.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)


class SkillSelector:
    """
    Ranks skills by semantic relevance to a user message.

    Uses chromadb's DefaultEmbeddingFunction (all-MiniLM-L6-v2 via
    onnxruntime). Falls back gracefully if unavailable — callers treat
    an empty result set as "use all skills".
    """

    def __init__(self, top_k: int = 2):
        self.top_k = top_k
        self._ef = None
        self._skill_names: list[str] = []
        self._embeddings: np.ndarray | None = None
        self._load_model()

    def _load_model(self) -> None:
        try:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
            self._ef = DefaultEmbeddingFunction()
            logger.info("SkillSelector: embedding function loaded")
        except Exception as exc:
            logger.warning(
                "SkillSelector: unavailable, falling back to all-skills-full: %s", exc
            )

    @property
    def available(self) -> bool:
        """True only when the model is loaded and skills have been indexed."""
        return self._ef is not None and self._embeddings is not None

    def index(self, skills: dict) -> None:
        """
        Embed all skill descriptions. Call after skill load or reload.

        skills: dict[str, Skill] — the loaded skills dict from SkillLoader.
        """
        if self._ef is None:
            return
        self._skill_names = list(skills.keys())
        texts = [f"{s.name}: {s.description}" for s in skills.values()]
        raw = self._ef(texts)
        self._embeddings = np.array(raw, dtype=np.float32)
        logger.debug("SkillSelector: indexed %d skills", len(self._skill_names))

    def select(self, user_message: str) -> set[str]:
        """
        Return skill names most relevant to user_message.

        Returns empty set when unavailable — PromptBuilder treats this
        as "expand all skills" (existing behaviour).
        """
        if not self.available:
            return set()

        query_raw = self._ef([user_message])
        query_emb = np.array(query_raw[0], dtype=np.float32)
        query_norm = np.linalg.norm(query_emb)

        if query_norm < 1e-8:
            # Zero-vector query — just return the first top_k skills
            return set(self._skill_names[: self.top_k])

        norms = np.linalg.norm(self._embeddings, axis=1)
        similarities = self._embeddings @ query_emb / (norms * query_norm + 1e-8)

        top_indices = np.argsort(similarities)[::-1][: self.top_k]
        return {self._skill_names[i] for i in top_indices}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_skill_selector.py -v
```

Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/skill_selector.py tests/test_skill_selector.py
git commit -m "feat: add SkillSelector for semantic skill relevance ranking"
```

---

## Task 3: Wire SkillSelector into PromptBuilder

**Files:**
- Modify: `core/prompt_builder.py`
- Create: `tests/test_prompt_builder_selector.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_prompt_builder_selector.py`:

```python
"""Tests for PromptBuilder with SkillSelector wired in."""

import sys
import types
import unittest

# Stub anthropic so MemoryProvider imports work without the real package
sys.modules.setdefault("anthropic", types.SimpleNamespace(NOT_GIVEN=object()))


class FakeSkill:
    def __init__(self, name, description, instructions="detailed instructions"):
        self.name = name
        self.description = description
        self.instructions = instructions


class MockSelector:
    """Always selects 'soundcloud' as the relevant skill."""

    @property
    def available(self):
        return True

    def select(self, user_message: str) -> set[str]:
        return {"soundcloud"}


class UnavailableSelector:
    @property
    def available(self):
        return False

    def select(self, user_message: str) -> set[str]:
        return set()


SKILLS = {
    "soundcloud": FakeSkill("soundcloud", "Play music from SoundCloud"),
    "weather": FakeSkill("weather", "Get current weather"),
    "dashboard": FakeSkill("dashboard", "Show a visual dashboard"),
    "set_env_var": FakeSkill("set_env_var", "Set an environment variable"),
    "save_memory": FakeSkill("save_memory", "Save a memory note"),
    "install_skill": FakeSkill("install_skill", "Install a new skill"),
}


class TestPromptBuilderSelector(unittest.TestCase):

    def _make_builder(self, selector=None):
        from core.prompt_builder import PromptBuilder
        return PromptBuilder(memory_provider=None, skill_selector=selector)

    def test_with_selector_expands_selected_skill_in_full(self):
        builder = self._make_builder(selector=MockSelector())
        prompt = builder.build(SKILLS, {}, user_message="play a song")
        self.assertIn("detailed instructions", prompt.split("soundcloud")[1])

    def test_with_selector_collapses_other_skills_to_one_liner(self):
        builder = self._make_builder(selector=MockSelector())
        prompt = builder.build(SKILLS, {}, user_message="play a song")
        # weather is not selected — should NOT have its full instructions
        # but should appear as a one-liner
        self.assertIn("weather", prompt)
        weather_section = "### weather"
        self.assertNotIn(weather_section, prompt)

    def test_always_full_skills_always_expanded(self):
        builder = self._make_builder(selector=MockSelector())
        prompt = builder.build(SKILLS, {}, user_message="play a song")
        # set_env_var, save_memory, install_skill always get full instructions
        for always_full in ("set_env_var", "save_memory", "install_skill"):
            self.assertIn(f"### {always_full}", prompt)

    def test_without_user_message_expands_all_skills(self):
        builder = self._make_builder(selector=MockSelector())
        prompt = builder.build(SKILLS, {}, user_message=None)
        # No user_message → fall back to existing all-skills-full behaviour
        for name in SKILLS:
            self.assertIn(f"### {name}", prompt)

    def test_unavailable_selector_expands_all_skills(self):
        builder = self._make_builder(selector=UnavailableSelector())
        prompt = builder.build(SKILLS, {}, user_message="play a song")
        for name in SKILLS:
            self.assertIn(f"### {name}", prompt)

    def test_no_selector_expands_all_skills(self):
        builder = self._make_builder(selector=None)
        prompt = builder.build(SKILLS, {}, user_message="play a song")
        for name in SKILLS:
            self.assertIn(f"### {name}", prompt)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_prompt_builder_selector.py -v 2>&1 | head -20
```

Expected: failures because `PromptBuilder.__init__` doesn't accept `skill_selector` yet.

- [ ] **Step 3: Update `core/prompt_builder.py`**

Replace the file with this updated version:

```python
"""
Prompt builder for MiniClaw.

Assembles the system prompt from static assistant policy, persisted memories,
and skill instructions.
"""

import json

from core.memory_provider import MemoryProvider


class PromptBuilder:
    """Build the full system prompt used for Claude requests."""

    ALWAYS_FULL_SKILLS = {"set_env_var", "save_memory", "install_skill"}

    BASE_PROMPT = (
        "Your name is Computer. You are Mason's personal voice assistant, running on a Raspberry Pi. "
        "You have a warm and direct personality. You value truth above everything else — never flatter, "
        "never soften a hard answer just to be agreeable, and never tell Mason what he wants to hear "
        "at the expense of what is actually true. If something is wrong, say so plainly. "
        "If you don't know something, say so rather than guessing. Warmth means you care; "
        "it does not mean you sugarcoat.\n\n"
        "Guidelines:\n"
        "- Never use asterisks, emojis, or markdown formatting\n"
        "- Speak naturally and conversationally — responses will be read aloud\n"
        "- Keep responses concise for spoken delivery\n"
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
        "his life or work — save it using the save_memory skill without waiting to be asked. "
        "Do not save trivial exchanges. Only save what would be useful to recall in a future session.\n"
    )

    def __init__(
        self,
        memory_provider: MemoryProvider | None = None,
        max_skill_tokens: int | None = 4000,
        skill_selector=None,
    ):
        self.memory_provider = memory_provider or MemoryProvider()
        self.max_skill_tokens = max_skill_tokens
        self._skill_selector = skill_selector

    def build(
        self,
        skills: dict,
        skipped_skills: dict,
        invalid_skills: dict | None = None,
        user_message: str | None = None,
    ) -> str:
        """
        Build the system prompt including memories and skill instructions.

        When user_message is provided and a SkillSelector is available,
        only the most relevant skills are expanded in full — all others
        collapse to compact one-liners. This cuts token usage significantly
        for simple single-skill commands.
        """
        prompt = self.BASE_PROMPT

        memories = self.memory_provider.load_for_prompt()
        if memories:
            prompt += f"\n--- Remembered from past conversations ---\n{memories}\n"

        skill_context = self._render_skill_context(skills, user_message=user_message)
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

        # Existing budget-based logic (used when no selector or no user_message)
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_prompt_builder_selector.py -v
```

Expected: 6 tests pass.

- [ ] **Step 5: Run existing tests to verify no regression**

```bash
python -m pytest tests/ -v --ignore=tests/test_voice_mode.py 2>&1 | tail -15
```

Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add core/prompt_builder.py tests/test_prompt_builder_selector.py
git commit -m "feat: wire SkillSelector into PromptBuilder for per-request skill expansion"
```

---

## Task 4: Wire into Orchestrator and add --skill-select flag

**Files:**
- Modify: `core/orchestrator.py`
- Modify: `main.py`

- [ ] **Step 1: Update `core/orchestrator.py`**

Replace the file with this updated version:

```python
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
from core.skill_selector import SkillSelector
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
        """Process a user message through Claude with tool support."""
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
```

- [ ] **Step 2: Update `main.py` — add `--skill-select` flag and `SKILL_SELECT_TOP_K`**

Add `--skill-select` argument to the parser (after the existing `--skills-dir` argument):

```python
    parser.add_argument(
        "--skill-select",
        type=str,
        metavar="QUERY",
        help="Test semantic skill selection for a query without making an API call",
    )
```

Pass `skill_select_top_k` to Orchestrator (add as the last kwarg in the Orchestrator constructor call):

```python
        skill_select_top_k=int(os.getenv("SKILL_SELECT_TOP_K", "2")),
```

Add the `--skill-select` handler after `orchestrator` is built (before the `if args.list:` block):

```python
    if args.skill_select:
        query = args.skill_select
        selected = orchestrator.skill_selector.select(query)
        always_full = orchestrator.prompt_builder.ALWAYS_FULL_SKILLS
        print(f"\nQuery: {query!r}")
        print(f"Selected for full instructions: {sorted(selected)}")
        print(f"Always-full skills: {sorted(always_full)}")
        all_skills = set(orchestrator.skills.keys())
        compact = all_skills - selected - always_full
        print(f"Compact one-liners: {sorted(compact)}")
        sys.exit(0)
```

- [ ] **Step 3: Run existing test suite**

```bash
python -m pytest tests/ -v --ignore=tests/test_voice_mode.py 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 4: Verify --skill-select works**

```bash
python main.py --skill-select "play a song"
python main.py --skill-select "what is the weather"
python main.py --skill-select "show me the dashboard"
python main.py --skill-select "search for latest AI news"
```

Expected for "play a song":
```
Query: 'play a song'
Selected for full instructions: ['soundcloud']
Always-full skills: ['install_skill', 'save_memory', 'set_env_var']
Compact one-liners: ['dashboard', 'homebridge', 'playwright_scraper', 'skill_tells_random', 'weather', 'web_search']
```

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator.py main.py
git commit -m "feat: per-request semantic skill selection in Orchestrator"
```

---

## Task 5: Verify token reduction in text mode

- [ ] **Step 1: Run a simple command in text mode and check token log**

```bash
python main.py --text
```

Type: `play a song by Tame Impala`

Expected log line: `INFO: Response ready: 2 rounds, ~2800 input / N output tokens`

The input token count should be roughly 2,500–3,200 (down from ~6,266).

- [ ] **Step 2: Verify dashboard behavior with 4 test commands**

In text mode, run each of these and verify Claude sends the correct tool inputs:

1. `open the dashboard` → `action: open`, panels includes news/weather/stocks/music
2. `show me Middle East news` → `gdelt_queries: ["Middle East..."]`, no `news_sources`
3. `switch to local news` → `news_sources: ["local_vt"]`, location-based `gdelt_queries`
4. `close the dashboard` → `action: close`

If any of these fail, the dashboard SKILL.md trim went too far — restore the relevant section from git.

- [ ] **Step 3: Final commit if any fixes were needed**

```bash
git add -p  # stage only what changed
git commit -m "fix: restore dashboard SKILL.md section after trim validation"
```

(Only run this step if Step 2 revealed issues.)
