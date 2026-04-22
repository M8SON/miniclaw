# Working Memory

Canonical shared memory for MiniClaw.

Update this file when durable project context changes. Do not create overlapping handoff files unless there is a short-lived reason.

## Identity

- Project: `miniclaw`
- Repo path: `~/linux/miniclaw`
- Owner: Mason Misch (`M8SON`)

## What It Is

- Modular Raspberry Pi voice assistant built around markdown-defined skills.
- Main flow: Whisper STT -> TierRouter -> direct native skill or Ollama or Claude -> native or Docker skill execution -> Kokoro TTS.

## Stable Decisions

- Hardware-adjacent or host-integrated capabilities should be native, not Docker.
- Stateless HTTP/text tools are good Docker skill candidates.
- Memory source of truth is the markdown vault at `~/.miniclaw/memory`.
- chromadb is the default semantic memory layer.
- MemPalace is optional and not required for normal operation.
- MiniClaw remains vault-backed even when MemPalace is installed: markdown vault is canonical storage, chromadb is the local semantic index, and MemPalace is an optional local API/CLI and wake-up/search layer over that store.
- Tiered routing gate: `TierRouter` classifies each transcript (<5ms, no LLM) as
  direct | ollama | claude. Ollama handles routine tool calls; Claude handles complex,
  ambiguous, and meta requests. Feature-flagged via `OLLAMA_ENABLED`.
- Direct routes now avoid building the full Claude system prompt first.
- If Ollama runs tools and then cannot finish the turn, MiniClaw now commits that tool activity into `ConversationState` and asks Claude to finalize the response without re-running the tools.
- Native handlers are a first-class execution path alongside Docker, not just a temporary exception.
- Memory policy is intentionally proactive: save durable, useful long-term facts even without an explicit "remember this" request; avoid trivial or one-turn context.
- Weather location is no longer an env-backed source of truth in the host runtime.
  Resolve location from explicit request override, then remembered memory (`topic: location`), then dashboard-only fallback.

## Skill Split

- Native: `dashboard`, `soundcloud_play`, `install_skill`, `set_env_var`, `save_memory`, `schedule`
- Container: `weather`, `web_search`, `playwright_scraper`, `homebridge`, `skill_tells_random`

## Current State

- CI is configured and passing on `main`.
- GitHub Actions CI on `main` now runs the fast suite plus the scheduler harness.
- Semantic skill selection is shipped.
- `PromptBuilder` expands only relevant skills in full per request.
- Always-full skills: `set_env_var`, `save_memory`, `install_skill`
- Preferred config: `SKILL_SELECT_TOP_K=1`
- Dashboard skill instructions were trimmed as part of token reduction.
- Dashboard now includes ranked NASA EONET priority hazards in the news panel and has hardened live-refresh behavior.
- Tiered intelligence architecture implemented (behind `OLLAMA_ENABLED=false`).
  Three tiers: deterministic → Ollama → Claude. Activate when Pi hardware arrives.
- The major Ollama/Claude handoff seam has been hardened: escalation after tool execution no longer requires re-executing the same side effects.

## Recent Durable Milestones

- 2026-04-19: shipped the `schedule` native skill with yaml-backed recurring tasks
  SchedulerThread drains into the orchestrator between voice turns; never interrupts conversation
  delivery modes: `immediate`, `next_wake` (default, queues for next wake-word), `silent` (log-only)
  missed fires are skipped on startup
- 2026-04-20: hardened dashboard/session runtime behavior and merged EONET hazard ranking into the dashboard news flow
  priority hazards render above normal news when they clear threshold
  live dashboard refresh now preserves session state more safely and keeps location/query updates in sync
  weather location resolution now uses remembered memory before any fallback
- 2026-04-07: voice/memory bug fixes, proactive memory behavior, chromadb-backed semantic memory as the default path
- 2026-04-10: native dashboard skill shipped with detached Flask container + host Chromium and live topic updates
- 2026-04-11: token reduction shipped via semantic skill selection and `main.py --skill-select "QUERY"`
- 2026-04-16: designed and implemented tiered intelligence: deterministic → Ollama → Claude
  TierRouter, OllamaToolLoop, config/intent_patterns.yaml
  all gated behind OLLAMA_ENABLED=false; zero behaviour change until activated
- 2026-04-18: clarified MemPalace integration and tightened routing architecture
  direct routes now defer prompt building until needed
  Ollama escalation with tool activity now finalizes through Claude without replaying tools
  save_memory policy aligned with proactive long-term memory behavior

## Known Gaps

- `ContainerManager` still uses post-construction injection for `_orchestrator` and `_meta_skill_executor`.
- Dashboard end-to-end validation on real Pi hardware is still pending.
- Voice stop/pause control for music is still incomplete.
- Pi 5 + AI HAT+ 2 dependent work is still blocked on hardware.
- Memory behavior is structurally aligned now, but still worth validating in practice once more real conversations accumulate.
- Weather/location memory capture by voice is still skill-prompt driven; there is not yet a dedicated first-class "set my location" tool.

## Open Technical Notes

- Ollama tier not yet validated on real Pi hardware — `OLLAMA_ENABLED=false` until Pi 5 + AI HAT+ arrives.
- Ollama model size (phi4-mini default) should be revisited once RAM tier (8GB vs 16GB) is confirmed.

## Likely Next Direction

- Validate the current tiered architecture on real Pi hardware before adding more routing complexity.
- Focus next on behavioral polish: real-world memory quality, voice flow smoothness, and routine-command reliability.

## Hermes-Inspired Enhancement Roadmap

Four enhancements inspired by the Hermes project. `schedule` skill (#1) shipped 2026-04-19.

1. ~~Cron/schedule skill — yaml-backed recurring tasks that fire natural-language prompts through the orchestrator.~~ Done 2026-04-19.
2. FTS5 session archive — persist past conversations to a sqlite FTS5 index so Claude can recall prior sessions by content search.
   v1 plan: FTS5-only, tool-invoked `recall_session` skill, per-turn writes for crash safety, captures user/assistant text + short tool-activity summaries.
   Engine choice: FTS5 over chromadb for v1 because per-turn `all-MiniLM-L6-v2` embedding (~50-200ms on Pi 5 CPU, ~150-300MB resident RAM) sits on the voice loop critical path. FTS5 writes are microseconds.
   Forward plan: design the `recall_session` interface and sqlite schema so a chromadb rerank layer can drop in cleanly once Hailo-8L NPU offload makes embeddings near-free. Do NOT implement the chromadb path until Hailo arrives — defer it as a follow-up so we never ship CPU-side embedding on the write path.
3. agentskills.io compat — align skill loader / manifest format with the agentskills.io registry so community skills are drop-in installable.
4. Self-improving skills — let skills record their own usage outcomes and refine their SKILL.md routing hints over time.

## Editing Rules

- Keep this file short.
- Keep only durable facts, active constraints, and likely next direction.
- Remove stale or overlapping notes when this file is updated.
- Do not turn this into a changelog or debugging diary.
