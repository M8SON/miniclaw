#!/usr/bin/env python3
"""Download Hailo Whisper assets into MiniClaw's user-scoped model store."""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path


DEFAULT_ASSETS_ROOT = Path.home() / ".miniclaw" / "models" / "hailo-whisper"
BASE_HEF = "https://hailo-csdata.s3.eu-west-2.amazonaws.com/resources/whisper"
BASE_DECODER_ASSETS = (
    "https://hailo-csdata.s3.eu-west-2.amazonaws.com/resources/npy%20files/whisper/decoder_assets"
)

HEF_URLS = {
    "hailo8": {
        "tiny": [
            f"{BASE_HEF}/h8/tiny-whisper-decoder-fixed-sequence-matmul-split.hef",
            f"{BASE_HEF}/h8/tiny-whisper-encoder-10s_15dB.hef",
        ],
        "base": [
            f"{BASE_HEF}/h8/base-whisper-decoder-fixed-sequence-matmul-split.hef",
            f"{BASE_HEF}/h8/base-whisper-encoder-5s.hef",
        ],
    },
    "hailo8l": {
        "tiny": [
            f"{BASE_HEF}/h8l/tiny-whisper-decoder-fixed-sequence-matmul-split_h8l.hef",
            f"{BASE_HEF}/h8l/tiny-whisper-encoder-10s_15dB_h8l.hef",
        ],
        "base": [
            f"{BASE_HEF}/h8l/base-whisper-decoder-fixed-sequence-matmul-split_h8l.hef",
            f"{BASE_HEF}/h8l/base-whisper-encoder-5s_h8l.hef",
        ],
    },
    "hailo10h": {
        "tiny": [
            f"{BASE_HEF}/h10h/tiny-whisper-decoder-fixed-sequence.hef",
            f"{BASE_HEF}/h10h/tiny-whisper-encoder-10s.hef",
        ],
        "tiny.en": [
            f"{BASE_HEF}/h10h/tiny_en-whisper-decoder-fixed-sequence.hef",
            f"{BASE_HEF}/h10h/tiny_en-whisper-encoder-10s.hef",
        ],
    },
}

DECODER_ASSET_URLS = {
    "tiny": [
        f"{BASE_DECODER_ASSETS}/tiny/decoder_tokenization/onnx_add_input_tiny.npy",
        f"{BASE_DECODER_ASSETS}/tiny/decoder_tokenization/token_embedding_weight_tiny.npy",
    ],
    "base": [
        f"{BASE_DECODER_ASSETS}/base/decoder_tokenization/onnx_add_input_base.npy",
        f"{BASE_DECODER_ASSETS}/base/decoder_tokenization/token_embedding_weight_base.npy",
    ],
    "tiny.en": [
        f"{BASE_DECODER_ASSETS}/tiny.en/decoder_tokenization/onnx_add_input_tiny.en.npy",
        f"{BASE_DECODER_ASSETS}/tiny.en/decoder_tokenization/token_embedding_weight_tiny.en.npy",
    ],
}


def build_download_manifest(variant: str, hw_arch: str) -> list[tuple[str, Path]]:
    try:
        hef_urls = HEF_URLS[hw_arch][variant]
    except KeyError as exc:
        raise ValueError(f"unsupported Hailo Whisper combo: arch={hw_arch} variant={variant}") from exc

    try:
        decoder_urls = DECODER_ASSET_URLS[variant]
    except KeyError as exc:
        raise ValueError(f"unsupported Hailo Whisper variant: {variant}") from exc

    manifest: list[tuple[str, Path]] = []
    for url in hef_urls:
        manifest.append((url, Path(variant) / "hefs" / hw_arch / Path(url).name))
    for url in decoder_urls:
        manifest.append(
            (
                url,
                Path(variant) / "decoder_assets" / Path(url).name,
            )
        )
    return manifest


def download_manifest(assets_root: Path, manifest: list[tuple[str, Path]]) -> None:
    assets_root.mkdir(parents=True, exist_ok=True)
    for url, relpath in manifest:
        destination = assets_root / relpath
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            print(f"skip {destination}")
            continue
        print(f"download {url} -> {destination}")
        urllib.request.urlretrieve(url, destination)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Hailo Whisper assets into MiniClaw's model store."
    )
    parser.add_argument(
        "--variant",
        required=True,
        choices=sorted(DECODER_ASSET_URLS.keys()),
        help="Whisper variant to download.",
    )
    parser.add_argument(
        "--hw-arch",
        required=True,
        choices=sorted(HEF_URLS.keys()),
        help="Target Hailo hardware architecture.",
    )
    parser.add_argument(
        "--assets-root",
        type=Path,
        default=DEFAULT_ASSETS_ROOT,
        help="Override the destination model root.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_download_manifest(args.variant, args.hw_arch)
    download_manifest(args.assets_root, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
