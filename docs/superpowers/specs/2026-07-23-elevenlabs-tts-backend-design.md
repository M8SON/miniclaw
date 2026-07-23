# ElevenLabs cloud TTS backend (with Kokoro fallback)

**Date:** 2026-07-23
**Status:** Design — approved, pending implementation plan
**Related:** `2026-07-18-first-answer-latency-warmup-design.md` and
`2026-07-18-kokoro-first-audio-flush-design.md` (both identified Kokoro
time-to-first-audio as the dominant, most-variable leg of the first-answer delay)

## Problem

Kokoro TTS on the Pi 5 CPU synthesizes ~1.15–1.4x slower than realtime and has
no in-fragment streaming, so time-to-first-audio is the biggest and most
*variable* leg of a voice response (measured 1.5–6s on-device). Offloading
Kokoro to the Hailo-8L was investigated on 2026-07-18 and found **not viable**
(the iSTFT vocoder uses dynamic ops that can't compile to a `.hef`). Local levers
(first-flush tuning) have been exhausted; the remaining lever is a cloud TTS
provider with sub-100ms streaming first-audio.

Provider comparison (2026-07-23) selected **ElevenLabs Flash v2.5** — ~75ms
first-audio (fastest commercial), best voice quality, ~$0.045/min (a few dollars
per month at realistic use). It directly attacks the first-audio latency *and its
variance*. STT stays local on the Hailo-8L; only speech-out moves to cloud.

## Goal

Add ElevenLabs as a selectable TTS backend that:

1. Streams first-audio in ~75ms via the Flash v2.5 model.
2. Reuses the existing `speak_stream` pipeline (sentence flush, synth/writer
   threads, barge-in, R2-D2 cue-stop) with minimal new code.
3. Falls back to local Kokoro at **startup** when unavailable (no API key,
   import failure, or connectivity/self-check failure), with a visible status
   line — never a silent downgrade.

Success criteria: `TTS_BACKEND=elevenlabs` produces streamed speech on-device via
ElevenLabs; barge-in still cuts playback mid-response; a missing/invalid key or
no connectivity at launch falls back to `kokoro-onnx` and says so in the startup
status line. Unit tests cover synth conversion, barge-in stream teardown, and the
fallback path with no live API calls.

## Approach

Chosen over a dedicated WebSocket `stream-input` implementation (Approach B,
below). Approach A subclasses `KokoroTTSBackend` and overrides a single method,
reusing the whole parallel pipeline.

### 1. `ElevenLabsTTSBackend` — `core/voice_backends.py`

Subclass of `KokoroTTSBackend`; overrides only `_synth_audio(text)`, the
documented extension point (same seam `KokoroONNXBackend` uses).

```python
class ElevenLabsTTSBackend(KokoroTTSBackend):
    sample_rate = KOKORO_SAMPLE_RATE  # 24000 — request pcm_24000 to match
    PREBUFFER_MS = 0                  # ElevenLabs streams faster than realtime;
                                      # Kokoro's 1500ms prebuffer would re-add latency

    def __init__(self, voice_id, api_key, model_id="eleven_flash_v2_5",
                 output_device=None, output_samplerate=None, speed=1.0):
        # Build the ElevenLabs client; DO NOT call KokoroTTSBackend.__init__
        # (no Kokoro pipeline to load). Set the attributes speak_stream/speak
        # read: voice(_id), output_device, output_samplerate, MIN_FIRST_FLUSH,
        # PREBUFFER_MS (via _configured_int, same as the Kokoro backends).

    def _synth_audio(self, text):
        audio_stream = self._client.text_to_speech.stream(
            voice_id=self._voice_id, text=text,
            model_id=self._model_id, output_format="pcm_24000",
        )
        try:
            for chunk in audio_stream:               # bytes, as they arrive
                if isinstance(chunk, bytes) and chunk:
                    yield np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
        finally:
            close = getattr(audio_stream, "close", None)
            if callable(close):
                close()
```

**Why the format is `pcm_24000`:** it matches `KOKORO_SAMPLE_RATE` (24000)
exactly, so the pipeline's existing `resample(audio, KOKORO_SAMPLE_RATE,
output_samplerate)` calls work unchanged. Raw PCM also avoids an mp3-decode step
on the first-audio path. Only work needed is int16-bytes → float32-in-[-1,1].

**Barge-in correctness (inherited, no new wiring):** `synth_worker` iterates
`for audio in self._synth_audio(sent)` and `break`s on `interrupt_event`.
`.stream()` returns a **plain iterator, not a context manager**, so breaking out
of `_synth_audio` raises `GeneratorExit` inside it and runs the `finally`, which
calls the underlying stream's `.close()` if present (tearing down the HTTP
connection). The `try/finally` is therefore load-bearing for clean barge-in.

**Inherited for free:** `speak()` (greetings / fixed strings), `speak_stream()`
(LLM replies), sentence-flush, `on_first_audio` cue-stop, device/sample-rate
resolution, prebuffer machinery.

### 2. Selection + startup self-check — `main._build_tts_backend()`

Add a `TTS_BACKEND=elevenlabs` branch alongside `kokoro` / `kokoro-onnx`.

- Read `ELEVENLABS_API_KEY`; if unset → fall back (see below).
- Construct the backend, then run a **cheap self-check** that validates the key
  and connectivity by making a **minimal 1-character `text_to_speech.stream`
  call and consuming its first chunk** (uses only the confirmed SDK method; no
  reliance on unverified endpoints), mirroring the intent of
  `hailo_transcription_self_check`.
- On any failure (missing key, `ImportError` for the `elevenlabs` package, or
  self-check raising) → **fall back to `kokoro-onnx`** and return a status
  message that names the reason. This follows the existing rule that the active
  backend is always printed at startup so a silent "still on Kokoro" is
  impossible.
- `output_device` / `output_samplerate` are resolved up front (as today) and
  passed in, so the backend opens its `OutputStream` against the same device the
  rest of the pipeline uses (USB DACs that reject 24kHz still work via resample).

Fallback is **startup-only** (explicit decision). A mid-session API error is
handled by the existing per-flush `try/except` in `synth_worker` (logs, drops
that fragment). Runtime per-utterance fallback is out of scope for v1.

### 3. Config

Add to `.env.example` and document in `CLAUDE.md`'s env table:

| Variable | Default | Notes |
|---|---|---|
| `TTS_BACKEND` | `kokoro` | add `elevenlabs` as an accepted value |
| `ELEVENLABS_API_KEY` | — | required for the elevenlabs backend; absent → Kokoro fallback |
| `ELEVENLABS_VOICE_ID` | `onwK4e9ZLuTAKqWW03F9` (Daniel — British, authoritative, closest to "Jarvis") | swappable; George = `JBFqnCBsd6RMkjVDRZzb` (warmer British) |
| `ELEVENLABS_MODEL_ID` | `eleven_flash_v2_5` | the ~75ms Flash model |

Add `elevenlabs` to `requirements.txt`.

### 4. Testing — `tests/test_voice_backends.py` (+ selection test)

No live API calls. With the `elevenlabs` SDK mocked:

- `_synth_audio` converts fake PCM int16 bytes into float32 numpy in [-1, 1] at
  24kHz, yielding one array per streamed chunk.
- Barge-in: setting `interrupt_event` and breaking the consumer closes the
  stream — assert the context manager's `__exit__` (or the mock's `.close()`)
  was called.
- `_build_tts_backend` falls back to `kokoro-onnx` when `ELEVENLABS_API_KEY` is
  unset and when the self-check raises, and the returned status string names the
  reason.

## Rejected / out of scope

- **Approach B — WebSocket `stream-input`** feeding LLM deltas straight to
  ElevenLabs (no sentence segmentation). Lowest theoretical latency but
  duplicates the threaded pipeline, re-implements barge-in, and hits the Flash
  text-normalization Enterprise caveat. Possible future optimization; not v1.
- **Runtime per-utterance fallback** (re-synthesize a failed sentence with a
  reserve Kokoro instance). Deferred; startup-only fallback chosen.
- **Cost metering / rate guards.** Not needed at expected volume.
- **STT changes.** STT stays on the Hailo-8L, untouched. (A separate future-work
  note covers compiling a larger Whisper `.hef`.)
