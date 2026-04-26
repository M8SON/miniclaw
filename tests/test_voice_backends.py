import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import voice_backends


class BuildSttBackendTests(unittest.TestCase):
    @patch("core.voice_backends.WhisperBackend")
    @patch("core.voice_backends.hailo_runtime_available", return_value=False)
    def test_falls_back_to_cpu_when_hailo_runtime_unavailable(
        self, mock_runtime, mock_whisper_backend
    ):
        cpu_backend = object()
        mock_whisper_backend.return_value = cpu_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, cpu_backend)
        self.assertEqual(
            message, "STT backend: CPU Whisper fallback — Hailo runtime unavailable"
        )

    @patch("core.voice_backends.WhisperBackend")
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_falls_back_to_cpu_when_transcription_variant_not_supported(
        self, mock_runtime, mock_whisper_backend
    ):
        cpu_backend = object()
        mock_whisper_backend.return_value = cpu_backend

        backend, message = voice_backends.build_stt_backend("tiny", "small")

        self.assertIs(backend, cpu_backend)
        self.assertEqual(
            message,
            "STT backend: CPU Whisper fallback — transcription model variant unsupported by Hailo",
        )

    @patch("core.voice_backends.WhisperBackend")
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(False, "transcription model asset missing"),
        create=True,
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_falls_back_to_cpu_when_transcription_asset_missing(
        self, mock_runtime, mock_assets, mock_whisper_backend
    ):
        cpu_backend = object()
        mock_whisper_backend.return_value = cpu_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, cpu_backend)
        self.assertEqual(
            message,
            "STT backend: CPU Whisper fallback — transcription model asset missing",
        )

    @patch("core.voice_backends.WhisperBackend")
    @patch(
        "core.voice_backends.HailoTranscriptionRuntime.self_check",
        side_effect=RuntimeError("self-check failed"),
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_falls_back_to_cpu_when_hailo_self_check_fails(
        self, mock_runtime, mock_assets, mock_self_check, mock_whisper_backend
    ):
        cpu_backend = object()
        mock_whisper_backend.return_value = cpu_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, cpu_backend)
        self.assertEqual(
            message, "STT backend: CPU Whisper fallback — Hailo self-check failed"
        )

    @patch("core.voice_backends.HybridWhisperBackend", create=True)
    @patch(
        "core.voice_backends.HailoTranscriptionRuntime.self_check",
        return_value=None,
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_selects_hybrid_backend_when_runtime_and_assets_are_ready(
        self, mock_runtime, mock_assets, mock_self_check, mock_hybrid_backend
    ):
        hybrid_backend = object()
        mock_hybrid_backend.return_value = hybrid_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, hybrid_backend)
        self.assertEqual(
            message,
            "STT backend: Hybrid Whisper (wake=cpu:tiny, transcription=hailo:base)",
        )

    def test_asset_root_is_user_scoped(self):
        self.assertEqual(
            voice_backends.HAILO_WHISPER_ASSET_ROOT,
            Path.home() / ".miniclaw" / "models" / "hailo-whisper",
        )


class HybridWhisperBackendTests(unittest.TestCase):
    @patch("core.voice_backends.HailoTranscriptionRuntime", create=True)
    @patch("core.voice_backends.whisper.load_model")
    def test_wake_audio_stays_on_cpu_but_file_transcription_uses_hailo(
        self, mock_load_model, mock_runtime_cls
    ):
        wake_model = Mock()
        wake_model.transcribe.return_value = {"text": "Computer"}
        mock_load_model.return_value = wake_model

        runtime = mock_runtime_cls.return_value
        runtime.transcribe_file.return_value = "transcribed by hailo"

        backend = voice_backends.HybridWhisperBackend(
            wake_model="tiny",
            transcription_model="base",
        )

        wake_text = backend.transcribe_wake_audio([0.0, 0.1])
        file_text = backend.transcribe_file("/tmp/example.wav")

        self.assertEqual(wake_text, "computer")
        self.assertEqual(file_text, "transcribed by hailo")
        runtime.transcribe_file.assert_called_once_with("/tmp/example.wav")
        mock_load_model.assert_called_once_with("tiny")


class HailoRuntimeAssetTests(unittest.TestCase):
    def test_self_check_rejects_missing_model_dir(self):
        from core.hailo_whisper_runtime import HailoTranscriptionRuntime

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "transcription model asset missing"):
                HailoTranscriptionRuntime.self_check(
                    model_name="base",
                    assets_root=Path(tmp),
                )

    def test_self_check_rejects_missing_hailo_platform(self):
        from core.hailo_whisper_runtime import HailoTranscriptionRuntime

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "base" / "decoder_assets").mkdir(parents=True)
            (root / "base" / "hefs" / "hailo8").mkdir(parents=True)
            (root / "base" / "decoder_assets" / "token_embedding_weight_base.npy").touch()
            (root / "base" / "decoder_assets" / "onnx_add_input_base.npy").touch()
            (root / "base" / "hefs" / "hailo8" / "base-whisper-encoder-5s.hef").touch()
            (root / "base" / "hefs" / "hailo8" / "base-whisper-decoder-fixed-sequence-matmul-split.hef").touch()

            with patch(
                "core.hailo_whisper_runtime._hailo_platform_import_error",
                ModuleNotFoundError("no hailo"),
            ):
                with self.assertRaisesRegex(RuntimeError, "hailo_platform python module not installed"):
                    HailoTranscriptionRuntime.self_check(
                        model_name="base",
                        assets_root=root,
                    )


if __name__ == "__main__":
    unittest.main()
