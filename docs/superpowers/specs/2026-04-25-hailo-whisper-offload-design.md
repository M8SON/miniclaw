# Hailo Whisper Offload Design

Date: 2026-04-25

## Goal

Offload only MiniClaw's full-utterance transcription path to Hailo when available,
while keeping wake-word detection on the existing CPU Whisper path:

- wake-word detection stays on CPU `whisper-tiny`
- full utterance transcription may use Hailo for `whisper-base`

MiniClaw should auto-detect Hailo support at startup, prefer the Hailo-backed
transcription path when it is ready, and otherwise fall back to the current CPU
Whisper backend with a clear one-time startup warning.

Kokoro TTS remains unchanged in this phase.

## Scope

In scope:

- backend selection for full transcription at startup
- MiniClaw-owned Hailo-backed transcription abstraction
- startup detection and warning/reporting
- user-scoped Hailo asset location under `~/.miniclaw/models/hailo-whisper`
- tests for selection and fallback behavior

Out of scope:

- Kokoro offload
- wake-word detection offload
- changes to orchestration, skills, or Docker execution
- model compilation workflow on the Pi
- UDP transport or any sidecar/frontend process from the Seeed demo
- runtime dependency on a separate checked-out Seeed repository

## Current State

MiniClaw currently routes all STT through `WhisperBackend` in
`core/voice_backends.py`, which loads CPU Whisper models via `openai-whisper`.
`VoiceInterface` is already backend-injected, so STT can be swapped without
rewriting the microphone/session loop.

There is already a draft backend-selection seam in progress in
`core/voice_backends.py` and `main.py`, but it still assumes Hailo may own both
the wake and full-transcription paths. This design narrows that seam to
MiniClaw's actual first-phase needs.

## Proposed Architecture

### Backend model

Keep the existing `VoiceInterface` as the control plane for:

- microphone capture
- wake loop timing
- silence detection
- playback calls

Keep the existing CPU `WhisperBackend`, but use it in hybrid fashion:

- `transcribe_wake_audio(audio_float)` remains CPU Whisper
- `transcribe_file(audio_file)` may route to Hailo when ready

MiniClaw should own a focused runtime helper module for Hailo transcription:

- `core/hailo_whisper_runtime.py` — local wrapper around Hailo inference needs
- no UDP, no external daemon, no sidecar frontend
- Seeed's project may inform design, but MiniClaw's module is its own
  implementation

The injected STT backend still exposes the same methods:

- `transcribe_wake_audio(audio_float) -> str`
- `transcribe_file(audio_file) -> str`

The simplest V1 shape is either:

- a hybrid `WhisperBackend` that keeps CPU wake and conditionally delegates only
  `transcribe_file()`, or
- a small wrapper backend such as `HybridWhisperBackend` that composes CPU wake
  plus Hailo transcription

Either is acceptable as long as `VoiceInterface` stays unaware of the split.

### Selection model

At startup, MiniClaw runs Hailo Whisper selection logic:

1. Check whether Hailo runtime/device access is available.
2. Check whether the configured transcription model variant is supported by the
   Hailo path.
3. Check whether the required local Hailo assets for the transcription model
   exist.
4. Run a lightweight backend self-check.
5. If all checks pass, enable Hailo only for `transcribe_file()`.
6. Otherwise, use the existing CPU `WhisperBackend` for both methods and emit a
   clear startup warning.

V1 does not require the wake model to exist on Hailo, and it must not try to
offload the wake loop.

### Asset model

MiniClaw should treat this user-scoped directory as canonical:

- `~/.miniclaw/models/hailo-whisper`

Within that root, the runtime may organize model-specific assets however it
needs, but the storage must remain user-owned and local to MiniClaw rather than
under `/opt`.

### Startup reporting

MiniClaw should print one startup line describing the selected STT backend.

Examples:

- `STT backend: Hybrid Whisper (wake=cpu:tiny, transcription=hailo:base)`
- `STT backend: CPU Whisper fallback — Hailo transcription assets missing`
- `STT backend: CPU Whisper fallback — Hailo runtime unavailable`

Warnings should be emitted once at startup, not on every wake cycle or
transcription call.

## Components

### `core/voice_backends.py`

Responsibilities:

- keep `WhisperBackend`
- add a backend shape that preserves CPU wake and conditionally enables Hailo
  transcription
- optionally add shared STT backend protocol/base helper if useful
- keep Hailo selection/readiness logic at this seam

### `core/hailo_whisper_runtime.py`

Responsibilities:

- own MiniClaw's local Hailo transcription integration
- encapsulate Hailo-specific asset lookup, runtime setup, and inference calls
- present a narrow Python API to `core/voice_backends.py`
- avoid leaking vendor-demo concepts like UDP hosts, ports, or external
  frontend coupling

### `main.py`

Responsibilities:

- decide which STT backend to instantiate before constructing `VoiceInterface`
- log/print the chosen backend
- pass the selected backend into `VoiceInterface`

### `core/voice.py`

Minimal or no structural change expected. It should continue calling the
injected backend methods and remain agnostic to whether full transcription runs
on CPU or Hailo.

## Fallback Rules

- If no Hailo device/runtime is available: use CPU Whisper.
- If the configured transcription model is unsupported by MiniClaw's Hailo
  path: use CPU Whisper.
- If Hailo is available but required transcription assets are incomplete: use
  CPU Whisper.
- If Hailo initialization/self-check fails: use CPU Whisper.
- If CPU fallback is chosen, startup should explain why in one line.

Runtime fallback after startup is out of scope for V1. Backend choice is fixed
for the process lifetime.

## Configuration

No new env var is required for V1.

Selection is automatic based on hardware/runtime/asset detection. Existing env
vars for model names continue to apply:

- `WAKE_MODEL`
- `WHISPER_MODEL`

`WAKE_MODEL` continues to drive the CPU wake detector only. `WHISPER_MODEL`
controls the full-transcription model and is the only model that matters for
Hailo selection in V1.

## Testing

Add focused unit coverage for:

- Hailo selected for transcription when runtime and transcription assets are
  available
- CPU fallback when Hailo runtime is unavailable
- CPU fallback when transcription asset is missing
- CPU fallback when the configured transcription model variant is unsupported
- CPU fallback when Hailo self-check fails
- startup message reflects selected backend and reason
- wake transcription path still stays on CPU in the hybrid configuration

Existing voice harnesses remain backend-agnostic and should not require real
Hailo hardware.

## Risks

### Hailo asset compatibility

The Hailo Whisper path depends on having usable compiled assets for the selected
full-transcription model, likely `base` first. Variant support should be
explicit and conservative rather than assumed.

### Integration boundary

The Hailo inference path may require different preprocessing or decoding
assumptions than `openai-whisper`. That complexity must stay inside the Hailo
runtime module and backend seam; `VoiceInterface` should not absorb
model-specific logic.

### Startup ambiguity

Silent fallback would make performance debugging difficult. The startup message
is required to keep backend choice visible.

### Vendor-reference drift

Seeed's demo can change independently of MiniClaw. MiniClaw should borrow
ideas, not mirror their runtime shape or depend on their repository layout
staying stable.

## Success Criteria

- MiniClaw auto-selects Hailo-backed transcription when fully available
- wake detection remains on the proven CPU Whisper path
- full transcription can offload locally on the Pi without UDP or external
  helper services
- MiniClaw remains usable on non-Hailo systems through CPU fallback
- backend choice is obvious from startup output
- no changes are required to the voice conversation loop semantics
