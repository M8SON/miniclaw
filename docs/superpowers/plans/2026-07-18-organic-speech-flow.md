# Organic Speech Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Kokoro speech flow smoothly on the Pi CPU via concise-by-default answers, a small audio pre-buffer, an R2-D2 cue masking the pre-buffer delay, and a rebalanced first-flush default.

**Architecture:** Four mostly-independent changes: (1) a system-prompt tweak in `prompt_builder`; (2) a jitter pre-buffer in `KokoroTTSBackend.speak_stream`'s writer thread plus the first-flush default raised to 30, both env-tunable; (3) a longer R2-D2 cue in `VoiceInterface` wired to the streaming first-delta hook; (4) deploy + on-device tuning.

**Tech Stack:** Python 3, pytest + unittest, numpy, sounddevice (mocked in tests), existing Kokoro streaming backend.

## Global Constraints

- New env vars, read per-backend in `__init__` (both `KokoroTTSBackend` and `KokoroONNXBackend` — the ONNX one does NOT call `super().__init__()`), at construction (not import), floor-guarded: `KOKORO_PREBUFFER_MS` (default 1500, floor 0; 0 = disabled). `KOKORO_MIN_FIRST_FLUSH` default is raised 20→30 (still env-overridable).
- Pre-buffer must not lose or reorder audio, must not deadlock, and must preserve existing barge-in behavior (interrupt still winds threads down promptly).
- Cue is non-blocking (`sd.play`, no `sd.wait`), built from existing `_r2_*` numpy helpers, and must swallow errors (a missing speaker can't crash the loop).
- Surgical changes; match existing style. Run tests with `python -m pytest <path> -v`.

---

### Task 1: Concise-by-default answers, depth on request

**Files:**
- Modify: `core/prompt_builder.py` (`BASE_PROMPT_TEMPLATE`, the concise line ~73)
- Test: `tests/test_prompt_builder_brevity.py` (create)

**Interfaces:**
- Consumes/Produces: none in code; changes the persona system-prompt text only.

- [ ] **Step 1: Write the failing test**

Create `tests/test_prompt_builder_brevity.py`:

```python
"""Guard: the base spoken-answer prompt asks for short-by-default answers
with depth only on explicit request."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.prompt_builder import PromptBuilder


def test_base_prompt_requests_brevity_with_depth_on_request():
    tmpl = PromptBuilder.BASE_PROMPT_TEMPLATE.lower()
    # short by default
    assert "one" in tmpl and "sentence" in tmpl
    assert "short" in tmpl
    # depth on explicit request
    assert "elaborate" in tmpl or "explain" in tmpl
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_prompt_builder_brevity.py -v`
Expected: FAIL (current template says only "Keep responses concise for spoken delivery").

- [ ] **Step 3: Update the guidance line**

In `core/prompt_builder.py`, replace the single line (currently
`"- Keep responses concise for spoken delivery\n"`) with:

```python
        "- Default to short, one or two sentence answers — short and sweet. "
        "Only give a longer, detailed answer when Mason explicitly asks you to "
        "explain, elaborate, go deeper, or tell him more\n"
```

Leave every other guideline line unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_prompt_builder_brevity.py -v`
Expected: PASS

- [ ] **Step 5: Run existing prompt tests for no regression**

Run: `python -m pytest tests/test_prompt_builder_persona.py tests/test_prompt_builder_selector.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add core/prompt_builder.py tests/test_prompt_builder_brevity.py
git commit -m "feat: concise-by-default spoken answers, depth on explicit request"
```

---

### Task 2: Audio pre-buffer + rebalance first-flush default

**Files:**
- Modify: `core/voice_backends.py` (generalize the env helper; add `PREBUFFER_MS`; raise `MIN_FIRST_FLUSH` default to 30; pre-buffer in `writer_worker`)
- Test: `tests/test_kokoro_first_flush.py` (update the default assertion), `tests/test_kokoro_prebuffer.py` (create)

**Interfaces:**
- Consumes: `KokoroTTSBackend.speak_stream`, existing `_configured_min_first_flush`.
- Produces: module-level `voice_backends._configured_int(env_var, default, minimum) -> int`; both backends set `self.PREBUFFER_MS`; class default `MIN_FIRST_FLUSH = 30`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_kokoro_prebuffer.py`:

```python
"""Tests for the Kokoro pre-buffer (KOKORO_PREBUFFER_MS)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import voice_backends


def _make_backend():
    with patch.object(voice_backends, "KPipeline"):
        backend = voice_backends.KokoroTTSBackend()
    # Each flush yields one (phonemes, tokens, audio) tuple of 2048 samples.
    backend.pipeline = MagicMock()
    backend.pipeline.side_effect = lambda *a, **k: iter(
        [("", "", np.zeros(2048, dtype=np.float32))]
    )
    return backend


def _written_samples(stream):
    return sum(len(c.args[0]) for c in stream.write.call_args_list)


@patch("core.voice_backends.sd")
def test_env_wires_prebuffer_ms(mock_sd, monkeypatch):
    monkeypatch.setenv("KOKORO_PREBUFFER_MS", "800")
    with patch.object(voice_backends, "KPipeline"):
        b = voice_backends.KokoroTTSBackend()
    assert b.PREBUFFER_MS == 800


@patch("core.voice_backends.sd")
def test_prebuffer_default(mock_sd, monkeypatch):
    monkeypatch.delenv("KOKORO_PREBUFFER_MS", raising=False)
    with patch.object(voice_backends, "KPipeline"):
        b = voice_backends.KokoroTTSBackend()
    assert b.PREBUFFER_MS == 1500


@patch("core.voice_backends.sd")
def test_prebuffer_floor_negative(mock_sd, monkeypatch):
    monkeypatch.setenv("KOKORO_PREBUFFER_MS", "-100")
    with patch.object(voice_backends, "KPipeline"):
        b = voice_backends.KokoroTTSBackend()
    assert b.PREBUFFER_MS == 0


@patch("core.voice_backends.sd")
def test_short_response_shorter_than_buffer_still_plays_all(mock_sd, monkeypatch):
    """Whole response < buffer target: SENTINEL-during-priming path must
    flush everything (no audio lost)."""
    monkeypatch.setenv("KOKORO_PREBUFFER_MS", "5000")  # 120000 samples @ 24k
    backend = _make_backend()
    stream = mock_sd.OutputStream.return_value.__enter__.return_value
    backend.speak_stream(iter(["One.", " Two.", " Three."]))
    assert _written_samples(stream) == 3 * 2048


@patch("core.voice_backends.sd")
def test_buffer_target_reached_midstream_plays_all(mock_sd, monkeypatch):
    """Target reached after the first chunk: prime flush + normal loop must
    together write everything."""
    monkeypatch.setenv("KOKORO_PREBUFFER_MS", "50")  # 1200 samples < one 2048 chunk
    backend = _make_backend()
    stream = mock_sd.OutputStream.return_value.__enter__.return_value
    backend.speak_stream(iter(["One.", " Two.", " Three."]))
    assert _written_samples(stream) == 3 * 2048


@patch("core.voice_backends.sd")
def test_prebuffer_zero_writes_all(mock_sd, monkeypatch):
    monkeypatch.setenv("KOKORO_PREBUFFER_MS", "0")
    backend = _make_backend()
    stream = mock_sd.OutputStream.return_value.__enter__.return_value
    backend.speak_stream(iter(["One.", " Two."]))
    assert _written_samples(stream) == 2 * 2048
```

- [ ] **Step 2: Update the flush default assertion**

In `tests/test_kokoro_first_flush.py`, the shipped `test_init_default_is_20`
must become 30. Replace that test with:

```python
def test_init_default_is_30(monkeypatch):
    monkeypatch.delenv("KOKORO_MIN_FIRST_FLUSH", raising=False)
    with patch.object(voice_backends, "KPipeline"):
        b = voice_backends.KokoroTTSBackend()
    assert b.MIN_FIRST_FLUSH == 30
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_kokoro_prebuffer.py tests/test_kokoro_first_flush.py -v`
Expected: FAIL — `PREBUFFER_MS` / `_configured_int` missing; default still 20.

- [ ] **Step 4: Generalize the env helper**

In `core/voice_backends.py`, replace the existing `_configured_min_first_flush`
with a general helper plus a thin wrapper (keeps existing callers/tests working):

```python
def _configured_int(env_var: str, default: int, minimum: int) -> int:
    """Read an int env var at backend construction (after load_dotenv), not
    import. Floor-guarded to `minimum`; non-numeric falls back to `default`."""
    try:
        return max(minimum, int(os.getenv(env_var, str(default))))
    except (TypeError, ValueError):
        return default


def _configured_min_first_flush(default: int) -> int:
    return _configured_int("KOKORO_MIN_FIRST_FLUSH", default, 1)
```

- [ ] **Step 5: Raise the first-flush default and add the pre-buffer constant**

In `KokoroTTSBackend`, change the class constant `MIN_FIRST_FLUSH = 20` to `30`,
and add a new constant next to it:

```python
    MIN_FIRST_FLUSH = 30
    PREBUFFER_MS = 1500
```

- [ ] **Step 6: Set `PREBUFFER_MS` in both backends' `__init__`**

In `KokoroTTSBackend.__init__`, right after the existing
`self.MIN_FIRST_FLUSH = _configured_min_first_flush(...)` line, add:

```python
        self.PREBUFFER_MS = _configured_int("KOKORO_PREBUFFER_MS", type(self).PREBUFFER_MS, 0)
```

Add the identical line in `KokoroONNXBackend.__init__`, right after its existing
`self.MIN_FIRST_FLUSH = _configured_min_first_flush(...)` line.

- [ ] **Step 7: Pre-buffer the writer thread**

In `KokoroTTSBackend.speak_stream`, replace the body of `writer_worker` with a
version that primes a jitter buffer before the first write. The new body:

```python
            def writer_worker():
                """Drain audio_q to the device in sub-blocks. Blocks on
                stream.write so playback is naturally paced. Primes a small
                jitter buffer (PREBUFFER_MS of audio) before the first write so
                playback doesn't immediately outrun Kokoro's slower-than-realtime
                synth. On interrupt, stop writing (checked between sub-blocks) but
                keep draining so synth never blocks on the bounded audio_q.put."""
                try:
                    def _write(audio):
                        if first_audio_at[0] is None:
                            first_audio_at[0] = time.perf_counter()
                        resampled = resample(
                            audio, KOKORO_SAMPLE_RATE, self.output_samplerate
                        )
                        for i in range(0, len(resampled), self.WRITE_SUB_BLOCK):
                            if _interrupted():
                                break
                            stream.write(resampled[i : i + self.WRITE_SUB_BLOCK])

                    # Prime the jitter buffer: accumulate audio until we have
                    # PREBUFFER_MS worth, or the stream ends, or a barge-in.
                    target_samples = int(KOKORO_SAMPLE_RATE * self.PREBUFFER_MS / 1000)
                    primed = []
                    primed_samples = 0
                    sentinel_seen = False
                    while primed_samples < target_samples:
                        audio = audio_q.get()
                        if audio is SENTINEL:
                            sentinel_seen = True
                            break
                        if _interrupted():
                            break
                        primed.append(audio)
                        primed_samples += len(audio) if audio is not None else 0

                    for audio in primed:
                        if _interrupted():
                            break
                        _write(audio)

                    if sentinel_seen:
                        return

                    while True:
                        audio = audio_q.get()
                        if audio is SENTINEL:
                            return
                        if _interrupted():
                            continue  # discard; keep the queue moving
                        _write(audio)
                        if _interrupted():
                            # barge-in landed mid-chunk — signal early so the
                            # caller doesn't wait the full teardown timeout.
                            writer_done_writing.set()
                finally:
                    writer_done_writing.set()
```

Note: with `PREBUFFER_MS=0`, `target_samples` is 0, the priming loop does not
run, `primed` is empty, and the normal loop runs exactly as before.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m pytest tests/test_kokoro_prebuffer.py tests/test_kokoro_first_flush.py -v`
Expected: PASS.

- [ ] **Step 9: Run the full Kokoro streaming suite for no regression**

Run: `python -m pytest tests/test_kokoro_stream.py -v`
Expected: PASS (barge-in / interrupt / no-deadlock tests still green — the
priming loop breaks on interrupt and the normal loop still drains).

- [ ] **Step 10: Commit**

```bash
git add core/voice_backends.py tests/test_kokoro_prebuffer.py tests/test_kokoro_first_flush.py
git commit -m "feat: Kokoro audio pre-buffer (KOKORO_PREBUFFER_MS) + first-flush default 30"
```

---

### Task 3: Pre-buffer audio cue

**Files:**
- Modify: `core/voice.py` (add `VoiceInterface.play_prebuffer_cue`), `main.py` (swap the streaming first-delta hook)
- Test: `tests/test_prebuffer_cue.py` (create)

**Interfaces:**
- Consumes: existing `VoiceInterface._r2_chirp/_r2_beep/_r2_tail`, `resample`, `KOKORO_SAMPLE_RATE`, `self._output_samplerate`, `self._output_device_index`.
- Produces: `VoiceInterface.play_prebuffer_cue() -> None` (non-blocking, error-swallowing); used at the streaming `on_first_chunk` hook.

- [ ] **Step 1: Write the failing test**

Create `tests/test_prebuffer_cue.py`:

```python
"""Tests for VoiceInterface.play_prebuffer_cue — the longer cue that covers
the TTS pre-buffer window."""

import sys
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
    return v


def test_cue_is_long_and_nonblocking():
    v = _make_voice()
    with patch.object(voice_mod, "sd") as mock_sd:
        v.play_prebuffer_cue()
        mock_sd.play.assert_called_once()
        mock_sd.wait.assert_not_called()  # non-blocking
        arr = mock_sd.play.call_args.args[0]
        # Cue must fill roughly the pre-buffer window — at least ~1s of audio.
        assert len(arr) >= int(1.0 * KOKORO_SAMPLE_RATE)


def test_cue_noop_when_tts_disabled():
    v = _make_voice()
    v.enable_tts = False
    with patch.object(voice_mod, "sd") as mock_sd:
        v.play_prebuffer_cue()
        mock_sd.play.assert_not_called()


def test_cue_swallows_errors():
    v = _make_voice()
    with patch.object(voice_mod, "sd") as mock_sd:
        mock_sd.play.side_effect = RuntimeError("no speaker")
        v.play_prebuffer_cue()  # must not raise
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_prebuffer_cue.py -v`
Expected: FAIL — `play_prebuffer_cue` does not exist.

- [ ] **Step 3: Add the cue method**

In `core/voice.py`, add to `VoiceInterface` right after `play_response_ready_sound`:

```python
    def play_prebuffer_cue(self):
        """Longer R2-D2 'here it comes' warble (~1.3s) that covers the Kokoro
        pre-buffer window so there's no dead air before speech starts. Plays
        non-blocking; errors are logged and swallowed so a missing speaker
        can't crash the voice loop."""
        if not self.enable_tts:
            return
        try:
            gs = np.zeros(int(KOKORO_SAMPLE_RATE * 0.03), dtype=np.float32)
            sound = np.concatenate([
                self._r2_chirp(700, 1500, 0.30, vibrato_hz=9, vibrato_depth=60),
                gs,
                self._r2_chirp(1500, 1000, 0.28, vibrato_hz=11, vibrato_depth=55),
                gs,
                self._r2_chirp(1000, 1800, 0.30, vibrato_hz=10, vibrato_depth=65),
                gs,
                self._r2_beep(2000, 0.06, volume=0.4),
                self._r2_tail(0.06),
            ])
            sd.play(
                resample(sound, KOKORO_SAMPLE_RATE, self._output_samplerate),
                samplerate=self._output_samplerate,
                device=self._output_device_index,
            )
            # Intentionally no sd.wait — non-blocking; Kokoro primes in parallel.
        except Exception as e:
            logger.warning("Pre-buffer cue error: %s", e)
```

If the composed `sound` is under ~1.0s at `KOKORO_SAMPLE_RATE`, lengthen the
chirp durations until it is (the test enforces ≥1.0s).

- [ ] **Step 4: Wire it to the streaming first-delta hook**

In `main.py`, in the streaming branch, change the `on_first_chunk` argument
(currently `on_first_chunk=voice.play_response_ready_sound,` at ~line 351) to:

```python
                            on_first_chunk=voice.play_prebuffer_cue,
```

Leave the non-streaming path's `voice.play_response_ready_sound()` call
unchanged (it has no pre-buffer to cover).

- [ ] **Step 5: Run the cue tests to verify they pass**

Run: `python -m pytest tests/test_prebuffer_cue.py -v`
Expected: PASS (all 3).

- [ ] **Step 6: Run the voice-mode tests for no regression**

Run: `python -m pytest tests/test_voice_mode.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add core/voice.py main.py tests/test_prebuffer_cue.py
git commit -m "feat: longer R2-D2 pre-buffer cue on streaming responses"
```

---

### Task 4: Full suite + on-device tuning

**Files:** none (verification + tuning)

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 2: Deploy to the Pi**

After merge to `main`:

```bash
ssh pi 'cd ~/kaizen && git pull --ff-only origin main && systemctl --user restart kaizen.service'
```

- [ ] **Step 3: Verify flow on-device**

Say "hey jarvis" + a plain question. Confirm: the cue plays, then speech starts
with no dead air, and a short answer has no mid-answer pause. Read the log:

```bash
ssh pi 'journalctl _SYSTEMD_USER_UNIT=kaizen.service --no-pager --since "-2 minutes" | grep -E "Kokoro flush|to first audio|TIMING-SUMMARY"'
```

(Note: `to first audio` will now be higher by ~the pre-buffer amount — that delay
is intentional and covered by the cue.)

- [ ] **Step 4: Confirm depth-on-request**

Ask a plain question (expect 1-2 sentences), then ask "can you elaborate on that?"
(expect a fuller answer).

- [ ] **Step 5: Tune**

Adjust `KOKORO_PREBUFFER_MS` in the Pi `.env` (restart between values) for the
best trade of coverage vs. start-delay. If the cue and speech overlap or leave a
gap, adjust the cue length in `play_prebuffer_cue` to match the chosen buffer.
Record the chosen value.
```
