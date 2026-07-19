"""Tests for the Kokoro pre-buffer (KOKORO_PREBUFFER_MS)."""

import sys
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
