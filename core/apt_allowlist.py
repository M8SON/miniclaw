"""
apt-get package allowlist for imported skills.

Default list is the minimum set that covers common needs; users can extend
by editing ~/.miniclaw/config/apt-allowlist.txt (one package per line,
lines starting with # are ignored).

Extending the allowlist is a deliberate, keyboard-only trust decision and
cannot be done by voice.
"""

import os
from pathlib import Path


DEFAULT_APT_ALLOWLIST: frozenset[str] = frozenset({
    "curl",
    "ca-certificates",
    "git",
    "jq",
    "ffmpeg",
    "libsndfile1",
    "espeak-ng",
})


def _user_allowlist_path() -> Path:
    return Path(os.path.expanduser("~")) / ".miniclaw" / "config" / "apt-allowlist.txt"


def load_apt_allowlist() -> frozenset[str]:
    """Return the effective allowlist (defaults plus any user additions)."""
    extra: set[str] = set()
    path = _user_allowlist_path()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                extra.add(line)
    return frozenset(DEFAULT_APT_ALLOWLIST | extra)
