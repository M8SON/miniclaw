"""
Skill eligibility checks for MiniClaw.

Evaluates whether a structurally valid skill can run on the current system
based on environment variables, binaries, and OS constraints.
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
