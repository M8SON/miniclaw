"""
Generic OpenClaw skill executor.

Receives SKILL_INPUT with {"skill_name": ..., "input": {...}}, mounts the
skill directory at /skill, and either:
  - Runs a detected entry point script (passing input via SKILL_INPUT + stdin)
  - Returns empty output for pure-instruction skills (no scripts), letting
    Claude handle the response from the system prompt instructions alone.

Entry point search order (first match wins):
  /skill/scripts/main.py
  /skill/scripts/run.sh
  /skill/scripts/main.sh
  /skill/scripts/index.js
  /skill/main.py
  /skill/run.sh
"""

import os
import sys
import json
import stat
import subprocess

ENTRY_POINTS = [
    ("/skill/scripts/main.py",  ["python3"]),
    ("/skill/scripts/run.sh",   ["bash"]),
    ("/skill/scripts/main.sh",  ["bash"]),
    ("/skill/scripts/index.js", ["node"]),
    ("/skill/main.py",          ["python3"]),
    ("/skill/run.sh",           ["bash"]),
]


def find_entry_point():
    for path, runner in ENTRY_POINTS:
        if os.path.isfile(path):
            return path, runner
    return None, None


def main():
    raw = os.environ.get("SKILL_INPUT", "") or sys.stdin.read()

    try:
        data = json.loads(raw)
        tool_input = data.get("input", data)
    except (json.JSONDecodeError, TypeError):
        tool_input = {"query": raw.strip()}

    entry, runner = find_entry_point()

    if entry is None:
        # Pure-instruction skill — no script to run.
        # Return empty so Claude uses the system prompt instructions.
        sys.exit(0)

    input_json = json.dumps(tool_input)

    result = subprocess.run(
        runner + [entry],
        input=input_json.encode(),
        capture_output=True,
        env={**os.environ, "SKILL_INPUT": input_json},
        timeout=25,
    )

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        print(f"Skill script error: {stderr}", file=sys.stderr)
        sys.exit(result.returncode)

    output = result.stdout.decode(errors="replace").strip()
    print(output)


if __name__ == "__main__":
    main()
