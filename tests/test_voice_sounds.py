"""Smoke tests for VoiceInterface R2-D2 sound helpers — they must be callable
without raising under both enabled and disabled TTS configurations."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


class PlayAckSound(unittest.TestCase):
    def test_method_exists_on_voice_interface(self):
        from core.voice import VoiceInterface
        self.assertTrue(hasattr(VoiceInterface, "play_ack_sound"))
        self.assertTrue(callable(VoiceInterface.play_ack_sound))

    def test_no_op_when_tts_disabled(self):
        """When enable_tts is False, the method must return immediately
        without touching sounddevice. We verify by patching sd.play and
        asserting it was never called."""
        from core.voice import VoiceInterface
        v = VoiceInterface.__new__(VoiceInterface)
        v.enable_tts = False
        with patch("core.voice.sd.play") as mock_play:
            v.play_ack_sound()
        mock_play.assert_not_called()

    def test_swallows_audio_errors(self):
        """Audio backend exceptions must be logged and swallowed —
        the voice loop can't crash on a missing speaker."""
        from core.voice import VoiceInterface
        v = VoiceInterface.__new__(VoiceInterface)
        v.enable_tts = True
        v._output_samplerate = 48000
        v._output_device_index = 0
        with patch("core.voice.sd.play", side_effect=RuntimeError("audio gone")):
            v.play_ack_sound()  # must not raise


class PlayThinkingSoundNonBlocking(unittest.TestCase):
    def test_does_not_block_on_sd_wait(self):
        """play_thinking_sound now fires from on_speech_done the instant the
        user stops talking, just before STT runs. It must play asynchronously
        (no sd.wait) so it overlaps the STT/processing wait instead of
        delaying it — matching play_ack_sound / play_response_ready_sound."""
        from core.voice import VoiceInterface
        v = VoiceInterface.__new__(VoiceInterface)
        v.enable_tts = True
        v._output_samplerate = 48000
        v._output_device_index = 0
        with patch("core.voice.sd.play") as mock_play, \
             patch("core.voice.sd.wait") as mock_wait:
            v.play_thinking_sound()
        mock_play.assert_called_once()
        mock_wait.assert_not_called()


if __name__ == "__main__":
    unittest.main()
