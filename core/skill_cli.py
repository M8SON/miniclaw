"""
`miniclaw skill` CLI — subcommand dispatch for skill management.

Subcommands:
  install <url|path> [--tier imported|authored]   install a skill
  uninstall <name>                                 remove an installed skill
  list [--tier bundled|authored|imported]          list loaded skills
  validate <path>                                  dry-run validation; no install
  dev <path>                                       dev-mode symlink (bypasses clamps)

Dispatched from main.py when the first positional is "skill".
"""

import argparse
import datetime
import logging
import os
import shutil
import sys
from pathlib import Path

from core.install_pipeline import (
    DockerBuilder,
    InstallDecision,
    InstallPipeline,
)
from core.skill_loader import SkillLoader
from core.skill_policy import TIER_AUTHORED, TIER_IMPORTED


logger = logging.getLogger(__name__)


class TextConfirmer:
    """stdin/stdout confirmer with y/N prompts for each gate."""
    def confirm_gate(self, gate: str, summary: str) -> bool:
        print()
        print(summary)
        print()
        prompt = f"Confirm '{gate}'? [y/N] "
        try:
            reply = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return reply in ("y", "yes")


class OrchestratorReloader:
    """File-based reload signal picked up by the running orchestrator."""
    def __init__(self):
        self.flag_path = Path.home() / ".miniclaw" / "reload.flag"

    def reload(self) -> None:
        self.flag_path.parent.mkdir(parents=True, exist_ok=True)
        self.flag_path.write_text(datetime.datetime.now().isoformat())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="miniclaw skill", description="Manage MiniClaw skills")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_install = sub.add_parser("install", help="install a skill")
    p_install.add_argument("source", help="URL or filesystem path to the skill")
    p_install.add_argument(
        "--tier",
        choices=[TIER_AUTHORED, TIER_IMPORTED],
        default=TIER_IMPORTED,
        help="install tier (default: imported)",
    )

    p_uninstall = sub.add_parser("uninstall", help="remove an installed skill")
    p_uninstall.add_argument("name", help="skill name (kebab-case)")

    p_list = sub.add_parser("list", help="list loaded skills")
    p_list.add_argument(
        "--tier",
        choices=["bundled", TIER_AUTHORED, TIER_IMPORTED, "dev"],
        help="filter by tier",
    )

    p_validate = sub.add_parser("validate", help="dry-run validation (no install)")
    p_validate.add_argument("path", help="skill directory to validate")

    p_dev = sub.add_parser("dev", help="symlink a skill into dev mode")
    p_dev.add_argument("path", help="skill directory on disk")

    return parser


def dispatch(args: argparse.Namespace) -> int:
    if args.subcommand == "install":
        return _cmd_install(args)
    if args.subcommand == "uninstall":
        return _cmd_uninstall(args)
    if args.subcommand == "list":
        return _cmd_list(args)
    if args.subcommand == "validate":
        return _cmd_validate(args)
    if args.subcommand == "dev":
        return _cmd_dev(args)
    return 1


def _install_root(tier: str) -> Path:
    return Path.home() / ".miniclaw" / tier


def _cmd_install(args) -> int:
    install_root = _install_root(args.tier)
    install_root.mkdir(parents=True, exist_ok=True)
    pipeline = InstallPipeline(
        confirmer=TextConfirmer(),
        builder=DockerBuilder(),
        reloader=OrchestratorReloader(),
        install_root=install_root,
    )
    source = args.source
    if source.startswith("http://") or source.startswith("https://"):
        decision = pipeline.install_from_url(source, tier=args.tier)
    else:
        decision = pipeline.install_from_path(Path(source), tier=args.tier)

    if decision == InstallDecision.INSTALLED:
        print(f"Skill installed at tier {args.tier}.")
        return 0
    if decision == InstallDecision.CANCELLED:
        print("Install cancelled.")
        return 1
    print("Install failed.", file=sys.stderr)
    return 1


def _cmd_uninstall(args) -> int:
    for tier in (TIER_AUTHORED, TIER_IMPORTED):
        candidate = _install_root(tier) / args.name
        if candidate.exists():
            reply = input(f"Remove {candidate}? [y/N] ").strip().lower()
            if reply not in ("y", "yes"):
                return 1
            shutil.rmtree(candidate)
            print(f"Removed {candidate}.")
            return 0
    print(f"Skill {args.name!r} not found in authored or imported.", file=sys.stderr)
    return 1


def _cmd_list(args) -> int:
    loader = SkillLoader()
    loader.load_all()
    for skill in sorted(loader.skills.values(), key=lambda s: s.name):
        if args.tier and skill.tier != args.tier:
            continue
        print(f"{skill.name:30s} {skill.tier:10s}  {skill.description}")
    return 0


def _cmd_validate(args) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"Path not found: {path}", file=sys.stderr)
        return 1

    import yaml
    from core.dockerfile_validator import DockerfileValidationError, validate as vd
    from core.skill_validator import SkillValidator

    v = SkillValidator()
    skill_md = path / "SKILL.md"
    if not skill_md.exists():
        print(f"No SKILL.md in {path}", file=sys.stderr)
        return 1
    try:
        frontmatter, _ = v.validate_markdown(skill_md.read_text(encoding="utf-8"), path)
        cfg_path = path / "config.yaml"
        if cfg_path.exists():
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            v.validate_execution_config(raw, tier=TIER_IMPORTED, skill_name=frontmatter["name"])
        df = path / "scripts" / "Dockerfile"
        if df.exists():
            vd(df, tier=TIER_IMPORTED)
    except (ValueError, DockerfileValidationError) as e:
        print(f"Validation failed: {e}", file=sys.stderr)
        return 1
    print("Validation passed at tier=imported.")
    return 0


def _cmd_dev(args) -> int:
    path = Path(args.path).resolve()
    if not path.exists() or not (path / "SKILL.md").exists():
        print(f"Not a valid skill directory: {path}", file=sys.stderr)
        return 1

    import yaml
    skill_md_text = (path / "SKILL.md").read_text(encoding="utf-8")
    parts = skill_md_text.split("---")
    if len(parts) < 3:
        print("SKILL.md has no frontmatter", file=sys.stderr)
        return 1
    fm = yaml.safe_load(parts[1]) or {}
    name = fm.get("name")
    if not name:
        print("Skill has no name in frontmatter", file=sys.stderr)
        return 1

    dev_target = _install_root(TIER_IMPORTED) / name
    dev_target.parent.mkdir(parents=True, exist_ok=True)
    if dev_target.exists() or dev_target.is_symlink():
        if dev_target.is_symlink():
            dev_target.unlink()
        else:
            shutil.rmtree(dev_target)
    os.symlink(path, dev_target)
    print(f"Dev mode: {dev_target} -> {path}")
    print("WARNING: security validations bypassed while this symlink exists.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
