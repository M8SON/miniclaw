"""
Voice backend implementations for MiniClaw.

These classes isolate concrete STT and TTS providers from the microphone and
conversation control logic in VoiceInterface.
"""

import logging
from pathlib import Path
from typing import Protocol

import sounddevice as sd
import whisper
from kokoro import KPipeline
from core.audio_devices import resample
from core.hailo_whisper_runtime import HailoTranscriptionRuntime, HailoWakeRuntime

logger = logging.getLogger(__name__)

KOKORO_SAMPLE_RATE = 24000
HAILO_WHISPER_ASSET_ROOT = Path.home() / ".miniclaw" / "models" / "hailo-whisper"
SUPPORTED_HAILO_WHISPER_WAKE_VARIANTS = {"base", "tiny", "tiny.en", "base.en"}
SUPPORTED_HAILO_WHISPER_TRANSCRIPTION_VARIANTS = {"base", "tiny", "tiny.en", "base.en"}


class SttBackend(Protocol):
    def transcribe_wake_audio(self, audio_float) -> str: ...
    def transcribe_file(self, audio_file: str) -> str: ...


class WhisperBackend:
    """Default speech-to-text backend using Whisper for wake and full transcription."""

    def __init__(self, wake_model: str = "tiny", transcription_model: str = "base"):
        logger.info("Loading Whisper wake model: %s", wake_model)
        self.wake_model = whisper.load_model(wake_model)

        logger.info("Loading Whisper transcription model: %s", transcription_model)
        self.transcription_model = whisper.load_model(transcription_model)

    def transcribe_wake_audio(self, audio_float) -> str:
        """Transcribe a wake-word detection audio window."""
        result = self.wake_model.transcribe(
            audio_float,
            language="en",
            fp16=False,
        )
        return result["text"].lower().strip()

    def transcribe_file(self, audio_file: str) -> str:
        """Transcribe a recorded WAV file."""
        result = self.transcription_model.transcribe(audio_file)
        return result["text"].strip()


class HybridWhisperBackend:
    """Independent Hailo/CPU selection for wake and full transcription."""

    def __init__(
        self,
        wake_model: str,
        transcription_model: str,
        use_hailo_wake: bool,
        use_hailo_transcription: bool,
    ):
        self.use_hailo_wake = use_hailo_wake
        self.use_hailo_transcription = use_hailo_transcription

        if use_hailo_wake:
            self.hailo_wake_runtime = HailoWakeRuntime(
                model_name=wake_model,
                assets_root=HAILO_WHISPER_ASSET_ROOT,
            )
        else:
            logger.info("Loading Whisper wake model: %s", wake_model)
            self.wake_model = whisper.load_model(wake_model)

        if use_hailo_transcription:
            self.hailo_runtime = HailoTranscriptionRuntime(
                model_name=transcription_model,
                assets_root=HAILO_WHISPER_ASSET_ROOT,
            )
        else:
            logger.info("Loading Whisper transcription model: %s", transcription_model)
            self.transcription_model = whisper.load_model(transcription_model)

    def transcribe_wake_audio(self, audio_float) -> str:
        if self.use_hailo_wake:
            return self.hailo_wake_runtime.transcribe_wake_audio(audio_float).strip()

        result = self.wake_model.transcribe(
            audio_float,
            language="en",
            fp16=False,
        )
        return result["text"].lower().strip()

    def transcribe_file(self, audio_file: str) -> str:
        if self.use_hailo_transcription:
            return self.hailo_runtime.transcribe_file(audio_file).strip()

        result = self.transcription_model.transcribe(audio_file)
        return result["text"].strip()


def hailo_runtime_available() -> bool:
    return Path("/dev/hailo0").exists()


def hailo_transcription_assets_available(transcription_model: str) -> tuple[bool, str]:
    transcription_dir = HAILO_WHISPER_ASSET_ROOT / transcription_model

    if not transcription_dir.exists():
        return False, "transcription model asset missing"
    return True, ""


