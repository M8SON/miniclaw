# TTS Barge-In (Wake-Word Interrupt) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user interrupt Jarvis mid-response by saying the wake word ("hey jarvis"); on interrupt, cut TTS playback within tens of ms and drop straight into the next `listen()`.

**Architecture:** Approach A — a cooperative `threading.Event`. During response playback a short-lived daemon *watcher* thread reuses the already-loaded `wake_backend` on its own mic stream; on a wake hit it sets the event. The Kokoro playback path (both streaming and non-streaming) writes audio in small sub-blocks and checks the event between them, stopping mid-sentence, then drains its bounded queues so no thread deadlocks on shutdown. Feature is env-gated (`BARGE_IN_ENABLED`, default on) and degrades to a silent no-op if the mic can't open during playback.

**Tech Stack:** Python 3.11, `threading`/`queue`, PyAudio (mic capture), sounddevice (`sd.OutputStream`), Kokoro TTS, openWakeWord backend, `unittest`/`unittest.mock` (mocking `sd`/`pyaudio` as existing voice tests do).

## Global Constraints

- Spec of record: `docs/superpowers/specs/2026-06-18-tts-barge-in-design.md`. Every task traces to it.
- Voice mode only. Text mode is untouched.
- When `interrupt_event is None`, `KokoroTTSBackend.speak_stream` and `.speak` must behave **byte-for-byte as today** — the existing `tests/test_kokoro_stream.py` suite must stay green unchanged.
- When `BARGE_IN_ENABLED=false`, no watcher is ever started and `finalize()`/`speak(..., interruptible=True)` return `False`; behavior identical to current.
- Reuse the existing `self.wake_backend` — no second model, no extra memory footprint.
- Never crash the voice loop: any mic-open / thread-join / watcher failure logs a warning and lets playback finish normally.
- Reuse existing constants: `VoiceInterface.CHUNK/FORMAT/CHANNELS/RATE`, `self._input_device_index`, `self._close_pyaudio`, `KOKORO_SAMPLE_RATE`. New sub-block size constant `WRITE_SUB_BLOCK = 1024` frames.
- Match existing style: local `import queue/threading` inside methods already using that idiom; tests are `unittest.TestCase` classes patching `core.voice_backends.sd` / `core.voice.pyaudio`.
- Commit after every task with a `feat:`/`test:`/`docs:` message.

Run all tests with: `cd /home/daedalus/linux/kaizen && python -m pytest tests/ -q` (venv: `.venv`).

---

### Task 1: Interruptible streaming playback — `KokoroTTSBackend.speak_stream`

The highest-risk task: cut playback mid-sentence **and** shut down the 3-thread pipeline without deadlocking on the bounded `audio_q`.

**Files:**
- Modify: `core/voice_backends.py` — `KokoroTTSBackend` (add `WRITE_SUB_BLOCK`; change `speak_stream` signature + writer + synth + delta loop)
- Test: `tests/test_kokoro_stream.py` (add cases to `SpeakStreamTests`)

