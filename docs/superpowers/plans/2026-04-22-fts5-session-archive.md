# FTS5 Session Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every conversation turn (voice + text mode) to a local sqlite + FTS5 store and expose a tool-invoked `recall_session` skill so Claude can search prior sessions by content.

**Architecture:** New `core/session_archive.py` owns a sqlite db at `~/.miniclaw/sessions.db`. The orchestrator starts a session at the beginning of each conversation arc, hands `tool_loop` an `archive_callback` that fires after each completed turn, and ends the session when the arc closes. A new native skill `recall_session` calls `archive.search()` with FTS5 BM25 ranking and returns dated snippets with ±1 turn of context. Forward-compatible with a future chromadb rerank layer (search returns structured dicts; `oversample` and `reranker` hooks are wired in v1 but inert).

**Tech Stack:** Python stdlib `sqlite3` (FTS5 built-in), no new dependencies. Hooks into existing `core/orchestrator.py`, `core/tool_loop.py`, `core/container_manager.py`, `main.py`.

**Spec:** `docs/superpowers/specs/2026-04-22-fts5-session-archive-design.md`

---

## File Structure

**New files:**
- `core/session_archive.py` — sqlite + FTS5 store, write/read API, ~200 lines
- `skills/recall_session/SKILL.md` — routing instructions for Claude
- `skills/recall_session/config.yaml` — `type: native`
- `tests/test_session_archive.py` — unit tests for SessionArchive
- `tests/test_recall_session_skill.py` — integration tests for the native handler
- `tests/test_orchestrator_archive.py` — orchestrator lifecycle integration tests

**Modified files:**
- `core/orchestrator.py` — add `start_session` / `end_session` / `_archive_callback`, wire archive into `__init__`, hook into `process_message` / `close_session` / `reset_conversation`
- `core/tool_loop.py` — accept optional `archive_callback`, build tool activity list during the loop, fire callback before prune
- `core/container_manager.py` — register `recall_session` native handler, accept injected `_archive` reference
- `main.py` — instantiate `SessionArchive`, inject into orchestrator and container_manager, call `start_session("voice")` / `start_session("text")` and `end_session()` at conversation arc boundaries

---

## Task 1: SessionArchive — schema + lifecycle

**Files:**
- Create: `core/session_archive.py`
- Test: `tests/test_session_archive.py`

- [ ] **Step 1: Write the failing test for schema creation**

```python
# tests/test_session_archive.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_session_archive.py -v`
Expected: FAIL with `ImportError: cannot import name 'SessionArchive'`

- [ ] **Step 3: Implement minimal SessionArchive — schema only**

Create `core/session_archive.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_session_archive.py -v`
Expected: PASS for both tests

- [ ] **Step 5: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add core/session_archive.py tests/test_session_archive.py && git commit -m "feat: add SessionArchive schema initialization"
```

---

## Task 2: SessionArchive — start_session and end_session

**Files:**
- Modify: `core/session_archive.py`
- Modify: `tests/test_session_archive.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session_archive.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_session_archive.py -v`
Expected: FAIL with `AttributeError: 'SessionArchive' object has no attribute 'start_session'`

- [ ] **Step 3: Implement start_session and end_session**

Add to `core/session_archive.py` inside the `SessionArchive` class:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_session_archive.py -v`
Expected: PASS for all 6 tests

- [ ] **Step 5: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add core/session_archive.py tests/test_session_archive.py && git commit -m "feat: add session start/end lifecycle to SessionArchive"
```

---

## Task 3: SessionArchive — append_turn

**Files:**
- Modify: `core/session_archive.py`
- Modify: `tests/test_session_archive.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session_archive.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_session_archive.py -v`
Expected: FAIL with `AttributeError: 'SessionArchive' object has no attribute 'append_turn'`

- [ ] **Step 3: Implement append_turn**

Add to `core/session_archive.py` inside the `SessionArchive` class:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_session_archive.py -v`
Expected: PASS for all 11 tests

- [ ] **Step 5: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add core/session_archive.py tests/test_session_archive.py && git commit -m "feat: add append_turn write path to SessionArchive"
```

---

## Task 4: SessionArchive — search basic (FTS5 BM25 ranking)

**Files:**
- Modify: `core/session_archive.py`
- Modify: `tests/test_session_archive.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session_archive.py`:

```python
def test_search_returns_structured_dicts(archive: SessionArchive) -> None:
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "tell me about kokoro voices")
    hits = archive.search("kokoro")
    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, dict)
    assert {
        "session_id", "turn_id", "ts", "role", "tool_name",
        "content", "context", "fts_rank",
    } <= set(hit.keys())
    assert hit["role"] == "user"
    assert "kokoro" in hit["content"]


def test_search_no_query_returns_empty(archive: SessionArchive) -> None:
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "anything")
    assert archive.search("") == []


def test_search_no_matches_returns_empty(archive: SessionArchive) -> None:
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "hello world")
    assert archive.search("nonexistentterm") == []


def test_search_respects_limit(archive: SessionArchive) -> None:
    sid = archive.start_session("text")
    for i in range(10):
        archive.append_turn(sid, "user", f"weather forecast for day {i}")
    hits = archive.search("weather", limit=3)
    assert len(hits) == 3


def test_search_porter_stemming(archive: SessionArchive) -> None:
    sid = archive.start_session("text")
    archive.append_turn(sid, "assistant", "we scheduled the meeting")
    hits = archive.search("scheduling")          # stem match
    assert len(hits) == 1


