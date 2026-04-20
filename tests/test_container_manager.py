import builtins
import importlib
import os
import tempfile
import unittest
import json
import sys
from pathlib import Path
from unittest.mock import patch


def _load_container_manager(*, missing_flask: bool = False):
    sys.modules.pop("core.container_manager", None)
    sys.modules.pop("containers.dashboard.app", None)
    sys.modules.pop("containers.dashboard.dashboard_defaults", None)

    if not missing_flask:
        return importlib.import_module("core.container_manager")

    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "flask":
            raise ModuleNotFoundError("No module named 'flask'")
        return original_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=fake_import):
        return importlib.import_module("core.container_manager")


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
    def test_container_manager_imports_without_flask_installed(self):
        container_manager = _load_container_manager(missing_flask=True)

        self.assertTrue(hasattr(container_manager, "ContainerManager"))

    def test_container_manager_import_guard_ignores_cached_dashboard_app_module(self):
        sentinel = object()
        sys.modules["containers.dashboard.app"] = sentinel
        sys.modules["containers.dashboard.dashboard_defaults"] = sentinel

        container_manager = _load_container_manager(missing_flask=True)

        self.assertTrue(hasattr(container_manager, "ContainerManager"))
        self.assertIsNot(sys.modules.get("containers.dashboard.app"), sentinel)
        self.assertIsNot(sys.modules.get("containers.dashboard.dashboard_defaults"), sentinel)

    def test_set_env_var_writes_repo_root_env(self):
        ContainerManager = _load_container_manager().ContainerManager
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
        ContainerManager = _load_container_manager().ContainerManager
        manager = ContainerManager()
        manager._orchestrator = FakeOrchestrator({"HOMEBRIDGE_USERNAME"})

        result = manager._execute_set_env_var({"key": "NOT_ALLOWED", "value": "x"})

        self.assertIn("not required by any unavailable skill", result)

    def test_verify_docker_reports_permission_denied(self):
        container_manager = _load_container_manager()
        ContainerManager = container_manager.ContainerManager
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

    def test_open_dashboard_includes_default_hazards_in_dashboard_config(self):
        container_manager = _load_container_manager()
        ContainerManager = container_manager.ContainerManager
        dashboard_defaults = importlib.import_module("containers.dashboard.dashboard_defaults")
        manager = ContainerManager()
        manager.docker_available = True

        class Result:
            returncode = 0
            stdout = "container123\n"
            stderr = ""

        class DummyTimer:
            def __init__(self, interval, fn):
                self.interval = interval
                self.fn = fn
                self.daemon = False
                self.started = False

            def cancel(self):
                return None

            def start(self):
                self.started = True

        with tempfile.TemporaryDirectory() as tmp:
            miniclaw_home = Path(tmp)
            dashboard_lock = miniclaw_home / "dashboard.lock"
            captured = {}

            def fake_run(cmd, capture_output=False, text=False, timeout=None):
                if cmd[:3] == ["docker", "run", "-d"]:
                    captured["docker_cmd"] = cmd
                    return Result()
                if cmd[:2] == ["which", "chromium-browser"]:
                    class WhichResult:
                        returncode = 0
                        stdout = "/usr/bin/chromium-browser\n"
                    return WhichResult()
                raise AssertionError(f"Unexpected command: {cmd}")

            class DummyProcess:
                pid = 4321

            def fake_urlopen(url, timeout=0):
                class Response:
                    def read(self):
                        return b""
                return Response()

            with patch("core.container_manager.Path.home", return_value=miniclaw_home), \
                 patch("core.container_manager.DASHBOARD_LOCK", dashboard_lock), \
                 patch("core.container_manager.subprocess.run", side_effect=fake_run), \
                 patch("core.container_manager.subprocess.Popen", return_value=DummyProcess()), \
                 patch("core.container_manager.urllib.request.urlopen", side_effect=fake_urlopen), \
                 patch("core.container_manager.threading.Timer", side_effect=DummyTimer):
                result = manager._open_dashboard(["news", "weather"], 5, "Burlington,VT", ["osint"], [])

        self.assertEqual(result, "Dashboard is up with news, weather.")
        docker_cmd = captured["docker_cmd"]
        cfg_arg = next(arg for arg in docker_cmd if arg.startswith("DASHBOARD_CONFIG="))
        dashboard_cfg = json.loads(cfg_arg.split("=", 1)[1])

        self.assertIn("hazards", dashboard_cfg)
        self.assertEqual(dashboard_cfg["hazards"], dashboard_defaults.default_hazard_config(enabled=True))


class ScheduleNativeHandlerTests(unittest.TestCase):
    def setUp(self):
        from core.scheduler import SchedulesStore
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SchedulesStore(Path(self._tmp.name) / "schedules.yaml")
        ContainerManager = _load_container_manager().ContainerManager
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
