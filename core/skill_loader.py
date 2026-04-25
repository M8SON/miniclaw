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

from core.install_metadata import read_metadata, compute_skill_sha256
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
        frontmatter: dict | None = None,
    ):
        self.name = name
        self.description = description
        self.instructions = instructions
        self.tool_definition = tool_definition
        self.execution_config = execution_config
        self.skill_dir = skill_dir
        self.tier = tier
        self.frontmatter = frontmatter or {}

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

    # Map search-path-index to tier. Index 0 = highest precedence.
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

        # Validate Dockerfile for non-bundled / non-dev tiers
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

        # Drift detection: if .install.json exists, the current SHA must match.
        if tier in (TIER_AUTHORED, TIER_IMPORTED):
            meta = read_metadata(skill_dir)
            if meta is not None and meta.sha256:
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
            frontmatter=frontmatter,
        )

    def _record_invalid_skill(self, name: str, description: str, reason: str) -> None:
        self.invalid_skills[name] = {
            "description": description,
            "reason": reason,
        }
