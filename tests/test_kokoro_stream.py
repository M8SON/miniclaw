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
    def test_first_audio_log_emitted(self, mock_sd):
        backend = self._make_backend()
        with self.assertLogs("core.voice_backends", level="INFO") as captured:
            backend.speak_stream(iter(["Hello.", " World."]))

        msgs = [r.getMessage() for r in captured.records]
        self.assertTrue(
            any("Kokoro TTS stream" in m for m in msgs),
            f"expected a stream timing log, got: {msgs}",
        )

    @patch("core.voice_backends.sd")
    def test_interrupt_before_playback_writes_nothing(self, mock_sd):
        import threading
        import numpy as np
        backend = self._make_backend()
        backend.pipeline.side_effect = lambda *a, **k: iter(
            [("", "", np.zeros(4096, dtype=np.float32))]
        )
        stream = mock_sd.OutputStream.return_value.__enter__.return_value
        ev = threading.Event()
        ev.set()  # already interrupted before any audio is written
        backend.speak_stream(iter(["Hello.", " World."]), interrupt_event=ev)
        stream.write.assert_not_called()

    @patch("core.voice_backends.sd")
    def test_no_deadlock_when_interrupted_midstream(self, mock_sd):
        """Interrupt after playback starts with far more audio queued than
        audio_q's maxsize (8). All threads must wind down and speak_stream
        must return — the highest-risk piece."""
        import threading
        import numpy as np
        backend = self._make_backend()
        backend.pipeline.side_effect = lambda *a, **k: iter(
            [("", "", np.zeros(2048, dtype=np.float32))]
        )
        stream = mock_sd.OutputStream.return_value.__enter__.return_value
        ev = threading.Event()
        writes = [0]

        def fake_write(_buf):
            writes[0] += 1
            if writes[0] == 1:
                ev.set()  # interrupt right after the first sub-block

        stream.write.side_effect = fake_write
        sentences = [f"Sentence number {i}." for i in range(20)]
        done = threading.Event()

        def run():
            backend.speak_stream(iter(sentences), interrupt_event=ev)
            done.set()

        threading.Thread(target=run, daemon=True).start()
        self.assertTrue(done.wait(timeout=10), "speak_stream deadlocked after interrupt")

    @patch("core.voice_backends.sd")
    def test_none_interrupt_writes_audio(self, mock_sd):
        import numpy as np
        backend = self._make_backend()
        backend.pipeline.side_effect = lambda *a, **k: iter(
            [("", "", np.zeros(2048, dtype=np.float32))]
        )
        stream = mock_sd.OutputStream.return_value.__enter__.return_value
        backend.speak_stream(iter(["Hello world."]), interrupt_event=None)
        self.assertTrue(stream.write.called)


class SpeakInterruptTests(unittest.TestCase):
    def _make_backend(self):
        from core import voice_backends
        with patch.object(voice_backends, "KPipeline"):
            backend = voice_backends.KokoroTTSBackend()
        backend.pipeline = MagicMock()
        return backend

    @patch("core.voice_backends.sd")
    def test_speak_stops_when_interrupted(self, mock_sd):
        import threading
        import numpy as np
        backend = self._make_backend()
        backend.pipeline.side_effect = lambda *a, **k: iter(
            [("", "", np.zeros(8192, dtype=np.float32))]
        )
        stream = mock_sd.OutputStream.return_value.__enter__.return_value
        ev = threading.Event()
        ev.set()
        backend.speak("Hello there, general.", interrupt_event=ev)
        stream.write.assert_not_called()

    @patch("core.voice_backends.sd")
    def test_speak_none_writes_audio(self, mock_sd):
        import numpy as np
        backend = self._make_backend()
        backend.pipeline.side_effect = lambda *a, **k: iter(
            [("", "", np.zeros(2048, dtype=np.float32))]
        )
        stream = mock_sd.OutputStream.return_value.__enter__.return_value
        backend.speak("Hello.", interrupt_event=None)
        self.assertTrue(stream.write.called)


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
