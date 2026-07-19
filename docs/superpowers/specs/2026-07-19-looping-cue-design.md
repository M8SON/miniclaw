# Looping pre-buffer cue (runs until speech) + R2-D2 sound

**Date:** 2026-07-19
**Status:** Design — approved (sound = candidate 3), pending implementation plan
**Related:** `2026-07-18-organic-speech-flow-design.md` (added the fixed-length pre-buffer cue this replaces)

## Problem

The pre-buffer cue is a fixed length (`KOKORO_CUE_MS`), but time-to-first-audio
varies (~1.9–2.5s), so the cue either ends before speech (dead air) or runs under
the first words (overlap). Mason wants the cue to run **continuously until speech
actually starts** — continuous "I'm working on it" feedback, no dead air — and to
**sound more like R2-D2** (the current smooth warble doesn't).

## Goal

Replace the fixed-length one-shot cue with a **short R2-D2 segment looped from
response-start until the first real audio plays**, then stopped cleanly. The
segment sound is candidate 3 ("questioning bloops") at its original pace, chosen
by ear on-device.

**Success criteria:**
1. The cue begins when the response starts (first LLM delta) and stops the moment
   speech begins — no dead air before speech, no cue bleeding into speech.
2. The cue never loops forever (stops even if the response produces no audio).
3. The cue sound is the candidate-3 bloops segment (rising "questioning"
   inflection + punctuating beeps).

## Non-goals

- Changing the pre-buffer, first-flush, concise prompt, or barge-in.
- The non-streaming `speak()` path (no pre-buffer there; keep `play_response_ready_sound`).
- A configurable cue length — obsolete now that the cue runs until speech
  (`KOKORO_CUE_MS` is removed).

## Design

### 1. The sound (candidate 3, ~0.44s loopable segment)

A `_prebuffer_cue_segment()` helper in `VoiceInterface` builds a short array from
the existing `_r2_chirp`/`_r2_beep` helpers, matching the auditioned candidate 3:

- `_r2_beep(800, 0.06)`, gap 0.03
- `_r2_beep(1200, 0.06)`, gap 0.03
- `_r2_chirp(1000, 2300, 0.18, vibrato_hz=10, vibrato_depth=60)` (rising question), gap 0.02
- `_r2_beep(1700, 0.05)`

(~0.44s; loops cleanly with the small trailing gap.)

### 2. Loop-until-speech mechanism

**`VoiceInterface.start_prebuffer_cue()`** — starts a daemon thread that writes
the segment to a dedicated `sd.OutputStream` on repeat, written in small
sub-blocks with the stop `threading.Event` checked between sub-blocks (same
pattern as the Kokoro writer's `WRITE_SUB_BLOCK`), so a stop lands within ~tens
of ms rather than waiting out a whole ~0.44s segment. Idempotent (a second start
while active is a no-op). Errors logged and swallowed (a missing speaker can't
crash the loop).

**`VoiceInterface.stop_prebuffer_cue()`** — sets the stop event and tears the cue
stream/thread down, applying a short fade on the final partial segment so there's
no click. Idempotent; safe to call when no cue is running.

**Signaling "speech started":** `KokoroTTSBackend.speak_stream` already records the
first real device write (`first_audio_at`, `core/voice_backends.py:567`). Add an
`on_first_audio` callback param to `speak_stream` (fired exactly once, there) and
thread it through `VoiceInterface.speak_stream_feeder` to the backend.

**Wiring (`main.py`, streaming branch):**
- `on_first_chunk=voice.start_prebuffer_cue` (was the one-shot cue).
- `on_first_audio=voice.stop_prebuffer_cue` (new).
- Belt-and-suspenders: `finalize()` (which always runs) also calls
  `stop_prebuffer_cue()`, so an empty/no-audio response can't leave the cue
  looping (success criterion 2).

Since `start`/`stop` use a dedicated cue `OutputStream`, they don't touch the
Kokoro TTS `OutputStream`; PipeWire mixes if they briefly overlap (consistent
with existing cues).

### 3. Remove `KOKORO_CUE_MS`

The loop length is now dynamic, so `KOKORO_CUE_MS` and its env read are removed
from `core/voice.py`. The Pi `.env` line becomes inert and is deleted on deploy.

## Testing

- **Segment:** unit test `_prebuffer_cue_segment()` returns a non-empty float32
  array of roughly the expected length.
- **`on_first_audio`:** unit test (mocked `sd`/pipeline) that `speak_stream` fires
  the callback exactly once, at first audio; and not at all when the response
  produces no audio.
- **Loop lifecycle:** unit test `start_prebuffer_cue()` then `stop_prebuffer_cue()`
  (mocked `sd`): after stop, the cue thread is not alive; `stop` without a prior
  `start` is a no-op; double `start` doesn't spawn two threads.
- **Feeder passthrough:** `speak_stream_feeder` forwards `on_first_audio` to the
  backend; `FakeVoice` in `test_voice_mode.py` gains `start_prebuffer_cue`/
  `stop_prebuffer_cue` stubs.
- **On-device:** run a turn; confirm the R2-D2 bloops loop from response-start and
  cut cleanly to speech with no dead air and no lingering cue.

## Deployment

Ship to `main`, deploy to Pi, remove the now-inert `KOKORO_CUE_MS` from the Pi
`.env`, restart, and confirm the cue feel on a live turn.
