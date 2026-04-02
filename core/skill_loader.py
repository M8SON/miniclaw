"""
Skill Loader - Parses SKILL.md files and builds Claude tool definitions.

Every skill must have both SKILL.md and config.yaml in its directory.
Skills missing config.yaml are skipped with a warning.

Scans skill directories, checks eligibility based on requirements
(env vars, binaries, OS), and builds Claude tool definitions for
the orchestrator.
"""

import logging
from pathlib import Path

import yaml

from core.skill_eligibility import SkillEligibility
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
    ):
        self.name = name
        self.description = description
        self.instructions = instructions
        self.tool_definition = tool_definition
        self.execution_config = execution_config
        self.skill_dir = skill_dir

    def __repr__(self):
        return f"Skill(name={self.name!r})"


class SkillLoader:
    """
    Loads skills from one or more directories.

    Every skill directory must contain:
      - SKILL.md  : Claude routing instructions
      - config.yaml: Container execution config

    Precedence (highest first):
      1. workspace skills  (./skills)
      2. user skills        (~/.miniclaw/skills)
      3. bundled skills     (installed with the package)

    A skill with the same name in a higher-precedence directory
    shadows the lower one.
    """

    DEFAULT_SEARCH_PATHS = [
        Path("./skills"),
        Path.home() / ".miniclaw" / "skills",
        Path(__file__).resolve().parent.parent / "skills",  # bundled
    ]

    def __init__(self, search_paths: list[Path] | None = None):
        self.search_paths = search_paths or self.DEFAULT_SEARCH_PATHS
        self.skills: dict[str, Skill] = {}
        self.skipped_skills: dict[str, dict] = {}  # name -> {description, reason}
        self.invalid_skills: dict[str, dict] = {}  # name -> {description, reason}
        self.validator = SkillValidator()
        self.eligibility = SkillEligibility()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_all(self) -> dict[str, Skill]:
        """Scan all search paths and return eligible skills keyed by name."""

        self.skipped_skills = {}
        self.invalid_skills = {}

        for search_path in reversed(self.search_paths):
            if not search_path.is_dir():
                continue

            for entry in sorted(search_path.iterdir()):
                skill_md = entry / "SKILL.md"
                if entry.is_dir() and skill_md.exists():
                    skill = self._load_skill(entry)
                    if skill:
                        self.skills[skill.name] = skill

        logger.info(
            "Loaded %d eligible skill(s): %s",
            len(self.skills),
            ", ".join(self.skills.keys()),
        )
        return self.skills

    def get_tool_definitions(self) -> list[dict]:
        """Return Claude-compatible tool definitions for all loaded skills."""
        return [s.tool_definition for s in self.skills.values()]

    def get_skill(self, tool_name: str) -> Skill | None:
        """Look up a skill by its tool name."""
        return self.skills.get(tool_name)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _load_skill(self, skill_dir: Path) -> Skill | None:
        """Parse a single skill directory and return a Skill or None."""

        skill_md = skill_dir / "SKILL.md"
        config_path = skill_dir / "config.yaml"

        raw = skill_md.read_text(encoding="utf-8")
        try:
            frontmatter, body = self.validator.validate_markdown(raw, skill_dir)
        except ValueError as e:
            logger.warning("Invalid skill markdown in %s: %s", skill_md, e)
            self._record_invalid_skill(skill_dir.name, "", str(e))
            return None

        name = frontmatter.get("name", skill_dir.name)
        description = frontmatter.get("description", "")

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
            execution_config = self.validator.validate_execution_config(raw_config)
        except (OSError, yaml.YAMLError, ValueError) as e:
            logger.warning("Invalid config for skill '%s': %s", name, e)
            self._record_invalid_skill(name, description, str(e))
            return None

        tool_definition = self.validator.build_tool_definition(name, description, body)

        return Skill(
            name=name,
            description=description,
            instructions=body,
            tool_definition=tool_definition,
            execution_config=execution_config,
            skill_dir=str(skill_dir),
        )

    def get_missing_env_vars(self) -> set[str]:
        """Return all env var names required by currently skipped skills."""
        result = set()
        for info in self.skipped_skills.values():
            result.update(info.get("missing_env_vars", []))
        return result

    def _record_invalid_skill(self, name: str, description: str, reason: str) -> None:
        """Track a skill that is installed but structurally invalid."""
        self.invalid_skills[name] = {
            "description": description,
            "reason": reason,
        }
