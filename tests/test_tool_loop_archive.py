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
