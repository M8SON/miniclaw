"""
Voice backend implementations for Kaizen.

These classes isolate concrete STT and TTS providers from the microphone and
conversation control logic in VoiceInterface.
"""

import logging
import os
import time
from pathlib import Path
from typing import Protocol

import numpy as np

import sounddevice as sd
import whisper
from kokoro import KPipeline
from core.audio_devices import resample
from core.hailo_whisper_runtime import HailoTranscriptionRuntime

logger = logging.getLogger(__name__)

KOKORO_SAMPLE_RATE = 24000
HAILO_WHISPER_ASSET_ROOT = Path.home() / ".kaizen" / "models" / "hailo-whisper"
SUPPORTED_HAILO_WHISPER_TRANSCRIPTION_VARIANTS = {"base", "tiny", "tiny.en", "base.en"}


class SttBackend(Protocol):
    def transcribe_file(self, audio_file: str) -> str: ...


class WakeBackend(Protocol):
    """Continuous wake-word detector. Consumes audio chunks, returns trigger bool."""
    def detect(self, audio_chunk: np.ndarray) -> bool: ...
    def reset(self) -> None: ...


try:
    import openwakeword
    _OPENWAKEWORD_AVAILABLE = True
except ImportError:
    openwakeword = None  # type: ignore[assignment]
    _OPENWAKEWORD_AVAILABLE = False


class OpenWakeWordBackend:
    """Wake backend — purpose-built keyword spotter.

    Expects ~80ms audio chunks at 16kHz int16 or float32. Returns True when
    the model's score for `model_name` crosses `threshold`.

    `model_name` accepts canonical openwakeword names ("hey_jarvis", "alexa",
    "hey_mycroft", "timer", "weather"). The backend resolves to the bundled
    ONNX path and to the version-suffixed score-dict key automatically.
    """

    def __init__(self, model_name: str = "hey_jarvis", threshold: float = 0.5):
        if not _OPENWAKEWORD_AVAILABLE:
            raise ImportError("openwakeword not installed")
        if model_name not in openwakeword.models:
            raise ValueError(
                f"unknown openwakeword model {model_name!r}; "
                f"available: {list(openwakeword.models)}"
            )

        logger.info("Loading openWakeWord model: %s", model_name)
        self.model_name = model_name
        self.threshold = threshold

        meta = openwakeword.models[model_name]
        model_path = meta["model_path"]
        # Score-dict key is the bundled filename stem (e.g. "hey_jarvis_v0.1").
        self._score_key = Path(model_path).stem

        self.model = openwakeword.Model(wakeword_model_paths=[model_path])

    def detect(self, audio_chunk: np.ndarray) -> bool:
        scores = self.model.predict(audio_chunk)
        score = scores.get(self._score_key, 0.0)
        return score >= self.threshold

    def reset(self) -> None:
        # openwakeword 0.4.0's Model.reset() only clears prediction_buffer.
        # The AudioFeatures preprocessor keeps a rolling mel/embedding state
        # that survives across calls, so on re-entry after a wake event the
        # next chunk scores on features primed by the prior wake utterance —
        # firing instantly. Clear those preprocessor buffers in place too.
        # Re-constructing the preprocessor would reload ONNX models (slow).
        self.model.reset()
        pre = getattr(self.model, "preprocessor", None)
        if pre is None:
            return
        if hasattr(pre, "raw_data_buffer"):
            pre.raw_data_buffer.clear()
        if hasattr(pre, "melspectrogram_buffer"):
            pre.melspectrogram_buffer = np.ones((76, 32))
        if hasattr(pre, "accumulated_samples"):
            pre.accumulated_samples = 0
        if hasattr(pre, "feature_buffer"):
            pre.feature_buffer = np.zeros_like(pre.feature_buffer)


class VadBackend(Protocol):
    """Voice activity detector. Consumes audio chunks, returns speech bool."""
    def is_speech(self, audio_chunk: np.ndarray) -> bool: ...
    def reset(self) -> None: ...


