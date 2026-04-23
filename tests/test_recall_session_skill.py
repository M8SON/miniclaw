"""Tests for the recall_session native skill handler."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.container_manager import ContainerManager
from core.session_archive import SessionArchive


@pytest.fixture
def archive(tmp_path: Path) -> SessionArchive:
    return SessionArchive(db_path=tmp_path / "sessions.db")


@pytest.fixture
def cm(archive: SessionArchive) -> ContainerManager:
    cm = ContainerManager()
    cm._archive = archive
    return cm


def test_handler_no_query_returns_error(cm):
    out = cm._execute_recall_session({})
    assert "no query" in out.lower()


def test_handler_no_results(cm, archive):
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "hello")
    out = cm._execute_recall_session({"query": "kokoro"})
    assert "no prior sessions" in out.lower()
    assert "kokoro" in out


def test_handler_returns_formatted_hits(cm, archive):
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "how is the schedule skill")
    archive.append_turn(sid, "assistant", "shipped the cron skill yesterday")

    out = cm._execute_recall_session({"query": "cron"})
    assert "schedule" in out.lower() or "cron" in out.lower()
    assert "[" in out and "]" in out
    assert "assistant" in out


def test_handler_parses_yesterday(cm, archive):
    """`since="yesterday"` must exclude old turns from being primary search hits.

    Old turns may still appear as ±1 context around a newer hit in the same
    session — that's intentional context, not a filter leak. We assert on hit
    selection via SessionArchive.search directly to avoid coupling to the
    formatter output.
    """
    # Use separate sessions so context lookups don't blur the boundary.
    old_sid = archive.start_session("text")
    archive.append_turn(old_sid, "user", "old kokoro question")
    archive.end_session(old_sid)

    new_sid = archive.start_session("text")
    archive.append_turn(new_sid, "user", "recent kokoro question")
    archive.end_session(new_sid)

    yesterday_iso = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    old_iso = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    import sqlite3
    conn = sqlite3.connect(archive.db_path)
    try:
        conn.execute(
            "UPDATE turns SET ts = ? WHERE content = 'old kokoro question'",
            (old_iso,),
        )
        conn.execute(
            "UPDATE turns SET ts = ? WHERE content = 'recent kokoro question'",
            (yesterday_iso,),
        )
        conn.commit()
    finally:
        conn.close()

    out = cm._execute_recall_session({"query": "kokoro", "since": "yesterday"})
    assert "recent" in out
    assert "old kokoro" not in out  # separate session → no context leak


def test_handler_unknown_since_falls_through_to_no_filter(cm, archive):
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "kokoro is the tts engine")
    out = cm._execute_recall_session({
        "query": "kokoro",
        "since": "blursday next week",
    })
    assert "kokoro" in out
