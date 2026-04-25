"""Verify self-update commits the right diff to git."""

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
            (repo / "unrelated.txt").write_text("staged but unrelated\n")
            _git(repo, "add", "unrelated.txt")

            apply_hint(
                loader, "foo", "- new bullet", "rationale",
                turn_id="t1", repo_root=repo,
            )

            stat = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.strip()
            self.assertEqual(stat, "skills/foo/SKILL.md")
            staged = _git(repo, "diff", "--cached", "--name-only").stdout.strip()
            self.assertIn("unrelated.txt", staged)

    def test_non_git_directory_succeeds_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
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
