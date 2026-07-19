"""speak_stream_feeder forwards on_first_audio to the backend."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.voice import VoiceInterface


def _voice_with_backend(backend):
    v = VoiceInterface.__new__(VoiceInterface)
    v.enable_tts = True
    v.tts_backend = backend
    v.barge_in_enabled = False
    return v


def test_feeder_forwards_on_first_audio():
    backend = MagicMock()
    backend.speak_stream = MagicMock()
    v = _voice_with_backend(backend)
    marker = lambda: None
    push, finalize = v.speak_stream_feeder(on_first_audio=marker)
    push("hello")       # spawns the consumer thread
    finalize()
    kwargs = backend.speak_stream.call_args.kwargs
    assert kwargs.get("on_first_audio") is marker