**Interfaces:**
- Produces: `KokoroTTSBackend.speak_stream(self, chunks, interrupt_event=None) -> None`. `interrupt_event` is a `threading.Event | None`. When set mid-run: writer stops touching the device, synth stops synthesising queued sentences, both queues drain to SENTINEL, all threads join, the `OutputStream` context exits normally, method returns.
- Class constant `WRITE_SUB_BLOCK = 1024` (frames per `stream.write`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_kokoro_stream.py` inside `class SpeakStreamTests`:

```python
    @patch("core.voice_backends.sd")
    def test_interrupt_before_playback_writes_nothing(self, mock_sd):
        import threading
        import numpy as np
        backend = self._make_backend()
        backend.pipeline.side_effect = lambda *a, **k: iter(
            [("", "", np.zeros(4096, dtype=np.float32))]
        )
        stream = mock_sd.OutputStream.return_value.__enter__.return_value
        ev = threading.Event()
        ev.set()  # already interrupted before any audio is written
        backend.speak_stream(iter(["Hello.", " World."]), interrupt_event=ev)
        stream.write.assert_not_called()

    @patch("core.voice_backends.sd")
    def test_no_deadlock_when_interrupted_midstream(self, mock_sd):
        """Interrupt after playback starts with far more audio queued than
        audio_q's maxsize (8). All threads must wind down and speak_stream
        must return — the highest-risk piece."""
        import threading
        import numpy as np
        backend = self._make_backend()
        backend.pipeline.side_effect = lambda *a, **k: iter(
            [("", "", np.zeros(2048, dtype=np.float32))]
        )
        stream = mock_sd.OutputStream.return_value.__enter__.return_value
        ev = threading.Event()
        writes = [0]

        def fake_write(_buf):
            writes[0] += 1
            if writes[0] == 1:
                ev.set()  # interrupt right after the first sub-block

        stream.write.side_effect = fake_write
        sentences = [f"Sentence number {i}." for i in range(20)]
        done = threading.Event()

        def run():
            backend.speak_stream(iter(sentences), interrupt_event=ev)
            done.set()

        threading.Thread(target=run, daemon=True).start()
        self.assertTrue(done.wait(timeout=10), "speak_stream deadlocked after interrupt")

    @patch("core.voice_backends.sd")
    def test_none_interrupt_writes_audio(self, mock_sd):
        import numpy as np
        backend = self._make_backend()
        backend.pipeline.side_effect = lambda *a, **k: iter(
            [("", "", np.zeros(2048, dtype=np.float32))]
        )
        stream = mock_sd.OutputStream.return_value.__enter__.return_value
        backend.speak_stream(iter(["Hello world."]), interrupt_event=None)
        self.assertTrue(stream.write.called)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_kokoro_stream.py -q`
Expected: the three new tests FAIL (e.g. `TypeError: speak_stream() got an unexpected keyword argument 'interrupt_event'`).

- [ ] **Step 3: Add the sub-block constant**

In `core/voice_backends.py`, in `class KokoroTTSBackend`, next to `SENTENCE_TERMINATORS`/`BUFFER_CAP` (~line 405):

```python
    SENTENCE_TERMINATORS = (".", "?", "!", "\n")
    BUFFER_CAP = 200
    WRITE_SUB_BLOCK = 1024  # frames per stream.write, so a barge-in cut lands within ~tens of ms
```

- [ ] **Step 4: Make `speak_stream` interruptible**

Change the signature (line ~417) and add the cooperative checks. Full replacement of the method body between the signature and the closing timing logs (the summary-log block at the end is unchanged):

```python
    def speak_stream(self, chunks, interrupt_event=None) -> None:
        """Consume LLM text deltas, run Kokoro per sentence, write audio.

        ... (existing docstring unchanged) ...
        """
        import queue
        import threading

        def _interrupted() -> bool:
            return interrupt_event is not None and interrupt_event.is_set()

        SENTINEL = object()
        sentence_q: queue.Queue = queue.Queue()
        audio_q: queue.Queue = queue.Queue(maxsize=8)

        t0 = time.perf_counter()
        first_audio_at: list[float | None] = [None]
        flushes_counter = [0]

        def synth_worker():
            """Pull sentences, synthesise, push audio chunks to audio_q."""
            while True:
                sent = sentence_q.get()
                if sent is SENTINEL:
                    audio_q.put(SENTINEL)
                    return
                if _interrupted():
                    # Stop synthesising audio nobody will hear; keep draining
                    # sentence_q until SENTINEL so the delta loop never blocks.
                    continue
                if not sent.strip():
                    continue
                flushes_counter[0] += 1
                flush_n = flushes_counter[0]
                t_synth_start = time.perf_counter()
                t_first_chunk: float | None = None
                chunks_n = 0
                audio_samples = 0
                try:
                    for audio in self._synth_audio(sent):
                        if _interrupted():
                            break
                        if t_first_chunk is None:
                            t_first_chunk = time.perf_counter()
                        chunks_n += 1
                        audio_samples += len(audio) if audio is not None else 0
                        audio_q.put(audio)
                except Exception:
                    logger.exception("Kokoro synth raised on flush #%d", flush_n)
                    continue
                synth_ms = int((time.perf_counter() - t_synth_start) * 1000)
                if t_first_chunk is None:
                    logger.info(
                        "Kokoro flush #%d (%d chars): NO AUDIO in %dms",
                        flush_n, len(sent), synth_ms,
                    )
                else:
                    ttfb_ms = int((t_first_chunk - t_synth_start) * 1000)
                    audio_ms = int(audio_samples / KOKORO_SAMPLE_RATE * 1000)
                    logger.info(
                        "Kokoro flush #%d (%d chars): %dms synth (%dms ttfb), "
                        "%d chunk(s), ~%dms audio, synth/audio %.2fx",
                        flush_n, len(sent), synth_ms, ttfb_ms,
                        chunks_n, audio_ms, synth_ms / max(audio_ms, 1),
                    )

        with sd.OutputStream(
            samplerate=self.output_samplerate,
            channels=1,
            dtype="float32",
            device=self.output_device,
        ) as stream:
            def writer_worker():
                """Drain audio_q to the device in sub-blocks. On interrupt,
                stop writing but keep draining so synth never blocks on the
                bounded audio_q.put."""
                while True:
                    audio = audio_q.get()
                    if audio is SENTINEL:
                        return
                    if _interrupted():
                        continue  # discard; keep the queue moving
                    if first_audio_at[0] is None:
                        first_audio_at[0] = time.perf_counter()
                    resampled = resample(audio, KOKORO_SAMPLE_RATE, self.output_samplerate)
                    for i in range(0, len(resampled), self.WRITE_SUB_BLOCK):
                        if _interrupted():
                            break
                        stream.write(resampled[i : i + self.WRITE_SUB_BLOCK])

            synth_thread = threading.Thread(
                target=synth_worker, daemon=True, name="kokoro-synth"
            )
            writer_thread = threading.Thread(
                target=writer_worker, daemon=True, name="kokoro-writer"
            )
            synth_thread.start()
            writer_thread.start()

            buffer = ""
            for delta in chunks:
                if _interrupted():
                    break  # stop feeding; SENTINEL below winds the pipeline down
                buffer += delta
                while True:
                    boundary = -1
                    for term in self.SENTENCE_TERMINATORS:
                        idx = buffer.find(term)
                        if idx != -1 and (boundary == -1 or idx < boundary):
                            boundary = idx
                    if boundary != -1:
                        sent_text = buffer[: boundary + 1]
                        buffer = buffer[boundary + 1 :]
                        if sent_text.strip():
                            sentence_q.put(sent_text)
                        continue
                    if len(buffer) >= self.BUFFER_CAP:
                        cap_text = buffer[: self.BUFFER_CAP]
                        buffer = buffer[self.BUFFER_CAP :]
                        if cap_text.strip():
                            sentence_q.put(cap_text)
                        continue
                    break

            if not _interrupted() and buffer.strip():
                sentence_q.put(buffer)
            sentence_q.put(SENTINEL)

            synth_thread.join()
            writer_thread.join()

        total_ms = int((time.perf_counter() - t0) * 1000)
        flushes = flushes_counter[0]
        first = first_audio_at[0]
        if flushes == 0:
            return
        if first is None:
            logger.info(
                "Kokoro TTS stream: %d flush(es), %dms total (no audio produced)",
                flushes, total_ms,
            )
        else:
            first_ms = int((first - t0) * 1000)
            logger.info(
                "Kokoro TTS stream: %d flush(es), %dms to first audio, %dms total",
                flushes, first_ms, total_ms,
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_kokoro_stream.py -q`
Expected: PASS — the 3 new tests plus all pre-existing `SpeakStreamTests`/`KokoroONNXBackendTests` (the `interrupt_event=None` default keeps old behavior).

- [ ] **Step 6: Commit**

```bash
git add core/voice_backends.py tests/test_kokoro_stream.py
git commit -m "feat(tts): interruptible streaming playback via cooperative event"
```

---

### Task 2: Interruptible non-streaming playback — `KokoroTTSBackend.speak`

**Files:**
- Modify: `core/voice_backends.py` — `KokoroTTSBackend.speak` (line ~573)
- Test: `tests/test_kokoro_stream.py` (new `class SpeakInterruptTests`)

**Interfaces:**
- Consumes: `WRITE_SUB_BLOCK` from Task 1.
- Produces: `KokoroTTSBackend.speak(self, text, interrupt_event=None) -> None`. Sub-block writes; stops when the event is set; `None` behaves as today.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_kokoro_stream.py`:

```python
class SpeakInterruptTests(unittest.TestCase):
    def _make_backend(self):
        from core import voice_backends
        with patch.object(voice_backends, "KPipeline"):
            backend = voice_backends.KokoroTTSBackend()
        backend.pipeline = MagicMock()
        return backend

    @patch("core.voice_backends.sd")
    def test_speak_stops_when_interrupted(self, mock_sd):
        import threading
        import numpy as np
        backend = self._make_backend()
        backend.pipeline.side_effect = lambda *a, **k: iter(
            [("", "", np.zeros(8192, dtype=np.float32))]
        )
        stream = mock_sd.OutputStream.return_value.__enter__.return_value
        ev = threading.Event()
        ev.set()
        backend.speak("Hello there, general.", interrupt_event=ev)
        stream.write.assert_not_called()

    @patch("core.voice_backends.sd")
    def test_speak_none_writes_audio(self, mock_sd):
        import numpy as np
        backend = self._make_backend()
        backend.pipeline.side_effect = lambda *a, **k: iter(
            [("", "", np.zeros(2048, dtype=np.float32))]
        )
        stream = mock_sd.OutputStream.return_value.__enter__.return_value
        backend.speak("Hello.", interrupt_event=None)
        self.assertTrue(stream.write.called)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_kokoro_stream.py::SpeakInterruptTests -q`
Expected: FAIL (`unexpected keyword argument 'interrupt_event'`).

- [ ] **Step 3: Make `speak` interruptible**

Replace the body of `KokoroTTSBackend.speak` (line ~573):

```python
    def speak(self, text: str, interrupt_event=None) -> None:
        """Stream generated speech directly to the output device.

        Logs perceived latency (time-to-first-audio-sample) separately
        from total wall time so the synthesis-cold-start gap is visible
        independently of how long the utterance actually plays.
        """
        t0 = time.perf_counter()
        first_chunk_at: float | None = None
        with sd.OutputStream(
            samplerate=self.output_samplerate,
            channels=1,
            dtype="float32",
            device=self.output_device,
        ) as stream:
            for audio in self._synth_audio(text):
                if interrupt_event is not None and interrupt_event.is_set():
                    break
                if first_chunk_at is None:
                    first_chunk_at = time.perf_counter()
                resampled = resample(audio, KOKORO_SAMPLE_RATE, self.output_samplerate)
                for i in range(0, len(resampled), self.WRITE_SUB_BLOCK):
                    if interrupt_event is not None and interrupt_event.is_set():
                        break
                    stream.write(resampled[i : i + self.WRITE_SUB_BLOCK])
        total_ms = int((time.perf_counter() - t0) * 1000)
        if first_chunk_at is None:
            logger.info("Kokoro TTS: %dms total (no audio produced)", total_ms)
        else:
            first_ms = int((first_chunk_at - t0) * 1000)
            logger.info(
                "Kokoro TTS: %dms to first audio, %dms total", first_ms, total_ms
            )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_kokoro_stream.py -q`
Expected: PASS (new `SpeakInterruptTests` + all prior).

- [ ] **Step 5: Commit**

```bash
git add core/voice_backends.py tests/test_kokoro_stream.py
git commit -m "feat(tts): interruptible non-streaming speak() via cooperative event"
```

---

### Task 3: Wake-word watcher on `VoiceInterface`

**Files:**
- Modify: `core/voice.py` — add `import threading` (top); `barge_in_enabled` init param + `self._barge_in = None`; `_start_barge_in_watcher` / `_stop_barge_in_watcher`; watcher teardown in `shutdown()`
- Test: `tests/test_barge_in.py` (new file)

**Interfaces:**
- Consumes: existing `self.wake_backend`, `self._input_device_index`, `self.CHUNK/FORMAT/CHANNELS/RATE`, `self._close_pyaudio`.
- Produces:
  - `VoiceInterface.__init__(..., barge_in_enabled: bool = False)` → sets `self.barge_in_enabled` and `self._barge_in = None`.
  - `VoiceInterface._start_barge_in_watcher(self, interrupt_event) -> None` — no-op when disabled; opens its own mic stream; on open failure logs a warning and leaves `self._barge_in is None`; else `wake_backend.reset()`, spawns a daemon thread that sets `interrupt_event` on a wake hit, stores `self._barge_in = (audio, stream, stop_event, thread)`.
  - `VoiceInterface._stop_barge_in_watcher(self) -> None` — idempotent; sets stop_event, joins (timeout 2s), closes the mic via `_close_pyaudio`, clears `self._barge_in`.
  - `shutdown()` calls `_stop_barge_in_watcher()` first.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_barge_in.py`:

```python
"""Tests for TTS barge-in: wake-word watcher + feeder wiring on VoiceInterface."""

import threading
import unittest
from unittest.mock import MagicMock, patch


def make_voice(barge_in_enabled=True, tts_backend=None, enable_tts=False):
    from core.voice import VoiceInterface
    wake = MagicMock()
    with patch("core.voice.resolve_input_device", return_value=0), \
         patch("core.voice.resolve_output_device", return_value=0), \
         patch("core.voice.output_samplerate", return_value=24000):
        v = VoiceInterface(
            stt_backend=MagicMock(),
            tts_backend=tts_backend,
            wake_backend=wake,
            enable_tts=enable_tts,
            barge_in_enabled=barge_in_enabled,
        )
    return v, wake


class WatcherTests(unittest.TestCase):
    @patch("core.voice.pyaudio")
    def test_watcher_sets_event_on_detect(self, mock_pyaudio):
        v, wake = make_voice(barge_in_enabled=True)
        wake.detect.return_value = True  # fires on the first frame
        stream = mock_pyaudio.PyAudio.return_value.open.return_value
        stream.read.return_value = b"\x00\x00" * v.CHUNK
        ev = threading.Event()
        v._start_barge_in_watcher(ev)
        self.assertTrue(ev.wait(timeout=2.0))
        v._stop_barge_in_watcher()
        self.assertIsNone(v._barge_in)

    def test_disabled_is_noop(self):
        v, _ = make_voice(barge_in_enabled=False)
        ev = threading.Event()
        v._start_barge_in_watcher(ev)
        self.assertIsNone(v._barge_in)
        self.assertFalse(ev.is_set())

    @patch("core.voice.pyaudio")
    def test_graceful_when_mic_open_fails(self, mock_pyaudio):
        v, _ = make_voice(barge_in_enabled=True)
        mock_pyaudio.PyAudio.return_value.open.side_effect = OSError("device busy")
        ev = threading.Event()
        v._start_barge_in_watcher(ev)  # must not raise
        self.assertIsNone(v._barge_in)
        self.assertFalse(ev.is_set())

    @patch("core.voice.pyaudio")
    def test_shutdown_stops_watcher(self, mock_pyaudio):
        v, wake = make_voice(barge_in_enabled=True)
        wake.detect.return_value = False  # never fires; watcher loops
        stream = mock_pyaudio.PyAudio.return_value.open.return_value
        stream.read.return_value = b"\x00\x00" * v.CHUNK
        ev = threading.Event()
        v._start_barge_in_watcher(ev)
        self.assertIsNotNone(v._barge_in)
        v.shutdown()
        self.assertIsNone(v._barge_in)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_barge_in.py::WatcherTests -q`
Expected: FAIL (`__init__() got an unexpected keyword argument 'barge_in_enabled'`).

- [ ] **Step 3: Add the module import**

In `core/voice.py` top imports (near line 13), add:

```python
import threading
```

- [ ] **Step 4: Add init fields**

In `VoiceInterface.__init__`, add the parameter to the signature (after `vad_min_silence_ms: int = 700,`):

```python
        vad_min_silence_ms: int = 700,
        barge_in_enabled: bool = False,
    ):
```

and set the fields near the other `self._shared_*`/`self._active_*` initialisation (after line 79):

```python
        # Barge-in: wake-word watcher active only during response playback.
        # Handle is (audio, stream, stop_event, thread) or None.
        self.barge_in_enabled = barge_in_enabled
        self._barge_in = None
```

- [ ] **Step 5: Add the watcher methods**

In `core/voice.py`, add these methods to `VoiceInterface` (place them right after `_close_pyaudio`, before `shutdown`):

```python
    def _start_barge_in_watcher(self, interrupt_event) -> None:
        """Run a wake-word watcher on its own mic stream during playback.

        Reuses self.wake_backend (the main wake loop is idle mid-conversation).
        On a wake hit it sets interrupt_event, which the TTS writer polls to
        cut playback. No-op when disabled; if the mic can't be opened (device
        busy / no full-duplex on the XVF3800) the feature goes silently
        inactive this turn rather than crashing the loop."""
        if not self.barge_in_enabled:
            return
        try:
            audio = pyaudio.PyAudio()
            stream = audio.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.RATE,
                input=True,
                input_device_index=self._input_device_index,
                frames_per_buffer=self.CHUNK,
            )
        except Exception as e:
            logger.warning(
                "Barge-in watcher could not open mic; inactive this turn: %s", e
            )
            self._barge_in = None
            return

        # Clear stale features so the watcher doesn't fire on the tail of the
        # prior wake event (same reasoning as wait_for_wake_word).
        self.wake_backend.reset()
        stop_event = threading.Event()

        def _watch():
            try:
                while not stop_event.is_set():
                    data = stream.read(self.CHUNK, exception_on_overflow=False)
                    chunk_int16 = np.frombuffer(data, dtype=np.int16)
                    if self.wake_backend.detect(chunk_int16):
                        logger.info("Barge-in wake word detected")
                        interrupt_event.set()
                        return
            except Exception:
                logger.exception("Barge-in watcher thread error")

        thread = threading.Thread(target=_watch, daemon=True, name="barge-in-watcher")
        thread.start()
        self._barge_in = (audio, stream, stop_event, thread)

    def _stop_barge_in_watcher(self) -> None:
        """Stop and tear down the watcher. Idempotent."""
        handle = self._barge_in
        if handle is None:
            return
        audio, stream, stop_event, thread = handle
        stop_event.set()
        thread.join(timeout=2.0)
        if thread.is_alive():
            logger.warning("Barge-in watcher did not join within 2s")
        self._close_pyaudio(audio, stream)
        self._barge_in = None
