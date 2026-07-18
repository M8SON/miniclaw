# Kokoro First-Audio Flush Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Kokoro first-flush threshold configurable via `KOKORO_MIN_FIRST_FLUSH` (default lowered 30→20) so the first spoken fragment breaks at an earlier clause boundary, cutting time-to-first-audio.

**Architecture:** One small change to `core/voice_backends.py`: a floor-guarded env-read helper, a lowered class-constant default, and an assignment of `self.MIN_FIRST_FLUSH` in each Kokoro backend's `__init__`. `_find_flush_boundary` already reads `self.MIN_FIRST_FLUSH`, so its logic is untouched. Then on-device tuning.

**Tech Stack:** Python 3, pytest + unittest, existing Kokoro streaming backend.

## Global Constraints

- New env var `KOKORO_MIN_FIRST_FLUSH`, default `20`, read via `os.getenv`.
- Read the env value **in each backend's `__init__`** (`KokoroTTSBackend` and `KokoroONNXBackend`) — `KokoroONNXBackend.__init__` does NOT call `super().__init__()`, and reading at construction (not import) avoids the import-order env trap.
- Floor-guard: the effective value must be `>= 1`; a non-numeric/zero/negative env value falls back safely.
- Do NOT change `_find_flush_boundary` logic, the clause-boundary set (`, ; : —`), later-sentence flushing, barge-in, or non-streaming `speak()`.
- Surgical changes only. Run tests with `python -m pytest <path> -v`.

---

### Task 1: Make `MIN_FIRST_FLUSH` env-configurable

**Files:**
- Modify: `core/voice_backends.py` (module-level helper; `KokoroTTSBackend` class constant + `__init__`; `KokoroONNXBackend.__init__`)
- Test: `tests/test_kokoro_first_flush.py` (create)

