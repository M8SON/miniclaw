import unittest
import unittest.mock
from unittest.mock import patch

import main


class BuildVoiceInterfaceSelectionTests(unittest.TestCase):
    @patch("core.audio_devices.output_samplerate", return_value=48000)
    @patch("core.audio_devices.resolve_output_device", return_value=0)
    @patch("core.voice.VoiceInterface")
    @patch("main.build_wake_backend")
    @patch("main.build_stt_backend")
    def test_build_voice_interface_passes_selected_backend(
        self, mock_build_stt_backend, mock_build_wake_backend, mock_voice_interface,
        _mock_resolve, _mock_sr,
    ):
        fake_backend = object()
        mock_build_stt_backend.return_value = (
            fake_backend,
            "STT backend: Hybrid Whisper (transcription=hailo:base)",
        )
        mock_build_wake_backend.return_value = (object(), "Wake backend: openwakeword (hey_jarvis, threshold=0.5)")

        main.build_voice_interface()

        _, kwargs = mock_voice_interface.call_args
        self.assertIs(kwargs["stt_backend"], fake_backend)

    @patch("core.audio_devices.output_samplerate", return_value=48000)
    @patch("core.audio_devices.resolve_output_device", return_value=0)
    @patch("builtins.print")
    @patch("core.voice.VoiceInterface")
    @patch("main.build_wake_backend")
    @patch("main.build_stt_backend")
    def test_build_voice_interface_prints_backend_status_lines(
        self, mock_build_stt_backend, mock_build_wake_backend, mock_voice_interface,
        mock_print, _mock_resolve, _mock_sr,
    ):
        fake_backend = object()
        stt_msg = (
            "STT backend: CPU Whisper (transcription=cpu:base) — Hailo runtime unavailable"
        )
        mock_build_stt_backend.return_value = (fake_backend, stt_msg)
        mock_build_wake_backend.return_value = (object(), "Wake backend: openwakeword (hey_jarvis, threshold=0.5)")

        main.build_voice_interface()

        # Both backend status lines are printed at startup so the active
        # backends are visible without spelunking through INFO logs.
        printed = [c.args[0] for c in mock_print.call_args_list if c.args]
        self.assertIn(stt_msg, printed)
        self.assertTrue(
            any("TTS backend:" in line for line in printed),
            f"expected a TTS backend status line, got: {printed}",
        )


    @patch("core.audio_devices.output_samplerate", return_value=48000)
    @patch("core.audio_devices.resolve_output_device", return_value=0)
    @patch("core.voice.VoiceInterface")
    @patch("main.build_wake_backend")
    @patch("main.build_stt_backend")
    def test_build_voice_interface_passes_barge_in_disabled(
        self, mock_build_stt_backend, mock_build_wake_backend, mock_voice_interface,
        _mock_resolve, _mock_sr,
    ):
        mock_build_stt_backend.return_value = (object(), "STT backend: x")
        mock_build_wake_backend.return_value = (object(), "Wake backend: y")
        with patch.dict("os.environ", {"BARGE_IN_ENABLED": "false"}):
            main.build_voice_interface()
        _, kwargs = mock_voice_interface.call_args
        self.assertFalse(kwargs["barge_in_enabled"])

    @patch("core.audio_devices.output_samplerate", return_value=48000)
    @patch("core.audio_devices.resolve_output_device", return_value=0)
    @patch("core.voice.VoiceInterface")
    @patch("main.build_wake_backend")
    @patch("main.build_stt_backend")
    def test_build_voice_interface_barge_in_defaults_on(
        self, mock_build_stt_backend, mock_build_wake_backend, mock_voice_interface,
        _mock_resolve, _mock_sr,
    ):
        mock_build_stt_backend.return_value = (object(), "STT backend: x")
        mock_build_wake_backend.return_value = (object(), "Wake backend: y")
        import os as _os
        with patch.dict("os.environ", {}, clear=False):
            _os.environ.pop("BARGE_IN_ENABLED", None)
            main.build_voice_interface()
        _, kwargs = mock_voice_interface.call_args
        self.assertTrue(kwargs["barge_in_enabled"])


class VoiceWakeBackendIntegrationTests(unittest.TestCase):
    @patch("core.voice.pyaudio.PyAudio")
    @patch("core.voice.resolve_input_device", return_value=None)
    @patch("core.voice.resolve_output_device", return_value=None)
    @patch("core.voice.output_samplerate", return_value=48000)
    def test_wait_for_wake_word_uses_wake_backend_when_provided(
        self, _mock_sr, _mock_out, _mock_in, mock_pa
    ):
        from core.voice import VoiceInterface

        wake_backend = unittest.mock.MagicMock()
        # Simulate: silence, silence, wake. The exact number of False returns
        # depends on how often wait_for_wake_word evaluates the buffer; allow
        # any prefix of False before the True.
        wake_backend.detect.side_effect = [False, False, True]
        stt_backend = unittest.mock.MagicMock()
        tts_backend = unittest.mock.MagicMock()

        mock_stream = unittest.mock.MagicMock()
        # 1s of silence at 16kHz mono int16 per read
        mock_stream.read.return_value = b"\x00" * 32000
        mock_pa.return_value.open.return_value = mock_stream

        voice = VoiceInterface(
            stt_backend=stt_backend,
            wake_backend=wake_backend,
            tts_backend=tts_backend,
            enable_tts=False,
        )

        result = voice.wait_for_wake_word()

        self.assertTrue(result)
        # Some number of calls before True; at least one
        self.assertGreaterEqual(wake_backend.detect.call_count, 1)


class VadBackendIntegrationTests(unittest.TestCase):
    @patch("core.voice.pyaudio.PyAudio")
    @patch("core.voice.tempfile.NamedTemporaryFile")
    @patch("core.voice.wave.open")
    @patch("core.voice.resolve_input_device", return_value=None)
    @patch("core.voice.resolve_output_device", return_value=None)
    @patch("core.voice.output_samplerate", return_value=48000)
    def test_record_until_silence_uses_vad_backend(
        self, _mock_sr, _mock_out, _mock_in, mock_wave, mock_tmp, mock_pa
    ):
        from core.voice import VoiceInterface

        # 5 chunks of "speech" then 6 chunks of "silence"
        speech_pattern = [True] * 5 + [False] * 6
        vad_backend = unittest.mock.MagicMock()
        vad_backend.is_speech.side_effect = speech_pattern

        mock_stream = unittest.mock.MagicMock()
        mock_stream.read.return_value = b"\x00" * 2048  # 1024 int16 samples
        mock_pa.return_value.open.return_value = mock_stream

        mock_tmp.return_value.__enter__.return_value.name = "/tmp/test.wav"

        voice = VoiceInterface(
            stt_backend=unittest.mock.MagicMock(),
            tts_backend=unittest.mock.MagicMock(),
            wake_backend=unittest.mock.MagicMock(),
            vad_backend=vad_backend,
            vad_min_silence_ms=200,
            enable_tts=False,
        )

        voice._record_until_silence()

        # vad_backend.is_speech called for each chunk consumed (>=6 to reach endpoint)
        self.assertGreaterEqual(vad_backend.is_speech.call_count, 6)
        # vad_backend.reset called once at the top of the function
        vad_backend.reset.assert_called_once()


if __name__ == "__main__":
    unittest.main()