def test_search_oversample_param_accepted_but_inert_in_v1(archive: SessionArchive) -> None:
    sid = archive.start_session("text")
    for i in range(5):
        archive.append_turn(sid, "user", f"weather forecast {i}")
    hits = archive.search("weather", limit=2, oversample=20)
    assert len(hits) == 2                        # v1 still returns limit, not oversample
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_session_archive.py -v`
Expected: FAIL with `AttributeError: 'SessionArchive' object has no attribute 'search'`

- [ ] **Step 3: Implement search (without context turns or since filter yet)**

Add to `core/session_archive.py` inside the `SessionArchive` class:

```python
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
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.warning("search failed: %s", exc)
            return []

        return [
            {
                "turn_id": row[0],
                "session_id": row[1],
                "ts": row[2],
                "role": row[3],
                "tool_name": row[4],
                "content": row[5],
                "context": [],
                "fts_rank": row[7],
            }
            for row in rows
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_session_archive.py -v`
Expected: PASS for all 17 tests

- [ ] **Step 5: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add core/session_archive.py tests/test_session_archive.py && git commit -m "feat: add FTS5 search to SessionArchive"
```

---

## Task 5: SessionArchive — `since` date filter and ±1 context turns

**Files:**
- Modify: `core/session_archive.py`
- Modify: `tests/test_session_archive.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session_archive.py`:

```python
def test_search_since_filter_excludes_old(archive: SessionArchive) -> None:
    """Manually insert turns with controlled timestamps to test the since filter."""
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "old weather report")
    # Backdate the just-inserted turn
    conn = sqlite3.connect(archive.db_path)
    try:
        conn.execute(
            "UPDATE turns SET ts = '2026-01-01T00:00:00' WHERE session_id = ?",
            (sid,),
        )
        conn.commit()
    finally:
        conn.close()

    archive.append_turn(sid, "user", "recent weather report")
    hits = archive.search("weather", since="2026-04-01")
    assert len(hits) == 1
    assert "recent" in hits[0]["content"]


def test_search_includes_surrounding_context(archive: SessionArchive) -> None:
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "what's the schedule status")
    archive.append_turn(sid, "assistant", "shipped the cron skill yesterday")
    archive.append_turn(sid, "tool", "schedule(action=list) -> 3 active tasks", tool_name="schedule")
    archive.append_turn(sid, "assistant", "you have three active tasks")

    hits = archive.search("cron", limit=1)
    assert len(hits) == 1
    context = hits[0]["context"]
    # Context must include the immediately preceding and following turns
    context_contents = [c["content"] for c in context]
    assert "what's the schedule status" in context_contents
    assert "schedule(action=list) -> 3 active tasks" in context_contents


def test_search_context_at_session_boundary(archive: SessionArchive) -> None:
    """Hits at index 0 should still return without crashing — only forward context exists."""
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "kokoro question")
    archive.append_turn(sid, "assistant", "kokoro is the tts engine")
    hits = archive.search("kokoro question", limit=1)
    assert len(hits) >= 1
    # No exception, context list returned (may be empty or just forward turn)
    assert isinstance(hits[0]["context"], list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_session_archive.py -v`
Expected: FAIL — `since` filter not applied; `context` always empty

- [ ] **Step 3: Update search to add `since` filter + context lookup**

Replace the `search` method body in `core/session_archive.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_session_archive.py -v`
Expected: PASS for all 20 tests

- [ ] **Step 5: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add core/session_archive.py tests/test_session_archive.py && git commit -m "feat: add since filter and context turns to SessionArchive.search"
```

---

## Task 6: SessionArchive — failure tolerance and kill switch

**Files:**
- Modify: `core/session_archive.py`
- Modify: `tests/test_session_archive.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_session_archive.py`:

```python
def test_unwritable_path_disables_archive_without_raising(tmp_path: Path) -> None:
    unwritable = tmp_path / "no_such_dir" / "deeper" / "sessions.db"
    # Make the parent unwritable by creating a file where the dir should be
    blocker = tmp_path / "no_such_dir"
    blocker.write_text("not a directory")

    archive = SessionArchive(db_path=unwritable)
    assert archive._available is False
    # All ops are no-ops — no raise
    sid = archive.start_session("text")
    assert sid == 0
    archive.append_turn(sid, "user", "x")
    archive.end_session(sid)
    assert archive.search("x") == []


def test_kill_switch_via_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SESSION_ARCHIVE_ENABLED", "false")
    archive = SessionArchive(db_path=tmp_path / "sessions.db")
    assert archive._available is False
    sid = archive.start_session("text")
    assert sid == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_session_archive.py -v`
Expected: FAIL — env kill switch not honored; unwritable path test may already pass since `_init_schema` already wraps in try/except

- [ ] **Step 3: Add the env kill switch**

Modify `__init__` in `core/session_archive.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_session_archive.py -v`
Expected: PASS for all 22 tests

- [ ] **Step 5: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add core/session_archive.py tests/test_session_archive.py && git commit -m "feat: add SESSION_ARCHIVE_ENABLED kill switch and verify failure tolerance"
```

---

## Task 7: ToolLoop — archive_callback hook

**Files:**
- Modify: `core/tool_loop.py`
- Test: `tests/test_tool_loop_archive.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tool_loop_archive.py`:

```python
"""Tests for the optional archive_callback hook in ToolLoop."""

from unittest.mock import MagicMock

from core.conversation_state import ConversationState
from core.tool_loop import ToolLoop


class _FakeBlock:
    def __init__(self, type_, **kwargs):
        self.type = type_
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeResponse:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = MagicMock(input_tokens=10, output_tokens=20)


def _make_text_response(text):
    return _FakeResponse([_FakeBlock("text", text=text)], stop_reason="end_turn")


def _make_loop(client, container_manager=None, skill_loader=None):
    cm = container_manager or MagicMock()
    sl = skill_loader or MagicMock()
    sl.get_tool_definitions.return_value = []
    return ToolLoop(
        client=client,
        model="claude-test",
        skill_loader=sl,
        container_manager=cm,
        conversation_state=ConversationState(),
        memory_provider=None,
    )


def test_run_calls_archive_callback_with_text_response():
    client = MagicMock()
    client.messages.create.return_value = _make_text_response("hello back")
    captured = []
    loop = _make_loop(client)

    loop.run(
        user_message="hi there",
        system_prompt="sys",
        archive_callback=lambda u, t, r: captured.append((u, t, r)),
    )

    assert len(captured) == 1
    user_msg, tool_activity, response_text = captured[0]
    assert user_msg == "hi there"
    assert tool_activity == []
    assert response_text == "hello back"


def test_run_calls_archive_callback_with_tool_activity():
    client = MagicMock()
    skill_loader = MagicMock()
    skill_loader.get_tool_definitions.return_value = [{"name": "weather"}]
    skill_loader.get_skill.return_value = MagicMock(name="weather")
    container_manager = MagicMock()
    container_manager.execute_skill.return_value = "Paris: 14C"

    tool_use_response = _FakeResponse(
        [_FakeBlock("tool_use", id="t1", name="weather", input={"city": "Paris"})],
        stop_reason="tool_use",
    )
    text_response = _make_text_response("It is 14 in Paris.")
    client.messages.create.side_effect = [tool_use_response, text_response]

    captured = []
    loop = _make_loop(client, container_manager=container_manager, skill_loader=skill_loader)

    loop.run(
        user_message="weather in Paris",
        system_prompt="sys",
        archive_callback=lambda u, t, r: captured.append((u, t, r)),
    )

    assert len(captured) == 1
    user_msg, tool_activity, response_text = captured[0]
    assert user_msg == "weather in Paris"
    assert len(tool_activity) == 1
    assert tool_activity[0]["name"] == "weather"
    assert tool_activity[0]["input"] == {"city": "Paris"}
    assert tool_activity[0]["result"] == "Paris: 14C"
    assert response_text == "It is 14 in Paris."


def test_run_without_callback_unchanged():
    client = MagicMock()
    client.messages.create.return_value = _make_text_response("ok")
    loop = _make_loop(client)
    text = loop.run(user_message="hi", system_prompt="sys")
    assert text == "ok"  # no exception, normal return
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_tool_loop_archive.py -v`
Expected: FAIL with `TypeError: run() got an unexpected keyword argument 'archive_callback'`

- [ ] **Step 3: Add archive_callback to ToolLoop.run**

Modify `core/tool_loop.py`. Replace the `run` method:

```python
    def run(
        self,
        user_message: str,
        system_prompt: str,
        archive_callback=None,
    ) -> str:
        """
        Process a user message through Claude with tool support.

        archive_callback: optional Callable[[str, list[dict], str], None].
        Called once per completed turn with (user_message, tool_activity,
        response_text). tool_activity is a list of {"name", "input", "result"}
        dicts, one per tool call this turn (in order). Fires before prune.
        """
        self.conversation_state.append_user_text(user_message)
        effective_system_prompt = self._augment_system_prompt(
            system_prompt=system_prompt,
            user_message=user_message,
        )

        tool_definitions = self.skill_loader.get_tool_definitions()
        tool_activity: list[dict] = []
        rounds = 0

        while rounds < self.max_rounds:
            rounds += 1

            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=effective_system_prompt,
                messages=self.conversation_state.select_messages_for_prompt(),
                tools=tool_definitions if tool_definitions else anthropic.NOT_GIVEN,
            )

            if response.stop_reason == "tool_use":
                tool_results = self._handle_tool_calls(response, tool_activity)
                self.conversation_state.append_assistant_content(response.content)
                self.conversation_state.append_tool_results(tool_results)
                continue

            response_text = self._extract_text(response)
            self.conversation_state.append_assistant_content(response.content)

            logger.info(
                "Response ready: %d rounds, %d input / %d output tokens",
                rounds,
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            if archive_callback is not None:
                try:
                    archive_callback(user_message, tool_activity, response_text)
                except Exception:
                    logger.exception("archive_callback failed")
            self.conversation_state.prune()
            return response_text

        logger.warning("Max tool rounds reached (%d)", self.max_rounds)
        if archive_callback is not None:
            try:
                archive_callback(user_message, tool_activity, "")
            except Exception:
                logger.exception("archive_callback failed")
        self.conversation_state.prune()
        return "I ran into an issue processing that request. Could you try again?"
```

Update `_handle_tool_calls` to accept and append to `tool_activity`:

```python
    def _handle_tool_calls(self, response, tool_activity: list[dict]) -> list[dict]:
        """Execute tool calls from Claude's response, appending to tool_activity."""
        tool_results = []

        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_input = block.input
            logger.info("Tool call: %s(%s)", tool_name, json.dumps(tool_input)[:200])

            skill = self.skill_loader.get_skill(tool_name)
            if skill:
                result = self.container_manager.execute_skill(skill, tool_input)
                result = self._extract_and_save_remember(result)
            else:
                result = f"Unknown tool: {tool_name}"

            tool_activity.append({
                "name": tool_name,
                "input": tool_input,
                "result": result,
            })

            logger.info("Tool result: %s", result[:200])
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                }
            )

        return tool_results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_tool_loop_archive.py tests/test_orchestrator_routing.py tests/test_orchestrator_scheduler.py -v`
Expected: PASS for new tests, no regressions in existing orchestrator tests

- [ ] **Step 5: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add core/tool_loop.py tests/test_tool_loop_archive.py && git commit -m "feat: add optional archive_callback to ToolLoop.run"
```

---

## Task 8: Orchestrator — session lifecycle + archive wiring

**Files:**
- Modify: `core/orchestrator.py`
- Test: `tests/test_orchestrator_archive.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrator_archive.py`:

```python
"""Integration tests for SessionArchive wiring in Orchestrator."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.session_archive import SessionArchive


@pytest.fixture
def archive(tmp_path: Path) -> SessionArchive:
    return SessionArchive(db_path=tmp_path / "sessions.db")


def _make_orchestrator(archive: SessionArchive):
    """Construct an orchestrator with all heavy deps stubbed out."""
    from core.orchestrator import Orchestrator

    with patch("core.orchestrator.anthropic.Anthropic"), \
         patch("core.orchestrator.SkillLoader") as sl_cls, \
         patch("core.orchestrator.SkillSelector") as ss_cls, \
         patch("core.orchestrator.ContainerManager") as cm_cls, \
         patch("core.orchestrator.MemoryProvider") as mp_cls, \
         patch("core.orchestrator.PromptBuilder") as pb_cls, \
         patch("core.orchestrator.ToolLoop") as tl_cls:
        sl_cls.return_value.load_all.return_value = {}
        sl_cls.return_value.skipped_skills = {}
        sl_cls.return_value.invalid_skills = {}
        ss_cls.return_value.available = False
        pb_cls.return_value.build.return_value = "system"
        tl_cls.return_value.run.return_value = "response text"

        orch = Orchestrator(anthropic_api_key="test", archive=archive)
        return orch, tl_cls.return_value


def test_start_session_creates_session_row(archive: SessionArchive):
    orch, _ = _make_orchestrator(archive)
    orch.start_session("text")
    assert orch._current_session_id > 0


def test_end_session_finalizes_and_resets(archive: SessionArchive):
    orch, _ = _make_orchestrator(archive)
    orch.start_session("text")
    sid = orch._current_session_id
    orch.end_session()
    assert orch._current_session_id is None

    import sqlite3
    conn = sqlite3.connect(archive.db_path)
    try:
        ended = conn.execute(
            "SELECT ended_at FROM sessions WHERE id = ?", (sid,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert ended is not None


def test_end_session_without_start_is_noop(archive: SessionArchive):
    orch, _ = _make_orchestrator(archive)
    orch.end_session()  # must not raise


def test_process_message_archives_user_and_assistant(archive: SessionArchive):
    orch, tl = _make_orchestrator(archive)

    # Simulate tool_loop calling the archive_callback once with text response
    def fake_run(user_message, system_prompt, archive_callback=None):
        if archive_callback:
            archive_callback(user_message, [], "the assistant reply")
        return "the assistant reply"

    tl.run.side_effect = fake_run
    orch.start_session("text")
    orch.process_message("hello there")

    import sqlite3
    conn = sqlite3.connect(archive.db_path)
    try:
        rows = conn.execute(
            "SELECT role, content FROM turns WHERE session_id = ? ORDER BY turn_index",
            (orch._current_session_id,),
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("user", "hello there"), ("assistant", "the assistant reply")]


def test_process_message_archives_tool_activity(archive: SessionArchive):
    orch, tl = _make_orchestrator(archive)

    def fake_run(user_message, system_prompt, archive_callback=None):
        if archive_callback:
            tool_activity = [{
                "name": "weather",
                "input": {"city": "Paris"},
                "result": "Paris: 14C, light rain",
            }]
            archive_callback(user_message, tool_activity, "It is 14 in Paris.")
        return "It is 14 in Paris."

    tl.run.side_effect = fake_run
    orch.start_session("text")
    orch.process_message("weather in Paris")

    import sqlite3
    conn = sqlite3.connect(archive.db_path)
    try:
        rows = conn.execute(
            "SELECT role, content, tool_name FROM turns "
            "WHERE session_id = ? ORDER BY turn_index",
            (orch._current_session_id,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 3
    assert rows[0] == ("user", "weather in Paris", None)
    assert rows[1][0] == "tool"
    assert rows[1][2] == "weather"
    assert "Paris" in rows[1][1]
    assert rows[2] == ("assistant", "It is 14 in Paris.", None)


def test_archive_noop_without_started_session(archive: SessionArchive):
    """If no session has been started, archive_callback writes nothing but does not raise."""
    orch, tl = _make_orchestrator(archive)

    def fake_run(user_message, system_prompt, archive_callback=None):
        if archive_callback:
            archive_callback(user_message, [], "reply")
        return "reply"

    tl.run.side_effect = fake_run
    orch.process_message("no session")  # no start_session called

    import sqlite3
    conn = sqlite3.connect(archive.db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    finally:
        conn.close()
    assert count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_orchestrator_archive.py -v`
Expected: FAIL — Orchestrator does not accept `archive` kwarg, has no `start_session`/`end_session` methods, no `_current_session_id` attribute

- [ ] **Step 3: Wire archive into Orchestrator**

Modify `core/orchestrator.py`. Add the import at the top:

```python
from core.session_archive import SessionArchive
```

Update `__init__` signature and body. Replace the existing `__init__` head:

```python
    def __init__(
        self,
        anthropic_api_key: str,
        model: str = "claude-sonnet-4-5-20250929",
        skill_paths: list[Path] | None = None,
        container_memory: str = "256m",
        conversation_max_messages: int | None = 24,
        conversation_max_tokens: int | None = 6000,
        memory_max_tokens: int | None = 2000,
        memory_recall_max_tokens: int | None = 600,
        skill_prompt_max_tokens: int | None = 4000,
        skill_select_top_k: int = 2,
        archive: SessionArchive | None = None,
    ):
```

Add archive setup at the end of `__init__` (after the existing `logger.info("Orchestrator ready: ...")` line):

```python
        # Session archive (optional — None means "no archive").
        self.archive = archive
        self._current_session_id: int | None = None
```

Add three new methods on `Orchestrator` (place them above `process_message`):

```python
    def start_session(self, mode: str) -> None:
        """Begin a new archived conversation arc. Idempotent — second call ends
        any existing session first."""
        if self.archive is None:
            return
        if self._current_session_id is not None:
            self.end_session()
        sid = self.archive.start_session(mode)
        self._current_session_id = sid if sid else None

    def end_session(self) -> None:
        """Finalize the current archived session and reset state."""
        if self.archive is None or self._current_session_id is None:
            return
        try:
            self.archive.end_session(self._current_session_id)
        except Exception:
            logger.exception("end_session failed")
        self._current_session_id = None

    def _archive_callback(
        self, user_message: str, tool_activity: list[dict], response_text: str
    ) -> None:
        """Append a completed turn to the archive. No-op if archive disabled."""
        if self.archive is None or self._current_session_id is None:
            return
        try:
            sid = self._current_session_id
            self.archive.append_turn(sid, "user", user_message)
            for activity in tool_activity:
                summary = self._format_tool_summary(activity)
                self.archive.append_turn(sid, "tool", summary, tool_name=activity["name"])
            if response_text:
                self.archive.append_turn(sid, "assistant", response_text)
        except Exception:
            logger.exception("_archive_callback failed")

    def _format_tool_summary(self, activity: dict) -> str:
        """Render a tool call as a one-line summary for the archive."""
        import json as _json
        try:
            input_str = _json.dumps(activity.get("input") or {}, separators=(",", ":"))
        except (TypeError, ValueError):
            input_str = str(activity.get("input"))
        result = str(activity.get("result", ""))
        if len(input_str) > 80:
            input_str = input_str[:77] + "..."
        if len(result) > 120:
            result = result[:117] + "..."
        return f"{activity.get('name','?')}({input_str}) -> {result}"
```

Update `process_message` to pass the archive callback. Replace the Claude-only branch (the `if self._tier_router is None:` block) and the `route.tier == "claude"` branch with the callback wired in. Find:

```python
        if self._tier_router is None:
            # OLLAMA_ENABLED=false — Claude-only path, unchanged.
            system_prompt = self._build_system_prompt(user_message=user_message)
            return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)
```

Replace with:

```python
        if self._tier_router is None:
            system_prompt = self._build_system_prompt(user_message=user_message)
            return self.tool_loop.run(
                user_message=user_message,
                system_prompt=system_prompt,
                archive_callback=self._archive_callback,
            )
```

Find:

```python
        if route.tier == "claude":
            return self.tool_loop.run(user_message=user_message, system_prompt=system_prompt)
```

Replace with:

```python
        if route.tier == "claude":
            return self.tool_loop.run(
                user_message=user_message,
                system_prompt=system_prompt,
                archive_callback=self._archive_callback,
            )
```

Update `close_session`. Replace the existing `close_session` method:

```python
    def close_session(self) -> str:
        """End the current session: save anything worth remembering, then say goodbye."""
        if not self.conversation_state.messages:
            self.end_session()
            return "Goodbye!"

        response = self.tool_loop.run(
            user_message=(
                "The user is ending this conversation. "
                "If anything worth remembering came up — a preference, a project detail, "
                "something to keep in mind for next time — use save_memory to save it now. "
                "Then say a brief, warm goodbye."
            ),
            system_prompt=self.system_prompt,
            archive_callback=self._archive_callback,
        )
        self.end_session()
        return response
```

Update `reset_conversation`:

```python
    def reset_conversation(self):
        """Clear conversation history and end any open archive session."""
        self.end_session()
        self.conversation_state.clear()
        logger.info("Conversation history cleared")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_orchestrator_archive.py tests/test_orchestrator_routing.py tests/test_orchestrator_scheduler.py -v`
Expected: PASS for all new tests, no regressions in existing orchestrator tests

- [ ] **Step 5: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add core/orchestrator.py tests/test_orchestrator_archive.py && git commit -m "feat: wire SessionArchive into Orchestrator lifecycle"
```

---

## Task 9: recall_session skill — files + native handler

**Files:**
- Create: `skills/recall_session/SKILL.md`
- Create: `skills/recall_session/config.yaml`
- Modify: `core/container_manager.py`
- Test: `tests/test_recall_session_skill.py`

- [ ] **Step 1: Write the failing handler tests**

Create `tests/test_recall_session_skill.py`:

```python
"""Tests for the recall_session native skill handler."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

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
    # Must include a date/time bracket and the assistant role label
    assert "[" in out and "]" in out
    assert "assistant" in out


def test_handler_parses_yesterday(cm, archive):
    sid = archive.start_session("text")
    yesterday_iso = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    old_iso = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")

    # Insert one old turn and one recent turn directly via archive then patch ts
    archive.append_turn(sid, "user", "old kokoro question")
    archive.append_turn(sid, "user", "recent kokoro question")
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
    assert "old kokoro" not in out


def test_handler_unknown_since_falls_through_to_no_filter(cm, archive):
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "kokoro is the tts engine")
    out = cm._execute_recall_session({
        "query": "kokoro",
        "since": "blursday next week",
    })
    assert "kokoro" in out  # garbage `since` did not break the search
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_recall_session_skill.py -v`
Expected: FAIL with `AttributeError: 'ContainerManager' object has no attribute '_execute_recall_session'`

- [ ] **Step 3: Add the recall_session handler to ContainerManager**

Modify `core/container_manager.py`. Add to the constructor — find the `_native_handlers` dict and add the entry:

```python
        self._native_handlers = {
            "install_skill": self._execute_install_skill,
            "set_env_var": self._execute_set_env_var,
            "save_memory": self._execute_save_memory,
            "dashboard": self._execute_dashboard,
            "soundcloud_play": self._execute_soundcloud,
            "schedule": self._execute_schedule,
            "recall_session": self._execute_recall_session,
        }
