"""Tests for VoiceInterface.play_prebuffer_cue — the longer cue that covers
the TTS pre-buffer window."""

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import voice as voice_mod
from core.voice import VoiceInterface
from core.voice_backends import KOKORO_SAMPLE_RATE


def _make_voice():
    v = VoiceInterface.__new__(VoiceInterface)
    v.enable_tts = True
    v._output_samplerate = KOKORO_SAMPLE_RATE
    v._output_device_index = None
    return v


def test_cue_is_long_and_nonblocking():
    v = _make_voice()
    with patch.object(voice_mod, "sd") as mock_sd:
        v.play_prebuffer_cue()
        mock_sd.play.assert_called_once()
        mock_sd.wait.assert_not_called()  # non-blocking
        arr = mock_sd.play.call_args.args[0]
        # Cue must fill roughly the pre-buffer window — at least ~1s of audio.
        assert len(arr) >= int(1.0 * KOKORO_SAMPLE_RATE)


def test_cue_noop_when_tts_disabled():
    v = _make_voice()
    v.enable_tts = False
    with patch.object(voice_mod, "sd") as mock_sd:
        v.play_prebuffer_cue()
        mock_sd.play.assert_not_called()


def test_cue_swallows_errors():
    v = _make_voice()
    with patch.object(voice_mod, "sd") as mock_sd:
        mock_sd.play.side_effect = RuntimeError("no speaker")
        v.play_prebuffer_cue()  # must not raise


def test_cue_length_env_tunable(monkeypatch):
    """KOKORO_CUE_MS must scale the produced cue length in the expected
    direction: a larger value yields more samples than default, a smaller
    (but still floor-guarded) value yields fewer."""
    v = _make_voice()

    monkeypatch.delenv("KOKORO_CUE_MS", raising=False)
    with patch.object(voice_mod, "sd") as mock_sd:
        v.play_prebuffer_cue()
        default_len = len(mock_sd.play.call_args.args[0])

    monkeypatch.setenv("KOKORO_CUE_MS", "2200")
    with patch.object(voice_mod, "sd") as mock_sd:
        v.play_prebuffer_cue()
        longer_len = len(mock_sd.play.call_args.args[0])

    monkeypatch.setenv("KOKORO_CUE_MS", "550")
    with patch.object(voice_mod, "sd") as mock_sd:
        v.play_prebuffer_cue()
        shorter_len = len(mock_sd.play.call_args.args[0])

    assert longer_len > default_len > shorter_len
