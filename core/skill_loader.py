"""
Skill Loader - Parses SKILL.md files and builds Claude tool definitions.

Every skill must have both SKILL.md and config.yaml in its directory.
Skills missing config.yaml are skipped with a warning.

Scans skill directories, checks eligibility based on requirements
(env vars, binaries, OS), and builds Claude tool definitions for
the orchestrator.
"""

import os
import re
import yaml
import shutil
import platform
import logging
from pathlib import Path

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_all(self) -> dict[str, Skill]:
        """Scan all search paths and return eligible skills keyed by name."""

        self.skipped_skills = {}

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
        frontmatter, body = self._parse_frontmatter(raw)
        if frontmatter is None:
            logger.warning("No valid frontmatter in %s, skipping", skill_md)
            return None

        name = frontmatter.get("name", skill_dir.name)
        description = frontmatter.get("description", "")

        if not config_path.exists():
            logger.warning(
                "Skill '%s' has no config.yaml — all skills require a container config, skipping",
                name,
            )
            return None

        skip_reason, missing_env_vars = self._check_eligible(frontmatter)
        if skip_reason:
            logger.info("Skill '%s' not eligible: %s", name, skip_reason)
            self.skipped_skills[name] = {
                "description": description,
                "reason": skip_reason,
                "missing_env_vars": missing_env_vars,
            }
            return None

        execution_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        tool_definition = self._build_tool_definition(name, description, body)

        return Skill(
            name=name,
            description=description,
            instructions=body,
            tool_definition=tool_definition,
            execution_config=execution_config,
            skill_dir=str(skill_dir),
        )

    def _parse_frontmatter(self, raw: str) -> tuple[dict | None, str]:
        """Split a SKILL.md into (frontmatter_dict, markdown_body)."""
        pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
        match = re.match(pattern, raw, re.DOTALL)
        if not match:
            return {}, raw

        try:
            fm = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as e:
            logger.warning("Failed to parse YAML frontmatter: %s", e)
            return None, raw

        return fm, match.group(2)

    # ------------------------------------------------------------------
    # Eligibility checking
    # ------------------------------------------------------------------

    def _check_eligible(self, frontmatter: dict) -> tuple[str | None, list[str]]:
        """
        Check whether a skill is eligible to run on this system.

        Returns (reason, missing_env_vars) where reason is None if eligible,
        or a human-readable string if not. missing_env_vars is the list of
        env var names that are unset (used to restrict set_env_var).

        Reads the top-level requires block:
          requires:
            env:     [LIST]     # all must be set
            bins:    [LIST]     # all must exist on PATH
            anyBins: [LIST]     # at least one must exist on PATH
            os:      [LIST]     # linux | darwin | win32
        """
        requires = frontmatter.get("requires", {})
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
        if any_bins and not any(shutil.which(b) for b in any_bins):
            missing.append(f"one of these binaries: {', '.join(any_bins)}")

        required_os = requires.get("os", [])
        if required_os:
            current_os = platform.system().lower()
            os_map = {"darwin": "darwin", "linux": "linux", "windows": "win32"}
            if os_map.get(current_os, current_os) not in required_os:
                missing.append(f"OS must be one of: {', '.join(required_os)}")

        reason = ("missing " + ", ".join(missing)) if missing else None
        return reason, missing_env_vars

    def get_missing_env_vars(self) -> set[str]:
        """Return all env var names required by currently skipped skills."""
        result = set()
        for info in self.skipped_skills.values():
            result.update(info.get("missing_env_vars", []))
        return result

    # ------------------------------------------------------------------
    # Tool definition building
    # ------------------------------------------------------------------

    def _build_tool_definition(self, name: str, description: str, body: str) -> dict:
        """Build a Claude-compatible tool definition from skill metadata."""
        return {
            "name": name,
            "description": description,
            "input_schema": self._extract_input_schema(body),
        }

    def _extract_input_schema(self, body: str) -> dict:
        """
        Extract input schema from an ## Inputs or ## Parameters section.
        Falls back to a generic {query: string} schema if none found.
        """
        pattern = r"##\s*(?:Inputs|Parameters|Input Schema)\s*\n```(?:yaml|json)\s*\n(.*?)```"
        match = re.search(pattern, body, re.DOTALL | re.IGNORECASE)

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
