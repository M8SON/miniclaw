# First-answer latency: warm-on-wake + boot warm-up

**Date:** 2026-07-18
**Status:** Design — approved, pending implementation plan
**Related:** `2026-06-18-voice-naturalness-latency-design.md` (Part B env-tuning; superseded here for the first-answer case)

## Problem

The first spoken answer after wake takes ~8 seconds. Later answers in the same
session are quicker. Measured on-device (Pi 5, `kokoro-onnx` fp32, Whisper
`base`, `KAIZEN_PROFILE=true`) for a plain non-tool Sonnet turn:

```
[TIMING-SUMMARY] stt=1615  listen_record=4778  llm_claude=3095  tts=19146
Response ready: cache_read=0 cache_write=2549   (COLD prompt cache)
Kokoro TTS: 2126ms to first audio
```

Perceived delay (stop-talking → first spoken word) decomposes into three roughly
equal cold stages:

| Stage | Time | Cause |
|---|---|---|
| VAD trailing silence | ~0.7s | endpoint waits `VAD_MIN_SILENCE_MS` after speech |
| STT (Whisper `base` decode) | ~1.6s | first decode of the session is cold |
| LLM (Sonnet, cold cache) | ~3.1s | greeting uses a *stripped* prompt, so the full ~2549-token prefix is never cached before turn 1 |
| Kokoro → first audio | ~2.1s | already near warm floor (greeting warms it) |
| **≈ total to first word** | **~7.5s** | |

