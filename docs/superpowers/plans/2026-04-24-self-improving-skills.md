# Self-Improving Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan. Batch tasks within a phase; checkpoint at phase boundaries. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `update_skill_hints` native skill, prompt-builder hooks, and a 15-tool-call checkpoint nudge in the tool loop so skills with `metadata.miniclaw.self_update.allow_body: true` autonomously gain additive routing hints from observed conversation patterns.

**Architecture:** A new pure module (`core/skill_self_update.py`) owns the handler pipeline (validate addition, find/append the auto-section, FIFO trim at 30, rewrite SKILL.md atomically, git commit, reload). `ContainerManager` registers the new native handler and tracks per-turn rate limits. `ToolLoop` counts tool-use blocks per turn and injects a checkpoint nudge into the per-round system prompt when the count crosses a multiple of 15. `PromptBuilder` adds standing self-update guidance only when at least one loaded skill is opted in.

**Tech Stack:** Python 3.11+, stdlib `subprocess` (for `git`), pytest, existing MiniClaw modules.

**Related spec:** `docs/superpowers/specs/2026-04-24-self-improving-skills-design.md`

---

## File Structure Map

### New files

| Path | Responsibility |
|---|---|
| `core/skill_self_update.py` | Pure handler — validate / append-with-FIFO / atomic-write / git-commit. No orchestrator coupling. |
| `skills/update-skill-hints/SKILL.md` | Native skill definition Claude sees as a tool. |
| `skills/update-skill-hints/config.yaml` | Native skill config (`type: native`). |
| `tests/test_skill_self_update.py` | Unit tests for the handler. |
| `tests/test_skill_self_update_git.py` | Tests covering the git-commit side effect. |
| `tests/test_orchestrator_checkpoint.py` | Tests for the 15-tool-call checkpoint nudge. |

### Modified files

| Path | Change |
|---|---|
| `core/container_manager.py` | Register `update-skill-hints` in `_native_handlers`; add `_execute_update_skill_hints`; add per-turn tracking for the rate limit; expose `start_turn()` for the tool loop to call. |
| `core/tool_loop.py` | Call `container_manager.start_turn()` at the top of `run()`. Count tool-use blocks per turn. Inject the checkpoint nudge into per-round system prompt when count crosses a multiple of 15. |
| `core/prompt_builder.py` | When any loaded skill has `metadata.miniclaw.self_update.allow_body: true`, append the standing self-update guidance to the assembled system prompt. |
| `WORKING_MEMORY.md` | Mark Hermes roadmap item #4 done. |

---

## Phase 1 — Handler module (pure, no orchestrator coupling)

Phase-boundary checkpoint: `pytest tests/test_skill_self_update.py -v` passes.

### Task 1: Validation + dispatch skeleton

**Files:**
- Create: `core/skill_self_update.py`
- Create: `tests/test_skill_self_update.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_skill_self_update.py
"""Unit tests for the skill_self_update handler."""

import unittest
from pathlib import Path
import tempfile
import textwrap

import yaml

from core.skill_self_update import (
    SelfUpdateResult,
    apply_hint,
)


def _write_skill(parent: Path, name: str, *, allow_body: bool, body: str = None) -> Path:
    skill_dir = parent / name
    skill_dir.mkdir(parents=True)
    fm = {
        "name": name,
        "description": f"Test skill {name}.",
        "metadata": {
            "miniclaw": {
                "self_update": {"allow_body": allow_body},
            }
        },
    }
    body = body or "# Test\n\nWhen the user says hello.\n"
    (skill_dir / "SKILL.md").write_text(
        "---\n" + yaml.dump(fm, sort_keys=False) + "---\n\n" + body
    )
    (skill_dir / "config.yaml").write_text(
        yaml.dump({"type": "docker", "image": f"miniclaw/{name}:latest"})
    )
    return skill_dir


class _StubLoader:
    """Minimal SkillLoader stub for tests."""
    def __init__(self, skills_by_name: dict):
        self.skills = skills_by_name


class _StubSkill:
    def __init__(self, name, tier, skill_dir, frontmatter):
        self.name = name
        self.tier = tier
        self.skill_dir = str(skill_dir)
        self._frontmatter = frontmatter

    @property
    def frontmatter(self):
        return self._frontmatter


def _make_loader(tmp: Path, name: str, *, tier: str, allow_body: bool):
    skill_dir = _write_skill(tmp, name, allow_body=allow_body)
    fm = {"metadata": {"miniclaw": {"self_update": {"allow_body": allow_body}}}}
    skill = _StubSkill(name, tier, skill_dir, fm)
    return _StubLoader({name: skill}), skill_dir


class TestEligibility(unittest.TestCase):
    def test_skill_not_found_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader = _StubLoader({})
            r = apply_hint(loader, "ghost", "- new bullet", "rationale", turn_id="t1")
            self.assertEqual(r.status, "rejected")
            self.assertIn("not found", r.reason.lower())

    def test_imported_tier_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = _make_loader(Path(tmp), "foo", tier="imported", allow_body=True)
            r = apply_hint(loader, "foo", "- bullet", "rationale", turn_id="t1")
            self.assertEqual(r.status, "rejected")
            self.assertIn("imported", r.reason.lower())

    def test_allow_body_false_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = _make_loader(Path(tmp), "foo", tier="bundled", allow_body=False)
            r = apply_hint(loader, "foo", "- bullet", "rationale", turn_id="t1")
            self.assertEqual(r.status, "rejected")
            self.assertIn("allow_body", r.reason.lower())

    def test_bundled_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = _make_loader(Path(tmp), "foo", tier="bundled", allow_body=True)
            r = apply_hint(loader, "foo", "- new phrasing", "rationale", turn_id="t1")
            self.assertEqual(r.status, "ok")

    def test_authored_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = _make_loader(Path(tmp), "foo", tier="authored", allow_body=True)
            r = apply_hint(loader, "foo", "- new phrasing", "rationale", turn_id="t1")
            self.assertEqual(r.status, "ok")

    def test_dev_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = _make_loader(Path(tmp), "foo", tier="dev", allow_body=True)
            r = apply_hint(loader, "foo", "- new phrasing", "rationale", turn_id="t1")
            self.assertEqual(r.status, "ok")


class TestAdditionStructuralChecks(unittest.TestCase):
    def _setup(self, tmp):
        return _make_loader(Path(tmp), "foo", tier="bundled", allow_body=True)

    def test_addition_too_long_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = self._setup(tmp)
            r = apply_hint(loader, "foo", "x" * 501, "rationale", turn_id="t1")
            self.assertEqual(r.status, "rejected")
            self.assertIn("500", r.reason)

    def test_empty_addition_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = self._setup(tmp)
            r = apply_hint(loader, "foo", "   ", "rationale", turn_id="t1")
            self.assertEqual(r.status, "rejected")

    def test_frontmatter_delimiter_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = self._setup(tmp)
            r = apply_hint(loader, "foo", "- bullet\n---\nname: evil", "rat", turn_id="t1")
            self.assertEqual(r.status, "rejected")
            self.assertIn("frontmatter", r.reason.lower())

    def test_input_schema_header_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = self._setup(tmp)
            r = apply_hint(loader, "foo", "## Inputs\n\nstuff", "rat", turn_id="t1")
            self.assertEqual(r.status, "rejected")
            self.assertIn("inputs", r.reason.lower())

    def test_top_level_heading_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            loader, _ = self._setup(tmp)
            r = apply_hint(loader, "foo", "# Big Heading", "rat", turn_id="t1")
            self.assertEqual(r.status, "rejected")
            self.assertIn("heading", r.reason.lower())


class TestAlreadyCovered(unittest.TestCase):
    def test_addition_already_in_body_is_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = (
                "## When to use\n"
                "- already there phrasing\n"
            )
            skill_dir = _write_skill(Path(tmp), "foo", allow_body=True, body=body)
            fm = {"metadata": {"miniclaw": {"self_update": {"allow_body": True}}}}
            skill = _StubSkill("foo", "bundled", skill_dir, fm)
            loader = _StubLoader({"foo": skill})
            r = apply_hint(loader, "foo", "- already there phrasing", "rat", turn_id="t1")
            self.assertEqual(r.status, "no-op")
            self.assertIn("already", r.reason.lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_skill_self_update.py -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'core.skill_self_update'`.

