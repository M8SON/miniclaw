"""
Container Manager - Handles Docker container lifecycle for skill execution.

Spins up a sandboxed container on demand when a skill is invoked, passes
the request via SKILL_INPUT, collects stdout as the result, and tears the
container down. All skills use the same execution model.

Designed for constrained environments (Raspberry Pi) where containers
should not persist between calls.
"""

import os
import json
import time
import subprocess
import logging

logger = logging.getLogger(__name__)


class ContainerManager:
    """Manages Docker containers for skill execution."""

    DEFAULT_TIMEOUT = 30
    DEFAULT_MEMORY_LIMIT = "256m"

    def __init__(self, memory_limit: str = DEFAULT_MEMORY_LIMIT):
        self.memory_limit = memory_limit
        self._verify_docker()

    def _verify_docker(self):
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
        and returning stdout as the result.

        Input is passed as JSON via the SKILL_INPUT environment variable.
        Output is read from stdout as plain text or JSON.
        """
        config = skill.execution_config
        image = config.get("image", "")
        timeout = config.get("timeout_seconds", self.DEFAULT_TIMEOUT)

        if not image:
            return f"Error: no container image defined for skill '{skill.name}'"

        cmd = self._build_docker_cmd(
            image=image,
            env_vars=self._collect_env_vars(config.get("env_passthrough", [])),
            devices=config.get("devices", []),
            input_data=json.dumps(tool_input),
        )

        return self._run_container(cmd, tool_input, timeout)

    def _build_docker_cmd(
        self,
        image: str,
        env_vars: dict[str, str] | None = None,
        devices: list[str] | None = None,
        input_data: str = "",
    ) -> list[str]:
        """Build a docker run command with security constraints."""
        cmd = [
            "docker", "run",
            "--rm",
            "-i",
            "--network=host",
            f"--memory={self.memory_limit}",
            "--cpus=1.0",
            "--read-only",
            "--tmpfs=/tmp:size=64m",
            "--security-opt=no-new-privileges",
        ]

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

    def _collect_env_vars(self, var_names: list[str]) -> dict[str, str]:
        """Collect env vars that exist in the host environment."""
        return {var: val for var in var_names if (val := os.environ.get(var))}

