"""Tests for the agentskills.io-compliant SkillValidator."""

import unittest
from pathlib import Path

from core.skill_validator import SkillValidator


class TestNameValidation(unittest.TestCase):
    def setUp(self):
        self.v = SkillValidator()

    def _md(self, name: str) -> str:
        return f"---\nname: {name}\ndescription: Does a thing.\n---\n\nBody.\n"

    def test_kebab_case_accepted(self):
        fm, _ = self.v.validate_markdown(self._md("web-search"), Path("/tmp/web-search"))
        self.assertEqual(fm["name"], "web-search")

    def test_all_lowercase_accepted(self):
        fm, _ = self.v.validate_markdown(self._md("weather"), Path("/tmp/weather"))
        self.assertEqual(fm["name"], "weather")

    def test_uppercase_rejected(self):
        with self.assertRaisesRegex(ValueError, "name.*lowercase"):
            self.v.validate_markdown(self._md("Web-Search"), Path("/tmp/Web-Search"))

    def test_snake_case_rejected(self):
        with self.assertRaisesRegex(ValueError, "name.*hyphen"):
            self.v.validate_markdown(self._md("web_search"), Path("/tmp/web_search"))

    def test_leading_hyphen_rejected(self):
        with self.assertRaisesRegex(ValueError, "name.*hyphen"):
            self.v.validate_markdown(self._md("-web"), Path("/tmp/-web"))

    def test_trailing_hyphen_rejected(self):
        with self.assertRaisesRegex(ValueError, "name.*hyphen"):
            self.v.validate_markdown(self._md("web-"), Path("/tmp/web-"))

    def test_consecutive_hyphens_rejected(self):
        with self.assertRaisesRegex(ValueError, "name.*hyphen"):
            self.v.validate_markdown(self._md("web--search"), Path("/tmp/web--search"))

    def test_name_must_match_parent_dir(self):
        with self.assertRaisesRegex(ValueError, "must match parent directory"):
            self.v.validate_markdown(self._md("web-search"), Path("/tmp/weather"))

    def test_name_over_64_chars_rejected(self):
        long = "a" + "-a" * 32  # 65 chars
        with self.assertRaisesRegex(ValueError, "name.*64"):
            self.v.validate_markdown(self._md(long), Path(f"/tmp/{long}"))


class TestDescriptionValidation(unittest.TestCase):
    def setUp(self):
        self.v = SkillValidator()

    def test_description_over_1024_chars_rejected(self):
        long_desc = "a" * 1025
        raw = f"---\nname: t\ndescription: {long_desc}\n---\n\nBody.\n"
        with self.assertRaisesRegex(ValueError, "description.*1024"):
            self.v.validate_markdown(raw, Path("/tmp/t"))

    def test_description_at_1024_chars_accepted(self):
        desc = "a" * 1024
        raw = f"---\nname: t\ndescription: {desc}\n---\n\nBody.\n"
        fm, _ = self.v.validate_markdown(raw, Path("/tmp/t"))
        self.assertEqual(len(fm["description"]), 1024)


if __name__ == "__main__":
    unittest.main()
