"""Tests for the Kokoro pre-buffer (KOKORO_PREBUFFER_MS)."""

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import voice_backends


def _make_backend():
    with patch.object(voice_backends, "KPipeline"):
        backend = voice_backends.KokoroTTSBackend()
    # Each flush yields one (phonemes, tokens, audio) tuple of 2048 samples.
    backend.pipeline = MagicMock()
    backend.pipeline.side_effect = lambda *a, **k: iter(
        [("", "", np.zeros(2048, dtype=np.float32))]
    )
    return backend


def _written_samples(stream):
    return sum(len(c.args[0]) for c in stream.write.call_args_list)


@patch("core.voice_backends.sd")
def test_env_wires_prebuffer_ms(mock_sd, monkeypatch):
    monkeypatch.setenv("KOKORO_PREBUFFER_MS", "800")
    with patch.object(voice_backends, "KPipeline"):
        b = voice_backends.KokoroTTSBackend()
    assert b.PREBUFFER_MS == 800


@patch("core.voice_backends.sd")
def test_prebuffer_default(mock_sd, monkeypatch):
    monkeypatch.delenv("KOKORO_PREBUFFER_MS", raising=False)
    with patch.object(voice_backends, "KPipeline"):
        b = voice_backends.KokoroTTSBackend()
    assert b.PREBUFFER_MS == 1500


@patch("core.voice_backends.sd")
def test_prebuffer_floor_negative(mock_sd, monkeypatch):
    monkeypatch.setenv("KOKORO_PREBUFFER_MS", "-100")
    with patch.object(voice_backends, "KPipeline"):
        b = voice_backends.KokoroTTSBackend()
    assert b.PREBUFFER_MS == 0


@patch("core.voice_backends.sd")
def test_short_response_shorter_than_buffer_still_plays_all(mock_sd, monkeypatch):
    """Whole response < buffer target: SENTINEL-during-priming path must
    flush everything (no audio lost)."""
    monkeypatch.setenv("KOKORO_PREBUFFER_MS", "5000")  # 120000 samples @ 24k
    backend = _make_backend()
    stream = mock_sd.OutputStream.return_value.__enter__.return_value
    backend.speak_stream(iter(["One.", " Two.", " Three."]))
    assert _written_samples(stream) == 3 * 2048


@patch("core.voice_backends.sd")
def test_buffer_target_reached_midstream_plays_all(mock_sd, monkeypatch):
    """Target reached after the first chunk: prime flush + normal loop must
    together write everything."""
    monkeypatch.setenv("KOKORO_PREBUFFER_MS", "50")  # 1200 samples < one 2048 chunk
    backend = _make_backend()
    stream = mock_sd.OutputStream.return_value.__enter__.return_value
    backend.speak_stream(iter(["One.", " Two.", " Three."]))
    assert _written_samples(stream) == 3 * 2048


@patch("core.voice_backends.sd")
def test_prebuffer_zero_writes_all(mock_sd, monkeypatch):
    monkeypatch.setenv("KOKORO_PREBUFFER_MS", "0")
    backend = _make_backend()
    stream = mock_sd.OutputStream.return_value.__enter__.return_value
    backend.speak_stream(iter(["One.", " Two."]))
    assert _written_samples(stream) == 2 * 2048


@patch("core.voice_backends.sd")
def test_bargein_during_priming_returns_promptly(mock_sd, monkeypatch):
    """Barge-in that fires strictly during priming (before the buffer target
    is reached, while a second already-synthesised chunk is still sitting in
    the audio queue) must not block speak_stream for the full teardown
    timeout. The writer never touches `stream.write` in this path, so it
    should signal done-writing the instant it notices the interrupt instead
    of waiting for the in-flight synth call (simulated here as slow, like
    real Kokoro synthesis) to finish producing everything up to SENTINEL.

    Real thread scheduling can't reliably land the interrupt on the
    priming loop's non-SENTINEL `_interrupted()` branch (vs. the
    already-correct SENTINEL branch), so a small instrumented queue.Queue
    stand-in pins down the exact interleaving instead of racing it.
    """
    import queue as queue_module

    monkeypatch.setenv("KOKORO_PREBUFFER_MS", "5000")  # large target; never reached
    backend = _make_backend()
    stream = mock_sd.OutputStream.return_value.__enter__.return_value

    calls = [0]

    def synth(*a, **k):
        calls[0] += 1
        if calls[0] == 3:
            time.sleep(1.5)  # simulate a slow in-flight synth call
        return iter([("", "", np.zeros(2048, dtype=np.float32))])

    backend.pipeline.side_effect = synth

    RealQueue = queue_module.Queue

    class ControlledAudioQueue(RealQueue):
        """Stalls the writer's *second* dequeue until the test flips the
        interrupt flag, guaranteeing the priming loop observes a real
        (non-SENTINEL) chunk with _interrupted() already True."""

        def __init__(self, maxsize=0):
            super().__init__(maxsize)
            self.put_count = 0
            self.get_count = 0
            self.two_puts_done = threading.Event()
            self.release_second_get = threading.Event()

        def put(self, item, *a, **kw):
            super().put(item, *a, **kw)
            self.put_count += 1
            if self.put_count == 2:
                self.two_puts_done.set()

        def get(self, *a, **kw):
            self.get_count += 1
            if self.get_count == 2:
                self.release_second_get.wait(timeout=5)
            return super().get(*a, **kw)

    captured = {}

    def queue_factory(maxsize=0):
        if maxsize == 8:
            q = ControlledAudioQueue(maxsize)
            captured["audio_q"] = q
            return q
        return RealQueue(maxsize)

    monkeypatch.setattr(queue_module, "Queue", queue_factory)

    ev = threading.Event()
    sentences = [f"Sentence {i}." for i in range(5)]
    done = threading.Event()
    t0 = time.perf_counter()

    def run():
        backend.speak_stream(iter(sentences), interrupt_event=ev)
        done.set()

    threading.Thread(target=run, daemon=True).start()

    audio_q = None
    for _ in range(200):
        audio_q = captured.get("audio_q")
        if audio_q is not None:
            break
        time.sleep(0.005)
    assert audio_q is not None, "audio_q was never constructed"
    assert audio_q.two_puts_done.wait(timeout=5), "synth never enqueued 2 chunks"

    ev.set()  # barge-in: chunk #2 is enqueued but the writer hasn't read it yet
    audio_q.release_second_get.set()

    assert done.wait(timeout=5), "speak_stream did not return after priming barge-in"
    elapsed = time.perf_counter() - t0
    stream.write.assert_not_called()  # priming discards on interrupt
    assert elapsed < 0.9, f"teardown blocked the full timeout ({elapsed:.2f}s)"
