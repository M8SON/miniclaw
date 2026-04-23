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
