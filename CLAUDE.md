# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MiniClaw is a modular, skill-based voice assistant designed for Raspberry Pi. Skills are defined as markdown files and executed in sandboxed Docker containers. Claude API handles reasoning and tool selection.

**Target hardware:** Raspberry Pi 5 (8-16GB RAM) with NVMe SSD, Raspberry Pi AI HAT+ 2 (Hailo-8L NPU for accelerated Whisper + Kokoro TTS), USB microphone, and speaker.

## Running the Project

```bash
./run.sh          # text mode (default) — handles venv, deps, and container builds automatically
./run.sh --voice  # voice mode (requires microphone + espeak-ng)
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
  → Kokoro TTS → Speaker
```

### Core Modules (`core/`)

- **`orchestrator.py`** — Central coordinator. Manages Claude API calls, the tool-use loop (up to 10 rounds), conversation history, and routing tool calls to containers. The system prompt includes each skill's full markdown body so Claude knows when and how to invoke it.
- **`skill_loader.py`** — Loads skills from `skills/`. Every skill must have both `SKILL.md` and `config.yaml` — skills missing `config.yaml` are skipped with a warning. Checks eligibility via `requires.env`, `requires.bins`, `requires.anyBins`, and `requires.os`.
- **`container_manager.py`** — Single execution path: spins up the skill's container, passes `SKILL_INPUT` as a JSON env var, collects stdout, tears down. All skills use the same model.
- **`voice.py`** — Whisper STT + Kokoro TTS. Two Whisper models loaded at init: `whisper-tiny` for wake word detection, `whisper-base` for full transcription. Kokoro (`KPipeline`) for TTS. PyAudio stream is reused between wake detection and listen phases to avoid setup gap.

### Voice Pipeline Detail

**Wake word detection** (`wait_for_wake_word`):
- Runs whisper-tiny on a 2-second sliding window, evaluated every 1 second
- Triggers when the wake phrase appears anywhere in the transcript (default: `"computer"`)
- Keeps the PyAudio stream open on detection — passes it via `self._shared_stream` to avoid teardown/setup gap

**Listen** (`listen` / `_record_until_silence`):
- Reuses `self._shared_stream` from wake detection if available
- Records until silence (configurable threshold + duration)
- `max_wait_seconds` timeout exits early if no speech starts — used for conversation idle detection

**Conversation session** (in `main.py`):
- After wake word → inner loop: listen → respond → listen again
- Exits session after `CONVERSATION_IDLE_TIMEOUT` seconds (default 8s) of no speech
- Say "goodbye" / "stop" to exit entirely

**TTS** (`speak`):
- Kokoro `KPipeline(lang_code="a")` — American English
- Generates audio as numpy chunks, concatenates, writes temp WAV via `soundfile`, plays with `aplay`
- Voice configurable via `TTS_VOICE` env var (default: `af_heart`)
- Requires `espeak-ng` system package (`sudo apt install espeak-ng`)
- Kokoro model (~80MB) auto-downloads to `~/.cache/huggingface/` on first run

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
- **System prompt**: Claude is instructed to avoid markdown, asterisks, and emojis — responses go through TTS.
- **Tool input/output**: Input is always JSON via `SKILL_INPUT` env var; output is plain text or JSON printed to stdout.
- **OpenClaw porting**: Community OpenClaw skills can be ported by adding a `config.yaml` and `Dockerfile` alongside their `SKILL.md`. Use `scripts/port-skill.py` to scaffold these files.

## Key Environment Variables

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required |
| `WHISPER_MODEL` | `base` | STT model size |
| `WAKE_MODEL` | `tiny` | Wake detection model |
| `WAKE_PHRASE` | `computer` | Any word/phrase; substring match |
| `ENABLE_TTS` | `true` | Set false to disable speech output |
| `TTS_VOICE` | `af_heart` | Kokoro voice (af_heart, am_adam, bm_george, bf_emma, etc.) |
| `SILENCE_THRESHOLD` | `1000` | Mic amplitude to count as speech |
| `SILENCE_DURATION` | `2.0` | Seconds of silence before ending recording |
| `CONVERSATION_IDLE_TIMEOUT` | `8` | Seconds of no speech before returning to wake word |
| `CONTAINER_MEMORY` | `256m` | Docker memory limit per skill |

## What's Next (from roadmap)

The next planned items in priority order:
1. **Streaming TTS** — Play Kokoro chunks as they're generated (sounddevice) to reduce response latency; needs a continuous audio queue to avoid inter-chunk gaps
2. **TTS interruption** — Stop speaking when user talks over the assistant
3. **AI HAT+ 2 integration** — Offload Whisper + Kokoro to Hailo-8L NPU
