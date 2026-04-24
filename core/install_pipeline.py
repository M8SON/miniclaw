"""
Shared install pipeline for MiniClaw skills.

Entry points (voice via install-skill, CLI via core.skill_cli, future mobile
HTTP) all funnel through InstallPipeline.install_from_path or
InstallPipeline.install_from_url.

Pipeline steps:
  1. Fetch   — external code (git clone / tarball) for install_from_url.
               install_from_path skips this.
  2. Locate  — find SKILL.md inside the staging directory.
  3. Validate— SkillValidator + DockerfileValidator at the requested tier.
  4. Summarize — render a PermissionSummary and pass to the confirmer.
  5. Confirm — three-gate confirmation: install -> build -> restart.
  6. Commit  — strip any .install.json shipped in staging, move staging to
               <install_root>/<name>, write fresh .install.json.
  7. Build   — delegate to the builder (Docker build invocation).
  8. Reload  — delegate to the reloader (orchestrator.reload_skills()).

Confirmer/builder/reloader are dependency-injected so tests can substitute
no-ops and voice/CLI paths can substitute their own prompts.
"""

import datetime
import enum
import logging
import shutil
import subprocess
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from core.dockerfile_validator import DockerfileValidationError, validate as validate_dockerfile
from core.install_metadata import (
    INSTALL_FILENAME,
    InstallMetadata,
    compute_skill_sha256,
    write_metadata,
)
from core.skill_policy import (
    TIER_IMPORTED,
    is_credential_pattern,
)
from core.skill_validator import SkillValidator


logger = logging.getLogger(__name__)


class InstallDecision(enum.Enum):
    INSTALLED = "installed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ConfirmProtocol(Protocol):
    def confirm_gate(self, gate: str, summary: str) -> bool: ...


class BuildProtocol(Protocol):
    def build(self, skill_dir: Path, image: str) -> None: ...


class ReloadProtocol(Protocol):
    def reload(self) -> None: ...


@dataclass
class PermissionSummary:
    name: str
    description: str
    tier: str
    image: str | None
    env_passthrough: list[str]
    credential_warnings: list[str]
    memory: str | None
    timeout_seconds: int | None
    devices: list[str]
    volumes: list[str]

    def to_text(self) -> str:
        lines = [
            f"Install skill '{self.name}' at tier {self.tier!r}.",
            f"  Description: {self.description}",
        ]
        if self.image:
            lines.append(f"  Image: {self.image}")
        if self.env_passthrough:
            lines.append(f"  env_passthrough: {', '.join(self.env_passthrough)}")
            if self.credential_warnings:
                lines.append(
                    f"  WARNING — credential-like names in env_passthrough: "
                    f"{', '.join(self.credential_warnings)}"
                )
        if self.memory:
            lines.append(f"  Memory: {self.memory}")
        if self.timeout_seconds is not None:
            lines.append(f"  Timeout: {self.timeout_seconds}s")
        if self.devices:
            lines.append(f"  Devices: {', '.join(self.devices)}")
        if self.volumes:
            lines.append(f"  Volumes: {', '.join(self.volumes)}")
        return "\n".join(lines)


def summarize_permissions(
    *, name: str, description: str, config: dict, tier: str = TIER_IMPORTED,
) -> PermissionSummary:
    env_passthrough = list(config.get("env_passthrough") or [])
    credential_warnings = [e for e in env_passthrough if is_credential_pattern(e)]
    return PermissionSummary(
        name=name,
        description=description,
        tier=tier,
        image=config.get("image"),
        env_passthrough=env_passthrough,
        credential_warnings=credential_warnings,
        memory=config.get("memory"),
        timeout_seconds=config.get("timeout_seconds"),
        devices=list(config.get("devices") or []),
        volumes=list(config.get("volumes") or []),
    )


