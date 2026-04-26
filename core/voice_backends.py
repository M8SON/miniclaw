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
from core.hailo_whisper_runtime import HailoTranscriptionRuntime

logger = logging.getLogger(__name__)

KOKORO_SAMPLE_RATE = 24000
HAILO_WHISPER_ASSET_ROOT = Path.home() / ".miniclaw" / "models" / "hailo-whisper"
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
    """CPU wake-word detection plus optional Hailo full transcription."""

    def __init__(self, wake_model: str, transcription_model: str):
        logger.info("Loading Whisper wake model: %s", wake_model)
        self.wake_model = whisper.load_model(wake_model)

        self.hailo_runtime = HailoTranscriptionRuntime(
            model_name=transcription_model,
            assets_root=HAILO_WHISPER_ASSET_ROOT,
        )

    def transcribe_wake_audio(self, audio_float) -> str:
        result = self.wake_model.transcribe(
            audio_float,
            language="en",
            fp16=False,
        )
        return result["text"].lower().strip()

    def transcribe_file(self, audio_file: str) -> str:
        return self.hailo_runtime.transcribe_file(audio_file).strip()


def hailo_runtime_available() -> bool:
    return Path("/dev/hailo0").exists()


def hailo_transcription_assets_available(transcription_model: str) -> tuple[bool, str]:
    transcription_dir = HAILO_WHISPER_ASSET_ROOT / transcription_model

    if not transcription_dir.exists():
        return False, "transcription model asset missing"
    return True, ""


def build_stt_backend(
    wake_model: str, transcription_model: str
) -> tuple[SttBackend, str]:
    if not hailo_runtime_available():
        return (
            WhisperBackend(
                wake_model=wake_model, transcription_model=transcription_model
            ),
            "STT backend: CPU Whisper fallback — Hailo runtime unavailable",
        )

    if transcription_model not in SUPPORTED_HAILO_WHISPER_TRANSCRIPTION_VARIANTS:
        return (
            WhisperBackend(
                wake_model=wake_model, transcription_model=transcription_model
            ),
            "STT backend: CPU Whisper fallback — transcription model variant unsupported by Hailo",
        )

    assets_ok, reason = hailo_transcription_assets_available(transcription_model)
    if not assets_ok:
        return (
            WhisperBackend(
                wake_model=wake_model, transcription_model=transcription_model
            ),
            f"STT backend: CPU Whisper fallback — {reason}",
        )

    try:
        HailoTranscriptionRuntime.self_check(
            model_name=transcription_model,
            assets_root=HAILO_WHISPER_ASSET_ROOT,
        )
        backend = HybridWhisperBackend(
            wake_model=wake_model, transcription_model=transcription_model
        )
    except Exception:
        return (
            WhisperBackend(
                wake_model=wake_model, transcription_model=transcription_model
            ),
            "STT backend: CPU Whisper fallback — Hailo self-check failed",
        )

    return (
        backend,
        f"STT backend: Hybrid Whisper (wake=cpu:{wake_model}, transcription=hailo:{transcription_model})",
    )


class KokoroTTSBackend:
    """Default text-to-speech backend using Kokoro with streaming playback."""

    sample_rate = KOKORO_SAMPLE_RATE

    def __init__(self, voice: str = "af_heart", speed: float = 1.0):
        logger.info("Loading Kokoro TTS pipeline (voice: %s)...", voice)
        self.voice = voice
        self.speed = speed
        self.pipeline = KPipeline(lang_code="a")

    def speak(self, text: str) -> None:
        """Stream generated speech directly to the output device."""
        with sd.OutputStream(
            samplerate=self.sample_rate, channels=1, dtype="float32"
        ) as stream:
            for _, _, audio in self.pipeline(text, voice=self.voice, speed=self.speed):
                stream.write(audio)