**Interfaces:**
- Consumes: `KokoroTTSBackend._find_flush_boundary(buffer, allow_clause)` (existing, reads `self.MIN_FIRST_FLUSH`).
- Produces: module-level `voice_backends._configured_min_first_flush(default: int) -> int`; both backends set `self.MIN_FIRST_FLUSH` from it during construction.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_kokoro_first_flush.py`:

```python
"""Tests for the tunable Kokoro first-flush threshold (KOKORO_MIN_FIRST_FLUSH)."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import voice_backends


# --- the env-read helper (pure, no model load) ---

def test_helper_default_when_unset(monkeypatch):
    monkeypatch.delenv("KOKORO_MIN_FIRST_FLUSH", raising=False)
    assert voice_backends._configured_min_first_flush(20) == 20


def test_helper_env_overrides(monkeypatch):
    monkeypatch.setenv("KOKORO_MIN_FIRST_FLUSH", "15")
    assert voice_backends._configured_min_first_flush(20) == 15


def test_helper_floors_zero(monkeypatch):
    monkeypatch.setenv("KOKORO_MIN_FIRST_FLUSH", "0")
    assert voice_backends._configured_min_first_flush(20) == 1


def test_helper_floors_negative(monkeypatch):
    monkeypatch.setenv("KOKORO_MIN_FIRST_FLUSH", "-5")
    assert voice_backends._configured_min_first_flush(20) == 1


def test_helper_non_numeric_falls_back(monkeypatch):
    monkeypatch.setenv("KOKORO_MIN_FIRST_FLUSH", "abc")
    assert voice_backends._configured_min_first_flush(20) == 20


# --- boundary actually moves with the threshold (pure, via __new__) ---

def _backend_with_min(min_val):
    b = voice_backends.KokoroTTSBackend.__new__(voice_backends.KokoroTTSBackend)
    b.MIN_FIRST_FLUSH = min_val
    return b


def test_lower_threshold_breaks_at_earlier_clause():
    buf = "That is a great question, and here is why."
    b20 = _backend_with_min(20)
    b30 = _backend_with_min(30)
    i20 = b20._find_flush_boundary(buf, allow_clause=True)
    i30 = b30._find_flush_boundary(buf, allow_clause=True)
    # 20 breaks at the comma (index 24); 30 ignores it (< 29) and defers later.
    assert buf[: i20 + 1] == "That is a great question,"
    assert i30 > i20


# --- __init__ reads the env (KPipeline patched so no model load) ---

def test_init_reads_env(monkeypatch):
    monkeypatch.setenv("KOKORO_MIN_FIRST_FLUSH", "15")
    with patch.object(voice_backends, "KPipeline"):
        b = voice_backends.KokoroTTSBackend()
    assert b.MIN_FIRST_FLUSH == 15


def test_init_default_is_20(monkeypatch):
    monkeypatch.delenv("KOKORO_MIN_FIRST_FLUSH", raising=False)
    with patch.object(voice_backends, "KPipeline"):
        b = voice_backends.KokoroTTSBackend()
    assert b.MIN_FIRST_FLUSH == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_kokoro_first_flush.py -v`
Expected: FAIL — `_configured_min_first_flush` does not exist (`AttributeError`), and `test_init_default_is_20` fails because the current default is 30.

- [ ] **Step 3: Add the env-read helper**

In `core/voice_backends.py`, add a module-level helper near the top of the file (after the imports; `os` is already imported at line 9):

```python
def _configured_min_first_flush(default: int) -> int:
    """First-flush threshold (chars) for TTS, from KOKORO_MIN_FIRST_FLUSH.

    Read at backend construction (after load_dotenv), not import. Floor-guarded
    so a bad/zero/negative value can't disable first-flush; non-numeric falls
    back to `default`."""
    try:
        return max(1, int(os.getenv("KOKORO_MIN_FIRST_FLUSH", str(default))))
    except (TypeError, ValueError):
        return default
```

- [ ] **Step 4: Lower the class constant default to 20**

In `core/voice_backends.py`, in `KokoroTTSBackend`, change the constant (currently `MIN_FIRST_FLUSH = 30`, around line 412):

```python
    MIN_FIRST_FLUSH = 20
```

Leave the surrounding comment (lines ~407-411) as-is; it still describes the mechanism accurately.

- [ ] **Step 5: Read the env in `KokoroTTSBackend.__init__`**

In `KokoroTTSBackend.__init__`, immediately after `self.pipeline = KPipeline(lang_code="a")` (around line 403), add:

```python
        self.MIN_FIRST_FLUSH = _configured_min_first_flush(type(self).MIN_FIRST_FLUSH)
```

- [ ] **Step 6: Read the env in `KokoroONNXBackend.__init__`**

`KokoroONNXBackend.__init__` does not call `super().__init__()`, so add the same line at the end of its `__init__`, immediately after `self.kokoro = _KokoroONNXImpl.from_session(session, str(voices_path))` (around line 743):

```python
        self.MIN_FIRST_FLUSH = _configured_min_first_flush(type(self).MIN_FIRST_FLUSH)
```

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `python -m pytest tests/test_kokoro_first_flush.py -v`
Expected: PASS (all 8).

- [ ] **Step 8: Run the existing Kokoro tests for no regression**

The new default (20) must not break the existing first-flush tests (their commas sit outside the 20–29 window, so behavior is unchanged).

Run: `python -m pytest tests/test_kokoro_stream.py -v`
Expected: PASS (all existing tests, including `test_first_flush_breaks_at_early_clause_boundary`, `test_first_flush_ignores_clause_boundary_below_min_length`, `test_clause_split_applies_only_to_first_flush`).

- [ ] **Step 9: Commit**

```bash
git add core/voice_backends.py tests/test_kokoro_first_flush.py
git commit -m "feat: tunable Kokoro first-flush size via KOKORO_MIN_FIRST_FLUSH (default 20)"
```

---

### Task 2: Deploy and tune on-device

**Files:** none (deploy + measurement only)

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 2: Deploy to the Pi**

After this branch merges to `main` and is pushed:

```bash
ssh pi 'cd ~/kaizen && git pull --ff-only origin main && systemctl --user restart kaizen.service'
```

(No `.env` change needed yet — default is now 20.)

- [ ] **Step 3: Baseline + sweep**

Say "hey jarvis" + a plain question at each setting, reading the first-audio line between runs:

```bash
ssh pi 'journalctl _SYSTEMD_USER_UNIT=kaizen.service --no-pager --since "-3 minutes" | grep -E "to first audio|TIMING-SUMMARY"'
```

Compare default (20) against a couple of alternatives by editing the Pi `.env` and restarting between them:

```bash
ssh pi 'cd ~/kaizen && sed -i "/^KOKORO_MIN_FIRST_FLUSH=/d" .env && printf "\nKOKORO_MIN_FIRST_FLUSH=15\n" >> .env && systemctl --user restart kaizen.service'
```

Try 15, 20, 25. For each, note `…ms to first audio` and listen for a pause after the opening words.

- [ ] **Step 4: Lock in the best value**

Set the chosen `KOKORO_MIN_FIRST_FLUSH` in the Pi `.env` (or remove the line to keep the default 20) and restart. Record the before/after first-audio numbers.