```

Also add the `_archive` attribute alongside the other injected references — find the lines `self._meta_skill_executor = None` etc. and add:

```python
        self._archive = None             # injected from main.py after construction
```

Add the handler method to `ContainerManager` (place it near `_execute_schedule`):

```python
    def _execute_recall_session(self, tool_input: dict) -> str:
        """Native handler for the recall_session skill."""
        if self._archive is None:
            return "Session archive is not initialised."

        query = str(tool_input.get("query", "")).strip()
        if not query:
            return "No query provided."

        since = self._parse_since(tool_input.get("since"))
        try:
            limit = int(tool_input.get("limit", os.environ.get("SESSION_RECALL_DEFAULT_LIMIT", 5)))
        except (TypeError, ValueError):
            limit = 5

        hits = self._archive.search(query, since=since, limit=limit)
        if not hits:
            return f"No prior sessions mention '{query}'."
        return self._format_recall_hits(hits)

    def _parse_since(self, value) -> str | None:
        """Convert an ISO date or relative phrase to an ISO datetime string. None on failure."""
        if not value:
            return None
        s = str(value).strip().lower()
        if not s:
            return None

        from datetime import datetime, timedelta
        now = datetime.now()

        # ISO date
        try:
            return datetime.fromisoformat(s).isoformat(timespec="seconds")
        except ValueError:
            pass

        if s in ("today",):
            return now.replace(hour=0, minute=0, second=0).isoformat(timespec="seconds")
        if s in ("yesterday",):
            return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0).isoformat(timespec="seconds")
        if s in ("last week", "past week"):
            return (now - timedelta(days=7)).isoformat(timespec="seconds")

        m = re.match(r"(\d+)\s*days?\s*ago$", s)
        if m:
            return (now - timedelta(days=int(m.group(1)))).isoformat(timespec="seconds")

        return None  # unrecognized → fall through to no filter

    def _format_recall_hits(self, hits: list[dict]) -> str:
        """Render search hits as dated snippets with surrounding context."""
        blocks = []
        for hit in hits:
            lines = []
            # Order: any context turn before, the hit itself, any context turn after.
            before = [c for c in hit["context"] if c["turn_index"] < self._hit_turn_index(hit)]
            after = [c for c in hit["context"] if c["turn_index"] > self._hit_turn_index(hit)]
            for c in before:
                lines.append(self._format_turn_line(c["ts"], c["role"], c.get("tool_name"), c["content"]))
            lines.append(self._format_turn_line(hit["ts"], hit["role"], hit.get("tool_name"), hit["content"]))
            for c in after:
                lines.append(self._format_turn_line(c["ts"], c["role"], c.get("tool_name"), c["content"]))
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def _hit_turn_index(self, hit: dict) -> int:
        """Look up the hit's own turn_index from its content row (cached on the hit)."""
        # The search() result does not currently include the hit's own turn_index;
        # context turns are returned as turn_index ± 1 so we can derive it from them
        # if either is present, otherwise 0.
        ctx_indexes = [c["turn_index"] for c in hit.get("context", [])]
        if not ctx_indexes:
            return 0
        return min(ctx_indexes) + 1 if min(ctx_indexes) >= 0 else max(ctx_indexes) - 1

    def _format_turn_line(self, ts: str, role: str, tool_name: str | None, content: str) -> str:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(ts)
            stamp = dt.strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            stamp = ts
        if role == "tool" and tool_name:
            return f"[{stamp}] tool ({tool_name}): {content}"
        return f"[{stamp}] {role}: {content}"