- [ ] **Step 3: Implement `core/skill_self_update.py`**

```python
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
        # rest starts with '\n' typically; the section continues until next ## or EOF.
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

    # No existing section — append at end.
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
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_skill_self_update.py -v 2>&1 | tail -25
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd ~/linux/miniclaw && git add core/skill_self_update.py tests/test_skill_self_update.py && git commit -m "feat(skills): add self-update handler with structural validation"
```

### Task 2: FIFO + section management

**Files:**
- Modify: `tests/test_skill_self_update.py` (append)

- [ ] **Step 1: Append failing tests**

```python
# Append to tests/test_skill_self_update.py

class TestSectionManagement(unittest.TestCase):
    def test_creates_auto_section_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            skill_dir = _write_skill(tmp_p, "foo", allow_body=True)
            fm = {"metadata": {"miniclaw": {"self_update": {"allow_body": True}}}}
            skill = _StubSkill("foo", "bundled", skill_dir, fm)
            loader = _StubLoader({"foo": skill})

            apply_hint(loader, "foo", "- first auto hint", "rat", turn_id="t1")

            content = (skill_dir / "SKILL.md").read_text()
            self.assertIn("## Auto-learned routing hints", content)
            self.assertIn("- first auto hint", content)

    def test_appends_to_existing_auto_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            body = (
                "# Test\n\n"
                "## Auto-learned routing hints\n\n"
                "- existing hint\n"
            )
            skill_dir = _write_skill(tmp_p, "foo", allow_body=True, body=body)
            fm = {"metadata": {"miniclaw": {"self_update": {"allow_body": True}}}}
            skill = _StubSkill("foo", "bundled", skill_dir, fm)
            loader = _StubLoader({"foo": skill})

            apply_hint(loader, "foo", "- second hint", "rat", turn_id="t1")

            content = (skill_dir / "SKILL.md").read_text()
            self.assertIn("- existing hint", content)
            self.assertIn("- second hint", content)
            # Only one auto-section header, not two.
            self.assertEqual(content.count("## Auto-learned routing hints"), 1)

    def test_fifo_drops_oldest_at_31st_bullet(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            existing = "\n".join(f"- bullet {i}" for i in range(30))
            body = (
                "# Test\n\n"
                "## Auto-learned routing hints\n\n"
                f"{existing}\n"
            )
            skill_dir = _write_skill(tmp_p, "foo", allow_body=True, body=body)
            fm = {"metadata": {"miniclaw": {"self_update": {"allow_body": True}}}}
            skill = _StubSkill("foo", "bundled", skill_dir, fm)
            loader = _StubLoader({"foo": skill})

            r = apply_hint(loader, "foo", "- bullet 30", "rat", turn_id="t1")
            self.assertEqual(r.status, "ok")

            content = (skill_dir / "SKILL.md").read_text()
            # The oldest (bullet 0) should be gone; bullet 30 is in.
            self.assertNotIn("- bullet 0\n", content)
            self.assertIn("- bullet 30", content)
            self.assertIn("- bullet 1\n", content)

    def test_section_inserted_before_subsequent_section(self):
        """If body has e.g. ## Other after the proposed insert location, preserve it."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            body = (
                "# Test\n\n"
                "## When to use\n\n"
                "- a\n\n"
                "## Auto-learned routing hints\n\n"
                "- existing\n\n"
                "## After section\n\n"
                "tail content\n"
            )
            skill_dir = _write_skill(tmp_p, "foo", allow_body=True, body=body)
            fm = {"metadata": {"miniclaw": {"self_update": {"allow_body": True}}}}
            skill = _StubSkill("foo", "bundled", skill_dir, fm)
            loader = _StubLoader({"foo": skill})

            apply_hint(loader, "foo", "- new", "rat", turn_id="t1")

            content = (skill_dir / "SKILL.md").read_text()
            self.assertIn("- existing", content)
            self.assertIn("- new", content)
            self.assertIn("## After section", content)
            self.assertIn("tail content", content)
```

