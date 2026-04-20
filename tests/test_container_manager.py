import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.container_manager import ContainerManager


class FakeSkillLoader:
    def __init__(self, missing_env_vars):
        self._missing_env_vars = set(missing_env_vars)
        self.skipped_skills = {"homebridge": {"reason": "missing env vars"}}

    def get_missing_env_vars(self):
        return set(self._missing_env_vars)


class FakeOrchestrator:
    def __init__(self, missing_env_vars):
        self.skill_loader = FakeSkillLoader(missing_env_vars)
        self.reload_count = 0

    def reload_skills(self):
        self.reload_count += 1


class ContainerManagerTests(unittest.TestCase):
    def test_set_env_var_writes_repo_root_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            env_path = repo_root / ".env"
            env_path.write_text("EXISTING=1\n", encoding="utf-8")

            manager = ContainerManager()
            manager._orchestrator = FakeOrchestrator({"HOMEBRIDGE_URL"})

            with patch("core.container_manager.REPO_ROOT", repo_root):
                result = manager._execute_set_env_var(
                    {"key": "HOMEBRIDGE_URL", "value": "http://example.invalid"}
                )

            self.assertIn("Set HOMEBRIDGE_URL", result)
            self.assertIn("HOMEBRIDGE_URL=http://example.invalid\n", env_path.read_text(encoding="utf-8"))
            self.assertEqual(manager._orchestrator.reload_count, 1)

    def test_set_env_var_rejects_unavailable_key(self):
        manager = ContainerManager()
        manager._orchestrator = FakeOrchestrator({"HOMEBRIDGE_USERNAME"})

        result = manager._execute_set_env_var({"key": "NOT_ALLOWED", "value": "x"})

        self.assertIn("not required by any unavailable skill", result)

    def test_verify_docker_reports_permission_denied(self):
        manager = ContainerManager()

        class Result:
            returncode = 1
            stderr = b"permission denied while trying to connect to the docker daemon socket"

        with patch("core.container_manager.subprocess.run", return_value=Result()):
            manager._verify_docker()

        self.assertFalse(manager.docker_available)
        self.assertEqual(
            manager.docker_error,
            "Docker is installed but this session cannot access the daemon",
        )


class ScheduleNativeHandlerTests(unittest.TestCase):
    def setUp(self):
        from core.scheduler import SchedulesStore
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SchedulesStore(Path(self._tmp.name) / "schedules.yaml")
        self.manager = ContainerManager()
        self.manager._schedules_store = self.store

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_returns_ok_and_persists(self):
        import json
        out = self.manager._execute_schedule({
            "action": "create",
            "cron": "0 8 * * *",
            "prompt": "tell me the weather",
            "delivery": "next_wake",
            "label": "morning briefing",
        })
        payload = json.loads(out)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(self.store.list_raw()[0].label, "morning briefing")

    def test_create_rejects_bad_cron(self):
        import json
        out = self.manager._execute_schedule({
            "action": "create",
            "cron": "not a cron",
            "prompt": "p",
            "delivery": "next_wake",
        })
        payload = json.loads(out)
        self.assertEqual(payload["status"], "error")
        self.assertIn("cron", payload["message"])

    def test_list_returns_all_enabled(self):
        import json
        from core.scheduler import ScheduleEntry
        self.store.create(ScheduleEntry.new(
            cron="0 8 * * *", prompt="a", delivery="next_wake", label="one",
        ))
        self.store.create(ScheduleEntry.new(
            cron="0 9 * * *", prompt="b", delivery="next_wake", label="two",
        ))
        out = self.manager._execute_schedule({"action": "list"})
        payload = json.loads(out)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(len(payload["schedules"]), 2)

    def test_cancel_by_label_removes(self):
        import json
        from core.scheduler import ScheduleEntry
        self.store.create(ScheduleEntry.new(
            cron="0 8 * * *", prompt="a", delivery="next_wake", label="one",
        ))
        out = self.manager._execute_schedule({
            "action": "cancel", "id_or_label": "one",
        })
        payload = json.loads(out)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(self.store.list_all(), [])

    def test_cancel_missing_returns_error(self):
        import json
        out = self.manager._execute_schedule({
            "action": "cancel", "id_or_label": "nope",
        })
        payload = json.loads(out)
        self.assertEqual(payload["status"], "error")

    def test_modify_updates_cron(self):
        import json
        from core.scheduler import ScheduleEntry
        self.store.create(ScheduleEntry.new(
            cron="0 8 * * *", prompt="a", delivery="next_wake", label="one",
        ))
        out = self.manager._execute_schedule({
            "action": "modify", "id_or_label": "one", "cron": "0 9 * * *",
        })
        payload = json.loads(out)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(self.store.list_raw()[0].cron, "0 9 * * *")

    def test_unknown_action_returns_error(self):
        import json
        out = self.manager._execute_schedule({"action": "bogus"})
        payload = json.loads(out)
        self.assertEqual(payload["status"], "error")


if __name__ == "__main__":
    unittest.main()
