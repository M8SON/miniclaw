"""
Per-tier trust policy for MiniClaw skills.

Trust tier is inferred from the directory a skill was loaded from; never
from the skill's own frontmatter. Each tier has a policy object that says
how much the validator and loader should clamp or reject.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

TIER_BUNDLED = "bundled"   # shipped in repo; full trust
TIER_AUTHORED = "authored" # voice-installed via install_skill
TIER_IMPORTED = "imported" # community-sourced
TIER_DEV = "dev"           # symlinked-in; bypasses security but not structural checks


@dataclass(frozen=True)
class TierPolicy:
    name: str
    max_memory_mb: int | None        # None = unlimited
    max_timeout_seconds: int | None
    max_cpus: float | None
    allow_native: bool
    require_dockerfile_allowlist: bool
    require_confirm_read_only_false: bool


_POLICIES: dict[str, TierPolicy] = {
    TIER_BUNDLED: TierPolicy(
        name=TIER_BUNDLED,
        max_memory_mb=None,
        max_timeout_seconds=None,
        max_cpus=None,
        allow_native=True,
        require_dockerfile_allowlist=False,
        require_confirm_read_only_false=False,
    ),
    TIER_AUTHORED: TierPolicy(
        name=TIER_AUTHORED,
        max_memory_mb=1024,
        max_timeout_seconds=120,
        max_cpus=2.0,
        allow_native=False,
        require_dockerfile_allowlist=True,
        require_confirm_read_only_false=False,
    ),
    TIER_IMPORTED: TierPolicy(
        name=TIER_IMPORTED,
        max_memory_mb=512,
        max_timeout_seconds=60,
        max_cpus=1.0,
        allow_native=False,
        require_dockerfile_allowlist=True,
        require_confirm_read_only_false=True,
    ),
}
# Dev mode inherits bundled policy — no security clamps, but structural
# validation still runs via the loader/validator checks that don't
# consult TierPolicy (name format, parent-dir match, frontmatter shape).
_POLICIES[TIER_DEV] = _POLICIES[TIER_BUNDLED]


def policy_for(tier: str) -> TierPolicy:
    """Return the TierPolicy for a tier name. Raises KeyError on unknown tier."""
    return _POLICIES[tier]


# Credential-pattern warning: env_passthrough values matching these trigger
# an extra confirmation even inside the normal first-run passthrough gate.
_CREDENTIAL_PATTERNS = [
    re.compile(r"^ANTHROPIC_API_KEY$"),
    re.compile(r".*_SECRET$"),
    re.compile(r".*_TOKEN$"),
    re.compile(r".*_KEY$"),
]


def is_credential_pattern(env_var_name: str) -> bool:
    """Return True if an env var name looks like a credential."""
    return any(p.match(env_var_name) for p in _CREDENTIAL_PATTERNS)


# Device allowlist for authored + imported tiers. Matched against the host
# path portion of a `--device` entry.
DEVICE_ALLOWLIST_PATTERNS = [
    re.compile(r"^/dev/snd$"),
    re.compile(r"^/dev/video\d+$"),
    re.compile(r"^/dev/i2c-\d+$"),
    re.compile(r"^/dev/gpiomem$"),
]


def is_scoped_volume(volume_spec: str, skill_name: str, home: str | None = None) -> bool:
    """
    Return True when a docker `-v <host>:<container>` volume's host-side path
    resolves inside ~/.miniclaw/<skill_name>/.

    Reject any mount that escapes to / or ~ wholesale, or that scopes under a
    different skill's directory.
    """
    if ":" not in volume_spec:
        return False
    host_side = volume_spec.split(":", 1)[0].strip()
    if not host_side:
        return False
    home_dir = home if home is not None else os.path.expanduser("~")
    expanded = os.path.expandvars(host_side.replace("~", home_dir, 1))
    try:
        resolved = Path(expanded).resolve()
    except (OSError, ValueError):
        return False
    scoped_root = Path(home_dir) / ".miniclaw" / skill_name
    try:
        resolved.relative_to(scoped_root)
    except ValueError:
        return False
    return True