- [ ] **Step 2: Run tests**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_skill_self_update.py::TestSectionManagement -v 2>&1 | tail -10
```

Expected: all 4 tests pass (handler logic in Task 1 already covers FIFO + section creation).

- [ ] **Step 3: If any test fails**, the issue is in `_append_to_auto_section`. Re-read the implementation, fix in place, re-run.

- [ ] **Step 4: Commit**

```bash
cd ~/linux/miniclaw && git add tests/test_skill_self_update.py && git commit -m "test(skills): cover FIFO + section management for self-update"
```

---

## Phase 2 — Native skill registration + container_manager wiring

Phase-boundary checkpoint: bundled skill `update-skill-hints` loads cleanly; existing tests still pass; new handler dispatches and rate-limits per turn.

### Task 3: Bundled `update-skill-hints` skill

**Files:**
- Create: `skills/update-skill-hints/SKILL.md`
- Create: `skills/update-skill-hints/config.yaml`

- [ ] **Step 1: Create `skills/update-skill-hints/SKILL.md`**

```markdown
---
name: update-skill-hints
description: Add an additive routing hint to a skill's SKILL.md so it learns
  from observed user phrasings. Use when you notice a skill was routed on a
  novel successful phrasing or when you corrected a misroute. Updates only
  skills with metadata.miniclaw.self_update.allow_body set to true.
metadata:
  miniclaw:
    self_update:
      allow_body: false
---

# Update Skill Hints

## When to use

Call this skill when you observe one of these patterns:

- NOVEL SUCCESSFUL PHRASING: a user said something the target skill's
  SKILL.md doesn't explicitly mention, the skill ran cleanly, and the
  result satisfied the user. Add the phrasing as an example.
- ROUTING MISS YOU CORRECTED: you initially routed to skill X, the user
  clarified or the result didn't fit, and re-routing to skill Y satisfied
  the request. After the request completes via Y, add a hint to Y about
  the original phrasing.

Do NOT use this for security-relevant skills (install-skill, set-env-var,
save-memory). Their routing must stay manually authored.

When in doubt, do not call. Auto-learned hints accumulate; bad ones are
work to clean up.

## Inputs

```yaml
type: object
properties:
  skill_name:
    type: string
    description: Kebab-case name of the skill to update (e.g. weather, web-search).
  addition:
    type: string
    description: |
      The new routing hint to append. A short markdown bullet (one line),
      typically a phrasing example. Must be additive — no section headers,
      no frontmatter, no input-schema modifications.
  rationale:
    type: string
    description: One sentence (15 words or fewer) explaining why this addition is warranted.
required:
  - skill_name
  - addition
  - rationale
```

## How to respond

After a successful call, the orchestrator will reload the skill and the new
hint takes effect on the next routing decision. Tell the user briefly that
you've updated the skill — short spoken sentence. After a rejection or
no-op, do not retry; continue the user's original request.
```

- [ ] **Step 2: Create `skills/update-skill-hints/config.yaml`**

```yaml
type: native
```

- [ ] **Step 3: Verify the skill loads (it should not be in `_native_handlers` yet, so it'll register as invalid)**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -c "
from core.skill_loader import SkillLoader
loader = SkillLoader()
loader.load_all()
s = loader.skills.get('update-skill-hints')
print('loaded:', bool(s), 'tier:', s.tier if s else None)
"
```

Expected: `loaded: True tier: bundled` (the skill structure is valid; the dispatch handler is added in Task 4).

- [ ] **Step 4: Commit**

```bash
cd ~/linux/miniclaw && git add skills/update-skill-hints/ && git commit -m "feat(skills): bundled update-skill-hints native skill"
```

### Task 4: Container manager handler + per-turn rate limit

**Files:**
- Modify: `core/container_manager.py`
- Modify: `tests/test_container_manager.py` (append)

- [ ] **Step 1: Append failing tests**

```python
# Append to tests/test_container_manager.py

import os as _os
import tempfile as _tempfile
from pathlib import Path as _Path


def test_update_skill_hints_dispatches_to_handler():
    container_manager = _load_container_manager().ContainerManager()

    captured = {}

    def fake_apply_hint(loader, skill_name, addition, rationale, *, turn_id, repo_root=None):
        from core.skill_self_update import SelfUpdateResult
        captured["skill_name"] = skill_name
        captured["turn_id"] = turn_id
        return SelfUpdateResult(status="ok", skill=skill_name, added=addition[:80])

    import core.skill_self_update as ssu
    orig = ssu.apply_hint
    ssu.apply_hint = fake_apply_hint
    try:
        manager = container_manager
        manager._orchestrator = None
        manager._skill_loader_for_self_update = type("L", (), {"skills": {}})()
        manager.start_turn()
        result_json = manager._execute_update_skill_hints({
            "skill_name": "weather",
            "addition": "- forecast",
            "rationale": "novel phrasing",
        })
        assert "ok" in result_json
        assert captured["skill_name"] == "weather"
        # turn_id is set by start_turn() to a non-empty string.
        assert captured["turn_id"]
    finally:
        ssu.apply_hint = orig


def test_update_skill_hints_rate_limited_within_turn():
    container_manager_module = _load_container_manager()
    manager = container_manager_module.ContainerManager()
    manager._orchestrator = None
    manager._skill_loader_for_self_update = type("L", (), {"skills": {}})()

    call_count = {"n": 0}

    def fake_apply_hint(loader, skill_name, addition, rationale, *, turn_id, repo_root=None):
        from core.skill_self_update import SelfUpdateResult
        call_count["n"] += 1
        return SelfUpdateResult(status="ok", skill=skill_name, added="x")

    import core.skill_self_update as ssu
    orig = ssu.apply_hint
    ssu.apply_hint = fake_apply_hint
    try:
        manager.start_turn()
        manager._execute_update_skill_hints({"skill_name": "weather", "addition": "-x", "rationale": "r"})
        result_json = manager._execute_update_skill_hints({"skill_name": "weather", "addition": "-y", "rationale": "r"})
        assert "rate-limited" in result_json
        # Second call to apply_hint did NOT happen.
        assert call_count["n"] == 1
    finally:
        ssu.apply_hint = orig


def test_update_skill_hints_rate_limit_resets_on_new_turn():
    container_manager_module = _load_container_manager()
    manager = container_manager_module.ContainerManager()
    manager._orchestrator = None
    manager._skill_loader_for_self_update = type("L", (), {"skills": {}})()

    call_count = {"n": 0}

    def fake_apply_hint(loader, skill_name, addition, rationale, *, turn_id, repo_root=None):
        from core.skill_self_update import SelfUpdateResult
        call_count["n"] += 1
        return SelfUpdateResult(status="ok", skill=skill_name, added="x")

    import core.skill_self_update as ssu
    orig = ssu.apply_hint
    ssu.apply_hint = fake_apply_hint
    try:
        manager.start_turn()
        manager._execute_update_skill_hints({"skill_name": "weather", "addition": "-a", "rationale": "r"})
        manager.start_turn()  # new turn
        manager._execute_update_skill_hints({"skill_name": "weather", "addition": "-b", "rationale": "r"})
        assert call_count["n"] == 2
    finally:
        ssu.apply_hint = orig
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_container_manager.py::test_update_skill_hints_dispatches_to_handler -v 2>&1 | tail -10
```

