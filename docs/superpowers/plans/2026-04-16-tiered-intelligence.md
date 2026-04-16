# Tiered Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a three-tier routing gate (deterministic → Ollama → Claude) so Claude is only invoked for complex requests, reducing token cost and latency.

**Architecture:** A new `TierRouter` classifies each STT transcript in <5ms before any LLM runs. Simple commands hit `OllamaToolLoop` (local LLM); meta/complex commands go straight to the existing `ToolLoop` (Claude). The entire feature is behind `OLLAMA_ENABLED=false` — existing behaviour is completely unchanged when it's unset.

**Tech Stack:** Python 3.12, `requests` (already in venv via transitive deps), `pyyaml` (already in requirements.txt), `unittest` + `pytest`, Ollama OpenAI-compatible API (`/v1/chat/completions`).

**Spec:** `docs/superpowers/specs/2026-04-16-tiered-intelligence-design.md`

---

## File Map

| File | Status | Responsibility |
|---|---|---|
| `config/intent_patterns.yaml` | Create | Dispatch + escalate regex patterns |
| `core/tier_router.py` | Create | `TierRouter` — transcript → `RouteResult(tier, skill, args, action)` |
| `core/ollama_tool_loop.py` | Create | `OllamaToolLoop` + `EscalateSignal` sentinel |
| `core/orchestrator.py` | Modify | Wire in `TierRouter` + `OllamaToolLoop` behind `OLLAMA_ENABLED` |
| `tests/test_tier_router.py` | Create | Unit tests for `TierRouter` |
| `tests/test_ollama_tool_loop.py` | Create | Unit tests for `OllamaToolLoop` |
| `.env.example` | Modify | Document new env vars |

---

## Task 1: Create `config/intent_patterns.yaml`

**Files:**
- Create: `config/intent_patterns.yaml`

- [ ] **Step 1: Create the patterns file**

```yaml
# config/intent_patterns.yaml
#
# dispatch: patterns that bypass all LLMs and call a skill or session action directly.
# escalate: patterns that bypass Ollama and go straight to Claude.
#
# Patterns are matched case-insensitively against the full transcript.
# Dispatch is checked first, then escalate, then Ollama is used as default.

dispatch:
  - pattern: "^(stop|pause|halt)(\\s+music|\\s+playing|\\s+audio)?[.!?]?$"
    skill: soundcloud_play
    args: {action: stop}

  - pattern: "^(volume up|turn it up|louder)[.!?]?$"
    skill: soundcloud_play
    args: {action: volume_up}

  - pattern: "^(volume down|turn it down|quieter|lower the volume)[.!?]?$"
    skill: soundcloud_play
    args: {action: volume_down}

  - pattern: "^(goodbye|bye|good night|stop listening|exit|shut down)[.!?]?$"
    action: close_session

escalate:
  # Skill/tool installation — always needs Claude's meta-reasoning
  - "\\b(install|add|make|create|build)\\s+(a\\s+)?(skill|tool|plugin|command)"

  # Memory operations — Claude is more reliable for structured saves
  - "\\bsave\\s+(this|that|a\\s+note)\\b"
  - "\\b(remember|don't forget|keep in mind)\\b"

  # Long explanation requests — likely complex
  - "\\bexplain\\b.{25,}"

  # Multi-step or compound requests
  - "\\b(first|then|after that|and also|as well as)\\b.{10,}"
```

- [ ] **Step 2: Commit**

```bash
cd miniclaw
git add config/intent_patterns.yaml
git commit -m "feat: add intent_patterns.yaml for tiered routing dispatch and escalate rules"
```

---

## Task 2: `core/tier_router.py` — dispatch patterns

**Files:**
- Create: `core/tier_router.py`
- Create: `tests/test_tier_router.py`

- [ ] **Step 1: Write the failing tests for dispatch**

```python
# tests/test_tier_router.py
"""Tests for TierRouter — transcript → tier routing."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Ensure core/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

PATTERNS_FILE = Path(__file__).parent.parent / "config" / "intent_patterns.yaml"


def _make_router(claude_only=None, skill_selector=None):
    from core.tier_router import TierRouter
    return TierRouter(
        patterns_path=PATTERNS_FILE,
        skill_selector=skill_selector,
        claude_only_skills=claude_only,
    )


class TestDispatchPatterns(unittest.TestCase):

    def test_stop_routes_direct(self):
        router = _make_router()
        result = router.route("stop")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.skill, "soundcloud_play")
        self.assertEqual(result.args, {"action": "stop"})

    def test_stop_music_routes_direct(self):
        router = _make_router()
        result = router.route("stop music")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.skill, "soundcloud_play")

    def test_pause_routes_direct(self):
        router = _make_router()
        result = router.route("pause")
        self.assertEqual(result.tier, "direct")

    def test_volume_up_routes_direct(self):
        router = _make_router()
        result = router.route("volume up")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.args, {"action": "volume_up"})

    def test_volume_down_routes_direct(self):
        router = _make_router()
        result = router.route("louder")
        self.assertEqual(result.tier, "direct")

    def test_goodbye_routes_direct_with_action(self):
        router = _make_router()
        result = router.route("goodbye")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.action, "close_session")
        self.assertIsNone(result.skill)

    def test_bye_routes_direct_session_close(self):
        router = _make_router()
        result = router.route("bye")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.action, "close_session")

    def test_unmatched_routes_ollama(self):
        router = _make_router()
        result = router.route("play some jazz")
        self.assertEqual(result.tier, "ollama")

    def test_missing_patterns_file_does_not_crash(self):
        from core.tier_router import TierRouter
        router = TierRouter(
            patterns_path=Path("/nonexistent/patterns.yaml"),
            skill_selector=None,
        )
        result = router.route("stop")
        # No patterns loaded — falls through to ollama
        self.assertEqual(result.tier, "ollama")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd miniclaw && source .venv/bin/activate
python -m pytest tests/test_tier_router.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'core.tier_router'`

