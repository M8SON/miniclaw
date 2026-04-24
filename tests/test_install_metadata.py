"""Tests for the .install.json provenance sidecar."""

import json
import tempfile
import unittest
from pathlib import Path

from core.install_metadata import (
    InstallMetadata,
    compute_skill_sha256,
    read_metadata,
    write_metadata,
)


class TestSha256(unittest.TestCase):
    def test_sha256_covers_all_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "foo"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("a")
            (skill_dir / "config.yaml").write_text("b")
            (skill_dir / "scripts").mkdir()
            (skill_dir / "scripts" / "app.py").write_text("c")

            sha1 = compute_skill_sha256(skill_dir)
            (skill_dir / "scripts" / "app.py").write_text("c2")
            sha2 = compute_skill_sha256(skill_dir)
            self.assertNotEqual(sha1, sha2)

    def test_sha256_excludes_install_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "foo"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("a")
            sha1 = compute_skill_sha256(skill_dir)
            (skill_dir / ".install.json").write_text('{"source": "x"}')
            sha2 = compute_skill_sha256(skill_dir)
            self.assertEqual(sha1, sha2)


class TestReadWrite(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "foo"
            skill_dir.mkdir()

            meta = InstallMetadata(
                source="https://github.com/user/pdf-tools",
                sha256="abc123",
                installed_at="2026-04-23T12:00:00",
                user_confirmed_env_passthrough=["OPENWEATHER_API_KEY"],
            )
            write_metadata(skill_dir, meta)
            loaded = read_metadata(skill_dir)
            self.assertEqual(loaded.source, meta.source)
            self.assertEqual(loaded.sha256, meta.sha256)
            self.assertEqual(
                loaded.user_confirmed_env_passthrough,
                meta.user_confirmed_env_passthrough,
            )

    def test_read_returns_none_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "foo"
            skill_dir.mkdir()
            self.assertIsNone(read_metadata(skill_dir))


if __name__ == "__main__":
    unittest.main()