```

- [ ] **Step 6: Tear down the watcher in `shutdown()`**

In `shutdown()` (line ~129), add as the first statement of the body (before `self._close_pyaudio(self._shared_audio, ...)`):

```python
        self._stop_barge_in_watcher()
```

- [ ] **Step 7: Run to verify pass**

Run: `python -m pytest tests/test_barge_in.py::WatcherTests -q`
Expected: PASS (4 tests).

- [ ] **Step 8: Commit**

```bash
git add core/voice.py tests/test_barge_in.py
git commit -m "feat(voice): wake-word barge-in watcher on VoiceInterface"
```

---

### Task 4: Feeder + `speak` wiring returning the interrupted flag

**Files:**
- Modify: `core/voice.py` — `speak` (line ~382), `speak_stream_feeder` (line ~397)
- Test: `tests/test_barge_in.py` (new `class FeederWiringTests`)

**Interfaces:**
- Consumes: `_start_barge_in_watcher` / `_stop_barge_in_watcher` and `self.barge_in_enabled` from Task 3; `KokoroTTSBackend.speak_stream(chunks, interrupt_event=...)` / `.speak(text, interrupt_event=...)` from Tasks 1–2.
- Produces:
  - `VoiceInterface.speak(self, text, interruptible=False) -> bool` — returns whether a barge-in fired.
  - `VoiceInterface.speak_stream_feeder(self, on_first_chunk=None, interruptible=False) -> (push, finalize)` where `finalize() -> bool` returns whether a barge-in fired. `interrupt_event` is created only when `interruptible and self.barge_in_enabled`; the watcher starts on the first delta and stops in `finalize()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_barge_in.py`:

```python
class _FakeStreamBackend:
    """Minimal tts_backend exposing speak_stream/speak that just drain input."""
    def speak_stream(self, chunks, interrupt_event=None):
        for _ in chunks:
            pass

    def speak(self, text, interrupt_event=None):
        pass


