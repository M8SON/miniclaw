"""
Session archive for MiniClaw.

Persists every conversation turn to a local sqlite + FTS5 store so Claude
can recall prior sessions by content via the `recall_session` skill.
"""

import logging
import os
import sqlite3
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    mode        TEXT NOT NULL,
    turn_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES sessions(id),
    turn_index  INTEGER NOT NULL,
    ts          TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    tool_name   TEXT
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, turn_index);
CREATE INDEX IF NOT EXISTS idx_turns_ts      ON turns(ts);

CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
    content,
    content='turns', content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TRIGGER IF NOT EXISTS turns_ad AFTER DELETE ON turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, content)
    VALUES('delete', old.id, old.content);
END;
"""


class SessionArchive:
    """sqlite + FTS5 store for past conversation turns."""

    def __init__(
        self,
        db_path: Path | None = None,
        reranker: Callable | None = None,
    ):
        self.db_path = Path(
            db_path
            or os.environ.get(
                "SESSION_ARCHIVE_PATH",
                Path.home() / ".miniclaw" / "sessions.db",
            )
        )
        self._reranker = reranker
        self._available = False
        self._init_schema()

    def _init_schema(self) -> None:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path)
            try:
                conn.executescript(_SCHEMA)
                conn.commit()
            finally:
                conn.close()
            self._available = True
        except (sqlite3.Error, OSError) as exc:
            logger.warning("SessionArchive disabled: %s", exc)
            self._available = False
