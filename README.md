# MiniClaw

An open-source, modular voice assistant designed for Raspberry Pi. Think Jarvis, but running on a $120 board in your living room.

Built around a skill-based architecture where capabilities are defined as lightweight markdown files and executed in sandboxed Docker containers. Compatible with [OpenClaw](https://github.com/openclaw/openclaw) skills out of the box.

## How It Works

```
Microphone → Whisper (speech-to-text) → Claude (reasoning + tool selection)
    → Docker container (skill execution) → Claude (summarize result)
    → Piper TTS (text-to-speech) → Speaker
```

The system uses two layers for extensibility:

**Skill layer** — Lightweight `SKILL.md` files that teach Claude *when* and *how* to use a tool. These are just markdown with YAML metadata, costing zero memory until invoked. Compatible with OpenClaw's skill format, giving you access to thousands of community-built skills.

**Container layer** — Each skill executes inside a sandboxed Docker container that spins up on demand and tears down after. This keeps the Pi's limited RAM free and provides security isolation between skills.

## Features

- Voice control with automatic silence detection
- Modular skill system (add capabilities without touching core code)
- OpenClaw skill compatibility (use existing community skills)
- Docker-sandboxed execution (security by default)
- Resource-aware (designed for 8-16GB Raspberry Pi)
- Text mode for development and testing without a microphone

## Requirements

- Python 3.10+
- Docker
- [Anthropic API key](https://console.anthropic.com/)
- Microphone + speaker (for voice mode)
- Optional: [Brave Search API key](https://brave.com/search/api/), [OpenWeatherMap API key](https://openweathermap.org/api), [Spotify credentials](https://developer.spotify.com/dashboard)

### Recommended Hardware

- Raspberry Pi 5 (16GB RAM)
- NVMe SSD via M.2 HAT+
- Raspberry Pi AI HAT+ 2 (for accelerated Whisper)
- Active cooler
- USB microphone

## Quick Start

### Install

```bash
git clone https://github.com/M8SON/MiniClaw.git
cd MiniClaw
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
```

### Build skill containers

```bash
docker build -t miniclaw/weather containers/weather/
docker build -t miniclaw/web-search containers/web_search/
docker build -t miniclaw/spotify containers/spotify/
```

### Run

```bash
# Voice mode (requires microphone)
python3 main.py

# Text mode (no microphone needed)
python3 main.py --text

# List loaded skills
python3 main.py --list
```

## Adding Skills

### Native skills

Create a directory in `skills/` with two files:

**`SKILL.md`** — Tells Claude when and how to use the skill:

```yaml
---
name: my_skill
description: What this skill does
requires:
  env:
    - MY_API_KEY
---

# My Skill

## When to use
Use when the user asks about...

## Inputs
...

## How to respond
...
```

**`config.yaml`** — Tells the orchestrator how to execute it:

```yaml
type: native
image: miniclaw/my-skill:latest
env_passthrough:
  - MY_API_KEY
timeout_seconds: 15
```

Then build a container in `containers/my_skill/` with a simple app that reads `SKILL_INPUT` from the environment and prints the result to stdout.

### OpenClaw-compatible skills

Drop any OpenClaw skill directory (containing a `SKILL.md` with OpenClaw metadata) into `skills/`. The skill loader auto-detects the format and wraps execution in a sandboxed container. Skills that require missing environment variables or binaries are silently skipped.

### Skill precedence

If the same skill name exists in multiple locations, higher-precedence directories win:

1. `./skills/` (workspace — highest)
2. `~/.miniclaw/skills/` (user)
3. Bundled skills (lowest)

## Project Structure

```
MiniClaw/
├── main.py                     # Entry point (voice, text, or list mode)
├── core/
│   ├── orchestrator.py         # Central coordinator: Claude + skills + containers
│   ├── skill_loader.py         # Parses SKILL.md files (native + OpenClaw format)
│   ├── container_manager.py    # Docker lifecycle: spin up, execute, tear down
│   └── voice.py                # Whisper STT + Piper TTS
├── skills/                     # Skill definitions (SKILL.md + config.yaml)
│   ├── weather/
│   ├── web_search/
│   └── spotify/
├── containers/                 # Docker containers for skill execution
│   ├── weather/
│   ├── web_search/
│   └── spotify/
├── requirements.txt
├── .env.example
└── .gitignore
```

## Roadmap

- [x] Core orchestrator + skill loader
- [x] Docker container execution
- [x] OpenClaw skill compatibility layer
- [ ] AI HAT+ 2 accelerated Whisper
- [ ] GPIO / hardware module skills (lights, sensors, displays)
- [ ] Camera + vision skills via AI HAT+ 2
- [ ] Wake word detection
- [ ] Web dashboard for skill management
- [ ] 3D printable case design
- [ ] Community skill registry

## Contributing

This project is in early development. Contributions welcome — especially new skills, hardware module integrations, and Pi-specific optimizations.

## License

MIT