class FeederWiringTests(unittest.TestCase):
    def test_finalize_false_when_no_deltas(self):
        v, _ = make_voice(
            barge_in_enabled=True, tts_backend=_FakeStreamBackend(), enable_tts=True
        )
        _push, finalize = v.speak_stream_feeder(interruptible=True)
        self.assertFalse(finalize())  # no thread was ever started

    @patch("core.voice.VoiceInterface._stop_barge_in_watcher")
    @patch("core.voice.VoiceInterface._start_barge_in_watcher")
    def test_finalize_true_when_watcher_fires(self, mock_start, mock_stop):
        v, _ = make_voice(
            barge_in_enabled=True, tts_backend=_FakeStreamBackend(), enable_tts=True
        )
        # Simulate a wake hit: the watcher sets the event it is handed.
        mock_start.side_effect = lambda ev: ev.set()
        push, finalize = v.speak_stream_feeder(interruptible=True)
        push("hello")  # first delta starts the watcher + consumer thread
        self.assertTrue(finalize())
        mock_start.assert_called_once()
        mock_stop.assert_called_once()

    @patch("core.voice.VoiceInterface._start_barge_in_watcher")
    def test_no_watcher_when_barge_in_disabled(self, mock_start):
        v, _ = make_voice(
            barge_in_enabled=False, tts_backend=_FakeStreamBackend(), enable_tts=True
        )
        push, finalize = v.speak_stream_feeder(interruptible=True)
        push("hello")
        self.assertFalse(finalize())
        mock_start.assert_not_called()

    @patch("core.voice.VoiceInterface._stop_barge_in_watcher")
    @patch("core.voice.VoiceInterface._start_barge_in_watcher")
    def test_speak_returns_interrupt_flag(self, mock_start, mock_stop):
        v, _ = make_voice(
            barge_in_enabled=True, tts_backend=_FakeStreamBackend(), enable_tts=True
        )
        mock_start.side_effect = lambda ev: ev.set()
        self.assertTrue(v.speak("some text", interruptible=True))
        mock_start.assert_called_once()
        mock_stop.assert_called_once()

    def test_speak_not_interruptible_returns_false(self):
        v, _ = make_voice(
            barge_in_enabled=True, tts_backend=_FakeStreamBackend(), enable_tts=True
        )
        self.assertFalse(v.speak("hi"))
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_barge_in.py::FeederWiringTests -q`
Expected: FAIL (`speak_stream_feeder() got an unexpected keyword argument 'interruptible'`, and `speak` returns `None`).

- [ ] **Step 3: Rewrite `speak` to be interruptible and return a flag**

Replace `VoiceInterface.speak` (line ~382):

```python
    def speak(self, text: str, interruptible: bool = False) -> bool:
        """Speak text aloud using Kokoro TTS with streaming playback.

        When interruptible and barge-in is enabled, a wake-word watcher runs
        during playback; saying the wake word cuts playback. Returns whether a
        barge-in fired (always False when not interruptible / TTS disabled)."""
        if not self.enable_tts or self.tts_backend is None:
            return False

        interrupt_event = (
            threading.Event() if interruptible and self.barge_in_enabled else None
        )
        if interrupt_event is not None:
            self._start_barge_in_watcher(interrupt_event)
        try:
            self.tts_backend.speak(text, interrupt_event=interrupt_event)
        except Exception as e:
            logger.warning("TTS error: %s", e)
        finally:
            if interrupt_event is not None:
                self._stop_barge_in_watcher()
        return interrupt_event is not None and interrupt_event.is_set()