`tts=19146` and `listen_record=4778` are full playback / full record windows
(they include the whole spoken answer and the user's talking time), not
latency-to-first-word. The `to first audio` line is the real TTS latency.

## Goal

Cut the first-answer latency by warming the stages that are cold on turn 1,
without adding CPU contention to the live pipeline and without changing answer
content. Target: ~8s → ~5.5s.

**Success criteria:**
1. First Sonnet turn after wake logs `cache_read=2549` (not `cache_write`) when
   warm-on-wake completes before the LLM call.
2. First STT decode of a session is measurably faster than today (boot warm-up
   removes the cold-start portion).
3. No regression to recording/decoding latency (warm-ups must not steal CPU from
   the live turn).
4. `KAIZEN_PROFILE=true` in `.env` actually enables profiling.

## Non-goals

- Smaller/alternate Whisper model, or Hailo offload for STT/TTS (separate,
  larger roadmap efforts).
- Kokoro warm-up — the boot greeting already warms the TTS backend (cold greeting
  synth `2801ms` vs first-turn `2126ms`); a separate warm-up would be redundant.
- Any change to answer text, routing, or the micro tier.

## Design

Three changes plus one already-applied config tweak.

### 1. Warm the LLM prompt cache on wake (the big lever)

**New method `Orchestrator.warm_prompt_cache()`** — fire-and-forget, never raises.

Mechanics:
- Build the stable prefix exactly as a real turn does:
  `stable, _ = self._build_system_prompt_split(user_message=None)`. `stable` is
  byte-stable regardless of `user_message` (only the *dynamic* block varies), so
  this equals what any real Sonnet turn sends.
- Build tools via the Sonnet loop: `tools = self.tool_loop._build_tool_definitions(None)`.
  The Sonnet `tool_loop` is constructed **without** a `skill_selector`
  (`orchestrator.py:101`), so it returns the **full, message-independent** tool
  list — identical to what the real Sonnet turn sends. (Only the micro tier
  filters tools per message; micro turns are already sub-second and are not the
  target.)
- Send a minimal request:
  ```python
  self.client.messages.create(
      model=self.model,                     # Sonnet — the cold-cache path
      max_tokens=1,
      system=[{"type": "text", "text": stable,
               "cache_control": {"type": "ephemeral"}}],
      tools=tools or anthropic.NOT_GIVEN,
      messages=[{"role": "user", "content": "."}],
  )
  ```
- Discard the response. **No** conversation-state mutation, **no** archive write,
  **no** tool execution. Wrap in `try/except` → log-and-swallow.

Why the real turn hits: the cache breakpoint sits on the stable system block,
preceded only by `tools`. Both are byte-identical between warm and real calls
(built from the same `skill_loader` / `prompt_builder` state seconds apart; the
only known invalidators — `save-memory`, skill reload, date rollover — cannot
fire in that window). The real turn's extra uncached dynamic block and messages
come *after* the breakpoint and do not affect the read.

**Hook point:** in `main.py` voice loop, immediately after
`voice.wait_for_wake_word()` returns `detected` (`main.py:309`), spawn a
`daemon` thread running `orchestrator.warm_prompt_cache()`. It overlaps the user
speaking their request + STT decode (~3-5s), so the cache is hot by the time the
real LLM call fires. Wake fires once per session, so this warms exactly the
first turn; turns 2+ are already warm from turn 1's real call.

Network-bound only — no CPU contention with recording/decode.

### 2. Warm Whisper at boot

**New method `warm()` on the STT backends** (`WhisperBackend`, and the
Hailo-backed variant) — runs one minimal decode over ~0.5s of silence
(`np.zeros`) to trigger the cold first-inference initialization. Log-and-swallow
on any error (Hailo path may differ).

**Hook point:** in `main.py` `run_voice_mode`, after the `VoiceInterface` is
built and models are loaded but as a `daemon` thread, so it overlaps the boot
greeting (LLM + TTS, ~5s) and finishes before the first wake. Zero user waiting,
zero contention (no live turn in flight at boot). The greeting does not exercise
STT, so without this the first real decode is cold.

### 3. Fix profiling flag ordering (root-cause)

`core/profiling.py` reads `KAIZEN_PROFILE` at import (`_refresh_enabled()`), but
`main.py` imports `core.profiling` (line 29) **before** `load_dotenv()` (line
35). So `.env`-based `KAIZEN_PROFILE` has never taken effect; profiling only ran
when the flag was in the real process environment.

**Fix:** move `load_dotenv()` above the `from core import …` block in `main.py`
so the flag is loaded before `profiling` is imported. (Alternative: call
`profiling._refresh_enabled()` after `load_dotenv()` — rejected as more surprising.)

Removes the need for the temporary `Environment=KAIZEN_PROFILE=true` line added
to the systemd unit during investigation; that line can be reverted after this
ships (the unit backup is at `~/.config/systemd/user/kaizen.service.bak`).

### 4. VAD trailing silence (already applied)

`VAD_MIN_SILENCE_MS` 1200 → 700 on the Pi `.env` (backup:
`~/kaizen/.env.bak.pre-vad-700`). ~0.5s off every turn. No code change; listed
here for completeness.

## Config

One toggle, default on, matching the codebase's env-flag convention so the
effect can be A/B'd with profiling:

- `VOICE_WARMUP` (default `true`) — gates both the wake cache warm-up and the
  boot STT warm-up. Read in `main.py` at the hook points.

## Testing

- **`warm_prompt_cache()` unit test** (mock the Anthropic client): asserts it
  issues exactly one `max_tokens=1` request whose `system[0]["text"]` byte-equals
  the `stable` from `_build_system_prompt_split(None)` and whose `tools` equal
  `tool_loop._build_tool_definitions(None)`; asserts `conversation_state` and the
  archive are untouched; asserts an Anthropic exception is swallowed.
- **STT `warm()` unit test**: asserts it calls the model's decode once and
  swallows exceptions.
- **Profiling ordering test**: with `KAIZEN_PROFILE=true` in a `.env` loaded by
  `main`, assert `profiling._enabled` is `True` after import ordering — or a
  lighter regression asserting `load_dotenv()` precedes the `core.profiling`
  import in `main.py`.
- **On-device verification**: after deploy, one wake + plain question →
  `TIMING-SUMMARY` shows `cache_read=2549` on the first turn and a lower `stt`
  than the cold baseline.

## Deployment note

Implementation targets `main` (merged). The Pi currently runs
`feat/tts-barge-in @ adb1bff`; deploying this work means the Pi checks out the
new branch (or updated `main`) and restarts `kaizen.service`.

## Expected outcome

| Stage | Before | After | Lever |
|---|---|---|---|
| VAD | 0.7s | 0.7s | already applied (was 1.2s) |
| STT | 1.6s | ~1.2s | boot warm-up |
| LLM | 3.1s | ~1.5s | wake cache warm-up |
| TTS first audio | 2.1s | 2.1s | unchanged (near floor) |
| **Total** | **~7.5s** | **~5.5s** | |
