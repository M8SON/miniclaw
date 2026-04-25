"""Unit tests for the skill_self_update handler."""

import unittest
from pathlib import Path
import tempfile

import yaml

from core.skill_self_update import (
    SelfUpdateResult,
    apply_hint,
)


def _write_skill(parent: Path, name: str, *, allow_body: bool, body: str = None) -> Path:
    skill_dir = parent / name
    skill_dir.mkdir(parents=True)
    fm = {
        "name": name,
        "description": f"Test skill {name}.",
        "metadata": {
            "miniclaw": {
                "self_update": {"allow_body": allow_body},
            }
        },
    }
    body = body or "# Test\n\nWhen the user says hello.\n"
    (skill_dir / "SKILL.md").write_text(
        "---\n" + yaml.dump(fm, sort_keys=False) + "---\n\n" + body
    )
    (skill_dir / "config.yaml").write_text(
        yaml.dump({"type": "docker", "image": f"miniclaw/{name}:latest"})
    )
    return skill_dir


class _StubLoader:
    """Minimal SkillLoader stub for tests."""
    def __init__(self, skills_by_name: dict):
        self.skills = skills_by_name


class _StubSkill:
    def __init__(self, name, tier, skill_dir, frontmatter):
        self.name = name
        self.tier = tier
        self.skill_dir = str(skill_dir)
        self._frontmatter = frontmatter

    @property
    def frontmatter(self):
        return self._frontmatter


def _make_loader(tmp: Path, name: str, *, tier: str, allow_body: bool):
    skill_dir = _write_skill(tmp, name, allow_body=allow_body)
    fm = {"metadata": {"miniclaw": {"self_update": {"allow_body": allow_body}}}}
    skill = _StubSkill(name, tier, skill_dir, fm)
    return _StubLoader({name: skill}), skill_dir


class TestEligibility(unittest.TestCase):
    def test_skill_not_found_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader = _StubLoader({})
            r = apply_hint(loader, "ghost", "- new bullet", "rationale", turn_id="t1")
            self.assertEqual(r.status, "rejected")
            self.assertIn("not found", r.reason.lower())

    def test_imported_tier_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = _make_loader(Path(tmp), "foo", tier="imported", allow_body=True)
            r = apply_hint(loader, "foo", "- bullet", "rationale", turn_id="t1")
            self.assertEqual(r.status, "rejected")
            self.assertIn("imported", r.reason.lower())

    def test_allow_body_false_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = _make_loader(Path(tmp), "foo", tier="bundled", allow_body=False)
            r = apply_hint(loader, "foo", "- bullet", "rationale", turn_id="t1")
            self.assertEqual(r.status, "rejected")
            self.assertIn("allow_body", r.reason.lower())

    def test_bundled_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = _make_loader(Path(tmp), "foo", tier="bundled", allow_body=True)
            r = apply_hint(loader, "foo", "- new phrasing", "rationale", turn_id="t1")
            self.assertEqual(r.status, "ok")

    def test_authored_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = _make_loader(Path(tmp), "foo", tier="authored", allow_body=True)
            r = apply_hint(loader, "foo", "- new phrasing", "rationale", turn_id="t1")
            self.assertEqual(r.status, "ok")

    def test_dev_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = _make_loader(Path(tmp), "foo", tier="dev", allow_body=True)
            r = apply_hint(loader, "foo", "- new phrasing", "rationale", turn_id="t1")
            self.assertEqual(r.status, "ok")


class TestAdditionStructuralChecks(unittest.TestCase):
    def _setup(self, tmp):
        return _make_loader(Path(tmp), "foo", tier="bundled", allow_body=True)

    def test_addition_too_long_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = self._setup(tmp)
            r = apply_hint(loader, "foo", "x" * 501, "rationale", turn_id="t1")
            self.assertEqual(r.status, "rejected")
            self.assertIn("500", r.reason)

    def test_empty_addition_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = self._setup(tmp)
            r = apply_hint(loader, "foo", "   ", "rationale", turn_id="t1")
            self.assertEqual(r.status, "rejected")

    def test_frontmatter_delimiter_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = self._setup(tmp)
            r = apply_hint(loader, "foo", "- bullet\n---\nname: evil", "rat", turn_id="t1")
            self.assertEqual(r.status, "rejected")
            self.assertIn("frontmatter", r.reason.lower())

    def test_input_schema_header_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = self._setup(tmp)
            r = apply_hint(loader, "foo", "## Inputs\n\nstuff", "rat", turn_id="t1")
            self.assertEqual(r.status, "rejected")
            self.assertIn("inputs", r.reason.lower())

    def test_top_level_heading_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = self._setup(tmp)
            r = apply_hint(loader, "foo", "# Big Heading", "rat", turn_id="t1")
            self.assertEqual(r.status, "rejected")
            self.assertIn("heading", r.reason.lower())


