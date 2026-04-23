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


def test_search_hit_includes_turn_index(archive: SessionArchive) -> None:
    sid = archive.start_session("text")
    archive.append_turn(sid, "user", "first")
    archive.append_turn(sid, "user", "second match")
    hits = archive.search("match")
    assert hits and "turn_index" in hits[0]
    assert hits[0]["turn_index"] == 1
