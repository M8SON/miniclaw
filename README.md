# MiniClaw

An open-source, modular voice assistant designed for Raspberry Pi. Think Jarvis, but running on a $120 board in your living room.

Built around a skill-based architecture where capabilities are defined as lightweight markdown files and executed in sandboxed Docker containers. Compatible with [OpenClaw](https://github.com/openclaw/openclaw) skills out of the box.

## How It Works

```
Microphone → Whisper (speech-to-text) → Claude (reasoning + tool selection)
    → Docker container (skill execution) → Claude (summarize result)
    → Kokoro TTS (text-to-speech) → Speaker
```

The system uses two layers for extensibility:

**Skill layer** — Lightweight `SKILL.md` files that teach Claude *when* and *how* to use a tool. These are just markdown with YAML metadata, costing zero memory until invoked. Compatible with OpenClaw's skill format, giving you access to community-built skills.

**Container layer** — Each skill executes inside a sandboxed Docker container that spins up on demand and tears down after. This keeps the Pi's RAM free and provides security isolation between skills.

## Features

- Wake word detection (`"computer"`) using a sliding Whisper window — any phrase works, no training required
- Conversation session mode — stays active between follow-ups until idle timeout
- Streaming TTS — Kokoro chunks play as they're generated, first words spoken immediately
- Voice skill installation — say "add a skill that does X" and Claude Code writes, builds, and loads it
- Persistent memory — say "remember this" and it's saved as a markdown note, recalled automatically next session
- Modular skill system — add capabilities without touching core code
- OpenClaw skill compatibility — use existing community skills
- Docker-sandboxed execution — security by default, resource-capped containers
- R2-D2 style audio feedback — startup chime and thinking sound
- Text mode for development and testing without a microphone

## Requirements

- Python 3.10+
- Docker
- Node.js 18+ with [Claude Code](https://claude.ai/code) (`npm install -g @anthropic-ai/claude-code`) — required for voice skill installation
- [Anthropic API key](https://console.anthropic.com/)
- `espeak-ng` system package (`sudo apt install espeak-ng`) — required by Kokoro TTS
- Microphone + speaker (for voice mode)
- Optional: [Brave Search API key](https://brave.com/search/api/), [OpenWeatherMap API key](https://openweathermap.org/api)

### Recommended Hardware

- Raspberry Pi 5 (8GB or 16GB RAM)
- NVMe SSD via M.2 HAT+
- Raspberry Pi AI HAT+ 2 (for accelerated Whisper + Kokoro)
- Active cooler
- USB microphone

## Cost

### Hardware

Two practical build tiers:

**Budget build** — Pi 5 only, CPU inference, no NPU or SSD:

| Component | Est. Cost |
|---|---|
| Raspberry Pi 5 (8GB) | ~$80 |
| Official power supply (27W USB-C) | ~$12 |
| USB microphone | ~$20 |
| Small speaker | ~$20 |
| **Total** | **~$132** |

**Recommended build** — full setup with AI HAT+ 2 and NVMe SSD:

| Component | Est. Cost |
|---|---|
| Raspberry Pi 5 (16GB) | ~$120 |
| Raspberry Pi AI HAT+ 2 (Hailo-8L) | ~$70 |
| M.2 HAT+ (NVMe adapter) | ~$12 |
| NVMe SSD (256GB) | ~$28 |
| Active cooler | ~$5 |
| Official power supply (27W USB-C) | ~$12 |
| USB microphone | ~$20 |
| Small speaker | ~$20 |
| Case | ~$10 |
| **Total** | **~$297** |

Prices are approximate and vary by region and retailer. The AI HAT+ 2 is optional but strongly recommended for always-on deployments — it offloads Whisper and Kokoro to the NPU, freeing the CPU and significantly reducing power draw.

### Yearly Electricity

See [Power Consumption](#power-consumption) below for the full breakdown. Summary:

| Build | Avg draw | Annual cost (US) | Annual cost (UK) |
|---|---|---|---|
| Budget (CPU inference) | ~7W | ~$8/yr | ~$17/yr |
| Recommended (NPU inference) | ~4–5W | ~$5/yr | ~$11/yr |

Running costs are negligible — the hardware pays for itself in utility long before electricity becomes a concern.

## Quick Start

```bash
git clone https://github.com/M8SON/MiniClaw.git
cd MiniClaw
cp .env.example .env
# Edit .env with your API keys
./run.sh          # text mode (default, no microphone needed)
./run.sh --voice  # voice mode
./run.sh --list   # list loaded skills and exit
```

`run.sh` handles everything automatically: creates the virtual environment, installs dependencies, and builds any missing Docker containers before launching.

## Adding Skills

### By voice

The easiest way. Say:

> *"computer, add a skill that tells me a random joke"*

Claude Code will write the skill files, validate them, and walk you through three confirmation steps before building and loading the skill. No coding required.

See `skills/skill_tells_random/` for an example of a skill created this way.

### Manually

Create a directory in `skills/<name>/` with two files, and a matching directory in `containers/<name>/` with a `Dockerfile` and `app.py`.

**`skills/<name>/SKILL.md`** — Tells Claude when and how to use the skill:

```
---
name: get_weather
description: Get current weather for a location
requires:
  env:
    - OPENWEATHER_API_KEY
---

## When to use
## Inputs
## How to respond
```

**`skills/<name>/config.yaml`** — Tells the orchestrator how to execute it:

```yaml
image: miniclaw/my-skill:latest
env_passthrough:
  - MY_API_KEY
timeout_seconds: 15
devices: []
```

**`containers/<name>/app.py`** — Reads `SKILL_INPUT` (JSON) from the environment, prints the result to stdout.

**`containers/<name>/Dockerfile`** — Must start with `FROM miniclaw/base:latest`.

`run.sh` auto-discovers any `containers/*/Dockerfile` and builds the image automatically on next launch — no need to register it anywhere.

### Porting an OpenClaw skill

```bash
python3 scripts/port-skill.py /path/to/openclaw-skill/
```

This reads the `SKILL.md`, generates `config.yaml` and a container scaffold (`Dockerfile` + `app.py`), and prints the next steps.

Skills that require missing environment variables or binaries are silently skipped at load time — graceful degradation is intentional.

## Memory

MiniClaw can remember things across conversations. Just say:

> *"computer, remember that my wife's name is Sarah"*
> *"computer, don't forget I prefer temperatures in Celsius"*
> *"computer, make a note that the garage code is 1234"*

Memories are saved as markdown files in `~/.miniclaw/memory/` (configurable via `MEMORY_VAULT_PATH`). Each file is named `YYYY-MM-DD_topic.md` with YAML frontmatter. On every startup the orchestrator reads the vault and injects the contents into Claude's context, so past memories are available automatically without any special commands.

**Obsidian integration** — open `~/.miniclaw/memory` as an Obsidian vault to browse, search, edit, or delete memories with a full GUI. Since the files are plain markdown, everything works out of the box.

## Configuration

Key environment variables in `.env`:

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required |
| `WAKE_PHRASE` | `computer` | Any word or phrase |
| `WHISPER_MODEL` | `base` | STT model size |
| `ENABLE_TTS` | `true` | Set `false` to disable speech |
| `TTS_VOICE` | `af_heart` | Kokoro voice (`af_heart`, `am_adam`, `bm_george`, etc.) |
| `TTS_SPEED` | `1.2` | Speech rate (1.0 = normal, 1.3 = faster) |
| `SILENCE_THRESHOLD` | `1000` | Mic amplitude to count as speech |
| `SILENCE_DURATION` | `2.0` | Seconds of silence before ending recording |
| `CONVERSATION_IDLE_TIMEOUT` | `8` | Seconds of no speech before returning to wake word |
| `WAKE_MODEL` | `tiny` | Wake word detection model size |
| `CONTAINER_MEMORY` | `256m` | Default Docker memory limit per skill |
| `MEMORY_VAULT_PATH` | `~/.miniclaw/memory` | Directory for memory notes (point Obsidian here) |
| `BRAVE_API_KEY` | — | Required for web search skill |
| `OPENWEATHER_API_KEY` | — | Required for weather skill |

## Power Consumption

MiniClaw is designed to run 24/7, so wake detection power draw is worth considering.

Wake word detection runs whisper-tiny every 2 seconds on a 2-second audio window. The cost depends on whether inference runs on CPU or the AI HAT+ 2 NPU:

| Mode | Avg system draw | Est. annual usage | US (~$0.13/kWh) | UK (~$0.28/kWh) |
|---|---|---|---|---|
| CPU inference (Pi 5 only) | ~7W | ~61 kWh | ~$8/yr | ~$17/yr |
| NPU inference (AI HAT+ 2) | ~4–5W | ~39 kWh | ~$5/yr | ~$11/yr |

**CPU mode:** whisper-tiny inference on Pi 5's Cortex-A76 takes roughly 0.3–0.8s per 2-second clip, putting wake detection at 15–40% CPU utilization continuously.

**NPU mode:** the Hailo-8L handles inference in ~50–150ms at ~2–3W peak, running at roughly 10% duty cycle. This frees the CPU entirely and reduces average system draw by ~2–3W.

The electricity cost is modest in either case, but NPU offload is recommended for always-on deployments — the real benefit is thermal headroom and CPU availability for skill execution.

## Project Structure

```
MiniClaw/
├── main.py                        # Entry point (voice, text, or list mode)
├── run.sh                         # Setup + launch script (auto-discovers containers)
├── core/
│   ├── orchestrator.py            # Claude API + skill routing + conversation history
│   ├── skill_loader.py            # Parses SKILL.md files, checks eligibility
│   ├── container_manager.py       # Docker lifecycle: spin up, execute, tear down
│   ├── voice.py                   # Whisper STT + Kokoro TTS + R2-D2 sounds
│   ├── meta_skill.py              # Voice skill installation executor
│   └── dockerfile_validator.py    # Security allowlist for voice-installed skills
├── scripts/
│   ├── port-skill.py              # Scaffold a container from an OpenClaw skill
│   └── build_new_skill.sh         # Host-side Docker build for voice-installed skills
├── skills/                        # Skill definitions (SKILL.md + config.yaml)
│   ├── weather/
│   ├── web_search/
│   ├── soundcloud/
│   ├── playwright_scraper/
│   ├── install_skill/             # Voice skill installation (native, no container)
│   ├── save_memory/               # Persistent memory (native, writes to ~/.miniclaw/memory)
│   └── skill_tells_random/        # Example voice-installed skill
├── containers/                    # Docker containers for skill execution
│   ├── base/                      # Shared base image (python:3.11-slim + requests)
│   ├── weather/
│   ├── web_search/
│   ├── soundcloud/
│   ├── playwright_scraper/
│   └── skill_tells_random/        # Example voice-installed skill
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
- [x] Kokoro TTS with streaming playback (chunks play as generated)
- [x] R2-D2 style audio feedback (startup chime + thinking sound)
- [x] Voice skill installation via Claude Code
- [x] Playwright web scraper skill (handles JS-rendered + bot-protected sites)
- [x] Persistent memory with Obsidian integration
- [ ] TTS interruption — stop speaking when user talks over the assistant
- [ ] AI HAT+ 2 accelerated Whisper (offload STT to Hailo-8L NPU)
- [ ] AI HAT+ 2 accelerated Kokoro TTS (offload synthesis to Hailo-8L NPU)
- [ ] GPIO / hardware module skills (lights, sensors, displays)
- [ ] Camera + vision skills via AI HAT+ 2
- [ ] Community skill registry

## Contributing

This project is in early development. Contributions welcome — especially new skills, hardware integrations, and Pi-specific optimizations.

## License

MIT