class RmsVadBackend:
    """Fallback VAD — amplitude threshold. Preserves current Kaizen behavior
    when VAD_BACKEND=rms or when Silero VAD fails to load."""

    def __init__(self, threshold: int = 1000):
        self.threshold = threshold

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        if audio_chunk.dtype != np.int16:
            # Treat any non-int16 input as float in [-1, 1] (the convention used
            # elsewhere in voice.py); rescale to int16 magnitude before comparing
            # to threshold. Direct astype(int16) would truncate normalized
            # samples to zero and silently report all audio as silence.
            audio_chunk = (audio_chunk * 32768.0).astype(np.int16)
        level = np.abs(audio_chunk).mean()
        return level > self.threshold

    def reset(self) -> None:
        # RMS check is stateless; nothing to clear between sessions.
        pass


try:
    import silero_vad
    import torch
    _SILERO_AVAILABLE = True
except ImportError:
    silero_vad = None  # type: ignore[assignment]
    torch = None  # type: ignore[assignment]
    _SILERO_AVAILABLE = False


class SileroVadBackend:
    """Primary VAD — Silero TorchScript speech-probability model.

    Silero VAD only accepts 512-sample frames at 16kHz. PyAudio chunks are
    typically 1024 samples, so we keep a carry-over buffer that yields exact
    512-sample frames to the model. Returns True if any frame in the current
    call's accumulated audio scored above the threshold (conservative — a
    single speech-positive frame keeps `recording` armed in the endpoint loop).
    """

    FRAME_SIZE = 512

    def __init__(self, threshold: float = 0.5):
        if not _SILERO_AVAILABLE:
            raise ImportError("silero-vad not installed")
        logger.info("Loading Silero VAD model")
        self.threshold = threshold
        self.model = silero_vad.load_silero_vad()
        self._buffer = np.zeros(0, dtype=np.float32)

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        if audio_chunk.dtype != np.float32:
            audio_chunk = audio_chunk.astype(np.float32) / 32768.0

        self._buffer = np.concatenate([self._buffer, audio_chunk])

        any_speech = False
        while len(self._buffer) >= self.FRAME_SIZE:
            frame = self._buffer[: self.FRAME_SIZE]
            self._buffer = self._buffer[self.FRAME_SIZE :]
            tensor = torch.from_numpy(frame)
            score = self.model(tensor, 16000).item()
            if score >= self.threshold:
                any_speech = True
        return any_speech

    def reset(self) -> None:
        # Clear streaming carry-over and the model's internal LSTM state.
        # The endpoint loop calls reset() between sessions so a stale tail
        # doesn't carry into the next utterance.
        self._buffer = np.zeros(0, dtype=np.float32)
        if hasattr(self.model, "reset_states"):
            self.model.reset_states()


def build_vad_backend(
    backend_name: str,
    threshold: float,
    rms_threshold: int,
) -> tuple[VadBackend, str]:
    """Select VAD backend by name with automatic fallback to RMS on Silero failure."""
    if backend_name == "silero":
        try:
            backend = SileroVadBackend(threshold=threshold)
            return backend, f"VAD backend: silero (threshold={threshold})"
        except Exception:
            logger.warning(
                "Silero VAD unavailable — falling back to RMS",
                exc_info=True,
            )
            backend = RmsVadBackend(threshold=rms_threshold)
            return backend, f"VAD backend: rms (threshold={rms_threshold}) — silero fallback"

    if backend_name == "rms":
        backend = RmsVadBackend(threshold=rms_threshold)
        return backend, f"VAD backend: rms (threshold={rms_threshold})"

    raise ValueError(
        f"unknown VAD backend {backend_name!r}; expected 'silero' or 'rms'"
    )


class WhisperBackend:
    """Speech-to-text backend using Whisper for full transcription only.

    Wake detection is handled separately by openWakeWord — see WakeBackend.
    """

    def __init__(self, transcription_model: str = "base"):
        logger.info("Loading Whisper transcription model: %s", transcription_model)
        self.transcription_model = whisper.load_model(transcription_model)

    def transcribe_file(self, audio_file: str) -> str:
        """Transcribe a recorded WAV file."""
        result = self.transcription_model.transcribe(audio_file)
        return result["text"].strip()


