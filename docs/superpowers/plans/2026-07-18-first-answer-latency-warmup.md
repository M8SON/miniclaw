# First-Answer Latency Warm-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut the first-answer voice latency (~8s → ~5.5s) by warming the cold LLM prompt cache on wake and warming Whisper at boot, and fix the profiling flag so `.env` `KAIZEN_PROFILE` works.

**Architecture:** Three independent, log-and-swallow warm-ups plus a config fix. `Orchestrator.warm_prompt_cache()` sends a `max_tokens=1` request with the byte-identical Sonnet `tools` + `stable` prefix so the first real turn reads cache instead of writing it; it runs in a daemon thread fired on wake. `VoiceInterface.warm_stt()` feeds silence through the STT backend at boot to remove Whisper's cold-start. `main.py` reorders `load_dotenv()` before the `core` imports so `KAIZEN_PROFILE` is seen at import. A single `VOICE_WARMUP` env flag (default on) gates both warm-ups.

**Tech Stack:** Python 3, Anthropic SDK, openai-whisper / faster-whisper, pytest + unittest, `numpy`, `wave`.

## Global Constraints

- Warm-ups MUST be fire-and-forget and MUST NOT raise into the voice loop — wrap bodies in `try/except Exception` and log at WARNING.
- Warm-ups MUST NOT mutate shared state: no `conversation_state` writes, no archive callback, no tool execution.
- The warm cache request MUST target `self.model` (Sonnet — the cold-cache path), `max_tokens=1`, and carry a `cache_control: {"type": "ephemeral"}` breakpoint on the stable system block.
- `VOICE_WARMUP` env var, default `"true"`, gates both warm-ups. Read with `os.getenv("VOICE_WARMUP", "true").strip().lower() == "true"`.
- Follow existing test style: `unittest.TestCase` + `Orchestrator.__new__(Orchestrator)` + `MagicMock`/`SimpleNamespace` for orchestrator tests; pytest `monkeypatch`/`caplog` for module-level behavior. Run tests with `python -m pytest <path> -v`.
- Match existing code style; surgical changes only.

---

### Task 1: Fix profiling flag import ordering

`core/profiling.py` reads `KAIZEN_PROFILE` at import (`_refresh_enabled()` on line 30). `main.py` imports `core.profiling` (line 29) *before* `load_dotenv()` (line 35), so `.env`-based `KAIZEN_PROFILE` is inert. Move `load_dotenv()` above the `core` imports.

**Files:**
- Modify: `main.py` (import block, lines ~14-35)
- Test: `tests/test_main_profiling_env_order.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing importable; a source-ordering guarantee other tasks rely on for on-device profiling.

- [ ] **Step 1: Write the failing test**

Create `tests/test_main_profiling_env_order.py`:

```python
"""Guard: load_dotenv() must run before core.profiling is imported, or
KAIZEN_PROFILE in .env is read too late and profiling stays disabled."""

import re
from pathlib import Path


