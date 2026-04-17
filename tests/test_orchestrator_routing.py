# tests/test_orchestrator_routing.py
"""Tests for Orchestrator tiered routing when OLLAMA_ENABLED=true."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_orchestrator_with_mocks():
    """
    Build an Orchestrator with all external dependencies mocked so no
    real API calls, containers, or file I/O occur.
    """
    from core.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch.client = MagicMock()
    orch.model = "test-model"
    orch.skill_loader = MagicMock()
    orch.skill_loader.load_all.return_value = {}
    orch.skill_loader.skipped_skills = {}
    orch.skill_loader.invalid_skills = {}
    orch.skill_loader.get_tool_definitions.return_value = []
    orch.skills = {}
    orch.skill_selector = MagicMock()
    orch.skill_selector.available = False
    orch.container_manager = MagicMock()
    orch.conversation_state = MagicMock()
    orch.memory_provider = MagicMock()
    orch.prompt_builder = MagicMock()
    orch.prompt_builder.build.return_value = "system prompt"
    orch.tool_loop = MagicMock()
    orch.tool_loop.run.return_value = "Claude response"
    orch._startup_context = ""
    orch.system_prompt = "system prompt"
    orch._tier_router = None
    orch._ollama_tool_loop = None
    return orch


class TestOrchestratorRoutingDisabled(unittest.TestCase):

    def test_process_message_goes_to_claude_when_tier_router_none(self):
        orch = _make_orchestrator_with_mocks()
        orch._tier_router = None
        result = orch.process_message("play some jazz")
        orch.tool_loop.run.assert_called_once()
        self.assertEqual(result, "Claude response")


class TestOrchestratorRoutingEnabled(unittest.TestCase):

    def _make_router(self, tier):
        from core.tier_router import RouteResult
        router = MagicMock()
        router.route.return_value = RouteResult(tier=tier)
        return router

    def test_claude_route_goes_to_tool_loop(self):
        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("claude")
        orch._ollama_tool_loop = MagicMock()
        result = orch.process_message("install a new skill")
        orch.tool_loop.run.assert_called_once()
        orch._ollama_tool_loop.run.assert_not_called()
        self.assertEqual(result, "Claude response")

    def test_ollama_route_goes_to_ollama_loop(self):
        from core.ollama_tool_loop import EscalateSignal

        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("ollama")
        orch._ollama_tool_loop = MagicMock()
        orch._ollama_tool_loop.run.return_value = "Ollama response"
        result = orch.process_message("play some jazz")
        orch._ollama_tool_loop.run.assert_called_once()
        orch.tool_loop.run.assert_not_called()
        self.assertEqual(result, "Ollama response")

    def test_ollama_escalation_falls_back_to_claude(self):
        from core.ollama_tool_loop import EscalateSignal

        orch = _make_orchestrator_with_mocks()
        orch._tier_router = self._make_router("ollama")
        orch._ollama_tool_loop = MagicMock()
        orch._ollama_tool_loop.run.return_value = EscalateSignal
        result = orch.process_message("something complex")
        orch._ollama_tool_loop.run.assert_called_once()
        orch.tool_loop.run.assert_called_once()
        self.assertEqual(result, "Claude response")

    def test_direct_skill_route_calls_container_manager(self):
        from core.tier_router import RouteResult

        fake_skill = MagicMock()
        orch = _make_orchestrator_with_mocks()
        orch.skills = {"soundcloud_play": fake_skill}
        orch.container_manager.execute_skill.return_value = "Stopped."
        router = MagicMock()
        router.route.return_value = RouteResult(
            tier="direct", skill="soundcloud_play", args={"action": "stop"}
        )
        orch._tier_router = router
        orch._ollama_tool_loop = MagicMock()
        result = orch.process_message("stop")
        orch.container_manager.execute_skill.assert_called_once_with(
            fake_skill, {"action": "stop"}
        )
        orch.tool_loop.run.assert_not_called()
        self.assertEqual(result, "Stopped.")

    def test_direct_close_session_calls_close_session(self):
        from core.tier_router import RouteResult

        orch = _make_orchestrator_with_mocks()
        orch.close_session = MagicMock(return_value="Goodbye!")
        router = MagicMock()
        router.route.return_value = RouteResult(tier="direct", action="close_session")
        orch._tier_router = router
        orch._ollama_tool_loop = MagicMock()
        result = orch.process_message("goodbye")
        orch.close_session.assert_called_once()
        orch.tool_loop.run.assert_not_called()
        self.assertEqual(result, "Goodbye!")


if __name__ == "__main__":
    unittest.main()