class HybridWhisperBackend:
    """Whisper transcription with optional Hailo offload."""

    def __init__(
        self,
        transcription_model: str,
        use_hailo_transcription: bool,
    ):
        self.use_hailo_transcription = use_hailo_transcription

        if use_hailo_transcription:
            self.hailo_runtime = HailoTranscriptionRuntime(
                model_name=transcription_model,
                assets_root=HAILO_WHISPER_ASSET_ROOT,
            )
        else:
            logger.info("Loading Whisper transcription model: %s", transcription_model)
            self.transcription_model = whisper.load_model(transcription_model)

    def transcribe_file(self, audio_file: str) -> str:
        if self.use_hailo_transcription:
            return self.hailo_runtime.transcribe_file(audio_file).strip()

        result = self.transcription_model.transcribe(audio_file)
        return result["text"].strip()


try:
    from faster_whisper import WhisperModel
    _FASTER_WHISPER_AVAILABLE = True
except ImportError:
    WhisperModel = None  # type: ignore[assignment]
    _FASTER_WHISPER_AVAILABLE = False


class FasterWhisperBackend:
    """CPU STT backend using faster-whisper (CTranslate2).

    Drop-in replacement for WhisperBackend.transcribe_file. Runs at int8
    quantization on CPU — materially better accuracy than openai-whisper
    base on Pi 5 without a meaningful latency hit because Whisper-small
    via CTranslate2 is roughly the same wall time as base via the
    reference implementation.
    """

    def __init__(self, model_name: str = "small"):
        if not _FASTER_WHISPER_AVAILABLE:
            raise ImportError("faster-whisper not installed")
        logger.info("Loading faster-whisper model: %s", model_name)
        self.model_name = model_name
        self.model = WhisperModel(model_name, device="cpu", compute_type="int8")

    def transcribe_file(self, audio_file: str) -> str:
        segments, _info = self.model.transcribe(audio_file, language="en")
        return "".join(seg.text for seg in segments).strip()


def hailo_runtime_available() -> bool:
    return Path("/dev/hailo0").exists()


def hailo_transcription_assets_available(transcription_model: str) -> tuple[bool, str]:
    transcription_dir = HAILO_WHISPER_ASSET_ROOT / transcription_model

    if not transcription_dir.exists():
        return False, "transcription model asset missing"
    return True, ""


def hailo_transcription_self_check(transcription_model: str) -> None:
    HailoTranscriptionRuntime.self_check(
        model_name=transcription_model,
        assets_root=HAILO_WHISPER_ASSET_ROOT,
    )


def build_stt_backend(
    transcription_model_cpu: str,
    transcription_model_hailo: str,
) -> tuple[SttBackend, str]:
    """Select STT backend with separate model variants per execution path.

    Hailo and CPU paths benefit from different Whisper variants: Hailo only
    has tiny/base HEFs published, while CPU on Pi 5 can comfortably run
    Whisper-small via faster-whisper for materially better accuracy.

    The CPU path prefers FasterWhisperBackend (CTranslate2 int8) and falls
    back to the openai-whisper reference impl if faster-whisper isn't
    available.
    """

    def _build_cpu(reason: str) -> tuple[SttBackend, str]:
        try:
            return (
                FasterWhisperBackend(model_name=transcription_model_cpu),
                f"STT backend: cpu:{transcription_model_cpu} (faster-whisper) — {reason}",
            )
        except (ImportError, Exception) as exc:
            logger.warning(
                "faster-whisper unavailable (%s) — using openai-whisper", exc
            )
            return (
                WhisperBackend(transcription_model=transcription_model_cpu),
                f"STT backend: cpu:{transcription_model_cpu} (openai-whisper fallback) — {reason}",
            )

    if not hailo_runtime_available():
        return _build_cpu("Hailo runtime unavailable")

    if transcription_model_hailo not in SUPPORTED_HAILO_WHISPER_TRANSCRIPTION_VARIANTS:
        return _build_cpu("Hailo transcription model variant unsupported")

    assets_ok, reason = hailo_transcription_assets_available(transcription_model_hailo)
    if not assets_ok:
        return _build_cpu(reason)

    try:
        hailo_transcription_self_check(transcription_model_hailo)
    except Exception as exc:
        return _build_cpu(f"Hailo self-check failed: {exc}")

    backend = HybridWhisperBackend(
        transcription_model=transcription_model_hailo,
        use_hailo_transcription=True,
    )
    return (
        backend,
        f"STT backend: Hybrid Whisper (transcription=hailo:{transcription_model_hailo})",
    )


