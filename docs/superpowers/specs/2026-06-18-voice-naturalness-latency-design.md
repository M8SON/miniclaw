# Voice Naturalness + Latency — Design Spec

**Date:** 2026-06-18
**Status:** Approved — implement Part A, document Part B
**Scope:** Voice turn responsiveness on `main`. Two complaints: (1) the turn
feels sluggish / has awkward pauses, (2) the "beep" should clearly signal
*"I heard you and I'm processing."*

---

## 1. Problem

In the live voice loop, the instant the user stops talking nothing plays
until the LLM's first token arrives. The whole end-of-speech → STT → routing
→ time-to-first-token window is silent dead air, so the turn feels sluggish
and unacknowledged.

The infrastructure to fix the *perceived* lag already exists but is
disconnected:

- `Voice.listen(max_wait_seconds, on_speech_done)` already fires
  `on_speech_done()` the moment speech-then-silence is detected, **before**
  transcription (`core/voice.py`). The live loop (`main.py`) calls `listen()`
  **without** passing it, so the hook never fires.
- `Voice.play_thinking_sound()` (an R2-D2 "curious warble") exists but is
  **dead code** — only referenced in tests. It was dropped from the live loop
  when LLM→TTS streaming landed.
- The first cue the user actually hears is `play_response_ready_sound()`,
  which is gated on the LLM's first streamed token — not on "I heard you."

## 2. Goal / success criteria

- A short audio cue plays **the instant the user stops speaking**, overlapping
  (not adding to) the STT + routing + think-time gap.
- The cue reuses the existing `play_thinking_sound` warble — no new sound
  design.
- The cue is **non-blocking**: it must not delay STT from starting.
- No regression to STT accuracy or to the existing response-ready / ack cues.
- Real-latency knobs are catalogued as an A/B tuning playbook the user runs on
  the Pi later; committed defaults are left untouched.

## 3. Part A — "Heard you / processing" cue wiring (code change)

### A.1 Make `play_thinking_sound` non-blocking

`play_thinking_sound` currently ends with `sd.wait()`, which blocks the
caller until the ~0.6s warble finishes. Since it will now be fired from
inside `_record_until_silence` (just before `_transcribe` runs), a blocking
call would stall STT and *add* latency.

**Change:** drop the `sd.wait()` so the warble plays asynchronously, matching
the existing `play_response_ready_sound` / `play_ack_sound` pattern (both
already `sd.play(...)` with no wait and rely on the trailing `_r2_tail()`
buffer + PipeWire mixing). The `_r2_tail()` at the end of the sound already
guards against teardown clipping.

### A.2 Wire the hook in the voice loop

In `main.py`, the conversation-session `listen()` call (currently
`voice.listen(max_wait_seconds=conversation_idle_timeout)`) passes
`on_speech_done=voice.play_thinking_sound`. The warble then fires the moment
the VAD endpoints, overlapping the silent processing window.

### A.3 Resulting cue sequence

```
user stops talking
  └─ [on_speech_done] play_thinking_sound   ← "got it, working" (NEW)
       │ (overlaps: STT + TierRouter + LLM TTFT)
       ├─ direct/micro music ack → play_ack_sound      ← "done"
       └─ normal answer → play_response_ready_sound     ← "here's your answer"
            └─ Kokoro streamed speech
```

Two cues per normal turn (warble, then response-ready chirp) separated by the
processing time. They mark genuinely different events. If it feels beepy on
the Pi, dropping `play_response_ready_sound` is a one-line follow-up — left as
a tuning knob, not removed now.

On a very fast (cached) turn the warble may still be playing when the
response-ready chirp fires; PipeWire mixes them. Acceptable.

### A.4 Testing

`tests/test_voice_mode.py` already has a fake `Voice.listen` that invokes
`on_speech_done` when provided. Add a test asserting that the voice loop wires
`play_thinking_sound` as `on_speech_done` and that it is invoked once per turn
before the response cue. No Pi hardware required — the cue methods are mocked.

## 4. Part B — Latency tuning playbook (documentation only)

All three real-latency levers are **already env vars**. No code change; the
deliverable is an A/B plan with recommended starting values and how to measure
each via `KAIZEN_PROFILE=true` (emits one `[TIMING-SUMMARY] stt=… tier=…
llm_…=… total=…` line per turn). Committed defaults stay as-is so no untested
behavior ships.

| Knob | Current | Try | Effect | Risk |
|---|---|---|---|---|
| `VAD_MIN_SILENCE_MS` | 700 | 550 | snappier end-of-turn | <600ms can clip halting / spelling speech |
| `WHISPER_MODEL_CPU` | `small` | `base` | ~2× faster STT decode | modest accuracy drop |
| `MICRO_TIER_ENABLED` | false | true | Haiku TTFT on routine commands | Haiku micro tier still unvalidated on Pi |

**Method:** set `KAIZEN_PROFILE=true`, change one knob at a time in the Pi's
`.env`, run several representative turns (a question, a music command, a
spelling/halting utterance), and compare the `[TIMING-SUMMARY]` buckets plus
subjective feel. The Part A cue makes a lower `VAD_MIN_SILENCE_MS` feel
intentional rather than abrupt, so tune VAD with the cue in place.

## 5. Out of scope

- Adaptive / semantic endpointing (extending the silence window when an
  utterance looks unfinished). Not worth building blind without Pi
  measurement; revisit if a lowered fixed `VAD_MIN_SILENCE_MS` clips speech
  too often.
- Changing committed env defaults.
- Hailo STT offload tuning, Kokoro NPU offload (separate roadmap items).
- New cue sound design.
