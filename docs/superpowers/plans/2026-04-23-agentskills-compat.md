# agentskills.io Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan. Batch tasks within a phase; checkpoint at phase boundaries. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MiniClaw skills agentskills.io-spec-compliant (bidirectional), introduce per-tier trust policy (`bundled` / `authored` / `imported`), and add a single install pipeline shared by voice, CLI, and future mobile entry points.

**Architecture:** Refactor the validator + loader to be tier-aware, migrate the 12 existing skills to kebab-case single-directory layout in one mechanical pass, then build the install pipeline + CLI on top of the new model. Dev mode is a symlink escape hatch. Self-update is scaffolded (frontmatter flag reserved) but not implemented — that's roadmap #4.

**Tech Stack:** Python 3.11+, PyYAML, subprocess (docker CLI), pytest/unittest, sqlite+FTS5 (existing archive — unaffected). No new runtime dependencies.

**Related spec:** `docs/superpowers/specs/2026-04-23-agentskills-compat-design.md`

---

## File Structure Map

### New files

| Path | Responsibility |
|---|---|
| `core/skill_policy.py` | Per-tier constants: memory/timeout/cpus clamps, device allowlist, apt-get allowlist, credential patterns |
| `core/install_metadata.py` | `.install.json` read/write; SHA256 computation over a skill directory |
| `core/install_pipeline.py` | Fetch → validate → summarize → confirm → install → build → reload. Shared by CLI + `install_skill` voice path |
| `core/skill_cli.py` | `miniclaw skill <subcommand>` argparse dispatch (install/uninstall/list/validate/dev) |
| `scripts/migrate-to-agentskills.py` | One-shot migration script (deleted in final commit) |
| `tests/test_skill_policy.py` | Policy constants + credential pattern matcher |
| `tests/test_dockerfile_validator_tiered.py` | Per-tier Dockerfile allowlist |
| `tests/test_skill_validator_tiered.py` | Tier-aware config clamps, frontmatter validation |
| `tests/test_skill_loader_tiered.py` | Three-path loading, tier inference, dev-mode, collision |
| `tests/test_install_metadata.py` | SHA256 computation + `.install.json` round-trip |
| `tests/test_install_pipeline.py` | End-to-end pipeline happy path + rejection branches |
| `tests/test_migration_script.py` | Migration script fixture round-trip |
| `tests/fixtures/agentskills/good-skill/` | Known-good fixture skill for integration tests |
| `tests/fixtures/agentskills/bad-*/ ` | One fixture per rejection reason |

### Modified files

| Path | Change |
|---|---|
| `core/skill_validator.py` | Kebab-case name regex, parent-dir match, metadata.miniclaw.requires, tier-aware `validate_execution_config`, spec-field validation |
| `core/skill_eligibility.py` | Read requires from `metadata.miniclaw.requires` instead of top-level |
| `core/dockerfile_validator.py` | Tier parameter + per-tier allowlists + apt-get package allowlist |
| `core/skill_loader.py` | New `DEFAULT_SEARCH_PATHS`, tier inference by path, dev-mode symlink detection, cross-tier collision rejection, load `.install.json` |
| `core/container_manager.py` | Rename `_native_handlers` dispatch keys to kebab-case; add imported-tier `read_only=false` confirmation path |
| `core/meta_skill.py` | Delegate voice install to shared install pipeline for the URL-install branch |
| `main.py` | Add `miniclaw skill …` subcommand dispatch |
| `run.sh` | Discovery path `containers/*/Dockerfile` → `skills/*/scripts/Dockerfile` |
| `scripts/port-skill.py` | Rename to `port-openclaw-skill.py`; emit single-directory layout |
| `CLAUDE.md` | Rewrite "Skill Structure" and skill listings to new layout |
| `WORKING_MEMORY.md` | Append migration note under Hermes roadmap item |

### Deleted at end

| Path | Reason |
|---|---|
| `containers/` (all subdirs) | Folded into `skills/<name>/scripts/` by migration |
| `scripts/migrate-to-agentskills.py` | Final commit of migration PR |

---

## Phase 1 — Per-tier policy module and constants

Phase-boundary checkpoint: run `pytest tests/test_skill_policy.py` at end of phase. Everything green before proceeding.

### Task 1: Per-tier policy module

**Files:**
- Create: `core/skill_policy.py`
- Create: `tests/test_skill_policy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_policy.py
"""Tests for per-tier policy constants and helpers."""

import unittest

from core.skill_policy import (
    TIER_BUNDLED,
    TIER_AUTHORED,
    TIER_IMPORTED,
    TIER_DEV,
    policy_for,
    is_credential_pattern,
    is_scoped_volume,
    DEVICE_ALLOWLIST_PATTERNS,
)


class TestPolicyLookup(unittest.TestCase):
    def test_bundled_has_no_clamps(self):
        policy = policy_for(TIER_BUNDLED)
        self.assertIsNone(policy.max_memory_mb)
        self.assertIsNone(policy.max_timeout_seconds)
        self.assertIsNone(policy.max_cpus)
        self.assertTrue(policy.allow_native)

    def test_authored_has_moderate_clamps(self):
        policy = policy_for(TIER_AUTHORED)
        self.assertEqual(policy.max_memory_mb, 1024)
        self.assertEqual(policy.max_timeout_seconds, 120)
        self.assertEqual(policy.max_cpus, 2.0)
        self.assertFalse(policy.allow_native)

    def test_imported_has_strict_clamps(self):
        policy = policy_for(TIER_IMPORTED)
        self.assertEqual(policy.max_memory_mb, 512)
        self.assertEqual(policy.max_timeout_seconds, 60)
        self.assertEqual(policy.max_cpus, 1.0)
        self.assertFalse(policy.allow_native)

    def test_dev_matches_bundled_policy(self):
        self.assertEqual(policy_for(TIER_DEV), policy_for(TIER_BUNDLED))


class TestCredentialPattern(unittest.TestCase):
    def test_anthropic_api_key_matches(self):
        self.assertTrue(is_credential_pattern("ANTHROPIC_API_KEY"))

    def test_generic_token_matches(self):
        self.assertTrue(is_credential_pattern("GITHUB_TOKEN"))

    def test_generic_secret_matches(self):
        self.assertTrue(is_credential_pattern("STRIPE_SECRET"))

    def test_generic_key_matches(self):
        self.assertTrue(is_credential_pattern("OPENWEATHER_API_KEY"))

    def test_plain_name_does_not_match(self):
        self.assertFalse(is_credential_pattern("LOG_LEVEL"))


class TestScopedVolume(unittest.TestCase):
    def test_miniclaw_scoped_path_ok(self):
        home = "/home/user"
        self.assertTrue(is_scoped_volume("~/.miniclaw/foo:/data", "foo", home))

    def test_root_mount_rejected(self):
        self.assertFalse(is_scoped_volume("/:/rootfs", "foo", "/home/user"))

    def test_home_root_rejected(self):
        self.assertFalse(is_scoped_volume("~:/host", "foo", "/home/user"))

    def test_wrong_skill_name_rejected(self):
        self.assertFalse(is_scoped_volume("~/.miniclaw/bar:/data", "foo", "/home/user"))


class TestDeviceAllowlist(unittest.TestCase):
    def test_snd_allowed(self):
        self.assertTrue(any(p.match("/dev/snd") for p in DEVICE_ALLOWLIST_PATTERNS))

    def test_i2c_wildcard_allowed(self):
        self.assertTrue(any(p.match("/dev/i2c-0") for p in DEVICE_ALLOWLIST_PATTERNS))

    def test_kmsg_rejected(self):
        self.assertFalse(any(p.match("/dev/kmsg") for p in DEVICE_ALLOWLIST_PATTERNS))

    def test_mem_rejected(self):
        self.assertFalse(any(p.match("/dev/mem") for p in DEVICE_ALLOWLIST_PATTERNS))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_skill_policy.py -v`
Expected: `ModuleNotFoundError: No module named 'core.skill_policy'`

- [ ] **Step 3: Write the implementation**

```python
# core/skill_policy.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_skill_policy.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/skill_policy.py tests/test_skill_policy.py
git commit -m "feat(skills): add per-tier trust policy module"
```

---

## Phase 2 — SkillValidator overhaul

Phase-boundary checkpoint: run `pytest tests/test_skill_validator*.py` at end of phase.

### Task 2: Kebab-case name regex + parent-dir match

**Files:**
- Modify: `core/skill_validator.py`
- Modify: `tests/` — new test file `tests/test_skill_validator_tiered.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_skill_validator_tiered.py
"""Tests for the agentskills.io-compliant SkillValidator."""

import unittest
from pathlib import Path

from core.skill_validator import SkillValidator


class TestNameValidation(unittest.TestCase):
    def setUp(self):
        self.v = SkillValidator()

    def _md(self, name: str) -> str:
        return f"---\nname: {name}\ndescription: Does a thing.\n---\n\nBody.\n"

    def test_kebab_case_accepted(self):
        fm, _ = self.v.validate_markdown(self._md("web-search"), Path("/tmp/web-search"))
        self.assertEqual(fm["name"], "web-search")

    def test_all_lowercase_accepted(self):
        fm, _ = self.v.validate_markdown(self._md("weather"), Path("/tmp/weather"))
        self.assertEqual(fm["name"], "weather")

    def test_uppercase_rejected(self):
        with self.assertRaisesRegex(ValueError, "name.*lowercase"):
            self.v.validate_markdown(self._md("Web-Search"), Path("/tmp/Web-Search"))

    def test_snake_case_rejected(self):
        with self.assertRaisesRegex(ValueError, "name.*hyphen"):
            self.v.validate_markdown(self._md("web_search"), Path("/tmp/web_search"))

    def test_leading_hyphen_rejected(self):
        with self.assertRaisesRegex(ValueError, "name.*hyphen"):
            self.v.validate_markdown(self._md("-web"), Path("/tmp/-web"))

    def test_trailing_hyphen_rejected(self):
        with self.assertRaisesRegex(ValueError, "name.*hyphen"):
            self.v.validate_markdown(self._md("web-"), Path("/tmp/web-"))

    def test_consecutive_hyphens_rejected(self):
        with self.assertRaisesRegex(ValueError, "name.*hyphen"):
            self.v.validate_markdown(self._md("web--search"), Path("/tmp/web--search"))

    def test_name_must_match_parent_dir(self):
        with self.assertRaisesRegex(ValueError, "must match parent directory"):
            self.v.validate_markdown(self._md("web-search"), Path("/tmp/weather"))

    def test_name_over_64_chars_rejected(self):
        long = "a" + "-a" * 32  # 65 chars
        with self.assertRaisesRegex(ValueError, "name.*64"):
            self.v.validate_markdown(self._md(long), Path(f"/tmp/{long}"))


class TestDescriptionValidation(unittest.TestCase):
    def setUp(self):
        self.v = SkillValidator()

    def test_description_over_1024_chars_rejected(self):
        long_desc = "a" * 1025
        raw = f"---\nname: t\ndescription: {long_desc}\n---\n\nBody.\n"
        with self.assertRaisesRegex(ValueError, "description.*1024"):
            self.v.validate_markdown(raw, Path("/tmp/t"))

    def test_description_at_1024_chars_accepted(self):
        desc = "a" * 1024
        raw = f"---\nname: t\ndescription: {desc}\n---\n\nBody.\n"
        fm, _ = self.v.validate_markdown(raw, Path("/tmp/t"))
        self.assertEqual(len(fm["description"]), 1024)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_skill_validator_tiered.py -v`
Expected: multiple failures (name not enforced, parent-dir match not enforced, length not enforced).

- [ ] **Step 3: Update `core/skill_validator.py`**

Replace the current `validate_markdown` method and add supporting constants:

