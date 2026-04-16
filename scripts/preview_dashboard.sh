#!/bin/bash
# preview_dashboard.sh - run the dashboard locally without going through MiniClaw.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PORT=7860
PANELS="news,weather,stocks,music"
WEATHER_LOCATION="${WEATHER_LOCATION:-New York,NY}"

usage() {
    cat <<EOF
Usage: ./scripts/preview_dashboard.sh [options]

Options:
  --port PORT           Local port to expose (default: 7860)
  --panels LIST         Comma-separated panels (default: news,weather,stocks,music)
  --location LOCATION   Weather location override (default: \$WEATHER_LOCATION or New York,NY)
  --help                Show this help

Examples:
  ./scripts/preview_dashboard.sh
  ./scripts/preview_dashboard.sh --panels news,weather --location "Burlington,VT"
  ./scripts/preview_dashboard.sh --port 9000
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --port)
            PORT="$2"
            shift 2
            ;;
        --panels)
            PANELS="$2"
            shift 2
            ;;
        --location)
            WEATHER_LOCATION="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage
            exit 1
            ;;
    esac
done

if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found" >&2
    exit 1
fi

DOCKER_USE_SG=false

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

if docker info >/dev/null 2>&1; then
    :
elif command -v sg >/dev/null 2>&1 && sg docker -c "docker info" >/dev/null 2>&1; then
    DOCKER_USE_SG=true
else
    echo "docker is installed but unavailable in this shell" >&2
    exit 1
fi

mkdir -p "$HOME/.miniclaw"

if ! docker_run image inspect miniclaw/base:latest >/dev/null 2>&1; then
    echo "Building miniclaw/base:latest..."
    docker_run build -t miniclaw/base:latest containers/base/
fi

if ! docker_run image inspect miniclaw/dashboard:latest >/dev/null 2>&1; then
    echo "Building miniclaw/dashboard:latest..."
    docker_run build -t miniclaw/dashboard:latest containers/dashboard/
fi

echo "Starting dashboard preview on http://localhost:$PORT"
echo "Panels: $PANELS"
echo "Weather location: $WEATHER_LOCATION"
echo "Press Ctrl+C to stop."

docker_run run --rm -it \
    -p "$PORT:7860" \
    -e "WEATHER_LOCATION=$WEATHER_LOCATION" \
    -e "SKILL_INPUT={\"panels\": [\"${PANELS//,/\", \"}\"]}" \
    -v "$HOME/.miniclaw:/miniclaw" \
    miniclaw/dashboard:latest
