"""
Voice backend implementations for MiniClaw.

These classes isolate concrete STT and TTS providers from the microphone and
conversation control logic in VoiceInterface.
"""

import logging

import sounddevice as sd
import whisper
from kokoro import KPipeline

logger = logging.getLogger(__name__)

KOKORO_SAMPLE_RATE = 24000


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