def build_wake_backend(
    model_name: str,
    threshold: float,
) -> tuple[WakeBackend, str]:
    """Build the openWakeWord wake backend.

    Raises if openWakeWord is not installed or the requested model name is
    invalid. There is no fallback: openWakeWord is a single pip dep and the
    legacy whisper-substring wake path was the source of the wake-stream
    hallucinations that motivated this backend in the first place.
    """
    backend = OpenWakeWordBackend(model_name=model_name, threshold=threshold)
    return backend, f"Wake backend: openwakeword ({model_name}, threshold={threshold})"


KOKORO_ONNX_ASSET_ROOT = Path.home() / ".kaizen" / "models" / "kokoro-onnx"

try:
    from kokoro_onnx import Kokoro as _KokoroONNXImpl
    _KOKORO_ONNX_AVAILABLE = True
except ImportError:
    _KokoroONNXImpl = None  # type: ignore[assignment]
    _KOKORO_ONNX_AVAILABLE = False

try:
    from elevenlabs.client import ElevenLabs as _ElevenLabsClient
    from elevenlabs import VoiceSettings as _VoiceSettings
    _ELEVENLABS_AVAILABLE = True
except ImportError:
    _ElevenLabsClient = None  # type: ignore[assignment]
    _VoiceSettings = None  # type: ignore[assignment]
    _ELEVENLABS_AVAILABLE = False

# ElevenLabs voice_settings.speed is limited to this range (1.0 = normal).
# TTS_SPEED values outside it (e.g. Kokoro's 1.4) are clamped, not rejected.
ELEVENLABS_SPEED_MIN = 0.7
ELEVENLABS_SPEED_MAX = 1.2


def _configured_int(env_var: str, default: int, minimum: int) -> int:
    """Read an int env var at backend construction (after load_dotenv), not
    import. Floor-guarded to `minimum`; non-numeric falls back to `default`."""
    try:
        return max(minimum, int(os.getenv(env_var, str(default))))
    except (TypeError, ValueError):
        return default


def _configured_min_first_flush(default: int) -> int:
    return _configured_int("KOKORO_MIN_FIRST_FLUSH", default, 1)


