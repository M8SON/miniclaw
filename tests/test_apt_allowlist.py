"""Tests for the apt-get package allowlist reader."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.apt_allowlist import DEFAULT_APT_ALLOWLIST, load_apt_allowlist


class TestAptAllowlist(unittest.TestCase):
    def test_defaults(self):
        self.assertIn("curl", DEFAULT_APT_ALLOWLIST)
        self.assertIn("ca-certificates", DEFAULT_APT_ALLOWLIST)
        self.assertIn("git", DEFAULT_APT_ALLOWLIST)

    def test_loads_defaults_when_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"HOME": tmp}):
                allowlist = load_apt_allowlist()
                self.assertEqual(allowlist, DEFAULT_APT_ALLOWLIST)

    def test_user_file_extends_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / ".miniclaw" / "config"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "apt-allowlist.txt").write_text("wget\nlibssl-dev\n# comment\n\n")
            with patch.dict(os.environ, {"HOME": tmp}):
                allowlist = load_apt_allowlist()
                self.assertIn("wget", allowlist)
                self.assertIn("libssl-dev", allowlist)
                self.assertIn("curl", allowlist)


if __name__ == "__main__":
    unittest.main()
