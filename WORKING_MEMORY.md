# Working Memory

Canonical shared memory for Kaizen.

Update this file when durable project context changes. Do not create overlapping handoff files unless there is a short-lived reason.

## Identity

- Project: `kaizen`
- Repo path: `~/linux/kaizen`
- Owner: Mason Misch (`M8SON`)

## What It Is

- Modular Raspberry Pi voice assistant built around markdown-defined skills.
- Main flow: Whisper STT -> TierRouter -> direct native skill or Claude Haiku micro tier or Claude Sonnet -> native or Docker skill execution -> Kokoro TTS.

## Stable Decisions

- Hardware-adjacent or host-integrated capabilities should be native, not Docker.
- Stateless HTTP/text tools are good Docker skill candidates.
- Memory source of truth is the markdown vault at `~/.kaizen/memory`.
- chromadb is the default semantic memory layer.
- MemPalace is optional and not required for normal operation.
- Kaizen remains vault-backed even when MemPalace is installed: markdown vault is canonical storage, chromadb is the local semantic index, and MemPalace is an optional local API/CLI and wake-up/search layer over that store.
- Tiered routing gate: `TierRouter` classifies each transcript (<5ms, no LLM) as
  direct | micro | claude. The Haiku micro tier handles routine tool calls; Sonnet handles complex,
  ambiguous, and meta requests. Feature-flagged via `MICRO_TIER_ENABLED`.
- Direct routes now avoid building the full Claude system prompt first.
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
- Tiered intelligence architecture implemented (behind `MICRO_TIER_ENABLED=false`).
  Three tiers: deterministic → Claude Haiku micro tier → Claude Sonnet. The Ollama tier
  was replaced by the Haiku micro tier on 2026-05-08.
- Hailo-backed full transcription path is implemented behind startup auto-detection.
  Kaizen selects `HybridWhisperBackend` when `/dev/hailo0`, `hailo_platform`, and `~/.kaizen/models/hailo-whisper/<variant>` assets are present.

## Recent Durable Milestones

- 2026-06-18: voice "heard you / processing" cue — wired the previously-unused
  `on_speech_done` hook in the voice loop to `play_thinking_sound` so the R2-D2
  warble fires the instant speech endpoints, overlapping the silent
  STT+routing+LLM-TTFT gap instead of leaving dead air (the warble had been dead
  code since LLM→TTS streaming landed). Made `play_thinking_sound` non-blocking
  (dropped `sd.wait`) so it can't delay STT. Cue does not fire on idle-timeout.
  Spec `docs/superpowers/specs/2026-06-18-voice-naturalness-latency-design.md`.
  Part B of that spec is a docs-only latency tuning playbook — committed defaults
  left untouched; recommended Pi A/B values: `VAD_MIN_SILENCE_MS` 700→550,
  `WHISPER_MODEL_CPU` small→base, `MICRO_TIER_ENABLED` false→true, measured via
  `KAIZEN_PROFILE=true` `[TIMING-SUMMARY]` lines. Implemented + 520 tests green;
  on-device Pi validation still pending.
- 2026-05-13: Anthropic prompt caching wired into the Sonnet tool_loop.
  `PromptBuilder.build_cacheable_parts()` returns `(stable, dynamic)`; stable
  carries BASE_PROMPT + vault memory + skipped/invalid + self-update +
  startup context, dynamic carries the selector-driven skill context.
  `ToolLoop.run` accepts `system_prompt_dynamic`; when non-empty, `system=`
  is a 2-block list with `cache_control: ephemeral` on the stable block.
  All three Sonnet paths in `_process_message` plus close_session use the
  cached split. Micro tier intentionally excluded — top-K tool filter +
  slim system prompt make caching infeasible there. Verified on Pi:
  ~2,525-token cache reads per Sonnet turn within the 5-min TTL.
  `save-memory` is an intentional within-session invalidator. Commits
  `a825a7b` + `4c5593b`.
