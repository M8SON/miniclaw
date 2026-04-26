import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.install_pipeline import InstallDecision, InstallPipeline


class AlwaysApprove:
    def confirm_gate(self, gate: str, summary: str) -> bool:
        return True


class NoopBuilder:
    def build(self, skill_dir: Path, image: str) -> None:
        pass


class NoopReloader:
    def reload(self) -> None:
        pass


class TarballFetchTests(unittest.TestCase):
    def test_install_from_url_rejects_symlink_in_tarball(self):
        self._assert_tarball_member_type_rejected(tarfile.SYMTYPE, "/etc/passwd")

    def test_install_from_url_rejects_hardlink_in_tarball(self):
        self._assert_tarball_member_type_rejected(tarfile.LNKTYPE, "good-skill/SKILL.md")

    def _assert_tarball_member_type_rejected(self, member_type: bytes, linkname: str):
        with tempfile.TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "imported"
            install_root.mkdir()
            tarball = Path(tmp) / "payload.tgz"

            with tarfile.open(tarball, "w:gz") as tar:
                skill_body = (
                    "---\n"
                    "name: good-skill\n"
                    "description: test skill\n"
                    "---\n"
                    "\n"
                    "body\n"
                ).encode("utf-8")
                skill_info = tarfile.TarInfo("good-skill/SKILL.md")
                skill_info.size = len(skill_body)
                tar.addfile(skill_info, io.BytesIO(skill_body))

                link_info = tarfile.TarInfo("good-skill/link-out")
                link_info.type = member_type
                link_info.linkname = linkname
                tar.addfile(link_info)

            pipeline = InstallPipeline(
                confirmer=AlwaysApprove(),
                builder=NoopBuilder(),
                reloader=NoopReloader(),
                install_root=install_root,
            )

            with patch("urllib.request.urlretrieve", side_effect=lambda url, dest: Path(dest).write_bytes(tarball.read_bytes())):
                decision = pipeline.install_from_url("https://example.com/skill.tgz", tier="imported")

            self.assertEqual(decision, InstallDecision.FAILED)


if __name__ == "__main__":
    unittest.main()
