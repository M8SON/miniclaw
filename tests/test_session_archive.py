import sqlite3
from pathlib import Path

import pytest

from core.session_archive import SessionArchive


@pytest.fixture
def archive(tmp_path: Path) -> SessionArchive:
    return SessionArchive(db_path=tmp_path / "sessions.db")


def test_init_creates_schema(archive: SessionArchive) -> None:
    conn = sqlite3.connect(archive.db_path)
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            )
        }
    finally:
        conn.close()
    assert {"sessions", "turns", "turns_fts", "idx_turns_session", "idx_turns_ts"} <= names


def test_init_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "sessions.db"
    SessionArchive(db_path=db)
    SessionArchive(db_path=db)  # second init must not raise


def test_start_session_returns_id(archive: SessionArchive) -> None:
    sid = archive.start_session("voice")
    assert isinstance(sid, int)
    assert sid > 0


def test_start_session_records_mode_and_started_at(archive: SessionArchive) -> None:
    sid = archive.start_session("text")
    conn = sqlite3.connect(archive.db_path)
    try:
        row = conn.execute(
            "SELECT mode, started_at, ended_at, turn_count FROM sessions WHERE id = ?",
            (sid,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "text"
    assert row[1]                    # started_at populated
    assert row[2] is None            # ended_at NULL until end_session
    assert row[3] == 0


def test_end_session_finalizes_ended_at(archive: SessionArchive) -> None:
    sid = archive.start_session("voice")
    archive.end_session(sid)
    conn = sqlite3.connect(archive.db_path)
    try:
        row = conn.execute(
            "SELECT ended_at FROM sessions WHERE id = ?", (sid,)
        ).fetchone()
    finally:
        conn.close()
    assert row[0]                    # ended_at now populated


def test_end_session_unknown_id_is_noop(archive: SessionArchive) -> None:
    archive.end_session(99999)       # must not raise


def test_append_turn_writes_row(archive: SessionArchive) -> None:
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "hello world")
    conn = sqlite3.connect(archive.db_path)
    try:
        row = conn.execute(
            "SELECT role, content, tool_name, turn_index FROM turns WHERE session_id = ?",
            (sid,),
        ).fetchone()
    finally:
        conn.close()
    assert row == ("user", "hello world", None, 0)


def test_append_turn_increments_session_turn_count(archive: SessionArchive) -> None:
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "first")
    archive.append_turn(sid, "assistant", "reply")
    conn = sqlite3.connect(archive.db_path)
    try:
        count = conn.execute(
            "SELECT turn_count FROM sessions WHERE id = ?", (sid,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 2


def test_append_turn_assigns_sequential_indexes(archive: SessionArchive) -> None:
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "a")
    archive.append_turn(sid, "assistant", "b")
    archive.append_turn(sid, "tool", "c", tool_name="weather")
    conn = sqlite3.connect(archive.db_path)
    try:
        rows = conn.execute(
            "SELECT turn_index, role, tool_name FROM turns WHERE session_id = ? ORDER BY turn_index",
            (sid,),
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(0, "user", None), (1, "assistant", None), (2, "tool", "weather")]


def test_append_turn_populates_fts_index(archive: SessionArchive) -> None:
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "the rain in spain")
    conn = sqlite3.connect(archive.db_path)
    try:
        hits = conn.execute(
            "SELECT content FROM turns_fts WHERE turns_fts MATCH ?", ("rain",)
        ).fetchall()
    finally:
        conn.close()
    assert len(hits) == 1
    assert "rain" in hits[0][0]


def test_append_turn_with_no_session_is_noop(archive: SessionArchive) -> None:
    archive.append_turn(0, "user", "orphan")  # session_id=0 means no session
    conn = sqlite3.connect(archive.db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    finally:
        conn.close()
    assert count == 0
