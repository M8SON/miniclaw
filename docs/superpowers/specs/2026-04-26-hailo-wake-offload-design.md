# Hailo Wake Offload Design

Date: 2026-04-26

## Goal

Offload MiniClaw's always-listening wake-word transcription path to Hailo when
available, while preserving the current configurable `WAKE_PHRASE` behavior and
leaving all post-wake routing and response logic unchanged.

Specifically:

- wake detection should continue to work by transcribing a sliding audio window
  and checking whether `WAKE_PHRASE` appears in the transcript
- no dedicated keyword-spotting model should be introduced
- TierRouter, orchestrator behavior, and Kokoro TTS are out of scope

## Scope

In scope:

- Hailo-backed `transcribe_wake_audio(audio_float)` support
- startup selection that independently evaluates wake and full-transcription
  Hailo readiness
- separate wake/transcription status reporting in one startup line
- tests covering mixed-mode backend combinations

Out of scope:

- dedicated wake-word detector models
- changes to `WAKE_PHRASE` semantics
- TierRouter/orchestrator/TTS changes
- Kokoro Hailo offload
- replacing the existing Hailo full-transcription design

## Current State

MiniClaw already supports Hailo-backed full post-wake transcription through a
hybrid backend:

- wake detection stays on CPU Whisper
- full transcription may run on Hailo when runtime and assets are present

`VoiceInterface` already has the right seam: it feeds audio windows into
`transcribe_wake_audio()` and hands full recorded utterances into
`transcribe_file()`. That means wake offload should extend the backend/runtime
layer, not alter the control flow in `core/voice.py`.

## Proposed Architecture

### Control-flow boundary

Keep `VoiceInterface` functionally unchanged.

Wake-word flow should remain:

1. microphone captures a 2-second sliding window
2. STT backend transcribes that window
3. MiniClaw checks whether `WAKE_PHRASE` appears in the transcript
4. on match, MiniClaw enters active listening mode

Post-wake flow should remain:

1. record the user's actual utterance
2. full transcription
3. TierRouter decides direct | ollama | claude
4. response is generated
5. Kokoro TTS speaks the response

Wake offload must affect only step 2 of the wake-word flow.

### Backend model

MiniClaw should keep a single STT backend seam with two public methods:

- `transcribe_wake_audio(audio_float) -> str`
- `transcribe_file(audio_file) -> str`

The backend should be able to support four startup-selected combinations:

- `wake=cpu`, `transcription=cpu`
- `wake=hailo`, `transcription=cpu`
- `wake=cpu`, `transcription=hailo`
- `wake=hailo`, `transcription=hailo`

The recommended implementation shape is to extend the current
`HybridWhisperBackend` so it can independently decide:

- whether wake windows use CPU Whisper or Hailo
- whether full recorded utterances use CPU Whisper or Hailo

`VoiceInterface` should remain unaware of which combination is active.

### Runtime model

`core/hailo_whisper_runtime.py` should grow wake-specific support in addition to
the existing one-shot file transcription path.

It should expose enough functionality to support:

- wake-window mel preprocessing from in-memory float audio
- model/asset resolution for the wake model variant
- one-shot wake transcription returning plain text

It should not require a separate wake-specific keyword detector. The wake path
must still produce text, because `WAKE_PHRASE` matching stays exactly as it is
today.

### Selection model

Startup should independently evaluate Hailo readiness for the configured
`WAKE_MODEL` and `WHISPER_MODEL`.

Selection logic should look like:

1. Check whether Hailo runtime/device access exists at all.
2. If runtime is unavailable, select CPU for both wake and transcription.
3. For wake:
   - check whether `WAKE_MODEL` is supported by the Hailo path
   - check whether wake assets exist
   - run a wake self-check
   - if all pass, select Hailo wake; otherwise select CPU wake
4. For transcription:
   - preserve the current Hailo transcription checks
   - select Hailo transcription only if supported/assets/self-check pass
5. Construct one backend that reflects the selected combination.

Wake fallback should be independent of transcription fallback. A failure in one
path must not force the other path back to CPU if it is otherwise valid.

### Asset model

Wake and transcription assets should continue to live under the same MiniClaw
root:

- `~/.miniclaw/models/hailo-whisper`

The runtime may use per-model subdirectories beneath that root. Wake assets will
typically correspond to `tiny` or `tiny.en`, while transcription assets may
correspond to `base`.

### Startup reporting

MiniClaw should print one truthful startup line with both backend selections.

Examples:

- `STT backend: Hybrid Whisper (wake=hailo:tiny, transcription=hailo:base)`
- `STT backend: Hybrid Whisper (wake=cpu:tiny, transcription=hailo:base)`
- `STT backend: Hybrid Whisper (wake=hailo:tiny, transcription=cpu:base)`
- `STT backend: CPU Whisper fallback (wake=cpu:tiny, transcription=cpu:base) — Hailo runtime unavailable`

This is important because wake and transcription may now fall back separately.

## Components

### `core/voice_backends.py`

Responsibilities:

- keep the STT provider seam
- extend backend construction to choose wake and transcription independently
- keep all wake/transcription fallback policy centralized
- produce a clear startup status line

### `core/hailo_whisper_runtime.py`

Responsibilities:

- resolve wake assets separately from transcription assets
- support one-shot wake-window inference from in-memory audio
- keep Hailo-specific preprocessing/tokenization/HEF logic isolated

### `main.py`

Responsibilities:

- remain a thin startup-selection seam
- print the final status line
- pass the selected STT backend into `VoiceInterface`

### `core/voice.py`

Expected change: none or near-zero. It should continue to:

- capture audio windows
- call `transcribe_wake_audio()`
- compare returned text with `WAKE_PHRASE`

## Fallback Rules

- If no Hailo runtime/device is available: CPU wake and CPU transcription.
- If wake assets are unsupported or missing: CPU wake only.
- If transcription assets are unsupported or missing: CPU transcription only.
- If wake self-check fails: CPU wake only.
- If transcription self-check fails: CPU transcription only.
- One path failing should not automatically demote the other.

## Configuration

No new env vars are required.

Existing env vars continue to apply:

- `WAKE_MODEL`
- `WHISPER_MODEL`
- `WAKE_PHRASE`

`WAKE_PHRASE` semantics must remain unchanged.

## Testing

Add focused coverage for:

- Hailo wake selected when wake runtime/assets/self-check are ready
- CPU wake fallback when wake assets are missing
- CPU wake fallback when wake model variant is unsupported
- mixed mode `wake=hailo, transcription=cpu`
- mixed mode `wake=cpu, transcription=hailo`
- full Hailo `wake=hailo, transcription=hailo`
- startup line reflects both selections truthfully
- `VoiceInterface` behavior remains unchanged at the call boundary

Tests should not require real Hailo hardware.

## Risks

### Wake latency sensitivity

Wake detection is the always-listening loop. Regressions here are more visible
than in post-wake transcription. Startup self-check and conservative fallback
matter more than raw offload ambition.

### Asset mismatch

Wake and transcription likely use different model variants (`tiny` vs `base`).
The runtime must not assume they share identical assets or chunk sizing.

### Over-coupling the control plane

Wake offload must not leak Hailo selection logic into `core/voice.py`. If it
does, the wake loop becomes harder to test and future backend changes become
more brittle.

## Success Criteria

- MiniClaw can offload the wake transcription loop to Hailo when ready
- `WAKE_PHRASE` remains fully configurable and transcript-based
- post-wake routing and TTS behavior are unchanged
- wake and transcription can fall back independently
- startup output clearly reports the actual combination in use
