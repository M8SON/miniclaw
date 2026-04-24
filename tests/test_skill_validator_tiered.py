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


from core.skill_eligibility import SkillEligibility
from core.skill_policy import TIER_BUNDLED, TIER_AUTHORED, TIER_IMPORTED


class TestTieredConfigValidation(unittest.TestCase):
    def setUp(self):
        self.v = SkillValidator()

    def _base_config(self):
        return {
            "type": "docker",
            "image": "miniclaw/foo:latest",
            "env_passthrough": [],
            "timeout_seconds": 15,
            "devices": [],
        }

    def test_native_rejected_for_authored(self):
        cfg = {"type": "native"}
        with self.assertRaisesRegex(ValueError, "native.*not allowed"):
            self.v.validate_execution_config(cfg, tier=TIER_AUTHORED, skill_name="foo")

    def test_native_rejected_for_imported(self):
        cfg = {"type": "native"}
        with self.assertRaisesRegex(ValueError, "native.*not allowed"):
            self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")

    def test_native_accepted_for_bundled(self):
        cfg = {"type": "native"}
        result = self.v.validate_execution_config(cfg, tier=TIER_BUNDLED, skill_name="foo")
        self.assertEqual(result["type"], "native")

    def test_memory_clamp_imported(self):
        cfg = self._base_config()
        cfg["memory"] = "1g"
        with self.assertRaisesRegex(ValueError, "memory.*exceeds"):
            self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")

    def test_memory_under_clamp_authored(self):
        cfg = self._base_config()
        cfg["memory"] = "512m"
        result = self.v.validate_execution_config(cfg, tier=TIER_AUTHORED, skill_name="foo")
        self.assertEqual(result["memory"], "512m")

    def test_timeout_clamp_imported(self):
        cfg = self._base_config()
        cfg["timeout_seconds"] = 90
        with self.assertRaisesRegex(ValueError, "timeout.*exceeds"):
            self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")

    def test_cpus_clamp_imported(self):
        cfg = self._base_config()
        cfg["cpus"] = 2.0
        with self.assertRaisesRegex(ValueError, "cpus.*exceeds"):
            self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")

    def test_disallowed_device_imported(self):
        cfg = self._base_config()
        cfg["devices"] = ["/dev/kmsg"]
        with self.assertRaisesRegex(ValueError, "device.*not allowed"):
            self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")

    def test_allowed_device_imported(self):
        cfg = self._base_config()
        cfg["devices"] = ["/dev/snd", "/dev/i2c-1"]
        result = self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")
        self.assertEqual(result["devices"], ["/dev/snd", "/dev/i2c-1"])

    def test_any_device_bundled(self):
        cfg = self._base_config()
        cfg["devices"] = ["/dev/kmsg"]
        result = self.v.validate_execution_config(cfg, tier=TIER_BUNDLED, skill_name="foo")
        self.assertEqual(result["devices"], ["/dev/kmsg"])

    def test_unscoped_volume_imported(self):
        cfg = self._base_config()
        cfg["volumes"] = ["~:/host"]
        with self.assertRaisesRegex(ValueError, "volume.*scope"):
            self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")

    def test_scoped_volume_imported(self):
        cfg = self._base_config()
        cfg["volumes"] = ["~/.miniclaw/foo:/data"]
        result = self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")
        self.assertEqual(result["volumes"], ["~/.miniclaw/foo:/data"])


class TestRequiresLocation(unittest.TestCase):
    def setUp(self):
        self.elig = SkillEligibility()

    def test_requires_read_from_metadata_miniclaw(self):
        fm = {
            "name": "foo",
            "description": "x",
            "metadata": {
                "miniclaw": {"requires": {"env": ["NEVER_SET_XYZ_VAR"]}}
            },
        }
        reason, missing = self.elig.check(fm)
        self.assertIn("NEVER_SET_XYZ_VAR", reason)
        self.assertEqual(missing, ["NEVER_SET_XYZ_VAR"])

    def test_top_level_requires_ignored_after_migration(self):
        fm = {
            "name": "foo",
            "description": "x",
            "requires": {"env": ["NEVER_SET_XYZ_VAR"]},  # old location
        }
        reason, missing = self.elig.check(fm)
        # Old top-level requires is NOT read anymore.
        self.assertIsNone(reason)
        self.assertEqual(missing, [])

    def test_empty_metadata_miniclaw_is_fine(self):
        fm = {"name": "foo", "description": "x", "metadata": {}}
        reason, missing = self.elig.check(fm)
        self.assertIsNone(reason)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