def hailo_wake_assets_available(wake_model: str) -> tuple[bool, str]:
    wake_dir = HAILO_WHISPER_ASSET_ROOT / wake_model

    if not wake_dir.exists():
        return False, "wake model asset missing"
    return True, ""


def hailo_wake_self_check(wake_model: str) -> None:
    HailoWakeRuntime.self_check(
        model_name=wake_model,
        assets_root=HAILO_WHISPER_ASSET_ROOT,
    )


def hailo_transcription_self_check(transcription_model: str) -> None:
    HailoTranscriptionRuntime.self_check(
        model_name=transcription_model,
        assets_root=HAILO_WHISPER_ASSET_ROOT,
    )


def build_stt_backend(
    wake_model: str, transcription_model: str
) -> tuple[SttBackend, str]:
    if not hailo_runtime_available():
        return (
            WhisperBackend(
                wake_model=wake_model, transcription_model=transcription_model
            ),
            f"STT backend: CPU Whisper fallback (wake=cpu:{wake_model}, transcription=cpu:{transcription_model}) — Hailo runtime unavailable",
        )

    use_hailo_wake = False
    use_hailo_transcription = False
    fallback_reasons: list[str] = []

    if wake_model in SUPPORTED_HAILO_WHISPER_WAKE_VARIANTS:
        wake_assets_ok, wake_reason = hailo_wake_assets_available(wake_model)
        if wake_assets_ok:
            try:
                hailo_wake_self_check(wake_model)
                use_hailo_wake = True
            except Exception as exc:
                fallback_reasons.append(f"wake {exc}")
        else:
            fallback_reasons.append(wake_reason)
    else:
        fallback_reasons.append("wake model variant unsupported by Hailo")

    if transcription_model in SUPPORTED_HAILO_WHISPER_TRANSCRIPTION_VARIANTS:
        transcription_assets_ok, transcription_reason = (
            hailo_transcription_assets_available(transcription_model)
        )
        if transcription_assets_ok:
            try:
                hailo_transcription_self_check(transcription_model)
                use_hailo_transcription = True
            except Exception as exc:
                fallback_reasons.append(f"transcription {exc}")
        else:
            fallback_reasons.append(transcription_reason)
    else:
        fallback_reasons.append("transcription model variant unsupported by Hailo")

    if not use_hailo_wake and not use_hailo_transcription:
        reason = fallback_reasons[0] if fallback_reasons else "Hailo unavailable"
        return (
            WhisperBackend(
                wake_model=wake_model, transcription_model=transcription_model
            ),
            f"STT backend: CPU Whisper fallback (wake=cpu:{wake_model}, transcription=cpu:{transcription_model}) — {reason}",
        )

    backend = HybridWhisperBackend(
        wake_model=wake_model,
        transcription_model=transcription_model,
        use_hailo_wake=use_hailo_wake,
        use_hailo_transcription=use_hailo_transcription,
    )
    wake_backend = f"{'hailo' if use_hailo_wake else 'cpu'}:{wake_model}"
    transcription_backend = (
        f"{'hailo' if use_hailo_transcription else 'cpu'}:{transcription_model}"
    )
    return (
        backend,
        f"STT backend: Hybrid Whisper (wake={wake_backend}, transcription={transcription_backend})",
    )


class KokoroTTSBackend:
    """Default text-to-speech backend using Kokoro with streaming playback."""

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

    def speak(self, text: str) -> None:
        """Stream generated speech directly to the output device."""
        with sd.OutputStream(
            samplerate=self.output_samplerate,
            channels=1,
            dtype="float32",
            device=self.output_device,
        ) as stream:
            for _, _, audio in self.pipeline(text, voice=self.voice, speed=self.speed):
                stream.write(resample(audio, KOKORO_SAMPLE_RATE, self.output_samplerate))
