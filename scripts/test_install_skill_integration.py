#!/usr/bin/env python3
"""
Optional integration test for the real install_skill flow.

This script uses:
- the real Claude CLI to generate a disposable skill
- the real Docker build path
- a fake voice object that auto-confirms the three prompts

By default it cleans up the generated skill directories and image on success
or failure so the repository is left unchanged.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.meta_skill import MetaSkillExecutor, _derive_skill_name
from core.skill_loader import SkillLoader


@dataclass
class IntegrationOrchestrator:
    skill_loader: SkillLoader
    reload_count: int = 0

    def reload_skills(self):
        self.reload_count += 1
        self.skill_loader.load_all()


class FakeVoice:
    def __init__(self, transcripts: list[str]):
        self._transcripts = list(transcripts)
        self.spoken: list[str] = []

    def speak(self, text: str):
        self.spoken.append(text)
        print(f"[voice] {text}")

    def listen(self, max_wait_seconds: int):
        if not self._transcripts:
            return ""
        response = self._transcripts.pop(0)
        print(f"[heard] {response}")
        return response


def _check_prerequisites() -> list[str]:
    errors = []

    if not shutil.which("claude"):
        errors.append("claude CLI not found in PATH")

    if not shutil.which("docker"):
        errors.append("docker not found in PATH")
    else:
        docker_info = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if docker_info.returncode != 0:
            errors.append(f"docker info failed: {(docker_info.stderr or docker_info.stdout).strip()}")

    if not (os.environ.get("ANTHROPIC_API_KEY") or (REPO_ROOT / ".env").exists()):
        errors.append("ANTHROPIC_API_KEY not set and .env not found")

    return errors


def _cleanup(skill_name: str):
    for path in [
        REPO_ROOT / "skills" / skill_name,
        REPO_ROOT / "containers" / skill_name,
    ]:
        if path.exists():
            shutil.rmtree(path)
            print(f"[cleanup] removed {path}")

    image_name = f"miniclaw/{skill_name.replace('_', '-')}:latest"
    subprocess.run(
        ["docker", "image", "rm", "-f", image_name],
        capture_output=True,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real install_skill integration test")
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Keep generated skill files and Docker image after the test",
    )
    parser.add_argument(
        "--description",
        default=None,
        help="Optional custom skill description. If omitted, a disposable test description is generated.",
    )
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")

    errors = _check_prerequisites()
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 2

    token = uuid.uuid4().hex[:8]
    description = args.description or (
        f"integration {token[:4]} {token[4:]} create a test skill that returns a short hello message"
    )
    skill_name = _derive_skill_name(description)

    print(f"[info] repo root: {REPO_ROOT}")
    print(f"[info] description: {description}")
    print(f"[info] expected skill name: {skill_name}")

    orchestrator = IntegrationOrchestrator(
        skill_loader=SkillLoader(search_paths=[REPO_ROOT / "skills"])
    )
    orchestrator.skill_loader.load_all()

    voice = FakeVoice(
        ["confirm install", "confirm build", "confirm restart"]
    )
    executor = MetaSkillExecutor(voice=voice, orchestrator=orchestrator)

    result = ""
    try:
        result = executor.run({"description": description})
        print(f"[result] {result}")

        if "is now active" not in result:
            print("error: install flow did not complete successfully", file=sys.stderr)
            return 1

        if skill_name not in orchestrator.skill_loader.skills:
            print(f"error: generated skill '{skill_name}' was not loaded after reload", file=sys.stderr)
            return 1

        print(f"[ok] generated skill '{skill_name}' loaded successfully")
        return 0
    finally:
        if not args.keep_artifacts:
            _cleanup(skill_name)
        else:
            print(f"[info] artifacts retained for {skill_name}")


if __name__ == "__main__":
    raise SystemExit(main())