- [ ] **Step 3: Create `core/tier_router.py` with dispatch support**

```python
"""
TierRouter - Fast pre-LLM routing for MiniClaw's tiered intelligence system.

Classifies each STT transcript as direct | ollama | claude in <5ms before
any LLM is invoked. Checked in order:

  1. Dispatch patterns  → direct skill call or session action (no LLM)
  2. Escalate patterns  → Claude immediately (skip Ollama)
  3. Skill prediction   → claude_only set → Claude, else Ollama
  4. Default            → Ollama
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

logger = logging.getLogger(__name__)

Tier = Literal["direct", "ollama", "claude"]


@dataclass
class RouteResult:
    tier: Tier
    skill: str | None = None    # set for tier=="direct" skill dispatches
    args: dict = field(default_factory=dict)
    action: str | None = None   # set for tier=="direct" session actions


class TierRouter:
    """
    Routes a transcript to the appropriate processing tier without invoking any LLM.

    Patterns are loaded from a YAML file at startup. A missing file logs a
    warning and falls back to routing everything to Ollama.
    """

    def __init__(
        self,
        patterns_path: Path,
        skill_selector=None,
        claude_only_skills: set[str] | None = None,
    ):
        self._dispatch: list[dict] = []
        self._escalate: list[re.Pattern] = []
        self._skill_selector = skill_selector
        self._claude_only: set[str] = claude_only_skills or {"install_skill"}
        self._load_patterns(patterns_path)

    def _load_patterns(self, path: Path) -> None:
        if not path.exists():
            logger.warning("TierRouter: patterns file not found at %s — no patterns loaded", path)
            return
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        for entry in data.get("dispatch", []):
            entry["_re"] = re.compile(entry["pattern"], re.IGNORECASE)
            self._dispatch.append(entry)
        for pattern in data.get("escalate", []):
            self._escalate.append(re.compile(pattern, re.IGNORECASE))
        logger.info(
            "TierRouter: loaded %d dispatch, %d escalate patterns",
            len(self._dispatch),
            len(self._escalate),
        )

    def route(self, transcript: str) -> RouteResult:
        """Classify a transcript into direct | ollama | claude."""
        text = transcript.strip()

        # 1. Dispatch patterns
        for entry in self._dispatch:
            if entry["_re"].search(text):
                if "action" in entry:
                    return RouteResult(tier="direct", action=entry["action"])
                return RouteResult(
                    tier="direct",
                    skill=entry.get("skill"),
                    args=dict(entry.get("args", {})),
                )

        # 2. Escalate patterns — checked in Task 3
        # 3. Skill prediction — checked in Task 3

        return RouteResult(tier="ollama")
```

- [ ] **Step 4: Run dispatch tests**

```bash
cd miniclaw && source .venv/bin/activate
python -m pytest tests/test_tier_router.py::TestDispatchPatterns -v
```

Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/tier_router.py tests/test_tier_router.py
git commit -m "feat: add TierRouter with dispatch pattern routing"
```

---

## Task 3: `core/tier_router.py` — escalate patterns + skill prediction

**Files:**
- Modify: `core/tier_router.py` (fill in steps 2 and 3 of `route()`)
- Modify: `tests/test_tier_router.py` (add escalate + skill prediction tests)

- [ ] **Step 1: Add failing tests for escalate and skill prediction**

Append to `tests/test_tier_router.py` before `if __name__ == "__main__":`:

```python
class TestEscalatePatterns(unittest.TestCase):

    def test_install_skill_escalates_to_claude(self):
        router = _make_router()
        result = router.route("install a skill that checks my calendar")
        self.assertEqual(result.tier, "claude")

    def test_add_tool_escalates(self):
        router = _make_router()
        result = router.route("add a tool that does X")
        self.assertEqual(result.tier, "claude")

    def test_remember_escalates(self):
        router = _make_router()
        result = router.route("remember that I prefer dark mode")
        self.assertEqual(result.tier, "claude")

    def test_long_explain_escalates(self):
        router = _make_router()
        result = router.route("explain how the skill system works in detail")
        self.assertEqual(result.tier, "claude")

    def test_short_explain_does_not_escalate(self):
        router = _make_router()
        result = router.route("explain this")
        # Short — no escalate, goes to ollama
        self.assertNotEqual(result.tier, "claude")


