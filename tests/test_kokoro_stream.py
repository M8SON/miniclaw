"""Tests for KokoroTTSBackend.speak_stream — per-sentence flushing."""

import unittest
from unittest.mock import MagicMock, patch


class SpeakStreamTests(unittest.TestCase):
    def _make_backend(self):
        from core import voice_backends

        with patch.object(voice_backends, "KPipeline"):
            backend = voice_backends.KokoroTTSBackend()
        # Replace the pipeline with a mock that returns an empty iterable
        # for every call — we only count flushes, not synthesise audio.
        backend.pipeline = MagicMock()
        backend.pipeline.side_effect = lambda *a, **k: iter([])
        return backend

    @patch("core.voice_backends.sd")
    def test_flushes_on_period(self, mock_sd):
        backend = self._make_backend()

        chunks = iter(["Hello", " world", ".", " More"])
        backend.speak_stream(chunks)

        # Two flushes: "Hello world." (sentence) + " More" (final remainder).
        self.assertEqual(backend.pipeline.call_count, 2)

    @patch("core.voice_backends.sd")
    def test_flushes_on_question_and_exclaim(self, mock_sd):
        backend = self._make_backend()

        chunks = iter(["Are you sure", "?", " Yes", "!"])
        backend.speak_stream(chunks)

        self.assertEqual(backend.pipeline.call_count, 2)

    @patch("core.voice_backends.sd")
    def test_per_sentence_flushing_for_multi_sentence_reply(self, mock_sd):
        """4 sentences = 4 flushes. Each Kokoro call on Pi 5 only yields one
        chunk after full synthesis, so flushing per-sentence distributes the
        synthesis waits across the response instead of stacking them into
        one large gap (which the batched-rest strategy produced)."""
        backend = self._make_backend()
        chunks = iter([
            "First sentence.",
            " Second one.",
            " Third here.",
            " And last.",
        ])
        backend.speak_stream(chunks)
        self.assertEqual(backend.pipeline.call_count, 4)
        flushed_texts = [c.args[0] for c in backend.pipeline.call_args_list]
        self.assertEqual(flushed_texts[0], "First sentence.")
        self.assertEqual(flushed_texts[1], " Second one.")
        self.assertEqual(flushed_texts[2], " Third here.")
        self.assertEqual(flushed_texts[3], " And last.")

    @patch("core.voice_backends.sd")
    def test_flushes_at_buffer_cap(self, mock_sd):
        backend = self._make_backend()

        # 250 chars, no sentence boundary — cap is 200, leaves 50 for trailing flush.
        long = "a" * 250
        backend.speak_stream(iter([long]))

        self.assertEqual(backend.pipeline.call_count, 2)

    @patch("core.voice_backends.sd")
    def test_no_flush_on_empty_input(self, mock_sd):
        backend = self._make_backend()
        backend.speak_stream(iter([]))
        self.assertEqual(backend.pipeline.call_count, 0)

    @patch("core.voice_backends.sd")
    def test_no_flush_on_whitespace_only(self, mock_sd):
        backend = self._make_backend()
        backend.speak_stream(iter(["   ", "\n\n"]))
        self.assertEqual(backend.pipeline.call_count, 0)

    @patch("core.voice_backends.sd")
    def test_first_flush_breaks_at_early_clause_boundary(self, mock_sd):
        """The first flush drives time-to-first-audio, so it should break at
        the first clause boundary (comma/em-dash/semicolon/colon) instead of
        waiting for the whole sentence to be synthesised."""
        backend = self._make_backend()
        text = "I caught something that sounds like a list of numbers, but I am not sure."
        backend.speak_stream(iter([text]))

        flushed = [c.args[0] for c in backend.pipeline.call_args_list]
        self.assertEqual(
            flushed[0], "I caught something that sounds like a list of numbers,"
        )
        self.assertGreaterEqual(backend.pipeline.call_count, 2)

    @patch("core.voice_backends.sd")
    def test_first_flush_ignores_clause_boundary_below_min_length(self, mock_sd):
        """A clause boundary in the first few chars must not trigger a tiny,
        choppy first flush — the min-length guard defers to the sentence end."""
        backend = self._make_backend()
        backend.speak_stream(iter(["Hi, I am here."]))

        flushed = [c.args[0] for c in backend.pipeline.call_args_list]
        self.assertEqual(flushed[0], "Hi, I am here.")
        self.assertEqual(backend.pipeline.call_count, 1)

    @patch("core.voice_backends.sd")
    def test_clause_split_applies_only_to_first_flush(self, mock_sd):
        """Only the first flush is clause-split; later sentences keep their
        commas (sentence N+1 synth already overlaps sentence N playback, so
        there's no first-audio benefit and clause-splitting them only risks
        choppiness)."""
        backend = self._make_backend()
        text = (
            "The opening clause is quite long indeed, and then it finishes here. "
            "Second sentence definitely contains a comma, yet stays whole."
        )
        backend.speak_stream(iter([text]))

        flushed = [c.args[0] for c in backend.pipeline.call_args_list]
        self.assertEqual(flushed[0], "The opening clause is quite long indeed,")
        self.assertEqual(
            flushed[-1],
            " Second sentence definitely contains a comma, yet stays whole.",
        )

    @patch("core.voice_backends.sd")
    def test_first_audio_log_emitted(self, mock_sd):
        backend = self._make_backend()
        with self.assertLogs("core.voice_backends", level="INFO") as captured:
            backend.speak_stream(iter(["Hello.", " World."]))

        msgs = [r.getMessage() for r in captured.records]
        self.assertTrue(
            any("Kokoro TTS stream" in m for m in msgs),
            f"expected a stream timing log, got: {msgs}",
        )


