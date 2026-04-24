"""Test the migrate-to-agentskills.py script against a fixture copy."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATE_SCRIPT = REPO_ROOT / "scripts" / "migrate-to-agentskills.py"


def _write_old_skill(root: Path, old_dir_name: str, *, declared_name: str,
                     with_container: bool = True, requires_env: list[str] | None = None):
    skill_dir = root / "skills" / old_dir_name
    skill_dir.mkdir(parents=True)
    fm = {"name": declared_name, "description": f"Old-style {declared_name} skill."}
    if requires_env:
        fm["requires"] = {"env": requires_env}
    (skill_dir / "SKILL.md").write_text(
        "---\n" + yaml.dump(fm, sort_keys=False) + "---\n\nBody.\n"
    )
    (skill_dir / "config.yaml").write_text(yaml.dump({
        "image": f"miniclaw/{declared_name}:latest",
        "env_passthrough": requires_env or [],
        "timeout_seconds": 15,
        "devices": [],
    }))
    if with_container:
        container_dir = root / "containers" / old_dir_name
        container_dir.mkdir(parents=True)
        (container_dir / "Dockerfile").write_text(
            "FROM miniclaw/base:latest\nCOPY app.py /app/app.py\nCMD [\"python\", \"/app/app.py\"]\n"
        )
        (container_dir / "app.py").write_text("print('ok')\n")


class TestMigration(unittest.TestCase):
    def test_rename_and_restructure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_old_skill(root, "web_search", declared_name="search_web",
                             requires_env=["BRAVE_API_KEY"])
            _write_old_skill(root, "dashboard", declared_name="dashboard",
                             with_container=False)  # native

            result = subprocess.run(
                [sys.executable, str(MIGRATE_SCRIPT), "--repo-root", str(root)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            # Web search renamed
            self.assertTrue((root / "skills" / "web-search").exists())
            self.assertFalse((root / "skills" / "web_search").exists())
            self.assertTrue((root / "skills" / "web-search" / "SKILL.md").exists())
            self.assertTrue((root / "skills" / "web-search" / "scripts" / "Dockerfile").exists())
            self.assertTrue((root / "skills" / "web-search" / "scripts" / "app.py").exists())

            # Frontmatter migrated
            skill_md = (root / "skills" / "web-search" / "SKILL.md").read_text()
            self.assertIn("name: web-search", skill_md)
            fm_body = skill_md.split("---")[1]
            # Old name must not be the declared name anymore.
            self.assertNotIn("name: search_web", fm_body)
            # Top-level requires gone.
            self.assertNotIn("\nrequires:", fm_body)
            self.assertIn("metadata:", skill_md)
            self.assertIn("miniclaw:", skill_md)
            self.assertIn("BRAVE_API_KEY", skill_md)

            # containers/ removed (or at most containers/base/ left)
            containers_root = root / "containers"
            if containers_root.exists():
                self.assertEqual(
                    [p.name for p in containers_root.iterdir()],
                    [],
                    msg="only containers/base/ should remain",
                )

            # Dashboard unchanged name
            self.assertTrue((root / "skills" / "dashboard").exists())


if __name__ == "__main__":
    unittest.main()