class TestAlreadyCovered(unittest.TestCase):
    def test_addition_already_in_body_is_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = (
                "## When to use\n"
                "- already there phrasing\n"
            )
            skill_dir = _write_skill(Path(tmp), "foo", allow_body=True, body=body)
            fm = {"metadata": {"miniclaw": {"self_update": {"allow_body": True}}}}
            skill = _StubSkill("foo", "bundled", skill_dir, fm)
            loader = _StubLoader({"foo": skill})
            r = apply_hint(loader, "foo", "- already there phrasing", "rat", turn_id="t1")
            self.assertEqual(r.status, "no-op")
            self.assertIn("already", r.reason.lower())


class TestSectionManagement(unittest.TestCase):
    def test_creates_auto_section_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            skill_dir = _write_skill(tmp_p, "foo", allow_body=True)
            fm = {"metadata": {"miniclaw": {"self_update": {"allow_body": True}}}}
            skill = _StubSkill("foo", "bundled", skill_dir, fm)
            loader = _StubLoader({"foo": skill})

            apply_hint(loader, "foo", "- first auto hint", "rat", turn_id="t1")

            content = (skill_dir / "SKILL.md").read_text()
            self.assertIn("## Auto-learned routing hints", content)
            self.assertIn("- first auto hint", content)

    def test_appends_to_existing_auto_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            body = (
                "# Test\n\n"
                "## Auto-learned routing hints\n\n"
                "- existing hint\n"
            )
            skill_dir = _write_skill(tmp_p, "foo", allow_body=True, body=body)
            fm = {"metadata": {"miniclaw": {"self_update": {"allow_body": True}}}}
            skill = _StubSkill("foo", "bundled", skill_dir, fm)
            loader = _StubLoader({"foo": skill})

            apply_hint(loader, "foo", "- second hint", "rat", turn_id="t1")

            content = (skill_dir / "SKILL.md").read_text()
            self.assertIn("- existing hint", content)
            self.assertIn("- second hint", content)
            self.assertEqual(content.count("## Auto-learned routing hints"), 1)

    def test_fifo_drops_oldest_at_31st_bullet(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            existing = "\n".join(f"- bullet {i}" for i in range(30))
            body = (
                "# Test\n\n"
                "## Auto-learned routing hints\n\n"
                f"{existing}\n"
            )
            skill_dir = _write_skill(tmp_p, "foo", allow_body=True, body=body)
            fm = {"metadata": {"miniclaw": {"self_update": {"allow_body": True}}}}
            skill = _StubSkill("foo", "bundled", skill_dir, fm)
            loader = _StubLoader({"foo": skill})

            r = apply_hint(loader, "foo", "- bullet 30", "rat", turn_id="t1")
            self.assertEqual(r.status, "ok")

            content = (skill_dir / "SKILL.md").read_text()
            self.assertNotIn("- bullet 0\n", content)
            self.assertIn("- bullet 30", content)
            self.assertIn("- bullet 1\n", content)

    def test_section_inserted_before_subsequent_section(self):
        """If body has e.g. ## Other after the proposed insert location, preserve it."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            body = (
                "# Test\n\n"
                "## When to use\n\n"
                "- a\n\n"
                "## Auto-learned routing hints\n\n"
                "- existing\n\n"
                "## After section\n\n"
                "tail content\n"
            )
            skill_dir = _write_skill(tmp_p, "foo", allow_body=True, body=body)
            fm = {"metadata": {"miniclaw": {"self_update": {"allow_body": True}}}}
            skill = _StubSkill("foo", "bundled", skill_dir, fm)
            loader = _StubLoader({"foo": skill})

            apply_hint(loader, "foo", "- new", "rat", turn_id="t1")

            content = (skill_dir / "SKILL.md").read_text()
            self.assertIn("- existing", content)
            self.assertIn("- new", content)
            self.assertIn("## After section", content)
            self.assertIn("tail content", content)


if __name__ == "__main__":
    unittest.main()
