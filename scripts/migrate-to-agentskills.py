#!/usr/bin/env python3
"""
migrate-to-agentskills.py — one-shot migration of MiniClaw skills to the
agentskills.io-compliant single-directory layout.

Run from repo root:
    python3 scripts/migrate-to-agentskills.py

Per skill:
  1. Rename snake_case directory to kebab-case; force parent name = skill name
  2. Move containers/<old>/ into skills/<new>/scripts/
  3. Rewrite SKILL.md frontmatter:
     - set name: <kebab-case> (matches new dir)
     - move top-level requires: under metadata.miniclaw.requires
  4. Delete top-level containers/ skill entries (keep containers/base/)

Idempotent: running twice leaves things unchanged.

This script is deleted after the migration PR merges.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

import yaml


# Per-skill mapping: OLD_DIR -> NEW_DIR (kebab-case, also the new skill name).
RENAMES: dict[str, str] = {
    "dashboard":          "dashboard",
    "homebridge":         "homebridge",
    "install_skill":      "install-skill",
    "playwright_scraper": "playwright-scraper",
    "recall_session":     "recall-session",
    "save_memory":        "save-memory",
    "schedule":           "schedule",
    "set_env_var":        "set-env-var",
    "skill_tells_random": "skill-tells-random",
    "soundcloud":         "soundcloud",
    "weather":            "weather",
    "web_search":         "web-search",
}


FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def migrate_frontmatter(raw: str, new_name: str) -> str:
    m = FRONTMATTER_RE.match(raw)
    if not m:
        raise ValueError("SKILL.md missing frontmatter")
    fm = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)

    fm["name"] = new_name

    old_requires = fm.pop("requires", None)
    if old_requires is not None:
        metadata = fm.setdefault("metadata", {}) or {}
        miniclaw = metadata.setdefault("miniclaw", {}) or {}
        miniclaw["requires"] = old_requires
        metadata["miniclaw"] = miniclaw
        fm["metadata"] = metadata

    preferred_order = ["name", "description", "license", "compatibility", "metadata"]
    ordered = {k: fm[k] for k in preferred_order if k in fm}
    for k, v in fm.items():
        if k not in ordered:
            ordered[k] = v

    new_fm = yaml.dump(ordered, sort_keys=False, default_flow_style=False)
    return f"---\n{new_fm}---\n{body}"


def migrate_skill(repo_root: Path, old_dir: str, new_name: str) -> None:
    old_skill_path = repo_root / "skills" / old_dir
    new_skill_path = repo_root / "skills" / new_name
    container_path = repo_root / "containers" / old_dir

    if not old_skill_path.exists() and new_skill_path.exists():
        return  # already migrated

    if not old_skill_path.exists():
        return  # nothing to migrate

    # 1. Rename directory
    if old_skill_path != new_skill_path:
        old_skill_path.rename(new_skill_path)

    # 2. Move containers/<old>/ into skills/<new>/scripts/
    if container_path.exists():
        scripts_dest = new_skill_path / "scripts"
        if scripts_dest.exists():
            for item in container_path.iterdir():
                target = scripts_dest / item.name
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                shutil.move(str(item), str(target))
            shutil.rmtree(container_path)
        else:
            shutil.move(str(container_path), str(scripts_dest))

    # 3. Rewrite SKILL.md frontmatter
    skill_md = new_skill_path / "SKILL.md"
    if skill_md.exists():
        skill_md.write_text(
            migrate_frontmatter(skill_md.read_text(encoding="utf-8"), new_name),
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", help="Path to repo root")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if not (repo_root / "skills").exists():
        print(f"error: {repo_root}/skills does not exist", file=sys.stderr)
        return 1

    for old_dir, new_name in RENAMES.items():
        migrate_skill(repo_root, old_dir, new_name)

    # 4. Remove migrated entries from containers/; leave containers/base/.
    containers_root = repo_root / "containers"
    if containers_root.exists():
        for old_dir in RENAMES:
            skill_container = containers_root / old_dir
            if skill_container.exists():
                shutil.rmtree(skill_container)
        # If nothing left at all (including base), remove the directory.
        if not any(containers_root.iterdir()):
            shutil.rmtree(containers_root)

    print("Migration complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
