import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

from core import voice_backends


class BuildSttBackendTests(unittest.TestCase):
    @patch("core.voice_backends.FasterWhisperBackend")
    @patch(
        "core.voice_backends.hailo_transcription_self_check",
        side_effect=RuntimeError("self-check failed"),
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_falls_back_to_cpu_when_hailo_self_check_raises(
        self,
        mock_runtime,
        mock_trans_assets,
        mock_trans_self_check,
        mock_fw,
    ):
        cpu_backend = object()
        mock_fw.return_value = cpu_backend

        backend, message = voice_backends.build_stt_backend(
            transcription_model_cpu="small",
            transcription_model_hailo="base",
        )

        self.assertIs(backend, cpu_backend)
        self.assertIn("Hailo self-check failed", message)

    @patch("core.voice_backends.FasterWhisperBackend")
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(False, "transcription model asset missing"),
        create=True,
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_falls_back_to_cpu_when_hailo_assets_missing(
        self, mock_runtime, mock_assets, mock_fw
    ):
        cpu_backend = object()
        mock_fw.return_value = cpu_backend

        backend, message = voice_backends.build_stt_backend(
            transcription_model_cpu="small",
            transcription_model_hailo="base",
        )

        self.assertIs(backend, cpu_backend)
        self.assertIn("transcription model asset missing", message)

    def test_asset_root_is_user_scoped(self):
        self.assertEqual(
            voice_backends.HAILO_WHISPER_ASSET_ROOT,
            Path.home() / ".kaizen" / "models" / "hailo-whisper",
        )


class HybridWhisperBackendTests(unittest.TestCase):
    @patch("core.voice_backends.HailoTranscriptionRuntime", create=True)
    def test_hailo_transcription_path_used_when_enabled(self, mock_trans_runtime_cls):
        runtime = mock_trans_runtime_cls.return_value
        runtime.transcribe_file.return_value = "spoken text"

        backend = voice_backends.HybridWhisperBackend(
            transcription_model="base",
            use_hailo_transcription=True,
        )
        text = backend.transcribe_file("/tmp/utt.wav")

        self.assertEqual(text, "spoken text")
        runtime.transcribe_file.assert_called_once()

    @patch("core.voice_backends.HailoTranscriptionRuntime", create=True)
    @patch("core.voice_backends.whisper.load_model")
    def test_cpu_transcription_path_used_when_hailo_disabled(
        self, mock_load_model, mock_trans_runtime_cls
    ):
        cpu_model = MagicMock()
        cpu_model.transcribe.return_value = {"text": "spoken"}
        mock_load_model.return_value = cpu_model

        backend = voice_backends.HybridWhisperBackend(
            transcription_model="base",
            use_hailo_transcription=False,
        )
        text = backend.transcribe_file("/tmp/utt.wav")

        self.assertEqual(text, "spoken")
        cpu_model.transcribe.assert_called_once()
        mock_trans_runtime_cls.assert_not_called()


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


class OpenWakeWordBackendTests(unittest.TestCase):
    @patch("core.voice_backends.openwakeword")
    def test_detect_returns_true_when_score_exceeds_threshold(self, mock_owww):
        mock_model = MagicMock()
        mock_model.predict.return_value = {"hey_jarvis_v0.1": 0.85}
        mock_owww.Model.return_value = mock_model
        mock_owww.models = {
            "hey_jarvis": {
                "model_path": "/fake/hey_jarvis_v0.1.onnx",
                "filename": "hey_jarvis_v0.1.onnx",
            }
        }

        backend = voice_backends.OpenWakeWordBackend(
            model_name="hey_jarvis", threshold=0.5
        )
        audio = np.zeros(1280, dtype=np.float32)

        self.assertTrue(backend.detect(audio))
        mock_owww.Model.assert_called_once_with(
            wakeword_model_paths=["/fake/hey_jarvis_v0.1.onnx"]
        )

    @patch("core.voice_backends.openwakeword")
    def test_detect_returns_false_when_score_below_threshold(self, mock_owww):
        mock_model = MagicMock()
        mock_model.predict.return_value = {"hey_jarvis_v0.1": 0.3}
        mock_owww.Model.return_value = mock_model
        mock_owww.models = {
            "hey_jarvis": {
                "model_path": "/fake/hey_jarvis_v0.1.onnx",
                "filename": "hey_jarvis_v0.1.onnx",
            }
        }

        backend = voice_backends.OpenWakeWordBackend(
            model_name="hey_jarvis", threshold=0.5
        )
        self.assertFalse(backend.detect(np.zeros(1280, dtype=np.float32)))

    @patch("core.voice_backends.openwakeword")
    def test_init_raises_for_unknown_model_name(self, mock_owww):
        mock_owww.models = {"hey_jarvis": {"model_path": "/fake/hey_jarvis_v0.1.onnx"}}
        with self.assertRaises(ValueError):
            voice_backends.OpenWakeWordBackend(
                model_name="not_a_real_model", threshold=0.5
            )

    @patch("core.voice_backends.openwakeword")
    def test_reset_clears_model_state(self, mock_owww):
        mock_model = MagicMock()
        mock_owww.Model.return_value = mock_model
        mock_owww.models = {
            "hey_jarvis": {"model_path": "/fake/hey_jarvis_v0.1.onnx"}
        }

        backend = voice_backends.OpenWakeWordBackend(
            model_name="hey_jarvis", threshold=0.5
        )
        backend.reset()

        mock_model.reset.assert_called_once()

    @patch("core.voice_backends._OPENWAKEWORD_AVAILABLE", False)
    def test_init_raises_when_openwakeword_unavailable(self):
        with self.assertRaises(ImportError):
            voice_backends.OpenWakeWordBackend(
                model_name="hey_jarvis", threshold=0.5
            )

    @patch("core.voice_backends.openwakeword")
    def test_detect_returns_true_when_score_equals_threshold(self, mock_owww):
        mock_model = MagicMock()
        mock_model.predict.return_value = {"hey_jarvis_v0.1": 0.5}
        mock_owww.Model.return_value = mock_model
        mock_owww.models = {
            "hey_jarvis": {"model_path": "/fake/hey_jarvis_v0.1.onnx"}
        }

        backend = voice_backends.OpenWakeWordBackend(
            model_name="hey_jarvis", threshold=0.5
        )

        self.assertTrue(backend.detect(np.zeros(1280, dtype=np.float32)))


class BuildWakeBackendTests(unittest.TestCase):
    @patch("core.voice_backends.OpenWakeWordBackend")
    def test_builds_openwakeword(self, mock_owww):
        instance = object()
        mock_owww.return_value = instance

        backend, message = voice_backends.build_wake_backend(
            model_name="hey_jarvis",
            threshold=0.5,
        )

        self.assertIs(backend, instance)
        self.assertIn("openwakeword", message)
        self.assertIn("hey_jarvis", message)

    @patch(
        "core.voice_backends.OpenWakeWordBackend",
        side_effect=ImportError("openwakeword not installed"),
    )
    def test_raises_when_openwakeword_unavailable(self, mock_owww):
        # No fallback path: openWakeWord is the only supported wake backend.
        # Failing loud beats silently routing to a known-bad whisper-substring
        # path that hallucinates wake events.
        with self.assertRaises(ImportError):
            voice_backends.build_wake_backend(
                model_name="hey_jarvis",
                threshold=0.5,
            )


class RmsVadBackendTests(unittest.TestCase):
    def test_is_speech_true_when_amplitude_above_threshold(self):
        backend = voice_backends.RmsVadBackend(threshold=1000)
        loud = (np.ones(1024, dtype=np.int16) * 5000).astype(np.int16)
        self.assertTrue(backend.is_speech(loud))

    def test_is_speech_false_when_amplitude_below_threshold(self):
        backend = voice_backends.RmsVadBackend(threshold=1000)
        quiet = np.zeros(1024, dtype=np.int16)
        self.assertFalse(backend.is_speech(quiet))

    def test_reset_is_noop(self):
        backend = voice_backends.RmsVadBackend(threshold=1000)
        backend.reset()  # should not raise

    def test_is_speech_handles_float32_normalized_audio(self):
        backend = voice_backends.RmsVadBackend(threshold=1000)
        # Normalized float32 audio at amplitude ~0.15 ≈ int16 5000 — well above
        # threshold 1000. Without proper rescaling this would astype(int16)
        # truncate to all zeros and incorrectly return False.
        loud_float = np.ones(1024, dtype=np.float32) * 0.15
        self.assertTrue(backend.is_speech(loud_float))


class SileroVadBackendTests(unittest.TestCase):
    @patch("core.voice_backends.silero_vad")
    @patch("core.voice_backends.torch")
    def test_is_speech_true_when_score_above_threshold(self, mock_torch, mock_silero):
        mock_model = MagicMock()
        mock_model.return_value = MagicMock(item=lambda: 0.85)
        mock_silero.load_silero_vad.return_value = mock_model
        mock_torch.from_numpy.side_effect = lambda x: x  # passthrough

        backend = voice_backends.SileroVadBackend(threshold=0.5)
        # 1024 samples → exactly two 512-sample sub-frames consumed
        audio = np.zeros(1024, dtype=np.float32)

        self.assertTrue(backend.is_speech(audio))
        self.assertEqual(mock_model.call_count, 2)

    @patch("core.voice_backends.silero_vad")
    @patch("core.voice_backends.torch")
    def test_is_speech_false_when_score_below_threshold(self, mock_torch, mock_silero):
        mock_model = MagicMock()
        mock_model.return_value = MagicMock(item=lambda: 0.2)
        mock_silero.load_silero_vad.return_value = mock_model
        mock_torch.from_numpy.side_effect = lambda x: x

        backend = voice_backends.SileroVadBackend(threshold=0.5)
        self.assertFalse(backend.is_speech(np.zeros(1024, dtype=np.float32)))

    @patch("core.voice_backends.silero_vad")
    @patch("core.voice_backends.torch")
    def test_carry_over_short_chunks(self, mock_torch, mock_silero):
        mock_model = MagicMock()
        mock_model.return_value = MagicMock(item=lambda: 0.0)
        mock_silero.load_silero_vad.return_value = mock_model
        mock_torch.from_numpy.side_effect = lambda x: x

        backend = voice_backends.SileroVadBackend(threshold=0.5)
        # Two 300-sample chunks: first call has no full 512-frame, second does
        backend.is_speech(np.zeros(300, dtype=np.float32))
        self.assertEqual(mock_model.call_count, 0)
        backend.is_speech(np.zeros(300, dtype=np.float32))
        self.assertEqual(mock_model.call_count, 1)

    @patch("core.voice_backends.silero_vad")
    @patch("core.voice_backends.torch")
    def test_reset_clears_buffer_and_model_state(self, mock_torch, mock_silero):
        mock_model = MagicMock()
        mock_silero.load_silero_vad.return_value = mock_model
        mock_torch.from_numpy.side_effect = lambda x: x

        backend = voice_backends.SileroVadBackend(threshold=0.5)
        backend.is_speech(np.zeros(300, dtype=np.float32))  # leaves 300 samples in buffer
        backend.reset()
        # After reset, a 200-sample chunk should not yet trigger the model (buffer cleared)
        backend.is_speech(np.zeros(200, dtype=np.float32))
        self.assertEqual(mock_model.call_count, 0)
        mock_model.reset_states.assert_called_once()

    @patch("core.voice_backends.silero_vad")
    @patch("core.voice_backends.torch")
    def test_is_speech_true_when_score_equals_threshold(self, mock_torch, mock_silero):
        mock_model = MagicMock()
        mock_model.return_value = MagicMock(item=lambda: 0.5)
        mock_silero.load_silero_vad.return_value = mock_model
        mock_torch.from_numpy.side_effect = lambda x: x

        backend = voice_backends.SileroVadBackend(threshold=0.5)
        self.assertTrue(backend.is_speech(np.zeros(512, dtype=np.float32)))


class BuildSttBackendCpuPathTests(unittest.TestCase):
    @patch("core.voice_backends.FasterWhisperBackend")
    @patch("core.voice_backends.hailo_runtime_available", return_value=False)
    def test_uses_faster_whisper_with_cpu_model_when_no_hailo(
        self, mock_runtime, mock_fw
    ):
        instance = object()
        mock_fw.return_value = instance

        backend, message = voice_backends.build_stt_backend(
            transcription_model_cpu="small",
            transcription_model_hailo="base",
        )

        self.assertIs(backend, instance)
        mock_fw.assert_called_once_with(model_name="small")
        self.assertIn("cpu:small", message)
        self.assertIn("faster-whisper", message)

    @patch("core.voice_backends.WhisperBackend")
    @patch(
        "core.voice_backends.FasterWhisperBackend",
        side_effect=ImportError("faster-whisper missing"),
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=False)
    def test_falls_back_to_openai_whisper_when_faster_whisper_missing(
        self, mock_runtime, mock_fw, mock_whisper
    ):
        instance = object()
        mock_whisper.return_value = instance

        backend, message = voice_backends.build_stt_backend(
            transcription_model_cpu="small",
            transcription_model_hailo="base",
        )

        self.assertIs(backend, instance)
        mock_whisper.assert_called_once_with(transcription_model="small")
        self.assertIn("openai-whisper fallback", message.lower())

    @patch("core.voice_backends.HybridWhisperBackend", create=True)
    @patch(
        "core.voice_backends.hailo_transcription_self_check",
        return_value=None,
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_hailo_path_uses_hailo_model_variant_not_cpu_variant(
        self,
        mock_runtime,
        mock_assets,
        mock_check,
        mock_hybrid,
    ):
        instance = object()
        mock_hybrid.return_value = instance

        backend, message = voice_backends.build_stt_backend(
            transcription_model_cpu="small",
            transcription_model_hailo="base",
        )

        self.assertIs(backend, instance)
        # Hybrid is built with the Hailo variant — small is unsupported by Hailo
        # and would always fall back if accidentally passed instead.
        mock_hybrid.assert_called_once_with(
            transcription_model="base",
            use_hailo_transcription=True,
        )
        self.assertIn("hailo:base", message)

    @patch("core.voice_backends.FasterWhisperBackend")
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_hailo_unsupported_variant_falls_back_to_cpu(
        self, mock_runtime, mock_fw
    ):
        instance = object()
        mock_fw.return_value = instance

        backend, message = voice_backends.build_stt_backend(
            transcription_model_cpu="small",
            transcription_model_hailo="medium",  # not in SUPPORTED_HAILO_*
        )

        self.assertIs(backend, instance)
        mock_fw.assert_called_once_with(model_name="small")
        self.assertIn("cpu:small", message)


class FasterWhisperBackendTests(unittest.TestCase):
    @patch("core.voice_backends.WhisperModel")
    def test_transcribe_file_returns_concatenated_text(self, mock_model_cls):
        seg1 = MagicMock(text="Hello world.")
        seg2 = MagicMock(text=" Goodbye.")
        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([seg1, seg2], MagicMock())
        mock_model_cls.return_value = mock_model

        backend = voice_backends.FasterWhisperBackend(model_name="small")
        result = backend.transcribe_file("/tmp/fake.wav")

        self.assertEqual(result, "Hello world. Goodbye.")

    @patch("core.voice_backends.WhisperModel")
    def test_loads_model_with_int8_compute_type_for_cpu(self, mock_model_cls):
        voice_backends.FasterWhisperBackend(model_name="small")
        args, kwargs = mock_model_cls.call_args
        self.assertEqual(args[0], "small")
        self.assertEqual(kwargs.get("compute_type"), "int8")
        self.assertEqual(kwargs.get("device"), "cpu")

    @patch("core.voice_backends._FASTER_WHISPER_AVAILABLE", False)
    def test_init_raises_when_faster_whisper_unavailable(self):
        with self.assertRaises(ImportError):
            voice_backends.FasterWhisperBackend(model_name="small")


class BuildVadBackendTests(unittest.TestCase):
    @patch("core.voice_backends.SileroVadBackend")
    def test_returns_silero_when_backend_is_silero(self, mock_silero):
        instance = object()
        mock_silero.return_value = instance

        backend, message = voice_backends.build_vad_backend(
            backend_name="silero", threshold=0.5, rms_threshold=1000
        )

        self.assertIs(backend, instance)
        self.assertIn("silero", message)

    @patch("core.voice_backends.RmsVadBackend")
    def test_returns_rms_when_backend_is_rms(self, mock_rms):
        instance = object()
        mock_rms.return_value = instance

        backend, message = voice_backends.build_vad_backend(
            backend_name="rms", threshold=0.5, rms_threshold=1000
        )

        self.assertIs(backend, instance)
        self.assertIn("rms", message)

    @patch("core.voice_backends.RmsVadBackend")
    @patch(
        "core.voice_backends.SileroVadBackend",
        side_effect=ImportError("silero-vad not installed"),
    )
    def test_falls_back_to_rms_on_silero_failure(self, mock_silero, mock_rms):
        instance = object()
        mock_rms.return_value = instance

        backend, message = voice_backends.build_vad_backend(
            backend_name="silero", threshold=0.5, rms_threshold=1000
        )

        self.assertIs(backend, instance)
        self.assertIn("fallback", message.lower())

    def test_raises_for_unknown_backend_name(self):
        with self.assertRaises(ValueError):
            voice_backends.build_vad_backend(
                backend_name="nonexistent", threshold=0.5, rms_threshold=1000
            )


class ElevenLabsTTSBackendTests(unittest.TestCase):
    def _backend(self, stream_return):
        mock_client = MagicMock()
        mock_client.text_to_speech.stream.return_value = stream_return
        return voice_backends.ElevenLabsTTSBackend(
            voice_id="onwK4e9ZLuTAKqWW03F9",
            api_key="k",
            client=mock_client,
        ), mock_client

    def test_synth_audio_converts_pcm_bytes_to_float32(self):
        # int16 samples 0, 16384, -16384 → float32 0.0, 0.5, -0.5
        pcm = np.array([0, 16384, -16384], dtype=np.int16).tobytes()
        backend, mock_client = self._backend([pcm])

        chunks = list(backend._synth_audio("hi"))

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].dtype, np.float32)
        np.testing.assert_allclose(chunks[0], [0.0, 0.5, -0.5], atol=1e-4)

    def test_synth_audio_requests_flash_pcm_24000(self):
        backend, mock_client = self._backend([b""])
        list(backend._synth_audio("hello"))
        _, kwargs = mock_client.text_to_speech.stream.call_args
        self.assertEqual(kwargs["voice_id"], "onwK4e9ZLuTAKqWW03F9")
        self.assertEqual(kwargs["model_id"], "eleven_flash_v2_5")
        self.assertEqual(kwargs["output_format"], "pcm_24000")
        self.assertEqual(kwargs["text"], "hello")

    def test_synth_audio_skips_non_bytes_and_empty_chunks(self):
        pcm = np.array([16384], dtype=np.int16).tobytes()
        backend, _ = self._backend(["metadata-not-bytes", b"", pcm])
        chunks = list(backend._synth_audio("hi"))
        self.assertEqual(len(chunks), 1)

    def test_synth_audio_closes_stream_on_break(self):
        # Simulate a barge-in: consumer stops early. The generator's finally
        # must call the underlying stream's close().
        closed = {"n": 0}

        class ClosableStream:
            def __init__(self):
                self._data = [np.array([1], dtype=np.int16).tobytes()] * 5
            def __iter__(self):
                return iter(self._data)
            def close(self):
                closed["n"] += 1

        backend, _ = self._backend(ClosableStream())
        gen = backend._synth_audio("hi")
        next(gen)          # pull one chunk
        gen.close()        # consumer abandons (as a break would)
        self.assertEqual(closed["n"], 1)

    def test_prebuffer_defaults_to_zero(self):
        backend, _ = self._backend([b""])
        self.assertEqual(backend.PREBUFFER_MS, 0)
        self.assertEqual(backend.sample_rate, voice_backends.KOKORO_SAMPLE_RATE)


class ElevenLabsSelfCheckTests(unittest.TestCase):
    def test_self_check_consumes_one_chunk(self):
        mock_client = MagicMock()
        mock_client.text_to_speech.stream.return_value = [
            np.array([1], dtype=np.int16).tobytes()
        ]
        backend = voice_backends.ElevenLabsTTSBackend(
            voice_id="v", api_key="k", client=mock_client
        )
        voice_backends.elevenlabs_self_check(backend)  # should not raise
        mock_client.text_to_speech.stream.assert_called_once()

    def test_self_check_raises_when_stream_errors(self):
        mock_client = MagicMock()
        mock_client.text_to_speech.stream.side_effect = RuntimeError("401 bad key")
        backend = voice_backends.ElevenLabsTTSBackend(
            voice_id="v", api_key="k", client=mock_client
        )
        with self.assertRaises(RuntimeError):
            voice_backends.elevenlabs_self_check(backend)


if __name__ == "__main__":
    unittest.main()
