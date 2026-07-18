"""Tests for Orchestrator.warm_prompt_cache — the wake-triggered warm-up
that pre-writes the Sonnet prompt cache."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_orchestrator():
    from core.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.client = MagicMock()
    orch.model = "claude-sonnet-test"
    orch.skill_loader = MagicMock()
    orch.skill_loader.get_tool_definitions.return_value = [
        {"name": "get_weather", "description": "w", "input_schema": {}}
    ]
    # _build_system_prompt_split is exercised for real elsewhere; here we
    # stub it so we can assert byte-identity of the cached block.
    orch._build_system_prompt_split = MagicMock(return_value=("STABLE_PREFIX", "DYNAMIC"))
    orch.conversation_state = MagicMock()
    orch.client.messages.create.return_value = SimpleNamespace(content=[])
    return orch


class TestWarmPromptCache(unittest.TestCase):
    def test_sends_one_minimal_request(self):
        orch = _make_orchestrator()
        orch.warm_prompt_cache()
        orch.client.messages.create.assert_called_once()
        kwargs = orch.client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "claude-sonnet-test")
        self.assertEqual(kwargs["max_tokens"], 1)

    def test_caches_the_stable_prefix_verbatim(self):
        orch = _make_orchestrator()
        orch.warm_prompt_cache()
        kwargs = orch.client.messages.create.call_args.kwargs
        system = kwargs["system"]
        self.assertEqual(system[0]["text"], "STABLE_PREFIX")
        self.assertEqual(system[0]["cache_control"], {"type": "ephemeral"})

    def test_builds_split_with_no_user_message(self):
        orch = _make_orchestrator()
        orch.warm_prompt_cache()
        orch._build_system_prompt_split.assert_called_once_with(user_message=None)

    def test_sends_full_tool_list(self):
        orch = _make_orchestrator()
        orch.warm_prompt_cache()
        kwargs = orch.client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["tools"], orch.skill_loader.get_tool_definitions.return_value)

    def test_does_not_touch_conversation_state(self):
        orch = _make_orchestrator()
        orch.warm_prompt_cache()
        orch.conversation_state.append_user_text.assert_not_called()

    def test_swallows_client_errors(self):
        orch = _make_orchestrator()
        orch.client.messages.create.side_effect = RuntimeError("api down")
        # Must not raise.
        orch.warm_prompt_cache()
