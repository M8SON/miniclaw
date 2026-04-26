import unittest
from unittest.mock import patch

import main


class BuildVoiceInterfaceSelectionTests(unittest.TestCase):
    @patch("core.voice.VoiceInterface")
    @patch("main.build_stt_backend")
    def test_build_voice_interface_passes_selected_backend(
        self, mock_build_stt_backend, mock_voice_interface
    ):
        fake_backend = object()
        mock_build_stt_backend.return_value = (
            fake_backend,
            "STT backend: Hybrid Whisper (wake=cpu:tiny, transcription=hailo:base)",
        )

        main.build_voice_interface()

        _, kwargs = mock_voice_interface.call_args
        self.assertIs(kwargs["stt_backend"], fake_backend)

    @patch("builtins.print")
    @patch("core.voice.VoiceInterface")
    @patch("main.build_stt_backend")
    def test_build_voice_interface_prints_backend_status_once(
        self, mock_build_stt_backend, mock_voice_interface, mock_print
    ):
        fake_backend = object()
        message = "STT backend: CPU Whisper fallback — Hailo runtime unavailable"
        mock_build_stt_backend.return_value = (fake_backend, message)

        main.build_voice_interface()

        mock_print.assert_called_once_with(message)


if __name__ == "__main__":
    unittest.main()