class TestSkillPrediction(unittest.TestCase):

    def _make_selector_predicting(self, skill_name: str):
        """Return a mock SkillSelector that always predicts skill_name."""
        sel = MagicMock()
        sel.available = True
        sel.select = MagicMock(return_value={skill_name})
        return sel

    def test_claude_only_skill_routes_to_claude(self):
        sel = self._make_selector_predicting("install_skill")
        router = _make_router(claude_only={"install_skill"}, skill_selector=sel)
        result = router.route("make me a new skill")
        self.assertEqual(result.tier, "claude")

    def test_non_claude_only_skill_routes_to_ollama(self):
        sel = self._make_selector_predicting("weather")
        router = _make_router(claude_only={"install_skill"}, skill_selector=sel)
        result = router.route("what is the weather in London")
        self.assertEqual(result.tier, "ollama")

    def test_no_skill_selector_defaults_to_ollama(self):
        router = _make_router(skill_selector=None)
        result = router.route("some unknown request")
        self.assertEqual(result.tier, "ollama")

    def test_unavailable_selector_defaults_to_ollama(self):
        sel = MagicMock()
        sel.available = False
        router = _make_router(skill_selector=sel)
        result = router.route("some unknown request")
        self.assertEqual(result.tier, "ollama")
```

- [ ] **Step 2: Run to confirm new tests fail**

```bash
cd miniclaw && source .venv/bin/activate
python -m pytest tests/test_tier_router.py::TestEscalatePatterns tests/test_tier_router.py::TestSkillPrediction -v 2>&1 | tail -15
```

Expected: escalate tests FAIL (tier is "ollama" not "claude"), skill prediction tests FAIL.

- [ ] **Step 3: Fill in escalate + skill prediction in `core/tier_router.py`**

Replace the `route()` method's stub comment block with:

```python
    def route(self, transcript: str) -> RouteResult:
        """Classify a transcript into direct | ollama | claude."""
        text = transcript.strip()

        # 1. Dispatch patterns
        for entry in self._dispatch:
            if entry["_re"].search(text):
                if "action" in entry:
                    return RouteResult(tier="direct", action=entry["action"])
                return RouteResult(
                    tier="direct",
                    skill=entry.get("skill"),
                    args=dict(entry.get("args", {})),
                )

        # 2. Escalate patterns — route to Claude immediately, skip Ollama latency
        for pattern in self._escalate:
            if pattern.search(text):
                logger.debug("TierRouter: escalate pattern matched → claude")
                return RouteResult(tier="claude")

        # 3. Skill prediction — if SkillSelector predicts a Claude-only skill, escalate
        if self._skill_selector and self._skill_selector.available:
            predicted = self._skill_selector.select(text)
            if predicted & self._claude_only:
                logger.debug(
                    "TierRouter: predicted claude-only skill(s) %s → claude", predicted
                )
                return RouteResult(tier="claude")

        # 4. Default — Ollama handles it
        logger.debug("TierRouter: no match → ollama")
        return RouteResult(tier="ollama")
```

- [ ] **Step 4: Run all TierRouter tests**

```bash
cd miniclaw && source .venv/bin/activate
python -m pytest tests/test_tier_router.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/tier_router.py tests/test_tier_router.py
git commit -m "feat: complete TierRouter with escalate patterns and skill prediction"
```

---

## Task 4: `core/ollama_tool_loop.py` — EscalateSignal + HTTP client

**Files:**
- Create: `core/ollama_tool_loop.py`
- Create: `tests/test_ollama_tool_loop.py`

- [ ] **Step 1: Write failing tests for EscalateSignal and timeout escalation**

```python
# tests/test_ollama_tool_loop.py
"""Tests for OllamaToolLoop and EscalateSignal."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_loop(
    host="http://localhost:11434",
    model="phi4-mini",
    skill_loader=None,
    container_manager=None,
    conversation_state=None,
    timeout_seconds=8.0,
):
    from core.ollama_tool_loop import OllamaToolLoop

    if skill_loader is None:
        skill_loader = MagicMock()
        skill_loader.get_tool_definitions.return_value = []
        skill_loader.get_skill.return_value = None

    if container_manager is None:
        container_manager = MagicMock()

    if conversation_state is None:
        from core.conversation_state import ConversationState
        conversation_state = ConversationState()

    return OllamaToolLoop(
        host=host,
        model=model,
        skill_loader=skill_loader,
        container_manager=container_manager,
        conversation_state=conversation_state,
        timeout_seconds=timeout_seconds,
    )


def _make_response(content=None, finish_reason="stop", tool_calls=None):
    """Build a minimal Ollama /v1/chat/completions JSON response."""
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [
            {
                "message": message,
                "finish_reason": finish_reason if not tool_calls else "tool_calls",
            }
        ]
    }


class TestEscalateSignal(unittest.TestCase):

    def test_escalate_signal_is_singleton(self):
        from core.ollama_tool_loop import EscalateSignal as E1
        from core.ollama_tool_loop import EscalateSignal as E2
        self.assertIs(E1, E2)

    def test_escalate_signal_identity_comparison(self):
        from core.ollama_tool_loop import EscalateSignal
        self.assertTrue(EscalateSignal is EscalateSignal)
        self.assertFalse(EscalateSignal is None)
        self.assertFalse(EscalateSignal is "ESCALATE")


class TestTimeoutEscalation(unittest.TestCase):

    def test_timeout_returns_escalate_signal(self):
        import requests
        from core.ollama_tool_loop import EscalateSignal

        loop = _make_loop()
        with patch("requests.post", side_effect=requests.Timeout):
            result = loop.run("play some jazz", "you are a voice assistant")
        self.assertIs(result, EscalateSignal)

    def test_connection_error_returns_escalate_signal(self):
        import requests
        from core.ollama_tool_loop import EscalateSignal

        loop = _make_loop()
        with patch("requests.post", side_effect=requests.ConnectionError):
            result = loop.run("play some jazz", "you are a voice assistant")
        self.assertIs(result, EscalateSignal)

    def test_conversation_state_unchanged_on_timeout(self):
        import requests

        from core.conversation_state import ConversationState
        state = ConversationState()
        loop = _make_loop(conversation_state=state)
        with patch("requests.post", side_effect=requests.Timeout):
            loop.run("play some jazz", "you are a voice assistant")
        # ConversationState must be untouched — Claude will append the message itself
        self.assertEqual(state.messages, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd miniclaw && source .venv/bin/activate
python -m pytest tests/test_ollama_tool_loop.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'core.ollama_tool_loop'`

- [ ] **Step 3: Create `core/ollama_tool_loop.py` with EscalateSignal and HTTP client**

```python
"""
OllamaToolLoop - Ollama-backed tool loop for MiniClaw.

