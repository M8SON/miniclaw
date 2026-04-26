# Hailo Whisper Offload Design

Date: 2026-04-25

## Goal

Offload both MiniClaw speech-to-text paths to Hailo when available:

- wake-word detection (`whisper-tiny`)
- full utterance transcription (`whisper-base`)

MiniClaw should auto-detect Hailo support at startup, prefer the Hailo-backed Whisper path when it is ready, and otherwise fall back to the current CPU Whisper backend with a clear one-time startup warning.

Kokoro TTS remains unchanged in this phase.

## Scope

In scope:

- backend selection for STT at startup
- Hailo-backed Whisper backend abstraction
- startup detection and warning/reporting
- tests for selection and fallback behavior

Out of scope:

- Kokoro offload
- mixed-mode operation where wake uses Hailo and transcription uses CPU
- changes to orchestration, skills, or Docker execution
- model compilation workflow on the Pi

## Current State

MiniClaw currently routes all STT through `WhisperBackend` in `core/voice_backends.py`, which loads CPU Whisper models via `openai-whisper`. `VoiceInterface` is already backend-injected, so STT can be swapped without rewriting the microphone/session loop.

## Proposed Architecture

### Backend model

Keep the existing `VoiceInterface` as the control plane for:

- microphone capture
- wake loop timing
- silence detection
- playback calls

Add a second STT backend implementation beside the current CPU backend:

- `WhisperBackend` — existing CPU implementation
- `HailoWhisperBackend` — new Hailo-backed implementation

Both backends expose the same methods:

- `transcribe_wake_audio(audio_float) -> str`
- `transcribe_file(audio_file) -> str`

### Selection model

At startup, MiniClaw runs Hailo Whisper selection logic:

1. Check whether Hailo runtime/device access is available.
2. Check whether the required Hailo Whisper assets for both configured models exist.
3. Run a lightweight backend self-check.
4. If all checks pass, select `HailoWhisperBackend`.
5. Otherwise, select the existing CPU `WhisperBackend` and emit a clear startup warning.

V1 requires both configured STT models to be ready on Hailo. If either wake or transcription assets are missing, MiniClaw falls back entirely to CPU Whisper instead of mixing execution paths.

### Startup reporting

MiniClaw should print one startup line describing the selected STT backend.

Examples:

- `STT backend: Hailo Whisper (wake=tiny, transcription=base)`
- `STT backend: CPU Whisper fallback — Hailo runtime detected but Whisper assets missing`
- `STT backend: CPU Whisper fallback — Hailo runtime unavailable`

Warnings should be emitted once at startup, not on every wake cycle or transcription call.

## Components

### `core/voice_backends.py`

Responsibilities:

- keep `WhisperBackend`
- add `HailoWhisperBackend`
- optionally add shared STT backend protocol/base helper if useful
- add Hailo readiness/self-check helpers if they fit naturally here

### `main.py`

Responsibilities:

- decide which STT backend to instantiate before constructing `VoiceInterface`
- log/print the chosen backend
- pass the selected backend into `VoiceInterface`

### `core/voice.py`

Minimal or no structural change expected. It should continue calling the injected backend methods and remain agnostic to whether STT runs on CPU or Hailo.

## Fallback Rules

- If no Hailo device/runtime is available: use CPU Whisper.
- If Hailo is available but required Whisper assets are incomplete: use CPU Whisper.
- If Hailo initialization/self-check fails: use CPU Whisper.
- If CPU fallback is chosen, startup should explain why in one line.

Runtime fallback after startup is out of scope for V1. Backend choice is fixed for the process lifetime.

## Configuration

No new env var is required for V1.

Selection is automatic based on hardware/runtime/asset detection. Existing env vars for model names continue to apply:

- `WAKE_MODEL`
- `WHISPER_MODEL`

## Testing

Add focused unit coverage for:

- Hailo selected when runtime and both assets are available
- CPU fallback when Hailo runtime is unavailable
- CPU fallback when wake asset is missing
- CPU fallback when transcription asset is missing
- CPU fallback when Hailo self-check fails
- startup message reflects selected backend and reason

Existing voice harnesses remain backend-agnostic and should not require real Hailo hardware.

## Risks

### Hailo asset compatibility

The Hailo Whisper path depends on having usable compiled assets for both `tiny` and `base`. If only one model is readily usable, V1 will fall back entirely to CPU.

### Integration boundary

The Hailo inference path may require different preprocessing or decoding assumptions than `openai-whisper`. That complexity must stay inside `HailoWhisperBackend`; `VoiceInterface` should not absorb model-specific logic.

### Startup ambiguity

Silent fallback would make performance debugging difficult. The startup message is required to keep backend choice visible.

## Success Criteria

- MiniClaw auto-selects Hailo Whisper when fully available
- both wake detection and full transcription use the same selected backend family
- MiniClaw remains usable on non-Hailo systems through CPU fallback
- backend choice is obvious from startup output
- no changes are required to the voice conversation loop semantics