```

- [ ] **Step 4: Wire the feeder**

Replace `VoiceInterface.speak_stream_feeder` (line ~397). Changes vs. current: new `interruptible` param; create `interrupt_event`; start watcher on first chunk; pass event into `speak_stream`; `finalize` stops the watcher and returns the interrupted bool; the disabled/no-TTS `_finalize` returns `False`.

```python
    def speak_stream_feeder(self, on_first_chunk=None, interruptible=False):
        """Return (push, finalize) for feeding text deltas into a streaming TTS run.

        ... (existing docstring paragraphs unchanged) ...

        interruptible: when True and barge-in is enabled, a wake-word watcher
        runs during playback and finalize() returns whether it fired.
        """
        import queue

        if not self.enable_tts or self.tts_backend is None or not hasattr(
            self.tts_backend, "speak_stream"
        ):
            def _push(_delta: str) -> None:
                return
            def _finalize() -> bool:
                return False
            return _push, _finalize

        interrupt_event = (
            threading.Event() if interruptible and self.barge_in_enabled else None
        )
        q: queue.Queue = queue.Queue()
        SENTINEL = object()
        backend = self.tts_backend
        thread_holder: list = [None]
        first_chunk_seen = [False]

        def _gen():
            while True:
                item = q.get()
                if item is SENTINEL:
                    return
                yield item

        def _consume():
            try:
                backend.speak_stream(_gen(), interrupt_event=interrupt_event)
            except Exception:
                logger.exception("speak_stream consumer raised")

        def _ensure_thread() -> None:
            if thread_holder[0] is not None:
                return
            t = threading.Thread(target=_consume, daemon=True, name="kokoro-stream")
            t.start()
            thread_holder[0] = t

        def push(delta: str) -> None:
            if not delta:
                return
            if not first_chunk_seen[0]:
                first_chunk_seen[0] = True
                if on_first_chunk is not None:
                    try:
                        on_first_chunk()
                    except Exception:
                        logger.exception("on_first_chunk hook raised")
                if interrupt_event is not None:
                    self._start_barge_in_watcher(interrupt_event)
                _ensure_thread()
            q.put(delta)

        def finalize() -> bool:
            if thread_holder[0] is None:
                # No deltas ever arrived; nothing to drain, join, or stop.
                return False
            q.put(SENTINEL)
            thread_holder[0].join(timeout=300)
            if thread_holder[0].is_alive():
                logger.warning(
                    "Kokoro stream thread did not finish within 300s — "
                    "audio device may be stuck; subsequent turns may glitch"
                )
            if interrupt_event is not None:
                self._stop_barge_in_watcher()
            return interrupt_event is not None and interrupt_event.is_set()

        return push, finalize
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_barge_in.py -q`
Expected: PASS (`WatcherTests` + `FeederWiringTests`).

- [ ] **Step 6: Commit**

```bash
git add core/voice.py tests/test_barge_in.py
git commit -m "feat(voice): barge-in feeder/speak wiring returning interrupted flag"
```

---

### Task 5: Config, main-loop integration, and docs

**Files:**
- Modify: `main.py` — `build_voice_interface` (line ~134, pass `barge_in_enabled`); voice loop streaming branch (lines ~345-362) and non-streaming branch (lines ~370-372)
- Modify: `CLAUDE.md` (env table + roadmap item #1), `README.md` (env table + roadmap checkbox)
- Test: `tests/test_main_voice_backend_selection.py` (add env-wiring case)

**Interfaces:**
- Consumes: `VoiceInterface(barge_in_enabled=...)`, `speak_stream_feeder(interruptible=True)`/`finalize() -> bool`, `speak(text, interruptible=True) -> bool`.
- Produces: `BARGE_IN_ENABLED` env (default `"true"`) → `barge_in_enabled` kwarg on `VoiceInterface`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_main_voice_backend_selection.py`, in `class BuildVoiceInterfaceSelectionTests`:

