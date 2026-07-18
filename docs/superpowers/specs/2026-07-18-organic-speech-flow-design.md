# Organic speech flow: concise answers + pre-buffer + cue

**Date:** 2026-07-18
**Status:** Design — approved (cue style A), pending implementation plan
**Related:** `2026-07-18-kokoro-first-audio-flush-design.md` (first-flush tuning — rebalanced here); `2026-07-18-first-answer-latency-warmup-design.md` Outcome (identified Kokoro as the dominant leg)

## Problem

Kokoro TTS on the Pi CPU synthesizes ~1.15–1.4× **slower than real-time** and
one fragment at a time, so on multi-sentence answers playback outruns synthesis
and the speech **pauses** between fragments (measured: after a 24-char first
fragment giving 938ms of audio, fragment #2's 3200ms synth left a ~2.3s gap).
This is structural to Kokoro-on-CPU. Faster synthesis is not locally reachable:
`kokoro-onnx fp32` is the CPU floor, Piper's voice was rejected on quality, and
Kokoro will not compile to the Hailo-8L NPU (its iSTFT vocoder uses
data-dependent ops like `NonZero`/`ScatterND` that a static-shape accelerator
can't take — investigated 2026-07-18). Mason wants to keep TTS/STT local.

## Goal

Make speech **flow smoothly** without faster synthesis, by (a) making answers
short by default so there's less to synthesize, (b) building a small audio
pre-buffer so playback can't immediately outrun synthesis, and (c) masking the
pre-buffer's added start-delay with a short audio cue. Keep depth available on
explicit request.

**Insight that makes it work:** with synth ~1.2× real-time, a ~1.5s buffered
head-start covers roughly the next ~9s of playback before it could gap. If
answers are short (1–2 sentences ≈ a few seconds of audio), a modest pre-buffer
covers the **whole** answer → gapless. Long "elaborate" answers will still gap a
little; that's the accepted trade, since short is the default.

**Success criteria:**
1. Default spoken answers are 1–2 sentences; asking to "explain / elaborate / go
   deeper / tell me more" still yields a fuller answer.
2. Short answers play with no audible mid-answer pause on-device.
3. The pre-buffer's start-delay is covered by an audio cue (no silent dead air
   before speech).
4. Buffer depth and cue length are env-tunable (dial in on-device).

## Non-goals

- Faster synthesis / NPU offload / model swap (ruled out above).
- Guaranteeing zero gaps on long answers (structural; mitigated, not eliminated).
- Changing barge-in, wake, STT, or the greeting.

## Design (4 parts)

### 1. Concise-by-default answers, depth on request

Strengthen the spoken-brevity guidance in `core/prompt_builder.py`
`BASE_PROMPT_TEMPLATE` (the line at ~73, "Keep responses concise for spoken
delivery"). New guidance, roughly:

> Default to short, one-or-two-sentence answers — short and sweet. Only give a
> longer, detailed answer when Mason explicitly asks you to explain, elaborate,
> go deeper, or tell him more.

Claude infers "he wants depth" from the request's phrasing — no keyword routing.
The micro-tier prompt (`MICRO_TIER_TEMPLATE`) already says "reply briefly"; leave
it. This part is independent of the audio changes and also speeds every answer up.

### 2. Audio pre-buffer in the Kokoro writer

In `KokoroTTSBackend.speak_stream` (`core/voice_backends.py`), before the writer
thread makes its **first** `stream.write`, accumulate synthesized audio until the
buffered audio duration reaches a target (`KOKORO_PREBUFFER_MS`, default ~1500)
**or** the stream ends (SENTINEL) — whichever first. Then release/write normally.
The held-back lead is the jitter buffer that keeps playback from immediately
outrunning synthesis.

- Read `KOKORO_PREBUFFER_MS` per-backend in `__init__` (same pattern and rationale
  as `KOKORO_MIN_FIRST_FLUSH` — `KokoroONNXBackend.__init__` does not call
  `super().__init__()`, and reading at construction avoids the import-time env
  trap). Floor at 0 (0 = disabled = today's behavior).
- Buffered-duration is computed from sample counts already flowing through
  `audio_q` at `KOKORO_SAMPLE_RATE`.
- Interruability: barge-in already only starts its watcher once playback begins,
  so the pre-buffer window is naturally pre-playback and unaffected.

### 3. Pre-buffer audio cue (style A)

A short **sustained R2-D2 "here it comes" warble**, ~1–1.5s (`KOKORO_CUE_MS`-ish,
tunable), played the moment the response starts — i.e. hook the existing
first-delta callback (`main.py:351`, currently `voice.play_response_ready_sound`).
Replace/extend that cue so it fills the pre-buffer window instead of the current
~0.2s blip. Built from the existing numpy R2-D2 helpers in `core/voice.py`
(`_r2_chirp`/`_r2_beep`/`_r2_tail`), played non-blocking via `sd.play` like the
other cues. Size the cue ≈ the pre-buffer target so the warble and the start of
speech abut; brief overlap is acceptable (PipeWire mixes, per existing cue code).

### 4. Rebalance `KOKORO_MIN_FIRST_FLUSH`

With the pre-buffer now doing the smoothing, the tiny-first-fragment trick is no
longer needed. Raise the default from 20 back to **30** so the first fragment is
a clean, natural clause. (Still env-overridable from the prior spec.)

## Testing

- **Prompt (part 1):** unit-assert the new brevity/depth guidance text is present
  in `BASE_PROMPT_TEMPLATE` (a light regression like the persona tests). Behavior
  (short vs elaborate) is validated on-device.
- **Pre-buffer (part 2):** unit test `speak_stream` with a mocked pipeline/`sd`
  (as `test_kokoro_stream.py` does): with `KOKORO_PREBUFFER_MS` set, the writer
  makes no `stream.write` until buffered audio ≥ target (or SENTINEL), and with it
  at 0 behaves exactly as today. Env-wiring test like the flush one.
- **Cue (part 3):** unit test that the cue helper produces ~target-duration audio
  and is non-blocking (mock `sd`); assert it's fired on first delta.
- **Rebalance (part 4):** the flush tests already cover the mechanism; update the
  default-value assertion to 30.
- **On-device:** short answer → confirm no mid-answer pause and the cue covers the
  start; sweep `KOKORO_PREBUFFER_MS` / cue length for the best feel; confirm an
  "elaborate" request still gives depth.

## Deployment

Ship to `main`, deploy to Pi, then tune `KOKORO_PREBUFFER_MS` (and cue length) in
the Pi `.env`. `KOKORO_MIN_FIRST_FLUSH` default becomes 30; the Pi `.env` has no
override, so it picks up 30.
