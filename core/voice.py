"""
Voice Interface - Handles microphone input (Whisper STT) and speaker output (Piper TTS).

Extracted from the original monolithic voice_assistant.py into a standalone
module that the orchestrator calls. Designed to be swappable — if the AI HAT+ 2
accelerates Whisper, only this module changes.
"""

import os
import wave
import tempfile
import subprocess
import logging

import numpy as np
import pyaudio
import whisper

logger = logging.getLogger(__name__)


class VoiceInterface:
    """
    Manages audio input (recording + transcription) and output (TTS).

    Audio pipeline:
      Input:  Microphone → PyAudio → silence detection → WAV → Whisper → text
      Output: text → Piper TTS → WAV → aplay → speaker
    """

    # Audio recording defaults
    CHUNK = 1024
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 16000

    def __init__(
        self,
        whisper_model: str = "base",
        enable_tts: bool = True,
        tts_model_path: str = "/app/en_GB-cori-medium.onnx",
        silence_threshold: int = 1000,
        silence_duration: float = 2.0,
    ):
        self.enable_tts = enable_tts
        self.tts_model_path = tts_model_path
        self.silence_threshold = silence_threshold
        self.silence_duration = silence_duration

        # Load Whisper
        logger.info("Loading Whisper model: %s", whisper_model)
        self.whisper_model = whisper.load_model(whisper_model)
        logger.info("Whisper model loaded")

    def listen(self) -> str | None:
        """
        Record audio until silence is detected, then transcribe.

        Returns:
            Transcribed text, or None if no speech detected.
        """
        audio_file = self._record_until_silence()
        transcription = self._transcribe(audio_file)

        # Clean up temp file
        try:
            os.unlink(audio_file)
        except OSError:
            pass

        if not transcription or len(transcription.strip()) < 3:
            return None

        return transcription.strip()

    def speak(self, text: str):
        """Speak text aloud using Piper TTS."""
        if not self.enable_tts:
            return

        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_wav = f.name

            subprocess.run(
                ["piper", "--model", self.tts_model_path, "--output_file", temp_wav],
                input=text.encode(),
                check=True,
                stderr=subprocess.DEVNULL,
            )

            subprocess.run(
                ["aplay", temp_wav],
                check=True,
                stderr=subprocess.DEVNULL,
            )

            os.unlink(temp_wav)

        except Exception as e:
            logger.warning("TTS error: %s", e)

    def _record_until_silence(self) -> str:
        """Record audio with automatic silence detection, return temp file path."""
        audio = pyaudio.PyAudio()

        stream = audio.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            frames_per_buffer=self.CHUNK,
        )

        logger.info("Listening...")

        frames = []
        silence_frames = 0
        silence_limit = int(self.RATE / self.CHUNK * self.silence_duration)
        recording = False

        try:
            while True:
                data = stream.read(self.CHUNK, exception_on_overflow=False)
                frames.append(data)

                audio_data = np.frombuffer(data, dtype=np.int16)
                audio_level = np.abs(audio_data).mean()

                if audio_level > self.silence_threshold:
                    recording = True
                    silence_frames = 0
                elif recording:
                    silence_frames += 1

                if recording and silence_frames > silence_limit:
                    break

        except KeyboardInterrupt:
            pass

        stream.stop_stream()
        stream.close()
        audio.terminate()

        temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)

        with wave.open(temp_file.name, "wb") as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(audio.get_sample_size(self.FORMAT))
            wf.setframerate(self.RATE)
            wf.writeframes(b"".join(frames))

        return temp_file.name

    def _transcribe(self, audio_file: str) -> str:
        """Transcribe an audio file using Whisper."""
        logger.info("Transcribing...")
        result = self.whisper_model.transcribe(audio_file)
        text = result["text"].strip()
        logger.info("Transcribed: %s", text)
        return text
