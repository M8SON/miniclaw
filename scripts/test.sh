#!/bin/bash
# Standard MiniClaw test entry point.
#
# Usage:
#   ./scripts/test.sh                 # fast suite
#   ./scripts/test.sh --voice         # fast suite + scripted voice harness
#   ./scripts/test.sh --install       # fast suite + real install integration
#   ./scripts/test.sh --all           # all of the above

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

RUN_VOICE=false
RUN_INSTALL=false

while [ $# -gt 0 ]; do
    case "$1" in
        --voice)
            RUN_VOICE=true
            shift
            ;;
        --install)
            RUN_INSTALL=true
            shift
            ;;
        --all)
            RUN_VOICE=true
            RUN_INSTALL=true
            shift
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

if [ ! -x ".venv/bin/python" ]; then
    echo "error: .venv/bin/python not found. Run ./run.sh first." >&2
    exit 1
fi

echo "[test] unit tests"
.venv/bin/python -m unittest discover -s tests -v

echo "[test] launcher syntax"
bash -n run.sh

if [ "$RUN_VOICE" = true ]; then
    echo "[test] scripted voice harness"
    .venv/bin/python scripts/test_voice_mode_harness.py
fi

if [ "$RUN_INSTALL" = true ]; then
    if command -v sg >/dev/null 2>&1; then
        echo "[test] real install_skill integration"
        sg docker -c "cd '$REPO_ROOT' && .venv/bin/python scripts/test_install_skill_integration.py"
    else
        echo "error: sg command not found; cannot run Docker integration as docker group." >&2
        exit 1
    fi
fi

echo "[test] done"
