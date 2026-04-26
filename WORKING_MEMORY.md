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
- Hailo STT rollout is intentionally hybrid in V1: wake detection stays CPU Whisper, full post-wake transcription can offload to Hailo when runtime + assets are present.
- Memory policy is intentionally proactive: save durable, useful long-term facts even without an explicit "remember this" request; avoid trivial or one-turn context.
- Weather location is no longer an env-backed source of truth in the host runtime.
  Resolve location from explicit request override, then remembered memory (`topic: location`), then dashboard-only fallback.

## Skill Split

- Native: `dashboard`, `soundcloud`, `install-skill`, `set-env-var`, `save-memory`, `schedule`, `recall-session`
- Container: `weather`, `web-search`, `playwright-scraper`, `homebridge`, `skill-tells-random`

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
- Hailo-backed full transcription path is implemented behind startup auto-detection.
  MiniClaw selects `HybridWhisperBackend` when `/dev/hailo0`, `hailo_platform`, and `~/.miniclaw/models/hailo-whisper/<variant>` assets are present.

## Recent Durable Milestones

- 2026-04-25: shipped Hailo-backed full transcription (hybrid STT)
  wake detection stays on CPU Whisper; full transcription can offload to Hailo
  MiniClaw-owned runtime in `core/hailo_whisper_runtime.py`
  user-scoped asset downloader: `scripts/download_hailo_whisper_assets.py`
- 2026-04-25: shipped voice transport for SoundCloud music
  pause / resume / skip / volume on top of existing play / stop
  20-track queue per play query; mpv IPC for in-flight control
  intent_patterns.yaml regex dispatch; SKILL.md exposes action enum
- 2026-04-25: shipped self-improving skills (Hermes roadmap #4)
  `update-skill-hints` native skill + tool loop 15-call checkpoint + prompt-builder guidance
  Tier 1 additive only; per-skill per-turn rate limit; FIFO at 30 bullets in the auto-section
  every change is a git commit; reversal is `git revert`
- 2026-04-22: shipped FTS5 session archive (`SessionArchive`) and `recall_session` native skill
  every voice/text turn is appended to `~/.miniclaw/sessions.db` (sqlite + FTS5, porter+unicode61, BM25)
  archive is failure-tolerant and gated by `SESSION_ARCHIVE_ENABLED` kill switch
  search returns ±1 surrounding turns for context; reranker hook reserved for future Hailo-8L chromadb layer
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
- ~~Voice stop/pause control for music is still incomplete.~~ Closed 2026-04-25.
  soundcloud handler now supports play / stop / pause / resume / skip / volume_up / volume_down via mpv IPC. play queues 20 tracks; transport actions are regex-dispatched through TierRouter (no LLM round-trip). On-Pi validation pending Ollama setup so TierRouter activates.
- Hailo-backed wake detection and full transcription are both implemented; on-device Pi validation is still pending.
- Memory behavior is structurally aligned now, but still worth validating in practice once more real conversations accumulate.
- Weather/location memory capture by voice is still skill-prompt driven; there is not yet a dedicated first-class "set my location" tool.

## Open Technical Notes

- Ollama tier not yet validated on real Pi hardware — `OLLAMA_ENABLED=false` until Pi 5 + AI HAT+ arrives.
- Ollama model size (phi4-mini default) should be revisited once RAM tier (8GB vs 16GB) is confirmed.

## Likely Next Direction

- Validate the current tiered architecture and hybrid Hailo transcription path on real Pi hardware before adding more routing complexity.
- Focus next on behavioral polish: real-world memory quality, voice flow smoothness, and routine-command reliability.

## Hermes-Inspired Enhancement Roadmap

Four enhancements inspired by the Hermes project. `schedule` skill (#1) shipped 2026-04-19.

1. ~~Cron/schedule skill — yaml-backed recurring tasks that fire natural-language prompts through the orchestrator.~~ Done 2026-04-19.
2. ~~FTS5 session archive — persist past conversations to a sqlite FTS5 index so Claude can recall prior sessions by content search.~~ Done 2026-04-22.
   Forward plan still open: a chromadb rerank layer can drop in via the reserved `reranker` hook on `SessionArchive` once Hailo-8L NPU makes embeddings near-free. Do NOT implement the chromadb path until Hailo arrives — never ship CPU-side embedding on the write path.
3. ~~agentskills.io compat — align skill loader / manifest format with the agentskills.io registry so community skills are drop-in installable.~~ In progress 2026-04-24.
   Skill layout migrated (single-directory, kebab-case names matching parent dirs, scripts/ subfolder). Three-tier trust model (bundled/authored/imported) wired into the loader with per-tier Dockerfile + config.yaml clamps. `requires:` now lives under `metadata.miniclaw.requires`. Remaining: shared install pipeline, CLI surface, voice URL install, self-update frontmatter scaffolding.
4. ~~Self-improving skills — let skills record their own usage outcomes and refine their SKILL.md routing hints over time.~~ Done 2026-04-25.
   Skills with `metadata.miniclaw.self_update.allow_body: true` autonomously gain additive routing hints via the new `update-skill-hints` native skill. Two trigger paths: Claude's in-the-moment judgment plus a 15-tool-call checkpoint nudge. Each change is a path-restricted git commit; rollback is `git revert`. Tier 2/3 changes (rewording, removal) remain manual. Imported-tier skills are blocked regardless of frontmatter.

## Editing Rules

- Keep this file short.
- Keep only durable facts, active constraints, and likely next direction.
- Remove stale or overlapping notes when this file is updated.
- Do not turn this into a changelog or debugging diary.
