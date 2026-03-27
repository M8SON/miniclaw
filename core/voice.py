"""
Voice Interface - Handles microphone input (Whisper STT) and speaker output (Piper TTS).

Designed to be swappable — if the AI HAT+ 2 accelerates Whisper, only this module changes.

Wake word detection uses whisper-tiny on a continuous sliding audio window so any
custom phrase works without training data. The larger transcription model is only
invoked after the wake phrase is detected.
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
      Wake:   Microphone → PyAudio → 2s sliding window → whisper-tiny → phrase check
      Input:  Microphone → PyAudio → silence detection → whisper-base → text
      Output: text → Piper TTS → WAV → aplay → speaker
    """

    CHUNK = 1024
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 16000

    # Wake word window: 2s of audio, re-evaluated every 1s
    WAKE_WINDOW_SECONDS = 2.0
    WAKE_STEP_SECONDS = 1.0

    def __init__(
        self,
        whisper_model: str = "base",
        wake_model: str = "tiny",
        wake_phrase: str = "hey miniclaw",
        enable_tts: bool = True,
        tts_model_path: str = "models/en_GB-cori-medium.onnx",
        silence_threshold: int = 1000,
        silence_duration: float = 2.0,
    ):
        self.enable_tts = enable_tts
        self.tts_model_path = tts_model_path
        self.silence_threshold = silence_threshold
        self.silence_duration = silence_duration
        self.wake_phrase = wake_phrase.lower().strip()

        logger.info("Loading Whisper wake model: %s", wake_model)
        self.wake_model = whisper.load_model(wake_model)

        logger.info("Loading Whisper transcription model: %s", whisper_model)
        self.whisper_model = whisper.load_model(whisper_model)

        logger.info("Models loaded — wake phrase: '%s'", self.wake_phrase)

    def wait_for_wake_word(self) -> bool:
        """
        Block until the wake phrase is detected in the microphone stream.

        Continuously records audio in a sliding 2-second window and runs
        whisper-tiny on each window. Returns True when the wake phrase is heard,
        False if interrupted by Ctrl+C.
        """
        audio = pyaudio.PyAudio()
        stream = audio.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            frames_per_buffer=self.CHUNK,
        )

        window_samples = int(self.RATE * self.WAKE_WINDOW_SECONDS)
        step_samples = int(self.RATE * self.WAKE_STEP_SECONDS)
        samples_collected = 0
        buffer = []

        logger.info("Waiting for wake phrase: '%s'", self.wake_phrase)

        try:
            while True:
                data = stream.read(self.CHUNK, exception_on_overflow=False)
                buffer.append(np.frombuffer(data, dtype=np.int16))
                samples_collected += self.CHUNK

                if samples_collected < step_samples:
                    continue

                samples_collected = 0

                # Build window from buffer, trim to last 2 seconds
                window = np.concatenate(buffer)
                if len(window) > window_samples:
                    window = window[-window_samples:]
                    # Trim buffer to avoid unbounded growth
                    buffer = [window]

                # Transcribe window with tiny model (no temp file needed)
                audio_float = window.astype(np.float32) / 32768.0
                result = self.wake_model.transcribe(
                    audio_float,
                    language="en",
                    fp16=False,
                )
                transcript = result["text"].lower().strip()

                if self.wake_phrase in transcript:
                    logger.info("Wake phrase detected: '%s'", transcript)
                    stream.stop_stream()
                    stream.close()
                    audio.terminate()
                    return True

        except KeyboardInterrupt:
            stream.stop_stream()
            stream.close()
            audio.terminate()
            return False

    def listen(self) -> str | None:
        """
        Record audio until silence is detected, then transcribe with the full model.

        Returns transcribed text, or None if nothing intelligible was captured.
        """
        audio_file = self._record_until_silence()
        transcription = self._transcribe(audio_file)

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

            piper_bin = os.environ.get("PIPER_BINARY", "piper")
            subprocess.run(
                [piper_bin, "--model", self.tts_model_path, "--output_file", temp_wav],
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
        """Record audio with automatic silence detection, return temp WAV file path."""
        audio = pyaudio.PyAudio()

        stream = audio.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            frames_per_buffer=self.CHUNK,
        )

        logger.info("Recording...")

        frames = []
        silence_frames = 0
        silence_limit = int(self.RATE / self.CHUNK * self.silence_duration)
        recording = False

        try:
            while True:
                data = stream.read(self.CHUNK, exception_on_overflow=False)
                frames.append(data)

                level = np.abs(np.frombuffer(data, dtype=np.int16)).mean()

                if level > self.silence_threshold:
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
        """Transcribe a WAV file using the full Whisper model."""
        logger.info("Transcribing...")
        result = self.whisper_model.transcribe(audio_file)
        text = result["text"].strip()
        logger.info("Transcribed: %s", text)
        return text
