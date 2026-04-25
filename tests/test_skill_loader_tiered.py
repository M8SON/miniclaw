"""Tests for tier-aware SkillLoader with three search paths."""

import json
import os
import tempfile
import unittest
from pathlib import Path

import yaml

from core.skill_loader import SkillLoader
from core.skill_policy import TIER_BUNDLED, TIER_AUTHORED, TIER_IMPORTED, TIER_DEV


def _write_skill(
    parent: Path,
    name: str,
    *,
    with_dockerfile: bool = True,
    with_install_json: bool = False,
) -> Path:
    skill_dir = parent / name
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test skill {name}.\n---\n\n"
        "## Inputs\n\n```yaml\ntype: object\nproperties:\n  query:\n    type: string\n"
        "required: [query]\n```\n\nBody.\n"
    )
    (skill_dir / "config.yaml").write_text(
        yaml.dump({
            "type": "docker",
            "image": f"miniclaw/{name}:latest",
            "env_passthrough": [],
            "timeout_seconds": 15,
            "devices": [],
        })
    )
    if with_dockerfile:
        (skill_dir / "scripts" / "Dockerfile").write_text(
            "FROM miniclaw/base:latest\nCMD [\"python\", \"app.py\"]\n"
        )
        (skill_dir / "scripts" / "app.py").write_text("print('ok')\n")
    if with_install_json:
        (skill_dir / ".install.json").write_text(json.dumps({
            "source": "https://example.com/" + name,
            "sha256": "x",
            "installed_at": "2026-04-23T00:00:00",
            "user_confirmed_env_passthrough": [],
        }))
    return skill_dir


class TestThreePaths(unittest.TestCase):
    def test_bundled_authored_imported_all_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            bundled = tmp_p / "bundled"; bundled.mkdir()
            authored = tmp_p / "authored"; authored.mkdir()
            imported = tmp_p / "imported"; imported.mkdir()

            _write_skill(bundled, "alpha")
            _write_skill(authored, "beta", with_install_json=False)
            _write_skill(imported, "gamma", with_install_json=False)

            loader = SkillLoader(search_paths=[bundled, authored, imported])
            skills = loader.load_all()

            self.assertIn("alpha", skills)
            self.assertIn("beta", skills)
            self.assertIn("gamma", skills)
            self.assertEqual(skills["alpha"].tier, TIER_BUNDLED)
            self.assertEqual(skills["beta"].tier, TIER_AUTHORED)
            self.assertEqual(skills["gamma"].tier, TIER_IMPORTED)

    def test_name_collision_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            bundled = tmp_p / "bundled"; bundled.mkdir()
            imported = tmp_p / "imported"; imported.mkdir()
            _write_skill(bundled, "foo")
            _write_skill(imported, "foo", with_install_json=False)

            loader = SkillLoader(search_paths=[bundled, imported])
            skills = loader.load_all()

            self.assertEqual(skills["foo"].tier, TIER_BUNDLED)
            self.assertTrue(any("collision" in info["reason"] for info in loader.invalid_skills.values()))

    def test_dev_mode_symlink_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            actual = tmp_p / "actual_home"; actual.mkdir()
            _write_skill(actual, "dev-skill")

            imported = tmp_p / "imported"; imported.mkdir()
            os.symlink(actual / "dev-skill", imported / "dev-skill")

            loader = SkillLoader(search_paths=[imported])
            skills = loader.load_all()
            self.assertEqual(skills["dev-skill"].tier, TIER_DEV)


def test_skill_exposes_frontmatter_dict():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        bundled = tmp_p / "bundled"; bundled.mkdir()
        skill_dir = bundled / "alpha"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: alpha\ndescription: x.\n"
            "metadata:\n  miniclaw:\n    self_update:\n      allow_body: true\n"
            "---\n\n"
            "## Inputs\n\n```yaml\ntype: object\nproperties: {}\nrequired: []\n```\n\nBody.\n"
        )
        (skill_dir / "config.yaml").write_text(yaml.dump({
            "type": "docker",
            "image": "miniclaw/alpha:latest",
            "env_passthrough": [],
            "timeout_seconds": 15,
            "devices": [],
        }))
        (skill_dir / "scripts" / "Dockerfile").write_text(
            "FROM miniclaw/base:latest\nCMD [\"python\", \"app.py\"]\n"
        )
        (skill_dir / "scripts" / "app.py").write_text("print('ok')\n")

        loader = SkillLoader(search_paths=[bundled])
        skills = loader.load_all()
        s = skills["alpha"]
        assert hasattr(s, "frontmatter")
        assert s.frontmatter["metadata"]["miniclaw"]["self_update"]["allow_body"] is True


if __name__ == "__main__":
    unittest.main()
