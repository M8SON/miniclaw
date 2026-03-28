#!/bin/bash
# build_new_skill.sh <skill_name>
#
# Called by meta_skill.py after Dockerfile validation passes.
# Builds the Docker image for a voice-installed skill.
# This script holds the Docker socket access — meta_skill.py does not.

set -e

SKILL_NAME="$1"
if [ -z "$SKILL_NAME" ]; then
    echo "Usage: build_new_skill.sh <skill_name>" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
CONTAINER_DIR="$REPO_ROOT/containers/$SKILL_NAME"
IMAGE_NAME="miniclaw/${SKILL_NAME//_/-}:latest"

if [ ! -d "$CONTAINER_DIR" ]; then
    echo "Container directory not found: $CONTAINER_DIR" >&2
    exit 1
fi

if [ ! -f "$CONTAINER_DIR/Dockerfile" ]; then
    echo "No Dockerfile found in $CONTAINER_DIR" >&2
    exit 1
fi

echo "Building $IMAGE_NAME..."
docker build -t "$IMAGE_NAME" "$CONTAINER_DIR"
echo "Done: $IMAGE_NAME"
