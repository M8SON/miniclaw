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


if __name__ == "__main__":
    unittest.main()
