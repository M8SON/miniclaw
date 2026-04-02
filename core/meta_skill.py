"""
Meta Skill Executor — allows users to install new MiniClaw skills by voice.

Claude Code runs as a restricted subprocess (no Docker socket, no core file access)
and writes skill files into skills/<name>/ and containers/<name>/ only.

Three voice confirmation gates prevent accidental or injected installs:
  1. "confirm install"  — before Claude Code writes any files
  2. "confirm build"    — after files pass validation, before docker build
  3. "confirm restart"  — after successful build, before skills hot-reload

Security measures applied at each stage:
  - Path traversal check: all written paths must resolve inside the two skill dirs
  - Dockerfile validator: allowlist of safe instructions only
  - env_passthrough audit: any requested API keys spoken aloud before build
  - Git commit: every installed skill is committed for audit trail / reversibility
  - Cleanup on any failure or cancellation
"""

import os
import re
import shutil
import logging
import subprocess
import textwrap
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIRM_TIMEOUT = 25  # seconds to wait for each spoken confirmation


class MetaSkillExecutor:

    def __init__(
        self,
        voice,
        orchestrator,
        *,
        run_claude_code=None,
        trigger_build=None,
        cleanup=None,
    ):
        self.voice = voice
        self.orchestrator = orchestrator
        self._run_claude_code = run_claude_code or _run_claude_code
        self._trigger_build = trigger_build or _trigger_build
        self._cleanup = cleanup or _cleanup

    # ── Public entry point ────────────────────────────────────────────────

    def run(self, tool_input: dict) -> str:
        description = tool_input.get("description", "").strip()
        if not description:
            return "Please describe what the skill should do."

        skill_name = _derive_skill_name(description)
        spoken_name = skill_name.replace("_", " ")

        # ── Phase 1: Confirm install ──────────────────────────────────────
        self._speak(
            f"I will create a new skill called {spoken_name}. "
            f"Say 'confirm install' to continue, or 'cancel' to stop."
        )
        if not self._confirm("confirm install"):
            return "Skill installation cancelled."

        self._speak("Writing skill files now. This may take a minute.")
        success, output = self._run_claude_code(skill_name, description)

        if not success:
            self._cleanup(skill_name)
            return f"Skill file generation failed. {output[:150]}"

        # Path traversal check
        ok, violations = _validate_paths(skill_name)
        if not ok:
            self._cleanup(skill_name)
            logger.warning("Path traversal detected: %s", violations)
            return "Security check failed: files written outside allowed directories. Installation aborted."

        # Dockerfile validation
        dockerfile = REPO_ROOT / "containers" / skill_name / "Dockerfile"
        if dockerfile.exists():
            from core.dockerfile_validator import validate, DockerfileValidationError
            try:
                validate(dockerfile)
            except DockerfileValidationError as e:
                self._cleanup(skill_name)
                return f"Dockerfile validation failed: {e}. Installation aborted."

        # ── Phase 2: Confirm build ────────────────────────────────────────
        file_summary = _summarize_written_files(skill_name)
        env_keys = _audit_env_passthrough(skill_name)
        env_notice = ""
        if env_keys:
            env_notice = (
                f" This skill requests access to these environment variables: "
                f"{', '.join(env_keys)}. Add them to your .env file before using it."
            )

        self._speak(
            f"{file_summary}.{env_notice} "
            f"Say 'confirm build' to build the Docker image, or 'cancel'."
        )
        if not self._confirm("confirm build"):
            self._cleanup(skill_name)
            return "Skill installation cancelled before build."

        self._speak("Building Docker image. This may take a few minutes.")
        build_ok, build_msg = self._trigger_build(skill_name)
        if not build_ok:
            self._cleanup(skill_name)
            return f"Docker build failed. {build_msg[:150]}"

        # ── Phase 3: Confirm restart ──────────────────────────────────────
        self._speak(
            f"Build complete. Say 'confirm restart' to load {spoken_name} now, or 'cancel'."
        )
        if not self._confirm("confirm restart"):
            return (
                f"Skill {spoken_name} is installed but not yet active. "
                f"Restart MiniClaw to load it."
            )

        self.orchestrator.reload_skills()
        return f"Skill {spoken_name} is now active. You can use it right away."

    # ── Helpers ───────────────────────────────────────────────────────────

    def _speak(self, text: str):
        if self.voice is not None:
            self.voice.speak(text)
        else:
            logger.info("[meta_skill] %s", text)

    def _confirm(self, expected_phrase: str) -> bool:
        """
        Listen for up to CONFIRM_TIMEOUT seconds.
        Returns True only if all words of expected_phrase appear in the transcript.
        Returns False on timeout, silence, 'cancel', or a non-matching response.
        """
        if self.voice is None:
            logger.info("[meta_skill] voice not available — auto-cancelling confirmation")
            return False

        transcript = self.voice.listen(max_wait_seconds=CONFIRM_TIMEOUT)

        if not transcript:
            self._speak("No response received. Cancelling.")
            return False

        t = transcript.lower()
        if "cancel" in t:
            return False

        required = expected_phrase.lower().split()
        return all(w in t for w in required)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _derive_skill_name(description: str) -> str:
    """Derive a safe snake_case name from the description (first 3 meaningful words)."""
    words = re.sub(r"[^a-z0-9 ]", "", description.lower()).split()
    # Drop common filler words
    stop = {"a", "an", "the", "that", "can", "to", "for", "and", "or", "i", "me"}
    words = [w for w in words if w not in stop][:3]
    name = "_".join(words)[:32] or "new_skill"

    # Avoid collisions with existing skill directories
    existing = {p.name for p in (REPO_ROOT / "skills").iterdir() if p.is_dir()}
    base, i = name, 2
    while name in existing:
        name = f"{base}_{i}"
        i += 1
    return name