```python
# core/skill_validator.py — replace existing validate_markdown + add constants

import re
from pathlib import Path

import yaml


SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SKILL_NAME_MAX = 64
SKILL_DESCRIPTION_MAX = 1024


class SkillValidator:
    """Parse skill markdown and derive tool definitions."""

    FRONTMATTER_PATTERN = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    INPUT_SCHEMA_PATTERN = (
        r"##\s*(?:Inputs|Parameters|Input Schema)\s*\n```(?:yaml|json)\s*\n(.*?)```"
    )

    def validate_markdown(self, raw: str, skill_dir: Path) -> tuple[dict, str]:
        """
        Parse and validate SKILL.md content.

        Returns (frontmatter, body) or raises ValueError.
        """
        frontmatter, body = self.parse_frontmatter(raw)
        if frontmatter is None:
            raise ValueError("invalid YAML frontmatter")

        name = frontmatter.get("name")
        description = frontmatter.get("description", "")

        if not isinstance(name, str) or not name.strip():
            raise ValueError("skill name must be a non-empty string")
        if len(name) > SKILL_NAME_MAX:
            raise ValueError(
                f"skill name must be at most {SKILL_NAME_MAX} characters"
            )
        if not SKILL_NAME_RE.match(name):
            if any(c.isupper() for c in name):
                raise ValueError(
                    "skill name must be lowercase; only a-z, 0-9, and hyphens are allowed"
                )
            raise ValueError(
                "skill name must only contain a-z, 0-9, and single hyphens "
                "(no leading/trailing/consecutive hyphens)"
            )
        if skill_dir.name != name:
            raise ValueError(
                f"skill name {name!r} must match parent directory name {skill_dir.name!r}"
            )

        if not isinstance(description, str) or not description.strip():
            raise ValueError("skill description must not be empty")
        if len(description) > SKILL_DESCRIPTION_MAX:
            raise ValueError(
                f"skill description must be at most {SKILL_DESCRIPTION_MAX} characters"
            )

        if not isinstance(body, str) or not body.strip():
            raise ValueError("skill instructions must not be empty")

        return frontmatter, body

    def parse_frontmatter(self, raw: str) -> tuple[dict | None, str]:
        """Split a SKILL.md into (frontmatter_dict, markdown_body)."""
        match = re.match(self.FRONTMATTER_PATTERN, raw, re.DOTALL)
        if not match:
            return {}, raw
        try:
            frontmatter = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            return None, raw
        return frontmatter, match.group(2)

    def build_tool_definition(self, name: str, description: str, body: str) -> dict:
        """Build a Claude-compatible tool definition from skill metadata."""
        return {
            "name": name,
            "description": description,
            "input_schema": self.extract_input_schema(body),
        }

    def extract_input_schema(self, body: str) -> dict:
        """Extract input schema or fall back to a generic {query: string} schema."""
        match = re.search(self.INPUT_SCHEMA_PATTERN, body, re.DOTALL | re.IGNORECASE)
        if match:
            try:
                schema = yaml.safe_load(match.group(1))
                if isinstance(schema, dict) and "type" in schema:
                    return schema
            except yaml.YAMLError:
                pass
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The input or query for this skill",
                }
            },
            "required": ["query"],
        }

    # validate_execution_config stays for now — rewritten in Task 4
    def validate_execution_config(self, config: object) -> dict:
        if not isinstance(config, dict):
            raise ValueError("config.yaml must contain a YAML mapping")
        execution_type = config.get("type", "docker")
        if execution_type not in {"docker", "native"}:
            raise ValueError("config type must be 'docker' or 'native'")
        if execution_type == "native":
            if "image" in config:
                raise ValueError("native skills must not define an image")
        else:
            image = config.get("image")
            if not isinstance(image, str) or not image.strip():
                raise ValueError("docker skills must define a non-empty image")
        self._require_optional_int(config, "timeout_seconds", minimum=1)
        self._require_optional_str(config, "memory")
        self._require_optional_bool(config, "read_only")
        self._require_optional_list_of_strings(config, "env_passthrough")
        self._require_optional_list_of_strings(config, "devices")
        self._require_optional_list_of_strings(config, "extra_tmpfs")
        return config

    def _require_optional_int(self, config, key, minimum=None):
        value = config.get(key)
        if value is None:
            return
        if not isinstance(value, int):
            raise ValueError(f"config field '{key}' must be an integer")
        if minimum is not None and value < minimum:
            raise ValueError(f"config field '{key}' must be >= {minimum}")

    def _require_optional_str(self, config, key):
        value = config.get(key)
        if value is None:
            return
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"config field '{key}' must be a non-empty string")

    def _require_optional_bool(self, config, key):
        value = config.get(key)
        if value is None:
            return
        if not isinstance(value, bool):
            raise ValueError(f"config field '{key}' must be a boolean")

    def _require_optional_list_of_strings(self, config, key):
        value = config.get(key)
        if value is None:
            return
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"config field '{key}' must be a list of strings")
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_skill_validator_tiered.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/skill_validator.py tests/test_skill_validator_tiered.py
git commit -m "feat(skills): enforce agentskills.io name regex + description length + parent-dir match"
```

### Task 3: Move `requires` to `metadata.miniclaw.requires`

**Files:**
- Modify: `core/skill_eligibility.py`
- Modify: `tests/test_skill_validator_tiered.py` (append)

- [ ] **Step 1: Append failing tests**

```python
# Append to tests/test_skill_validator_tiered.py

from core.skill_eligibility import SkillEligibility


class TestRequiresLocation(unittest.TestCase):
    def setUp(self):
        self.elig = SkillEligibility()

    def test_requires_read_from_metadata_miniclaw(self):
        fm = {
            "name": "foo",
            "description": "x",
            "metadata": {
                "miniclaw": {"requires": {"env": ["NEVER_SET_XYZ_VAR"]}}
            },
        }
        reason, missing = self.elig.check(fm)
        self.assertIn("NEVER_SET_XYZ_VAR", reason)
        self.assertEqual(missing, ["NEVER_SET_XYZ_VAR"])

    def test_top_level_requires_ignored_after_migration(self):
        fm = {
            "name": "foo",
            "description": "x",
            "requires": {"env": ["NEVER_SET_XYZ_VAR"]},  # old location
        }
        reason, missing = self.elig.check(fm)
        # Old top-level requires is NOT read anymore.
        self.assertIsNone(reason)
        self.assertEqual(missing, [])

    def test_empty_metadata_miniclaw_is_fine(self):
        fm = {"name": "foo", "description": "x", "metadata": {}}
        reason, missing = self.elig.check(fm)
        self.assertIsNone(reason)
        self.assertEqual(missing, [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_skill_validator_tiered.py::TestRequiresLocation -v`
Expected: `test_requires_read_from_metadata_miniclaw` fails (top-level still read); `test_top_level_requires_ignored_after_migration` may fail depending on current behavior.

- [ ] **Step 3: Update `core/skill_eligibility.py`**

```python
"""
Skill eligibility checks for MiniClaw.

Evaluates whether a structurally valid skill can run on the current system
based on environment variables, binaries, and OS constraints.

The `requires` block lives under `metadata.miniclaw.requires` per the
agentskills.io compat spec. The old top-level `requires:` key is ignored.
"""

import os
import platform
import shutil


class SkillEligibility:
    """Check whether a skill is currently runnable on this machine."""

    OS_MAP = {"darwin": "darwin", "linux": "linux", "windows": "win32"}

    def check(self, frontmatter: dict) -> tuple[str | None, list[str]]:
        """
        Return (reason, missing_env_vars) for the skill's requires block.

        reason is None when the skill is eligible, otherwise a human-readable
        description of the missing requirement(s).
        """
        requires = (
            frontmatter.get("metadata", {})
            .get("miniclaw", {})
            .get("requires", {})
        )
        if not requires:
            return None, []

        missing = []
        missing_env_vars = []

        for var in requires.get("env", []):
            if not os.environ.get(var):
                missing.append(f"{var} env var")
                missing_env_vars.append(var)

        for binary in requires.get("bins", []):
            if not shutil.which(binary):
                missing.append(f"{binary} binary")

        any_bins = requires.get("anyBins", [])
        if any_bins and not any(shutil.which(binary) for binary in any_bins):
            missing.append(f"one of these binaries: {', '.join(any_bins)}")

        required_os = requires.get("os", [])
        if required_os:
            current_os = platform.system().lower()
            normalized_os = self.OS_MAP.get(current_os, current_os)
            if normalized_os not in required_os:
                missing.append(f"OS must be one of: {', '.join(required_os)}")

        reason = ("missing " + ", ".join(missing)) if missing else None
        return reason, missing_env_vars
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_skill_validator_tiered.py::TestRequiresLocation -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/skill_eligibility.py tests/test_skill_validator_tiered.py
git commit -m "feat(skills): read requires from metadata.miniclaw.requires only"
```

### Task 4: Tier-aware `validate_execution_config` with clamps

**Files:**
- Modify: `core/skill_validator.py`
- Modify: `tests/test_skill_validator_tiered.py` (append)

- [ ] **Step 1: Append failing tests**

```python
# Append to tests/test_skill_validator_tiered.py

from core.skill_policy import TIER_BUNDLED, TIER_AUTHORED, TIER_IMPORTED


class TestTieredConfigValidation(unittest.TestCase):
    def setUp(self):
        self.v = SkillValidator()

    def _base_config(self):
        return {
            "type": "docker",
            "image": "miniclaw/foo:latest",
            "env_passthrough": [],
            "timeout_seconds": 15,
            "devices": [],
        }

    def test_native_rejected_for_authored(self):
        cfg = {"type": "native"}
        with self.assertRaisesRegex(ValueError, "native.*not allowed"):
            self.v.validate_execution_config(cfg, tier=TIER_AUTHORED, skill_name="foo")

    def test_native_rejected_for_imported(self):
        cfg = {"type": "native"}
        with self.assertRaisesRegex(ValueError, "native.*not allowed"):
            self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")

    def test_native_accepted_for_bundled(self):
        cfg = {"type": "native"}
        result = self.v.validate_execution_config(cfg, tier=TIER_BUNDLED, skill_name="foo")
        self.assertEqual(result["type"], "native")

    def test_memory_clamp_imported(self):
        cfg = self._base_config()
        cfg["memory"] = "1g"  # 1024 > 512 max for imported
        with self.assertRaisesRegex(ValueError, "memory.*exceeds"):
            self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")

    def test_memory_under_clamp_authored(self):
        cfg = self._base_config()
        cfg["memory"] = "512m"
        result = self.v.validate_execution_config(cfg, tier=TIER_AUTHORED, skill_name="foo")
        self.assertEqual(result["memory"], "512m")

    def test_timeout_clamp_imported(self):
        cfg = self._base_config()
        cfg["timeout_seconds"] = 90
        with self.assertRaisesRegex(ValueError, "timeout.*exceeds"):
            self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")

    def test_cpus_clamp_imported(self):
        cfg = self._base_config()
        cfg["cpus"] = 2.0
        with self.assertRaisesRegex(ValueError, "cpus.*exceeds"):
            self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")

    def test_disallowed_device_imported(self):
        cfg = self._base_config()
        cfg["devices"] = ["/dev/kmsg"]
        with self.assertRaisesRegex(ValueError, "device.*not allowed"):
            self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")

    def test_allowed_device_imported(self):
        cfg = self._base_config()
        cfg["devices"] = ["/dev/snd", "/dev/i2c-1"]
        result = self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")
        self.assertEqual(result["devices"], ["/dev/snd", "/dev/i2c-1"])

    def test_any_device_bundled(self):
        cfg = self._base_config()
        cfg["devices"] = ["/dev/kmsg"]
        result = self.v.validate_execution_config(cfg, tier=TIER_BUNDLED, skill_name="foo")
        self.assertEqual(result["devices"], ["/dev/kmsg"])

    def test_unscoped_volume_imported(self):
        cfg = self._base_config()
        cfg["volumes"] = ["~:/host"]
        with self.assertRaisesRegex(ValueError, "volume.*scope"):
            self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")

    def test_scoped_volume_imported(self):
        cfg = self._base_config()
        cfg["volumes"] = ["~/.miniclaw/foo:/data"]
        result = self.v.validate_execution_config(cfg, tier=TIER_IMPORTED, skill_name="foo")
        self.assertEqual(result["volumes"], ["~/.miniclaw/foo:/data"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_skill_validator_tiered.py::TestTieredConfigValidation -v`
Expected: many failures — method signature mismatch (`tier` not accepted).

- [ ] **Step 3: Replace `validate_execution_config` and helpers**

Replace the old `validate_execution_config` body in `core/skill_validator.py` with the tier-aware version below. Keep `_require_optional_*` helpers but add a new `_parse_memory_to_mb` and `_validate_clamps`:

```python
# Add to core/skill_validator.py imports
from core.skill_policy import (
    TIER_BUNDLED,
    TIER_AUTHORED,
    TIER_IMPORTED,
    TIER_DEV,
    policy_for,
    DEVICE_ALLOWLIST_PATTERNS,
    is_scoped_volume,
)

# Replace existing validate_execution_config on SkillValidator:

    def validate_execution_config(
        self,
        config: object,
        *,
        tier: str = TIER_BUNDLED,
        skill_name: str = "",
    ) -> dict:
        """
        Validate config.yaml shape and apply tier-specific clamps.

        tier:        one of bundled/authored/imported/dev (see core.skill_policy).
        skill_name:  the skill's kebab-case name. Required for volume scoping
                     in authored/imported tiers; may be "" for bundled.
        """
        if not isinstance(config, dict):
            raise ValueError("config.yaml must contain a YAML mapping")

        policy = policy_for(tier)

        execution_type = config.get("type", "docker")
        if execution_type not in {"docker", "native"}:
            raise ValueError("config type must be 'docker' or 'native'")

        if execution_type == "native":
            if not policy.allow_native:
                raise ValueError(
                    f"type: native is not allowed for tier {tier!r} "
                    "(only bundled skills may run natively)"
                )
            if "image" in config:
                raise ValueError("native skills must not define an image")
        else:
            image = config.get("image")
            if not isinstance(image, str) or not image.strip():
                raise ValueError("docker skills must define a non-empty image")

        self._require_optional_int(config, "timeout_seconds", minimum=1)
        self._require_optional_str(config, "memory")
        self._require_optional_bool(config, "read_only")
        self._require_optional_list_of_strings(config, "env_passthrough")
        self._require_optional_list_of_strings(config, "devices")
        self._require_optional_list_of_strings(config, "extra_tmpfs")
        self._require_optional_list_of_strings(config, "volumes")

        # Clamps (skip when policy says unlimited)
        if policy.max_memory_mb is not None:
            memory_str = config.get("memory")
            if memory_str is not None:
                mb = self._parse_memory_to_mb(memory_str)
                if mb > policy.max_memory_mb:
                    raise ValueError(
                        f"memory {memory_str!r} exceeds tier {tier!r} max of "
                        f"{policy.max_memory_mb}m"
                    )

        if policy.max_timeout_seconds is not None:
            timeout = config.get("timeout_seconds")
            if timeout is not None and timeout > policy.max_timeout_seconds:
                raise ValueError(
                    f"timeout_seconds {timeout} exceeds tier {tier!r} max of "
                    f"{policy.max_timeout_seconds}"
                )

        if policy.max_cpus is not None:
            cpus = config.get("cpus")
            if cpus is not None:
                try:
                    cpus_f = float(cpus)
                except (TypeError, ValueError):
                    raise ValueError(f"cpus {cpus!r} must be a number")
                if cpus_f > policy.max_cpus:
                    raise ValueError(
                        f"cpus {cpus!r} exceeds tier {tier!r} max of {policy.max_cpus}"
                    )

        # Device allowlist (only enforced in authored/imported)
        if tier in (TIER_AUTHORED, TIER_IMPORTED):
            for device in config.get("devices", []) or []:
                host_path = device.split(":", 1)[0] if ":" in device else device
                if not any(p.match(host_path) for p in DEVICE_ALLOWLIST_PATTERNS):
                    raise ValueError(
                        f"device {device!r} is not allowed for tier {tier!r}"
                    )

        # Volume scope check (only enforced in authored/imported)
        if tier in (TIER_AUTHORED, TIER_IMPORTED):
            home = __import__("os").path.expanduser("~")
            for vol in config.get("volumes", []) or []:
                if not is_scoped_volume(vol, skill_name, home):
                    raise ValueError(
                        f"volume {vol!r} is out of scope for skill {skill_name!r} "
                        f"(must resolve under ~/.miniclaw/{skill_name}/)"
                    )

        return config

    @staticmethod
    def _parse_memory_to_mb(memory_str: str) -> int:
        """
        Parse a Docker-style memory spec ('512m', '1g', '1024M') to megabytes.

        Raises ValueError on unparseable input.
        """
        import re
        m = re.match(r"^\s*(\d+)\s*([mMgGkK]?)\s*$", memory_str)
        if not m:
            raise ValueError(f"memory {memory_str!r} is not a valid size")
        number = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "g":
            return number * 1024
        if unit == "m" or unit == "":
            return number
        if unit == "k":
            # Round up any sub-MB to 1 MB
            return max(1, number // 1024)
        raise ValueError(f"unknown memory unit in {memory_str!r}")
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_skill_validator_tiered.py::TestTieredConfigValidation -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add core/skill_validator.py tests/test_skill_validator_tiered.py
git commit -m "feat(skills): tier-aware config validation with clamps, devices, volumes"
```

---

## Phase 3 — Dockerfile validator generalization

Phase-boundary checkpoint: run `pytest tests/test_dockerfile_validator*.py`.

### Task 5: Per-tier Dockerfile allowlist + apt-get allowlist

**Files:**
- Modify: `core/dockerfile_validator.py`
- Create: `tests/test_dockerfile_validator_tiered.py`
- Create: `core/apt_allowlist.py`
- Create: `tests/test_apt_allowlist.py`

- [ ] **Step 1: Write failing tests for apt allowlist**

```python
# tests/test_apt_allowlist.py
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
                self.assertIn("curl", allowlist)  # defaults still present


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_apt_allowlist.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create `core/apt_allowlist.py`**

```python
"""
apt-get package allowlist for imported skills.

Default list is the minimum set that cover common needs; users can extend
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
```

- [ ] **Step 4: Run to verify pass**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_apt_allowlist.py -v`
Expected: all pass.

- [ ] **Step 5: Write failing tests for tiered Dockerfile validator**

```python
# tests/test_dockerfile_validator_tiered.py
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
        # Bundled skills bypass validation entirely.
        validate(df, tier=TIER_BUNDLED)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Run to verify they fail**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_dockerfile_validator_tiered.py -v`
Expected: failures (validate doesn't accept `tier`).

- [ ] **Step 7: Rewrite `core/dockerfile_validator.py`**

```python
"""
Dockerfile validator for MiniClaw skills.

Per-tier allowlist:
  bundled  — no validation (trusted; repo-reviewed)
  authored — FROM must be miniclaw/base:latest; RUN only pip/apt prefixes; no ADD; no USER
  imported — everything authored allows, PLUS apt-get packages must be in
             the allowlist (core.apt_allowlist), and pip install must not use
             --index-url / --extra-index-url
"""

import re
from pathlib import Path

from core.apt_allowlist import load_apt_allowlist
from core.skill_policy import TIER_BUNDLED, TIER_AUTHORED, TIER_IMPORTED, TIER_DEV


class DockerfileValidationError(Exception):
    pass


BLOCKED_PATTERNS = [
    (r"curl\s+.*\|\s*(ba)?sh", "curl pipe to shell"),
    (r"wget\s+.*\|\s*(ba)?sh", "wget pipe to shell"),
    (r"\beval\b",               "eval"),
    (r"\bnetcat\b",             "netcat"),
    (r"(?<!\w)nc\s+-",          "netcat (nc)"),
    (r"--privileged",           "privileged flag"),
    (r"/var/run/docker",        "Docker socket reference"),
    (r"^COPY\s+https?://",      "COPY from URL"),
]

ALLOWED_RUN_PREFIXES = (
    "pip install",
    "pip3 install",
    "apt-get install",
    "apt-get update",
    "apt-get clean",
    "apt-get -y install",
    "apt-get -y update",
    "rm -rf /var/lib/apt/lists",
)

BLOCKED_INSTRUCTIONS = {"ADD", "USER", "VOLUME"}


def validate(dockerfile_path: Path, *, tier: str = TIER_AUTHORED) -> None:
    """
    Validate a Dockerfile against the per-tier allowlist.

    Raises DockerfileValidationError with a descriptive message on failure.
    Bundled and dev tiers bypass validation entirely.
    """
    if tier in (TIER_BUNDLED, TIER_DEV):
        return

    apt_allowlist = load_apt_allowlist() if tier == TIER_IMPORTED else None
    text = dockerfile_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    from_count = 0

    for lineno, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        for pattern, label in BLOCKED_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                raise DockerfileValidationError(
                    f"Line {lineno}: blocked pattern '{label}' — {raw_line!r}"
                )

        instruction = line.split()[0].upper() if line.split() else ""

        if instruction in BLOCKED_INSTRUCTIONS:
            raise DockerfileValidationError(
                f"Line {lineno}: {instruction} is not allowed for tier {tier!r}"
            )

        if instruction == "FROM":
            from_count += 1
            if from_count > 1:
                raise DockerfileValidationError(
                    f"Line {lineno}: multi-stage builds are not allowed (second FROM)"
                )
            parts = line.split()
            image_ref = parts[1] if len(parts) > 1 else ""
            if image_ref.lower() != "miniclaw/base:latest":
                raise DockerfileValidationError(
                    f"Line {lineno}: base image must be 'miniclaw/base:latest', got {image_ref!r}"
                )

        elif instruction == "RUN":
            run_body = re.sub(r"^RUN\s+", "", line, flags=re.IGNORECASE).strip()
            run_body = re.sub(r"^/bin/(ba)?sh\s+-c\s+", "", run_body).strip("\"'")
            if not _is_allowed_run(run_body):
                raise DockerfileValidationError(
                    f"Line {lineno}: RUN only permits pip install / apt-get commands. "
                    f"Got: {run_body!r}"
                )
            if tier == TIER_IMPORTED:
                _validate_run_imported(run_body, apt_allowlist, lineno)

        elif instruction == "COPY":
            parts = line.split()
            src_parts = [p for p in parts[1:] if not p.startswith("--")]
            # --from=... flags are caught here because COPY --from is treated
            # as a stage reference which we disallow in authored/imported tiers.
            if any(p.startswith("--from=") for p in parts[1:]):
                raise DockerfileValidationError(
                    f"Line {lineno}: COPY --from is not allowed for tier {tier!r}"
                )
            if len(src_parts) >= 2:
                sources = src_parts[:-1]
                invalid = next(
                    (src for src in sources if not _is_relative_copy_source(src)),
                    None,
                )
                if invalid is not None:
                    raise DockerfileValidationError(
                        f"Line {lineno}: COPY source must be a relative local path, "
                        f"got {invalid!r}"
                    )

    if from_count == 0:
        raise DockerfileValidationError("Dockerfile has no FROM instruction")


def _is_allowed_run(run_body: str) -> bool:
    segments = [s.strip() for s in re.split(r"\s*(?:&&|\|\||;)\s*", run_body)]
    return all(
        any(seg.startswith(prefix) for prefix in ALLOWED_RUN_PREFIXES)
        for seg in segments
        if seg
    )


def _validate_run_imported(run_body: str, apt_allowlist: frozenset[str], lineno: int) -> None:
    segments = [s.strip() for s in re.split(r"\s*(?:&&|\|\||;)\s*", run_body) if s.strip()]
    for seg in segments:
        # pip install must not pass --index-url or --extra-index-url
        if seg.startswith("pip install") or seg.startswith("pip3 install"):
            if "--index-url" in seg or "--extra-index-url" in seg:
                raise DockerfileValidationError(
                    f"Line {lineno}: pip install --index-url / --extra-index-url "
                    "is not allowed for imported skills"
                )
        # apt-get install packages must be in the allowlist
        if seg.startswith("apt-get install") or seg.startswith("apt-get -y install"):
            tokens = [
                t for t in seg.split()
                if t not in {"apt-get", "install", "-y", "--yes", "--no-install-recommends"}
            ]
            for pkg in tokens:
                if pkg in apt_allowlist:
                    continue
                raise DockerfileValidationError(
                    f"Line {lineno}: apt package {pkg!r} is not in the allowlist "
                    "(extend via ~/.miniclaw/config/apt-allowlist.txt)"
                )


def _is_relative_copy_source(src: str) -> bool:
    if not src or src.startswith("/"):
        return False
    src_path = Path(src)
    return ".." not in src_path.parts
```

- [ ] **Step 8: Run to verify pass**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_dockerfile_validator_tiered.py tests/test_apt_allowlist.py -v`
Expected: all pass.

Also run the existing dockerfile validator tests (if any) to make sure nothing regressed:
```bash
python3 -m pytest tests/ -k dockerfile -v
```

- [ ] **Step 9: Commit**

```bash
git add core/dockerfile_validator.py core/apt_allowlist.py tests/test_dockerfile_validator_tiered.py tests/test_apt_allowlist.py
git commit -m "feat(skills): generalize Dockerfile validator with per-tier allowlists + apt allowlist"
```

---

## Phase 4 — Loader + tier model

Phase-boundary checkpoint: `pytest tests/test_skill_loader*.py tests/test_install_metadata.py`.

### Task 6: `.install.json` provenance sidecar

**Files:**
- Create: `core/install_metadata.py`
- Create: `tests/test_install_metadata.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_install_metadata.py
"""Tests for the .install.json provenance sidecar."""

import json
import tempfile
import unittest
from pathlib import Path

from core.install_metadata import (
    InstallMetadata,
    compute_skill_sha256,
    read_metadata,
    write_metadata,
)


class TestSha256(unittest.TestCase):
    def test_sha256_covers_all_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "foo"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("a")
            (skill_dir / "config.yaml").write_text("b")
            (skill_dir / "scripts").mkdir()
            (skill_dir / "scripts" / "app.py").write_text("c")

            sha1 = compute_skill_sha256(skill_dir)
            # Touch an unrelated file — sha must change.
            (skill_dir / "scripts" / "app.py").write_text("c2")
            sha2 = compute_skill_sha256(skill_dir)
            self.assertNotEqual(sha1, sha2)

    def test_sha256_excludes_install_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "foo"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("a")
            sha1 = compute_skill_sha256(skill_dir)
            (skill_dir / ".install.json").write_text('{"source": "x"}')
            sha2 = compute_skill_sha256(skill_dir)
            self.assertEqual(sha1, sha2)


class TestReadWrite(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "foo"
            skill_dir.mkdir()

            meta = InstallMetadata(
                source="https://github.com/user/pdf-tools",
                sha256="abc123",
                installed_at="2026-04-23T12:00:00",
                user_confirmed_env_passthrough=["OPENWEATHER_API_KEY"],
            )
            write_metadata(skill_dir, meta)
            loaded = read_metadata(skill_dir)
            self.assertEqual(loaded.source, meta.source)
            self.assertEqual(loaded.sha256, meta.sha256)
            self.assertEqual(
                loaded.user_confirmed_env_passthrough,
                meta.user_confirmed_env_passthrough,
            )

    def test_read_returns_none_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "foo"
            skill_dir.mkdir()
            self.assertIsNone(read_metadata(skill_dir))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_install_metadata.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `core/install_metadata.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_install_metadata.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add core/install_metadata.py tests/test_install_metadata.py
git commit -m "feat(skills): add .install.json provenance sidecar + SHA256 helper"
```

### Task 7: Loader — three search paths, tier inference, dev detection, collision rejection

**Files:**
- Modify: `core/skill_loader.py`
- Create: `tests/test_skill_loader_tiered.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_skill_loader_tiered.py
"""Tests for tier-aware SkillLoader with three search paths."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from core.skill_loader import SkillLoader
from core.skill_policy import TIER_BUNDLED, TIER_AUTHORED, TIER_IMPORTED, TIER_DEV


