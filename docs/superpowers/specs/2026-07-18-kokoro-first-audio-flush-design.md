# Kokoro first-audio: tunable first-flush size

**Date:** 2026-07-18
**Status:** Design — approved, pending implementation plan
**Related:** `2026-07-18-first-answer-latency-warmup-design.md` (its Outcome identified Kokoro time-to-first-audio as the real dominant leg)

## Problem

Time-to-first-audio for a voice response is ~2s (measured `Kokoro TTS: …ms to
first audio`), and it is the dominant, most-variable leg of the first-answer
delay now that the warm-up work is reverted. Kokoro cannot stream *within* a
fragment — it synthesizes the whole flushed fragment in one blocking call before
any audio plays — so **first-audio time = synth time of the first flush**.

The streaming path (`core/voice_backends.py`, `KokoroTTSBackend.speak_stream` /
`_find_flush_boundary`) already breaks the first fragment early: it flushes at
the earliest sentence terminator, or (first flush only) the earliest clause
boundary (`, ; : —`) at index ≥ `MIN_FIRST_FLUSH - 1`. But `MIN_FIRST_FLUSH` is
hardcoded to `30`, so any clause boundary before ~char 29 is ignored and the
first fragment is often larger — and slower to synthesize — than it needs to be.

## Goal

Shave time-to-first-audio by letting the first fragment break at an earlier
clause boundary, **without** introducing an audible mid-response pause and
**without** breaking mid-word (which would sound choppy). Keep the exact size
tunable on-device, because first-flush size vs. gap-risk is empirical and
CPU-dependent — the prior session's lesson was to measure the real wall-clock
lever, not guess.

**Success criteria:**
1. With a lower threshold, `_find_flush_boundary` returns an earlier clause
   boundary for a first flush that has one in the newly-eligible range.
2. On-device, the `…ms to first audio` line drops versus the `MIN_FIRST_FLUSH=30`
   baseline for responses whose opening sentence has an early clause boundary.
3. No audible gap after the opening words at the chosen value (subjective,
   confirmed on-device); if there is one, the env value is raised — no redeploy.

## Non-goals

- Mid-word / space-boundary breaking of the first fragment (would sound choppy;
  conflicts with "stay smooth").
- Changing later-sentence flushing — those already overlap prior playback.
- Barge-in, greeting, cues, or non-streaming `speak()`.
- Speeding up Kokoro synthesis itself (CPU floor / Hailo offload — separate work).

## Design

Make the first-flush threshold configurable via a new env var and lower its
default.

- **New env var `KOKORO_MIN_FIRST_FLUSH`, default `20`** (down from the hardcoded
  30). The first flush may then break at a clause boundary as early as ~char 17
  (`value - 1 - `first term length), instead of ~char 29.
- **Read it in `__init__`, in both `KokoroTTSBackend` and `KokoroONNXBackend`.**
  `KokoroONNXBackend.__init__` does **not** call `super().__init__()`
  (`core/voice_backends.py:695`), so a single base-class read would not reach the
  ONNX backend the Pi runs. Reading in each `__init__` (at construction, well
  after `load_dotenv()`) also avoids the import-time-env-read trap that bit the
  profiling flag last session.
- Lower the class constant to `MIN_FIRST_FLUSH = 20` (the new default) and let
  the env var override it: set
  `self.MIN_FIRST_FLUSH = max(1, int(os.getenv("KOKORO_MIN_FIRST_FLUSH", str(type(self).MIN_FIRST_FLUSH))))`,
  floor-guarded so a bad value can't make it 0/negative. Using the class
  constant as the getenv fallback keeps a single source of truth for the
  default. To stay DRY, factor the env read into one small helper (e.g. a
  module-level `_configured_min_first_flush(default)` or a base-class static
  method) called from both `__init__`s.
- `_find_flush_boundary` already reads `self.MIN_FIRST_FLUSH`, so no change to
  its logic — only where the value comes from. The clause-boundary set
  (`, ; : —`) and the sentence-terminator-always-qualifies rule are unchanged, so
  the first fragment stays a natural (just shorter) clause.

### The smoothness constraint / residual risk

The pipeline overlaps fragment #2's synthesis with fragment #1's playback; the
inter-fragment gap is `max(0, synth(#2) − playback(#1))`. At ~20 chars the first
fragment is ~1.3–2s of speech vs ~1.5–2s synth, so a small gap can appear only
when sentence #2 is long. This is the accepted "moderate" trade-off; the env var
is the mitigation (raise toward 25–30 if it sounds gappy).

## Testing

- **Unit test** on `_find_flush_boundary` (no audio, no Kokoro): with a backend
  instance whose `MIN_FIRST_FLUSH` is set low (e.g. 15), a buffer like
  `"Sure, here is the answer."` returns the index of the early comma; with
  `MIN_FIRST_FLUSH=30` the same buffer returns the sentence terminator instead.
  Asserts the threshold actually moves the boundary.
- **Env-wiring unit test**: constructing the backend with
  `KOKORO_MIN_FIRST_FLUSH` set (monkeypatch env) yields that
  `self.MIN_FIRST_FLUSH`; unset yields the default (20); a non-numeric/zero value
  is floored safely.
- **On-device**: set `KOKORO_MIN_FIRST_FLUSH` on the Pi `.env`, restart, run a
  few turns, and compare the `…ms to first audio` numbers across values
  (e.g. 15 / 20 / 25) while listening for a gap. Lock in the best value.

## Deployment

Pi is on `main`. Ship the code, then tune `KOKORO_MIN_FIRST_FLUSH` in the Pi
`.env` and restart `kaizen.service`.
