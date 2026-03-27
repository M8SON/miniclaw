#!/usr/bin/env python3
"""
port-skill.py — Scaffold a MiniClaw container for an OpenClaw skill.

Takes an OpenClaw skill directory (containing a SKILL.md) and generates
the config.yaml and container files needed to run it in MiniClaw.

Usage:
    python3 scripts/port-skill.py <path-to-openclaw-skill-dir>

Example:
    python3 scripts/port-skill.py ~/Downloads/some-openclaw-skill/
"""

import sys
import re
import shutil
import yaml
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

APP_PY_SKELETON = '''\
"""
{name} skill.
TODO: implement the skill logic below.

Input:  SKILL_INPUT env var (JSON) or stdin
Output: result printed to stdout
"""

import os
import sys
import json


def run(data: dict) -> str:
    # TODO: implement skill logic here
    # Access env vars with: os.environ.get("YOUR_API_KEY")
    query = data.get("query", "")
    return f"Result for: {{query}}"


def main():
    raw = os.environ.get("SKILL_INPUT", "") or sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {{"query": raw.strip()}}

    if not data:
        print("No input provided")
        sys.exit(1)

    print(run(data))


if __name__ == "__main__":
    main()
'''

APP_PY_WITH_SCRIPTS = '''\
"""
{name} skill — runs scripts/main.py with input passed via SKILL_INPUT.
"""

import os
import sys
import json
import subprocess


def main():
    raw = os.environ.get("SKILL_INPUT", "") or sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {{"query": raw.strip()}}

    input_json = json.dumps(data)

    result = subprocess.run(
        ["python3", "/app/scripts/main.py"],
        input=input_json.encode(),
        capture_output=True,
        env={{**os.environ, "SKILL_INPUT": input_json}},
        timeout=25,
    )

    if result.returncode != 0:
        print(result.stderr.decode(errors="replace").strip(), file=sys.stderr)
        sys.exit(result.returncode)

    print(result.stdout.decode(errors="replace").strip())


if __name__ == "__main__":
    main()
'''

DOCKERFILE_TEMPLATE = """\
FROM miniclaw/base:latest
# TODO: add any pip packages this skill needs
# RUN pip install --no-cache-dir <package>
COPY app.py /app/app.py
{scripts_line}WORKDIR /app
CMD ["python", "app.py"]
"""


def parse_frontmatter(text: str) -> dict:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not match:
        return {}
    try:
        return yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}


def slugify(name: str) -> str:
    """Convert name to a safe directory/image slug."""
    return re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_")


def image_name(slug: str) -> str:
    return slug.replace("_", "-")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    source_dir = Path(sys.argv[1]).resolve()
    skill_md_path = source_dir / "SKILL.md"

    if not skill_md_path.exists():
        print(f"Error: no SKILL.md found in {source_dir}")
        sys.exit(1)

    raw = skill_md_path.read_text(encoding="utf-8")
    fm = parse_frontmatter(raw)

    # Derive names
    raw_name = fm.get("name", source_dir.name)
    slug = slugify(raw_name)
    img = image_name(slug)

    # Requirements
    requires = fm.get("requires", {}) or {}
    # Also check OpenClaw metadata block for compatibility
    if not requires:
        requires = fm.get("metadata", {}).get("openclaw", {}).get("requires", {}) or {}
    env_vars = requires.get("env", [])

    # Destination paths
    skill_dest = REPO_ROOT / "skills" / slug
    container_dest = REPO_ROOT / "containers" / slug

    skill_dest.mkdir(parents=True, exist_ok=True)
    container_dest.mkdir(parents=True, exist_ok=True)

    # ── SKILL.md ──────────────────────────────────────────────────────────
    shutil.copy(skill_md_path, skill_dest / "SKILL.md")

    # ── config.yaml ───────────────────────────────────────────────────────
    config = {
        "type": "native",
        "image": f"miniclaw/{img}:latest",
        "env_passthrough": env_vars,
        "timeout_seconds": 30,
        "devices": [],
    }
    with open(skill_dest / "config.yaml", "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    # ── scripts/ (copy if present) ────────────────────────────────────────
    scripts_src = source_dir / "scripts"
    has_scripts = scripts_src.is_dir() and any(scripts_src.iterdir())

    if has_scripts:
        scripts_dest = container_dest / "scripts"
        if scripts_dest.exists():
            shutil.rmtree(scripts_dest)
        shutil.copytree(scripts_src, scripts_dest)

    # ── Dockerfile ────────────────────────────────────────────────────────
    scripts_line = "COPY scripts/ /app/scripts/\n" if has_scripts else ""
    dockerfile = DOCKERFILE_TEMPLATE.format(scripts_line=scripts_line)
    (container_dest / "Dockerfile").write_text(dockerfile)

    # ── app.py ────────────────────────────────────────────────────────────
    template = APP_PY_WITH_SCRIPTS if has_scripts else APP_PY_SKELETON
    (container_dest / "app.py").write_text(template.format(name=slug))

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\nPorted '{raw_name}' → MiniClaw skill '{slug}'\n")
    print(f"  skills/{slug}/")
    print(f"    SKILL.md      (copied)")
    print(f"    config.yaml   (generated)")
    print(f"  containers/{slug}/")
    print(f"    Dockerfile    (generated)")
    if has_scripts:
        print(f"    scripts/      (copied from source)")
        print(f"    app.py        (generated — runs scripts/main.py)")
    else:
        print(f"    app.py        (generated — TODO: implement logic)")

    print("\nNext steps:")
    step = 1
    if not has_scripts:
        print(f"  {step}. Implement the skill in containers/{slug}/app.py")
        step += 1
    if env_vars:
        print(f"  {step}. Add to your .env:")
        for v in env_vars:
            print(f"       {v}=your_value_here")
        step += 1
    print(f"  {step}. Add to run.sh SKILL_CONTAINERS:")
    print(f'       ["miniclaw/{img}:latest"]="containers/{slug}"')
    step += 1
    print(f"  {step}. Build and test:")
    print(f"       docker build -t miniclaw/{img}:latest containers/{slug}/")
    print(f"       ./run.sh --list")
    print()


if __name__ == "__main__":
    main()
