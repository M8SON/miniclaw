import unittest
from pathlib import Path


RUN_SH = Path(__file__).resolve().parent.parent / "run.sh"


class RunShTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = RUN_SH.read_text(encoding="utf-8")

    def test_install_system_deps_includes_portaudio_dev(self):
        self.assertIn("portaudio19-dev", self.text)

    def test_dependency_probe_checks_voice_and_memory_modules(self):
        self.assertIn('import anthropic, chromadb, dotenv, pyaudio, whisper, yaml', self.text)


if __name__ == "__main__":
    unittest.main()