Mirrors ToolLoop's interface but calls Ollama's OpenAI-compatible API
(/v1/chat/completions with tools parameter).

Returns EscalateSignal when it cannot handle the request — the Orchestrator
then re-runs the same turn with Claude's ToolLoop. ConversationState is NOT
updated until the loop succeeds, so Claude's ToolLoop can append the user
message and full exchange itself.
"""

import json
import logging
import re

import requests

logger = logging.getLogger(__name__)


class _EscalateSignalType:
    """Singleton sentinel returned when Ollama cannot handle a request."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self):
        return "EscalateSignal"


# Module-level singleton — use `result is EscalateSignal` to detect escalation.
EscalateSignal = _EscalateSignalType()

_REMEMBER_RE = re.compile(
    r"\n?##\s*remember:\n+topic:\s*(.+?)\n+content:\s*(.+?)(?=\n##|\Z)",
    re.IGNORECASE | re.DOTALL,
)


class OllamaToolLoop:
    """
    Execute an Ollama tool-use loop for a single user message.

    Uses Ollama's OpenAI-compatible API. Tool results are passed back as
    OpenAI tool messages. ConversationState is only updated on success so
    that escalation to Claude leaves a clean slate.
    """

    ESCALATE_WORD = "ESCALATE"

    def __init__(
        self,
        host: str,
        model: str,
        skill_loader,
        container_manager,
        conversation_state,
        memory_provider=None,
        timeout_seconds: float = 8.0,
        max_rounds: int = 10,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.skill_loader = skill_loader
        self.container_manager = container_manager
        self.conversation_state = conversation_state
        self.memory_provider = memory_provider
        self.timeout = timeout_seconds
        self.max_rounds = max_rounds

    def run(self, user_message: str, system_prompt: str) -> str | _EscalateSignalType:
        """
        Process a user message through Ollama with tool support.

        Returns a string response on success, or EscalateSignal if Ollama
        cannot handle the request. ConversationState is only modified on success.
        """
        local_messages = self._build_local_messages(system_prompt, user_message)
        tool_definitions = self._build_tool_definitions()
        rounds = 0

        while rounds < self.max_rounds:
            rounds += 1

            try:
                response = requests.post(
                    f"{self.host}/v1/chat/completions",
                    json={
                        "model": self.model,
                        "messages": local_messages,
                        "tools": tool_definitions or None,
                        "stream": False,
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
            except requests.Timeout:
                logger.warning("OllamaToolLoop: timeout after %.1fs → escalate", self.timeout)
                return EscalateSignal
            except requests.RequestException as exc:
                logger.warning("OllamaToolLoop: request error %s → escalate", exc)
                return EscalateSignal

            # Tool call handling and response extraction added in Task 5
            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content") or ""

            # Explicit ESCALATE signal
            if content.strip() == self.ESCALATE_WORD:
                logger.info("OllamaToolLoop: model signalled ESCALATE → escalate")
                return EscalateSignal

            # Final text response (tool_calls handled in Task 5)
            if content:
                self._commit_to_state(user_message, content)
                logger.info("OllamaToolLoop: response ready in %d round(s)", rounds)
                return content

            logger.warning("OllamaToolLoop: empty response → escalate")
            return EscalateSignal

        logger.warning("OllamaToolLoop: max rounds (%d) reached → escalate", self.max_rounds)
        return EscalateSignal

    def _build_local_messages(self, system_prompt: str, user_message: str) -> list[dict]:
        """
        Build an OpenAI-format message list from ConversationState history.

        Only plain text user/assistant turns are included — Anthropic tool
        blocks are skipped since Ollama uses a different tool format.
        """
        local = [{"role": "system", "content": system_prompt}]
        for msg in self.conversation_state.select_messages_for_prompt():
            role = msg.get("role")
            content = msg.get("content")
            if role == "user" and isinstance(content, str):
                local.append({"role": "user", "content": content})
            elif role == "assistant" and isinstance(content, list):
                text = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ).strip()
                if text:
                    local.append({"role": "assistant", "content": text})
        local.append({"role": "user", "content": user_message})
        return local

    def _build_tool_definitions(self) -> list[dict]:
        """Convert Anthropic-format tool definitions to OpenAI format."""
        result = []
        for td in self.skill_loader.get_tool_definitions():
            result.append({
                "type": "function",
                "function": {
                    "name": td["name"],
                    "description": td.get("description", ""),
                    "parameters": td.get("input_schema", {}),
                },
            })
        return result

    def _commit_to_state(self, user_message: str, assistant_response: str) -> None:
        """Commit a successful turn to ConversationState."""
        self.conversation_state.append_user_text(user_message)
        self.conversation_state.append_assistant_content(
            [{"type": "text", "text": assistant_response}]
        )
        self.conversation_state.prune()

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
                    logger.info("OllamaToolLoop: skill filed memory: %s", filename)
            cleaned = cleaned.replace(match.group(0), "")
        return cleaned.strip() or "Skill completed with no output"
```

- [ ] **Step 4: Run all ollama loop tests**

```bash
cd miniclaw && source .venv/bin/activate
python -m pytest tests/test_ollama_tool_loop.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/ollama_tool_loop.py tests/test_ollama_tool_loop.py
git commit -m "feat: add OllamaToolLoop skeleton with EscalateSignal and HTTP client"
```

---

## Task 5: `core/ollama_tool_loop.py` — tool call handling + escalation triggers

**Files:**
- Modify: `core/ollama_tool_loop.py` (complete `run()` with tool call branch)
- Modify: `tests/test_ollama_tool_loop.py` (add tool call + escalation trigger tests)

- [ ] **Step 1: Add failing tests for tool calls and escalation triggers**

Append to `tests/test_ollama_tool_loop.py` before `if __name__ == "__main__":`:

```python
class TestEscalateTriggers(unittest.TestCase):

    def _run_with_response(self, response_json, skill_loader=None, container_manager=None):
        from core.ollama_tool_loop import EscalateSignal

        loop = _make_loop(
            skill_loader=skill_loader or MagicMock(
                get_tool_definitions=MagicMock(return_value=[]),
                get_skill=MagicMock(return_value=None),
            ),
            container_manager=container_manager or MagicMock(),
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_json
        mock_resp.raise_for_status.return_value = None
        with patch("requests.post", return_value=mock_resp):
            return loop.run("play some jazz", "you are a voice assistant")

    def test_explicit_escalate_word_escalates(self):
        from core.ollama_tool_loop import EscalateSignal
        result = self._run_with_response(_make_response(content="ESCALATE"))
        self.assertIs(result, EscalateSignal)

    def test_empty_response_escalates(self):
        from core.ollama_tool_loop import EscalateSignal
        result = self._run_with_response(_make_response(content=""))
        self.assertIs(result, EscalateSignal)

    def test_unknown_tool_name_escalates(self):
        from core.ollama_tool_loop import EscalateSignal

        tool_call = {
            "id": "call_1",
            "function": {"name": "nonexistent_skill", "arguments": "{}"},
        }
        sl = MagicMock()
        sl.get_tool_definitions.return_value = []
        sl.get_skill.return_value = None  # skill not found

        result = self._run_with_response(
            _make_response(tool_calls=[tool_call]),
            skill_loader=sl,
        )
        self.assertIs(result, EscalateSignal)

    def test_malformed_tool_args_escalates(self):
        from core.ollama_tool_loop import EscalateSignal

        fake_skill = MagicMock()
        sl = MagicMock()
        sl.get_tool_definitions.return_value = []
        sl.get_skill.return_value = fake_skill  # skill exists

        tool_call = {
            "id": "call_1",
            "function": {"name": "weather", "arguments": "NOT_VALID_JSON"},
        }
        result = self._run_with_response(
            _make_response(tool_calls=[tool_call]),
            skill_loader=sl,
        )
        self.assertIs(result, EscalateSignal)

    def test_plain_text_response_returned_as_string(self):
        result = self._run_with_response(_make_response(content="The weather is sunny."))
        self.assertEqual(result, "The weather is sunny.")

    def test_successful_tool_call_returns_string(self):
        fake_skill = MagicMock()
        sl = MagicMock()
        sl.get_tool_definitions.return_value = []
        sl.get_skill.return_value = fake_skill

        cm = MagicMock()
        cm.execute_skill.return_value = "Currently 18°C and cloudy in London."

        # First response: tool call. Second response: final text.
        tool_call_response = _make_response(
            tool_calls=[{
                "id": "call_1",
                "function": {"name": "weather", "arguments": '{"city": "London"}'},
            }]
        )
        final_response = _make_response(content="It is 18 degrees and cloudy in London.")

        mock_resp_1 = MagicMock()
        mock_resp_1.json.return_value = tool_call_response
        mock_resp_1.raise_for_status.return_value = None

        mock_resp_2 = MagicMock()
        mock_resp_2.json.return_value = final_response
        mock_resp_2.raise_for_status.return_value = None

        with patch("requests.post", side_effect=[mock_resp_1, mock_resp_2]):
            result = _make_loop(skill_loader=sl, container_manager=cm).run(
                "what's the weather in London", "you are a voice assistant"
            )

        self.assertEqual(result, "It is 18 degrees and cloudy in London.")
        cm.execute_skill.assert_called_once_with(fake_skill, {"city": "London"})

    def test_successful_turn_commits_to_conversation_state(self):
        from core.conversation_state import ConversationState
        state = ConversationState()
        loop = _make_loop(conversation_state=state)
        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_response(content="Sure, playing jazz.")
        mock_resp.raise_for_status.return_value = None
        with patch("requests.post", return_value=mock_resp):
            loop.run("play jazz", "system prompt")
        self.assertEqual(len(state.messages), 2)
        self.assertEqual(state.messages[0]["role"], "user")
        self.assertEqual(state.messages[1]["role"], "assistant")
```

- [ ] **Step 2: Run to confirm new tests fail**

```bash
cd miniclaw && source .venv/bin/activate
python -m pytest tests/test_ollama_tool_loop.py::TestEscalateTriggers -v 2>&1 | tail -20
```

Expected: tool call tests FAIL (tool call branch not yet implemented in `run()`).

- [ ] **Step 3: Complete `run()` in `core/ollama_tool_loop.py`**

Replace the `run()` method body (from `data = response.json()` to end of while loop) with:

```python
            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content") or ""
            finish_reason = choice.get("finish_reason", "stop")

            # Explicit ESCALATE signal from model
            if content.strip() == self.ESCALATE_WORD:
                logger.info("OllamaToolLoop: model signalled ESCALATE → escalate")
                return EscalateSignal

            # Tool calls
            if finish_reason == "tool_calls" and message.get("tool_calls"):
                tool_calls = message["tool_calls"]
                local_messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                })

                for tc in tool_calls:
                    tool_name = tc["function"]["name"]
                    skill = self.skill_loader.get_skill(tool_name)
                    if not skill:
                        logger.warning(
                            "OllamaToolLoop: unknown tool %r → escalate", tool_name
                        )
                        return EscalateSignal

                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except (json.JSONDecodeError, KeyError):
                        logger.warning(
                            "OllamaToolLoop: malformed args for %r → escalate", tool_name
                        )
                        return EscalateSignal

                    result = self.container_manager.execute_skill(skill, args)
                    result = self._extract_and_save_remember(result)
                    logger.info("OllamaToolLoop: tool %s → %s", tool_name, result[:100])

                    local_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    })
                continue

            # Final text response
            if content:
                self._commit_to_state(user_message, content)
                logger.info("OllamaToolLoop: response ready in %d round(s)", rounds)
                return content

            logger.warning("OllamaToolLoop: empty response → escalate")
            return EscalateSignal