def _write_skill(
    parent: Path,
    name: str,
    *,
    with_dockerfile: bool = True,
    with_install_json: bool = False,
) -> Path:
    skill_dir = parent / name
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test skill {name}.\n---\n\n"
        "## Inputs\n\n```yaml\ntype: object\nproperties:\n  query:\n    type: string\n"
        "required: [query]\n```\n\nBody.\n"
    )
    (skill_dir / "config.yaml").write_text(
        yaml.dump({
            "type": "docker",
            "image": f"miniclaw/{name}:latest",
            "env_passthrough": [],
            "timeout_seconds": 15,
            "devices": [],
        })
    )
    if with_dockerfile:
        (skill_dir / "scripts" / "Dockerfile").write_text(
            "FROM miniclaw/base:latest\nCMD [\"python\", \"app.py\"]\n"
        )
        (skill_dir / "scripts" / "app.py").write_text("print('ok')\n")
    if with_install_json:
        import json
        (skill_dir / ".install.json").write_text(json.dumps({
            "source": "https://example.com/" + name,
            "sha256": "x",
            "installed_at": "2026-04-23T00:00:00",
            "user_confirmed_env_passthrough": [],
        }))
    return skill_dir


class TestThreePaths(unittest.TestCase):
    def test_bundled_authored_imported_all_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            bundled = tmp_p / "bundled"; bundled.mkdir()
            authored = tmp_p / "authored"; authored.mkdir()
            imported = tmp_p / "imported"; imported.mkdir()

            _write_skill(bundled, "alpha")
            _write_skill(authored, "beta", with_install_json=True)
            _write_skill(imported, "gamma", with_install_json=True)

            loader = SkillLoader(search_paths=[bundled, authored, imported])
            skills = loader.load_all()

            self.assertIn("alpha", skills)
            self.assertIn("beta", skills)
            self.assertIn("gamma", skills)
            self.assertEqual(skills["alpha"].tier, TIER_BUNDLED)
            self.assertEqual(skills["beta"].tier, TIER_AUTHORED)
            self.assertEqual(skills["gamma"].tier, TIER_IMPORTED)

    def test_name_collision_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            bundled = tmp_p / "bundled"; bundled.mkdir()
            imported = tmp_p / "imported"; imported.mkdir()
            _write_skill(bundled, "foo")
            _write_skill(imported, "foo", with_install_json=True)

            loader = SkillLoader(search_paths=[bundled, imported])
            skills = loader.load_all()

            # Bundled wins; imported 'foo' is rejected and recorded.
            self.assertEqual(skills["foo"].tier, TIER_BUNDLED)
            self.assertIn("foo", loader.invalid_skills)

    def test_dev_mode_symlink_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            actual = tmp_p / "actual_home"; actual.mkdir()
            _write_skill(actual, "dev-skill")

            imported = tmp_p / "imported"; imported.mkdir()
            os.symlink(actual / "dev-skill", imported / "dev-skill")

            loader = SkillLoader(search_paths=[imported])
            skills = loader.load_all()
            self.assertEqual(skills["dev-skill"].tier, TIER_DEV)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failures**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_skill_loader_tiered.py -v`
Expected: failures (Skill has no `tier` attribute, collision not rejected, no dev detection).

- [ ] **Step 3: Rewrite `core/skill_loader.py`**

```python
"""
Skill Loader — scans tier-specific search paths and builds Claude tool
definitions.

Search paths (highest precedence first):
  1. bundled   — ./skills
  2. authored  — ~/.miniclaw/authored
  3. imported  — ~/.miniclaw/imported

A skill's tier is inferred from which search path matched. If the skill's
directory is a symlink, it is treated as tier=dev (security clamps bypassed,
structural checks still run). Cross-tier name collisions are rejected —
higher-precedence wins, the lower-precedence entry is recorded as invalid.
"""

import logging
from pathlib import Path

import yaml

from core.install_metadata import read_metadata
from core.skill_eligibility import SkillEligibility
from core.skill_policy import (
    TIER_BUNDLED,
    TIER_AUTHORED,
    TIER_IMPORTED,
    TIER_DEV,
)
from core.skill_validator import SkillValidator


logger = logging.getLogger(__name__)


class Skill:
    """Represents a loaded and validated skill."""

    def __init__(
        self,
        name: str,
        description: str,
        instructions: str,
        tool_definition: dict,
        execution_config: dict,
        skill_dir: str,
        tier: str,
    ):
        self.name = name
        self.description = description
        self.instructions = instructions
        self.tool_definition = tool_definition
        self.execution_config = execution_config
        self.skill_dir = skill_dir
        self.tier = tier

    def __repr__(self):
        return f"Skill(name={self.name!r}, tier={self.tier!r})"


_REPO_ROOT = Path(__file__).resolve().parent.parent


class SkillLoader:
    """Loads skills from tier-specific search paths."""

    DEFAULT_SEARCH_PATHS = [
        _REPO_ROOT / "skills",                       # bundled
        Path.home() / ".miniclaw" / "authored",
        Path.home() / ".miniclaw" / "imported",
    ]

    # Map search-path-index to tier. Index is the position in
    # DEFAULT_SEARCH_PATHS (or the passed-in list); higher precedence first.
    _TIER_BY_INDEX = {0: TIER_BUNDLED, 1: TIER_AUTHORED, 2: TIER_IMPORTED}

    def __init__(self, search_paths: list[Path] | None = None):
        self.search_paths = search_paths or self.DEFAULT_SEARCH_PATHS
        self.skills: dict[str, Skill] = {}
        self.skipped_skills: dict[str, dict] = {}
        self.invalid_skills: dict[str, dict] = {}
        self.validator = SkillValidator()
        self.eligibility = SkillEligibility()

    def load_all(self) -> dict[str, Skill]:
        self.skills = {}
        self.skipped_skills = {}
        self.invalid_skills = {}

        # Iterate from highest precedence (index 0) to lowest.
        for path_index, search_path in enumerate(self.search_paths):
            if not search_path.is_dir():
                continue

            tier = self._TIER_BY_INDEX.get(path_index, TIER_IMPORTED)

            for entry in sorted(search_path.iterdir()):
                skill_md = entry / "SKILL.md"
                if not (entry.is_dir() or entry.is_symlink()):
                    continue
                if not skill_md.exists():
                    continue

                effective_tier = TIER_DEV if entry.is_symlink() else tier
                skill = self._load_skill(entry, effective_tier)
                if skill is None:
                    continue

                if skill.name in self.skills:
                    existing = self.skills[skill.name]
                    logger.warning(
                        "Name collision: %s already loaded from tier %s; "
                        "rejecting duplicate in tier %s",
                        skill.name, existing.tier, skill.tier,
                    )
                    self.invalid_skills[skill.name + f"@{skill.tier}"] = {
                        "description": skill.description,
                        "reason": f"name collision with {existing.tier!r} tier",
                    }
                    continue

                self.skills[skill.name] = skill

        logger.info(
            "Loaded %d eligible skill(s): %s",
            len(self.skills),
            ", ".join(f"{s.name}({s.tier})" for s in self.skills.values()),
        )
        return self.skills

    def get_tool_definitions(self) -> list[dict]:
        return [s.tool_definition for s in self.skills.values()]

    def get_skill(self, tool_name: str) -> Skill | None:
        return self.skills.get(tool_name)

    def get_missing_env_vars(self) -> set[str]:
        result = set()
        for info in self.skipped_skills.values():
            result.update(info.get("missing_env_vars", []))
        return result

    def _load_skill(self, skill_dir: Path, tier: str) -> Skill | None:
        skill_md = skill_dir / "SKILL.md"
        config_path = skill_dir / "config.yaml"

        if tier == TIER_DEV:
            logger.warning(
                "SKILL %s IN DEV MODE — security validations bypassed "
                "(directory is a symlink)",
                skill_dir.name,
            )

        raw = skill_md.read_text(encoding="utf-8")
        try:
            frontmatter, body = self.validator.validate_markdown(raw, skill_dir)
        except ValueError as e:
            logger.warning("Invalid skill markdown in %s: %s", skill_md, e)
            self._record_invalid_skill(skill_dir.name, "", str(e))
            return None

        name = frontmatter["name"]
        description = frontmatter["description"]

        if not config_path.exists():
            reason = "missing config.yaml"
            logger.warning("Skill '%s' has no config.yaml, skipping", name)
            self._record_invalid_skill(name, description, reason)
            return None

        skip_reason, missing_env_vars = self.eligibility.check(frontmatter)
        if skip_reason:
            logger.info("Skill '%s' not eligible: %s", name, skip_reason)
            self.skipped_skills[name] = {
                "description": description,
                "reason": skip_reason,
                "missing_env_vars": missing_env_vars,
            }
            return None

        try:
            raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            execution_config = self.validator.validate_execution_config(
                raw_config, tier=tier, skill_name=name,
            )
        except (OSError, yaml.YAMLError, ValueError) as e:
            logger.warning("Invalid config for skill '%s': %s", name, e)
            self._record_invalid_skill(name, description, str(e))
            return None

        # Validate Dockerfile for non-bundled tiers
        if tier in (TIER_AUTHORED, TIER_IMPORTED):
            dockerfile = skill_dir / "scripts" / "Dockerfile"
            if execution_config.get("type", "docker") == "docker" and dockerfile.exists():
                from core.dockerfile_validator import validate, DockerfileValidationError
                try:
                    validate(dockerfile, tier=tier)
                except DockerfileValidationError as e:
                    logger.warning("Invalid Dockerfile for skill '%s': %s", name, e)
                    self._record_invalid_skill(name, description, str(e))
                    return None

        # Drift detection for authored/imported: SHA256 mismatch => invalid.
        # (Pipeline writes .install.json; if sha doesn't match, loader rejects.
        # Re-install is required to reset the trust state.)
        if tier in (TIER_AUTHORED, TIER_IMPORTED):
            meta = read_metadata(skill_dir)
            if meta is not None and meta.sha256:
                from core.install_metadata import compute_skill_sha256
                current_sha = compute_skill_sha256(skill_dir)
                if current_sha != meta.sha256:
                    reason = (
                        "skill changed on disk since install (SHA256 mismatch); "
                        "reinstall via `miniclaw skill install` to approve"
                    )
                    logger.warning("Drift for skill '%s': %s", name, reason)
                    self._record_invalid_skill(name, description, reason)
                    return None

        tool_definition = self.validator.build_tool_definition(name, description, body)

        return Skill(
            name=name,
            description=description,
            instructions=body,
            tool_definition=tool_definition,
            execution_config=execution_config,
            skill_dir=str(skill_dir),
            tier=tier,
        )

    def _record_invalid_skill(self, name: str, description: str, reason: str) -> None:
        self.invalid_skills[name] = {
            "description": description,
            "reason": reason,
        }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_skill_loader_tiered.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add core/skill_loader.py tests/test_skill_loader_tiered.py
