"""
Self-update handler for SKILL.md routing hints.

apply_hint() is the only public entry point. It performs structural
validation, locates or creates the auto-learned section, appends the
addition (with FIFO trim at 30 bullets), atomically rewrites the file,
and commits the change to git. All operations are reversible via
git revert.

Tier eligibility, allow_body=true gating, and rate-limiting are checked
here. The caller (ContainerManager._execute_update_skill_hints) is
responsible for passing turn_id; the rate-limit cache lives on the
caller, not in this module.
"""

import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.skill_policy import TIER_IMPORTED
from core.skill_validator import SkillValidator


logger = logging.getLogger(__name__)


AUTO_SECTION_HEADER = "## Auto-learned routing hints"
MAX_ADDITION_CHARS = 500
MAX_AUTO_BULLETS = 30


@dataclass
class SelfUpdateResult:
    status: str             # ok | rejected | no-op
    reason: str = ""
    skill: str = ""
    added: str = ""


def apply_hint(
    skill_loader,
    skill_name: str,
    addition: str,
    rationale: str,
    *,
    turn_id: str,
    repo_root: Path | None = None,
) -> SelfUpdateResult:
    """Validate + apply an additive routing hint to a skill's SKILL.md."""
    skill = skill_loader.skills.get(skill_name)
    if skill is None:
        return SelfUpdateResult(status="rejected", reason="skill not found")

    if skill.tier == TIER_IMPORTED:
        return SelfUpdateResult(
            status="rejected",
            reason=f"skill {skill_name} is in imported tier; self-update is blocked",
        )

    fm = getattr(skill, "frontmatter", None) or {}
    allow_body = (
        fm.get("metadata", {}).get("miniclaw", {})
          .get("self_update", {}).get("allow_body")
    )
    if allow_body is not True:
        return SelfUpdateResult(
            status="rejected",
            reason=f"skill {skill_name} does not have allow_body: true",
        )

    err = _validate_addition(addition)
    if err:
        return SelfUpdateResult(status="rejected", reason=err)

    addition = addition.strip()
    skill_dir = Path(skill.skill_dir)
    skill_md = skill_dir / "SKILL.md"
    raw = skill_md.read_text(encoding="utf-8")

    if addition in raw:
        return SelfUpdateResult(
            status="no-op",
            reason="already covered",
            skill=skill_name,
        )

    new_raw = _append_to_auto_section(raw, addition)

    # Re-validate the rewritten file end-to-end.
    try:
        SkillValidator().validate_markdown(new_raw, skill_dir)
    except ValueError as e:
        return SelfUpdateResult(
            status="rejected",
            reason=f"rewritten SKILL.md failed validation: {e}",
        )

    _atomic_write(skill_md, new_raw)
    _git_commit_safe(repo_root or skill_dir, skill_md, skill_name, rationale, addition)

    return SelfUpdateResult(
        status="ok",
        skill=skill_name,
        added=addition[:80],
    )


_VALIDATION_RE_FRONTMATTER = re.compile(r"^\s*---\s*$", re.MULTILINE)
_VALIDATION_RE_INPUT_HEADER = re.compile(
    r"^\s*##\s*(Inputs|Parameters|Input Schema)\s*$", re.MULTILINE | re.IGNORECASE
)
_VALIDATION_RE_TOP_HEADING = re.compile(r"^\s*#[^#]", re.MULTILINE)
_VALIDATION_RE_HTML = re.compile(r"<\s*(script|iframe|object|embed)", re.IGNORECASE)


def _validate_addition(addition: str) -> str | None:
    if not addition or not addition.strip():
        return "addition is empty"
    if len(addition) > MAX_ADDITION_CHARS:
        return f"addition exceeds 500 char limit (got {len(addition)})"
    if _VALIDATION_RE_FRONTMATTER.search(addition):
        return "addition contains a frontmatter delimiter (---)"
    if _VALIDATION_RE_INPUT_HEADER.search(addition):
        return (
            "addition contains an input-schema header "
            "(Inputs / Parameters / Input Schema); these would shadow the parsed schema"
        )
    if _VALIDATION_RE_TOP_HEADING.search(addition):
        return "addition contains a top-level (#) heading"
    if _VALIDATION_RE_HTML.search(addition):
        return "addition contains disallowed html"
    return None


def _append_to_auto_section(raw: str, bullet: str) -> str:
    """Find or create the auto section, append bullet, FIFO at MAX_AUTO_BULLETS."""
    bullet = bullet.strip()
    if not bullet.startswith("-"):
        bullet = f"- {bullet}"

    if AUTO_SECTION_HEADER in raw:
        before, _, rest = raw.partition(AUTO_SECTION_HEADER)
        next_section = re.search(r"\n##\s", rest)
        if next_section:
            section_body = rest[: next_section.start()]
            after = rest[next_section.start():]
        else:
            section_body = rest
            after = ""

        existing_bullets = [
            line for line in section_body.splitlines()
            if line.strip().startswith("- ")
        ]
        existing_bullets.append(bullet)
        if len(existing_bullets) > MAX_AUTO_BULLETS:
            existing_bullets = existing_bullets[-MAX_AUTO_BULLETS:]

        new_section = AUTO_SECTION_HEADER + "\n\n" + "\n".join(existing_bullets) + "\n"
        if after:
            return before + new_section + "\n" + after.lstrip("\n")
        return before + new_section

    suffix = "\n" if not raw.endswith("\n") else ""
    return raw + suffix + "\n" + AUTO_SECTION_HEADER + "\n\n" + bullet + "\n"


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _git_commit_safe(repo_root: Path, file_path: Path, skill_name: str, rationale: str, addition: str) -> None:
    """Commit the change. Failure is logged but non-fatal — file write still succeeds."""
    try:
        rel = file_path.relative_to(repo_root)
    except ValueError:
        rel = file_path

    subject = f"self-update({skill_name}): {rationale.strip()[:80]}"
    body = f"added: {addition[:80]}"
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "commit", "-m", subject, "-m", body, "--", str(rel)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning(
                "self-update git commit failed (rc=%d): %s",
                result.returncode, result.stderr.strip()[:200],
            )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("self-update git commit skipped: %s", e)
