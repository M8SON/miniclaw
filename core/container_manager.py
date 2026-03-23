"""
Container Manager - Handles Docker container lifecycle for skill execution.

Spins up containers on demand when a skill is invoked, passes the request,
collects the result, and tears the container down. Designed for constrained
environments (Raspberry Pi) where memory is limited and containers should
not persist unnecessarily.
"""

import os
import json
import time
import subprocess
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ContainerManager:
    """
    Manages Docker containers for skill execution.

    Execution modes:
      - native:         Runs a purpose-built container image defined in config.yaml
      - openclaw_compat: Runs a generic executor image that interprets SKILL.md
    """

    # Maximum time a container can run before being killed
    DEFAULT_TIMEOUT = 30

    # Memory limit per container (critical on Pi with 8-16GB)
    DEFAULT_MEMORY_LIMIT = "256m"

    def __init__(self, memory_limit: str = DEFAULT_MEMORY_LIMIT):
        self.memory_limit = memory_limit
        self._verify_docker()

    def _verify_docker(self):
        """Check that Docker is available and the daemon is running."""
        try:
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                raise RuntimeError("Docker daemon is not running")
            logger.info("Docker is available")
        except FileNotFoundError:
            raise RuntimeError("Docker is not installed")

    def execute_skill(self, skill, tool_input: dict) -> str:
        """
        Execute a skill by spinning up its container, passing input,
        and returning the result.

        Args:
            skill: A Skill object from the skill loader
            tool_input: The input dict from Claude's tool call

        Returns:
            Result string to pass back to Claude
        """
        config = skill.execution_config
        exec_type = config.get("type", "native")

        if exec_type == "native":
            return self._execute_native(skill, tool_input)
        elif exec_type == "openclaw_compat":
            return self._execute_openclaw_compat(skill, tool_input)
        else:
            return f"Unknown execution type: {exec_type}"

    def _execute_native(self, skill, tool_input: dict) -> str:
        """
        Execute a native skill using its purpose-built container.

        Native containers expose a simple contract:
          - Input: JSON passed via stdin or environment variable SKILL_INPUT
          - Output: Result written to stdout as plain text or JSON
        """
        config = skill.execution_config
        image = config.get("image", "")
        timeout = config.get("timeout_seconds", self.DEFAULT_TIMEOUT)

        if not image:
            return f"Error: No container image defined for skill '{skill.name}'"

        # Build the docker run command
        cmd = self._build_docker_cmd(
            image=image,
            env_vars=self._collect_env_vars(config.get("env_passthrough", [])),
            devices=config.get("devices", []),
            timeout=timeout,
            input_data=json.dumps(tool_input),
        )

        return self._run_container(cmd, tool_input, timeout)

    def _execute_openclaw_compat(self, skill, tool_input: dict) -> str:
        """
        Execute an OpenClaw-compatible skill in a sandboxed container.

        Uses a generic executor image that:
          1. Reads the SKILL.md instructions
          2. Receives the tool input
          3. Executes the appropriate action
          4. Returns the result

        This is the compatibility layer - OpenClaw skills expect host access,
        but we run them in isolation.
        """
        config = skill.execution_config
        image = config.get("image", "miniclaw/skill-executor:latest")
        timeout = config.get("timeout_seconds", self.DEFAULT_TIMEOUT)

        # Build environment from passthrough vars
        env_vars = self._collect_env_vars(config.get("env_passthrough", []))

        # Mount the skill directory read-only so the executor can read SKILL.md
        skill_dir = config.get("skill_dir", "")

        cmd = self._build_docker_cmd(
            image=image,
            env_vars=env_vars,
            volumes={skill_dir: "/skill:ro"} if skill_dir else {},
            timeout=timeout,
            input_data=json.dumps(
                {"skill_name": skill.name, "input": tool_input}
            ),
        )

        return self._run_container(cmd, tool_input, timeout)

    def _build_docker_cmd(
        self,
        image: str,
        env_vars: dict[str, str] | None = None,
        devices: list[str] | None = None,
        volumes: dict[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        input_data: str = "",
    ) -> list[str]:
        """Build a docker run command with security constraints."""

        cmd = [
            "docker",
            "run",
            "--rm",                          # auto-remove when done
            "-i",                            # allow stdin
            "--network=host",                # API access (configurable later)
            f"--memory={self.memory_limit}",  # hard memory cap
            "--cpus=1.0",                    # limit CPU on Pi
            "--read-only",                   # read-only root filesystem
            "--tmpfs=/tmp:size=64m",         # writable /tmp with size cap
            "--security-opt=no-new-privileges",
        ]

        # Environment variables
        if env_vars:
            for key, value in env_vars.items():
                cmd.extend(["-e", f"{key}={value}"])

        # Pass input as env var
        if input_data:
            cmd.extend(["-e", f"SKILL_INPUT={input_data}"])

        # Device access (e.g., /dev/gpio for hardware skills)
        if devices:
            for device in devices:
                cmd.extend(["--device", device])

        # Volume mounts
        if volumes:
            for host_path, container_path in volumes.items():
                cmd.extend(["-v", f"{host_path}:{container_path}"])

        cmd.append(image)
        return cmd

    def _run_container(
        self, cmd: list[str], tool_input: dict, timeout: int
    ) -> str:
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

    def _collect_env_vars(self, var_names: list[str]) -> dict[str, str]:
        """
        Collect environment variables to pass into the container.
        Only passes vars that exist in the host environment.
        """
        env_vars = {}
        for var in var_names:
            value = os.environ.get(var)
            if value:
                env_vars[var] = value
        return env_vars

    def image_exists(self, image: str) -> bool:
        """Check if a Docker image exists locally."""
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def pull_image(self, image: str) -> bool:
        """Pull a Docker image if not available locally."""
        if self.image_exists(image):
            return True

        logger.info("Pulling image: %s", image)
        try:
            result = subprocess.run(
                ["docker", "pull", image],
                capture_output=True,
                timeout=300,
            )
            return result.returncode == 0
        except Exception as e:
            logger.error("Failed to pull image %s: %s", image, e)
            return False
