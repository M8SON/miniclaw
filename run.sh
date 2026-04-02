#!/bin/bash
# run.sh - Start MiniClaw, building anything missing first.
#
# Usage:
#   ./run.sh                       # text mode (default)
#   ./run.sh --voice               # voice mode
#   ./run.sh --list                # list skills and exit
#   ./run.sh --install-system-deps # install Docker + espeak-ng on Debian/Ubuntu

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

CURRENT_USER="$(id -un)"
DOCKER_READY=false
DOCKER_USE_SG=false

INSTALL_SYSTEM_DEPS=false
POSITIONAL_ARGS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --install-system-deps)
            INSTALL_SYSTEM_DEPS=true
            shift
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

set -- "${POSITIONAL_ARGS[@]}"

if [ "$#" -eq 0 ]; then
    ARGS=(--text)
elif [ "$1" = "--voice" ] && [ "$#" -eq 1 ]; then
    ARGS=()
else
    ARGS=("$@")
fi

venv_usable() {
    [ -f ".venv/bin/activate" ] || return 1
    [ -x ".venv/bin/python3" ] || return 1
    [ -f ".venv/pyvenv.cfg" ] || return 1
}

create_venv() {
    echo "  Creating virtual environment..."
    if ! python3 -m venv .venv; then
        fail "failed to create .venv. On Debian/Ubuntu, install python3-venv (for example: sudo apt install python3.12-venv) and try again"
    fi
}

docker_group_has_user() {
    getent group docker | awk -F: '{print $4}' | tr ',' '\n' | grep -Fxq "$CURRENT_USER"
}

ensure_docker_group_membership() {
    if ! getent group docker >/dev/null 2>&1; then
        fail "docker group not found after installation"
    fi

    if docker_group_has_user; then
        ok "user '$CURRENT_USER' is in docker group"
        return
    fi

    echo "  Adding '$CURRENT_USER' to docker group..."
    sudo usermod -aG docker "$CURRENT_USER"
    ok "added '$CURRENT_USER' to docker group"
}

docker_run() {
    if [ "$DOCKER_USE_SG" = true ]; then
        local quoted=()
        local arg
        for arg in "$@"; do
            quoted+=("$(printf '%q' "$arg")")
        done
        sg docker -c "docker ${quoted[*]}"
    else
        docker "$@"
    fi
}

launch_miniclaw() {
    if [ "$DOCKER_USE_SG" = true ]; then
        local quoted=(".venv/bin/python3" "main.py")
        local arg
        for arg in "${ARGS[@]}"; do
            quoted+=("$(printf '%q' "$arg")")
        done
        exec sg docker -c "cd $(printf '%q' "$SCRIPT_DIR") && ${quoted[*]}"
    fi

    exec python3 main.py "${ARGS[@]}"
}

install_system_deps() {
    if [ ! -f /etc/os-release ]; then
        fail "--install-system-deps is only supported on Debian/Ubuntu systems"
    fi

    . /etc/os-release
    if [[ "${ID:-}" != "ubuntu" && "${ID:-}" != "debian" && "${ID_LIKE:-}" != *debian* ]]; then
        fail "--install-system-deps is only supported on Debian/Ubuntu systems"
    fi

    if ! command -v sudo &>/dev/null; then
        fail "sudo not found — install Docker and espeak-ng manually"
    fi

    echo "  Installing system packages (docker.io, espeak-ng)..."
    sudo apt-get update
    sudo apt-get install -y docker.io espeak-ng
    sudo systemctl enable --now docker
    ensure_docker_group_membership
    ok "system dependencies installed"
}

echo -e "\n${BOLD}MiniClaw${NC}"
echo "──────────────────────────────"

if [ "$INSTALL_SYSTEM_DEPS" = true ]; then
    install_system_deps
fi

# ── Python ──────────────────────────────────────────────────────────────────

if ! command -v python3 &>/dev/null; then
    fail "python3 not found"
fi
ok "python3 $(python3 --version 2>&1 | cut -d' ' -f2)"

# ── Virtual environment ──────────────────────────────────────────────────────

if [ ! -d ".venv" ]; then
    create_venv
elif ! venv_usable; then
    warn ".venv exists but is incomplete or broken — recreating it"
    if ! /usr/bin/rm -rf -- .venv; then
        fail "failed to remove broken .venv"
    fi
    create_venv
fi

source .venv/bin/activate
ok "venv active"

# ── Dependencies ─────────────────────────────────────────────────────────────

if ! python3 -c "import anthropic, dotenv, yaml" &>/dev/null 2>&1; then
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
    warn "espeak-ng not found — TTS will fail. Install with: sudo apt install espeak-ng or ./run.sh --install-system-deps"
fi

# ── Claude Code CLI (required for voice skill installation) ──────────────────

if command -v claude &>/dev/null; then
    ok "claude $(claude --version 2>/dev/null | head -1)"
else
    warn "claude CLI not found — voice skill installation unavailable. Install with: npm install -g @anthropic-ai/claude-code"
fi

# ── Environment ──────────────────────────────────────────────────────────────

if [ ! -f ".env" ]; then
    fail ".env not found — copy .env.example and fill in your API keys"
fi
ok ".env present"

# ── Docker ───────────────────────────────────────────────────────────────────

if ! command -v docker &>/dev/null; then
    warn "docker not found — Docker-backed skills will be unavailable. Install with: ./run.sh --install-system-deps"
else
    if docker info &>/dev/null 2>&1; then
        DOCKER_READY=true
        ok "docker available"
    elif command -v sg &>/dev/null && sg docker -c "docker info" &>/dev/null 2>&1; then
        DOCKER_READY=true
        DOCKER_USE_SG=true
        ok "docker available via refreshed docker-group shell"
        warn "current shell has stale docker permissions — using 'sg docker' for this run"
    else
        if getent group docker >/dev/null 2>&1 && ! docker_group_has_user; then
            warn "docker is installed but '$CURRENT_USER' is not in the docker group — run: sudo usermod -aG docker $CURRENT_USER"
        else
            warn "Docker daemon is not running or this session cannot access it — Docker-backed skills will be unavailable"
        fi
    fi
fi

# ── Skill containers ─────────────────────────────────────────────────────────
# Base image must be built first — skill containers depend on it

if [ "$DOCKER_READY" = true ]; then
    if docker_run image inspect miniclaw/base:latest &>/dev/null 2>&1; then
        ok "miniclaw/base:latest"
    else
        echo "  Building miniclaw/base:latest..."
        docker_run build -t miniclaw/base:latest containers/base/ -q
        ok "miniclaw/base:latest (built)"
    fi

    # Auto-discover all skill containers (any containers/<name>/Dockerfile except base)
    for dockerfile in containers/*/Dockerfile; do
        dir="$(dirname "$dockerfile")"
        name="$(basename "$dir")"
        [ "$name" = "base" ] && continue

        image="miniclaw/${name//_/-}:latest"

        if docker_run image inspect "$image" &>/dev/null 2>&1; then
            ok "$image"
        else
            echo "  Building $image..."
            docker_run build -t "$image" "$dir" -q
            ok "$image (built)"
        fi
    done
fi

# ── Launch ───────────────────────────────────────────────────────────────────

echo "──────────────────────────────"

launch_miniclaw
