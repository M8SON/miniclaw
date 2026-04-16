"""Tests for OllamaToolLoop and EscalateSignal."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_loop(
    host="http://localhost:11434",
    model="phi4-mini",
    skill_loader=None,
    container_manager=None,
    conversation_state=None,
    timeout_seconds=8.0,
):
    from core.ollama_tool_loop import OllamaToolLoop

    if skill_loader is None:
        skill_loader = MagicMock()
        skill_loader.get_tool_definitions.return_value = []
        skill_loader.get_skill.return_value = None

    if container_manager is None:
        container_manager = MagicMock()

    if conversation_state is None:
        from core.conversation_state import ConversationState
        conversation_state = ConversationState()

    return OllamaToolLoop(
        host=host,
        model=model,
        skill_loader=skill_loader,
        container_manager=container_manager,
        conversation_state=conversation_state,
        timeout_seconds=timeout_seconds,
    )


def _make_response(content=None, finish_reason="stop", tool_calls=None):
    """Build a minimal Ollama /v1/chat/completions JSON response."""
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [
            {
                "message": message,
                "finish_reason": finish_reason if not tool_calls else "tool_calls",
            }
        ]
    }


class TestEscalateSignal(unittest.TestCase):

    def test_escalate_signal_is_singleton(self):
        from core.ollama_tool_loop import EscalateSignal as E1
        from core.ollama_tool_loop import EscalateSignal as E2
        self.assertIs(E1, E2)

    def test_escalate_signal_identity_comparison(self):
        from core.ollama_tool_loop import _EscalateSignalType, EscalateSignal
        second_instance = _EscalateSignalType()
        self.assertIs(second_instance, EscalateSignal)
        self.assertIsNot(EscalateSignal, None)
        self.assertNotEqual(EscalateSignal, "ESCALATE")


class TestTimeoutEscalation(unittest.TestCase):

    def test_timeout_returns_escalate_signal(self):
        import requests
        from core.ollama_tool_loop import EscalateSignal

        loop = _make_loop()
        with patch("requests.post", side_effect=requests.Timeout):
            result = loop.run("play some jazz", "you are a voice assistant")
        self.assertIs(result, EscalateSignal)

    def test_connection_error_returns_escalate_signal(self):
        import requests
        from core.ollama_tool_loop import EscalateSignal

        loop = _make_loop()
        with patch("requests.post", side_effect=requests.ConnectionError):
            result = loop.run("play some jazz", "you are a voice assistant")
        self.assertIs(result, EscalateSignal)

    def test_conversation_state_unchanged_on_timeout(self):
        import requests

        from core.conversation_state import ConversationState
        state = ConversationState()
        loop = _make_loop(conversation_state=state)
        with patch("requests.post", side_effect=requests.Timeout):
            loop.run("play some jazz", "you are a voice assistant")
        # ConversationState must be untouched — Claude will append the message itself
        self.assertEqual(state.messages, [])


class TestEscalateTriggers(unittest.TestCase):

    def _run_with_response(self, response_json, skill_loader=None, container_manager=None):
        from core.ollama_tool_loop import EscalateSignal

        loop = _make_loop(
            skill_loader=skill_loader or MagicMock(
                get_tool_definitions=MagicMock(return_value=[]),
                get_skill=MagicMock(return_value=None),
            ),
            container_manager=container_manager or MagicMock(),
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_json
        mock_resp.raise_for_status.return_value = None
        with patch("requests.post", return_value=mock_resp):
            return loop.run("play some jazz", "you are a voice assistant")

    def test_explicit_escalate_word_escalates(self):
        from core.ollama_tool_loop import EscalateSignal
        result = self._run_with_response(_make_response(content="ESCALATE"))
        self.assertIs(result, EscalateSignal)

    def test_empty_response_escalates(self):
        from core.ollama_tool_loop import EscalateSignal
        result = self._run_with_response(_make_response(content=""))
        self.assertIs(result, EscalateSignal)

    def test_unknown_tool_name_escalates(self):
        from core.ollama_tool_loop import EscalateSignal

        tool_call = {
            "id": "call_1",
            "function": {"name": "nonexistent_skill", "arguments": "{}"},
        }
        sl = MagicMock()
        sl.get_tool_definitions.return_value = []
        sl.get_skill.return_value = None  # skill not found

        result = self._run_with_response(
            _make_response(tool_calls=[tool_call]),
            skill_loader=sl,
        )
        self.assertIs(result, EscalateSignal)

    def test_malformed_tool_args_escalates(self):
        from core.ollama_tool_loop import EscalateSignal

        fake_skill = MagicMock()
        sl = MagicMock()
        sl.get_tool_definitions.return_value = []
        sl.get_skill.return_value = fake_skill  # skill exists

        tool_call = {
            "id": "call_1",
            "function": {"name": "weather", "arguments": "NOT_VALID_JSON"},
        }
        result = self._run_with_response(
            _make_response(tool_calls=[tool_call]),
            skill_loader=sl,
        )
        self.assertIs(result, EscalateSignal)

    def test_plain_text_response_returned_as_string(self):
        result = self._run_with_response(_make_response(content="The weather is sunny."))
        self.assertEqual(result, "The weather is sunny.")

    def test_successful_tool_call_returns_string(self):
        fake_skill = MagicMock()
        sl = MagicMock()
        sl.get_tool_definitions.return_value = []
        sl.get_skill.return_value = fake_skill

        cm = MagicMock()
        cm.execute_skill.return_value = "Currently 18°C and cloudy in London."

        # First response: tool call. Second response: final text.
        tool_call_response = _make_response(
            tool_calls=[{
                "id": "call_1",
                "function": {"name": "weather", "arguments": '{"city": "London"}'},
            }]
        )
        final_response = _make_response(content="It is 18 degrees and cloudy in London.")

        mock_resp_1 = MagicMock()
        mock_resp_1.json.return_value = tool_call_response
        mock_resp_1.raise_for_status.return_value = None

        mock_resp_2 = MagicMock()
        mock_resp_2.json.return_value = final_response
        mock_resp_2.raise_for_status.return_value = None

        with patch("requests.post", side_effect=[mock_resp_1, mock_resp_2]):
            result = _make_loop(skill_loader=sl, container_manager=cm).run(
                "what's the weather in London", "you are a voice assistant"
            )

        self.assertEqual(result, "It is 18 degrees and cloudy in London.")
        cm.execute_skill.assert_called_once_with(fake_skill, {"city": "London"})

    def test_successful_turn_commits_to_conversation_state(self):
        from core.conversation_state import ConversationState
        state = ConversationState()
        loop = _make_loop(conversation_state=state)
        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_response(content="Sure, playing jazz.")
        mock_resp.raise_for_status.return_value = None
        with patch("requests.post", return_value=mock_resp):
            loop.run("play jazz", "system prompt")
        self.assertEqual(len(state.messages), 2)
        self.assertEqual(state.messages[0]["role"], "user")
        self.assertEqual(state.messages[1]["role"], "assistant")


if __name__ == "__main__":
    unittest.main()
