# Working Memory

Canonical shared memory for MiniClaw.

Update this file when durable project context changes. Do not create overlapping handoff files unless there is a short-lived reason.

## Identity

- Project: `miniclaw`
- Repo path: `~/linux/miniclaw`
- Owner: Mason Misch (`M8SON`)

## What It Is

- Modular Raspberry Pi voice assistant built around markdown-defined skills.
- Main flow: Whisper STT -> Claude reasoning/tool selection -> native or Docker skill execution -> Kokoro TTS.

## Stable Decisions

- Hardware-adjacent or host-integrated capabilities should be native, not Docker.
- Stateless HTTP/text tools are good Docker skill candidates.
- Memory source of truth is the markdown vault at `~/.miniclaw/memory`.
- chromadb is the default semantic memory layer.
- MemPalace is optional and not required for normal operation.
- Tiered routing gate: `TierRouter` classifies each transcript (<5ms, no LLM) as
  direct | ollama | claude. Ollama handles routine tool calls; Claude handles complex,
  ambiguous, and meta requests. Feature-flagged via `OLLAMA_ENABLED`.

## Skill Split

- Native: `dashboard`, `soundcloud_play`, `install_skill`, `set_env_var`, `save_memory`
- Container: `weather`, `web_search`, `playwright_scraper`, `homebridge`, `skill_tells_random`

## Current State

- CI is configured and passing on `main`.
- Semantic skill selection is shipped.
- `PromptBuilder` expands only relevant skills in full per request.
- Always-full skills: `set_env_var`, `save_memory`, `install_skill`
- Preferred config: `SKILL_SELECT_TOP_K=1`
- Dashboard skill instructions were trimmed as part of token reduction.
- Tiered intelligence architecture implemented (behind `OLLAMA_ENABLED=false`).
  Three tiers: deterministic → Ollama → Claude. Activate when Pi hardware arrives.

## Recent Durable Milestones

- 2026-04-07: voice/memory bug fixes, proactive memory behavior, chromadb-backed semantic memory as the default path
- 2026-04-10: native dashboard skill shipped with detached Flask container + host Chromium and live topic updates
- 2026-04-11: token reduction shipped via semantic skill selection and `main.py --skill-select "QUERY"`
- 2026-04-16: designed and implemented tiered intelligence: deterministic → Ollama → Claude
  TierRouter, OllamaToolLoop, config/intent_patterns.yaml
  all gated behind OLLAMA_ENABLED=false; zero behaviour change until activated

## Known Gaps

- `ContainerManager` still uses post-construction injection for `_orchestrator` and `_meta_skill_executor`.
- Dashboard end-to-end validation on real Pi hardware is still pending.
- Voice stop/pause control for music is still incomplete.
- Pi 5 + AI HAT+ 2 dependent work is still blocked on hardware.

## Open Technical Notes

- Ollama tier not yet validated on real Pi hardware — `OLLAMA_ENABLED=false` until Pi 5 + AI HAT+ arrives.
- Ollama model size (phi4-mini default) should be revisited once RAM tier (8GB vs 16GB) is confirmed.

## Likely Next Direction

- Add a local intent classifier on Pi hardware as a gatekeeper for routine commands.
- Goal: bypass Claude entirely for simple, high-confidence intents like music and weather, and escalate only ambiguous or complex requests.

## Editing Rules

- Keep this file short.
- Keep only durable facts, active constraints, and likely next direction.
- Remove stale or overlapping notes when this file is updated.
- Do not turn this into a changelog or debugging diary.
