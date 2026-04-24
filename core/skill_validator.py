"""
Skill validation helpers for MiniClaw.

Agentskills.io-compliant: kebab-case names matching parent directory,
description capped at 1024 chars, unknown frontmatter fields permitted.

Keeps skill parsing and tool-definition extraction separate from directory
scanning and eligibility checks.
"""

import os
import re
from pathlib import Path

import yaml

from core.skill_policy import (
    TIER_BUNDLED,
    TIER_AUTHORED,
    TIER_IMPORTED,
    TIER_DEV,
    policy_for,
    DEVICE_ALLOWLIST_PATTERNS,
    is_scoped_volume,
)


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
            home = os.path.expanduser("~")
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
            return max(1, number // 1024)
        raise ValueError(f"unknown memory unit in {memory_str!r}")

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
