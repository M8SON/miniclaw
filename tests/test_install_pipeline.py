"""Tests for the shared install pipeline."""

import json
import tempfile
import unittest
from pathlib import Path

from core.install_pipeline import (
    InstallDecision,
    InstallPipeline,
    summarize_permissions,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "agentskills"


class AlwaysApprove:
    """Test confirmer that approves all gates."""
    def confirm_gate(self, gate: str, summary: str) -> bool:
        return True


class AlwaysReject:
    def confirm_gate(self, gate: str, summary: str) -> bool:
        return False


class NoopBuilder:
    """Test builder that skips docker build."""
    def build(self, skill_dir: Path, image: str) -> None:
        pass


class NoopReloader:
    def reload(self) -> None:
        pass


class TestSummary(unittest.TestCase):
    def test_permission_summary_flags_credential_env(self):
        summary = summarize_permissions(
            name="foo",
            description="desc",
            config={"env_passthrough": ["ANTHROPIC_API_KEY"], "memory": "128m", "timeout_seconds": 10},
        )
        self.assertTrue(summary.credential_warnings)
        self.assertIn("ANTHROPIC_API_KEY", summary.credential_warnings)


class TestPipelineHappyPath(unittest.TestCase):
    def test_install_good_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "imported"
            install_root.mkdir()

            pipeline = InstallPipeline(
                confirmer=AlwaysApprove(),
                builder=NoopBuilder(),
                reloader=NoopReloader(),
                install_root=install_root,
            )
            decision = pipeline.install_from_path(
                FIXTURES / "good-skill",
                tier="imported",
            )
            self.assertEqual(decision, InstallDecision.INSTALLED)
            installed_dir = install_root / "good-skill"
            self.assertTrue(installed_dir.exists())
            self.assertTrue((installed_dir / ".install.json").exists())

            meta = json.loads((installed_dir / ".install.json").read_text())
            self.assertIn("sha256", meta)
            self.assertIn("installed_at", meta)

    def test_install_rejected_on_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "imported"
            install_root.mkdir()

            pipeline = InstallPipeline(
                confirmer=AlwaysReject(),
                builder=NoopBuilder(),
                reloader=NoopReloader(),
                install_root=install_root,
            )
            decision = pipeline.install_from_path(
                FIXTURES / "good-skill",
                tier="imported",
            )
            self.assertEqual(decision, InstallDecision.CANCELLED)
            self.assertFalse((install_root / "good-skill").exists())

    def test_install_strips_shipped_install_json(self):
        """A malicious skill that ships its own .install.json must not spoof provenance."""
        with tempfile.TemporaryDirectory() as tmp:
            import shutil
            staging_src = Path(tmp) / "good-skill"
            shutil.copytree(FIXTURES / "good-skill", staging_src)
            (staging_src / ".install.json").write_text(
                json.dumps({"source": "FAKE", "sha256": "deadbeef",
                            "installed_at": "1970-01-01T00:00:00",
                            "user_confirmed_env_passthrough": ["ANTHROPIC_API_KEY"]})
            )

            install_root = Path(tmp) / "imported"
            install_root.mkdir()

            pipeline = InstallPipeline(
                confirmer=AlwaysApprove(),
                builder=NoopBuilder(),
                reloader=NoopReloader(),
                install_root=install_root,
            )
            decision = pipeline.install_from_path(staging_src, tier="imported")
            self.assertEqual(decision, InstallDecision.INSTALLED)

            meta = json.loads((install_root / "good-skill" / ".install.json").read_text())
            self.assertNotEqual(meta["source"], "FAKE")
            self.assertNotEqual(meta["sha256"], "deadbeef")
            self.assertEqual(meta["user_confirmed_env_passthrough"], [])


class TestFetch(unittest.TestCase):
    def test_install_from_url_rejects_bad_scheme(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "imported"
            install_root.mkdir()
            pipeline = InstallPipeline(
                confirmer=AlwaysApprove(),
                builder=NoopBuilder(),
                reloader=NoopReloader(),
                install_root=install_root,
            )
            decision = pipeline.install_from_url("ftp://example.com/foo.tgz", tier="imported")
            self.assertEqual(decision, InstallDecision.FAILED)

    def test_staging_dir_is_renamed_to_match_declared_name(self):
        """install_from_path recovers when staging dir name doesn't match SKILL.md name."""
        with tempfile.TemporaryDirectory() as tmp:
            # Simulate a git clone that dumped contents into an arbitrary dir name.
            import shutil
            oddly_named = Path(tmp) / "random-temp-name"
            shutil.copytree(FIXTURES / "good-skill", oddly_named)

            install_root = Path(tmp) / "imported"
            install_root.mkdir()

            pipeline = InstallPipeline(
                confirmer=AlwaysApprove(),
                builder=NoopBuilder(),
                reloader=NoopReloader(),
                install_root=install_root,
            )
            decision = pipeline.install_from_path(oddly_named, tier="imported")
            self.assertEqual(decision, InstallDecision.INSTALLED)
            # Final dir must be named by declared name, not staging name.
            self.assertTrue((install_root / "good-skill").exists())
            self.assertFalse((install_root / "random-temp-name").exists())


if __name__ == "__main__":
    unittest.main()