```

Note: `_hit_turn_index` derives the hit's index from context. For a cleaner design, also include `turn_index` in the hit dict returned by `SessionArchive.search`. Update `core/session_archive.py` `search` method to add `turn_index` to each hit dict (the field already exists in the row at index 6):

In `SessionArchive.search`, change the hit dict construction to include `turn_index`:

```python
                    hits.append({
                        "turn_id": row[0],
                        "session_id": row[1],
                        "ts": row[2],
                        "role": row[3],
                        "tool_name": row[4],
                        "content": row[5],
                        "turn_index": row[6],
                        "context": context,
                        "fts_rank": row[7],
                    })
```

Then simplify the formatter in `container_manager.py` — replace `_hit_turn_index` and the `before`/`after` lines with:

```python
    def _format_recall_hits(self, hits: list[dict]) -> str:
        """Render search hits as dated snippets with surrounding context."""
        blocks = []
        for hit in hits:
            lines = []
            hit_idx = hit["turn_index"]
            before = [c for c in hit["context"] if c["turn_index"] < hit_idx]
            after = [c for c in hit["context"] if c["turn_index"] > hit_idx]
            for c in before:
                lines.append(self._format_turn_line(c["ts"], c["role"], c.get("tool_name"), c["content"]))
            lines.append(self._format_turn_line(hit["ts"], hit["role"], hit.get("tool_name"), hit["content"]))
            for c in after:
                lines.append(self._format_turn_line(c["ts"], c["role"], c.get("tool_name"), c["content"]))
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)
```

Delete the `_hit_turn_index` helper.

Add a quick test that `turn_index` is included in search hits — append to `tests/test_session_archive.py`:

```python
def test_search_hit_includes_turn_index(archive: SessionArchive) -> None:
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "first")
    archive.append_turn(sid, "user", "second match")
    hits = archive.search("match")
    assert hits and "turn_index" in hits[0]
    assert hits[0]["turn_index"] == 1
