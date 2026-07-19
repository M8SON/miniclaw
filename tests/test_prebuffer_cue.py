"""Tests for the looping R2-D2 pre-buffer cue."""

import sys
import time
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
    v._prebuffer_cue = None
    return v


def test_segment_is_nonempty_float32():
    v = _make_voice()
    seg = v._prebuffer_cue_segment()
    assert isinstance(seg, np.ndarray)
    assert seg.dtype == np.float32
    assert len(seg) > int(0.3 * v._output_samplerate)  # ~0.44s segment


def test_start_then_stop_lifecycle():
    v = _make_voice()
    with patch.object(voice_mod, "sd"):
        v.start_prebuffer_cue()
        assert v._prebuffer_cue is not None
        _, _, thread = v._prebuffer_cue
        v.stop_prebuffer_cue()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert v._prebuffer_cue is None


def test_stop_without_start_is_noop():
    v = _make_voice()
    with patch.object(voice_mod, "sd"):
        v.stop_prebuffer_cue()  # must not raise
        assert v._prebuffer_cue is None


def test_double_start_does_not_spawn_two():
    v = _make_voice()
    with patch.object(voice_mod, "sd"):
        v.start_prebuffer_cue()
        first = v._prebuffer_cue
        v.start_prebuffer_cue()  # idempotent
        assert v._prebuffer_cue is first
        v.stop_prebuffer_cue()


def test_noop_when_tts_disabled():
    v = _make_voice()
    v.enable_tts = False
    with patch.object(voice_mod, "sd"):
        v.start_prebuffer_cue()
        assert v._prebuffer_cue is None
