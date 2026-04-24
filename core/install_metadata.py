"""
Provenance sidecar for installed skills.

Each authored/imported skill directory contains .install.json with:
  source: URL or path the skill was installed from
  sha256: hash over the skill's files (excluding .install.json itself)
  installed_at: ISO timestamp of install
  user_confirmed_env_passthrough: list of env var names the user has approved

The install pipeline writes this file. The loader reads it to detect drift
(SHA256 mismatch => skill changed on disk => trigger re-confirmation) and
to short-circuit the env_passthrough gate for keys already confirmed.

Tier is NEVER stored in this file — tier comes from install directory.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


INSTALL_FILENAME = ".install.json"


@dataclass
class InstallMetadata:
    source: str
    sha256: str
    installed_at: str
    user_confirmed_env_passthrough: list[str] = field(default_factory=list)


def compute_skill_sha256(skill_dir: Path) -> str:
    """
    Compute a deterministic SHA256 across the skill's files, excluding
    .install.json (which would otherwise make the hash self-referential).

    Files are sorted by relative path for deterministic output.
    """
    h = hashlib.sha256()
    for rel in sorted(_iter_relative_paths(skill_dir)):
        if rel.name == INSTALL_FILENAME:
            continue
        full = skill_dir / rel
        if not full.is_file():
            continue
        h.update(str(rel).encode("utf-8"))
        h.update(b"\0")
        h.update(full.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _iter_relative_paths(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file():
            yield p.relative_to(root)


def read_metadata(skill_dir: Path) -> InstallMetadata | None:
    """Return the parsed .install.json, or None if absent or malformed."""
    path = skill_dir / INSTALL_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return InstallMetadata(
            source=str(data.get("source", "")),
            sha256=str(data.get("sha256", "")),
            installed_at=str(data.get("installed_at", "")),
            user_confirmed_env_passthrough=list(
                data.get("user_confirmed_env_passthrough", []) or []
            ),
        )
    except (TypeError, ValueError):
        return None


def write_metadata(skill_dir: Path, meta: InstallMetadata) -> None:
    """Serialize InstallMetadata to <skill_dir>/.install.json."""
    path = skill_dir / INSTALL_FILENAME
    path.write_text(json.dumps(asdict(meta), indent=2, sort_keys=True), encoding="utf-8")