```python
    @patch("core.audio_devices.output_samplerate", return_value=48000)
    @patch("core.audio_devices.resolve_output_device", return_value=0)
    @patch("core.voice.VoiceInterface")
    @patch("main.build_wake_backend")
    @patch("main.build_stt_backend")
    def test_build_voice_interface_passes_barge_in_disabled(
        self, mock_build_stt_backend, mock_build_wake_backend, mock_voice_interface,
        _mock_resolve, _mock_sr,
    ):
        mock_build_stt_backend.return_value = (object(), "STT backend: x")
        mock_build_wake_backend.return_value = (object(), "Wake backend: y")
        with patch.dict("os.environ", {"BARGE_IN_ENABLED": "false"}):
            main.build_voice_interface()
        _, kwargs = mock_voice_interface.call_args
        self.assertFalse(kwargs["barge_in_enabled"])

    @patch("core.audio_devices.output_samplerate", return_value=48000)
    @patch("core.audio_devices.resolve_output_device", return_value=0)
    @patch("core.voice.VoiceInterface")
    @patch("main.build_wake_backend")
    @patch("main.build_stt_backend")
    def test_build_voice_interface_barge_in_defaults_on(
        self, mock_build_stt_backend, mock_build_wake_backend, mock_voice_interface,
        _mock_resolve, _mock_sr,
    ):
        mock_build_stt_backend.return_value = (object(), "STT backend: x")
        mock_build_wake_backend.return_value = (object(), "Wake backend: y")
        with patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("BARGE_IN_ENABLED", None)
            main.build_voice_interface()
        _, kwargs = mock_voice_interface.call_args
        self.assertTrue(kwargs["barge_in_enabled"])
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_main_voice_backend_selection.py -q`
Expected: FAIL (`KeyError: 'barge_in_enabled'` — kwarg not passed yet).

