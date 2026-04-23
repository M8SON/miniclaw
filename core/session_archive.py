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

        if os.environ.get("SESSION_ARCHIVE_ENABLED", "true").strip().lower() == "false":
            logger.info("SessionArchive disabled by SESSION_ARCHIVE_ENABLED=false")
            return

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

    def _now_iso(self) -> str:
        from datetime import datetime
        return datetime.now().isoformat(timespec="seconds")

    def start_session(self, mode: str) -> int:
        if not self._available:
            return 0
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                cur = conn.execute(
                    "INSERT INTO sessions (started_at, mode) VALUES (?, ?)",
                    (self._now_iso(), mode),
                )
                conn.commit()
                return int(cur.lastrowid)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.warning("start_session failed: %s", exc)
            return 0

    def end_session(self, session_id: int) -> None:
        if not self._available or not session_id:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    "UPDATE sessions SET ended_at = ? WHERE id = ?",
                    (self._now_iso(), session_id),
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.warning("end_session failed: %s", exc)

    def append_turn(
        self,
        session_id: int,
        role: str,
        content: str,
        tool_name: str | None = None,
    ) -> None:
        if not self._available or not session_id:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                turn_index = conn.execute(
                    "SELECT COALESCE(MAX(turn_index), -1) + 1 FROM turns WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO turns (session_id, turn_index, ts, role, content, tool_name) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (session_id, turn_index, self._now_iso(), role, content, tool_name),
                )
                conn.execute(
                    "UPDATE sessions SET turn_count = turn_count + 1 WHERE id = ?",
                    (session_id,),
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.warning("append_turn failed: %s", exc)

    def search(
        self,
        query: str,
        since: str | None = None,
        limit: int = 5,
        oversample: int | None = None,
    ) -> list[dict]:
        """
        Full-text search past turns. Returns ranked structured dicts.

        oversample is accepted for forward-compat with a future chromadb
        rerank layer but is inert in v1 (no reranker → return `limit` hits).
        """
        query = (query or "").strip()
        if not query or not self._available:
            return []
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                if since:
                    rows = conn.execute(
                        """
                        SELECT t.id, t.session_id, t.ts, t.role, t.tool_name,
                               t.content, t.turn_index, turns_fts.rank
                        FROM turns_fts
                        JOIN turns t ON t.id = turns_fts.rowid
                        WHERE turns_fts MATCH ? AND t.ts >= ?
                        ORDER BY turns_fts.rank
                        LIMIT ?
                        """,
                        (query, since, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT t.id, t.session_id, t.ts, t.role, t.tool_name,
                               t.content, t.turn_index, turns_fts.rank
                        FROM turns_fts
                        JOIN turns t ON t.id = turns_fts.rowid
                        WHERE turns_fts MATCH ?
                        ORDER BY turns_fts.rank
                        LIMIT ?
                        """,
                        (query, limit),
                    ).fetchall()

                hits = []
                for row in rows:
                    context = self._fetch_context(conn, row[1], row[6])
                    hits.append({
                        "turn_id": row[0],
                        "session_id": row[1],
                        "ts": row[2],
                        "role": row[3],
                        "tool_name": row[4],
                        "content": row[5],
                        "context": context,
                        "fts_rank": row[7],
                    })
                return hits
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.warning("search failed: %s", exc)
            return []

    def _fetch_context(
        self, conn: sqlite3.Connection, session_id: int, turn_index: int
    ) -> list[dict]:
        """Fetch turns at turn_index ± 1 within the same session."""
        rows = conn.execute(
            """
            SELECT ts, role, tool_name, content, turn_index
            FROM turns
            WHERE session_id = ? AND turn_index IN (?, ?)
            ORDER BY turn_index
            """,
            (session_id, turn_index - 1, turn_index + 1),
        ).fetchall()
        return [
            {
                "ts": r[0],
                "role": r[1],
                "tool_name": r[2],
                "content": r[3],
                "turn_index": r[4],
            }
            for r in rows
        ]