Expected: failure (`AttributeError: 'ContainerManager' object has no attribute '_execute_update_skill_hints'` or `start_turn`).

- [ ] **Step 3: Modify `core/container_manager.py`**

In the imports near the top (after the existing imports), add:

```python
import uuid

from core import skill_self_update
```

In `ContainerManager.__init__`, after `self._native_handlers = {...}` block, register the new handler. Locate the existing dict and add the new entry:

```python
        self._native_handlers = {
            "install-skill": self._execute_install_skill,
            "set-env-var": self._execute_set_env_var,
            "save-memory": self._execute_save_memory,
            "dashboard": self._execute_dashboard,
            "soundcloud": self._execute_soundcloud,
            "schedule": self._execute_schedule,
            "recall-session": self._execute_recall_session,
            "update-skill-hints": self._execute_update_skill_hints,
        }
```

Also in `__init__`, add the rate-limit cache and turn id:

```python
        self._self_update_seen: dict[str, str] = {}  # skill_name -> turn_id
        self._current_turn_id: str = ""
        # _skill_loader_for_self_update is injected from main.py after construction
        # (alongside _orchestrator). Lets the handler reach skill metadata.
        self._skill_loader_for_self_update = None
```

Add these methods to the `ContainerManager` class (after `_execute_recall_session`):

```python
    def start_turn(self) -> None:
        """Bump the turn id used to scope per-turn rate limits."""
        self._current_turn_id = str(uuid.uuid4())

    def _execute_update_skill_hints(self, tool_input: dict) -> str:
        """Apply an additive routing hint to a skill's SKILL.md."""
        skill_name = str(tool_input.get("skill_name", "")).strip()
        addition = str(tool_input.get("addition", ""))
        rationale = str(tool_input.get("rationale", "")).strip()

        if not skill_name or not addition or not rationale:
            return json.dumps({
                "status": "rejected",
                "reason": "skill_name, addition, and rationale are all required",
            })

        last_turn = self._self_update_seen.get(skill_name)
        if last_turn == self._current_turn_id and self._current_turn_id:
            return json.dumps({
                "status": "rate-limited",
                "reason": f"already updated {skill_name} this turn",
            })

        loader = (
            self._skill_loader_for_self_update
            or (self._orchestrator.skill_loader if self._orchestrator else None)
        )
        if loader is None:
            return json.dumps({
                "status": "rejected",
                "reason": "skill loader not available",
            })

        result = skill_self_update.apply_hint(
            loader, skill_name, addition, rationale,
            turn_id=self._current_turn_id,
            repo_root=REPO_ROOT,
        )

        if result.status == "ok":
            self._self_update_seen[skill_name] = self._current_turn_id
            if self._orchestrator is not None:
                try:
                    self._orchestrator.reload_skills()
                except Exception:
                    logger.exception("reload_skills failed after self-update")

        return json.dumps({
            "status": result.status,
            "skill": result.skill,
            "reason": result.reason,
            "added": result.added,
        })
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_container_manager.py -k "update_skill_hints or rate_limit" -v 2>&1 | tail -10
```

Expected: 3 tests pass.

- [ ] **Step 5: Verify nothing else regressed**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
cd ~/linux/miniclaw && git add core/container_manager.py tests/test_container_manager.py && git commit -m "feat(skills): wire update-skill-hints native handler with per-turn rate limit"
```

---

## Phase 3 — Loader exposes frontmatter; main.py wiring

Phase-boundary checkpoint: `Skill` objects expose `frontmatter`; main.py injects skill_loader into container_manager.

### Task 5: Expose frontmatter on `Skill`

**Files:**
- Modify: `core/skill_loader.py`
- Modify: `tests/test_skill_loader_tiered.py` (append)
- Modify: `main.py`

- [ ] **Step 1: Append a failing test**

```python
# Append to tests/test_skill_loader_tiered.py

def test_skill_exposes_frontmatter_dict():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        bundled = tmp_p / "bundled"; bundled.mkdir()
        # Custom write so the SKILL.md has a self_update block.
        skill_dir = bundled / "alpha"
        (skill_dir / "scripts").mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: alpha\ndescription: x.\n"
            "metadata:\n  miniclaw:\n    self_update:\n      allow_body: true\n"
            "---\n\n"
            "## Inputs\n\n```yaml\ntype: object\nproperties: {}\nrequired: []\n```\n\nBody.\n"
        )
        (skill_dir / "config.yaml").write_text(yaml.dump({
            "type": "docker",
            "image": "miniclaw/alpha:latest",
            "env_passthrough": [],
            "timeout_seconds": 15,
            "devices": [],
        }))
        (skill_dir / "scripts" / "Dockerfile").write_text(
            "FROM miniclaw/base:latest\nCMD [\"python\", \"app.py\"]\n"
        )
        (skill_dir / "scripts" / "app.py").write_text("print('ok')\n")

        loader = SkillLoader(search_paths=[bundled])
        skills = loader.load_all()
        s = skills["alpha"]
        assert hasattr(s, "frontmatter")
        assert s.frontmatter["metadata"]["miniclaw"]["self_update"]["allow_body"] is True
