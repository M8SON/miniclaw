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
