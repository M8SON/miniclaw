# Hailo Whisper Offload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Offload only MiniClaw's full-transcription path to Hailo when the Pi runtime is ready, while keeping wake detection on CPU Whisper and preserving a clean CPU fallback.

**Architecture:** Keep `VoiceInterface` unchanged as the microphone/session control plane. Introduce a MiniClaw-owned Hailo runtime module for full transcription only, then select a hybrid STT backend in `core/voice_backends.py` that always uses CPU for `transcribe_wake_audio()` and conditionally uses Hailo for `transcribe_file()`. Startup selection in `main.py` performs Hailo readiness checks once and prints one exact backend status line.

**Tech Stack:** Python 3.11+, `openai-whisper`, Pi-installed Hailo runtime packages, `pytest`, existing MiniClaw voice harnesses.

---

## File Map

- `core/voice_backends.py`
  - Keep `WhisperBackend`
  - Add user-scoped Hailo asset constants and readiness helpers
  - Add a hybrid backend path that preserves CPU wake and conditionally enables Hailo transcription
- `core/hailo_whisper_runtime.py`
  - New MiniClaw-owned Hailo transcription runtime wrapper
  - Encapsulate asset lookup, backend self-check, and transcription entrypoint
- `main.py`
  - Keep startup wiring thin
  - Print one backend status line and pass selected STT backend into `VoiceInterface`
- `tests/test_main_voice_backend_selection.py`
  - Verify the selected backend is passed through `build_voice_interface()`
  - Verify the startup status line is printed once
- `tests/test_voice_backends.py`
  - Cover Hailo readiness selection, user-scoped asset path, unsupported variants, self-check fallback, and hybrid behavior

## Task 1: Lock the startup seam in `main.py`

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_voice_backend_selection.py`

- [ ] **Step 1: Replace the startup test file with the approved expectations**

```python
import unittest
from unittest.mock import patch

import main


class BuildVoiceInterfaceSelectionTests(unittest.TestCase):
    @patch("core.voice.VoiceInterface")
    @patch("main.build_stt_backend")
    def test_build_voice_interface_passes_selected_backend(
        self, mock_build_stt_backend, mock_voice_interface
    ):
        fake_backend = object()
        mock_build_stt_backend.return_value = (
            fake_backend,
            "STT backend: Hybrid Whisper (wake=cpu:tiny, transcription=hailo:base)",
        )

        main.build_voice_interface()

        _, kwargs = mock_voice_interface.call_args
        self.assertIs(kwargs["stt_backend"], fake_backend)

    @patch("builtins.print")
    @patch("core.voice.VoiceInterface")
    @patch("main.build_stt_backend")
    def test_build_voice_interface_prints_backend_status_once(
        self, mock_build_stt_backend, mock_voice_interface, mock_print
    ):
        fake_backend = object()
        message = "STT backend: CPU Whisper fallback — Hailo runtime unavailable"
        mock_build_stt_backend.return_value = (fake_backend, message)

        main.build_voice_interface()

        mock_print.assert_called_once_with(message)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the startup tests**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_main_voice_backend_selection.py -v`

Expected: `PASS` if the current `main.py` wiring still matches the seam; otherwise fix `main.py` before continuing.

- [ ] **Step 3: Ensure `main.py` stays as a thin selector**

`main.py` should contain this shape in `build_voice_interface()`:

```python
from core.voice_backends import build_stt_backend


def build_voice_interface():
    from core.voice import VoiceInterface

    wake_phrase = os.getenv("WAKE_PHRASE", "computer")
    stt_backend, stt_status = build_stt_backend(
        wake_model=os.getenv("WAKE_MODEL", "tiny"),
        transcription_model=os.getenv("WHISPER_MODEL", "base"),
    )
    print(stt_status)

    return VoiceInterface(
        whisper_model=os.getenv("WHISPER_MODEL", "base"),
        wake_model=os.getenv("WAKE_MODEL", "tiny"),
        wake_phrase=wake_phrase,
        enable_tts=os.getenv("ENABLE_TTS", "true").lower() == "true",
        tts_voice=os.getenv("TTS_VOICE", "af_heart"),
        tts_speed=float(os.getenv("TTS_SPEED", "1.2")),
        silence_threshold=int(os.getenv("SILENCE_THRESHOLD", "1000")),
        silence_duration=float(os.getenv("SILENCE_DURATION", "2.0")),
        stt_backend=stt_backend,
    )
```

- [ ] **Step 4: Re-run the startup tests**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_main_voice_backend_selection.py -v`

Expected: `2 passed`

- [ ] **Step 5: Commit the seam checkpoint**

```bash
cd /home/daedalus/linux/miniclaw
git add main.py tests/test_main_voice_backend_selection.py
git commit -m "test(voice): lock startup STT backend selection seam"
```

