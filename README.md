# MiniClaw

An open-source, modular voice assistant designed for Raspberry Pi. Think Jarvis, but running on a $120 board in your living room.

Built around a skill-based architecture where capabilities are defined as lightweight markdown files and executed in sandboxed Docker containers. Compatible with [OpenClaw](https://github.com/openclaw/openclaw) skills out of the box.

## How It Works

```
Microphone → Whisper (speech-to-text) → TierRouter (<5ms, no LLM)
    ├─ deterministic → skill called directly   (stop, volume, goodbye)
    ├─ ollama        → Ollama LLM → skill → Ollama response
    │                  (escalates to Claude if Ollama can't handle it)
    └─ claude        → Claude → skill → Claude response
         → Kokoro TTS (text-to-speech) → Speaker
```

**Tiered intelligence** keeps Claude as the premium reasoning layer — invoked only for complex, ambiguous, or meta requests. Routine commands go to a local Ollama model or bypass LLMs entirely. See [Intelligence Tiers](#intelligence-tiers) for details.

The system uses two layers for extensibility:

**Skill layer** — Lightweight `SKILL.md` files that teach Claude *when* and *how* to use a tool. These are just markdown with YAML metadata, costing zero memory until invoked. Compatible with OpenClaw's skill format, giving you access to community-built skills.

**Container layer** — Each skill executes inside a sandboxed Docker container that spins up on demand and tears down after. This keeps the Pi's RAM free and provides security isolation between skills.

## Features

- Tiered intelligence — deterministic dispatch for instant commands, Ollama for routine requests, Claude only for complex reasoning
- Wake word detection (`"computer"`) using a sliding Whisper window — any phrase works, no training required
- Optional Hailo-backed full transcription on Raspberry Pi AI HAT+ 2 hardware (wake detection remains CPU Whisper for now)
- Conversation session mode — stays active between follow-ups until idle timeout
- Streaming TTS — Kokoro chunks play as they're generated, first words spoken immediately
- Voice skill installation — say "add a skill that does X" and Claude Code writes, builds, and loads it
- Persistent memory — plain markdown notes for transparency, with MemPalace preferred by default when installed
- Modular skill system — add capabilities without touching core code
- OpenClaw skill compatibility — use existing community skills
- Docker-sandboxed execution — security by default, resource-capped containers
- Visual dashboard skill — voice-triggered monitor display with news/OSINT, weather, stocks, and music
- R2-D2 style audio feedback — startup chime and thinking sound
- Text mode for development and testing without a microphone

## Requirements

- Python 3.10+
- Docker
- Node.js 18+ with [Claude Code](https://claude.ai/code) (`npm install -g @anthropic-ai/claude-code`) — required for voice skill installation
- [Anthropic API key](https://console.anthropic.com/)
- `espeak-ng` system package (`sudo apt install espeak-ng`) — required by Kokoro TTS
- Microphone + speaker (for voice mode)
- Optional: [Brave Search API key](https://brave.com/search/api/)
- Optional: HDMI monitor + `chromium-browser` (`sudo apt install chromium-browser`) — required for the dashboard skill

### Recommended Hardware

- Raspberry Pi 5 (8GB or 16GB RAM)
- NVMe SSD via M.2 HAT+
- Raspberry Pi AI HAT+ 2 (for Hailo-backed transcription now, Kokoro offload later)
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

Prices are approximate and vary by region and retailer. The AI HAT+ 2 is optional but strongly recommended for always-on deployments — MiniClaw currently uses it for Hailo-backed full transcription, with wake detection and Kokoro acceleration still remaining on the roadmap.

### Yearly Electricity

See [Power Consumption](#power-consumption) below for the full breakdown. Summary:

| Build | Avg draw | Annual cost (US) | Annual cost (UK) |
|---|---|---|---|
| Budget (CPU inference) | ~7W | ~$8/yr | ~$17/yr |
| Recommended (target with broader NPU offload) | ~4–5W | ~$5/yr | ~$11/yr |

Running costs are negligible — the hardware pays for itself in utility long before electricity becomes a concern.

## Quick Start

```bash
git clone https://github.com/M8SON/MiniClaw.git
cd MiniClaw
./run.sh --install-system-deps  # Debian/Ubuntu only: installs Docker + audio system deps
cp .env.example .env
# Edit .env with your API keys
./run.sh          # text mode (default, no microphone needed)
./run.sh --voice  # voice mode
./run.sh --list   # list loaded skills and exit
```

`run.sh` handles Python setup automatically: creates the virtual environment, installs Python dependencies, and builds any missing Docker containers before launching.

## Optional: Hailo Whisper Offload

MiniClaw can offload **full post-wake transcription** to a Raspberry Pi AI HAT+ 2 / Hailo device. The current implementation is hybrid:

- wake detection stays on CPU Whisper (`WAKE_MODEL`, usually `tiny`)
- full utterance transcription can run on Hailo (`WHISPER_MODEL`, currently `base`/`tiny` variants)

### Pi prerequisites

Install the Hailo runtime on the Pi:

```bash
sudo apt update
sudo apt install -y hailo-all ffmpeg libblas-dev nlohmann-json3-dev
sudo reboot
```

Verify the device and runtime:

```bash
hailortcli fw-control identify
ls /dev/hailo0
```

### Python environment note

MiniClaw's default `run.sh` creates a normal `.venv`. On many Pi installs, the `hailo_platform` Python module is provided by the system package, so you may want MiniClaw's virtualenv to see system site-packages:

```bash
rm -rf .venv
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If `python3 -c "import hailo_platform"` already works inside `.venv`, you do not need to recreate it.

### Download MiniClaw Hailo assets

Download the HEFs and decoder assets into MiniClaw's user-scoped model store:

```bash
.venv/bin/python scripts/download_hailo_whisper_assets.py --variant base --hw-arch hailo8l
```

Assets are stored under:

```text
~/.miniclaw/models/hailo-whisper/
```

### Validate on the Pi

Run MiniClaw in voice mode:

```bash
./run.sh --voice
```

Expected startup line when Hailo is active:

```text
STT backend: Hybrid Whisper (wake=cpu:tiny, transcription=hailo:base)
```

Expected fallback line if something is missing:

```text
STT backend: CPU Whisper fallback — <reason>
```

If you still see CPU fallback, the likely causes are:

- `hailo_platform` is not visible inside `.venv`
- `~/.miniclaw/models/hailo-whisper/base` is missing assets
- the selected `WHISPER_MODEL` variant is unsupported by the Hailo path

Current limitation: wake-word detection is still CPU-only. Hailo currently accelerates the heavier full-transcription path after the wake phrase is detected.

System packages are separate because they require privileged OS changes. On Debian/Ubuntu, you can opt into that setup with:

```bash
./run.sh --install-system-deps
```

That installs `docker.io`, `espeak-ng`, `mpv`, and `portaudio19-dev`, then starts the Docker service.

On systems where Docker was just installed, `run.sh` also adds the current user to the `docker` group. If the current shell has not picked up the new group yet, the launcher will try to continue automatically via `sg docker` for that run.

If a later shell still does not have Docker access, refresh your login session and verify with:

```bash
id
docker info
```

## Testing

MiniClaw now includes a small `unittest` smoke suite for core non-audio behavior, including:

- conversation history normalization and pruning
- native config-writing behavior for `set_env_var`
- the `install_skill` voice flow via injected test doubles instead of live voice, Claude Code, or Docker builds

Run it with:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

There is also a single standard test entry point:

```bash
./scripts/test.sh
```

Optional layers:

```bash
./scripts/test.sh --voice    # scripted voice-loop harness, no mic/speaker needed
./scripts/test.sh --install  # real install_skill integration using Claude CLI + Docker
./scripts/test.sh --all
```

There is also an optional real integration harness for the `install_skill` flow. It uses the real Claude CLI and real Docker build path, but replaces microphone confirmation with scripted responses so it can run unattended:

```bash
.venv/bin/python scripts/test_install_skill_integration.py
```

Notes:
- it requires `claude`, Docker, and valid auth/config on the machine
- it creates a disposable skill and image, then cleans them up by default
- pass `--keep-artifacts` if you want to inspect the generated files afterward

The scripted voice-loop harness exercises the real `run_voice_mode` control flow with a fake voice interface, so it covers wake detection/session management/exit behavior without audio hardware:

```bash
.venv/bin/python scripts/test_voice_mode_harness.py
```

## Adding Skills

Just ask:

> *"computer, add a skill that tells me a random joke"*

Claude Code writes the skill files, validates them, and walks you through three confirmation steps before building and loading the skill. No coding required. See `skills/skill_tells_random/` for an example of a skill created this way.

To port a community [OpenClaw](https://github.com/openclaw/openclaw) skill:

```bash
python3 scripts/port-skill.py /path/to/openclaw-skill/
```

For the skill file structure and developer details, see `CLAUDE.md`.

## Memory

MiniClaw can remember things across conversations. Just say:

> *"computer, remember that my wife's name is Sarah"*
> *"computer, don't forget I prefer temperatures in Celsius"*
> *"computer, make a note that the garage code is 1234"*

Memories are saved as markdown files in `~/.miniclaw/memory/` (configurable via `MEMORY_VAULT_PATH`). Each file is named `YYYY-MM-DD_topic.md` with YAML frontmatter.

**How recall works:**

- **Startup** — vault notes are synced into a local chromadb vector store and the most recent ones are injected into Claude's system prompt
- **Per message** — semantic search over the vector store surfaces relevant memories alongside the user's request, even when the phrasing doesn't match exactly (e.g. asking "what's my wife's name?" finds a note that says "Sarah is Mason's spouse")
- chromadb is included in the default dependencies — no extra setup required

**Obsidian integration** — open `~/.miniclaw/memory` as an Obsidian vault to browse, search, edit, or delete memories with a full GUI. Since the files are plain markdown, everything works out of the box.

### MemPalace

[MemPalace](https://github.com/milla-jovovich/mempalace) is the **recommended/default recall layer when installed** because MiniClaw's default `MEMORY_BACKEND=auto` setting prefers it automatically. MiniClaw still remains vault-backed: memories are always stored as markdown notes in the vault, and when `chromadb` is available they are also synced into a local vector store for semantic recall. Installing MemPalace does not replace that storage model. Instead, MiniClaw prefers MemPalace's Python API or CLI for:

- **Wake-up memory** — curated startup summaries via `mempalace wake-up`
- **Per-message recall** — semantic search via `mempalace search`
- **Browsing/debugging** — using the MemPalace CLI against the same local palace directory

If MemPalace is not installed, MiniClaw still keeps semantic recall working through direct `chromadb` access. In other words:

- **Vault markdown files** remain the source of truth
- **chromadb** provides the actual local vector store
- **MemPalace** is the preferred wake-up/search interface when available

```bash
pip install mempalace
mempalace init ~/projects/miniclaw-memory
```

Leave `MEMORY_BACKEND=auto` to get the default behavior: use MemPalace when installed and otherwise fall back to direct `chromadb` access. Set `MEMORY_BACKEND=mempalace` only if you want to force MemPalace usage, or `MEMORY_BACKEND=vault` to disable the MemPalace/chromadb semantic layer entirely.

## Intelligence Tiers

MiniClaw routes each voice command through a three-tier gate before any LLM runs:

| Tier | Latency | Examples |
|---|---|---|
| **Deterministic** | <5ms | "stop", "volume up", "goodbye" |
| **Ollama** | ~1–3s | "play some jazz", "what's the weather" |
| **Claude** | ~2–5s | "make a skill that...", "remember that...", complex queries |

The router classifies each transcript using:
1. **Dispatch patterns** — regex table (`config/intent_patterns.yaml`). Match → skill called directly, no LLM.
2. **Escalate patterns** — phrases Ollama handles poorly (skill installation, memory, long explanations) → routed straight to Claude, skipping Ollama entirely to avoid double latency.
3. **Skill prediction** — reuses the existing `SkillSelector`. Skills in `CLAUDE_ONLY_SKILLS` go to Claude; everything else goes to Ollama.

When Ollama can't complete a request (unknown tool, malformed response, timeout, or explicit `ESCALATE` signal), it hands off to Claude with the full conversation context intact — no history is lost.

**Enabling Ollama routing** (requires a local [Ollama](https://ollama.com) installation):

```bash
# In .env
OLLAMA_ENABLED=true
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=phi4-mini
```

Leave `OLLAMA_ENABLED` unset or `false` to use Claude for everything — the existing behaviour is completely unchanged.

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
| `CONVERSATION_MAX_MESSAGES` | `24` | Max message-count budget for short-term context, retained as whole recent turns |
| `CONVERSATION_MAX_TOKENS` | `6000` | Approximate token budget for short-term context sent to Claude |
| `MEMORY_BACKEND` | `auto` | `vault`, `mempalace`, or `auto` |
| `MEMORY_MAX_TOKENS` | `2000` | Approximate token budget for persisted memory injected into the system prompt |
| `MEMORY_RECALL_MAX_TOKENS` | `600` | Approximate token budget for live memory recall added per user turn |
| `SKILL_PROMPT_MAX_TOKENS` | `4000` | Approximate token budget for skill instructions in the system prompt |
| `WAKE_MODEL` | `tiny` | Wake word detection model size |
| `CONTAINER_MEMORY` | `256m` | Default Docker memory limit per skill |
| `MEMORY_VAULT_PATH` | `~/.miniclaw/memory` | Directory for memory notes (point Obsidian here) |
| `MEMPALACE_PALACE_PATH` | `~/.mempalace/palace` | Override MemPalace data directory |
| `MEMPALACE_WING` | — | Optional wing filter for MemPalace wake-up memory |
| `MEMPALACE_SAVE_MEMORY` | `auto` | `auto`, `true`, or `false` for MemPalace mirroring on `save_memory` |
| `MEMPALACE_MEMORY_WING` | `wing_miniclaw` | Target wing when mirroring saved memories |
| `MEMPALACE_MEMORY_ROOM` | `assistant-memory` | Target room when mirroring saved memories |
| `BRAVE_API_KEY` | — | Required for web search skill |
| `WEATHER_LOCATION` | `New York,NY` | Default city for the dashboard weather panel |
| `OLLAMA_ENABLED` | `false` | Enable tiered intelligence (requires local Ollama) |
| `OLLAMA_HOST` | `http://localhost:11434` | Local Ollama instance URL |
| `OLLAMA_MODEL` | `phi4-mini` | Ollama model to use for routine commands |
| `OLLAMA_TIMEOUT_SECONDS` | `8` | Escalate to Claude if Ollama exceeds this |
| `CLAUDE_ONLY_SKILLS` | `install_skill` | Comma-separated skills always routed to Claude |

## Power Consumption

MiniClaw is designed to run 24/7, so wake detection power draw is worth considering.

Wake word detection currently runs whisper-tiny every 2 seconds on a 2-second audio window **on CPU**. The current Hailo integration accelerates full post-wake transcription, not the always-on wake loop.

| Mode | Avg system draw | Est. annual usage | US (~$0.13/kWh) | UK (~$0.28/kWh) |
|---|---|---|---|---|
| Current wake loop (CPU inference) | ~7W | ~61 kWh | ~$8/yr | ~$17/yr |

**CPU mode:** whisper-tiny inference on Pi 5's Cortex-A76 takes roughly 0.3–0.8s per 2-second clip, putting wake detection at 15–40% CPU utilization continuously.

**Current Hailo mode:** the Hailo-backed path helps the heavier full-transcription step after wake, reducing post-wake CPU load and latency, but it does not yet change the always-listening wake-loop power profile.

## Project Structure

```
MiniClaw/
├── main.py                        # Entry point (voice, text, or list mode)
├── run.sh                         # Setup + launch script (auto-discovers containers)
├── config/
│   └── intent_patterns.yaml       # Dispatch + escalate patterns for TierRouter
├── core/
│   ├── orchestrator.py            # Routing gate + Claude API + conversation history
│   ├── tier_router.py             # TierRouter: deterministic/ollama/claude classification
│   ├── ollama_tool_loop.py        # Ollama tool loop with EscalateSignal fallback
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
│   ├── dashboard/                 # Visual dashboard on connected monitor (native, no container)
│   ├── install_skill/             # Voice skill installation (native, no container)
│   ├── save_memory/               # Persistent memory (native, writes markdown and can mirror to MemPalace)
│   └── skill_tells_random/        # Example voice-installed skill
├── containers/                    # Docker containers for skill execution
│   ├── base/                      # Shared base image (python:3.11-slim + requests)
│   ├── weather/
│   ├── web_search/
│   ├── soundcloud/
│   ├── playwright_scraper/
│   ├── dashboard/                 # Flask server + Jinja2 template (runs detached, host Chromium points here)
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
- [x] MemPalace-backed wake-up memory and live semantic recall
- [x] Visual dashboard skill (news/OSINT, weather, stocks, music — voice-triggered, auto-closes)
- [x] Tiered intelligence — deterministic dispatch + Ollama routing + Claude fallback (feature-flagged, enable with `OLLAMA_ENABLED=true`)
- [ ] TTS interruption — stop speaking when user talks over the assistant
- [x] AI HAT+ 2 accelerated full transcription (Hailo-backed post-wake STT)
- [ ] AI HAT+ 2 accelerated wake detection (offload whisper-tiny sliding window)
- [ ] AI HAT+ 2 accelerated Kokoro TTS (offload synthesis to Hailo-8L NPU)
- [ ] GPIO / hardware module skills (lights, sensors, displays)
- [ ] Camera + vision skills via AI HAT+ 2
- [ ] Community skill registry

## Contributing

This project is in early development. Contributions welcome — especially new skills, hardware integrations, and Pi-specific optimizations.

## License

MIT