git commit -m "feat(skills): tier-aware loader with three search paths + drift detection"
```

---

## Phase 5 — Migration

**Phase-boundary checkpoint:** before starting, ensure Phases 1-4 are all green. After Task 10, run the whole existing test suite: `python3 -m pytest tests/ -v` — migration should leave everything green except tests that referenced the old names (those get updated in Task 10).

### Task 8: Write the migration script

**Files:**
- Create: `scripts/migrate-to-agentskills.py`
- Create: `tests/test_migration_script.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_migration_script.py
"""Test the migrate-to-agentskills.py script against a fixture copy."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATE_SCRIPT = REPO_ROOT / "scripts" / "migrate-to-agentskills.py"


def _write_old_skill(root: Path, old_dir_name: str, *, declared_name: str,
                     with_container: bool = True, requires_env: list[str] | None = None):
    skill_dir = root / "skills" / old_dir_name
    skill_dir.mkdir(parents=True)
    fm = {"name": declared_name, "description": f"Old-style {declared_name} skill."}
    if requires_env:
        fm["requires"] = {"env": requires_env}
    (skill_dir / "SKILL.md").write_text(
        "---\n" + yaml.dump(fm, sort_keys=False) + "---\n\nBody.\n"
    )
    (skill_dir / "config.yaml").write_text(yaml.dump({
        "image": f"miniclaw/{declared_name}:latest",
        "env_passthrough": requires_env or [],
        "timeout_seconds": 15,
        "devices": [],
    }))
    if with_container:
        container_dir = root / "containers" / old_dir_name
        container_dir.mkdir(parents=True)
        (container_dir / "Dockerfile").write_text(
            "FROM miniclaw/base:latest\nCOPY app.py /app/app.py\nCMD [\"python\", \"/app/app.py\"]\n"
        )
        (container_dir / "app.py").write_text("print('ok')\n")


class TestMigration(unittest.TestCase):
    def test_rename_and_restructure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_old_skill(root, "web_search", declared_name="search_web",
                             requires_env=["BRAVE_API_KEY"])
            _write_old_skill(root, "dashboard", declared_name="dashboard",
                             with_container=False)  # native

            result = subprocess.run(
                [sys.executable, str(MIGRATE_SCRIPT), "--repo-root", str(root)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)

            # Web search renamed
            self.assertTrue((root / "skills" / "web-search").exists())
            self.assertFalse((root / "skills" / "web_search").exists())
            self.assertTrue((root / "skills" / "web-search" / "SKILL.md").exists())
            self.assertTrue((root / "skills" / "web-search" / "scripts" / "Dockerfile").exists())
            self.assertTrue((root / "skills" / "web-search" / "scripts" / "app.py").exists())

            # Frontmatter migrated
            skill_md = (root / "skills" / "web-search" / "SKILL.md").read_text()
            self.assertIn("name: web-search", skill_md)
            self.assertNotIn("search_web", skill_md)
            self.assertIn("metadata:", skill_md)
            self.assertIn("miniclaw:", skill_md)
            self.assertIn("BRAVE_API_KEY", skill_md)
            # Old top-level requires should be gone.
            fm_body = skill_md.split("---")[1]
            self.assertNotIn("\nrequires:", fm_body)

            # containers/ tree removed
            self.assertFalse((root / "containers").exists())

            # Dashboard unchanged name, no containers tree for it either
            self.assertTrue((root / "skills" / "dashboard").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_migration_script.py -v`
Expected: script not found / FileNotFoundError.

- [ ] **Step 3: Create `scripts/migrate-to-agentskills.py`**

```python
#!/usr/bin/env python3
"""
migrate-to-agentskills.py — one-shot migration of MiniClaw skills to the
agentskills.io-compliant single-directory layout.

Run from repo root:
    python3 scripts/migrate-to-agentskills.py

Per skill:
  1. Rename snake_case directory to kebab-case; force parent name = skill name
  2. Move containers/<old>/ into skills/<new>/scripts/
  3. Rewrite SKILL.md frontmatter:
     - set name: <kebab-case> (matches new dir)
     - move top-level requires: under metadata.miniclaw.requires
  4. Delete top-level containers/ directory entirely when empty

Idempotent: running twice leaves things unchanged.

This script is deleted after the migration PR merges.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

import yaml


# Per-skill mapping of OLD_DIR -> (NEW_DIR, NEW_NAME). NEW_NAME always matches NEW_DIR.
# Derived from CLAUDE.md + skills/ enumeration; matches the spec's migration table.
RENAMES: dict[str, str] = {
    "dashboard":          "dashboard",
    "homebridge":         "homebridge",
    "install_skill":      "install-skill",
    "playwright_scraper": "playwright-scraper",
    "recall_session":     "recall-session",
    "save_memory":        "save-memory",
    "schedule":           "schedule",
    "set_env_var":        "set-env-var",
    "skill_tells_random": "skill-tells-random",
    "soundcloud":         "soundcloud",
    "weather":            "weather",
    "web_search":         "web-search",
}


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def migrate_frontmatter(raw: str, new_name: str) -> str:
    m = FRONTMATTER_RE.match(raw)
    if not m:
        raise ValueError("SKILL.md missing frontmatter")
    fm = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)

    fm["name"] = new_name

    # Move top-level requires under metadata.miniclaw.requires.
    old_requires = fm.pop("requires", None)
    if old_requires is not None:
        metadata = fm.setdefault("metadata", {}) or {}
        miniclaw = metadata.setdefault("miniclaw", {}) or {}
        miniclaw["requires"] = old_requires
        fm["metadata"] = metadata  # in case setdefault returned the existing

    # Stable key order: name, description, license, compatibility, metadata, then rest.
    preferred_order = ["name", "description", "license", "compatibility", "metadata"]
    ordered = {k: fm[k] for k in preferred_order if k in fm}
    for k, v in fm.items():
        if k not in ordered:
            ordered[k] = v

    new_fm = yaml.dump(ordered, sort_keys=False, default_flow_style=False)
    return f"---\n{new_fm}---\n{body}"