class KokoroTTSBackend:
    """Text-to-speech backend using the kokoro PyTorch package."""

    sample_rate = KOKORO_SAMPLE_RATE

    def __init__(
        self,
        voice: str = "af_heart",
        speed: float = 1.0,
        output_device: int | None = None,
        output_samplerate: int | None = None,
    ):
        logger.info("Loading Kokoro TTS pipeline (voice: %s)...", voice)
        self.voice = voice
        self.speed = speed
        self.output_device = output_device
        self.output_samplerate = output_samplerate or KOKORO_SAMPLE_RATE
        self.pipeline = KPipeline(lang_code="a")
        self.MIN_FIRST_FLUSH = _configured_min_first_flush(type(self).MIN_FIRST_FLUSH)
        self.PREBUFFER_MS = _configured_int("KOKORO_PREBUFFER_MS", type(self).PREBUFFER_MS, 0)

    SENTENCE_TERMINATORS = (".", "?", "!", "\n")
    BUFFER_CAP = 200
    # The first flush alone sets time-to-first-audio (Kokoro has no in-sentence
    # streaming), so break it at the earliest clause boundary once it's at least
    # MIN_FIRST_FLUSH chars — long enough not to sound choppy. Later sentences
    # already overlap prior playback, so they keep sentence-level flushing.
    CLAUSE_BOUNDARIES = (",", ";", ":", "—")
    MIN_FIRST_FLUSH = 30
    PREBUFFER_MS = 1500
    WRITE_SUB_BLOCK = 1024  # frames per stream.write, so a barge-in cut lands within ~tens of ms

    def _synth_audio(self, text: str):
        """Yield audio chunks for `text` at KOKORO_SAMPLE_RATE float32.

        Extension point for backends that share the parallel-pipeline
        machinery but use a different synth library underneath.
        """
        for _, _, audio in self.pipeline(text, voice=self.voice, speed=self.speed):
            yield audio

    def _find_flush_boundary(self, buffer: str, allow_clause: bool) -> int:
        """Index of the earliest flush point in buffer, or -1 if none.

        Sentence terminators always qualify. When allow_clause is set (the
        first flush only), clause boundaries also qualify — but only at or
        past MIN_FIRST_FLUSH - 1, so the first spoken fragment is long enough
        not to sound choppy while still starting audio fast.
        """
        boundary = -1
        for term in self.SENTENCE_TERMINATORS:
            idx = buffer.find(term)
            if idx != -1 and (boundary == -1 or idx < boundary):
                boundary = idx
        if allow_clause:
            for term in self.CLAUSE_BOUNDARIES:
                idx = buffer.find(term, self.MIN_FIRST_FLUSH - 1)
                if idx != -1 and (boundary == -1 or idx < boundary):
                    boundary = idx
        return boundary

    def speak_stream(self, chunks, interrupt_event=None, on_first_audio=None) -> None:
        """Consume LLM text deltas, run Kokoro per sentence, write audio.

        Three-stage pipeline that overlaps synthesis with playback:

          Main thread:  LLM deltas → sentence-queue
          Synth thread: sentence-queue → audio-queue  (Kokoro pipeline)
          Writer thread: audio-queue → OutputStream  (stream.write blocks
                         until the device drains, so it paces playback)

        Per-flush diagnostic (Pi 5 2026-05-08) showed Kokoro yields exactly
        one chunk per pipeline call after the full synthesis finishes — no
        in-call streaming. The earlier sequential implementation therefore
        stalled the user between sentences for the full per-sentence synth
        time (~1.4x audio duration on Pi 5 CPU). With this pipeline,
        sentence N+1's synthesis runs while sentence N is playing, so the
        between-sentence gap shrinks to max(0, synth_time - playback_time)
        — typically near-zero for short sentences and small for long ones.

        Sentence boundaries are detected by SENTENCE_TERMINATORS plus a
        defensive BUFFER_CAP so no flush stalls forever on a comma-heavy
        delta stream.

        If on_first_audio is provided, it will be called exactly once when
        the first real audio is written to the device.
        """
        import queue
        import threading

        def _interrupted() -> bool:
            return interrupt_event is not None and interrupt_event.is_set()

        SENTINEL = object()
        sentence_q: queue.Queue = queue.Queue()
        # Bounded queue — a runaway synth (e.g. Kokoro returning huge audio)
        # shouldn't OOM the host while writer is blocked on the device.
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
                if _interrupted():
                    # This flush was abandoned by a barge-in — its audio is
                    # discarded, so skip the (misleading "NO AUDIO") per-flush log.
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
            writer_done_writing = threading.Event()

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
                            if on_first_audio is not None:
                                try:
                                    on_first_audio()
                                except Exception:
                                    logger.exception("on_first_audio hook raised")
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
                            # Writer will not touch the stream after a priming
                            # interrupt, so signal teardown-complete immediately
                            # instead of leaving the caller to hit its full wait
                            # timeout.
                            writer_done_writing.set()
                            break
                        primed.append(audio)
                        primed_samples += len(audio) if audio is not None else 0

                    for audio in primed:
                        if _interrupted():
                            writer_done_writing.set()
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

            synth_thread = threading.Thread(
                target=synth_worker, daemon=True, name="kokoro-synth"
            )
            writer_thread = threading.Thread(
                target=writer_worker, daemon=True, name="kokoro-writer"
            )
            synth_thread.start()
            writer_thread.start()

            buffer = ""
            first_flush_emitted = False
            for delta in chunks:
                if _interrupted():
                    break  # stop feeding; SENTINEL below winds the pipeline down
                buffer += delta
                while True:
                    boundary = self._find_flush_boundary(
                        buffer, allow_clause=not first_flush_emitted
                    )
                    if boundary != -1:
                        sent_text = buffer[: boundary + 1]
                        buffer = buffer[boundary + 1 :]
                        if sent_text.strip():
                            sentence_q.put(sent_text)
                            first_flush_emitted = True
                        continue
                    if len(buffer) >= self.BUFFER_CAP:
                        cap_text = buffer[: self.BUFFER_CAP]
                        buffer = buffer[self.BUFFER_CAP :]
                        if cap_text.strip():
                            sentence_q.put(cap_text)
                            first_flush_emitted = True
                        continue
                    break

            if not _interrupted() and buffer.strip():
                sentence_q.put(buffer)
            sentence_q.put(SENTINEL)

            # Normal drain: synth puts SENTINEL on audio_q, writer plays it out
            # and returns, then the OutputStream closes below. On a barge-in,
            # poll instead of hard-joining — Kokoro synthesises each sentence in
            # one call that can't be preempted, so a plain join() stalls
            # teardown for the whole in-flight synth (observed ~5s of dead air
            # on the Pi). We break out within ~0.2s, wait only until the writer
            # has stopped touching the device, then return and let the daemon
            # synth/writer threads wind down in the background (synth_worker
            # skips all further queued sentences, so it makes at most one more
            # Kokoro call before exiting).
            while writer_thread.is_alive() and not _interrupted():
                writer_thread.join(timeout=0.2)
            if _interrupted():
                writer_done_writing.wait(timeout=1.0)
            else:
                synth_thread.join()

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


