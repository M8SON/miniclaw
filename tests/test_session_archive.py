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
