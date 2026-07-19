# Looping Pre-Buffer Cue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed-length pre-buffer cue with a short R2-D2 "bloops" segment looped from response-start until speech begins, then cut cleanly.

**Architecture:** `speak_stream` gains an `on_first_audio` callback (fired at the first real device write); `speak_stream_feeder` threads it through. `VoiceInterface` gets a looping cue (`start_prebuffer_cue`/`stop_prebuffer_cue`) on its own `sd.OutputStream`. `main.py` wires start→first-delta, stop→first-audio, plus a `finally` stop. `KOKORO_CUE_MS` and the old one-shot cue are removed.

**Tech Stack:** Python 3, numpy, sounddevice (mocked in tests), pytest + unittest.

## Global Constraints

- The cue must never loop forever: it stops on first audio, and unconditionally in `main.py`'s `finally`.
- `start`/`stop_prebuffer_cue` are idempotent; errors logged and swallowed (a missing speaker can't crash the loop).
- The cue uses its OWN `sd.OutputStream` (not `sd.play`), so it doesn't disturb the Kokoro TTS stream or other cues.
- `on_first_audio` fires exactly once, at the first real audio write, and not at all when a response produces no audio.
- Cue sound = candidate 3: `_r2_beep(800,0.06)`, gap 0.03, `_r2_beep(1200,0.06)`, gap 0.03, `_r2_chirp(1000,2300,0.18,vibrato_hz=10,vibrato_depth=60)`, gap 0.02, `_r2_beep(1700,0.05)`.
- Surgical; match existing style. Interpreter is `.venv/bin/python`. Run tests with `.venv/bin/python -m pytest <path> -v`.

---

### Task 1: `on_first_audio` callback

**Files:**
- Modify: `core/voice_backends.py` (`KokoroTTSBackend.speak_stream` signature + first-audio spot)
- Test: `tests/test_kokoro_stream.py` (add tests)

**Interfaces:**
- Produces: `speak_stream(chunks, interrupt_event=None, on_first_audio=None)` — fires `on_first_audio()` once when the first real audio is written.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_kokoro_stream.py` (inside `SpeakStreamTests`):

```python
    @patch("core.voice_backends.sd")
    def test_on_first_audio_fires_once(self, mock_sd):
        import numpy as np
        backend = self._make_backend()
        backend.pipeline.side_effect = lambda *a, **k: iter(
            [("", "", np.zeros(2048, dtype=np.float32))]
        )
        calls = []
        backend.speak_stream(iter(["One.", " Two."]), on_first_audio=lambda: calls.append(1))
        self.assertEqual(len(calls), 1)

    @patch("core.voice_backends.sd")
    def test_on_first_audio_not_fired_without_audio(self, mock_sd):
        backend = self._make_backend()  # pipeline yields empty → no audio
        calls = []
        backend.speak_stream(iter(["Hello."]), on_first_audio=lambda: calls.append(1))
        self.assertEqual(calls, [])
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_kokoro_stream.py -k on_first_audio -v`
Expected: FAIL — `speak_stream() got an unexpected keyword argument 'on_first_audio'`.

- [ ] **Step 3: Add the param and fire it**

In `core/voice_backends.py`, change the signature (line ~460):

```python
    def speak_stream(self, chunks, interrupt_event=None, on_first_audio=None) -> None:
```

At the first-audio spot in `writer_worker`'s `_write` (currently around line 567):

```python
                        if first_audio_at[0] is None:
                            first_audio_at[0] = time.perf_counter()
                            if on_first_audio is not None:
                                try:
                                    on_first_audio()
                                except Exception:
                                    logger.exception("on_first_audio hook raised")
```

(The `first_audio_at[0] is None` guard already ensures once-only.)

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_kokoro_stream.py -k on_first_audio -v`
Expected: PASS

- [ ] **Step 5: Full streaming suite (no regression)**

Run: `.venv/bin/python -m pytest tests/test_kokoro_stream.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add core/voice_backends.py tests/test_kokoro_stream.py
git commit -m "feat: on_first_audio callback in Kokoro speak_stream"
```

---

### Task 2: Looping R2-D2 cue on VoiceInterface

**Files:**
- Modify: `core/voice.py` (remove `play_prebuffer_cue` + its `KOKORO_CUE_MS`; add `_prebuffer_cue_segment`, `start_prebuffer_cue`, `stop_prebuffer_cue`; add cue-handle init in `__init__`)
- Test: `tests/test_prebuffer_cue.py` (replace contents)

**Interfaces:**
- Consumes: `_r2_chirp`/`_r2_beep`, `resample`, `KOKORO_SAMPLE_RATE`, `self._output_samplerate`, `self._output_device_index`, `self.enable_tts`.
- Produces: `_prebuffer_cue_segment() -> np.ndarray`; `start_prebuffer_cue() -> None`; `stop_prebuffer_cue() -> None` (idempotent).

- [ ] **Step 1: Replace the cue tests**

Overwrite `tests/test_prebuffer_cue.py`:

```python
"""Tests for the looping R2-D2 pre-buffer cue."""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import voice as voice_mod
from core.voice import VoiceInterface
from core.voice_backends import KOKORO_SAMPLE_RATE


def _make_voice():
    v = VoiceInterface.__new__(VoiceInterface)
    v.enable_tts = True
    v._output_samplerate = KOKORO_SAMPLE_RATE
    v._output_device_index = None
    v._prebuffer_cue = None
    return v


def test_segment_is_nonempty_float32():
    v = _make_voice()
    seg = v._prebuffer_cue_segment()
    assert isinstance(seg, np.ndarray)
    assert seg.dtype == np.float32
    assert len(seg) > int(0.3 * v._output_samplerate)  # ~0.44s segment


def test_start_then_stop_lifecycle():
    v = _make_voice()
    with patch.object(voice_mod, "sd"):
        v.start_prebuffer_cue()
        assert v._prebuffer_cue is not None
        _, _, thread = v._prebuffer_cue
        v.stop_prebuffer_cue()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert v._prebuffer_cue is None


def test_stop_without_start_is_noop():
    v = _make_voice()
    with patch.object(voice_mod, "sd"):
        v.stop_prebuffer_cue()  # must not raise
        assert v._prebuffer_cue is None


def test_double_start_does_not_spawn_two():
    v = _make_voice()
    with patch.object(voice_mod, "sd"):
        v.start_prebuffer_cue()
        first = v._prebuffer_cue
        v.start_prebuffer_cue()  # idempotent
        assert v._prebuffer_cue is first
        v.stop_prebuffer_cue()


def test_noop_when_tts_disabled():
    v = _make_voice()
    v.enable_tts = False
    with patch.object(voice_mod, "sd"):
        v.start_prebuffer_cue()
        assert v._prebuffer_cue is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_prebuffer_cue.py -v`
Expected: FAIL — `_prebuffer_cue_segment` / `start_prebuffer_cue` don't exist.

- [ ] **Step 3: Add the cue handle to `__init__`**

In `VoiceInterface.__init__` (near the other `self._...` audio-resource fields, e.g. after `self._barge_in = None`), add:

```python
        # Looping pre-buffer cue: (stream, stop_event, thread) or None.
        self._prebuffer_cue = None
```

- [ ] **Step 4: Replace `play_prebuffer_cue` with the segment builder + start/stop**

In `core/voice.py`, delete the entire `play_prebuffer_cue` method (and its `KOKORO_CUE_MS` `os.getenv`) and put in its place:

```python
    def _prebuffer_cue_segment(self) -> "np.ndarray":
        """One R2-D2 'questioning bloops' segment (~0.44s), looped by the cue."""
        gs = np.zeros(int(KOKORO_SAMPLE_RATE * 0.03), dtype=np.float32)
        gs2 = np.zeros(int(KOKORO_SAMPLE_RATE * 0.02), dtype=np.float32)
        sound = np.concatenate([
            self._r2_beep(800, 0.06),
            gs,
            self._r2_beep(1200, 0.06),
            gs,
            self._r2_chirp(1000, 2300, 0.18, vibrato_hz=10, vibrato_depth=60),
            gs2,
            self._r2_beep(1700, 0.05),
        ])
        return resample(sound, KOKORO_SAMPLE_RATE, self._output_samplerate)

    def start_prebuffer_cue(self) -> None:
        """Start looping the R2-D2 cue on its own output stream until
        stop_prebuffer_cue() is called. Idempotent; errors swallowed."""
        if not self.enable_tts or self._prebuffer_cue is not None:
            return
        try:
            seg = self._prebuffer_cue_segment()
            stream = sd.OutputStream(
                samplerate=self._output_samplerate,
                channels=1,
                dtype="float32",
                device=self._output_device_index,
            )
            stream.start()
            stop_event = threading.Event()

            def _loop():
                SUB = 1024
                try:
                    while not stop_event.is_set():
                        for i in range(0, len(seg), SUB):
                            block = seg[i : i + SUB]
                            if stop_event.is_set():
                                # Fade the in-flight block so the cut is clickless.
                                block = block * np.linspace(1, 0, len(block), dtype=np.float32)
                                stream.write(block)
                                return
                            stream.write(block)
                except Exception:
                    logger.exception("Pre-buffer cue loop error")

            thread = threading.Thread(target=_loop, daemon=True, name="prebuffer-cue")
            thread.start()
            self._prebuffer_cue = (stream, stop_event, thread)
        except Exception as e:
            logger.warning("Pre-buffer cue start error: %s", e)
            self._prebuffer_cue = None

    def stop_prebuffer_cue(self) -> None:
        """Stop the looping cue and tear down its stream. Idempotent."""
        handle = self._prebuffer_cue
        if handle is None:
            return
        self._prebuffer_cue = None
        stream, stop_event, thread = handle
        stop_event.set()
        thread.join(timeout=2.0)
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass
```

Confirm `os` is no longer used elsewhere in `voice.py` after removing the `KOKORO_CUE_MS` read; if it is (it is — other code uses `os`), leave the `import os`.

Note (expected transient state): `main.py` still references `voice.play_prebuffer_cue` at the streaming `on_first_chunk` hook until Task 3 rewires it. That's fine — `test_voice_mode.py` uses a `FakeVoice` that still carries the old stub, so the suite stays green; Task 3 swaps both the real wiring and the fake.

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_prebuffer_cue.py -v`
Expected: PASS (all 5).

- [ ] **Step 6: Commit**

```bash
git add core/voice.py tests/test_prebuffer_cue.py
git commit -m "feat: looping R2-D2 pre-buffer cue (start/stop); drop KOKORO_CUE_MS"
```

---

### Task 3: Wire the loop into the voice loop

**Files:**
- Modify: `core/voice.py` (`speak_stream_feeder`: add `on_first_audio` param, pass to backend), `main.py` (start/stop wiring + `finally` stop)
- Test: `tests/test_voice_mode.py` (FakeVoice stubs), `tests/test_speak_stream_feeder.py` (create, if not covered) — see below

**Interfaces:**
- Consumes: Task 1's `speak_stream(..., on_first_audio=)`, Task 2's `start/stop_prebuffer_cue`.
- Produces: `speak_stream_feeder(on_first_chunk=None, on_first_audio=None, interruptible=False)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_speak_stream_feeder.py`:

```python
"""speak_stream_feeder forwards on_first_audio to the backend."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.voice import VoiceInterface


def _voice_with_backend(backend):
    v = VoiceInterface.__new__(VoiceInterface)
    v.enable_tts = True
    v.tts_backend = backend
    v.barge_in_enabled = False
    return v


def test_feeder_forwards_on_first_audio():
    backend = MagicMock()
    backend.speak_stream = MagicMock()
    v = _voice_with_backend(backend)
    marker = lambda: None
    push, finalize = v.speak_stream_feeder(on_first_audio=marker)
    push("hello")       # spawns the consumer thread
    finalize()
    kwargs = backend.speak_stream.call_args.kwargs
    assert kwargs.get("on_first_audio") is marker
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_speak_stream_feeder.py -v`
Expected: FAIL — `speak_stream_feeder` has no `on_first_audio` param / not forwarded.

- [ ] **Step 3: Add `on_first_audio` to the feeder**

In `core/voice.py` `speak_stream_feeder`, add the param and forward it:

```python
    def speak_stream_feeder(self, on_first_chunk=None, on_first_audio=None, interruptible=False):
```

In the nested `_consume`:

```python
        def _consume():
            try:
                backend.speak_stream(
                    _gen(), interrupt_event=interrupt_event, on_first_audio=on_first_audio
                )
            except Exception:
                logger.exception("speak_stream consumer raised")
```

(The TTS-disabled early-return branch is unaffected — it returns no-op push/finalize.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_speak_stream_feeder.py -v`
Expected: PASS

- [ ] **Step 5: Wire start/stop in `main.py`**

In `main.py`'s streaming branch, change the feeder call and add the `finally`
stop. Replace:

```python
                        push_raw, finalize = voice.speak_stream_feeder(
                            on_first_chunk=voice.play_prebuffer_cue,
                            interruptible=True,
                        )
                        try:
                            response = orchestrator.process_message(
                                transcription,
                                on_chunk=push_raw,
                                on_ack_success=voice.play_ack_sound,
                            )
```

with:

```python
                        push_raw, finalize = voice.speak_stream_feeder(
                            on_first_chunk=voice.start_prebuffer_cue,
                            on_first_audio=voice.stop_prebuffer_cue,
                            interruptible=True,
                        )
                        try:
                            response = orchestrator.process_message(
                                transcription,
                                on_chunk=push_raw,
                                on_ack_success=voice.play_ack_sound,
                            )
```

Then find the matching `except Exception:` for that `try` (it calls `finalize()` and `raise`) and add a `finally` that always stops the cue:

```python
                        except Exception:
                            finalize()
                            raise
                        finally:
                            voice.stop_prebuffer_cue()
```

- [ ] **Step 6: Add FakeVoice stubs**

In `tests/test_voice_mode.py`, `FakeVoice` referenced `play_prebuffer_cue` (or `play_response_ready_sound`) at the streaming hook. Add minimal stubs so the swapped hooks resolve:

```python
    def start_prebuffer_cue(self):
        pass

    def stop_prebuffer_cue(self):
        pass
```

Remove/replace any `play_prebuffer_cue` stub if present, and update any assertion that counted it (mirror the existing `FakeVoice` pattern; do not weaken unrelated assertions).

- [ ] **Step 7: Run the affected tests**

Run: `.venv/bin/python -m pytest tests/test_speak_stream_feeder.py tests/test_voice_mode.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add core/voice.py main.py tests/test_speak_stream_feeder.py tests/test_voice_mode.py
git commit -m "feat: loop pre-buffer cue until first audio (start on delta, stop on audio)"
```

---

### Task 4: Full suite + on-device

**Files:** none.

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 2: Deploy + drop the inert env var**

After merge to `main`:

```bash
ssh pi 'cd ~/kaizen && git pull --ff-only origin main && sed -i "/^KOKORO_CUE_MS=/d" .env && systemctl --user restart kaizen.service'
```

- [ ] **Step 3: Confirm on-device**

Run a turn. Confirm: the R2-D2 bloops loop from response-start, then cut cleanly to speech with no dead air and no cue bleeding into the first words. Check the journal shows no `on_first_audio`/cue errors.
```
