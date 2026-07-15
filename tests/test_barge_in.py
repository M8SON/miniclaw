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


if __name__ == "__main__":
    unittest.main()