class KokoroONNXBackendTests(unittest.TestCase):
    """KokoroONNXBackend mirrors KokoroTTSBackend's interface, just with a
    different synth library. Smoke-test the override and the missing-asset
    error path; full streaming behavior is covered by SpeakStreamTests
    above (the parallel pipeline lives in the parent class)."""

    @patch("core.voice_backends.Path.exists", return_value=False)
    def test_init_raises_when_model_missing(self, _mock_exists):
        from core.voice_backends import KokoroONNXBackend

        with self.assertRaises(FileNotFoundError) as ctx:
            KokoroONNXBackend()
        self.assertIn("kokoro onnx assets missing", str(ctx.exception).lower())

    @patch("core.voice_backends.Path.exists", return_value=True)
    def test_synth_audio_yields_one_chunk_from_kokoro_create(self, _mock_exists):
        """_synth_audio must wrap kokoro.create() output as a one-element generator."""
        import numpy as np
        from core import voice_backends

        fake_impl = MagicMock()
        with patch.object(voice_backends, "_KokoroONNXImpl", fake_impl), \
             patch.object(voice_backends, "_KOKORO_ONNX_AVAILABLE", True), \
             patch("onnxruntime.InferenceSession") as mock_session_cls:
            mock_session_cls.return_value = MagicMock()
            backend = voice_backends.KokoroONNXBackend(voice="af_heart")
            backend.kokoro = MagicMock()
            backend.kokoro.create.return_value = (np.zeros(1024, dtype=np.float32), 24000)

            chunks = list(backend._synth_audio("hello"))

        self.assertEqual(len(chunks), 1)
        backend.kokoro.create.assert_called_once_with(
            "hello", voice="af_heart", speed=1.0, lang="en-us"
        )

    @patch("core.voice_backends.Path.exists", return_value=True)
    def test_init_uses_all_cpus_for_intra_op_threads_by_default(self, _mock_exists):
        from core import voice_backends

        fake_impl = MagicMock()
        with patch.object(voice_backends, "_KokoroONNXImpl", fake_impl), \
             patch.object(voice_backends, "_KOKORO_ONNX_AVAILABLE", True), \
             patch("onnxruntime.InferenceSession") as mock_session_cls, \
             patch("onnxruntime.SessionOptions") as mock_opts_cls, \
             patch.object(voice_backends.os, "cpu_count", return_value=4):
            opts = MagicMock()
            mock_opts_cls.return_value = opts
            mock_session_cls.return_value = MagicMock()
            backend = voice_backends.KokoroONNXBackend(voice="af_heart")

        self.assertEqual(backend.intra_op_threads, 4)
        self.assertEqual(opts.intra_op_num_threads, 4)
        self.assertEqual(opts.inter_op_num_threads, 1)

    @patch("core.voice_backends.Path.exists", return_value=True)
    def test_init_explicit_thread_count_overrides_default(self, _mock_exists):
        from core import voice_backends

        fake_impl = MagicMock()
        with patch.object(voice_backends, "_KokoroONNXImpl", fake_impl), \
             patch.object(voice_backends, "_KOKORO_ONNX_AVAILABLE", True), \
             patch("onnxruntime.InferenceSession") as mock_session_cls, \
             patch("onnxruntime.SessionOptions") as mock_opts_cls:
            opts = MagicMock()
            mock_opts_cls.return_value = opts
            mock_session_cls.return_value = MagicMock()
            backend = voice_backends.KokoroONNXBackend(voice="af_heart", intra_op_threads=2)

        self.assertEqual(backend.intra_op_threads, 2)
        self.assertEqual(opts.intra_op_num_threads, 2)

    @patch("core.voice_backends._KOKORO_ONNX_AVAILABLE", False)
    def test_init_raises_import_error_when_kokoro_onnx_uninstalled(self):
        from core.voice_backends import KokoroONNXBackend

        with self.assertRaises(ImportError):
            KokoroONNXBackend()


if __name__ == "__main__":
    unittest.main()