class KokoroONNXBackend(KokoroTTSBackend):
    """Same Kokoro voices, ONNX Runtime instead of PyTorch.

    Pi 5 voice test 2026-05-08 measured the kokoro PyTorch package at
    ~1.4x slower than realtime — fast enough to be smooth on a laptop
    but too slow for gap-free playback on Pi 5 ARM64. The int8-quantized
    Kokoro ONNX model runs ~2-3x faster on the same CPU; combined with
    the parallel synth pipeline this brings synth-vs-realtime under 1.0
    and the audio queue stays full ahead of the writer.

    Inherits the parallel-pipeline machinery (sentence segmentation,
    synth + writer threads, OutputStream lifecycle) and overrides only
    the actual synthesis call.

    Model files (download once with scripts/download_kokoro_onnx.py):
      - <KOKORO_ONNX_ASSET_ROOT>/kokoro-v1.0.int8.onnx  (~30 MB)
      - <KOKORO_ONNX_ASSET_ROOT>/voices-v1.0.bin         (~10 MB)
    """

    def __init__(
        self,
        voice: str = "af_heart",
        speed: float = 1.0,
        output_device: int | None = None,
        output_samplerate: int | None = None,
        model_path: Path | None = None,
        voices_path: Path | None = None,
        intra_op_threads: int | None = None,
    ):
        if not _KOKORO_ONNX_AVAILABLE:
            raise ImportError("kokoro-onnx not installed")

        model_path = model_path or KOKORO_ONNX_ASSET_ROOT / "kokoro-v1.0.int8.onnx"
        voices_path = voices_path or KOKORO_ONNX_ASSET_ROOT / "voices-v1.0.bin"
        if not Path(model_path).exists() or not Path(voices_path).exists():
            raise FileNotFoundError(
                f"Kokoro ONNX assets missing — expected at {model_path} and "
                f"{voices_path}. Run scripts/download_kokoro_onnx.py to fetch."
            )

        # Pi 5 voice test 2026-05-09: default kokoro-onnx ran ~2.7x slower
        # than realtime — twice as slow as the PyTorch path. Cause: ONNX
        # Runtime defaults to a single intra-op thread on ARM64 builds,
        # while PyTorch was implicitly using all four Cortex-A76 cores.
        # Explicitly pin intra_op_num_threads to all available cores.
        if intra_op_threads is None:
            intra_op_threads = max(1, (os.cpu_count() or 1))

        import onnxruntime as rt
        opts = rt.SessionOptions()
        opts.intra_op_num_threads = intra_op_threads
        opts.inter_op_num_threads = 1
        session = rt.InferenceSession(
            str(model_path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )

        logger.info(
            "Loading Kokoro ONNX (voice: %s, model: %s, intra_op_threads=%d)",
            voice, model_path.name, intra_op_threads,
        )
        self.voice = voice
        self.speed = speed
        self.output_device = output_device
        self.output_samplerate = output_samplerate or KOKORO_SAMPLE_RATE
        self.intra_op_threads = intra_op_threads
        self.kokoro = _KokoroONNXImpl.from_session(session, str(voices_path))
        self.MIN_FIRST_FLUSH = _configured_min_first_flush(type(self).MIN_FIRST_FLUSH)
        self.PREBUFFER_MS = _configured_int("KOKORO_PREBUFFER_MS", type(self).PREBUFFER_MS, 0)

    def _synth_audio(self, text: str):
        # kokoro.create returns (audio_array, sample_rate). Returns a single
        # array per call rather than streaming chunks like the PyTorch
        # pipeline — same observable behavior we already saw on Pi where
        # the pytorch path also yields one chunk per call.
        audio, _sr = self.kokoro.create(
            text, voice=self.voice, speed=self.speed, lang="en-us"
        )
        yield audio


class ElevenLabsTTSBackend(KokoroTTSBackend):
    """Cloud TTS via ElevenLabs Flash v2.5 (~75ms first-audio).

    Reuses KokoroTTSBackend's parallel speak_stream pipeline (sentence flush,
    synth + writer threads, barge-in, on_first_audio cue-stop) and overrides
    only _synth_audio. Requests pcm_24000 so the audio matches KOKORO_SAMPLE_RATE
    and the inherited resample path is correct with no changes.
    """

    sample_rate = KOKORO_SAMPLE_RATE
    # ElevenLabs streams faster than realtime, so the 1500ms Kokoro jitter
    # buffer would only re-add latency. Default to no prebuffer.
    PREBUFFER_MS = 0
    MODEL_ID = "eleven_flash_v2_5"
    DEFAULT_VOICE_ID = "onwK4e9ZLuTAKqWW03F9"  # Daniel — British, Jarvis-like

    def __init__(
        self,
        voice_id: str,
        api_key: str,
        model_id: str = MODEL_ID,
        speed: float = 1.0,
        output_device: int | None = None,
        output_samplerate: int | None = None,
        client=None,
    ):
        # Do NOT call super().__init__ — there is no Kokoro pipeline to load.
        # Set only the attributes speak_stream/speak read.
        if client is None:
            if not _ELEVENLABS_AVAILABLE:
                raise ImportError("elevenlabs not installed")
            client = _ElevenLabsClient(api_key=api_key)
        self._client = client
        self._voice_id = voice_id
        self._model_id = model_id
        self.voice = voice_id
        # ElevenLabs caps speed at ELEVENLABS_SPEED_MAX; clamp rather than error
        # so a Kokoro-tuned TTS_SPEED (e.g. 1.4) still works (capped at 1.2).
        self.speed = min(max(speed, ELEVENLABS_SPEED_MIN), ELEVENLABS_SPEED_MAX)
        self.output_device = output_device
        self.output_samplerate = output_samplerate or KOKORO_SAMPLE_RATE
        self.MIN_FIRST_FLUSH = _configured_min_first_flush(type(self).MIN_FIRST_FLUSH)
        self.PREBUFFER_MS = _configured_int(
            "KOKORO_PREBUFFER_MS", type(self).PREBUFFER_MS, 0
        )
        logger.info(
            "Loading ElevenLabs TTS (voice_id: %s, model: %s, speed: %.2f)",
            voice_id, model_id, self.speed,
        )

    def _synth_audio(self, text: str):
        audio_stream = self._client.text_to_speech.stream(
            voice_id=self._voice_id,
            text=text,
            model_id=self._model_id,
            output_format="pcm_24000",
            voice_settings=_VoiceSettings(speed=self.speed),
        )
        carry = b""
        try:
            for chunk in audio_stream:
                if not (isinstance(chunk, bytes) and chunk):
                    continue
                buf = carry + chunk
                n = len(buf) - (len(buf) % 2)   # largest even (whole-sample) prefix
                if n:
                    yield np.frombuffer(buf[:n], dtype="<i2").astype(np.float32) / 32768.0
                carry = buf[n:]
        finally:
            close = getattr(audio_stream, "close", None)
            if callable(close):
                close()


def elevenlabs_self_check(backend: "ElevenLabsTTSBackend") -> None:
    """Prove key + connectivity by synthesising one character and consuming
    the first streamed chunk. Raises on any failure so the caller can fall
    back to a local backend."""
    for _ in backend._synth_audio("."):
        break