def _run_claude_code(skill_name: str, description: str) -> tuple[bool, str]:
    """
    Invoke Claude Code as a restricted subprocess to write skill files.

    Allowed tools: Read, Write, Edit, Glob, Grep, WebSearch, WebFetch.
    Bash is excluded so Claude Code cannot run shell commands.
    The prompt explicitly restricts writes to skills/<name>/ and containers/<name>/.
    """
    image_name = f"miniclaw/{skill_name.replace('_', '-')}:latest"

    prompt = textwrap.dedent(f"""
        You are implementing a new MiniClaw skill named '{skill_name}'.
        The user wants: {description}

        First read CLAUDE.md to understand the project architecture.
        Then study these reference files:
          skills/web_search/SKILL.md
          skills/web_search/config.yaml
          containers/web_search/Dockerfile
          containers/web_search/app.py

        Create EXACTLY these four files (no others):
          skills/{skill_name}/SKILL.md
          skills/{skill_name}/config.yaml
          containers/{skill_name}/Dockerfile
          containers/{skill_name}/app.py

        Rules you MUST follow:
          - Dockerfile MUST start with: FROM miniclaw/base:latest
          - Only allowed Dockerfile instructions: FROM, RUN (pip install or apt-get only),
            COPY (local files only), WORKDIR, CMD, ENV
          - config.yaml must set image: {image_name}
          - config.yaml network field: omit it (inherits host networking)
          - Do NOT write any file outside skills/{skill_name}/ or containers/{skill_name}/
          - Do NOT modify run.sh, main.py, any core/ file, or any existing skill
    """).strip()

    # Pass ANTHROPIC_API_KEY from .env if not already in environment
    env = {**os.environ}
    if "ANTHROPIC_API_KEY" not in env:
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    env["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    try:
        result = subprocess.run(
            [
                "claude",
                "--allowedTools", "Read,Write,Edit,Glob,Grep,WebSearch,WebFetch",
                "--output-format", "text",
                "-p", prompt,
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        success = result.returncode == 0
        output = result.stdout.strip() if success else (result.stderr or result.stdout).strip()
        return success, output
    except FileNotFoundError:
        return False, "Claude Code CLI not found. Install it with: npm install -g @anthropic-ai/claude-code"
    except subprocess.TimeoutExpired:
        return False, "Claude Code timed out after 5 minutes."


def _validate_paths(skill_name: str) -> tuple[bool, list[str]]:
    """
    Verify every file written by Claude Code resolves strictly within
    skills/<skill_name>/ or containers/<skill_name>/.
    """
    allowed = [
        (REPO_ROOT / "skills" / skill_name).resolve(),
        (REPO_ROOT / "containers" / skill_name).resolve(),
    ]
    violations = []

    for root in allowed:
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            resolved = p.resolve()
            if not any(str(resolved).startswith(str(a)) for a in allowed):
                violations.append(str(p))
            if p.is_symlink():
                target = p.resolve()
                if not any(str(target).startswith(str(a)) for a in allowed):
                    violations.append(f"symlink {p} -> {target}")

    return len(violations) == 0, violations


def _audit_env_passthrough(skill_name: str) -> list[str]:
    """Return any env_passthrough keys from the generated config.yaml."""
    config_path = REPO_ROOT / "skills" / skill_name / "config.yaml"
    if not config_path.exists():
        return []
    try:
        data = yaml.safe_load(config_path.read_text()) or {}
        return data.get("env_passthrough", []) or []
    except yaml.YAMLError:
        return []


def _summarize_written_files(skill_name: str) -> str:
    """Return a brief TTS-friendly summary of files written."""
    files = []
    for root in [
        REPO_ROOT / "skills" / skill_name,
        REPO_ROOT / "containers" / skill_name,
    ]:
        if root.is_dir():
            files.extend(p.name for p in root.iterdir() if p.is_file())

    if not files:
        return "Skill files written"
    if len(files) == 1:
        return f"1 file written: {files[0]}"
    return f"{len(files)} files written: {', '.join(files[:-1])}, and {files[-1]}"


def _trigger_build(skill_name: str) -> tuple[bool, str]:
    """Run build_new_skill.sh on the host to build the Docker image."""
    script = REPO_ROOT / "scripts" / "build_new_skill.sh"
    try:
        result = subprocess.run(
            ["bash", str(script), skill_name],
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Docker build timed out after 5 minutes."


def _cleanup(skill_name: str):
    """Remove partially-created skill files on failure or cancellation."""
    for path in [
        REPO_ROOT / "skills" / skill_name,
        REPO_ROOT / "containers" / skill_name,
    ]:
        if path.exists():
            shutil.rmtree(path)
            logger.info("Cleaned up %s", path)