```

- [ ] **Step 4: Create the skill files**

Create `skills/recall_session/SKILL.md`:

```markdown
---
name: recall_session
description: Search past conversation transcripts. Use when the user references something said in a prior session.
---

Search the local archive of past conversations for content matching a query.

When to use:
- User references a prior conversation ("what did we decide about X last week?")
- User asks "did we ever talk about X?" or "when did we last discuss X?"
- You need to verify what was actually said in a past session

When NOT to use:
- For general facts or preferences — those live in saved memory and are already in your prompt
- For the current ongoing conversation — that is in your context window

Input (JSON via SKILL_INPUT):
- query (required): keywords or short phrase to search for
- since (optional): ISO date "2026-04-15" or relative "yesterday" / "last week" / "N days ago"
- limit (optional): max results, default 5

Output: dated snippets ordered by relevance, each with surrounding turns for context.
Tell the user when matches were from and quote the relevant lines. If nothing matches, say so plainly.
```

Create `skills/recall_session/config.yaml`:

```yaml
type: native
timeout_seconds: 5
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/daedalus/linux/miniclaw && python -m pytest tests/test_recall_session_skill.py tests/test_session_archive.py tests/test_container_manager.py -v`
Expected: PASS for all new tests, no regressions

- [ ] **Step 6: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add core/container_manager.py core/session_archive.py skills/recall_session tests/test_recall_session_skill.py tests/test_session_archive.py && git commit -m "feat: add recall_session native skill"
```

