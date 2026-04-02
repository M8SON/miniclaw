import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.meta_skill import MetaSkillExecutor


class FakeVoice:
    def __init__(self, transcripts):
        self.transcripts = list(transcripts)
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)

    def listen(self, max_wait_seconds):
        if self.transcripts:
            return self.transcripts.pop(0)
        return ""


class FakeOrchestrator:
    def __init__(self):
        self.reload_count = 0

    def reload_skills(self):
        self.reload_count += 1


class MetaSkillExecutorTests(unittest.TestCase):
    def test_full_install_flow_reloads_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "skills").mkdir()
            (repo_root / "containers").mkdir()

            def write_skill(skill_name, description):
                skill_dir = repo_root / "skills" / skill_name
                container_dir = repo_root / "containers" / skill_name
                skill_dir.mkdir()
                container_dir.mkdir()
                (skill_dir / "SKILL.md").write_text(
                    f"---\nname: {skill_name}\ndescription: Demo skill\n---\n\n# Demo\n",
                    encoding="utf-8",
                )
                (skill_dir / "config.yaml").write_text(
                    "image: miniclaw/demo-skill:latest\nenv_passthrough:\n  - DEMO_KEY\n",
                    encoding="utf-8",
                )
                (container_dir / "Dockerfile").write_text(
                    "FROM miniclaw/base:latest\nCMD [\"python\", \"app.py\"]\n",
                    encoding="utf-8",
                )
                (container_dir / "app.py").write_text("print('ok')\n", encoding="utf-8")
                return True, "ok"

            orchestrator = FakeOrchestrator()
            voice = FakeVoice(["confirm install", "confirm build", "confirm restart"])
            executor = MetaSkillExecutor(
                voice=voice,
                orchestrator=orchestrator,
                run_claude_code=write_skill,
                trigger_build=lambda skill_name: (True, "built"),
            )

            with patch("core.meta_skill.REPO_ROOT", repo_root):
                result = executor.run({"description": "Create demo skill"})

            self.assertEqual(
                result,
                "Skill create demo skill is now active. You can use it right away.",
            )
            self.assertEqual(orchestrator.reload_count, 1)
            self.assertTrue(any("DEMO_KEY" in spoken for spoken in voice.spoken))

    def test_cancel_before_build_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            (repo_root / "skills").mkdir()
            (repo_root / "containers").mkdir()
            cleanup_calls = []

            def write_skill(skill_name, description):
                (repo_root / "skills" / skill_name).mkdir()
                (repo_root / "containers" / skill_name).mkdir()
                (repo_root / "skills" / skill_name / "config.yaml").write_text(
                    "image: miniclaw/demo:latest\n",
                    encoding="utf-8",
                )
                (repo_root / "containers" / skill_name / "Dockerfile").write_text(
                    "FROM miniclaw/base:latest\n",
                    encoding="utf-8",
                )
                return True, "ok"

            def cleanup(skill_name):
                cleanup_calls.append(skill_name)

            executor = MetaSkillExecutor(
                voice=FakeVoice(["confirm install", "cancel"]),
                orchestrator=FakeOrchestrator(),
                run_claude_code=write_skill,
                cleanup=cleanup,
            )

            with patch("core.meta_skill.REPO_ROOT", repo_root):
                result = executor.run({"description": "Create demo skill"})

            self.assertEqual(result, "Skill installation cancelled before build.")
            self.assertEqual(cleanup_calls, ["create_demo_skill"])


if __name__ == "__main__":
    unittest.main()