def test_load_dotenv_precedes_core_profiling_import():
    src = (Path(__file__).parent.parent / "main.py").read_text()
    dotenv_call = re.search(r"^load_dotenv\(\)", src, re.MULTILINE)
    profiling_import = re.search(
        r"^from core import profiling", src, re.MULTILINE
    )
    assert dotenv_call is not None, "load_dotenv() call not found in main.py"
    assert profiling_import is not None, "core.profiling import not found"
    assert dotenv_call.start() < profiling_import.start(), (
        "load_dotenv() must be called before `from core import profiling` "
        "so KAIZEN_PROFILE from .env is visible at profiling import time"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_main_profiling_env_order.py -v`
Expected: FAIL (`load_dotenv()` currently appears after the profiling import).

- [ ] **Step 3: Reorder `load_dotenv()` in `main.py`**

This is a pure move of one line — do not rewrite any other import. Cut the existing `load_dotenv()` call from its current location (~line 35) and paste it immediately after the `from dotenv import load_dotenv` line (~line 20), so it runs *before* the first `from core.skill_cli …` / `from core import profiling` imports. Add a one-line comment above it:

```python
from dotenv import load_dotenv

# Load .env BEFORE importing core modules — core.profiling reads
# KAIZEN_PROFILE at import time, so the flag must be in the environment first.
load_dotenv()
```

Leave every other import (including the `skill_cli` conditional block and all `from core …` lines) exactly where it is. Verify `load_dotenv()` now appears exactly once in the file.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_main_profiling_env_order.py -v`
Expected: PASS

- [ ] **Step 5: Run the existing profiling + main tests for no regression**

Run: `python -m pytest tests/test_profiling.py tests/test_main_location.py tests/test_main_voice_backend_selection.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main_profiling_env_order.py
git commit -m "fix: load .env before importing core.profiling so KAIZEN_PROFILE works"
```

---

### Task 2: `Orchestrator.warm_prompt_cache()`

Add a method that writes the Sonnet prompt cache with a minimal request so the first real Sonnet turn reads it.

**Files:**
- Modify: `core/orchestrator.py` (add method near `_build_system_prompt_split`, ~line 245)
- Test: `tests/test_orchestrator_warm_cache.py` (create)

**Interfaces:**
- Consumes: `self.client` (Anthropic), `self.model` (str), `self.skill_loader.get_tool_definitions() -> list`, `self._build_system_prompt_split(user_message=None) -> tuple[str, str]`.
- Produces: `Orchestrator.warm_prompt_cache() -> None` — fire-and-forget; never raises; no shared-state mutation. Called by Task 4.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrator_warm_cache.py`:

```python
"""Tests for Orchestrator.warm_prompt_cache — the wake-triggered warm-up
that pre-writes the Sonnet prompt cache."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_orchestrator():
    from core.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.client = MagicMock()
    orch.model = "claude-sonnet-test"
    orch.skill_loader = MagicMock()
    orch.skill_loader.get_tool_definitions.return_value = [
        {"name": "get_weather", "description": "w", "input_schema": {}}
    ]
    # _build_system_prompt_split is exercised for real elsewhere; here we
    # stub it so we can assert byte-identity of the cached block.
    orch._build_system_prompt_split = MagicMock(return_value=("STABLE_PREFIX", "DYNAMIC"))
    orch.conversation_state = MagicMock()
    orch.client.messages.create.return_value = SimpleNamespace(content=[])
    return orch


class TestWarmPromptCache(unittest.TestCase):
    def test_sends_one_minimal_request(self):
        orch = _make_orchestrator()
        orch.warm_prompt_cache()
        orch.client.messages.create.assert_called_once()
        kwargs = orch.client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-sonnet-test")
        self.assertEqual(kwargs["max_tokens"], 1)

    def test_caches_the_stable_prefix_verbatim(self):
        orch = _make_orchestrator()
        orch.warm_prompt_cache()
        kwargs = orch.client.messages.create.call_args.kwargs
        system = kwargs["system"]
        self.assertEqual(system[0]["text"], "STABLE_PREFIX")
        self.assertEqual(system[0]["cache_control"], {"type": "ephemeral"})

    def test_builds_split_with_no_user_message(self):
        orch = _make_orchestrator()
        orch.warm_prompt_cache()
        orch._build_system_prompt_split.assert_called_once_with(user_message=None)

    def test_sends_full_tool_list(self):
        orch = _make_orchestrator()
        orch.warm_prompt_cache()
        kwargs = orch.client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["tools"], orch.skill_loader.get_tool_definitions.return_value)

    def test_does_not_touch_conversation_state(self):
        orch = _make_orchestrator()
        orch.warm_prompt_cache()
        orch.conversation_state.append_user_text.assert_not_called()

    def test_swallows_client_errors(self):
        orch = _make_orchestrator()
        orch.client.messages.create.side_effect = RuntimeError("api down")
        # Must not raise.
        orch.warm_prompt_cache()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_orchestrator_warm_cache.py -v`
Expected: FAIL with `AttributeError: 'Orchestrator' object has no attribute 'warm_prompt_cache'`.

- [ ] **Step 3: Implement the method**

In `core/orchestrator.py`, add after `_build_system_prompt_split` (around line 245). Confirm `import anthropic` is already present at the top of the file (it is — `anthropic.Anthropic(...)` is used in `__init__`):

```python
    def warm_prompt_cache(self) -> None:
        """Pre-write the Sonnet prompt cache so the first turn after wake
        reads it instead of paying the cold cache_write.

        Fire-and-forget: sends one max_tokens=1 request carrying the same
        stable system prefix and full tool list a real Sonnet turn sends,
        with a cache breakpoint on the stable block. Never mutates
        conversation state, never runs tools, never raises.
        """
        try:
            stable, _dynamic = self._build_system_prompt_split(user_message=None)
            tools = self.skill_loader.get_tool_definitions()
            self.client.messages.create(
                model=self.model,
                max_tokens=1,
                system=[
                    {
                        "type": "text",
                        "text": stable,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=tools if tools else anthropic.NOT_GIVEN,
                messages=[{"role": "user", "content": "."}],
            )
        except Exception:
            logger.warning("warm_prompt_cache failed", exc_info=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_orchestrator_warm_cache.py -v`
Expected: PASS (all 6).

- [ ] **Step 5: Commit**

```bash
git add core/orchestrator.py tests/test_orchestrator_warm_cache.py
git commit -m "feat: Orchestrator.warm_prompt_cache to pre-warm Sonnet cache on wake"
```

---

### Task 3: `VoiceInterface.warm_stt()`

Add a backend-agnostic Whisper warm-up that feeds ~0.5s of silence through the STT backend to trigger its cold first-inference at boot.

**Files:**
- Modify: `core/voice.py` (add method to `VoiceInterface`, near `_transcribe`, ~line 674)
- Test: `tests/test_voice_warm_stt.py` (create)

**Interfaces:**
- Consumes: `self.stt_backend.transcribe_file(path: str) -> str`, `self.RATE`, `self.CHANNELS`.
- Produces: `VoiceInterface.warm_stt() -> None` — fire-and-forget; never raises. Called by Task 4.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_voice_warm_stt.py`:

```python
"""Tests for VoiceInterface.warm_stt — boot-time Whisper warm-up."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_voice():
    from core.voice import VoiceInterface

    v = VoiceInterface.__new__(VoiceInterface)
    v.RATE = 16000
    v.CHANNELS = 1
    v.FORMAT = 8  # pyaudio.paInt16; unused by warm_stt but set for parity
    v.stt_backend = MagicMock()
    v.stt_backend.transcribe_file.return_value = ""
    return v


class TestWarmStt(unittest.TestCase):
    def test_calls_transcribe_once(self):
        v = _make_voice()
        v.warm_stt()
        v.stt_backend.transcribe_file.assert_called_once()

    def test_passes_an_existing_wav_path(self):
        v = _make_voice()
        captured = {}

        def _capture(path):
            captured["path"] = path
            assert Path(path).exists(), "warm_stt must pass a real wav file"
            return ""

        v.stt_backend.transcribe_file.side_effect = _capture
        v.warm_stt()
        assert captured["path"].endswith(".wav")

    def test_swallows_backend_errors(self):
        v = _make_voice()
        v.stt_backend.transcribe_file.side_effect = RuntimeError("model exploded")
        # Must not raise.
        v.warm_stt()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_voice_warm_stt.py -v`
Expected: FAIL with `AttributeError: 'VoiceInterface' object has no attribute 'warm_stt'`.

- [ ] **Step 3: Implement the method**

In `core/voice.py`, add to `VoiceInterface` right after `_transcribe` (around line 680). `wave`, `tempfile`, `numpy as np`, and `os` are already imported at the top of the file:

```python
    def warm_stt(self) -> None:
        """Warm the STT backend's cold first-inference by decoding ~0.5s of
        silence at boot. Backend-agnostic (goes through transcribe_file), so
        it works for openai-whisper, faster-whisper, and Hailo backends.
        Fire-and-forget: never raises."""
        path = None
        try:
            silence = np.zeros(int(self.RATE * 0.5), dtype=np.int16)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                path = tmp.name
            with wave.open(path, "wb") as wf:
                wf.setnchannels(self.CHANNELS)
                wf.setsampwidth(2)  # int16
                wf.setframerate(self.RATE)
                wf.writeframes(silence.tobytes())
            self.stt_backend.transcribe_file(path)
            logger.info("STT warm-up complete")
        except Exception:
            logger.warning("warm_stt failed", exc_info=True)
        finally:
            if path is not None:
                try:
                    os.unlink(path)
                except OSError:
                    pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_voice_warm_stt.py -v`
Expected: PASS (all 3).

- [ ] **Step 5: Commit**

```bash
git add core/voice.py tests/test_voice_warm_stt.py
git commit -m "feat: VoiceInterface.warm_stt to warm Whisper cold-start at boot"
```

---

### Task 4: Wire the warm-ups into the voice loop behind `VOICE_WARMUP`

Fire `warm_stt()` at boot and `warm_prompt_cache()` on wake, both in daemon threads, gated by `VOICE_WARMUP`.

**Files:**
- Modify: `main.py` (add a `_spawn_warmup` helper; call sites in `run_voice_mode` at boot ~line 252 and in the wake loop after `wait_for_wake_word()` ~line 309)
- Test: `tests/test_main_warmup_wiring.py` (create)

**Interfaces:**
- Consumes: `Orchestrator.warm_prompt_cache` (Task 2), `VoiceInterface.warm_stt` (Task 3).
- Produces: `main._spawn_warmup(fn) -> threading.Thread | None` — spawns a daemon thread running `fn` when `VOICE_WARMUP` is enabled, else returns `None` without calling `fn`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_main_warmup_wiring.py`:

```python
"""Tests for the VOICE_WARMUP gate helper in main."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import main


def test_spawn_warmup_runs_fn_when_enabled(monkeypatch):
    monkeypatch.setenv("VOICE_WARMUP", "true")
    called = MagicMock()
    t = main._spawn_warmup(called)
    assert t is not None
    t.join(timeout=2)
    called.assert_called_once()


def test_spawn_warmup_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("VOICE_WARMUP", "false")
    called = MagicMock()
    t = main._spawn_warmup(called)
    assert t is None
    called.assert_not_called()


def test_spawn_warmup_default_is_enabled(monkeypatch):
    monkeypatch.delenv("VOICE_WARMUP", raising=False)
    called = MagicMock()
    t = main._spawn_warmup(called)
    assert t is not None
    t.join(timeout=2)
    called.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_main_warmup_wiring.py -v`
Expected: FAIL with `AttributeError: module 'main' has no attribute '_spawn_warmup'`.

- [ ] **Step 3: Add the helper to `main.py`**

`main.py` does not import `threading` yet — add `import threading` to the stdlib import block at the top (alongside `import os`, `import sys`, `import signal`). Then add the helper near the other module-level helpers (after the logging setup, before `run_voice_mode`):

```python
def _spawn_warmup(fn):
    """Run a warm-up callable in a daemon thread when VOICE_WARMUP is on.

    Returns the started Thread, or None when disabled. Warm-ups are
    fire-and-forget; the caller never joins them in production."""
    if os.getenv("VOICE_WARMUP", "true").strip().lower() != "true":
        return None
    t = threading.Thread(target=fn, daemon=True, name=f"warmup-{getattr(fn, '__name__', 'fn')}")
    t.start()
    return t
```

- [ ] **Step 4: Run the helper tests to verify they pass**

Run: `python -m pytest tests/test_main_warmup_wiring.py -v`
Expected: PASS (all 3).

- [ ] **Step 5: Add the boot STT warm-up call site**

In `run_voice_mode` (`main.py`), right after `voice.play_startup_sound()` (~line 252) and before the greeting, add:

```python
        # Warm Whisper's cold first-inference during the greeting so the
        # first real transcription isn't cold. Overlaps greeting LLM+TTS.
        _spawn_warmup(voice.warm_stt)
```

- [ ] **Step 6: Add the wake cache warm-up call site**

In the wake loop, immediately after `detected = voice.wait_for_wake_word()` succeeds and before `print("Listening...")` (~line 313), add:

```python
            # Warm the Sonnet prompt cache while the user speaks their first
            # request, so the first turn reads cache instead of writing it.
            _spawn_warmup(orchestrator.warm_prompt_cache)
```

- [ ] **Step 7: Run the full affected test set for no regression**

Run: `python -m pytest tests/test_main_warmup_wiring.py tests/test_main_location.py tests/test_main_voice_backend_selection.py tests/test_voice_mode.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add main.py tests/test_main_warmup_wiring.py
git commit -m "feat: fire STT + prompt-cache warm-ups behind VOICE_WARMUP flag"
```

---

### Task 5: Full suite + on-device verification

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `python -m pytest -q`
Expected: PASS (no regressions from the four new modules).

- [ ] **Step 2: Deploy to the Pi and restart**

The Pi runs `feat/tts-barge-in`; check out this branch (or merged `main` once merged) on the Pi and restart:

```bash
ssh pi 'cd ~/kaizen && git fetch && git checkout feat/first-answer-warmup && systemctl --user restart kaizen.service'
```

- [ ] **Step 3: Confirm the temporary profiling workaround can be removed**

With Task 1 shipped, `KAIZEN_PROFILE=true` in `.env` now works, so the investigation-time `Environment=KAIZEN_PROFILE=true` line in the unit is redundant. Remove it and reload (unit backup: `~/.config/systemd/user/kaizen.service.bak`):

```bash
ssh pi 'sed -i "/^Environment=KAIZEN_PROFILE=true/d" ~/.config/systemd/user/kaizen.service && systemctl --user daemon-reload && systemctl --user restart kaizen.service'
```

- [ ] **Step 4: Measure one cold turn**

Say "hey jarvis" + a plain non-tool question. Then read the timing:

```bash
ssh pi 'journalctl _SYSTEMD_USER_UNIT=kaizen.service --no-pager --since "-3 minutes" | grep -E "TIMING-SUMMARY|Response ready|to first audio"'
```

Expected: first turn shows `cache_read=2549` (not `cache_write`), a lower `stt=` than the ~1615ms cold baseline, and a lower total time-to-first-word than ~7.5s.

- [ ] **Step 5: Commit any notes**

If verification reveals tuning (e.g. warm-up losing the cache race), capture it before closing out.