## Task 2: Rewrite `tests/test_voice_backends.py` around the approved hybrid model

**Files:**
- Modify: `tests/test_voice_backends.py`

- [ ] **Step 1: Replace the test file with transcription-only expectations**

```python
import unittest
from pathlib import Path
from unittest.mock import patch

from core import voice_backends


class BuildSttBackendTests(unittest.TestCase):
    @patch("core.voice_backends.WhisperBackend")
    @patch("core.voice_backends.hailo_runtime_available", return_value=False)
    def test_falls_back_to_cpu_when_hailo_runtime_unavailable(
        self, mock_runtime, mock_whisper_backend
    ):
        cpu_backend = object()
        mock_whisper_backend.return_value = cpu_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, cpu_backend)
        self.assertEqual(
            message, "STT backend: CPU Whisper fallback — Hailo runtime unavailable"
        )

    @patch("core.voice_backends.WhisperBackend")
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_falls_back_to_cpu_when_transcription_variant_not_supported(
        self, mock_runtime, mock_whisper_backend
    ):
        cpu_backend = object()
        mock_whisper_backend.return_value = cpu_backend

        backend, message = voice_backends.build_stt_backend("tiny", "small")

        self.assertIs(backend, cpu_backend)
        self.assertEqual(
            message,
            "STT backend: CPU Whisper fallback — transcription model variant unsupported by Hailo",
        )

    @patch("core.voice_backends.WhisperBackend")
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(False, "transcription model asset missing"),
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_falls_back_to_cpu_when_transcription_asset_missing(
        self, mock_runtime, mock_assets, mock_whisper_backend
    ):
        cpu_backend = object()
        mock_whisper_backend.return_value = cpu_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, cpu_backend)
        self.assertEqual(
            message,
            "STT backend: CPU Whisper fallback — transcription model asset missing",
        )

    @patch("core.voice_backends.WhisperBackend")
    @patch(
        "core.voice_backends.HailoTranscriptionRuntime.self_check",
        side_effect=RuntimeError("self-check failed"),
    )
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(True, ""),
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_falls_back_to_cpu_when_hailo_self_check_fails(
        self, mock_runtime, mock_assets, mock_self_check, mock_whisper_backend
    ):
        cpu_backend = object()
        mock_whisper_backend.return_value = cpu_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, cpu_backend)
        self.assertEqual(
            message, "STT backend: CPU Whisper fallback — Hailo self-check failed"
        )

    @patch("core.voice_backends.HybridWhisperBackend")
    @patch("core.voice_backends.HailoTranscriptionRuntime.self_check", return_value=None)
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(True, ""),
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_selects_hybrid_backend_when_runtime_and_assets_are_ready(
        self, mock_runtime, mock_assets, mock_self_check, mock_hybrid_backend
    ):
        hybrid_backend = object()
        mock_hybrid_backend.return_value = hybrid_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, hybrid_backend)
        self.assertEqual(
            message,
            "STT backend: Hybrid Whisper (wake=cpu:tiny, transcription=hailo:base)",
        )

    def test_asset_root_is_user_scoped(self):
        self.assertEqual(
            voice_backends.HAILO_WHISPER_ASSET_ROOT,
            Path.home() / ".miniclaw" / "models" / "hailo-whisper",
        )


class HybridWhisperBackendTests(unittest.TestCase):
    @patch("core.voice_backends.HailoTranscriptionRuntime")
    @patch("core.voice_backends.whisper.load_model")
    def test_wake_audio_stays_on_cpu_but_file_transcription_uses_hailo(
        self, mock_load_model, mock_runtime_cls
    ):
        wake_model = unittest.mock.Mock()
        transcription_model = unittest.mock.Mock()
        wake_model.transcribe.return_value = {"text": "Computer"}
        mock_load_model.side_effect = [wake_model, transcription_model]

        runtime = mock_runtime_cls.return_value
        runtime.transcribe_file.return_value = "transcribed by hailo"

        backend = voice_backends.HybridWhisperBackend(
            wake_model="tiny",
            transcription_model="base",
        )

        wake_text = backend.transcribe_wake_audio([0.0, 0.1])
        file_text = backend.transcribe_file("/tmp/example.wav")

        self.assertEqual(wake_text, "computer")
        self.assertEqual(file_text, "transcribed by hailo")
        runtime.transcribe_file.assert_called_once_with("/tmp/example.wav")
        self.assertFalse(transcription_model.transcribe.called)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the backend tests and confirm they fail for the right reasons**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_voice_backends.py -v`

Expected: failures because `hailo_transcription_assets_available`, `HybridWhisperBackend`, and `HailoTranscriptionRuntime` do not exist yet, and the current constants/messages still reflect the old all-Hailo design.

- [ ] **Step 3: Commit the red test definition**

