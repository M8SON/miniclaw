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


if __name__ == "__main__":
    unittest.main()