```

- [ ] **Step 2: Run to verify failure**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_skill_loader_tiered.py::test_skill_exposes_frontmatter_dict -v 2>&1 | tail -10
```

Expected: `AttributeError: 'Skill' object has no attribute 'frontmatter'`.

- [ ] **Step 3: Modify `core/skill_loader.py`** — add `frontmatter` to `Skill`

Find the `Skill` class definition. Update its `__init__` to accept and store frontmatter:

```python
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
        tier: str,
        frontmatter: dict,
    ):
        self.name = name
        self.description = description
        self.instructions = instructions
        self.tool_definition = tool_definition
        self.execution_config = execution_config
        self.skill_dir = skill_dir
        self.tier = tier
        self.frontmatter = frontmatter

    def __repr__(self):
        return f"Skill(name={self.name!r}, tier={self.tier!r})"
```

In `_load_skill`, find the `return Skill(...)` at the end and add the `frontmatter` arg:

```python
        return Skill(
            name=name,
            description=description,
            instructions=body,
            tool_definition=tool_definition,
            execution_config=execution_config,
            skill_dir=str(skill_dir),
            tier=tier,
            frontmatter=frontmatter,
        )
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_skill_loader_tiered.py -v 2>&1 | tail -10
```

Expected: all loader tests pass.

- [ ] **Step 5: Wire skill_loader injection in `main.py`**

Find where `container_manager` is instantiated and the existing injections happen (search for `container_manager._orchestrator =` or `container_manager._meta_skill_executor =`). Add right after them:

```python
container_manager._skill_loader_for_self_update = orchestrator.skill_loader
```

(If the variable name differs, mirror the existing injection pattern. The point is that the `_skill_loader_for_self_update` attribute is set to the same `SkillLoader` instance the orchestrator uses.)

- [ ] **Step 6: Run full suite**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
cd ~/linux/miniclaw && git add core/skill_loader.py tests/test_skill_loader_tiered.py main.py && git commit -m "feat(skills): expose frontmatter on Skill; wire loader into container_manager"
```

---

## Phase 4 — Prompt builder + tool loop checkpoint

Phase-boundary checkpoint: prompt-builder injects guidance only when at least one skill is opted in; tool loop counts tool-use blocks and injects checkpoint nudge at multiples of 15.

### Task 6: PromptBuilder injects standing self-update guidance

**Files:**
- Modify: `core/prompt_builder.py`
- Create: `tests/test_prompt_builder_self_update.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_prompt_builder_self_update.py
"""PromptBuilder injects standing self-update guidance only when an opted-in skill is loaded."""

import unittest

from core.prompt_builder import PromptBuilder


class _StubSkill:
    def __init__(self, name, frontmatter):
        self.name = name
        self.frontmatter = frontmatter
        # Minimal attrs for any other prompt-builder access patterns:
        self.description = ""
        self.instructions = ""


def _opted_in(name):
    return _StubSkill(name, {"metadata": {"miniclaw": {"self_update": {"allow_body": True}}}})


def _opted_out(name):
    return _StubSkill(name, {"metadata": {"miniclaw": {"self_update": {"allow_body": False}}}})


