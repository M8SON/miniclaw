import os
import tempfile
import unittest
import json
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

    def test_open_dashboard_includes_default_hazards_in_dashboard_config(self):
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
        self.assertEqual(
            dashboard_cfg["hazards"],
            {
                "enabled": True,
                "limit": 3,
                "min_score": 40,
                "days": 14,
                "fetch_limit": 20,
                "categories": [
                    "wildfires",
                    "severeStorms",
                    "volcanoes",
                    "floods",
                    "earthquakes",
                    "landslides",
                    "extremeTemperatures",
                    "dustHaze",
                ],
            },
        )


if __name__ == "__main__":
    unittest.main()
