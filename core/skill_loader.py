"""
Skill Loader - Parses SKILL.md files in both native and OpenClaw-compatible formats.

Scans skill directories, checks eligibility based on requirements (env vars, 
binaries, OS), and builds Claude tool definitions for the orchestrator.

Supports three tiers:
  1. Native skills   - SKILL.md + config.yaml (Docker-routed execution)
  2. OpenClaw skills  - SKILL.md with OpenClaw metadata (compatibility layer)
  3. Community skills - Either format, user-contributed
"""

import os
import re
import yaml
import shutil
import platform
import logging
from pathlib import Path
from typing import Optional

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
        source_format: str,
        skill_dir: str,
        metadata: Optional[dict] = None,
    ):
        self.name = name
        self.description = description
        self.instructions = instructions
        self.tool_definition = tool_definition
        self.execution_config = execution_config
        self.source_format = source_format  # "native" or "openclaw"
        self.skill_dir = skill_dir
        self.metadata = metadata or {}

    def __repr__(self):
        return f"Skill(name={self.name!r}, format={self.source_format!r})"


class SkillLoader:
    """
    Loads skills from one or more directories.

    Precedence (highest first):
      1. workspace skills  (./skills)
      2. user skills        (~/.miniclaw/skills)
      3. bundled skills     (installed with the package)

    A skill with the same name in a higher-precedence directory
    shadows the lower one, matching OpenClaw's override behaviour.
    """

    # Directories scanned in order of precedence (highest first)
    DEFAULT_SEARCH_PATHS = [
        Path("./skills"),
        Path.home() / ".miniclaw" / "skills",
        Path(__file__).resolve().parent.parent / "skills",  # bundled
    ]

    def __init__(self, search_paths: list[Path] | None = None):
        self.search_paths = search_paths or self.DEFAULT_SEARCH_PATHS
        self.skills: dict[str, Skill] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_all(self) -> dict[str, Skill]:
        """Scan all search paths and return eligible skills keyed by name."""

        # Walk paths in reverse so higher-precedence dirs overwrite
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
        raw = skill_md.read_text(encoding="utf-8")

        # Split YAML frontmatter from markdown body
        frontmatter, body = self._parse_frontmatter(raw)
        if frontmatter is None:
            logger.warning("No valid frontmatter in %s", skill_md)
            return None

        name = frontmatter.get("name", skill_dir.name)
        description = frontmatter.get("description", "")

        # Detect format: native skills have a config.yaml alongside SKILL.md
        config_path = skill_dir / "config.yaml"
        is_native = config_path.exists()
        source_format = "native" if is_native else "openclaw"

        # Check eligibility
        if not self._check_eligible(frontmatter, source_format):
            logger.info("Skill '%s' not eligible on this system, skipping", name)
            return None

        # Build execution config
        if is_native:
            execution_config = yaml.safe_load(
                config_path.read_text(encoding="utf-8")
            ) or {}
        else:
            # OpenClaw skill - wrap in container execution layer
            execution_config = self._build_openclaw_execution_config(
                frontmatter, skill_dir
            )

        # Build Claude tool definition
        tool_definition = self._build_tool_definition(name, description, body)

        return Skill(
            name=name,
            description=description,
            instructions=body,
            tool_definition=tool_definition,
            execution_config=execution_config,
            source_format=source_format,
            skill_dir=str(skill_dir),
            metadata=frontmatter.get("metadata", {}),
        )

    def _parse_frontmatter(self, raw: str) -> tuple[dict | None, str]:
        """
        Split a SKILL.md into (frontmatter_dict, markdown_body).
        Supports the standard --- delimited YAML block.
        """
        pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
        match = re.match(pattern, raw, re.DOTALL)
        if not match:
            # No frontmatter - treat entire file as body with empty metadata
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

    def _check_eligible(self, frontmatter: dict, source_format: str) -> bool:
        """
        Check whether a skill is eligible to run on this system.
        
        Supports both native config.yaml requirements and OpenClaw-style
        metadata.openclaw.requires blocks.
        """

        # OpenClaw-style requirements live under metadata.openclaw.requires
        if source_format == "openclaw":
            requires = (
                frontmatter.get("metadata", {})
                .get("openclaw", {})
                .get("requires", {})
            )
        else:
            requires = frontmatter.get("requires", {})

        if not requires:
            return True

        # Check required environment variables
        required_env = requires.get("env", [])
        for var in required_env:
            if not os.environ.get(var):
                logger.debug("Skill missing env var: %s", var)
                return False

        # Check required binaries on PATH
        required_bins = requires.get("bins", [])
        for binary in required_bins:
            if not shutil.which(binary):
                logger.debug("Skill missing binary: %s", binary)
                return False

        # Check OS constraint
        required_os = requires.get("os", [])
        if required_os:
            current_os = platform.system().lower()
            os_map = {"darwin": "darwin", "linux": "linux", "windows": "win32"}
            mapped = os_map.get(current_os, current_os)
            if mapped not in required_os:
                logger.debug("Skill not supported on OS: %s", current_os)
                return False

        return True

    # ------------------------------------------------------------------
    # Tool definition building
    # ------------------------------------------------------------------

    def _build_tool_definition(
        self, name: str, description: str, body: str
    ) -> dict:
        """
        Build a Claude-compatible tool definition from skill metadata.

        Scans the markdown body for an ## Inputs or ## Parameters section
        to extract input schema. Falls back to a generic single-input schema.
        """

        # Look for explicitly defined parameters in the body
        input_schema = self._extract_input_schema(body)

        return {
            "name": name,
            "description": description,
            "input_schema": input_schema,
        }

    def _extract_input_schema(self, body: str) -> dict:
        """
        Try to extract structured input parameters from the markdown body.
        
        Looks for a ```yaml or ```json block under ## Inputs / ## Parameters.
        Falls back to a generic {query: string} schema if nothing is found.
        """

        # Look for a parameters/inputs section with a code block
        params_pattern = r"##\s*(?:Inputs|Parameters|Input Schema)\s*\n```(?:yaml|json)\s*\n(.*?)```"
        match = re.search(params_pattern, body, re.DOTALL | re.IGNORECASE)

        if match:
            try:
                schema = yaml.safe_load(match.group(1))
                if isinstance(schema, dict) and "type" in schema:
                    return schema
            except yaml.YAMLError:
                pass

        # Default: single query/input parameter
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

    # ------------------------------------------------------------------
    # OpenClaw compatibility layer
    # ------------------------------------------------------------------

    def _build_openclaw_execution_config(
        self, frontmatter: dict, skill_dir: Path
    ) -> dict:
        """
        Translate an OpenClaw skill into an execution config that our
        container-based orchestrator understands.

        OpenClaw skills expect direct host execution. We wrap them in a
        sandboxed container with a generic executor image.
        """

        requires = (
            frontmatter.get("metadata", {})
            .get("openclaw", {})
            .get("requires", {})
        )

        # Determine which env vars to pass into the container
        env_vars = requires.get("env", []) if requires else []

        # Check for a primary API key
        primary_env = (
            frontmatter.get("metadata", {})
            .get("openclaw", {})
            .get("primaryEnv", "")
        )

        return {
            "type": "openclaw_compat",
            "image": "miniclaw/skill-executor:latest",
            "env_passthrough": env_vars,
            "primary_env": primary_env,
            "required_bins": requires.get("bins", []) if requires else [],
            "skill_dir": str(skill_dir.resolve()),
            "timeout_seconds": 30,
        }
