"""
Container Manager - Handles Docker container lifecycle for skill execution.

Spins up a sandboxed container on demand when a skill is invoked, passes
the request via SKILL_INPUT, collects stdout as the result, and tears the
container down. All skills use the same execution model.

Designed for constrained environments (Raspberry Pi) where containers
should not persist between calls.
"""

import os
import re
import json
import time
import subprocess
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parent.parent


class ContainerManager:
    """Manages Docker containers for skill execution."""

    DEFAULT_TIMEOUT = 30
    DEFAULT_MEMORY_LIMIT = "256m"

    def __init__(self, memory_limit: str = DEFAULT_MEMORY_LIMIT):
        self.memory_limit = memory_limit
        self._meta_skill_executor = None  # injected from main.py after construction
        self._orchestrator = None          # injected from main.py after construction
        self.docker_available = False
        self.docker_error = None
        self._native_handlers = {
            "install_skill": self._execute_install_skill,
            "set_env_var": self._execute_set_env_var,
            "save_memory": self._execute_save_memory,
        }
        self._verify_docker()

    def _verify_docker(self):
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace").lower()
                if "permission denied" in stderr:
                    self.docker_error = "Docker is installed but this session cannot access the daemon"
                else:
                    self.docker_error = "Docker daemon is not running"
                logger.warning(self.docker_error)
                return
            self.docker_available = True
            self.docker_error = None
            logger.info("Docker is available")
        except FileNotFoundError:
            self.docker_error = "Docker is not installed"
            logger.warning(self.docker_error)
        except subprocess.TimeoutExpired:
            self.docker_error = "Docker daemon did not respond in time"
            logger.warning(self.docker_error)

    def execute_skill(self, skill, tool_input: dict) -> str:
        """
        Execute a skill by spinning up its container, passing input,
        and returning stdout as the result.

        Input is passed as JSON via the SKILL_INPUT environment variable.
        Output is read from stdout as plain text or JSON.
        """
        config = skill.execution_config

        # Native skills bypass Docker entirely
        if config.get("type") == "native":
            return self._execute_native_skill(skill, tool_input)

        if not self.docker_available:
            return f"Skill unavailable: {self.docker_error or 'Docker is unavailable'}"

        image = config.get("image", "")
        timeout = config.get("timeout_seconds", self.DEFAULT_TIMEOUT)

        if not image:
            return f"Error: no container image defined for skill '{skill.name}'"

        cmd = self._build_docker_cmd(
            image=image,
            env_vars=self._collect_env_vars(config.get("env_passthrough", [])),
            devices=config.get("devices", []),
            input_data=json.dumps(tool_input),
            memory=config.get("memory", self.memory_limit),
            read_only=config.get("read_only", True),
            extra_tmpfs=config.get("extra_tmpfs", []),
        )

        return self._run_container(cmd, tool_input, timeout)

    def _build_docker_cmd(
        self,
        image: str,
        env_vars: dict[str, str] | None = None,
        devices: list[str] | None = None,
        input_data: str = "",
        memory: str | None = None,
        read_only: bool = True,
        extra_tmpfs: list[str] | None = None,
    ) -> list[str]:
        """Build a docker run command with security constraints.

        read_only and extra_tmpfs can be overridden per skill via config.yaml
        for skills that need a writable filesystem (e.g. browser automation).
        """
        cmd = [
            "docker", "run",
            "--rm",
            "-i",
            "--network=host",
            f"--memory={memory or self.memory_limit}",
            "--cpus=1.0",
            "--security-opt=no-new-privileges",
        ]

        if read_only:
            cmd.append("--read-only")

        cmd.extend(["--tmpfs=/tmp:size=64m"])
        for tmpfs in (extra_tmpfs or []):
            cmd.extend(["--tmpfs", tmpfs])

        if env_vars:
            for key, value in env_vars.items():
                cmd.extend(["-e", f"{key}={value}"])

        if input_data:
            cmd.extend(["-e", f"SKILL_INPUT={input_data}"])

        if devices:
            for device in devices:
                cmd.extend(["--device", device])

        cmd.append(image)
        return cmd

    def _run_container(self, cmd: list[str], tool_input: dict, timeout: int) -> str:
        """Run the container and return its stdout output."""
        logger.info("Running container: %s", " ".join(cmd[-3:]))
        start_time = time.time()

        try:
            result = subprocess.run(
                cmd,
                input=json.dumps(tool_input).encode(),
                capture_output=True,
                timeout=timeout,
            )

            elapsed = time.time() - start_time
            logger.info("Container finished in %.1fs (exit=%d)", elapsed, result.returncode)

            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace").strip()
                logger.warning("Container error: %s", stderr[:500])
                return f"Skill execution error: {stderr[:500]}"

            output = result.stdout.decode(errors="replace").strip()
            return output if output else "Skill completed with no output"

        except subprocess.TimeoutExpired:
            logger.warning("Container timed out after %ds", timeout)
            return f"Skill timed out after {timeout} seconds"

        except Exception as e:
            logger.error("Container execution failed: %s", e)
            return f"Skill execution failed: {str(e)}"

    def _execute_native_skill(self, skill, tool_input: dict) -> str:
        """Route to a registered native (non-Docker) skill handler."""
        handler = self._native_handlers.get(skill.name)
        if handler is None:
            return f"No native handler registered for skill '{skill.name}'"
        return handler(tool_input)

    def _execute_install_skill(self, tool_input: dict) -> str:
        """Delegate voice-driven skill installation to the meta skill executor."""
        if self._meta_skill_executor is None:
            return "Meta skill executor not initialised — restart MiniClaw in voice mode."
        return self._meta_skill_executor.run(tool_input)

    def _execute_set_env_var(self, tool_input: dict) -> str:
        """Write a key=value pair to .env and reload skills."""
        key = str(tool_input.get("key", "")).strip()
        value = str(tool_input.get("value", "")).strip()

        if not key:
            return "Error: no key provided."

        if not re.match(r'^[A-Z][A-Z0-9_]*$', key):
            return f"Error: '{key}' is not a valid environment variable name."

        # Only allow keys that are actually needed by a skipped skill
        if self._orchestrator is not None:
            allowed = self._orchestrator.skill_loader.get_missing_env_vars()
            if key not in allowed:
                return (
                    f"Error: '{key}' is not required by any unavailable skill. "
                    f"Allowed keys: {', '.join(sorted(allowed)) or 'none'}."
                )

        # Write to .env
        env_path = REPO_ROOT / ".env"
        try:
            existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        except OSError as e:
            return f"Error reading .env: {e}"

        lines = existing.splitlines(keepends=True)
        new_line = f"{key}={value}\n"
        found = False
        for i, line in enumerate(lines):
            if re.match(rf'^{re.escape(key)}\s*=', line):
                lines[i] = new_line
                found = True
                break
        if not found:
            if lines and not lines[-1].endswith("\n"):
                lines.append("\n")
            lines.append(new_line)

        try:
            env_path.write_text("".join(lines), encoding="utf-8")
        except OSError as e:
            return f"Error writing .env: {e}"

        # Update the running process environment
        os.environ[key] = value

        # Reload skills so newly satisfied requirements take effect
        if self._orchestrator is None:
            logger.info("Set env var %s", key)
            return f"Set {key} successfully."

        self._orchestrator.reload_skills()
        logger.info("Set env var %s and reloaded skills", key)

        skipped = list(self._orchestrator.skill_loader.skipped_skills.keys())
        if skipped:
            return f"Set {key}. Skills still unavailable: {', '.join(skipped)}."
        return f"Set {key}. All skills are now available."

    def _execute_save_memory(self, tool_input: dict) -> str:
        """Write a memory note as a markdown file to the memory vault."""
        topic = str(tool_input.get("topic", "")).strip()
        content = str(tool_input.get("content", "")).strip()

        if not topic or not content:
            return "Error: both topic and content are required."

        vault_path = Path(os.environ.get("MEMORY_VAULT_PATH", Path.home() / ".miniclaw" / "memory"))
        try:
            vault_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return f"Error creating memory vault: {e}"

        date_str = date.today().isoformat()
        slug = re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")
        filename = f"{date_str}_{slug}.md"
        note_path = vault_path / filename

        note = f"---\ndate: {date_str}\ntopic: {topic}\n---\n\n{content}\n"
        try:
            note_path.write_text(note, encoding="utf-8")
        except OSError as e:
            return f"Error saving memory: {e}"

        logger.info("Memory saved: %s", note_path)
        return f"Memory saved: {filename}"

    def _collect_env_vars(self, var_names: list[str]) -> dict[str, str]:
        """Collect env vars that exist in the host environment."""
        return {var: val for var in var_names if (val := os.environ.get(var))}
