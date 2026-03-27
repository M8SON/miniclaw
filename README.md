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
- Optional: [Brave Search API key](https://brave.com/search/api/), [OpenWeatherMap API key](https://openweathermap.org/api)

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
cp .env.example .env
# Edit .env with your API keys
```

### Run

```bash
./run.sh          # text mode (default, no microphone needed)
./run.sh --voice  # voice mode
./run.sh --list   # list loaded skills and exit
```

`run.sh` handles everything automatically: creates the virtual environment, installs dependencies, and builds any missing Docker containers before launching.

## Adding Skills

### Native skills

Create a directory in `skills/` with two files. See `skills/weather/` for a complete working example.

**`SKILL.md`** — Tells Claude when and how to use the skill (see `skills/weather/SKILL.md` for the full file):

```
---
name: get_weather
description: Get current weather information for a specific location
requires:
  env:
    - OPENWEATHER_API_KEY
---

## When to use
Use this skill when the user asks about weather, temperature, or
conditions in a specific location.

## Inputs
(yaml schema defining the query parameter)

## How to respond
Summarize the weather conversationally. Keep it brief for spoken delivery.
```

**`config.yaml`** — Tells the orchestrator how to execute it:

```yaml
type: native
image: miniclaw/weather:latest
env_passthrough:
  - OPENWEATHER_API_KEY
timeout_seconds: 15
devices: []
```

Then build a container in `containers/my_skill/` with an app that reads `SKILL_INPUT` (JSON) from the environment and prints the result to stdout. Add the image to the `CONTAINERS` map in `run.sh` so it gets built automatically.

### Porting an OpenClaw skill

Every skill in MiniClaw runs in a container — including community OpenClaw skills. Use the porting script to scaffold the required files from any OpenClaw skill directory:

```bash
python3 scripts/port-skill.py /path/to/openclaw-skill/
```

This reads the `SKILL.md`, generates `config.yaml` and a container scaffold (`Dockerfile` + `app.py`), and prints the next steps. If the OpenClaw skill includes a `scripts/` directory, those are copied in and wired up automatically.

**What you still need to do after running the script:**

1. Implement (or verify) the logic in `containers/<name>/app.py` — the script generates a working skeleton but the API calls are yours to fill in
2. Add any required API keys to `.env`
3. Add the image to the `SKILL_CONTAINERS` map in `run.sh` so it builds automatically
4. Build and test:
   ```bash
   docker build -t miniclaw/<name>:latest containers/<name>/
   ./run.sh --list
   ```

Skills that require missing environment variables or binaries (`requires.env`, `requires.bins`, `requires.anyBins`) are silently skipped at load time.

### Skill precedence

If the same skill name exists in multiple locations, higher-precedence directories win:

1. `./skills/` (workspace — highest)
2. `~/.miniclaw/skills/` (user)
3. Bundled skills (lowest)

## Project Structure

```
MiniClaw/
├── main.py                     # Entry point (voice, text, or list mode)
├── run.sh                      # Setup + launch script
├── scripts/
│   └── port-skill.py           # Scaffold a container from an OpenClaw skill
├── core/
│   ├── orchestrator.py         # Central coordinator: Claude + skills + containers
│   ├── skill_loader.py         # Parses SKILL.md files (native + OpenClaw format)
│   ├── container_manager.py    # Docker lifecycle: spin up, execute, tear down
│   └── voice.py                # Whisper STT + Piper TTS
├── skills/                     # Skill definitions (SKILL.md + config.yaml)
│   ├── weather/
│   ├── web_search/
│   └── soundcloud/
├── containers/                 # Docker containers for skill execution
│   ├── weather/
│   ├── web_search/
│   ├── soundcloud/
│   └── skill-executor/         # Generic executor for OpenClaw skills with scripts
├── requirements.txt
├── .env.example
└── .gitignore
```

## Roadmap

- [x] Core orchestrator + skill loader
- [x] Docker container execution
- [x] OpenClaw skill compatibility layer
- [x] Wake word detection (whisper-tiny sliding window)
- [x] Conversation session mode (stay active between follow-ups)
- [ ] AI HAT+ 2 accelerated Whisper (offload STT to Hailo-8L NPU)
- [ ] Swap whisper-tiny wake model for a lightweight dedicated wake word model once HAT+ 2 support matures
- [ ] GPIO / hardware module skills (lights, sensors, displays)
- [ ] Camera + vision skills via AI HAT+ 2
- [ ] Web dashboard for skill management
- [ ] 3D printable case design
- [ ] Community skill registry

## Contributing

This project is in early development. Contributions welcome — especially new skills, hardware module integrations, and Pi-specific optimizations.

## License

MIT
