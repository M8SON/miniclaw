# Kaizen

An open-source, modular voice assistant designed for Raspberry Pi. Think Jarvis, but running on a $120 board in your living room.

Built around a skill-based architecture where capabilities are defined as lightweight markdown files and executed in sandboxed Docker containers. Compatible with [OpenClaw](https://github.com/openclaw/openclaw) skills out of the box.

## How It Works

```
Microphone → Whisper (speech-to-text) → TierRouter (<5ms, no LLM)
    ├─ deterministic → skill called directly        (stop, volume, goodbye)
    ├─ micro         → Claude Haiku → skill → Haiku response
    │                  (whole turn escalates to Sonnet on error)
    └─ claude        → Claude Sonnet → skill → Sonnet response
         → Kokoro TTS (text-to-speech) → Speaker
```

**Tiered intelligence** keeps Claude Sonnet as the premium reasoning layer — invoked only for complex, ambiguous, or meta requests. Routine tool calls route to Claude Haiku (the "micro" tier) with a slimmer prompt, and the most common commands bypass LLMs entirely. See [Intelligence Tiers](#intelligence-tiers) for details.

The system uses two layers for extensibility:

**Skill layer** — Lightweight `SKILL.md` files that teach Claude *when* and *how* to use a tool. These are just markdown with YAML metadata, costing zero memory until invoked. Compatible with OpenClaw's skill format, giving you access to community-built skills.

**Container layer** — Each skill executes inside a sandboxed Docker container that spins up on demand and tears down after. This keeps the Pi's RAM free and provides security isolation between skills.

## Features

- Tiered intelligence — deterministic dispatch for instant commands, Claude Haiku for routine tool calls, Claude Sonnet for complex reasoning
- Wake word detection via openWakeWord — lightweight bundled ONNX models (default `hey_jarvis`); ~order-of-magnitude lower CPU than the previous Whisper-window approach
- Optional Hailo-backed full transcription on Raspberry Pi AI HAT+ 2 (wake detection is openWakeWord on CPU — Hailo doesn't run the wake loop)
- Conversation session mode — stays active between follow-ups until idle timeout
- Streaming TTS — Kokoro chunks play as they're generated; ONNX backend ships fp32 + int8 variants (~2–3× faster than the PyTorch baseline on Pi 5)
- Voice skill installation — say "add a skill that does X" and Claude Code writes, builds, and loads it
- Self-improving skills — bundled skills can autonomously refine their own routing hints based on usage
- Persistent memory — plain markdown notes for transparency, with MemPalace preferred by default when installed
- FTS5 session archive — every conversation turn is searchable via the `recall-session` skill
- Cron-style scheduler — yaml-backed recurring tasks fire natural-language prompts through the orchestrator
- Music: Spotify Connect (raspotify), SoundCloud (yt-dlp + mpv), unified `music-control` voice transport
- Modular skill system — agentskills.io-compatible (single-directory, kebab-case)
- OpenClaw skill compatibility — use existing community skills
- Docker-sandboxed execution — security by default, resource-capped containers; native execution path for host-integration skills
- Visual dashboard skill — voice-triggered monitor display with news/OSINT, weather, stocks, and music
- R2-D2 style audio feedback — startup chime and thinking sound
- Run on boot via systemd — installer ships in-tree (see [Run on boot](#run-on-boot-raspberry-pi))
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
- MicroSD for storage
- Raspberry Pi AI HAT+ 2 (for Hailo-backed wake detection and transcription now, Kokoro offload later)
- Active cooler
- USB microphone 
- USB Speaker

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

Prices are approximate and vary by region and retailer. The AI HAT+ 2 is optional but recommended for always-on deployments — Kaizen uses it for Hailo-backed full transcription, with Kokoro acceleration still on the roadmap. Wake detection runs on CPU via openWakeWord; the Hailo wake offload was reverted (see Optional Hailo Whisper Offload below).

### Yearly Electricity

See [Power Consumption](#power-consumption) below for the full breakdown. Summary:

| Build | Avg draw | Annual cost (US) | Annual cost (UK) |
|---|---|---|---|
| Current (openWakeWord CPU loop + Hailo transcription) | ~4–5W | ~$5/yr | ~$11/yr |
| Pre-openWakeWord (Whisper-tiny CPU wake loop) | ~7W | ~$8/yr | ~$17/yr |

Running costs are negligible — the hardware pays for itself in utility long before electricity becomes a concern.

## Quick Start

```bash
git clone https://github.com/M8SON/kaizen.git
cd kaizen
./run.sh --install-system-deps  # Debian/Ubuntu only: installs Docker + audio system deps
cp .env.example .env
# Edit .env with your API keys
./run.sh          # text mode (default, no microphone needed)
./run.sh --voice  # voice mode
./run.sh --list   # list loaded skills and exit
```

`run.sh` handles Python setup automatically: creates the virtual environment, installs Python dependencies, and builds any missing Docker containers before launching.

## Optional: Hailo Whisper Offload

Kaizen can offload **full post-wake transcription** to a Raspberry Pi AI HAT+ 2 / Hailo device. Wake detection runs on CPU via openWakeWord and stays there — the earlier Hailo Whisper wake offload was reverted (the published Hailo wake encoder needs a 10s window, the wake loop buffers ~80ms, and silence padding produced hallucinations).

- wake detection runs on openWakeWord (`WAKE_WORD_MODEL`, default `hey_jarvis`; `WAKE_WORD_THRESHOLD`, default `0.5`)
- full utterance transcription can run on Hailo Whisper (`WHISPER_MODEL_HAILO`, `base` or `tiny`)
- the transcription path falls back to CPU automatically if the Hailo runtime or assets are missing

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

Kaizen's default `run.sh` creates a normal `.venv`. On many Pi installs, the `hailo_platform` Python module is provided by the system package, so you may want Kaizen's virtualenv to see system site-packages:

```bash
rm -rf .venv
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If `python3 -c "import hailo_platform"` already works inside `.venv`, you do not need to recreate it.

### Download Kaizen Hailo assets

Download the HEFs and decoder assets into Kaizen's user-scoped model store:

```bash
.venv/bin/python scripts/download_hailo_whisper_assets.py --variant base --hw-arch hailo8l
```

Assets are stored under:

```text
~/.kaizen/models/hailo-whisper/
```

### Validate on the Pi

Run Kaizen in voice mode:

```bash
./run.sh --voice
```

Expected startup lines when transcription is on Hailo:

```text
Wake backend: openwakeword (hey_jarvis, threshold=0.5)
STT backend: Hybrid Whisper (transcription=hailo:base)
```

Full CPU fallback line if Hailo is unavailable:

```text
STT backend: cpu:small (faster-whisper) — <reason>
```

If you see CPU fallback when expecting Hailo, the likely causes are:

- `hailo_platform` is not visible inside `.venv` (recreate with `--system-site-packages`)
- `~/.kaizen/models/hailo-whisper/base` is missing assets
- the selected `WHISPER_MODEL_HAILO` variant has no published HEF (only `tiny` and `base` exist today)

Current limitation: Hailo currently accelerates Whisper only. TierRouter and Kokoro are unchanged, and Kokoro offload is still future work.

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

Kaizen now includes a small `unittest` smoke suite for core non-audio behavior, including:

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

Claude Code writes the skill files, validates them, and walks you through three confirmation steps before building and loading the skill. No coding required. See `skills/skill-tells-random/` for an example of a skill created this way.

To port a community [OpenClaw](https://github.com/openclaw/openclaw) skill:

```bash
python3 scripts/port-skill.py /path/to/openclaw-skill/
```

For the skill file structure and developer details, see `CLAUDE.md`.

## Memory

Kaizen can remember things across conversations. Just say:

> *"computer, remember that my wife's name is Sarah"*
> *"computer, don't forget I prefer temperatures in Celsius"*
> *"computer, make a note that the garage code is 1234"*

Memories are saved as markdown files in `~/.kaizen/memory/` (configurable via `MEMORY_VAULT_PATH`). Each file is named `YYYY-MM-DD_topic.md` with YAML frontmatter.

**How recall works:**

- **Startup** — vault notes are synced into a local chromadb vector store and the most recent ones are injected into Claude's system prompt
- **Per message** — semantic search over the vector store surfaces relevant memories alongside the user's request, even when the phrasing doesn't match exactly (e.g. asking "what's my wife's name?" finds a note that says "Sarah is Mason's spouse")
- chromadb is included in the default dependencies — no extra setup required

**Obsidian integration** — open `~/.kaizen/memory` as an Obsidian vault to browse, search, edit, or delete memories with a full GUI. Since the files are plain markdown, everything works out of the box.

### MemPalace

[MemPalace](https://github.com/milla-jovovich/mempalace) is the **recommended/default recall layer when installed** because Kaizen's default `MEMORY_BACKEND=auto` setting prefers it automatically. Kaizen still remains vault-backed: memories are always stored as markdown notes in the vault, and when `chromadb` is available they are also synced into a local vector store for semantic recall. Installing MemPalace does not replace that storage model. Instead, Kaizen prefers MemPalace's Python API or CLI for:

- **Wake-up memory** — curated startup summaries via `mempalace wake-up`
- **Per-message recall** — semantic search via `mempalace search`
- **Browsing/debugging** — using the MemPalace CLI against the same local palace directory

If MemPalace is not installed, Kaizen still keeps semantic recall working through direct `chromadb` access. In other words:

- **Vault markdown files** remain the source of truth
- **chromadb** provides the actual local vector store
- **MemPalace** is the preferred wake-up/search interface when available

```bash
pip install mempalace
mempalace init ~/projects/kaizen-memory
```

Leave `MEMORY_BACKEND=auto` to get the default behavior: use MemPalace when installed and otherwise fall back to direct `chromadb` access. Set `MEMORY_BACKEND=mempalace` only if you want to force MemPalace usage, or `MEMORY_BACKEND=vault` to disable the MemPalace/chromadb semantic layer entirely.

## Intelligence Tiers

Kaizen routes each voice command through a three-tier gate before any LLM runs:

| Tier | Model | Latency | Examples |
|---|---|---|---|
| **Deterministic** | — (regex) | <5ms | "stop", "volume up", "goodbye" |
| **Micro** | Claude Haiku | ~1–2s | "play some jazz", "what's the weather" |
| **Claude** | Claude Sonnet | ~2–5s | "make a skill that...", "remember that...", ambiguous or multi-step requests |

The router classifies each transcript (`core/tier_router.py`) using:
1. **Dispatch patterns** — regex table (`config/intent_patterns.yaml`). Match → skill called directly, no LLM.
2. **Escalate patterns** — phrases Haiku handles poorly (skill installation, memory edits, long explanations) → routed straight to Sonnet, skipping Haiku to avoid double latency.
3. **Skill prediction** — reuses the existing `SkillSelector`. Skills in `CLAUDE_ONLY_SKILLS` go to Sonnet; everything else goes to Haiku.

When Haiku errors (network blip, malformed response), the whole turn escalates to Sonnet via try/except — no history is lost.

**Enabling the micro tier:**

```bash
# In .env
MICRO_TIER_ENABLED=true
MICRO_TIER_MODEL=claude-haiku-4-5
```

Leave `MICRO_TIER_ENABLED` unset or `false` to send every request to Sonnet.

## Configuration

Key environment variables in `.env`:

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required |
| `WHISPER_MODEL_CPU` | `small` | faster-whisper model on CPU path (tiny/base/small/medium/large) |
| `WHISPER_MODEL_HAILO` | `base` | Hailo HEF variant (only `tiny` and `base` are published) |
| `WHISPER_MODEL` | — | Legacy single-model knob; overrides the above when set |
| `ENABLE_TTS` | `true` | Set `false` to disable speech |
| `TTS_BACKEND` | `kokoro` | `kokoro` (PyTorch) or `kokoro-onnx` (ONNX, ~2–3× faster on Pi 5) |
| `KOKORO_ONNX_VARIANT` | `fp32` | `fp32` or `int8`; fp32 is faster on ARM, int8 on x86_64 |
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
| `WAKE_WORD_MODEL` | `hey_jarvis` | openWakeWord bundled model (`hey_jarvis`, `alexa`, `hey_mycroft`, `timer`, `weather`) |
| `WAKE_WORD_THRESHOLD` | `0.5` | Activation confidence (0.0–1.0); raise to reduce false fires |
| `BARGE_IN_ENABLED` | `true` | Say the wake word during a response to interrupt playback and start listening; set false to disable |
| `CONTAINER_MEMORY` | `256m` | Default Docker memory limit per skill |
| `MEMORY_VAULT_PATH` | `~/.kaizen/memory` | Directory for memory notes (point Obsidian here) |
| `MEMPALACE_PALACE_PATH` | `~/.mempalace/palace` | Override MemPalace data directory |
| `MEMPALACE_WING` | — | Optional wing filter for MemPalace wake-up memory |
| `MEMPALACE_SAVE_MEMORY` | `auto` | `auto`, `true`, or `false` for MemPalace mirroring on `save_memory` |
| `MEMPALACE_MEMORY_WING` | `wing_kaizen` | Target wing when mirroring saved memories |
| `MEMPALACE_MEMORY_ROOM` | `assistant-memory` | Target room when mirroring saved memories |
| `BRAVE_API_KEY` | — | Required for web search skill |
| `SPOTIFY_CLIENT_ID` / `_SECRET` / `_REDIRECT_URI` | — | Spotify Web API auth; redirect must use `127.0.0.1` (Spotify deprecated `localhost` in 2025) |
| `SPOTIFY_DEVICE_NAME` | — | Pin playback to one Spotify Connect device (e.g. `Kaizen`) so multi-device accounts don't spill onto phone/TV |
| `MIC_DEVICE` | `Array` | Case-insensitive substring match against the ALSA/PortAudio device name |
| `SPEAKER_DEVICE` | `KT USB` | Same — set to `pipewire` on Pi 5 if running raspotify alongside Kaizen |
| `MICRO_TIER_ENABLED` | `false` | Enable Haiku micro-tier routing |
| `MICRO_TIER_MODEL` | `claude-haiku-4-5` | Model used for the micro tier |
| `CLAUDE_ONLY_SKILLS` | `install-skill` | Comma-separated skills always routed to Sonnet |

## Power Consumption

Kaizen is designed to run 24/7, so wake detection power draw is worth considering.

Wake word detection runs **openWakeWord** — a lightweight melspectrogram → embedding → classifier ONNX pipeline (the `hey_jarvis_v0.1` model by default). It scores roughly every ~80 ms on a small audio frame, and full post-wake transcription only fires after a positive detection. The Hailo integration accelerates that post-wake transcription, not the always-on wake loop.

| Mode | Avg system draw | Est. annual usage | US (~$0.13/kWh) | UK (~$0.28/kWh) |
|---|---|---|---|---|
| Current — openWakeWord wake loop + Hailo transcription | ~4–5W | ~35–44 kWh | ~$5/yr | ~$10–12/yr |
| Previous — Whisper-tiny wake loop on CPU | ~7W | ~61 kWh | ~$8/yr | ~$17/yr |

All numbers are approximate — actual draw depends on USB DAC, mic, network activity, and Pi 5 board revision. Measure on your own hardware if it matters.

**Wake-loop CPU:** openWakeWord on Pi 5 (Cortex-A76) typically runs at ~1–3% utilization of a single core in the always-listening state, an order of magnitude below the previous Whisper-tiny window which sat at 15–40% continuously. The drop is the dominant factor in the ~2W lower average draw.

**Hailo mode:** the Hailo-backed path accelerates the heavier full-transcription step after wake, reducing post-wake CPU load and latency. It doesn't touch the wake loop — openWakeWord stays on CPU.

## Run on boot (Raspberry Pi)

To make Kaizen start automatically when the Pi powers on:

```bash
./scripts/install_systemd_service.sh
```

The installer is idempotent — re-run it any time the unit file changes. It will:

- Copy `config/systemd/kaizen.service` to `~/.config/systemd/user/`.
- Enable `loginctl enable-linger` so user services start at boot (asks for sudo).
- Ensure `/var/log/journal` exists so logs survive reboot (asks for sudo).
- Enable + start the service.

### Day-to-day

```bash
systemctl --user status kaizen      # is it running?
systemctl --user start kaizen       # start after a manual stop
systemctl --user restart kaizen     # restart after a config change
systemctl --user stop kaizen        # stop until next boot or manual start
journalctl --user -u kaizen -f      # tail live logs
journalctl --user -u kaizen -p err --since '1 hour ago'   # crashes only
```

### Uninstall

```bash
./scripts/uninstall_systemd_service.sh
```

## Project Structure

```
kaizen/
├── main.py                        # Entry point (voice, text, or list mode)
├── run.sh                         # Setup + launch script (auto-discovers containers)
├── config/
│   ├── intent_patterns.yaml       # Dispatch + escalate patterns for TierRouter
│   └── systemd/kaizen.service   # User-level unit for boot auto-start
├── core/
│   ├── orchestrator.py            # Tiered routing gate + Claude API + conversation history
│   ├── tier_router.py             # TierRouter: deterministic/micro/claude classification
│   ├── tool_loop.py               # Shared tool loop (serves both Haiku micro and Sonnet)
│   ├── prompt_builder.py          # Token-budgeted prompt assembly (full + slim micro variant)
│   ├── skill_loader.py            # Parses SKILL.md files, enforces three-tier policy
│   ├── container_manager.py       # Docker + native skill execution
│   ├── voice.py                   # STT + Kokoro TTS + R2-D2 sounds + streaming pipeline
│   ├── voice_backends.py          # FasterWhisper / Hailo Whisper backend selection
│   ├── session_archive.py         # FTS5 sqlite archive of every conversation turn
│   ├── meta_skill.py              # Voice skill installation executor
│   └── dockerfile_validator.py    # Security allowlist for voice-installed skills
├── scripts/
│   ├── install_systemd_service.sh # Idempotent systemd installer (boot auto-start)
│   ├── uninstall_systemd_service.sh
│   ├── download_hailo_whisper_assets.py
│   ├── download_kokoro_onnx.py    # Fetch Kokoro ONNX models for the fast TTS backend
│   ├── spotify_login.py           # One-time Spotify OAuth bootstrap
│   ├── port-openclaw-skill.py     # Scaffold a skill from an OpenClaw definition
│   └── build_new_skill.sh         # Host-side Docker build for voice-installed skills
├── skills/                        # agentskills.io layout: SKILL.md + config.yaml + scripts/
│   ├── dashboard/                 # Visual dashboard (native)
│   ├── homebridge/                # Smart home control via Homebridge UI X (Docker)
│   ├── install-skill/             # Voice skill installation (native)
│   ├── music-control/             # Unified pause/resume/skip/volume across active source (native)
│   ├── playwright-scraper/        # Headless Chromium scraper (Docker)
│   ├── recall-session/            # FTS5 search over past conversations (native)
│   ├── save-memory/               # Persistent memory, mirrors to MemPalace when present (native)
│   ├── schedule/                  # Cron-style yaml-backed recurring tasks (native)
│   ├── set-env-var/               # Voice-driven .env edit + skill reload (native)
│   ├── skill-tells-random/        # Example voice-installed skill (Docker)
│   ├── soundcloud/                # SoundCloud playback via yt-dlp + mpv, narrowed to remix scope (native)
│   ├── spotify/                   # Spotify Connect playback via raspotify (native)
│   ├── update-skill-hints/        # Self-improving skill routing hints (native)
│   ├── weather/                   # OpenWeatherMap (Docker)
│   └── web-search/                # Brave Search (Docker)
├── containers/
│   └── base/                      # Shared Docker base (python:3.11-slim + requests)
├── tests/                         # pytest suite — fast suite + scheduler harness in CI
├── requirements.txt
├── .env.example
└── .gitignore
```

Per-skill Docker build assets live under `skills/<name>/scripts/` (Dockerfile + app.py). The standalone `containers/<name>/` layout was retired during the agentskills.io migration.

## Roadmap

- [x] Core orchestrator + skill loader
- [x] Docker container execution + native execution path for host-integration skills
- [x] OpenClaw skill compatibility layer + agentskills.io single-directory layout
- [x] Wake word detection via openWakeWord (replaced the earlier Whisper sliding-window approach)
- [x] faster-whisper CPU backend for post-wake transcription
- [x] Conversation session mode (stay active between follow-ups)
- [x] Kokoro TTS with streaming playback (chunks play as generated)
- [x] Kokoro ONNX backend (fp32/int8, ~2–3× faster than PyTorch on Pi 5)
- [x] R2-D2 style audio feedback (startup chime + thinking sound)
- [x] Voice skill installation via Claude Code
- [x] Self-improving skills (additive routing-hint refinement, git-tracked)
- [x] Playwright web scraper skill (handles JS-rendered + bot-protected sites)
- [x] Persistent memory with Obsidian integration
- [x] MemPalace-backed wake-up memory and live semantic recall
- [x] FTS5 session archive + `recall-session` skill
- [x] Cron-style scheduler skill
- [x] SoundCloud transport (pause/resume/skip/volume via mpv IPC)
- [x] Spotify Connect playback via raspotify + unified `music-control` skill
- [x] Visual dashboard skill (news/OSINT, weather, stocks, music — voice-triggered, auto-closes)
- [x] EONET hazard ranking in dashboard news flow
- [x] Tiered intelligence — deterministic dispatch + Haiku micro-tier + Sonnet (feature-flagged, enable with `MICRO_TIER_ENABLED=true`)
- [x] AI HAT+ 2 accelerated full transcription (Hailo-backed post-wake STT)
- [x] Run on boot via systemd (user-level unit with linger)
- [x] TTS interruption — stop speaking when user says the wake word over the assistant
- [ ] AI HAT+ 2 accelerated Kokoro TTS (offload synthesis to Hailo-8L NPU)
- [ ] GPIO / hardware module skills (lights, sensors, displays)
- [ ] Camera + vision skills via AI HAT+ 2
- [ ] Community skill registry

> Hailo wake offload was implemented and reverted: the published wake encoder needs a 10s window, but the wake loop only buffers 2s of audio, so silence padding produced hallucinations. To re-enable, the wake loop would need to accumulate 10s before invoking the Hailo transcriber, or Hailo would need to publish a shorter-window wake HEF.

## Contributing

This project is in early development. Contributions welcome — especially new skills, hardware integrations, and Pi-specific optimizations.

## License

MIT
