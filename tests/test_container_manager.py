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


if __name__ == "__main__":
    unittest.main()