```bash
cd /home/daedalus/linux/miniclaw
git add tests/test_voice_backends.py
git commit -m "test(voice): define hybrid hailo transcription behavior"
```

## Task 3: Add a MiniClaw-owned Hailo runtime wrapper

**Files:**
- Create: `core/hailo_whisper_runtime.py`
- Test: `tests/test_voice_backends.py`

- [ ] **Step 1: Add the runtime wrapper module**

Create `core/hailo_whisper_runtime.py` with:

```python
from __future__ import annotations

from pathlib import Path


class HailoTranscriptionRuntime:
    def __init__(self, model_name: str, assets_root: Path):
        self.model_name = model_name
        self.assets_root = assets_root
        self.model_dir = assets_root / model_name

    @classmethod
    def self_check(cls, model_name: str, assets_root: Path) -> None:
        runtime = cls(model_name=model_name, assets_root=assets_root)
        if not runtime.model_dir.exists():
            raise RuntimeError("transcription model asset missing")

    def transcribe_file(self, audio_file: str) -> str:
        raise NotImplementedError(
            "Hailo transcription runtime integration must be implemented on Pi hardware"
        )
```

- [ ] **Step 2: Run the backend tests again**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_voice_backends.py -v`

Expected: failures move forward to `core.voice_backends` because the runtime wrapper now exists but the hybrid backend and new selector logic do not.

- [ ] **Step 3: Commit the runtime scaffold**

```bash
cd /home/daedalus/linux/miniclaw
git add core/hailo_whisper_runtime.py
git commit -m "feat(voice): add miniclaw-owned hailo runtime scaffold"
```

## Task 4: Implement the hybrid backend and selection helpers

**Files:**
- Modify: `core/voice_backends.py`
- Test: `tests/test_voice_backends.py`

- [ ] **Step 1: Update constants, imports, and supported variants**

Near the top of `core/voice_backends.py`, make these changes:

```python
import logging
from pathlib import Path
from typing import Protocol

import sounddevice as sd
import whisper
from kokoro import KPipeline

from core.hailo_whisper_runtime import HailoTranscriptionRuntime

logger = logging.getLogger(__name__)

KOKORO_SAMPLE_RATE = 24000
HAILO_WHISPER_ASSET_ROOT = Path.home() / ".miniclaw" / "models" / "hailo-whisper"
SUPPORTED_HAILO_WHISPER_TRANSCRIPTION_VARIANTS = {"base", "tiny", "tiny.en", "base.en"}
```

- [ ] **Step 2: Add a focused asset helper for transcription only**

Replace the old asset helper with:

```python
def hailo_transcription_assets_available(transcription_model: str) -> tuple[bool, str]:
    transcription_dir = HAILO_WHISPER_ASSET_ROOT / transcription_model
    if not transcription_dir.exists():
        return False, "transcription model asset missing"
    return True, ""
```

- [ ] **Step 3: Replace the placeholder Hailo backend with a hybrid backend**

Replace the `HailoWhisperBackend` class with:

```python
class HybridWhisperBackend:
    """CPU wake-word detection plus optional Hailo full transcription."""

    def __init__(self, wake_model: str, transcription_model: str):
        logger.info("Loading Whisper wake model: %s", wake_model)
        self.wake_model = whisper.load_model(wake_model)
        logger.info("Loading Whisper transcription fallback model: %s", transcription_model)
        self.transcription_model = whisper.load_model(transcription_model)
        self.hailo_runtime = HailoTranscriptionRuntime(
            model_name=transcription_model,
            assets_root=HAILO_WHISPER_ASSET_ROOT,
        )

    def transcribe_wake_audio(self, audio_float) -> str:
        result = self.wake_model.transcribe(
            audio_float,
            language="en",
            fp16=False,
        )
        return result["text"].lower().strip()

    def transcribe_file(self, audio_file: str) -> str:
        return self.hailo_runtime.transcribe_file(audio_file).strip()
```

- [ ] **Step 4: Rewrite `build_stt_backend()` to follow the approved fallback rules**

Replace `build_stt_backend()` with:

```python
def build_stt_backend(
    wake_model: str, transcription_model: str
) -> tuple[SttBackend, str]:
    if not hailo_runtime_available():
        return (
            WhisperBackend(
                wake_model=wake_model, transcription_model=transcription_model
            ),
            "STT backend: CPU Whisper fallback — Hailo runtime unavailable",
        )

    if transcription_model not in SUPPORTED_HAILO_WHISPER_TRANSCRIPTION_VARIANTS:
        return (
            WhisperBackend(
                wake_model=wake_model, transcription_model=transcription_model
            ),
            "STT backend: CPU Whisper fallback — transcription model variant unsupported by Hailo",
        )

    assets_ok, reason = hailo_transcription_assets_available(transcription_model)
    if not assets_ok:
        return (
            WhisperBackend(
                wake_model=wake_model, transcription_model=transcription_model
            ),
            f"STT backend: CPU Whisper fallback — {reason}",
        )

    try:
        HailoTranscriptionRuntime.self_check(
            model_name=transcription_model,
            assets_root=HAILO_WHISPER_ASSET_ROOT,
        )
        backend = HybridWhisperBackend(
            wake_model=wake_model,
            transcription_model=transcription_model,
        )
    except Exception:
        return (
            WhisperBackend(
                wake_model=wake_model, transcription_model=transcription_model
            ),
            "STT backend: CPU Whisper fallback — Hailo self-check failed",
        )

    return (
        backend,
        f"STT backend: Hybrid Whisper (wake=cpu:{wake_model}, transcription=hailo:{transcription_model})",
    )
