"""
Voice Interface - Handles microphone input (Whisper STT) and speaker output (Kokoro TTS).

Designed to be swappable — if the AI HAT+ 2 accelerates Whisper, only this module changes.

Wake word detection uses whisper-tiny on a continuous sliding audio window so any
custom phrase works without training data. The larger transcription model is only
invoked after the wake phrase is detected.
"""

import os
import wave
import tempfile
import logging

import numpy as np
import pyaudio
import sounddevice as sd

from core.audio_devices import resolve_input_device, resolve_output_device
from core.voice_backends import KOKORO_SAMPLE_RATE, KokoroTTSBackend, WhisperBackend

logger = logging.getLogger(__name__)


class VoiceInterface:
    """
    Manages audio input (recording + transcription) and output (TTS).

    Audio pipeline:
      Wake:   Microphone → PyAudio → 2s sliding window → whisper-tiny → phrase check
      Input:  Microphone → PyAudio → silence detection → whisper-base → text
      Output: text → Kokoro TTS → WAV → aplay → speaker
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
        wake_phrase: str = "computer",
        enable_tts: bool = True,
        tts_voice: str = "af_heart",
        tts_speed: float = 1.0,
        silence_threshold: int = 1000,
        silence_duration: float = 2.0,
        stt_backend=None,
        tts_backend=None,
    ):
        self.enable_tts = enable_tts
        self.silence_threshold = silence_threshold
        self.silence_duration = silence_duration
        self.wake_phrase = wake_phrase.lower().strip()

        self._input_device_index = resolve_input_device()
        self._output_device_index = resolve_output_device()

        # Shared PyAudio stream passed from wake detection to listen()
        # to avoid the teardown/setup gap between the two phases.
        self._shared_audio = None
        self._shared_stream = None

        self.stt_backend = stt_backend or WhisperBackend(
            wake_model=wake_model,
            transcription_model=whisper_model,
        )
        self.tts_backend = (
            tts_backend
            if tts_backend is not None
            else (
                KokoroTTSBackend(
                    voice=tts_voice,
                    speed=tts_speed,
                    output_device=self._output_device_index,
                )
                if enable_tts
                else None
            )
        )

        logger.info("Models loaded — wake phrase: '%s'", self.wake_phrase)

    def wait_for_wake_word(self) -> bool:
        """
        Block until the wake phrase is detected in the microphone stream.

        Continuously records audio in a sliding 2-second window and runs
        whisper-tiny on each window. Returns True when the wake phrase is heard,
        False if interrupted by Ctrl+C.

        On detection the PyAudio stream is kept open and stored in self._shared_stream
        so that listen() can start capturing immediately with no gap.
        """
        audio = pyaudio.PyAudio()
        stream = audio.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            input_device_index=self._input_device_index,
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
                    buffer = [window]

                # Transcribe window with tiny model
                audio_float = window.astype(np.float32) / 32768.0
                transcript = self.stt_backend.transcribe_wake_audio(audio_float)

                if transcript:
                    logger.info("Wake window heard: '%s'", transcript)

                if self.wake_phrase in transcript:
                    logger.info("Wake phrase detected: '%s'", transcript)
                    # Keep stream open — listen() will use it immediately
                    self._shared_audio = audio
                    self._shared_stream = stream
                    return True

        except KeyboardInterrupt:
            stream.stop_stream()
            stream.close()
            audio.terminate()
            return False
        except Exception:
            stream.stop_stream()
            stream.close()
            audio.terminate()
            raise

    def listen(self, max_wait_seconds: float = 0) -> str | None:
        """
        Record audio until silence is detected, then transcribe with the full model.

        Reuses the stream left open by wait_for_wake_word() if available, so
        recording starts instantly with no setup gap.

        max_wait_seconds: give up and return None if no speech starts within this many
        seconds (0 = wait forever). Used for conversation idle timeout.
        """
        audio_file = self._record_until_silence(max_wait_seconds=max_wait_seconds)
        try:
            transcription = self._transcribe(audio_file)
        finally:
            try:
                os.unlink(audio_file)
            except OSError:
                pass

        if not transcription or len(transcription.strip()) < 3:
            return None

        return transcription.strip()

    def _r2_chirp(self, freq_start, freq_end, duration, volume=0.45, vibrato_hz=0, vibrato_depth=0):
        """Frequency-sweep chirp with optional vibrato — the core R2-D2 building block.

        vibrato_hz: LFO rate in Hz (0 = off). Modulates instantaneous frequency to
        produce the characteristic wobbly droid quality.
        vibrato_depth: frequency deviation in Hz at peak LFO swing.
        """
        n = int(KOKORO_SAMPLE_RATE * duration)
        t = np.linspace(0, duration, n, False)
        freq = np.linspace(freq_start, freq_end, n)
        if vibrato_hz > 0:
            freq = freq + vibrato_depth * np.sin(2 * np.pi * vibrato_hz * t)
        phase = np.cumsum(2 * np.pi * freq / KOKORO_SAMPLE_RATE)
        env = np.ones(n)
        a, d = max(1, int(n * 0.08)), max(1, int(n * 0.25))
        env[:a] = np.linspace(0, 1, a)
        env[-d:] = np.linspace(1, 0, d)
        return (np.sin(phase) * env * volume).astype(np.float32)

    def _r2_beep(self, freq, duration, volume=0.4):
        """Short pure-tone beep — punctuation between R2-D2 chirps."""
        n = int(KOKORO_SAMPLE_RATE * duration)
        t = np.linspace(0, duration, n, False)
        env = np.ones(n)
        a, d = max(1, int(n * 0.05)), max(1, int(n * 0.35))
        env[:a] = np.linspace(0, 1, a)
        env[-d:] = np.linspace(1, 0, d)
        return (np.sin(2 * np.pi * freq * t) * env * volume).astype(np.float32)

    def play_startup_sound(self):
        """Play an R2-D2-style happy greeting sequence on startup."""
        if not self.enable_tts:
            return
        try:
            g  = np.zeros(int(KOKORO_SAMPLE_RATE * 0.04), dtype=np.float32)
            gs = np.zeros(int(KOKORO_SAMPLE_RATE * 0.02), dtype=np.float32)
            sound = np.concatenate([
                # Opening ascending wobble sweep
                self._r2_chirp(480, 1600, 0.17, vibrato_hz=10, vibrato_depth=90),
                g,
                # Staccato arpeggio burst
                self._r2_beep(1800, 0.06), gs,
                self._r2_beep(1400, 0.05), gs,
                self._r2_beep(2000, 0.05), gs,
                self._r2_beep(1600, 0.05),
                g,
                # Descending wobble — question/acknowledgement feel
                self._r2_chirp(1700, 750, 0.15, vibrato_hz=13, vibrato_depth=110),
                g,
                # Rising two-note finish — happy affirmation
                self._r2_beep(1500, 0.06), gs,
                self._r2_beep(2200, 0.10, volume=0.5),
            ])
            sd.play(sound, samplerate=KOKORO_SAMPLE_RATE, device=self._output_device_index)
            sd.wait()
        except Exception as e:
            logger.warning("Startup sound error: %s", e)

    def play_thinking_sound(self):
        """Play a short R2-D2-style curious warble while processing a request."""
        if not self.enable_tts:
            return
        try:
            g  = np.zeros(int(KOKORO_SAMPLE_RATE * 0.03), dtype=np.float32)
            gs = np.zeros(int(KOKORO_SAMPLE_RATE * 0.02), dtype=np.float32)
            sound = np.concatenate([
                # Quick ascending wobble — "hmm, let me think"
                self._r2_chirp(780, 1700, 0.11, vibrato_hz=9, vibrato_depth=80),
                g,
                # Staccato pair
                self._r2_beep(1900, 0.06), gs,
                self._r2_beep(1500, 0.05),
                g,
                # Descending wobble close
                self._r2_chirp(1600, 900, 0.11, vibrato_hz=11, vibrato_depth=90),
                g,
                self._r2_beep(1650, 0.07),
            ])
            sd.play(sound, samplerate=KOKORO_SAMPLE_RATE, device=self._output_device_index)
            sd.wait()
        except Exception as e:
            logger.warning("Thinking sound error: %s", e)

    def speak(self, text: str):
        """Speak text aloud using Kokoro TTS with streaming playback.

        Each Kokoro chunk is written to a sounddevice OutputStream as it is
        generated, so the first words play immediately without waiting for the
        full response to be synthesised.
        """
        if not self.enable_tts or self.tts_backend is None:
            return

        try:
            self.tts_backend.speak(text)
        except Exception as e:
            logger.warning("TTS error: %s", e)

    def _record_until_silence(self, max_wait_seconds: float = 0) -> str:
        """Record audio with automatic silence detection, return temp WAV file path.

        Reuses self._shared_stream if set by wait_for_wake_word(), then clears it.
        max_wait_seconds: stop early if no speech starts within this window (0 = wait forever).
        """
        # Reuse the open stream from wake detection if available
        if self._shared_stream is not None:
            audio = self._shared_audio
            stream = self._shared_stream
            self._shared_audio = None
            self._shared_stream = None
        else:
            audio = pyaudio.PyAudio()
            stream = audio.open(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.RATE,
                input=True,
                input_device_index=self._input_device_index,
                frames_per_buffer=self.CHUNK,
            )

        logger.info("Recording...")

        frames = []
        silence_frames = 0
        silence_limit = int(self.RATE / self.CHUNK * self.silence_duration)
        max_wait_chunks = int(self.RATE / self.CHUNK * max_wait_seconds) if max_wait_seconds else 0
        waited_chunks = 0
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

                # Idle timeout: give up if no speech started within max_wait_seconds
                if not recording:
                    waited_chunks += 1
                    if max_wait_chunks and waited_chunks > max_wait_chunks:
                        break

        except KeyboardInterrupt:
            pass
        finally:
            sample_width = audio.get_sample_size(self.FORMAT)
            stream.stop_stream()
            stream.close()
            audio.terminate()

        temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        with wave.open(temp_file.name, "wb") as wf:
            wf.setnchannels(self.CHANNELS)
            wf.setsampwidth(sample_width)
            wf.setframerate(self.RATE)
            wf.writeframes(b"".join(frames))

        return temp_file.name

    def _transcribe(self, audio_file: str) -> str:
        """Transcribe a WAV file using the full Whisper model."""
        logger.info("Transcribing...")
        text = self.stt_backend.transcribe_file(audio_file)
        logger.info("Transcribed: %s", text)
        return text