- 2026-05-05: voice-pipeline Wave 1 shipped — openWakeWord-based wake detection
  replaces Whisper-tiny continuous-transcription wake stream; `WakeBackend` Protocol
  with `OpenWakeWordBackend` (primary) + `WhisperWakeBackend` (fallback) gated by
  `WAKE_BACKEND=openwakeword|whisper`. Default wake word `hey_jarvis` via
  `WAKE_WORD_MODEL`; per-environment threshold via `WAKE_WORD_THRESHOLD` (Pi
  XVF3800 settled on 0.7).
  openwakeword pinned to **0.4.0** because 0.5+ requires `tflite-runtime`, which
  has no PyPI wheel for Python 3.12+ on ARM/x86. 0.4.0 ships ONNX models bundled
  in the package. Constructor takes `wakeword_model_paths`, score-dict keys are
  version-suffixed (e.g. `hey_jarvis_v0.1`).
  Smoke-test surfaced two bugs caught + fixed before merge: (1) every PyAudio
  chunk must be fed to `wake_backend.detect()` continuously — earlier 2-second
  windowing silently zeroed scores because the model's internal buffer never
  primed; (2) `Model.reset()` in 0.4.0 only clears `prediction_buffer`, leaving
  the `AudioFeatures` preprocessor's `raw_data_buffer`, `melspectrogram_buffer`,
  `accumulated_samples`, and `feature_buffer` primed with the prior wake utterance
  — `OpenWakeWordBackend.reset()` now clears all of them in place.
  Spec at `docs/superpowers/specs/2026-05-04-kaizen-voice-pipeline-design.md`,
  plan at `docs/superpowers/plans/2026-05-04-kaizen-voice-pipeline.md`. Wave 2
  (Silero VAD), Wave 3 (faster-whisper + small), Wave 4 (Sonnet→Kokoro streaming)
  are the remaining waves.
- 2026-05-05: voice-pipeline Wave 2 shipped — Silero VAD endpointing
  replaces RMS amplitude threshold in `_record_until_silence` with `VadBackend`
  Protocol + `SileroVadBackend` (primary) + `RmsVadBackend` (fallback) gated by
  `VAD_BACKEND=silero|rms`. Pi smoke-test waived after sufficient conversational
  validation; merged to main in commit `804aa71`. (The `feat/voice-pipeline-wave2`
  branch was later repurposed for the unrelated 3D-printed enclosure CAD track,
  worktree `~/linux/kaizen-voice-pipeline`; v0 parts ordered 2026-06-18.)
  silero-vad 6.x via `load_silero_vad()` returns a TorchScript module that only
  accepts **exactly 512 samples at 16 kHz** per call — `SileroVadBackend` keeps
  an internal carry-over buffer to yield 512-sample frames from PyAudio's
  1024-sample chunks. `reset()` clears both that buffer and the model's LSTM
  state via `model.reset_states()`. Pi-tuning settled on
  `VAD_MIN_SILENCE_MS=1200` (default 700 was too aggressive for halting speech
  / spelling). Wave 3 (faster-whisper + small) and Wave 4 (Sonnet → Kokoro
  streaming) are the remaining waves.
- 2026-04-26: voice latency tuning Phase 1 + 3 (Phase 2 pending)
  user reported 7-10s gap from end-of-speech to first audio in voice mode
  root cause: `orchestrator.process_message()` blocks for full LLM response before TTS starts in `main.py:194-196` — Kokoro's chunk streaming can't help because it never starts mid-LLM
  Phase 1 (shipped): `.env` `SILENCE_DURATION` 1.5→0.8 saves ~0.7s per turn.
  (Historical: `OLLAMA_KEEP_ALIVE=-1` once pinned phi4-mini in RAM via the
  now-removed `core/ollama_tool_loop.py`; obsoleted by the Haiku swap on 2026-05-08.)
  Phase 3 (shipped): per-turn `[timing] listen=Xs llm=Ys tts=Zs total=Ws` line in `main.py` voice loop using `time.monotonic()` around `voice.listen` / `process_message` / `voice.speak`
  Phase 2 (pending): stream LLM tokens to Kokoro per sentence — see Likely Next Direction
- 2026-04-25: shipped Hailo-backed full transcription (hybrid STT)
  wake detection stays on CPU Whisper; full transcription can offload to Hailo
  Kaizen-owned runtime in `core/hailo_whisper_runtime.py`
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
  every voice/text turn is appended to `~/.kaizen/sessions.db` (sqlite + FTS5, porter+unicode61, BM25)
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
  all gated behind OLLAMA_ENABLED=false; zero behaviour change until activated.
  (Ollama tier later replaced by Claude Haiku micro tier on 2026-05-08; this
  entry kept for historical context.)
- 2026-04-18: clarified MemPalace integration and tightened routing architecture
  direct routes now defer prompt building until needed
  Ollama escalation with tool activity now finalizes through Claude without replaying tools
  save_memory policy aligned with proactive long-term memory behavior

## Known Gaps