```

- [ ] **Step 5: Run the backend tests**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_voice_backends.py -v`

Expected: all tests in `tests/test_voice_backends.py` pass.

- [ ] **Step 6: Commit the hybrid selector**

```bash
cd /home/daedalus/linux/miniclaw
git add core/voice_backends.py tests/test_voice_backends.py
git commit -m "feat(voice): select hybrid hailo transcription backend"
```

## Task 5: Cross-check startup wiring against the new backend messages

**Files:**
- Modify: `tests/test_main_voice_backend_selection.py` if needed
- Test: `tests/test_main_voice_backend_selection.py`

- [ ] **Step 1: Run the startup tests after the selector rewrite**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_main_voice_backend_selection.py -v`

Expected: `2 passed`

- [ ] **Step 2: If needed, update only the expected success message**

If the current test file still uses the old all-Hailo success string anywhere, it should match:

```python
"STT backend: Hybrid Whisper (wake=cpu:tiny, transcription=hailo:base)"
```

- [ ] **Step 3: Re-run the startup tests**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_main_voice_backend_selection.py -v`

Expected: `2 passed`

- [ ] **Step 4: Commit the verification pass**

```bash
cd /home/daedalus/linux/miniclaw
git add tests/test_main_voice_backend_selection.py
git commit -m "test(voice): align startup status messages with hybrid backend"
```

## Task 6: Full verification and documentation sanity

**Files:**
- No code changes expected

- [ ] **Step 1: Run the targeted voice tests together**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_voice_backends.py tests/test_main_voice_backend_selection.py -v`

Expected: all targeted voice backend tests pass.

- [ ] **Step 2: Run the full suite**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/`

Expected: full suite passes.

- [ ] **Step 3: Inspect the startup seam manually**

Run:

```bash
cd /home/daedalus/linux/miniclaw
.venv/bin/python - <<'PY'
from unittest.mock import patch
import main

with patch("main.build_stt_backend", return_value=(object(), "STT backend: Hybrid Whisper (wake=cpu:tiny, transcription=hailo:base)")):
    with patch("core.voice.VoiceInterface"):
        main.build_voice_interface()
PY
```

Expected: one printed line:

```text
STT backend: Hybrid Whisper (wake=cpu:tiny, transcription=hailo:base)
```

- [ ] **Step 4: Commit the finished implementation**

```bash
cd /home/daedalus/linux/miniclaw
git add core/hailo_whisper_runtime.py core/voice_backends.py main.py tests/test_main_voice_backend_selection.py tests/test_voice_backends.py
git commit -m "feat(voice): add hailo-backed full transcription with cpu wake fallback"
```

---

## Self-Review

**Spec coverage:**

- `VoiceInterface` remains the control plane: covered by Tasks 1, 4, 5.
- CPU wake detection remains unchanged: covered by Task 2 hybrid behavior test and Task 4 backend implementation.
- Hailo applies only to `transcribe_file()`: covered by Tasks 2, 3, 4.
- Assets live under `~/.miniclaw/models/hailo-whisper`: covered by Task 2 asset-root test and Task 4 constant update.
- No UDP or external Seeed repo dependency: covered by Task 3 local runtime wrapper.
- Startup selection and one-line reporting: covered by Tasks 1, 4, 5, 6.
- CPU fallback reasons: covered by Task 2 tests and Task 4 selector logic.

**Placeholder scan:** no `TODO`, `TBD`, or “implement later” steps in the plan. The runtime scaffold intentionally raises `NotImplementedError`, but that is explicit planned behavior for the scaffolded module, not a plan placeholder.

**Type consistency:**

- `build_stt_backend(wake_model, transcription_model) -> tuple[SttBackend, str]` is used consistently in Tasks 1 and 4.
- `hailo_transcription_assets_available(transcription_model)` is the only asset helper referenced after Task 4.
- `HailoTranscriptionRuntime.self_check(model_name, assets_root)` is referenced consistently in Tasks 2, 3, and 4.
- `HybridWhisperBackend` is the only Hailo-enabled backend class referenced after Task 4.
