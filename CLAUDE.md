# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MiniClaw is a modular, skill-based voice assistant designed for Raspberry Pi. Skills are defined as markdown files and executed in sandboxed Docker containers. Claude API handles reasoning and tool selection.

**Target hardware:** Raspberry Pi 5 (8-16GB RAM) with NVMe SSD, Raspberry Pi AI HAT+ 2 (Hailo-8L NPU for accelerated Whisper), USB microphone, and speaker.

## Running the Project

```bash
./run.sh          # text mode (default) — handles venv, deps, and container builds automatically
./run.sh --voice  # voice mode (requires microphone + Piper TTS model)
./run.sh --list   # list loaded skills and exit
```

For manual control:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in API keys
python3 main.py --text
```

There are no test suites, linters, or Makefiles.

## Architecture

```
Microphone → Whisper STT → Claude (reasoning + tool selection)
  → Docker container (skill execution) → Claude (summarize result)
  → Piper TTS → Speaker
```

### Core Modules (`core/`)

- **`orchestrator.py`** — Central coordinator. Manages Claude API calls, the tool-use loop (up to 10 rounds), conversation history, and routing tool calls to containers. The system prompt includes each skill's full markdown body so Claude knows when and how to invoke it.
- **`skill_loader.py`** — Loads skills from `skills/`. Every skill must have both `SKILL.md` and `config.yaml` — skills missing `config.yaml` are skipped with a warning. Checks eligibility via `requires.env`, `requires.bins`, `requires.anyBins`, and `requires.os`.
- **`container_manager.py`** — Single execution path: spins up the skill's container, passes `SKILL_INPUT` as a JSON env var, collects stdout, tears down. All skills use the same model.
- **`voice.py`** — Wraps Whisper (STT) and Piper (TTS). Records 16kHz mono audio, detects silence, transcribes, and speaks responses via `aplay`.

### Skill Structure

Every skill follows the same layout:

```
skills/<name>/
    SKILL.md      ← Claude routing instructions
    config.yaml   ← Container execution config (required)

containers/<name>/
    app.py        ← Execution logic (reads SKILL_INPUT, prints to stdout)
    Dockerfile    ← Builds FROM miniclaw/base:latest
```

**`SKILL.md`** frontmatter fields:
```yaml
---
name: skill_name
description: Brief description for Claude
requires:
  env: [ENV_VAR_NAME]       # all must be set
  bins: [binary_name]       # all must exist on PATH
  anyBins: [a, b]           # at least one must exist
  os: [linux, darwin]       # OS constraint
---
```

**`config.yaml`** fields:
```yaml
type: native
image: miniclaw/skill-name:latest
env_passthrough: [ENV_VAR_NAME]
timeout_seconds: 15
devices: []                 # e.g. [/dev/snd] for audio skills
```

**`Dockerfile`** always starts with the shared base:
```dockerfile
FROM miniclaw/base:latest
# add skill-specific deps here
COPY app.py /app/app.py
WORKDIR /app
CMD ["python", "app.py"]
```

### Container Images

- **`miniclaw/base:latest`** — Shared base (`python:3.11-slim` + `requests`). Built first; all skill containers layer on top to minimise disk footprint on the Pi.
- **`miniclaw/weather:latest`** — OpenWeatherMap weather lookups
- **`miniclaw/web-search:latest`** — Brave Search web queries
- **`miniclaw/soundcloud:latest`** — SoundCloud playback via yt-dlp + mpv; requires `/dev/snd` device passthrough

### Container Security

All containers run with:
`--rm --memory=256m --cpus=1.0 --read-only --tmpfs=/tmp:size=64m --security-opt=no-new-privileges`

### Key Behaviours

- **Skill eligibility**: Skills missing required env vars or binaries are skipped silently — graceful degradation is intentional for optional skills.
- **System prompt**: Claude is instructed to avoid markdown, asterisks, and emojis — responses go through Piper TTS.
- **Tool input/output**: Input is always JSON via `SKILL_INPUT` env var; output is plain text or JSON printed to stdout.
- **OpenClaw porting**: Community OpenClaw skills can be ported by adding a `config.yaml` and `Dockerfile` alongside their `SKILL.md`. Raw drop-in without a container config is not supported.