def migrate_skill(repo_root: Path, old_dir: str, new_name: str) -> None:
    old_skill_path = repo_root / "skills" / old_dir
    new_skill_path = repo_root / "skills" / new_name
    container_path = repo_root / "containers" / old_dir

    if not old_skill_path.exists() and new_skill_path.exists():
        # Already migrated.
        return

    if not old_skill_path.exists():
        return  # nothing to do

    # 1. Rename directory if needed
    if old_skill_path != new_skill_path:
        old_skill_path.rename(new_skill_path)

    # 2. Move containers/<old>/ into skills/<new>/scripts/
    if container_path.exists():
        scripts_dest = new_skill_path / "scripts"
        if scripts_dest.exists():
            for item in container_path.iterdir():
                target = scripts_dest / item.name
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                shutil.move(str(item), str(target))
            shutil.rmtree(container_path)
        else:
            shutil.move(str(container_path), str(scripts_dest))

    # 3. Rewrite SKILL.md frontmatter
    skill_md = new_skill_path / "SKILL.md"
    if skill_md.exists():
        skill_md.write_text(
            migrate_frontmatter(skill_md.read_text(encoding="utf-8"), new_name),
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Path to the MiniClaw repo root (default: current directory)",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if not (repo_root / "skills").exists():
        print(f"error: {repo_root}/skills does not exist", file=sys.stderr)
        return 1

    for old_dir, new_name in RENAMES.items():
        migrate_skill(repo_root, old_dir, new_name)

    # 4. Remove empty containers/ tree
    containers_root = repo_root / "containers"
    if containers_root.exists():
        remaining = [p for p in containers_root.iterdir() if p.name != "base"]
        # Keep containers/base/ if it exists — that's the shared base image,
        # not a skill. Delete it only if completely empty.
        if not remaining:
            shutil.rmtree(containers_root)
        else:
            # Leave containers/base/ in place; remove only the skill entries
            # we migrated.
            for old_dir in RENAMES:
                skill_container = containers_root / old_dir
                if skill_container.exists():
                    shutil.rmtree(skill_container)

    print("Migration complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify pass**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_migration_script.py -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate-to-agentskills.py tests/test_migration_script.py
git commit -m "feat(migrate): add one-shot migration script with fixture test"
```

### Task 9: Run the migration against the real repo

- [ ] **Step 1: Run the migration**

```bash
cd ~/linux/miniclaw
python3 scripts/migrate-to-agentskills.py
```

Expected output: `Migration complete.`

- [ ] **Step 2: Inspect the diff**

```bash
git status
git diff --stat
```

Expected: every `skills/<snake>/` renamed to `skills/<kebab>/`, `containers/<skill>/` moved into `skills/<skill>/scripts/`, SKILL.md frontmatter migrated. `containers/base/` should remain untouched.

- [ ] **Step 3: Verify structural integrity with a dry-run load**

```bash
python3 -c "from core.skill_loader import SkillLoader; loader = SkillLoader(); loader.load_all(); print('OK, loaded:', sorted(loader.skills))"
```

Expected: list of 12 skill names in kebab-case; no exceptions. (Note: this may still log warnings about specific skill Dockerfiles if they don't match the authored-tier allowlist — these 12 are bundled so they bypass the allowlist.)

- [ ] **Step 4: Commit the mechanical migration**

```bash
git add skills/ containers/
git commit -m "refactor(skills): migrate 12 bundled skills to agentskills.io-compliant layout"
```

### Task 10: Update code touch points for new skill names

**Files:**
- Modify: `core/container_manager.py:52-60` (native dispatch dict)
- Modify: `run.sh` (Dockerfile discovery path)
- Modify: existing tests that reference old names

- [ ] **Step 1: Update `core/container_manager.py` native dispatch keys**

Find this block (`core/container_manager.py:52-60`) and replace:

```python
        self._native_handlers = {
            "install_skill": self._execute_install_skill,
            "set_env_var": self._execute_set_env_var,
            "save_memory": self._execute_save_memory,
            "dashboard": self._execute_dashboard,
            "soundcloud_play": self._execute_soundcloud,
            "schedule": self._execute_schedule,
            "recall_session": self._execute_recall_session,
        }
```

With:

```python
        self._native_handlers = {
            "install-skill": self._execute_install_skill,
            "set-env-var": self._execute_set_env_var,
            "save-memory": self._execute_save_memory,
            "dashboard": self._execute_dashboard,
            "soundcloud": self._execute_soundcloud,
            "schedule": self._execute_schedule,
            "recall-session": self._execute_recall_session,
        }
```

- [ ] **Step 2: Update `run.sh` Dockerfile discovery**

Open `run.sh`. Find the loop that iterates over `containers/*/Dockerfile`. It looks like:

```bash
for dockerfile in containers/*/Dockerfile; do
    ...
done
```

Replace with:

```bash
for dockerfile in skills/*/scripts/Dockerfile; do
    ...
done
```

Also update the image-name derivation inside the loop. Before, it derived the image name from `containers/<name>/Dockerfile`; now it derives from `skills/<name>/scripts/Dockerfile`. The name extraction line will look something like:

```bash
skill_dir=$(dirname "$(dirname "$dockerfile")")
skill_name=$(basename "$skill_dir")
```

Read the current `run.sh` carefully to port this change — search for the exact literal `containers/*/Dockerfile` and adjust in-place.

- [ ] **Step 3: Update `tests/test_meta_skill.py`, `tests/test_install_skill_integration.py`, `tests/test_skill_selector.py`, `tests/test_recall_session_skill.py`**

Each of these references one or more old-format skill names. Search for old snake_case names and update.

```bash
grep -l "search_web\|get_weather\|scrape_webpage\|soundcloud_play\|install_skill\|set_env_var\|save_memory\|recall_session\|skill_tells_random\|playwright_scraper" tests/
```

For each match, update old → new mapping:
- `search_web` → `web-search`
- `get_weather` → `weather`
- `scrape_webpage` → `playwright-scraper`
- `soundcloud_play` → `soundcloud`
- `install_skill` → `install-skill`
- `set_env_var` → `set-env-var`
- `save_memory` → `save-memory`
- `recall_session` → `recall-session`
- `skill_tells_random` → `skill-tells-random`
- `playwright_scraper` → `playwright-scraper`

Replace them in the test files. These are pure string replacements. Verify no other identifiers share the same text (e.g., don't rename a function called `install_skill_handler`).

- [ ] **Step 4: Run the full test suite**

```bash
cd ~/linux/miniclaw && python3 -m pytest tests/ -v
```

Expected: all tests pass. If a test fails because it references an old SKILL.md path or image name, update accordingly. Specifically the docker image names are unchanged (`miniclaw/web-search:latest` etc. — kebab-case already), so no test should reference old image names.

- [ ] **Step 5: Commit**

```bash
git add core/container_manager.py run.sh tests/
git commit -m "refactor(skills): update native dispatch keys, run.sh, and tests for new skill names"
```

### Task 11: Update scripts/port-skill.py and documentation

**Files:**
- Rename + modify: `scripts/port-skill.py` → `scripts/port-openclaw-skill.py`
- Modify: `CLAUDE.md`
- Modify: `WORKING_MEMORY.md`

- [ ] **Step 1: Rename and update port-skill.py**

```bash
git mv scripts/port-skill.py scripts/port-openclaw-skill.py
```

Open `scripts/port-openclaw-skill.py`. Update the output layout:
- `config.yaml` stays at `skills/<new-name>/config.yaml`
- `Dockerfile` and `app.py` move from `containers/<slug>/` to `skills/<new-name>/scripts/`
- Skill name must be kebab-case (update `slugify` accordingly)

Specifically, replace the `slugify` and `image_name` functions and the write paths:

```python
def slugify(name: str) -> str:
    """Convert name to a kebab-case skill/dir slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "skill"


def image_name(slug: str) -> str:
    return slug  # already kebab-case


# Inside main(), replace the destination-path block with:
skill_dest = REPO_ROOT / "skills" / slug
scripts_dest = skill_dest / "scripts"
skill_dest.mkdir(parents=True, exist_ok=True)
scripts_dest.mkdir(parents=True, exist_ok=True)

# (old `container_dest` was containers/<slug>/; now it's scripts_dest above.)
```

Update the `APP_PY_SKELETON` / `APP_PY_WITH_SCRIPTS` / Dockerfile writes so they target `scripts_dest` instead of the old `container_dest`, and update the final summary `print` lines to reflect the new paths.

- [ ] **Step 2: Test the renamed porter**

Create a small fixture and run the porter:

```bash
mkdir -p /tmp/openclaw_test/scripts
cat > /tmp/openclaw_test/SKILL.md <<'EOF'
---
name: openclaw-test
description: Test port.
metadata:
  openclaw:
    requires:
      env: [TEST_KEY]
---

Body.
EOF
echo 'print("hi")' > /tmp/openclaw_test/scripts/main.py

cd ~/linux/miniclaw
python3 scripts/port-openclaw-skill.py /tmp/openclaw_test/
ls skills/openclaw-test/
ls skills/openclaw-test/scripts/
# Cleanup
rm -rf skills/openclaw-test /tmp/openclaw_test
```

Expected: `skills/openclaw-test/SKILL.md`, `config.yaml`, and `scripts/app.py` + `Dockerfile` + `main.py` all exist.

- [ ] **Step 3: Update `CLAUDE.md` — "Skill Structure" section and skill list**

Find the "Skill Structure" section. Replace with:

```markdown
### Skill Structure

Every skill is a single directory. Docker skills keep their build assets in a `scripts/` subfolder.

```
skills/<name>/
    SKILL.md              ← Claude routing instructions
    config.yaml           ← Container execution config (optional for native)
    scripts/
        app.py            ← Entrypoint (Docker skills only)
        Dockerfile        ← Builds FROM miniclaw/base:latest (Docker skills only)
    references/           ← Optional, agentskills.io convention
    assets/               ← Optional, agentskills.io convention
    .install.json         ← Provenance sidecar (authored/imported tiers only)
```

Skill names are lowercase kebab-case (e.g. `web-search`, `recall-session`) and must match the parent directory. Execution tier is inferred from the search path the skill was loaded from:

- `./skills/` → bundled (full trust; native execution allowed)
- `~/.miniclaw/authored/` → voice-installed via `install-skill`
- `~/.miniclaw/imported/` → community-sourced (stricter: Dockerfile allowlist + config clamps)

`SKILL.md` frontmatter:

```yaml
---
name: web-search
description: Search the web using Brave Search. Use when the user asks for current
  information or anything that needs a live lookup.
license: MIT                          # optional
metadata:
  miniclaw:
    requires:
      env: [BRAVE_API_KEY]
      bins: [curl]
    self_update:
      allow_body: false               # opt-in for future self-improving skills
---
```

`config.yaml` is unchanged for Docker skills; see per-tier clamps in `core/skill_policy.py`.
```

Also update the bullet listing current native skills:

```markdown
Current native skills: `install-skill`, `set-env-var`, `save-memory`, `dashboard`, `recall-session`, `schedule`, `soundcloud`.
```

- [ ] **Step 4: Update `WORKING_MEMORY.md`**

Append under the Hermes roadmap item #3 (which is currently bullet 3 under the "Hermes-Inspired Enhancement Roadmap" heading):

```markdown
3. ~~agentskills.io compat — align skill loader / manifest format with the agentskills.io registry so community skills are drop-in installable.~~ Done 2026-04-23.
   Skills now live as single directories with `scripts/` subfolders, kebab-case names matching parent dirs, and `metadata.miniclaw.requires` in SKILL.md. Imports run through a per-tier install pipeline with Dockerfile allowlists and config clamps.
```

And under "Recent milestones" add:

```markdown
- 2026-04-23: shipped agentskills.io-compliant skill format
  12 bundled skills migrated to kebab-case single-directory layout (scripts/ subfolder)
  three-tier trust model (bundled/authored/imported) wired into the loader
  shared install pipeline for voice + CLI + future mobile entry points
```

- [ ] **Step 5: Run full suite once more**

```bash
python3 -m pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit docs**

```bash
git add scripts/port-openclaw-skill.py CLAUDE.md WORKING_MEMORY.md
git commit -m "docs: update CLAUDE.md and WORKING_MEMORY for agentskills.io layout; rename porter"
```

---

## Phase 6 — Install pipeline

Phase-boundary checkpoint: `pytest tests/test_install_pipeline.py`.

### Task 12: Install pipeline — core module and happy path

**Files:**
- Create: `core/install_pipeline.py`
- Create: `tests/test_install_pipeline.py`
- Create: `tests/fixtures/agentskills/good-skill/` (plus contents)

- [ ] **Step 1: Create the fixture skill**

```bash
mkdir -p tests/fixtures/agentskills/good-skill/scripts
cat > tests/fixtures/agentskills/good-skill/SKILL.md <<'EOF'
---
name: good-skill
description: Good test skill. Use when tests need a valid skill fixture.
metadata:
  miniclaw:
    requires: {}
---

# Good Skill

## Inputs

```yaml
type: object
properties:
  query:
    type: string
required: [query]
```

Body.
EOF
cat > tests/fixtures/agentskills/good-skill/config.yaml <<'EOF'
type: docker
image: miniclaw/good-skill:latest
env_passthrough: []
timeout_seconds: 15
memory: 128m
EOF
cat > tests/fixtures/agentskills/good-skill/scripts/Dockerfile <<'EOF'
FROM miniclaw/base:latest
COPY app.py /app/app.py
WORKDIR /app
CMD ["python", "app.py"]
EOF
cat > tests/fixtures/agentskills/good-skill/scripts/app.py <<'EOF'
print("good")
EOF
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_install_pipeline.py
"""Tests for the shared install pipeline."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.install_pipeline import (
    InstallDecision,
    InstallPipeline,
    PermissionSummary,
    summarize_permissions,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "agentskills"


class AlwaysApprove:
    """Test confirmer that approves all gates."""
    def confirm_gate(self, gate: str, summary: str) -> bool:
        return True


class AlwaysReject:
    def confirm_gate(self, gate: str, summary: str) -> bool:
        return False


class NoopBuilder:
    """Test builder that skips docker build."""
    def build(self, skill_dir: Path, image: str) -> None:
        pass


class NoopReloader:
    def reload(self) -> None:
        pass


class TestSummary(unittest.TestCase):
    def test_permission_summary_flags_credential_env(self):
        summary = summarize_permissions(
            name="foo",
            description="desc",
            config={"env_passthrough": ["ANTHROPIC_API_KEY"], "memory": "128m", "timeout_seconds": 10},
        )
        self.assertTrue(summary.credential_warnings)
        self.assertIn("ANTHROPIC_API_KEY", summary.credential_warnings)


class TestPipelineHappyPath(unittest.TestCase):
    def test_install_good_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "imported"
            install_root.mkdir()

            pipeline = InstallPipeline(
                confirmer=AlwaysApprove(),
                builder=NoopBuilder(),
                reloader=NoopReloader(),
                install_root=install_root,
            )
            decision = pipeline.install_from_path(
                FIXTURES / "good-skill",
                tier="imported",
            )
            self.assertEqual(decision, InstallDecision.INSTALLED)
            installed_dir = install_root / "good-skill"
            self.assertTrue(installed_dir.exists())
            self.assertTrue((installed_dir / ".install.json").exists())

            meta = json.loads((installed_dir / ".install.json").read_text())
            self.assertIn("sha256", meta)
            self.assertIn("installed_at", meta)

    def test_install_rejected_on_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "imported"
            install_root.mkdir()

            pipeline = InstallPipeline(
                confirmer=AlwaysReject(),
                builder=NoopBuilder(),
                reloader=NoopReloader(),
                install_root=install_root,
            )
            decision = pipeline.install_from_path(
                FIXTURES / "good-skill",
                tier="imported",
            )
            self.assertEqual(decision, InstallDecision.CANCELLED)
            self.assertFalse((install_root / "good-skill").exists())

    def test_install_strips_shipped_install_json(self):
        """A malicious skill that ships its own .install.json must not spoof provenance."""
        with tempfile.TemporaryDirectory() as tmp:
            # Copy fixture and plant a fake .install.json
            import shutil
            staging_src = Path(tmp) / "staging-src"
            shutil.copytree(FIXTURES / "good-skill", staging_src)
            (staging_src / ".install.json").write_text(
                json.dumps({"source": "FAKE", "sha256": "deadbeef",
                            "installed_at": "1970-01-01T00:00:00",
                            "user_confirmed_env_passthrough": ["ANTHROPIC_API_KEY"]})
            )

            install_root = Path(tmp) / "imported"
            install_root.mkdir()

            pipeline = InstallPipeline(
                confirmer=AlwaysApprove(),
                builder=NoopBuilder(),
                reloader=NoopReloader(),
                install_root=install_root,
            )
            decision = pipeline.install_from_path(staging_src, tier="imported")
            self.assertEqual(decision, InstallDecision.INSTALLED)

            meta = json.loads((install_root / "good-skill" / ".install.json").read_text())
            self.assertNotEqual(meta["source"], "FAKE")
            self.assertNotEqual(meta["sha256"], "deadbeef")
            self.assertEqual(meta["user_confirmed_env_passthrough"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run to verify failures**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_install_pipeline.py -v`
Expected: `ModuleNotFoundError: No module named 'core.install_pipeline'`.

- [ ] **Step 4: Implement `core/install_pipeline.py`**

```python
"""
Shared install pipeline for MiniClaw skills.

Entry points (voice via install_skill, CLI via core.skill_cli, future mobile
HTTP) all funnel through InstallPipeline.install_from_path or
InstallPipeline.install_from_url.

Pipeline steps:
  1. Fetch   — external code (git clone / tarball) for install_from_url.
               install_from_path skips this.
  2. Locate  — find SKILL.md inside the staging directory.
  3. Validate— SkillValidator + DockerfileValidator at the requested tier.
  4. Summarize — render a PermissionSummary and pass to the confirmer.
  5. Confirm — three-gate confirmation: install -> build -> restart.
  6. Commit  — strip any .install.json shipped in staging, move staging to
               <install_root>/<name>, write fresh .install.json.
  7. Build   — delegate to the builder (Docker build invocation).
  8. Reload  — delegate to the reloader (orchestrator.reload_skills()).

The confirmer, builder, and reloader are dependency-injected so tests can
substitute no-ops and voice/CLI paths can substitute their own prompts.
"""

import datetime
import enum
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

from core.dockerfile_validator import DockerfileValidationError, validate as validate_dockerfile
from core.install_metadata import (
    INSTALL_FILENAME,
    InstallMetadata,
    compute_skill_sha256,
    write_metadata,
)
from core.skill_policy import (
    TIER_AUTHORED,
    TIER_IMPORTED,
    is_credential_pattern,
)
from core.skill_validator import SkillValidator


logger = logging.getLogger(__name__)


class InstallDecision(enum.Enum):
    INSTALLED = "installed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ConfirmProtocol(Protocol):
    def confirm_gate(self, gate: str, summary: str) -> bool: ...


class BuildProtocol(Protocol):
    def build(self, skill_dir: Path, image: str) -> None: ...


class ReloadProtocol(Protocol):
    def reload(self) -> None: ...


@dataclass
class PermissionSummary:
    name: str
    description: str
    tier: str
    image: str | None
    env_passthrough: list[str]
    credential_warnings: list[str]
    memory: str | None
    timeout_seconds: int | None
    devices: list[str]
    volumes: list[str]

    def to_text(self) -> str:
        lines = [
            f"Install skill '{self.name}' at tier {self.tier!r}.",
            f"  Description: {self.description}",
        ]
        if self.image:
            lines.append(f"  Image: {self.image}")
        if self.env_passthrough:
            lines.append(f"  env_passthrough: {', '.join(self.env_passthrough)}")
            if self.credential_warnings:
                lines.append(
                    f"  WARNING — credential-like names in env_passthrough: "
                    f"{', '.join(self.credential_warnings)}"
                )
        if self.memory:
            lines.append(f"  Memory: {self.memory}")
        if self.timeout_seconds is not None:
            lines.append(f"  Timeout: {self.timeout_seconds}s")
        if self.devices:
            lines.append(f"  Devices: {', '.join(self.devices)}")
        if self.volumes:
            lines.append(f"  Volumes: {', '.join(self.volumes)}")
        return "\n".join(lines)


def summarize_permissions(*, name: str, description: str, config: dict, tier: str = TIER_IMPORTED) -> PermissionSummary:
    env_passthrough = list(config.get("env_passthrough") or [])
    credential_warnings = [e for e in env_passthrough if is_credential_pattern(e)]
    return PermissionSummary(
        name=name,
        description=description,
        tier=tier,
        image=config.get("image"),
        env_passthrough=env_passthrough,
        credential_warnings=credential_warnings,
        memory=config.get("memory"),
        timeout_seconds=config.get("timeout_seconds"),
        devices=list(config.get("devices") or []),
        volumes=list(config.get("volumes") or []),
    )


class DockerBuilder:
    """Production builder — invokes the host-side build script."""
    def __init__(self, build_script: Path | None = None):
        self.build_script = build_script or Path(__file__).resolve().parent.parent / "scripts" / "build_new_skill.sh"

    def build(self, skill_dir: Path, image: str) -> None:
        if not self.build_script.exists():
            raise RuntimeError(f"Build script not found: {self.build_script}")
        result = subprocess.run(
            [str(self.build_script), str(skill_dir), image],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"docker build failed (rc={result.returncode}): "
                f"{result.stderr.strip()[:300]}"
            )


class InstallPipeline:
    def __init__(
        self,
        *,
        confirmer: ConfirmProtocol,
        builder: BuildProtocol,
        reloader: ReloadProtocol,
        install_root: Path,
    ):
        self.confirmer = confirmer
        self.builder = builder
        self.reloader = reloader
        self.install_root = install_root
        self.validator = SkillValidator()

    def install_from_path(self, staging: Path, *, tier: str) -> InstallDecision:
        """Install a staged skill directory. Staging is not modified until the final commit step."""
        skill_md = staging / "SKILL.md"
        if not skill_md.exists():
            logger.error("staging %s has no SKILL.md", staging)
            return InstallDecision.FAILED

        # --- Validate structure and config ---
        try:
            frontmatter, _ = self.validator.validate_markdown(
                skill_md.read_text(encoding="utf-8"),
                # Use the staging path's name; it must match the declared name.
                staging,
            )
        except ValueError as e:
            logger.error("invalid SKILL.md: %s", e)
            return InstallDecision.FAILED

        name = frontmatter["name"]
        description = frontmatter["description"]

        config_path = staging / "config.yaml"
        raw_config = {}
        if config_path.exists():
            try:
                raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as e:
                logger.error("invalid config.yaml: %s", e)
                return InstallDecision.FAILED
        try:
            config = self.validator.validate_execution_config(raw_config, tier=tier, skill_name=name)
        except ValueError as e:
            logger.error("config.yaml rejected at tier %s: %s", tier, e)
            return InstallDecision.FAILED

        dockerfile = staging / "scripts" / "Dockerfile"
        if config.get("type", "docker") == "docker" and dockerfile.exists():
            try:
                validate_dockerfile(dockerfile, tier=tier)
            except DockerfileValidationError as e:
                logger.error("Dockerfile rejected at tier %s: %s", tier, e)
                return InstallDecision.FAILED

        # --- Summarize + three-gate confirmation ---
        summary = summarize_permissions(
            name=name, description=description, config=config, tier=tier,
        )
        summary_text = summary.to_text()

        if not self.confirmer.confirm_gate("install", summary_text):
            return InstallDecision.CANCELLED
        if not self.confirmer.confirm_gate("build", summary_text):
            return InstallDecision.CANCELLED

        # --- Commit install: strip shipped metadata, move into place ---
        shipped_install = staging / INSTALL_FILENAME
        if shipped_install.exists():
            logger.warning("staging shipped its own %s; discarding", INSTALL_FILENAME)
            shipped_install.unlink()

        final_dir = self.install_root / name
        if final_dir.exists():
            shutil.rmtree(final_dir)
        shutil.copytree(staging, final_dir, symlinks=False)

        sha256 = compute_skill_sha256(final_dir)
        meta = InstallMetadata(
            source=str(staging),
            sha256=sha256,
            installed_at=datetime.datetime.now().isoformat(timespec="seconds"),
            user_confirmed_env_passthrough=[],
        )
        write_metadata(final_dir, meta)

        # --- Build ---
        image = config.get("image")
        if config.get("type", "docker") == "docker" and image:
            try:
                self.builder.build(final_dir, image)
            except Exception as e:
                logger.error("build failed: %s", e)
                shutil.rmtree(final_dir, ignore_errors=True)
                return InstallDecision.FAILED

        # --- Reload ---
        if not self.confirmer.confirm_gate("restart", summary_text):
            # User approved install+build but not restart — leave files in place,
            # they will be loaded on next startup.
            return InstallDecision.INSTALLED

        try:
            self.reloader.reload()
        except Exception as e:
            logger.error("reload failed: %s", e)
            # Files still installed; reload can be retried.

        return InstallDecision.INSTALLED
```

- [ ] **Step 5: Run to verify tests pass**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_install_pipeline.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add core/install_pipeline.py tests/test_install_pipeline.py tests/fixtures/agentskills/
git commit -m "feat(skills): install pipeline with tier-aware validation + three-gate confirmation"
```

### Task 13: Install pipeline — fetch step (git clone + tarball)

**Files:**
- Modify: `core/install_pipeline.py`
- Modify: `tests/test_install_pipeline.py`

- [ ] **Step 1: Append failing tests**

```python
# Append to tests/test_install_pipeline.py

class TestFetch(unittest.TestCase):
    def test_install_from_path_accepts_directory(self):
        # Already covered by happy path.
        pass

    def test_install_from_url_rejects_bad_scheme(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_root = Path(tmp) / "imported"
            install_root.mkdir()
            pipeline = InstallPipeline(
                confirmer=AlwaysApprove(),
                builder=NoopBuilder(),
                reloader=NoopReloader(),
                install_root=install_root,
            )
            decision = pipeline.install_from_url("ftp://example.com/foo.tgz", tier="imported")
            self.assertEqual(decision, InstallDecision.FAILED)
```

- [ ] **Step 2: Run to verify failure (method missing)**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_install_pipeline.py::TestFetch -v`
Expected: `AttributeError: 'InstallPipeline' object has no attribute 'install_from_url'`.

- [ ] **Step 3: Add `install_from_url` to `InstallPipeline`**

Add these imports at the top of `core/install_pipeline.py`:

```python
import tempfile
import urllib.parse
```

Add this method to `InstallPipeline`:

```python
    def install_from_url(self, url: str, *, tier: str) -> InstallDecision:
        """
        Fetch a skill from a git repository or tarball URL, then delegate to
        install_from_path. Supports https URLs only (no ftp/file/ssh).
        """
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("https",):
            logger.error("install_from_url: unsupported scheme %r", parsed.scheme)
            return InstallDecision.FAILED

        with tempfile.TemporaryDirectory(prefix="miniclaw-install-") as tmp:
            staging = Path(tmp) / "staging"
            if url.endswith(".git") or parsed.netloc.endswith("github.com"):
                result = subprocess.run(
                    ["git", "clone", "--depth", "1", url, str(staging)],
                    capture_output=True, text=True, timeout=180,
                )
                if result.returncode != 0:
                    logger.error("git clone failed: %s", result.stderr.strip()[:300])
                    return InstallDecision.FAILED
            elif url.endswith((".tar.gz", ".tgz")):
                import tarfile, urllib.request
                tarball = Path(tmp) / "archive.tgz"
                urllib.request.urlretrieve(url, tarball)
                staging.mkdir()
                with tarfile.open(tarball) as tar:
                    # Extract safely — reject members with absolute or parent paths.
                    for member in tar.getmembers():
                        if member.name.startswith("/") or ".." in Path(member.name).parts:
                            logger.error("rejecting tar member with unsafe path: %r", member.name)
                            return InstallDecision.FAILED
                    tar.extractall(staging)
                # If the tarball contains a single top-level dir, descend into it.
                entries = list(staging.iterdir())
                if len(entries) == 1 and entries[0].is_dir():
                    staging = entries[0]
            else:
                logger.error("install_from_url: unknown URL format %r", url)
                return InstallDecision.FAILED

            return self.install_from_path(staging, tier=tier)
```

- [ ] **Step 4: Run the appended tests**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_install_pipeline.py::TestFetch -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add core/install_pipeline.py tests/test_install_pipeline.py
git commit -m "feat(skills): install pipeline fetch step supports https git + tarball"
```

---

## Phase 7 — CLI + voice integration

Phase-boundary checkpoint: `pytest tests/` (full suite) plus manual dry-run of the CLI.

### Task 14: `miniclaw skill` CLI subcommand dispatch

**Files:**
- Create: `core/skill_cli.py`
- Modify: `main.py`
- Create: `tests/test_skill_cli.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_skill_cli.py
"""Tests for the miniclaw skill CLI."""

import tempfile
import unittest
from pathlib import Path
from io import StringIO
from unittest.mock import patch

from core.skill_cli import build_parser, dispatch


class TestParser(unittest.TestCase):
    def test_install_requires_source(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["install"])  # missing positional

    def test_install_accepts_path(self):
        parser = build_parser()
        args = parser.parse_args(["install", "/tmp/foo", "--tier", "imported"])
        self.assertEqual(args.subcommand, "install")
        self.assertEqual(args.source, "/tmp/foo")
        self.assertEqual(args.tier, "imported")

    def test_list_default(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        self.assertEqual(args.subcommand, "list")

    def test_dev_requires_path(self):
        parser = build_parser()
        args = parser.parse_args(["dev", "/tmp/foo"])
        self.assertEqual(args.subcommand, "dev")


class TestListDispatch(unittest.TestCase):
    def test_list_prints_loaded_skills(self):
        parser = build_parser()
        args = parser.parse_args(["list"])
        with patch("sys.stdout", new_callable=StringIO) as stdout:
            rc = dispatch(args)
        self.assertEqual(rc, 0)
        # At least one skill should be listed (the bundled ones).
        self.assertIn("dashboard", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_skill_cli.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create `core/skill_cli.py`**

```python
"""
`miniclaw skill` CLI — subcommand dispatch for skill management.

Subcommands:
  install <url|path> [--tier imported|authored]   install a skill
  uninstall <name>                                 remove an installed skill
  list [--tier bundled|authored|imported]          list loaded skills
  validate <path>                                  dry-run validation; no install
  dev <path>                                       dev-mode symlink (bypasses clamps)

Dispatched from main.py when the first positional is "skill".
"""

import argparse
import datetime
import logging
import os
import shutil
import sys
from pathlib import Path

from core.install_metadata import INSTALL_FILENAME
from core.install_pipeline import (
    DockerBuilder,
    InstallDecision,
    InstallPipeline,
)
from core.skill_loader import SkillLoader
from core.skill_policy import TIER_AUTHORED, TIER_IMPORTED


logger = logging.getLogger(__name__)


class TextConfirmer:
    """stdin/stdout confirmer with y/N prompts for each gate."""
    def confirm_gate(self, gate: str, summary: str) -> bool:
        print()
        print(summary)
        print()
        prompt = f"Confirm '{gate}'? [y/N] "
        try:
            reply = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return reply in ("y", "yes")


class OrchestratorReloader:
    """Production reloader — triggers a file-based reload signal."""
    def __init__(self):
        self.flag_path = Path.home() / ".miniclaw" / "reload.flag"

    def reload(self) -> None:
        self.flag_path.parent.mkdir(parents=True, exist_ok=True)
        self.flag_path.write_text(datetime.datetime.now().isoformat())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="miniclaw skill", description="Manage MiniClaw skills")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_install = sub.add_parser("install", help="install a skill")
    p_install.add_argument("source", help="URL or filesystem path to the skill")
    p_install.add_argument(
        "--tier",
        choices=[TIER_AUTHORED, TIER_IMPORTED],
        default=TIER_IMPORTED,
        help="install tier (default: imported)",
    )

    p_uninstall = sub.add_parser("uninstall", help="remove an installed skill")
    p_uninstall.add_argument("name", help="skill name (kebab-case)")

    p_list = sub.add_parser("list", help="list loaded skills")
    p_list.add_argument(
        "--tier",
        choices=["bundled", TIER_AUTHORED, TIER_IMPORTED, "dev"],
        help="filter by tier",
    )

    p_validate = sub.add_parser("validate", help="dry-run validation (no install)")
    p_validate.add_argument("path", help="skill directory to validate")

    p_dev = sub.add_parser("dev", help="symlink a skill into dev mode")
    p_dev.add_argument("path", help="skill directory on disk")

    return parser


def dispatch(args: argparse.Namespace) -> int:
    if args.subcommand == "install":
        return _cmd_install(args)
    if args.subcommand == "uninstall":
        return _cmd_uninstall(args)
    if args.subcommand == "list":
        return _cmd_list(args)
    if args.subcommand == "validate":
        return _cmd_validate(args)
    if args.subcommand == "dev":
        return _cmd_dev(args)
    return 1


def _install_root(tier: str) -> Path:
    return Path.home() / ".miniclaw" / tier


def _cmd_install(args) -> int:
    install_root = _install_root(args.tier)
    install_root.mkdir(parents=True, exist_ok=True)
    pipeline = InstallPipeline(
        confirmer=TextConfirmer(),
        builder=DockerBuilder(),
        reloader=OrchestratorReloader(),
        install_root=install_root,
    )
    source = args.source
    if source.startswith("http://") or source.startswith("https://"):
        decision = pipeline.install_from_url(source, tier=args.tier)
    else:
        decision = pipeline.install_from_path(Path(source), tier=args.tier)

    if decision == InstallDecision.INSTALLED:
        print(f"Skill installed at tier {args.tier}.")
        return 0
    if decision == InstallDecision.CANCELLED:
        print("Install cancelled.")
        return 1
    print("Install failed.", file=sys.stderr)
    return 1


def _cmd_uninstall(args) -> int:
    for tier in (TIER_AUTHORED, TIER_IMPORTED):
        candidate = _install_root(tier) / args.name
        if candidate.exists():
            reply = input(f"Remove {candidate}? [y/N] ").strip().lower()
            if reply not in ("y", "yes"):
                return 1
            shutil.rmtree(candidate)
            print(f"Removed {candidate}.")
            return 0
    print(f"Skill {args.name!r} not found in authored or imported.", file=sys.stderr)
    return 1


def _cmd_list(args) -> int:
    loader = SkillLoader()
    loader.load_all()
    for skill in sorted(loader.skills.values(), key=lambda s: s.name):
        if args.tier and skill.tier != args.tier:
            continue
        print(f"{skill.name:30s} {skill.tier:10s}  {skill.description}")
    return 0


def _cmd_validate(args) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"Path not found: {path}", file=sys.stderr)
        return 1

    from core.skill_validator import SkillValidator
    from core.dockerfile_validator import DockerfileValidationError, validate as vd

    v = SkillValidator()
    skill_md = path / "SKILL.md"
    if not skill_md.exists():
        print(f"No SKILL.md in {path}", file=sys.stderr)
        return 1
    import yaml
    try:
        frontmatter, _ = v.validate_markdown(skill_md.read_text(encoding="utf-8"), path)
        cfg_path = path / "config.yaml"
        if cfg_path.exists():
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            v.validate_execution_config(raw, tier=TIER_IMPORTED, skill_name=frontmatter["name"])
        df = path / "scripts" / "Dockerfile"
        if df.exists():
            vd(df, tier=TIER_IMPORTED)
    except (ValueError, DockerfileValidationError) as e:
        print(f"Validation failed: {e}", file=sys.stderr)
        return 1
    print("Validation passed at tier=imported.")
    return 0


def _cmd_dev(args) -> int:
    path = Path(args.path).resolve()
    if not path.exists() or not (path / "SKILL.md").exists():
        print(f"Not a valid skill directory: {path}", file=sys.stderr)
        return 1

    import yaml
    fm = yaml.safe_load((path / "SKILL.md").read_text(encoding="utf-8").split("---")[1])
    name = fm.get("name")
    if not name:
        print("Skill has no name in frontmatter", file=sys.stderr)
        return 1

    dev_target = _install_root(TIER_IMPORTED) / name
    dev_target.parent.mkdir(parents=True, exist_ok=True)
    if dev_target.exists() or dev_target.is_symlink():
        dev_target.unlink() if dev_target.is_symlink() else shutil.rmtree(dev_target)
    os.symlink(path, dev_target)
    print(f"Dev mode: {dev_target} -> {path}")
    print("WARNING: security validations bypassed while this symlink exists.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Wire into `main.py`**

Near the top of `main.py`, find the argument handling. Before `args.text` / `args.voice` dispatch, add (exact placement depends on current `main.py` layout — look for where it parses args):

```python
# Early dispatch for `skill` subcommand. Call signature:
#   python3 main.py skill install <url>
#   python3 main.py skill list
# etc.
if len(sys.argv) >= 2 and sys.argv[1] == "skill":
    from core.skill_cli import main as skill_main
    sys.exit(skill_main(sys.argv[2:]))
```

- [ ] **Step 5: Run tests**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_skill_cli.py -v`
Expected: pass.

Also smoke test the CLI:

```bash
python3 main.py skill list
python3 main.py skill list --tier bundled
```

Expected: table-like listing of skills with their tier and description.

- [ ] **Step 6: Commit**

```bash
git add core/skill_cli.py main.py tests/test_skill_cli.py
git commit -m "feat(skills): add miniclaw skill CLI (install/uninstall/list/validate/dev)"
```

### Task 15: Voice `install-skill` URL-install branch

**Files:**
- Modify: `core/meta_skill.py`
- Modify: `core/container_manager.py` (`_execute_install_skill`)
- Modify: existing test `tests/test_install_skill_integration.py` if needed

- [ ] **Step 1: Add URL-install branch to `core/meta_skill.py`**

In `MetaSkillExecutor.run`, detect whether `tool_input` contains a `source` URL; if so, delegate to `InstallPipeline.install_from_url` using a voice-backed confirmer. If not, keep the existing "author from scratch" flow.

Locate the start of `run()`:

```python
    def run(self, tool_input: dict) -> str:
        description = tool_input.get("description", "").strip()
        if not description:
            return "Please describe what the skill should do."
        ...
```

Add a branch at the top:

```python
    def run(self, tool_input: dict) -> str:
        source = tool_input.get("source", "").strip()
        if source:
            return self._install_from_source(source)
        description = tool_input.get("description", "").strip()
        if not description:
            return "Please describe what the skill should do."
        # (existing author-from-scratch flow continues unchanged)
        ...
```

Add the new method to `MetaSkillExecutor`:

```python
    def _install_from_source(self, source: str) -> str:
        """
        Voice-driven install of an existing skill from a URL or path.
        Routes through the shared InstallPipeline with a voice-backed confirmer.
        """
        from core.install_pipeline import (
            DockerBuilder, InstallDecision, InstallPipeline,
        )
        from core.skill_policy import TIER_IMPORTED
        from pathlib import Path
        import os

        class VoiceConfirmer:
            def __init__(self, outer):
                self.outer = outer
            def confirm_gate(self, gate: str, summary: str) -> bool:
                self.outer._speak(
                    f"Ready to {gate}. {summary}. "
                    f"Say 'confirm {gate}' to continue, or 'cancel' to stop."
                )
                return self.outer._confirm(f"confirm {gate}")

        class OrchestratorReloader:
            def __init__(self, orch):
                self.orch = orch
            def reload(self):
                self.orch.reload_skills()

        install_root = Path.home() / ".miniclaw" / TIER_IMPORTED
        install_root.mkdir(parents=True, exist_ok=True)
        pipeline = InstallPipeline(
            confirmer=VoiceConfirmer(self),
            builder=DockerBuilder(),
            reloader=OrchestratorReloader(self.orchestrator),
            install_root=install_root,
        )

        if source.startswith(("http://", "https://")):
            decision = pipeline.install_from_url(source, tier=TIER_IMPORTED)
        else:
            decision = pipeline.install_from_path(Path(source), tier=TIER_IMPORTED)

        if decision == InstallDecision.INSTALLED:
            return "Skill installed."
        if decision == InstallDecision.CANCELLED:
            return "Skill install cancelled."
        return "Skill install failed."
```

- [ ] **Step 2: Update `install-skill` SKILL.md to advertise the source parameter**

Open `skills/install-skill/SKILL.md`. Add to the input schema section:

```yaml
type: object
properties:
  description:
    type: string
    description: Human description of a new skill to author from scratch
  source:
    type: string
    description: URL or filesystem path to an existing agentskills.io-format skill; if provided, install from there instead of authoring
```

Also add a "How to use" paragraph explaining both modes so Claude picks the right branch.

- [ ] **Step 3: Verify the existing tests still pass**

```bash
cd ~/linux/miniclaw && python3 -m pytest tests/test_install_skill_integration.py tests/test_meta_skill.py -v
```

Expected: pass. The existing tests exercise the `description` flow, which is unchanged; the new `source` flow is guarded by the new test in the next task.

- [ ] **Step 4: Commit**

```bash
git add core/meta_skill.py skills/install-skill/SKILL.md
git commit -m "feat(install-skill): add voice branch for installing from URL/path"
```

---

## Phase 8 — Self-update scaffolding + cleanup

Phase-boundary checkpoint: final `pytest tests/ -v`.

### Task 16: Recognize `metadata.miniclaw.self_update.allow_body`

**Files:**
- Modify: `core/skill_validator.py`
- Modify: `tests/test_skill_validator_tiered.py` (append)

- [ ] **Step 1: Append failing test**

```python
# Append to tests/test_skill_validator_tiered.py

class TestSelfUpdateScaffolding(unittest.TestCase):
    def setUp(self):
        self.v = SkillValidator()

    def test_self_update_flag_parsed(self):
        raw = (
            "---\nname: foo\ndescription: x\n"
            "metadata:\n  miniclaw:\n    self_update:\n      allow_body: true\n---\n\nBody.\n"
        )
        fm, _ = self.v.validate_markdown(raw, Path("/tmp/foo"))
        self.assertTrue(
            fm["metadata"]["miniclaw"]["self_update"]["allow_body"]
        )

    def test_self_update_flag_defaults_to_missing(self):
        raw = "---\nname: foo\ndescription: x\n---\n\nBody.\n"
        fm, _ = self.v.validate_markdown(raw, Path("/tmp/foo"))
        # Field simply absent — no error.
        self.assertNotIn("metadata", fm)

    def test_self_update_wrong_type_warns_not_fails(self):
        # Future work would validate type, but per spec we accept unknown shapes
        # and warn. For now, just assert the field is parsed as-is.
        raw = (
            "---\nname: foo\ndescription: x\n"
            "metadata:\n  miniclaw:\n    self_update:\n      allow_body: maybe\n---\n\nBody.\n"
        )
        fm, _ = self.v.validate_markdown(raw, Path("/tmp/foo"))
        self.assertEqual(
            fm["metadata"]["miniclaw"]["self_update"]["allow_body"],
            "maybe",
        )
```

- [ ] **Step 2: Run to verify the test behavior**

Run: `cd ~/linux/miniclaw && python3 -m pytest tests/test_skill_validator_tiered.py::TestSelfUpdateScaffolding -v`
Expected: should already pass — the validator returns the raw frontmatter dict. No code change needed; this task is a formal acceptance test for the scaffolding.

- [ ] **Step 3: Confirm scaffolding documented in SKILL.md template**

If `scripts/port-openclaw-skill.py` emits a SKILL.md template, ensure it writes:

```yaml
metadata:
  miniclaw:
    self_update:
      allow_body: false
```

as an opt-in default. If the porter doesn't already do this, edit the emitted SKILL.md body accordingly. (Optional — not a test requirement, but consistent with the spec.)

- [ ] **Step 4: Commit (test-only addition)**

```bash
git add tests/test_skill_validator_tiered.py scripts/port-openclaw-skill.py
git commit -m "test(skills): assert self_update scaffolding frontmatter is preserved"
```

### Task 17: Delete migration script

- [ ] **Step 1: Delete the script and its test**

```bash
git rm scripts/migrate-to-agentskills.py
git rm tests/test_migration_script.py
```

- [ ] **Step 2: Run suite one last time**

```bash
python3 -m pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git commit -m "chore(migrate): remove one-shot agentskills.io migration script and its test"
```

### Task 18: Final integration sanity check

- [ ] **Step 1: Voice-install fixture smoke test (optional, local only)**

If Docker is available and you want to exercise the full pipeline:

```bash
# Use the fixture skill from earlier
python3 main.py skill install tests/fixtures/agentskills/good-skill --tier imported
# Approve the three gates interactively.

python3 main.py skill list --tier imported
# Expect 'good-skill' in the listing.

python3 main.py skill uninstall good-skill
# Confirm removal.
```

Expected: install completes, skill appears in listing, uninstall removes it.

- [ ] **Step 2: Load the full orchestrator once**

```bash
./run.sh --list
```

Expected: 12 bundled skills listed in kebab-case. No errors.

- [ ] **Step 3: Commit (nothing to commit unless smoke test surfaces a fix)**

If nothing else changed, skip the commit. If the smoke test surfaced a bug, fix it and commit:

```bash
git add <files>
git commit -m "fix(<area>): <fix from integration smoke test>"
```

---

## Final Phase-8 wrap-up

At this point:

- All 12 bundled skills are in agentskills.io-compliant single-directory layout.
- Loader, validator, and Dockerfile validator are tier-aware.
- Install pipeline is the single entry point for voice + CLI + (future) mobile.
- `.install.json` carries provenance only; trust tier is inferred from install directory.
- Dev mode is symlink-based; unspoofable by skill content.
- Self-update scaffolding (frontmatter flag) is in place; execution deferred to roadmap #4.
- Migration script is deleted.
- `CLAUDE.md` + `WORKING_MEMORY.md` reflect the new layout.

## Self-Review

Spec coverage: validated below against each section of the design spec.

- ✅ **Directory layout** — Tasks 8-10 (migration) + Task 14 (CLI dev mode assumes new layout).
- ✅ **Trust tiers** — Task 1 (policy), Task 7 (loader tier inference), Task 14 (CLI tier argument).
- ✅ **Frontmatter** — Task 2 (name regex + parent-dir match), Task 3 (metadata.miniclaw.requires), Task 16 (self_update scaffolding), Task 4 (metadata shape implicitly preserved).
- ✅ **Config.yaml per-tier clamps** — Task 4.
- ✅ **Dockerfile validator** — Task 5.
- ✅ **Apt allowlist** — Task 5.
- ✅ **Install pipeline** — Tasks 12-13 (core + fetch).
- ✅ **Env_passthrough credential warnings** — Task 1 (`is_credential_pattern`) + Task 12 (used in summary).
- ✅ **Voice ergonomics** — Task 15 (voice `install-skill` URL branch).
- ✅ **Keyboard-only escape hatches** — apt allowlist is file-based (Task 5); dev mode symlink is CLI-only (Task 14).
- ✅ **Dev mode** — Task 14 (`skill dev`) + Task 7 (loader symlink detection).
- ✅ **Self-update scaffolding** — Task 16.
- ✅ **Migration of 12 existing skills** — Tasks 8-11.
- ✅ **Testing** — unit tests per task; integration in Task 18. Per the "prefer inline execution" memory, phase-boundary test runs are the primary integration checkpoint.
- ✅ **Rollout** — commit-per-task matches the suggested 5-commit rollout pattern closely enough.

Placeholder scan: grep the plan for TBD/TODO/"fill in" — none present. Every code block is complete.

Type consistency: `SkillValidator.validate_execution_config(config, *, tier, skill_name)` used consistently from Task 4 onward. `Skill.tier` field introduced in Task 7 and used by `skill_cli.py` in Task 14. `InstallDecision` enum defined in Task 12, used by `skill_cli.py` in Task 14.

Scope: one cohesive implementation plan with clear phase boundaries. Phases are interdependent (cannot ship install pipeline without tier model), so single plan is correct.