- [ ] **Step 3: Read the env var and pass the kwarg**

In `main.py build_voice_interface`, add near the other env reads (after line 130 `tts_speed = ...`):

```python
    barge_in_enabled = os.getenv("BARGE_IN_ENABLED", "true").lower() == "true"
```

and add to the `VoiceInterface(...)` return (after `vad_min_silence_ms=vad_min_silence_ms,`):

```python
        vad_min_silence_ms=vad_min_silence_ms,
        barge_in_enabled=barge_in_enabled,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_main_voice_backend_selection.py -q`
Expected: PASS.

- [ ] **Step 5: Wire the voice loop (streaming branch)**

In `main.py run_voice_mode`, streaming branch (lines ~345-362), pass `interruptible=True` and read the flag:

```python
                        push_raw, finalize = voice.speak_stream_feeder(
                            on_first_chunk=voice.play_response_ready_sound,
                            interruptible=True,
                        )
                        try:
                            response = orchestrator.process_message(
                                transcription,
                                on_chunk=push_raw,
                                on_ack_success=voice.play_ack_sound,
                            )
                            if response:
                                print(f"Assistant: {response}\n")
                            with profiling.stage("tts"):
                                interrupted = finalize()
                            if interrupted:
                                print("[barge-in — listening]")
                                continue
                        except Exception:
                            finalize()
                            raise
```