```

- [ ] **Step 4: Run all OllamaToolLoop tests**

```bash
cd miniclaw && source .venv/bin/activate
python -m pytest tests/test_ollama_tool_loop.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add core/ollama_tool_loop.py tests/test_ollama_tool_loop.py
git commit -m "feat: complete OllamaToolLoop with tool call handling and all escalation triggers"
```

---

## Task 6: Wire into `core/orchestrator.py` + update `.env.example`

**Files:**
- Modify: `core/orchestrator.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing orchestrator routing tests**

Create `tests/test_orchestrator_routing.py`:

```python
# tests/test_orchestrator_routing.py
"""Tests for Orchestrator tiered routing when OLLAMA_ENABLED=true."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_orchestrator_with_mocks():
    """
    Build an Orchestrator with all external dependencies mocked so no
    real API calls, containers, or file I/O occur.
    """
    from core.orchestrator import Orchestrator

    with (
        patch("core.orchestrator.SkillLoader") as MockSL,
        patch("core.orchestrator.ContainerManager"),
        patch("core.orchestrator.ConversationState"),
        patch("core.orchestrator.MemoryProvider"),
        patch("core.orchestrator.PromptBuilder") as MockPB,
        patch("core.orchestrator.SkillSelector") as MockSS,
        patch("core.orchestrator.ToolLoop") as MockTL,
        patch("anthropic.Anthropic"),
    ):
        MockSL.return_value.load_all.return_value = {}
        MockSL.return_value.skipped_skills = {}
        MockSL.return_value.invalid_skills = {}
        MockSS.return_value.available = False
        MockPB.return_value.build.return_value = "system prompt"
        MockTL.return_value.run.return_value = "Claude response"

        orch = Orchestrator.__new__(Orchestrator)
        orch.client = MagicMock()
        orch.model = "test-model"
        orch.skill_loader = MockSL.return_value
        orch.skills = {}
        orch.skill_selector = MockSS.return_value
        orch.container_manager = MagicMock()
        orch.conversation_state = MagicMock()
        orch.memory_provider = MagicMock()
        orch.prompt_builder = MockPB.return_value
        orch.tool_loop = MockTL.return_value
        orch._startup_context = ""
        orch.system_prompt = "system prompt"
        orch._tier_router = None
        orch._ollama_tool_loop = None
        return orch


class TestOrchestratorRoutingDisabled(unittest.TestCase):

    def test_process_message_goes_to_claude_when_ollama_disabled(self):
        orch = _make_orchestrator_with_mocks()
        orch._tier_router = None
        result = orch.process_message("play some jazz")
        orch.tool_loop.run.assert_called_once()
        self.assertEqual(result, "Claude response")


class TestOrchestratorRoutingEnabled(unittest.TestCase):

    def _make_router(self, tier):
        from core.tier_router import RouteResult
        router = MagicMock()
        router.route.return_value = RouteResult(tier=tier)
        return router

    def test_claude_route_goes_to_tool_loop(self):
        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("claude")
        orch._ollama_tool_loop = MagicMock()
        result = orch.process_message("install a new skill")
        orch.tool_loop.run.assert_called_once()
        orch._ollama_tool_loop.run.assert_not_called()
        self.assertEqual(result, "Claude response")

    def test_ollama_route_goes_to_ollama_loop(self):
        from core.ollama_tool_loop import EscalateSignal

        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("ollama")
        orch._ollama_tool_loop = MagicMock()
        orch._ollama_tool_loop.run.return_value = "Ollama response"
        result = orch.process_message("play some jazz")
        orch._ollama_tool_loop.run.assert_called_once()
        orch.tool_loop.run.assert_not_called()
        self.assertEqual(result, "Ollama response")

    def test_ollama_escalation_falls_back_to_claude(self):
        from core.ollama_tool_loop import EscalateSignal

        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("ollama")
        orch._ollama_tool_loop = MagicMock()
        orch._ollama_tool_loop.run.return_value = EscalateSignal
        result = orch.process_message("something complex")
        orch._ollama_tool_loop.run.assert_called_once()
        orch.tool_loop.run.assert_called_once()
        self.assertEqual(result, "Claude response")

    def test_direct_skill_route_calls_container_manager(self):
        from core.tier_router import RouteResult

        fake_skill = MagicMock()
        orch = _make_orchestrator_with_mocks()
        orch.skills = {"soundcloud_play": fake_skill}
        orch.container_manager.execute_skill.return_value = "Stopped."
        router = MagicMock()
        router.route.return_value = RouteResult(
            tier="direct", skill="soundcloud_play", args={"action": "stop"}
        )
        orch._tier_router = router
        orch._ollama_tool_loop = MagicMock()
        result = orch.process_message("stop")
        orch.container_manager.execute_skill.assert_called_once_with(
            fake_skill, {"action": "stop"}
        )
        orch.tool_loop.run.assert_not_called()
        self.assertEqual(result, "Stopped.")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd miniclaw && source .venv/bin/activate
python -m pytest tests/test_orchestrator_routing.py -v 2>&1 | tail -20
```

