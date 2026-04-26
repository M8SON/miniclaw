import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import voice_backends


class BuildSttBackendTests(unittest.TestCase):
    @patch("core.voice_backends.WhisperBackend")
    @patch("core.voice_backends.hailo_runtime_available", return_value=False)
    def test_falls_back_to_cpu_for_both_paths_when_hailo_runtime_unavailable(
        self, mock_runtime, mock_whisper_backend
    ):
        cpu_backend = object()
        mock_whisper_backend.return_value = cpu_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, cpu_backend)
        self.assertEqual(
            message,
            "STT backend: CPU Whisper fallback (wake=cpu:tiny, transcription=cpu:base) — Hailo runtime unavailable",
        )

    @patch("core.voice_backends.HybridWhisperBackend", create=True)
    @patch(
        "core.voice_backends.hailo_transcription_self_check",
        side_effect=RuntimeError("transcription self-check failed"),
        create=True,
    )
    @patch("core.voice_backends.hailo_wake_self_check", return_value=None, create=True)
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_wake_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_selects_hailo_wake_but_cpu_transcription_when_only_wake_is_ready(
        self,
        mock_runtime,
        mock_wake_assets,
        mock_trans_assets,
        mock_wake_self_check,
        mock_trans_self_check,
        mock_hybrid_backend,
    ):
        hybrid_backend = object()
        mock_hybrid_backend.return_value = hybrid_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, hybrid_backend)
        self.assertEqual(
            message,
            "STT backend: Hybrid Whisper (wake=hailo:tiny, transcription=cpu:base)",
        )

    @patch("core.voice_backends.HybridWhisperBackend", create=True)
    @patch(
        "core.voice_backends.hailo_transcription_self_check",
        return_value=None,
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_wake_self_check",
        side_effect=RuntimeError("wake self-check failed"),
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_wake_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_selects_cpu_wake_but_hailo_transcription_when_only_transcription_is_ready(
        self,
        mock_runtime,
        mock_wake_assets,
        mock_trans_assets,
        mock_wake_self_check,
        mock_trans_self_check,
        mock_hybrid_backend,
    ):
        hybrid_backend = object()
        mock_hybrid_backend.return_value = hybrid_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, hybrid_backend)
        self.assertEqual(
            message,
            "STT backend: Hybrid Whisper (wake=cpu:tiny, transcription=hailo:base)",
        )

    @patch("core.voice_backends.HybridWhisperBackend", create=True)
    @patch(
        "core.voice_backends.hailo_transcription_self_check",
        return_value=None,
        create=True,
    )
    @patch("core.voice_backends.hailo_wake_self_check", return_value=None, create=True)
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_wake_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_selects_hailo_for_both_paths_when_both_are_ready(
        self,
        mock_runtime,
        mock_wake_assets,
        mock_trans_assets,
        mock_wake_self_check,
        mock_trans_self_check,
        mock_hybrid_backend,
    ):
        hybrid_backend = object()
        mock_hybrid_backend.return_value = hybrid_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, hybrid_backend)
        self.assertEqual(
            message,
            "STT backend: Hybrid Whisper (wake=hailo:tiny, transcription=hailo:base)",
        )

    @patch("core.voice_backends.HybridWhisperBackend", create=True)
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_wake_variant_unsupported_for_hailo_falls_back_only_for_wake(
        self, mock_runtime, mock_hybrid_backend
    ):
        hybrid_backend = object()
        mock_hybrid_backend.return_value = hybrid_backend

        with patch(
            "core.voice_backends.hailo_transcription_assets_available",
            return_value=(True, ""),
            create=True,
        ), patch(
            "core.voice_backends.hailo_transcription_self_check",
            return_value=None,
            create=True,
        ):
            backend, message = voice_backends.build_stt_backend("small", "base")

        self.assertIs(backend, hybrid_backend)
        self.assertEqual(
            message,
            "STT backend: Hybrid Whisper (wake=cpu:small, transcription=hailo:base)",
        )

    def test_asset_root_is_user_scoped(self):
        self.assertEqual(
            voice_backends.HAILO_WHISPER_ASSET_ROOT,
            Path.home() / ".miniclaw" / "models" / "hailo-whisper",
        )


class HybridWhisperBackendTests(unittest.TestCase):
    @patch("core.voice_backends.HailoTranscriptionRuntime", create=True)
    @patch("core.voice_backends.HailoWakeRuntime", create=True)
    @patch("core.voice_backends.whisper.load_model")
    def test_hailo_wake_runtime_is_used_when_enabled(
        self, mock_load_model, mock_wake_runtime_cls, mock_trans_runtime_cls
    ):
        wake_model = Mock()
        mock_load_model.return_value = wake_model

        wake_runtime = mock_wake_runtime_cls.return_value
        wake_runtime.transcribe_wake_audio.return_value = "computer"

        backend = voice_backends.HybridWhisperBackend(
            wake_model="tiny",
            transcription_model="base",
            use_hailo_wake=True,
            use_hailo_transcription=False,
        )

        wake_text = backend.transcribe_wake_audio([0.0, 0.1])

        self.assertEqual(wake_text, "computer")
        wake_runtime.transcribe_wake_audio.assert_called_once()
        mock_load_model.assert_called_once_with("base")

    @patch("core.voice_backends.HailoTranscriptionRuntime", create=True)
    @patch("core.voice_backends.HailoWakeRuntime", create=True)
    @patch("core.voice_backends.whisper.load_model")
    def test_cpu_wake_path_is_used_when_hailo_wake_disabled(
        self, mock_load_model, mock_wake_runtime_cls, mock_trans_runtime_cls
    ):
        wake_model = Mock()
        wake_model.transcribe.return_value = {"text": "Computer"}
        mock_load_model.return_value = wake_model

        backend = voice_backends.HybridWhisperBackend(
            wake_model="tiny",
            transcription_model="base",
            use_hailo_wake=False,
            use_hailo_transcription=True,
        )

        wake_text = backend.transcribe_wake_audio([0.0, 0.1])

        self.assertEqual(wake_text, "computer")
        wake_model.transcribe.assert_called_once()
        mock_wake_runtime_cls.assert_not_called()


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


class HailoWakeRuntimeAssetTests(unittest.TestCase):
    def test_wake_self_check_rejects_missing_model_dir(self):
        from core.hailo_whisper_runtime import HailoWakeRuntime

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "wake model asset missing"):
                HailoWakeRuntime.self_check(
                    model_name="tiny",
                    assets_root=Path(tmp),
                )


if __name__ == "__main__":
    unittest.main()