---

## Task 10: main.py wiring + smoke test

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Wire SessionArchive into main.py**

Modify `main.py`. Add the import near the existing core imports:

```python
from core.session_archive import SessionArchive
```

Find the orchestrator instantiation in `main()` and update to pass the archive. Replace:

```python
    # Initialize orchestrator
    from core.orchestrator import Orchestrator

    orchestrator = Orchestrator(
        anthropic_api_key=api_key,
        model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        skill_paths=skill_paths,
        container_memory=os.getenv("CONTAINER_MEMORY", "256m"),
        conversation_max_messages=int(os.getenv("CONVERSATION_MAX_MESSAGES", "24")),
        conversation_max_tokens=int(os.getenv("CONVERSATION_MAX_TOKENS", "6000")),
        memory_max_tokens=int(os.getenv("MEMORY_MAX_TOKENS", "2000")),
        memory_recall_max_tokens=int(os.getenv("MEMORY_RECALL_MAX_TOKENS", "600")),
        skill_prompt_max_tokens=int(os.getenv("SKILL_PROMPT_MAX_TOKENS", "4000")),
        skill_select_top_k=int(os.getenv("SKILL_SELECT_TOP_K", "2")),
    )

    # Inject orchestrator reference for native skills that need to reload
    orchestrator.container_manager._orchestrator = orchestrator
```