Expected: routing tests FAIL (`_tier_router` attribute missing from `process_message` logic, `_execute_direct` not defined).

- [ ] **Step 3: Update `core/orchestrator.py`**

Add import at the top of the file (after existing imports):

```python
import os
```

Add `_tier_router` and `_ollama_tool_loop` initialisation at the end of `__init__` (before the closing logger.info call):

```python
        # Tiered intelligence — optional, gated by OLLAMA_ENABLED env var.
        # When disabled, all requests go through Claude's ToolLoop unchanged.
        self._tier_router = None
        self._ollama_tool_loop = None
        if os.getenv("OLLAMA_ENABLED", "false").lower() == "true":
            from core.tier_router import TierRouter
            from core.ollama_tool_loop import OllamaToolLoop
            _patterns_path = Path(__file__).parent.parent / "config" / "intent_patterns.yaml"
            _claude_only = set(
                os.getenv("CLAUDE_ONLY_SKILLS", "install_skill").split(",")
            )
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
                timeout_seconds=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "8")),
            )
            logger.info(
                "Tiered routing enabled: ollama_model=%s, claude_only=%s",
                os.getenv("OLLAMA_MODEL", "phi4-mini"),
                _claude_only,
            )
```

Replace the `process_message` method:

```python
    def process_message(self, user_message: str) -> str:
        """Process a user message through the tiered intelligence stack."""
        system_prompt = self._build_system_prompt(user_message=user_message)

        if self._tier_router is None:
            # OLLAMA_ENABLED=false — existing Claude-only path, unchanged.
            return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)

        route = self._tier_router.route(user_message)
        logger.debug("TierRouter: %s → tier=%s", user_message[:60], route.tier)

        if route.tier == "direct":
            return self._execute_direct(route, system_prompt, user_message)

        if route.tier == "claude":
            return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)

        # Ollama tier
        from core.ollama_tool_loop import EscalateSignal
        result = self._ollama_tool_loop.run(
            user_message=user_message, system_prompt=system_prompt
        )
        if result is EscalateSignal:
            logger.info("OllamaToolLoop escalated → Claude")
            return self.tool_loop.run(
                user_message=user_message, system_prompt=system_prompt
            )
        return result
```

