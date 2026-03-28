#!/bin/bash
# run.sh - Start MiniClaw, building anything missing first.
#
# Usage:
#   ./run.sh              # text mode (default)
#   ./run.sh --voice      # voice mode
#   ./run.sh --list       # list skills and exit

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; exit 1; }

echo -e "\n${BOLD}MiniClaw${NC}"
echo "──────────────────────────────"

# ── Python ──────────────────────────────────────────────────────────────────

if ! command -v python3 &>/dev/null; then
    fail "python3 not found"
fi
ok "python3 $(python3 --version 2>&1 | cut -d' ' -f2)"

# ── Virtual environment ──────────────────────────────────────────────────────

if [ ! -d ".venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate
ok "venv active"

# ── Dependencies ─────────────────────────────────────────────────────────────

if ! python3 -c "import anthropic" &>/dev/null 2>&1; then
    echo "  Installing dependencies..."
    pip install -r requirements.txt -q
    ok "dependencies installed"
else
    ok "dependencies present"
fi

# ── espeak-ng (required by Kokoro TTS) ───────────────────────────────────────
# Kokoro downloads its own model automatically on first run (~80MB to ~/.cache/huggingface/).
# espeak-ng must be installed as a system package.

if command -v espeak-ng &>/dev/null; then
    ok "espeak-ng"
else
    warn "espeak-ng not found — TTS will fail. Install with: sudo apt install espeak-ng"
fi

# ── Environment ──────────────────────────────────────────────────────────────

if [ ! -f ".env" ]; then
    fail ".env not found — copy .env.example and fill in your API keys"
fi
ok ".env present"

# ── Docker ───────────────────────────────────────────────────────────────────

if ! command -v docker &>/dev/null; then
    fail "docker not found"
fi
if ! docker info &>/dev/null 2>&1; then
    fail "Docker daemon is not running"
fi
ok "docker available"

# ── Skill containers ─────────────────────────────────────────────────────────
# Base image must be built first — skill containers depend on it

if docker image inspect miniclaw/base:latest &>/dev/null 2>&1; then
    ok "miniclaw/base:latest"
else
    echo "  Building miniclaw/base:latest..."
    docker build -t miniclaw/base:latest containers/base/ -q
    ok "miniclaw/base:latest (built)"
fi

declare -A SKILL_CONTAINERS=(
    ["miniclaw/weather:latest"]="containers/weather"
    ["miniclaw/web-search:latest"]="containers/web_search"
    ["miniclaw/soundcloud:latest"]="containers/soundcloud"
    ["miniclaw/playwright-scraper:latest"]="containers/playwright_scraper"
)

for image in "${!SKILL_CONTAINERS[@]}"; do
    dir="${SKILL_CONTAINERS[$image]}"
    if docker image inspect "$image" &>/dev/null 2>&1; then
        ok "$image"
    else
        echo "  Building $image..."
        docker build -t "$image" "$dir" -q
        ok "$image (built)"
    fi
done

# ── Launch ───────────────────────────────────────────────────────────────────

echo "──────────────────────────────"

# Map --voice to no args (voice is the default mode), pass everything else through
if [ "$*" = "--voice" ]; then
    ARGS=""
else
    ARGS="${@:---text}"
fi

python3 main.py $ARGS
