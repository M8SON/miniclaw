"""15-tool-call checkpoint nudge in ToolLoop."""

import unittest
from unittest.mock import MagicMock


class _FakeBlock:
    def __init__(self, btype, **kw):
        self.type = btype
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeUsage:
    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0


class _FakeResponse:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _FakeUsage()


def _make_tool_use_response(n_tool_calls):
    blocks = []
    for i in range(n_tool_calls):
        blocks.append(_FakeBlock("tool_use", id=f"tu{i}", name="weather", input={"query": "x"}))
    return _FakeResponse(blocks, "tool_use")


def _make_text_response(text):
    return _FakeResponse([_FakeBlock("text", text=text)], "end_turn")


class _Loader:
    def __init__(self, opted_in: bool):
        s = MagicMock()
        s.frontmatter = {"metadata": {"miniclaw": {"self_update": {"allow_body": opted_in}}}}
        self.skills = {"weather": s}

    def get_tool_definitions(self):
        return [{"name": "weather", "description": "x", "input_schema": {"type": "object"}}]

    def get_skill(self, name):
        return self.skills.get(name)


class _CM:
    def __init__(self):
        self.calls = 0

    def execute_skill(self, skill, tool_input):
        self.calls += 1
        return "result"

    def start_turn(self):
        pass


class _ConvState:
    def __init__(self):
        self.user_messages = []
        self.assistant_blocks = []

    def append_user_text(self, t):
        self.user_messages.append(t)

    def append_assistant_content(self, c):
        self.assistant_blocks.append(c)

    def append_tool_results(self, r):
        pass

    def select_messages_for_prompt(self):
        return []

    def prune(self):
        pass


def _run_loop_with_calls(num_tool_calls, opted_in=True):
    """Drive a ToolLoop run that performs num_tool_calls before returning text."""
    from core.tool_loop import ToolLoop

    loader = _Loader(opted_in=opted_in)
    cm = _CM()
    state = _ConvState()
    client = MagicMock()

    responses = [_make_tool_use_response(1) for _ in range(num_tool_calls)]
    responses.append(_make_text_response("done"))
    client.messages.create = MagicMock(side_effect=responses)

    loop = ToolLoop(
        client=client, model="claude-test",
        skill_loader=loader, container_manager=cm,
        conversation_state=state, max_rounds=num_tool_calls + 5,
    )
    loop.run("hi", system_prompt="base")
    return client.messages.create.call_args_list


class TestCheckpointNudge(unittest.TestCase):
    def test_below_15_calls_no_nudge(self):
        calls = _run_loop_with_calls(num_tool_calls=7)
        for c in calls:
            sys_arg = c.kwargs.get("system", "")
            self.assertNotIn("CHECKPOINT", sys_arg)

    def test_at_15_calls_nudge_in_next_request(self):
        calls = _run_loop_with_calls(num_tool_calls=15)
        # The 16th request (index 15) should carry the nudge.
        self.assertIn("CHECKPOINT", calls[15].kwargs.get("system", ""))

    def test_no_opted_in_skill_suppresses_nudge(self):
        calls = _run_loop_with_calls(num_tool_calls=15, opted_in=False)
        for c in calls:
            sys_arg = c.kwargs.get("system", "")
            self.assertNotIn("CHECKPOINT", sys_arg)


if __name__ == "__main__":
    unittest.main()