- [ ] **Step 6: Wire the voice loop (non-streaming branch)**

Non-streaming branch (lines ~368-372):

```python
                        if response:
                            print(f"Assistant: {response}\n")
                            voice.play_response_ready_sound()
                            with profiling.stage("tts"):
                                interrupted = voice.speak(response, interruptible=True)
                            if interrupted:
                                print("[barge-in — listening]")
                                continue
```

`continue` re-enters the inner `while True:` loop → the next `voice.listen(...)` captures the user's command. (The exit-words `voice.speak(response)` at line 336, the greeting, and the "before we chat" reminders stay non-interruptible — they call `speak`/nothing without `interruptible=True`, per spec §6.)

- [ ] **Step 7: Full suite — verify no regressions**

Run: `python -m pytest tests/ -q`
Expected: PASS — all pre-existing tests plus the new `test_kokoro_stream` cases, `test_barge_in.py`, and the two `build_voice_interface` env cases.

- [ ] **Step 8: Docs**

`CLAUDE.md` — add a row to the Key Environment Variables table (after the `WAKE_WORD_THRESHOLD` row):

```markdown
| `BARGE_IN_ENABLED` | `true` | Say the wake word during a response to interrupt playback and start listening; set false to disable |
```

`CLAUDE.md` — replace roadmap item #1 (line 243):

```markdown
1. **TTS interruption** — shipped: say the wake word ("hey jarvis") mid-response to cut playback and drop into the next listen (`BARGE_IN_ENABLED`, default on; wake-word trigger, not VAD). On-device XVF3800 full-duplex validation still pending.
```

`README.md` — add the same `BARGE_IN_ENABLED` row after the `WAKE_WORD_THRESHOLD` row (line ~376), and check off the roadmap item (line 522):

```markdown
- [x] TTS interruption — stop speaking when user says the wake word over the assistant
```

- [ ] **Step 9: Commit**

```bash
git add main.py tests/test_main_voice_backend_selection.py CLAUDE.md README.md
git commit -m "feat(voice): enable wake-word barge-in in the voice loop + docs"
```

---

## Manual on-device validation (Pi — cannot unit-test)

These gate "done" but are not automatable. Run on the Pi after the branch merges:

1. **Primary hardware unknown (spec §7):** confirm the XVF3800 allows concurrent mic capture + Kokoro output. If `_start_barge_in_watcher` logs "could not open mic; inactive this turn", the device does not support simultaneous mic+speaker and barge-in silently no-ops — escalate to Mason (this is spec §7's open question and the whole feature's gate).
2. Say "hey jarvis" mid-response → playback cuts within ~1s and the next `listen()` captures the follow-up command.
3. Confirm `BARGE_IN_ENABLED=false` → behavior byte-for-byte as before.
4. SIGINT (Ctrl+C) mid-playback → clean shutdown, no stranded `/dev/snd` (no Errno -9996 on the next `./run.sh --voice`).

---

## Self-Review

**Spec coverage:**
- §2/§4.1 wake-word watcher reusing `wake_backend` → Task 3. ✅
- §4.2 interruptible streaming playback + no-deadlock drain → Task 1. ✅
- §4.2 non-streaming `speak` sub-blocks → Task 2. ✅
- §4.3 feeder/`speak` wiring + `finalize()`/`speak()` return bool → Task 4. ✅
- §4.4 `BARGE_IN_ENABLED` env → `barge_in_enabled` param → Task 5 Steps 1-4. ✅
- §5 data flow + main integration (`interruptible=True`, read flag, loop to listen) → Task 5 Steps 5-6. ✅
- §6 scope (only LLM-response playback interruptible; greeting/goodbye/cues not) → Task 5 Step 6 note. ✅
- §7 degradation (mic-open failure, SIGINT teardown, join timeout) → Task 3 (`_start` try/except, `shutdown()`, `_stop` join timeout). ✅
- §8 tests 1-7 → Task 1 (writer stop, no-deadlock, none-behaves-as-today), Task 3 (watcher fires, disabled, graceful degrade), Task 4 (finalize returns bool), Task 5 Step 7 (full suite). Test #5's "main reacts" loop behavior is on-device manual. ✅
- §9 docs (env row, roadmap) → Task 5 Step 8. ✅

**Placeholder scan:** No TBD/TODO/"handle edge cases"; every code step shows full code; every test step shows the assertion.

**Type consistency:** `interrupt_event` (`threading.Event | None`) is the single shared type across Tasks 1-4; `_start_barge_in_watcher(interrupt_event)` / `_stop_barge_in_watcher()` names match between Task 3 (def) and Task 4 (call); `barge_in_enabled` name matches across Tasks 3-5; `finalize() -> bool` and `speak(..., interruptible) -> bool` consistent between Task 4 (def) and Task 5 (call).