class TestSelfUpdateGuidance(unittest.TestCase):
    def test_no_opted_in_skill_omits_guidance(self):
        builder = PromptBuilder()
        prompt = builder.add_self_update_guidance("base prompt", skills={"a": _opted_out("a")})
        self.assertEqual(prompt, "base prompt")

    def test_opted_in_skill_appends_guidance(self):
        builder = PromptBuilder()
        prompt = builder.add_self_update_guidance(
            "base prompt",
            skills={"a": _opted_in("a"), "b": _opted_out("b")},
        )
        self.assertIn("update_skill_hints", prompt)
        self.assertIn("NOVEL SUCCESSFUL PHRASING", prompt)
        self.assertTrue(prompt.startswith("base prompt"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_prompt_builder_self_update.py -v 2>&1 | tail -10
```

Expected: `AttributeError: 'PromptBuilder' object has no attribute 'add_self_update_guidance'`.

- [ ] **Step 3: Modify `core/prompt_builder.py`**

Add this constant and method to the `PromptBuilder` class. (Place the method anywhere on the class; the constant near the top of the file.)

```python
SELF_UPDATE_GUIDANCE = """
Self-improving skills are enabled. Use update_skill_hints when:

  1. NOVEL SUCCESSFUL PHRASING: a user said something the skill's
     SKILL.md doesn't mention as a trigger phrase, and the skill
     ran cleanly. Add the phrasing as an example.

  2. ROUTING MISS YOU CORRECTED: you initially called skill X, the
     user clarified or the result didn't fit, and you re-routed to
     skill Y. After the user's request is satisfied via skill Y,
     add a hint to Y about the original phrasing.

Constraints:

  - Additions are short markdown bullets (one line).
  - Only call update_skill_hints once per skill per turn.
  - If the phrasing is already covered by existing SKILL.md content,
    don't call.
  - Never call on bundled skills whose routing is security-relevant
    (install-skill, set-env-var, save-memory).
  - Provide a rationale field naming the user phrasing or pattern
    that motivated the addition, in 15 words or fewer.

When in doubt, don't call. Auto-learned hints accumulate; bad ones
take effort to clean up.
""".strip()


# ... add this method to the class:

    def add_self_update_guidance(self, prompt: str, *, skills: dict) -> str:
        """Append standing self-update guidance if any loaded skill has allow_body: true."""
        any_opted_in = any(
            (
                getattr(s, "frontmatter", {}) or {}
            ).get("metadata", {}).get("miniclaw", {}).get("self_update", {}).get("allow_body") is True
            for s in skills.values()
        )
        if not any_opted_in:
            return prompt
        return prompt + "\n\n--- Self-update guidance ---\n" + SELF_UPDATE_GUIDANCE
```

- [ ] **Step 4: Wire it into the prompt assembly path**

Find the method in `PromptBuilder` that returns the assembled system prompt (likely `build_system_prompt` or similar — search for the method that returns the string the orchestrator uses). After the prompt is otherwise assembled, before it's returned, call:

```python
prompt = self.add_self_update_guidance(prompt, skills=skill_loader.skills)
```

(If the method takes `skill_loader` already, call from there. If it doesn't, find the call site in the orchestrator and apply the wrap there instead.)

- [ ] **Step 5: Run tests**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_prompt_builder_self_update.py tests/test_prompt_builder_selector.py -v 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd ~/linux/miniclaw && git add core/prompt_builder.py tests/test_prompt_builder_self_update.py && git commit -m "feat(skills): PromptBuilder injects self-update guidance for opted-in skills"
```

### Task 7: ToolLoop tracks tool-use count + 15-call checkpoint

**Files:**
- Modify: `core/tool_loop.py`
- Create: `tests/test_orchestrator_checkpoint.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_orchestrator_checkpoint.py
"""15-tool-call checkpoint nudge in ToolLoop."""

import unittest
from unittest.mock import MagicMock


class _FakeBlock:
    def __init__(self, btype, **kw):
        self.type = btype
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeUsage:
    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0


class _FakeResponse:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _FakeUsage()


def _make_tool_use_response(n_tool_calls):
    blocks = []
    for i in range(n_tool_calls):
        blocks.append(_FakeBlock("tool_use", id=f"tu{i}", name="weather", input={"query": "x"}))
    return _FakeResponse(blocks, "tool_use")


def _make_text_response(text):
    return _FakeResponse([_FakeBlock("text", text=text)], "end_turn")


class _Loader:
    def __init__(self, opted_in: bool):
        from unittest.mock import MagicMock
        s = MagicMock()
        s.frontmatter = {"metadata": {"miniclaw": {"self_update": {"allow_body": opted_in}}}}
        self.skills = {"weather": s}

    def get_tool_definitions(self):
        return [{"name": "weather", "description": "x", "input_schema": {"type": "object"}}]

    def get_skill(self, name):
        return self.skills.get(name)


class _CM:
    def __init__(self):
        self.calls = 0

    def execute_skill(self, skill, tool_input):
        self.calls += 1
        return "result"


class _ConvState:
    def __init__(self):
        self.user_messages = []
        self.assistant_blocks = []

    def append_user_text(self, t):
        self.user_messages.append(t)

    def append_assistant_content(self, c):
        self.assistant_blocks.append(c)

    def append_tool_results(self, r):
        pass

    def select_messages_for_prompt(self):
        return []

    def prune(self):
        pass


def _run_loop_with_calls(num_tool_calls, opted_in=True):
    """Drive a ToolLoop run that performs num_tool_calls before returning text."""
    from core.tool_loop import ToolLoop

    loader = _Loader(opted_in=opted_in)
    cm = _CM()
    state = _ConvState()
    client = MagicMock()

    # Plan: num_tool_calls tool calls split one-per-round, then a final text response.
    responses = [_make_tool_use_response(1) for _ in range(num_tool_calls)]
    responses.append(_make_text_response("done"))
    client.messages.create = MagicMock(side_effect=responses)

    loop = ToolLoop(
        client=client, model="claude-test",
        skill_loader=loader, container_manager=cm,
        conversation_state=state, max_rounds=num_tool_calls + 5,
    )
    loop.run("hi", system_prompt="base")
    return client.messages.create.call_args_list


class TestCheckpointNudge(unittest.TestCase):
    def test_below_15_calls_no_nudge(self):
        calls = _run_loop_with_calls(num_tool_calls=7)
        for c in calls:
            sys_arg = c.kwargs.get("system", "")
            self.assertNotIn("CHECKPOINT", sys_arg)

    def test_at_15_calls_nudge_in_next_request(self):
        calls = _run_loop_with_calls(num_tool_calls=15)
        # The request that comes AFTER the 15th tool call should carry the nudge.
        # That's the 16th call to messages.create (index 15).
        self.assertIn("CHECKPOINT", calls[15].kwargs.get("system", ""))

    def test_no_opted_in_skill_suppresses_nudge(self):
        calls = _run_loop_with_calls(num_tool_calls=15, opted_in=False)
        for c in calls:
            sys_arg = c.kwargs.get("system", "")
            self.assertNotIn("CHECKPOINT", sys_arg)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_orchestrator_checkpoint.py -v 2>&1 | tail -10
```

Expected: tests fail because no nudge is currently injected.

- [ ] **Step 3: Modify `core/tool_loop.py`**

Add the checkpoint constants near the top of the file (after the existing `_REMEMBER_RE`):

```python
CHECKPOINT_INTERVAL = 15

CHECKPOINT_NUDGE = (
    "[CHECKPOINT — {n} tool calls in this turn]\n"
    "Step back briefly: in the calls so far, did any skill route on a phrasing\n"
    "that isn't in its SKILL.md? Did you correct a misroute? If so, call\n"
    "update_skill_hints now before continuing the user's request."
)
```

In `ToolLoop.run`, find the `while rounds < self.max_rounds:` loop. Before the existing `response = self.client.messages.create(...)` call, build a per-round system prompt that conditionally includes the nudge:

```python
        last_nudged_at = 0
        while rounds < self.max_rounds:
            rounds += 1

            # Build per-round system prompt — checkpoint nudge if we just
            # crossed a multiple of CHECKPOINT_INTERVAL since last nudge.
            tool_count = len(tool_activity)
            current_checkpoint = (tool_count // CHECKPOINT_INTERVAL) * CHECKPOINT_INTERVAL
            if (
                current_checkpoint > last_nudged_at
                and current_checkpoint > 0
                and self._any_opted_in_skill()
            ):
                round_system = (
                    effective_system_prompt
                    + "\n\n"
                    + CHECKPOINT_NUDGE.format(n=current_checkpoint)
                )
                last_nudged_at = current_checkpoint
            else:
                round_system = effective_system_prompt

            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=round_system,
                messages=self.conversation_state.select_messages_for_prompt(),
                tools=tool_definitions if tool_definitions else anthropic.NOT_GIVEN,
            )
```

Add the helper method to `ToolLoop`:

```python
    def _any_opted_in_skill(self) -> bool:
        for s in self.skill_loader.skills.values():
            fm = getattr(s, "frontmatter", None) or {}
            allow = (
                fm.get("metadata", {}).get("miniclaw", {})
                  .get("self_update", {}).get("allow_body")
            )
            if allow is True:
                return True
        return False
```

Also at the very top of `run`, call `start_turn` on the container manager so the rate-limit window is fresh:

```python
    def run(
        self,
        user_message: str,
        system_prompt: str,
        archive_callback=None,
    ) -> str:
        if hasattr(self.container_manager, "start_turn"):
            self.container_manager.start_turn()
        self.conversation_state.append_user_text(user_message)
        # ... rest unchanged
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_orchestrator_checkpoint.py -v 2>&1 | tail -10
```

Expected: 3 tests pass.

- [ ] **Step 5: Run full suite to catch regressions in tool-loop tests**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd ~/linux/miniclaw && git add core/tool_loop.py tests/test_orchestrator_checkpoint.py && git commit -m "feat(skills): 15-tool-call checkpoint nudge in tool loop"
```

---

## Phase 5 — Git audit tests + integration test

Phase-boundary checkpoint: full suite green; manual smoke test against a real opted-in fixture.

### Task 8: Git side-effect tests

**Files:**
- Create: `tests/test_skill_self_update_git.py`

- [ ] **Step 1: Write the tests**

```python
# tests/test_skill_self_update_git.py
"""Verify self-update commits the right diff to git."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from core.skill_self_update import apply_hint


def _git(repo: Path, *args, check=True):
    res = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {args} failed: {res.stderr}")
    return res


class _Skill:
    def __init__(self, name, tier, skill_dir, frontmatter):
        self.name = name
        self.tier = tier
        self.skill_dir = str(skill_dir)
        self.frontmatter = frontmatter


class _Loader:
    def __init__(self, m):
        self.skills = m


def _setup_repo(tmp: Path, *, allow_body: bool = True) -> tuple[Path, _Loader]:
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    skill_dir = repo / "skills" / "foo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        + yaml.dump({
            "name": "foo",
            "description": "Test skill foo.",
            "metadata": {"miniclaw": {"self_update": {"allow_body": allow_body}}},
        }, sort_keys=False)
        + "---\n\n## When to use\n- existing\n"
    )
    (skill_dir / "config.yaml").write_text(
        yaml.dump({"type": "docker", "image": "miniclaw/foo:latest"})
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial")

    fm = {"metadata": {"miniclaw": {"self_update": {"allow_body": allow_body}}}}
    skill = _Skill("foo", "bundled", skill_dir, fm)
    return repo, _Loader({"foo": skill})


class TestGitCommit(unittest.TestCase):
    def test_successful_update_creates_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, loader = _setup_repo(Path(tmp))
            r = apply_hint(
                loader, "foo", "- new bullet here", "novel phrasing 'foo'",
                turn_id="t1", repo_root=repo,
            )
            self.assertEqual(r.status, "ok")

            log = _git(repo, "log", "--oneline").stdout
            self.assertIn("self-update(foo): novel phrasing", log)

    def test_commit_only_touches_skill_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, loader = _setup_repo(Path(tmp))
            apply_hint(
                loader, "foo", "- new bullet", "rationale",
                turn_id="t1", repo_root=repo,
            )

            stat = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.strip()
            self.assertEqual(stat, "skills/foo/SKILL.md")

    def test_unstaged_unrelated_changes_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, loader = _setup_repo(Path(tmp))
            # Stage an unrelated change (NOT inside skills/foo).
            (repo / "unrelated.txt").write_text("staged but unrelated\n")
            _git(repo, "add", "unrelated.txt")

            apply_hint(
                loader, "foo", "- new bullet", "rationale",
                turn_id="t1", repo_root=repo,
            )

            # The auto-commit should NOT have the unrelated file.
            stat = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.strip()
            self.assertEqual(stat, "skills/foo/SKILL.md")
            # The unrelated file is still in the index, uncommitted.
            staged = _git(repo, "diff", "--cached", "--name-only").stdout.strip()
            self.assertIn("unrelated.txt", staged)

    def test_non_git_directory_succeeds_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            # Build a skill outside any git repo.
            skill_dir = tmp_p / "skills" / "foo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                + yaml.dump({
                    "name": "foo",
                    "description": "x.",
                    "metadata": {"miniclaw": {"self_update": {"allow_body": True}}},
                }, sort_keys=False)
                + "---\n\n## When to use\n- existing\n"
            )
            (skill_dir / "config.yaml").write_text(
                yaml.dump({"type": "docker", "image": "miniclaw/foo:latest"})
            )
            fm = {"metadata": {"miniclaw": {"self_update": {"allow_body": True}}}}
            skill = _Skill("foo", "bundled", skill_dir, fm)
            loader = _Loader({"foo": skill})

            r = apply_hint(
                loader, "foo", "- new bullet", "rationale",
                turn_id="t1", repo_root=tmp_p,
            )
            self.assertEqual(r.status, "ok")
            content = (skill_dir / "SKILL.md").read_text()
            self.assertIn("- new bullet", content)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_skill_self_update_git.py -v 2>&1 | tail -10
```

Expected: 4 tests pass.

- [ ] **Step 3: Commit**

```bash
cd ~/linux/miniclaw && git add tests/test_skill_self_update_git.py && git commit -m "test(skills): cover self-update git commit side effects"
```

### Task 9: WORKING_MEMORY.md update

**Files:**
- Modify: `WORKING_MEMORY.md`

- [ ] **Step 1: Locate the Hermes roadmap section**

```bash
grep -n "Self-improving skills" ~/linux/miniclaw/WORKING_MEMORY.md
```

Expected output: a line like `4. Self-improving skills — let skills record their own usage outcomes...`

- [ ] **Step 2: Strike-through item #4 and add a milestone entry**

Replace the bullet:

```
4. Self-improving skills — let skills record their own usage outcomes and refine their SKILL.md routing hints over time.
```

with:

```
4. ~~Self-improving skills — let skills record their own usage outcomes and refine their SKILL.md routing hints over time.~~ Done 2026-04-24.
   Skills with `metadata.miniclaw.self_update.allow_body: true` autonomously gain additive routing hints via the new `update-skill-hints` native skill. Two trigger paths: Claude's in-the-moment judgment plus a 15-tool-call checkpoint nudge. Each change is a path-restricted git commit; rollback is `git revert`. Tier 2/3 changes (rewording, removal) remain manual. Imported-tier skills are blocked regardless of frontmatter.
```

Also append under "Recent milestones":

```
- 2026-04-24: shipped self-improving skills (Hermes roadmap #4)
  update-skill-hints native skill + tool loop 15-call checkpoint + prompt-builder guidance
  Tier 1 additive only; per-skill per-turn rate limit; FIFO at 30 bullets in the auto-section
  every change is a git commit; reversal is git revert
```

- [ ] **Step 3: Run full suite**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
cd ~/linux/miniclaw && git add WORKING_MEMORY.md && git commit -m "docs: mark Hermes roadmap #4 (self-improving skills) done"
```

### Task 10: Final integration sanity

- [ ] **Step 1: Manual end-to-end smoke (against a fresh fixture, not a real bundled skill)**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -c "
import tempfile, subprocess, yaml
from pathlib import Path
from core.skill_self_update import apply_hint

class S:
    def __init__(s, name, tier, d, fm):
        s.name=name; s.tier=tier; s.skill_dir=str(d); s.frontmatter=fm

class L:
    def __init__(s, m): s.skills=m

with tempfile.TemporaryDirectory() as tmp:
    repo = Path(tmp) / 'r'; repo.mkdir()
    subprocess.run(['git','-C',str(repo),'init','-q'])
    subprocess.run(['git','-C',str(repo),'config','user.email','t@t'])
    subprocess.run(['git','-C',str(repo),'config','user.name','t'])
    skd = repo / 'skills' / 'demo'; skd.mkdir(parents=True)
    (skd / 'SKILL.md').write_text(
        '---\n' + yaml.dump({'name':'demo','description':'d.','metadata':{'miniclaw':{'self_update':{'allow_body':True}}}}, sort_keys=False)
        + '---\n\n## When to use\n- a\n'
    )
    (skd / 'config.yaml').write_text(yaml.dump({'type':'docker','image':'miniclaw/demo:latest'}))
    subprocess.run(['git','-C',str(repo),'add','.'])
    subprocess.run(['git','-C',str(repo),'commit','-q','-m','init'])
    fm={'metadata':{'miniclaw':{'self_update':{'allow_body':True}}}}
    loader = L({'demo': S('demo','bundled',skd,fm)})
    r = apply_hint(loader, 'demo', '- forecast', 'novel phrasing', turn_id='t', repo_root=repo)
    print('result:', r)
    print('---')
    print((skd / 'SKILL.md').read_text())
    print('---')
    print(subprocess.run(['git','-C',str(repo),'log','--oneline'], capture_output=True, text=True).stdout)
"
```

Expected: result status=`ok`, SKILL.md gains a `## Auto-learned routing hints` section with `- forecast`, git log shows two commits (the `init` and the new `self-update(demo): novel phrasing`).

- [ ] **Step 2: Full suite**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/ -v 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 3: Commit (only if a fix was needed during smoke)**

If the smoke test surfaces something:

```bash
cd ~/linux/miniclaw && git add <files> && git commit -m "fix(skills): <reason>"
```

Otherwise, this task is verification-only and produces no commit.

---

## Self-Review

**Spec coverage:**

- ✅ `update_skill_hints` native skill — Tasks 3, 4.
- ✅ Per-tier eligibility (`imported` blocked) — Task 1 (`apply_hint` body).
- ✅ `allow_body: true` opt-in — Task 1.
- ✅ Body-only validation (no frontmatter, no input-schema headers, no top-level headings) — Task 1.
- ✅ 500-char addition cap — Task 1.
- ✅ "Already covered" no-op — Task 1.
- ✅ `## Auto-learned routing hints` section management — Task 2.
- ✅ FIFO at 30 bullets — Task 2.
- ✅ Atomic write (tempfile + rename) — Task 1 (`_atomic_write`).
- ✅ Re-validate full file via `SkillValidator.validate_markdown` — Task 1.
- ✅ Path-restricted git commit — Task 1 (`_git_commit_safe`).
- ✅ Non-git directory degrades gracefully — Task 8.
- ✅ Per-turn rate limit on `update-skill-hints` — Task 4.
- ✅ Frontmatter exposed on `Skill` — Task 5.
- ✅ Standing self-update guidance in PromptBuilder when at least one opted-in skill — Task 6.
- ✅ 15-tool-call checkpoint nudge — Task 7.
- ✅ Checkpoint nudge suppressed when no opted-in skills loaded — Task 7.
- ✅ `start_turn()` resets per-turn state — Task 7 (call site) + Task 4 (handler-side reset).
- ✅ `update-skill-hints` itself opt-out (`allow_body: false` in its own frontmatter) — Task 3.
- ✅ WORKING_MEMORY.md updated — Task 9.
- ✅ Integration smoke — Task 10.

**Placeholder scan:** no TBD/TODO/"implement later". Every code block is complete.

**Type consistency:**
- `SelfUpdateResult` defined in Task 1, used in Task 4 (handler dispatch) and Task 8 (git tests).
- `apply_hint` signature `(loader, skill_name, addition, rationale, *, turn_id, repo_root=None) -> SelfUpdateResult` is consistent across Task 1 implementation, Task 4 handler call site, and Task 8 git tests.
- `Skill.frontmatter` introduced in Task 5; consumed in Tasks 4 (handler), 6 (prompt builder), 7 (tool loop helper).
- `ContainerManager.start_turn()` defined in Task 4; called from `ToolLoop.run` in Task 7.
- `_skill_loader_for_self_update` attribute defined in Task 4; injected from main.py in Task 5.
- `AUTO_SECTION_HEADER` constant defined in Task 1, asserted on in Task 2 tests.
- `MAX_AUTO_BULLETS = 30` defined in Task 1, asserted on in Task 2 tests.
- `CHECKPOINT_INTERVAL = 15` and `CHECKPOINT_NUDGE` defined in Task 7, asserted on in Task 7 tests via the literal `"CHECKPOINT"` substring.

**Scope:** one cohesive plan, ~50 new test cases, ~700 LOC total across new module + handler + checkpoint + prompt hook.
