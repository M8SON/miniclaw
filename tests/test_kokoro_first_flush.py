"""Tests for the tunable Kokoro first-flush threshold (KOKORO_MIN_FIRST_FLUSH)."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import voice_backends


# --- the env-read helper (pure, no model load) ---

def test_helper_default_when_unset(monkeypatch):
    monkeypatch.delenv("KOKORO_MIN_FIRST_FLUSH", raising=False)
    assert voice_backends._configured_min_first_flush(20) == 20


def test_helper_env_overrides(monkeypatch):
    monkeypatch.setenv("KOKORO_MIN_FIRST_FLUSH", "15")
    assert voice_backends._configured_min_first_flush(20) == 15


def test_helper_floors_zero(monkeypatch):
    monkeypatch.setenv("KOKORO_MIN_FIRST_FLUSH", "0")
    assert voice_backends._configured_min_first_flush(20) == 1


def test_helper_floors_negative(monkeypatch):
    monkeypatch.setenv("KOKORO_MIN_FIRST_FLUSH", "-5")
    assert voice_backends._configured_min_first_flush(20) == 1


def test_helper_non_numeric_falls_back(monkeypatch):
    monkeypatch.setenv("KOKORO_MIN_FIRST_FLUSH", "abc")
    assert voice_backends._configured_min_first_flush(20) == 20


# --- boundary actually moves with the threshold (pure, via __new__) ---

def _backend_with_min(min_val):
    b = voice_backends.KokoroTTSBackend.__new__(voice_backends.KokoroTTSBackend)
    b.MIN_FIRST_FLUSH = min_val
    return b


def test_lower_threshold_breaks_at_earlier_clause():
    buf = "That is a great question, and here is why."
    b20 = _backend_with_min(20)
    b30 = _backend_with_min(30)
    i20 = b20._find_flush_boundary(buf, allow_clause=True)
    i30 = b30._find_flush_boundary(buf, allow_clause=True)
    # 20 breaks at the comma (index 24); 30 ignores it (< 29) and defers later.
    assert buf[: i20 + 1] == "That is a great question,"
    assert i30 > i20


# --- __init__ reads the env (KPipeline patched so no model load) ---

def test_init_reads_env(monkeypatch):
    monkeypatch.setenv("KOKORO_MIN_FIRST_FLUSH", "15")
    with patch.object(voice_backends, "KPipeline"):
        b = voice_backends.KokoroTTSBackend()
    assert b.MIN_FIRST_FLUSH == 15


def test_init_default_is_20(monkeypatch):
    monkeypatch.delenv("KOKORO_MIN_FIRST_FLUSH", raising=False)
    with patch.object(voice_backends, "KPipeline"):
        b = voice_backends.KokoroTTSBackend()
    assert b.MIN_FIRST_FLUSH == 20