With:

```python
    # Initialize session archive
    archive = SessionArchive()

    # Initialize orchestrator
    from core.orchestrator import Orchestrator

    orchestrator = Orchestrator(
        anthropic_api_key=api_key,
        model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        skill_paths=skill_paths,
        container_memory=os.getenv("CONTAINER_MEMORY", "256m"),
        conversation_max_messages=int(os.getenv("CONVERSATION_MAX_MESSAGES", "24")),
        conversation_max_tokens=int(os.getenv("CONVERSATION_MAX_TOKENS", "6000")),
        memory_max_tokens=int(os.getenv("MEMORY_MAX_TOKENS", "2000")),
        memory_recall_max_tokens=int(os.getenv("MEMORY_RECALL_MAX_TOKENS", "600")),
        skill_prompt_max_tokens=int(os.getenv("SKILL_PROMPT_MAX_TOKENS", "4000")),
        skill_select_top_k=int(os.getenv("SKILL_SELECT_TOP_K", "2")),
        archive=archive,
    )

    # Inject orchestrator reference for native skills that need to reload
    orchestrator.container_manager._orchestrator = orchestrator
    orchestrator.container_manager._archive = archive
```

- [ ] **Step 2: Add session start/end calls in voice mode**

In `main.py`, in `run_voice_mode`, find the inner conversation loop:

```python
            print("Listening...")
            active_flag[0] = True

            # Conversation session — keep listening until idle
            while True:
                transcription = voice.listen(max_wait_seconds=conversation_idle_timeout)
```

Insert `orchestrator.start_session("voice")` between `active_flag[0] = True` and `while True:`:

```python
            print("Listening...")
            active_flag[0] = True
            orchestrator.start_session("voice")

            # Conversation session — keep listening until idle
            while True:
                transcription = voice.listen(max_wait_seconds=conversation_idle_timeout)
```

Find the `# No speech within idle timeout — end session` block:

```python
                if not transcription:
                    # No speech within idle timeout — end session
                    print("Session ended.")
                    active_flag[0] = False
                    break
```

Add `orchestrator.end_session()` before `break`:

```python
                if not transcription:
                    print("Session ended.")
                    active_flag[0] = False
                    orchestrator.end_session()
                    break
```

The "goodbye" path already calls `orchestrator.close_session()` which calls `end_session()` internally — no change needed there. The KeyboardInterrupt at the bottom should also end any open session. Find:

```python
    except KeyboardInterrupt:
        print("\n\nShutting down...")
```

Replace with:

```python
    except KeyboardInterrupt:
        print("\n\nShutting down...")
    finally:
        orchestrator.end_session()
```

- [ ] **Step 3: Add session start/end calls in text mode**

In `main.py`, in `run_text_mode`, find the start of the function body — after the print banner and `_print_loaded_skills`:

```python
def run_text_mode(orchestrator):
    """Run the assistant in text-only mode (no microphone needed)."""
    print("\n" + "=" * 60)
    print("  MiniClaw (Text Mode)")
    print("=" * 60)

    _print_loaded_skills(orchestrator)
    print("\n  Type your message. Type 'quit' to exit.\n")

    try:
```

Add `orchestrator.start_session("text")` right before `try:`:

```python
def run_text_mode(orchestrator):
    """Run the assistant in text-only mode (no microphone needed)."""
    print("\n" + "=" * 60)
    print("  MiniClaw (Text Mode)")
    print("=" * 60)

    _print_loaded_skills(orchestrator)
    print("\n  Type your message. Type 'quit' to exit.\n")

    orchestrator.start_session("text")

    try:
```

Find the end of `run_text_mode`:

```python
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye!")
```

Replace with:

```python
    except (KeyboardInterrupt, EOFError):
        print("\nGoodbye!")
    finally:
        orchestrator.end_session()
```

- [ ] **Step 4: Manual smoke test (text mode)**

Run text mode and exercise the archive end-to-end. The user (Mason) needs an API key set up for this; if `ANTHROPIC_API_KEY` is not in `.env`, skip this step and rely on the unit/integration tests.

```bash
cd /home/daedalus/linux/miniclaw && rm -f ~/.miniclaw/sessions.db
cd /home/daedalus/linux/miniclaw && python main.py --text
```

In the prompt:
1. Ask: `tell me about the kokoro voice options`
2. Wait for the response
3. Type `quit`
4. Re-launch: `python main.py --text`
5. Ask: `recall what we said about kokoro`
6. Verify: response references the prior session content

Then inspect the db directly:

```bash
cd /home/daedalus/linux/miniclaw && python -c "
import sqlite3
from pathlib import Path
db = Path.home() / '.miniclaw' / 'sessions.db'
conn = sqlite3.connect(db)
print('sessions:', list(conn.execute('SELECT id, mode, started_at, ended_at, turn_count FROM sessions')))
print('turns:', conn.execute('SELECT COUNT(*) FROM turns').fetchone())
"
```

Expected:
- Two sessions, both with `ended_at` populated and `turn_count > 0`.
- Total `turns` count > 0.

- [ ] **Step 5: Run the full unit/integration test suite**

Run:

```bash
cd /home/daedalus/linux/miniclaw && python -m pytest tests/ -v
```

Expected: all tests pass — no regressions in the existing 16 test files.

- [ ] **Step 6: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add main.py && git commit -m "feat: wire SessionArchive lifecycle into main.py voice and text modes"
```

---

## Task 11: Update CLAUDE.md with the new skill and env vars

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add recall_session to the native skills list**

In `CLAUDE.md`, find the line:

```markdown
Current native skills: `install_skill`, `set_env_var`, `save_memory`, `dashboard`.
```

Replace with:

```markdown
Current native skills: `install_skill`, `set_env_var`, `save_memory`, `dashboard`, `soundcloud_play`, `schedule`, `recall_session`.
```

- [ ] **Step 2: Add the recall_session behavior description**

In `CLAUDE.md`, find the `### Key Behaviours` section and the `save_memory` entry. After the `save_memory` paragraph, add:

```markdown
- **`recall_session` skill**: Native skill that searches past conversation transcripts via FTS5. Every conversation arc (one per voice wake/idle cycle, one per text process) is logged to `~/.miniclaw/sessions.db` (configurable via `SESSION_ARCHIVE_PATH`). The orchestrator hooks `start_session` / `end_session` around each arc and passes an `archive_callback` to `tool_loop.run` so each completed turn writes user text + tool activity summaries + assistant response immediately. Recall is tool-invoked only — Claude calls `recall_session(query=..., since=..., limit=...)` when the user references prior conversations. Returns dated snippets with ±1 turn of context, BM25-ranked. Forward-compatible with a chromadb rerank layer (see spec `2026-04-22-fts5-session-archive-design.md`) — `SessionArchive.search` returns structured dicts and accepts an `oversample` param (inert in v1).
```

- [ ] **Step 3: Add the new env vars to the table**

In `CLAUDE.md`, find the `## Key Environment Variables` table. Add three rows at the bottom (before the closing of the table):

```markdown
| `SESSION_ARCHIVE_PATH` | `~/.miniclaw/sessions.db` | sqlite + FTS5 file for the conversation archive |
| `SESSION_ARCHIVE_ENABLED` | `true` | Set false to disable the archive entirely |
| `SESSION_RECALL_DEFAULT_LIMIT` | `5` | Default `limit` when `recall_session` omits it |
```

- [ ] **Step 4: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add CLAUDE.md && git commit -m "docs: document recall_session skill and session archive env vars in CLAUDE.md"
```

---

## Task 12: Update WORKING_MEMORY.md with the shipped milestone

**Files:**
- Modify: `WORKING_MEMORY.md`

- [ ] **Step 1: Mark item #2 done in the Hermes roadmap**

In `WORKING_MEMORY.md`, find:

```markdown
2. FTS5 session archive — persist past conversations to a sqlite FTS5 index so Claude can recall prior sessions by content search.
   v1 plan: FTS5-only, tool-invoked `recall_session` skill, per-turn writes for crash safety, captures user/assistant text + short tool-activity summaries.
   Engine choice: FTS5 over chromadb for v1 because per-turn `all-MiniLM-L6-v2` embedding (~50-200ms on Pi 5 CPU, ~150-300MB resident RAM) sits on the voice loop critical path. FTS5 writes are microseconds.
   Forward plan: design the `recall_session` interface and sqlite schema so a chromadb rerank layer can drop in cleanly once Hailo-8L NPU offload makes embeddings near-free. Do NOT implement the chromadb path until Hailo arrives — defer it as a follow-up so we never ship CPU-side embedding on the write path.
```

Replace with:

```markdown
2. ~~FTS5 session archive — persist past conversations to a sqlite FTS5 index so Claude can recall prior sessions by content search.~~ Done 2026-04-22.
   Shipped as `recall_session` native skill backed by `~/.miniclaw/sessions.db`. v1 is FTS5-only with per-turn writes; `SessionArchive.search` returns structured dicts and accepts an inert `oversample` param so a chromadb rerank layer can drop in cleanly once Hailo-8L NPU offload arrives. Do not add chromadb to the write path — only as a lazy rerank layer over FTS5 top-N when embeddings are near-free.
```

- [ ] **Step 2: Add a Recent Durable Milestones entry**

In `WORKING_MEMORY.md`, find the `## Recent Durable Milestones` section and add a new entry at the top:

```markdown
- 2026-04-22: shipped the FTS5 session archive and `recall_session` native skill
  per-turn writes from orchestrator to `~/.miniclaw/sessions.db` via `SessionArchive`
  tool-invoked recall with BM25 ranking + ±1 turn of context
  forward-compatible with chromadb rerank when Hailo-8L lands (no schema migration needed)
```

- [ ] **Step 3: Commit**

```bash
cd /home/daedalus/linux/miniclaw && git add WORKING_MEMORY.md && git commit -m "docs: record fts5 session archive milestone in working memory"
```

---

## Self-Review Notes

Verified against `docs/superpowers/specs/2026-04-22-fts5-session-archive-design.md`:

- Engine choice (FTS5 over chromadb) → §Engine choice covered by tasks 1-6.
- Architecture diagram → Tasks 1-10 land all the new files and touch points listed.
- Schema → Task 1 creates exactly the spec's tables, indexes, FTS virtual table, and triggers.
- Write path (start_session / append_turn / end_session, per-turn writes for crash safety) → Tasks 2, 3, 8, 10.
- Read path (`recall_session` skill, structured snippets with ±1 context) → Tasks 4, 5, 9.
- Forward-compat contracts (structured dict returns, `oversample` param, no vectors in sqlite, optional `reranker`) → Task 1 (`__init__(reranker=None)`), Task 4 (structured dicts + oversample), Task 5 (context turns).
- Failure modes (unwritable path, kill switch, voice loop never raised into) → Task 6, plus `try/except` guards in all archive methods (Tasks 2, 3, 5) and orchestrator callback (Task 8).
- Config knobs (`SESSION_ARCHIVE_PATH`, `SESSION_ARCHIVE_ENABLED`, `SESSION_RECALL_DEFAULT_LIMIT`) → Task 1, Task 6, Task 9.
- Testing approach (unit tests on `:memory:` style fixtures, integration test for native skill, failure injection, manual smoke test) → Tasks 1-9 cover unit + integration; Task 10 covers manual smoke + full suite run.
- Docs updates → Tasks 11-12.

No placeholders. Method names consistent across tasks (`start_session`, `end_session`, `append_turn`, `search`, `_archive_callback`, `_format_tool_summary`, `_execute_recall_session`, `_format_recall_hits`, `_parse_since`, `_format_turn_line`).