- `ContainerManager` still uses post-construction injection for `_orchestrator` and `_meta_skill_executor`.
- Dashboard end-to-end validation on real Pi hardware is still pending.
- ~~Voice stop/pause control for music is still incomplete.~~ Closed 2026-04-25.
  soundcloud handler now supports play / stop / pause / resume / skip / volume_up / volume_down via mpv IPC. play queues 20 tracks; transport actions are regex-dispatched through TierRouter (no LLM round-trip).
- Hailo-backed wake detection and full transcription are both implemented; on-device Pi validation is still pending.
- Memory behavior is structurally aligned now, but still worth validating in practice once more real conversations accumulate.
- Weather/location memory capture by voice is still skill-prompt driven; there is not yet a dedicated first-class "set my location" tool.

## Open Technical Notes

- Haiku micro tier replaced the Ollama tier on 2026-05-08; gate is now `MICRO_TIER_ENABLED`.

## Likely Next Direction

- Validate the current tiered architecture and hybrid Hailo transcription path on real Pi hardware before adding more routing complexity.
- Focus next on behavioral polish: real-world memory quality, voice flow smoothness, and routine-command reliability.
- **Voice latency Phase 2 — stream LLM → TTS at sentence boundaries.** Test Phase 1+3 first on Pi to confirm the new `[timing]` line shows LLM as the dominant bucket; then implement:
  1. `core/tool_loop.py` — replace `client.messages.create(...)` (line ~100) with `client.messages.stream(...)`. Add `speak_callback: Callable[[str], None] | None = None` to `run()`. As text deltas arrive, accumulate buffer; flush completed sentences (split on `[.!?]\s+` and `\n\n`) via `speak_callback`. Flush remainder when stream closes.
  2. `core/orchestrator.py` — thread `speak_callback=self.speak_callback` into the three `tool_loop.run()` call sites in `process_message` (lines ~282/297/310).
  3. `main.py` — drop the trailing `voice.speak(response)` at line ~196 since `speak_callback` is already wired at line ~119; otherwise audio plays twice.
  Tradeoffs: tool-use rounds may emit preamble that gets spoken before the tool runs (UX win, not a bug). Each `KokoroTTSBackend.speak()` opens a fresh `sd.OutputStream` (~50ms gap between sentences) — acceptable; promote to a persistent stream only if it sounds choppy.
- **Hygiene:** real API keys (`ANTHROPIC_API_KEY`, `BRAVE_API_KEY`, `OPENWEATHER_API_KEY`) in `.env` were exposed in a Claude Code conversation transcript on 2026-04-26 when the file was read. Rotate `ANTHROPIC_API_KEY` (billing exposure); other two optional. Going forward, grep `.env` for specific keys instead of full reads.

## Hermes-Inspired Enhancement Roadmap

Four enhancements inspired by the Hermes project. `schedule` skill (#1) shipped 2026-04-19.

1. ~~Cron/schedule skill — yaml-backed recurring tasks that fire natural-language prompts through the orchestrator.~~ Done 2026-04-19.
2. ~~FTS5 session archive — persist past conversations to a sqlite FTS5 index so Claude can recall prior sessions by content search.~~ Done 2026-04-22.
   Forward plan still open: a chromadb rerank layer can drop in via the reserved `reranker` hook on `SessionArchive` once Hailo-8L NPU makes embeddings near-free. Do NOT implement the chromadb path until Hailo arrives — never ship CPU-side embedding on the write path.
3. ~~agentskills.io compat — align skill loader / manifest format with the agentskills.io registry so community skills are drop-in installable.~~ In progress 2026-04-24.
   Skill layout migrated (single-directory, kebab-case names matching parent dirs, scripts/ subfolder). Three-tier trust model (bundled/authored/imported) wired into the loader with per-tier Dockerfile + config.yaml clamps. `requires:` now lives under `metadata.kaizen.requires`. Remaining: shared install pipeline, CLI surface, voice URL install, self-update frontmatter scaffolding.
4. ~~Self-improving skills — let skills record their own usage outcomes and refine their SKILL.md routing hints over time.~~ Done 2026-04-25.
   Skills with `metadata.kaizen.self_update.allow_body: true` autonomously gain additive routing hints via the new `update-skill-hints` native skill. Two trigger paths: Claude's in-the-moment judgment plus a 15-tool-call checkpoint nudge. Each change is a path-restricted git commit; rollback is `git revert`. Tier 2/3 changes (rewording, removal) remain manual. Imported-tier skills are blocked regardless of frontmatter.

## Editing Rules

- Keep this file short.
- Keep only durable facts, active constraints, and likely next direction.
- Remove stale or overlapping notes when this file is updated.
- Do not turn this into a changelog or debugging diary.
