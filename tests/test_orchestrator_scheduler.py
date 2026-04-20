import queue
import unittest
from unittest.mock import MagicMock, patch


class OrchestratorSchedulerHooksTests(unittest.TestCase):
    def _make_orchestrator(self):
        with patch("core.orchestrator.anthropic.Anthropic"), \
             patch("core.orchestrator.SkillLoader"), \
             patch("core.orchestrator.ContainerManager"), \
             patch("core.orchestrator.MemoryProvider"), \
             patch("core.orchestrator.PromptBuilder"), \
             patch("core.orchestrator.SkillSelector"), \
             patch("core.orchestrator.ToolLoop"):
            from core.orchestrator import Orchestrator
            return Orchestrator(anthropic_api_key="test-key")

    def test_exposes_scheduled_fire_queue(self):
        orch = self._make_orchestrator()
        self.assertIsInstance(orch.scheduled_fire_queue, queue.Queue)

    def test_exposes_pending_next_wake_announcements(self):
        orch = self._make_orchestrator()
        self.assertEqual(orch.pending_next_wake_announcements, [])

    def test_drain_pending_announcements_returns_and_clears_fifo(self):
        orch = self._make_orchestrator()
        orch.pending_next_wake_announcements.extend(["a", "b", "c"])
        drained = orch.drain_pending_announcements()
        self.assertEqual(drained, ["a", "b", "c"])
        self.assertEqual(orch.pending_next_wake_announcements, [])


class ProcessScheduledFireTests(unittest.TestCase):
    def _make_orchestrator_with_tool_loop(self, tool_loop_result="the weather is sunny"):
        with patch("core.orchestrator.anthropic.Anthropic"), \
             patch("core.orchestrator.SkillLoader"), \
             patch("core.orchestrator.ContainerManager"), \
             patch("core.orchestrator.MemoryProvider"), \
             patch("core.orchestrator.PromptBuilder") as pb, \
             patch("core.orchestrator.SkillSelector"), \
             patch("core.orchestrator.ToolLoop") as tl:
            pb.return_value.build.return_value = "SYSTEM"
            tl.return_value.run.return_value = tool_loop_result
            from core.orchestrator import Orchestrator
            orch = Orchestrator(anthropic_api_key="test-key")
            return orch

    def _make_fire(self, delivery):
        from core.scheduler import ScheduleEntry, ScheduledFire
        from datetime import datetime
        entry = ScheduleEntry.new(
            cron="0 8 * * *", prompt="tell me the weather", delivery=delivery
        )
        return ScheduledFire(entry=entry, fired_at=datetime.now())

    def test_next_wake_appends_to_pending(self):
        orch = self._make_orchestrator_with_tool_loop("weather update")
        orch.process_scheduled_fire(self._make_fire("next_wake"))
        self.assertEqual(orch.pending_next_wake_announcements, ["weather update"])

    def test_immediate_when_idle_calls_speak(self):
        orch = self._make_orchestrator_with_tool_loop("weather update")
        speak_calls = []
        orch.speak_callback = lambda text: speak_calls.append(text)
        orch.process_scheduled_fire(self._make_fire("immediate"))
        self.assertEqual(speak_calls, ["weather update"])
        self.assertEqual(orch.pending_next_wake_announcements, [])

    def test_immediate_downgrades_when_conversation_active(self):
        orch = self._make_orchestrator_with_tool_loop("weather update")
        orch.speak_callback = lambda text: None
        orch.is_conversation_active = lambda: True
        orch.process_scheduled_fire(self._make_fire("immediate"))
        self.assertEqual(orch.pending_next_wake_announcements, ["weather update"])

    def test_silent_logs_and_does_nothing_user_facing(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "scheduler.log"
            orch = self._make_orchestrator_with_tool_loop("web check done")
            orch.scheduler_log_path = log_path
            speak_calls = []
            orch.speak_callback = lambda text: speak_calls.append(text)
            orch.process_scheduled_fire(self._make_fire("silent"))
            self.assertEqual(speak_calls, [])
            self.assertEqual(orch.pending_next_wake_announcements, [])
            self.assertTrue(log_path.exists())
            self.assertIn("web check done", log_path.read_text(encoding="utf-8"))

    def test_tool_loop_failure_does_not_raise(self):
        orch = self._make_orchestrator_with_tool_loop("unused")
        orch.tool_loop.run.side_effect = RuntimeError("API down")
        orch.speak_callback = lambda text: None
        orch.process_scheduled_fire(self._make_fire("immediate"))
        self.assertEqual(orch.pending_next_wake_announcements, [])


if __name__ == "__main__":
    unittest.main()
