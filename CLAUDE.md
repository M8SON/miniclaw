# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MiniClaw is a modular, skill-based voice assistant designed for Raspberry Pi. Skills are defined as markdown files and executed in sandboxed Docker containers. Claude API handles reasoning and tool selection.

## Running the Project

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then fill in API keys

# Run modes
python3 main.py              # Voice mode (requires microphone + Whisper + Piper)
python3 main.py --text       # Text-only mode (no audio dependencies required)
python3 main.py --list       # List loaded skills and exit
python3 main.py --skills-dir /path/to/skills  # Load additional skills from path

# Build skill containers (required before use)
docker build -t miniclaw/weather containers/weather/
docker build -t miniclaw/web-search containers/web_search/
docker build -t miniclaw/spotify containers/spotify/
```

There are no test suites, linters, or Makefiles — development is run directly with Python.

## Architecture

The main flow is:

```
User input → Whisper STT → Claude (reasoning + tool selection)
  → Docker container (skill execution) → Claude (summarize result)
  → Piper TTS → Speaker
```

### Core Modules (`core/`)

- **`orchestrator.py`** — Central coordinator. Manages Claude API calls, the tool-use loop (up to 10 rounds), conversation history, and routing tool calls to containers. The system prompt is built by prepending each skill's full markdown body so Claude knows when and how to invoke it.
- **`skill_loader.py`** — Parses `SKILL.md` files from multiple directories with precedence (workspace > `~/.miniclaw/skills` > bundled). Checks skill eligibility by verifying required env vars and binaries. Builds Claude tool definitions from skill metadata.
- **`container_manager.py`** — Manages Docker container lifecycle. Spins up containers per-call, passes `SKILL_INPUT` as a JSON env var, collects stdout, and tears down. Two execution modes: `native` (purpose-built image from `config.yaml`) and `openclaw_compat` (generic executor with skill dir mounted).
- **`voice.py`** — Wraps Whisper (STT) and Piper (TTS). Records 16kHz mono audio, detects silence, transcribes, and speaks responses via `aplay`.

### Skill Structure

Each skill lives in `skills/<name>/` and has two files:

**`SKILL.md`** — YAML frontmatter + markdown body:
```yaml
---
name: skill_name
description: Brief description
requires:
  env: [ENV_VAR_NAME]
---
## When to use
...
## Inputs
```yaml
type: object
properties:
  query: {type: string}
required: [query]
```
## How to respond
...
```

**`config.yaml`** — Execution config:
```yaml
type: native
image: miniclaw/skill-name:latest
env_passthrough: [ENV_VAR_NAME]
timeout_seconds: 15
```

Each skill also has a container implementation in `containers/<name>/` with `app.py` and `Dockerfile`. The container reads `SKILL_INPUT` (JSON) and writes results to stdout.

### Container Security

All containers run with: `--rm --memory=256m --cpus=1.0 --read-only --tmpfs=/tmp:size=64m --security-opt=no-new-privileges`

### Key Behaviors

- **Skill eligibility**: Skills are silently skipped if required env vars or binaries are missing — graceful degradation is intentional.
- **System prompt**: Claude is instructed to avoid asterisks, emojis, and markdown (responses go through TTS).
- **Tool input**: Always JSON via `SKILL_INPUT` env var; output is plain text or JSON to stdout.
- **OpenClaw compatibility**: `skill_loader.py` also parses OpenClaw-format `SKILL.md` files and wraps them in a generic executor container.
