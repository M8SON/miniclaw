import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


_SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "download_hailo_whisper_assets.py"
)
_SPEC = importlib.util.spec_from_file_location("download_hailo_whisper_assets", _SCRIPT_PATH)
download_hailo_whisper_assets = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(download_hailo_whisper_assets)


class DownloadHailoWhisperAssetsTests(unittest.TestCase):
    def test_manifest_layout_matches_runtime_expectations(self):
        manifest = download_hailo_whisper_assets.build_download_manifest("base", "hailo8l")

        relpaths = [relpath for _, relpath in manifest]
        self.assertIn(
            Path("base/hefs/hailo8l/base-whisper-encoder-5s_h8l.hef"),
            relpaths,
        )
        self.assertIn(
            Path("base/hefs/hailo8l/base-whisper-decoder-fixed-sequence-matmul-split_h8l.hef"),
            relpaths,
        )
        self.assertIn(
            Path("base/decoder_assets/onnx_add_input_base.npy"),
            relpaths,
        )
        self.assertIn(
            Path("base/decoder_assets/token_embedding_weight_base.npy"),
            relpaths,
        )

    def test_invalid_combo_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported Hailo Whisper combo"):
            download_hailo_whisper_assets.build_download_manifest("base", "hailo10h")

    def test_download_manifest_skips_existing_files(self):
        manifest = [
            ("https://example.invalid/already-there.bin", Path("base/hefs/hailo8/already-there.bin"))
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / manifest[0][1]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("present", encoding="utf-8")

            with patch("urllib.request.urlretrieve") as mock_urlretrieve:
                download_hailo_whisper_assets.download_manifest(root, manifest)

            mock_urlretrieve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
