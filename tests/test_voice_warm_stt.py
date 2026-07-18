"""Tests for VoiceInterface.warm_stt — boot-time Whisper warm-up."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_voice():
    from core.voice import VoiceInterface

    v = VoiceInterface.__new__(VoiceInterface)
    v.RATE = 16000
    v.CHANNELS = 1
    v.FORMAT = 8  # pyaudio.paInt16; unused by warm_stt but set for parity
    v.stt_backend = MagicMock()
    v.stt_backend.transcribe_file.return_value = ""
    return v


class TestWarmStt(unittest.TestCase):
    def test_calls_transcribe_once(self):
        v = _make_voice()
        v.warm_stt()
        v.stt_backend.transcribe_file.assert_called_once()

    def test_passes_an_existing_wav_path(self):
        v = _make_voice()
        captured = {}

        def _capture(path):
            captured["path"] = path
            assert Path(path).exists(), "warm_stt must pass a real wav file"
            return ""

        v.stt_backend.transcribe_file.side_effect = _capture
        v.warm_stt()
        assert captured["path"].endswith(".wav")

    def test_swallows_backend_errors(self):
        v = _make_voice()
        v.stt_backend.transcribe_file.side_effect = RuntimeError("model exploded")
        # Must not raise.
        v.warm_stt()