class DockerBuilder:
    """Production builder — invokes the host-side build script."""
    def __init__(self, build_script: Path | None = None):
        self.build_script = build_script or (
            Path(__file__).resolve().parent.parent / "scripts" / "build_new_skill.sh"
        )

    def build(self, skill_dir: Path, image: str) -> None:
        if not self.build_script.exists():
            raise RuntimeError(f"Build script not found: {self.build_script}")
        result = subprocess.run(
            [str(self.build_script), str(skill_dir), image],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"docker build failed (rc={result.returncode}): "
                f"{result.stderr.strip()[:300]}"
            )


class InstallPipeline:
    def __init__(
        self,
        *,
        confirmer: ConfirmProtocol,
        builder: BuildProtocol,
        reloader: ReloadProtocol,
        install_root: Path,
    ):
        self.confirmer = confirmer
        self.builder = builder
        self.reloader = reloader
        self.install_root = install_root
        self.validator = SkillValidator()

    def install_from_path(self, staging: Path, *, tier: str) -> InstallDecision:
        """Install a staged skill directory. Staging is copied, not moved."""
        skill_md = staging / "SKILL.md"
        if not skill_md.exists():
            logger.error("staging %s has no SKILL.md", staging)
            return InstallDecision.FAILED

        try:
            frontmatter, _ = self.validator.validate_markdown(
                skill_md.read_text(encoding="utf-8"),
                staging,
            )
        except ValueError as e:
            logger.error("invalid SKILL.md: %s", e)
            return InstallDecision.FAILED

        name = frontmatter["name"]
        description = frontmatter["description"]

        config_path = staging / "config.yaml"
        raw_config = {}
        if config_path.exists():
            try:
                raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as e:
                logger.error("invalid config.yaml: %s", e)
                return InstallDecision.FAILED
        try:
            config = self.validator.validate_execution_config(
                raw_config, tier=tier, skill_name=name,
            )
        except ValueError as e:
            logger.error("config.yaml rejected at tier %s: %s", tier, e)
            return InstallDecision.FAILED

        dockerfile = staging / "scripts" / "Dockerfile"
        if config.get("type", "docker") == "docker" and dockerfile.exists():
            try:
                validate_dockerfile(dockerfile, tier=tier)
            except DockerfileValidationError as e:
                logger.error("Dockerfile rejected at tier %s: %s", tier, e)
                return InstallDecision.FAILED

        summary = summarize_permissions(
            name=name, description=description, config=config, tier=tier,
        )
        summary_text = summary.to_text()

        if not self.confirmer.confirm_gate("install", summary_text):
            return InstallDecision.CANCELLED
        if not self.confirmer.confirm_gate("build", summary_text):
            return InstallDecision.CANCELLED

        # Commit install: strip shipped metadata, copy into place.
        final_dir = self.install_root / name
        if final_dir.exists():
            shutil.rmtree(final_dir)
        shutil.copytree(staging, final_dir, symlinks=False)

        shipped_install = final_dir / INSTALL_FILENAME
        if shipped_install.exists():
            logger.warning(
                "skill shipped its own %s; discarding (provenance is pipeline-written)",
                INSTALL_FILENAME,
            )
            shipped_install.unlink()

        sha256 = compute_skill_sha256(final_dir)
        meta = InstallMetadata(
            source=str(staging),
            sha256=sha256,
            installed_at=datetime.datetime.now().isoformat(timespec="seconds"),
            user_confirmed_env_passthrough=[],
        )
        write_metadata(final_dir, meta)

        # Build
        image = config.get("image")
        if config.get("type", "docker") == "docker" and image:
            try:
                self.builder.build(final_dir, image)
            except Exception as e:
                logger.error("build failed: %s", e)
                shutil.rmtree(final_dir, ignore_errors=True)
                return InstallDecision.FAILED

        # Reload gate
        if not self.confirmer.confirm_gate("restart", summary_text):
            return InstallDecision.INSTALLED  # files placed; reload skipped

        try:
            self.reloader.reload()
        except Exception as e:
            logger.error("reload failed: %s", e)

        return InstallDecision.INSTALLED