Add `_execute_direct` method after `process_message`:

```python
    def _execute_direct(self, route, system_prompt: str, user_message: str) -> str:
        """Execute a dispatch-pattern route without any LLM involvement."""
        if route.action == "close_session":
            return self.close_session()

        if route.skill:
            skill = self.skills.get(route.skill)
            if skill:
                result = self.container_manager.execute_skill(skill, route.args)
                return result or "Done."

        # Dispatch resolution failed — fall back to Claude
        logger.warning(
            "_execute_direct: could not resolve skill=%r, falling back to Claude",
            route.skill,
        )
        return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)
```

- [ ] **Step 4: Update `.env.example`**

Add after the `SKILL_SELECT_TOP_K` block:

```bash
# Tiered Intelligence (optional — requires local Ollama installation)
# When OLLAMA_ENABLED=true, routine commands are handled by a local Ollama model.
# Claude is only invoked for complex, ambiguous, or meta requests.
# Leave unset or false until Raspberry Pi + AI HAT+ hardware is available.
# OLLAMA_ENABLED=false
# OLLAMA_HOST=http://localhost:11434
# OLLAMA_MODEL=phi4-mini
# OLLAMA_TIMEOUT_SECONDS=8
# CLAUDE_ONLY_SKILLS=install_skill
```

