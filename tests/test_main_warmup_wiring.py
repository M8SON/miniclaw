"""Tests for the VOICE_WARMUP gate helper in main."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import main


def test_spawn_warmup_runs_fn_when_enabled(monkeypatch):
    monkeypatch.setenv("VOICE_WARMUP", "true")
    called = MagicMock()
    t = main._spawn_warmup(called)
    assert t is not None
    t.join(timeout=2)
    called.assert_called_once()


def test_spawn_warmup_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("VOICE_WARMUP", "false")
    called = MagicMock()
    t = main._spawn_warmup(called)
    assert t is None
    called.assert_not_called()


def test_spawn_warmup_default_is_enabled(monkeypatch):
    monkeypatch.delenv("VOICE_WARMUP", raising=False)
    called = MagicMock()
    t = main._spawn_warmup(called)
    assert t is not None
    t.join(timeout=2)
    called.assert_called_once()