- [ ] **Step 5: Run all tests**

```bash
cd miniclaw && source .venv/bin/activate
python -m pytest tests/ -v --ignore=tests/test_voice_mode.py
```

Expected: all tests PASS. (test_voice_mode.py is excluded — it requires audio hardware.)

- [ ] **Step 6: Commit**

```bash
git add core/orchestrator.py .env.example tests/test_orchestrator_routing.py
git commit -m "feat: wire TierRouter and OllamaToolLoop into Orchestrator behind OLLAMA_ENABLED flag"
```

---

## Task 7: Update `WORKING_MEMORY.md`

**Files:**
- Modify: `WORKING_MEMORY.md`

- [ ] **Step 1: Update the architecture and milestones sections**

In `WORKING_MEMORY.md`, update **Current State** to add:

```markdown
- Tiered intelligence architecture implemented (behind `OLLAMA_ENABLED=false`).
  Three tiers: deterministic → Ollama → Claude. Activate when Pi hardware arrives.
```

Update **Durable Architecture Decisions** to add:

```markdown
- Tiered routing gate: `TierRouter` classifies each transcript (<5ms, no LLM) as
  direct | ollama | claude. Ollama handles routine tool calls; Claude handles complex,
  ambiguous, and meta requests. Feature-flagged via `OLLAMA_ENABLED`.
```

Update **Open Technical Notes** to add:

```markdown
- Ollama tier not yet validated on real Pi hardware — `OLLAMA_ENABLED=false` until Pi 5 + AI HAT+ arrives.
- Ollama model size (phi4-mini default) should be revisited once RAM tier (8GB vs 16GB) is confirmed.
```

Add to **Recent Milestones**:

```markdown
- 2026-04-16:
  - designed and implemented tiered intelligence: deterministic → Ollama → Claude
  - TierRouter, OllamaToolLoop, config/intent_patterns.yaml
  - all gated behind OLLAMA_ENABLED=false; zero behaviour change until activated
```

- [ ] **Step 2: Commit**

```bash
git add WORKING_MEMORY.md
git commit -m "docs: update WORKING_MEMORY with tiered intelligence architecture and milestones"
```

---

## Validation

After all tasks complete, run the full test suite:

```bash
cd miniclaw && source .venv/bin/activate
python -m pytest tests/ -v --ignore=tests/test_voice_mode.py
```

Expected: all tests pass. `OLLAMA_ENABLED` is unset so the existing assistant behaviour is completely unchanged. To smoke-test the routing logic in isolation:

```bash
cd miniclaw && source .venv/bin/activate
python -c "
from pathlib import Path
from core.tier_router import TierRouter

router = TierRouter(Path('config/intent_patterns.yaml'))
for phrase in ['stop', 'volume up', 'goodbye', 'install a skill', 'remember this', 'play some jazz', 'what is the weather']:
    r = router.route(phrase)
    print(f'{phrase!r:45} → {r.tier} skill={r.skill} action={r.action}')
"
```

Expected output:
```
'stop'                                        → direct skill=soundcloud_play action=None
'volume up'                                   → direct skill=soundcloud_play action=None
'goodbye'                                     → direct skill=None action=close_session
'install a skill'                             → claude skill=None action=None
'remember this'                               → claude skill=None action=None
'play some jazz'                              → ollama skill=None action=None
'what is the weather'                         → ollama skill=None action=None
```
