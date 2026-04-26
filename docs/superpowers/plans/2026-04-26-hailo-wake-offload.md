# Hailo Wake Offload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Offload MiniClaw's wake-word transcription loop to Hailo when available while preserving the current configurable `WAKE_PHRASE` behavior and keeping post-wake routing unchanged.

**Architecture:** Extend the existing STT seam so wake and full-transcription backends are selected independently at startup. Add wake-specific support to `core/hailo_whisper_runtime.py`, then update `core/voice_backends.py` so one backend can represent mixed combinations such as `wake=hailo, transcription=cpu` or `wake=cpu, transcription=hailo` without pushing Hailo logic into `core/voice.py`.

**Tech Stack:** Python 3.11+, `openai-whisper`, Pi-installed Hailo runtime packages, `pytest`, existing MiniClaw voice harnesses.

---

## File Map

- `core/voice_backends.py`
  - Extend the current selector from “CPU wake + optional Hailo transcription” to independent wake/transcription selection
  - Add wake-specific variant/asset helpers
  - Keep startup status-line generation centralized
- `core/hailo_whisper_runtime.py`
  - Add wake-window inference support from in-memory float audio
  - Add wake-specific asset/self-check helpers
  - Preserve the existing one-shot full-transcription path
- `main.py`
  - Keep startup wiring thin; only status string expectations should change
- `tests/test_voice_backends.py`
  - Add wake-selection, mixed-mode, and wake-runtime asset tests
- `tests/test_main_voice_backend_selection.py`
  - Align startup message expectations with the new dual-selection status line

## Task 1: Define the new startup-selection behavior in tests

**Files:**
- Modify: `tests/test_main_voice_backend_selection.py`

- [ ] **Step 1: Update the success-path startup message expectation**

Replace the success string in `tests/test_main_voice_backend_selection.py` with:

```python
"STT backend: Hybrid Whisper (wake=hailo:tiny, transcription=hailo:base)"
```

The file should become:

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
            "STT backend: Hybrid Whisper (wake=hailo:tiny, transcription=hailo:base)",
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
        message = (
            "STT backend: CPU Whisper fallback "
            "(wake=cpu:tiny, transcription=cpu:base) — Hailo runtime unavailable"
        )
        mock_build_stt_backend.return_value = (fake_backend, message)

        main.build_voice_interface()

        mock_print.assert_called_once_with(message)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the startup-selection tests**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_main_voice_backend_selection.py -v`

Expected: FAIL because `build_stt_backend()` still returns the older single-path status strings.

- [ ] **Step 3: Commit the red startup expectation**

```bash
cd /home/daedalus/linux/miniclaw
git add tests/test_main_voice_backend_selection.py
git commit -m "test(voice): define dual wake/transcription startup status"
```

## Task 2: Expand backend tests for wake-specific selection

**Files:**
- Modify: `tests/test_voice_backends.py`

- [ ] **Step 1: Replace the current selector tests with wake-aware cases**

Rewrite `tests/test_voice_backends.py` so the top-level selector tests include:

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import voice_backends


class BuildSttBackendTests(unittest.TestCase):
    @patch("core.voice_backends.WhisperBackend")
    @patch("core.voice_backends.hailo_runtime_available", return_value=False)
    def test_falls_back_to_cpu_for_both_paths_when_hailo_runtime_unavailable(
        self, mock_runtime, mock_whisper_backend
    ):
        cpu_backend = object()
        mock_whisper_backend.return_value = cpu_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, cpu_backend)
        self.assertEqual(
            message,
            "STT backend: CPU Whisper fallback (wake=cpu:tiny, transcription=cpu:base) — Hailo runtime unavailable",
        )

    @patch("core.voice_backends.HybridWhisperBackend")
    @patch(
        "core.voice_backends.hailo_transcription_self_check",
        side_effect=RuntimeError("transcription self-check failed"),
        create=True,
    )
    @patch("core.voice_backends.hailo_wake_self_check", return_value=None, create=True)
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_wake_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_selects_hailo_wake_but_cpu_transcription_when_only_wake_is_ready(
        self,
        mock_runtime,
        mock_wake_assets,
        mock_trans_assets,
        mock_wake_self_check,
        mock_trans_self_check,
        mock_hybrid_backend,
    ):
        hybrid_backend = object()
        mock_hybrid_backend.return_value = hybrid_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, hybrid_backend)
        self.assertEqual(
            message,
            "STT backend: Hybrid Whisper (wake=hailo:tiny, transcription=cpu:base)",
        )

    @patch("core.voice_backends.HybridWhisperBackend")
    @patch("core.voice_backends.hailo_transcription_self_check", return_value=None, create=True)
    @patch(
        "core.voice_backends.hailo_wake_self_check",
        side_effect=RuntimeError("wake self-check failed"),
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_wake_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_selects_cpu_wake_but_hailo_transcription_when_only_transcription_is_ready(
        self,
        mock_runtime,
        mock_wake_assets,
        mock_trans_assets,
        mock_wake_self_check,
        mock_trans_self_check,
        mock_hybrid_backend,
    ):
        hybrid_backend = object()
        mock_hybrid_backend.return_value = hybrid_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, hybrid_backend)
        self.assertEqual(
            message,
            "STT backend: Hybrid Whisper (wake=cpu:tiny, transcription=hailo:base)",
        )

    @patch("core.voice_backends.HybridWhisperBackend")
    @patch("core.voice_backends.hailo_transcription_self_check", return_value=None, create=True)
    @patch("core.voice_backends.hailo_wake_self_check", return_value=None, create=True)
    @patch(
        "core.voice_backends.hailo_transcription_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch(
        "core.voice_backends.hailo_wake_assets_available",
        return_value=(True, ""),
        create=True,
    )
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_selects_hailo_for_both_paths_when_both_are_ready(
        self,
        mock_runtime,
        mock_wake_assets,
        mock_trans_assets,
        mock_wake_self_check,
        mock_trans_self_check,
        mock_hybrid_backend,
    ):
        hybrid_backend = object()
        mock_hybrid_backend.return_value = hybrid_backend

        backend, message = voice_backends.build_stt_backend("tiny", "base")

        self.assertIs(backend, hybrid_backend)
        self.assertEqual(
            message,
            "STT backend: Hybrid Whisper (wake=hailo:tiny, transcription=hailo:base)",
        )

    @patch("core.voice_backends.WhisperBackend")
    @patch("core.voice_backends.hailo_runtime_available", return_value=True)
    def test_wake_variant_unsupported_for_hailo_falls_back_only_for_wake(
        self, mock_runtime, mock_whisper_backend
    ):
        cpu_backend = object()
        mock_whisper_backend.return_value = cpu_backend

        with patch(
            "core.voice_backends.hailo_transcription_assets_available",
            return_value=(False, "transcription model asset missing"),
            create=True,
        ):
            backend, message = voice_backends.build_stt_backend("small", "base")

        self.assertIs(backend, cpu_backend)
        self.assertIn("wake=cpu:small", message)

    def test_asset_root_is_user_scoped(self):
        self.assertEqual(
            voice_backends.HAILO_WHISPER_ASSET_ROOT,
            Path.home() / ".miniclaw" / "models" / "hailo-whisper",
        )
```

- [ ] **Step 2: Add backend-behavior tests for wake offload**

Extend `HybridWhisperBackendTests` with:

```python
class HybridWhisperBackendTests(unittest.TestCase):
    @patch("core.voice_backends.HailoTranscriptionRuntime")
    @patch("core.voice_backends.HailoWakeRuntime")
    @patch("core.voice_backends.whisper.load_model")
    def test_hailo_wake_runtime_is_used_when_enabled(
        self, mock_load_model, mock_wake_runtime_cls, mock_trans_runtime_cls
    ):
        wake_model = Mock()
        mock_load_model.return_value = wake_model

        wake_runtime = mock_wake_runtime_cls.return_value
        wake_runtime.transcribe_wake_audio.return_value = "computer"

        backend = voice_backends.HybridWhisperBackend(
            wake_model="tiny",
            transcription_model="base",
            use_hailo_wake=True,
            use_hailo_transcription=False,
        )

        wake_text = backend.transcribe_wake_audio([0.0, 0.1])

        self.assertEqual(wake_text, "computer")
        wake_runtime.transcribe_wake_audio.assert_called_once()
        mock_load_model.assert_not_called()

    @patch("core.voice_backends.HailoTranscriptionRuntime")
    @patch("core.voice_backends.HailoWakeRuntime")
    @patch("core.voice_backends.whisper.load_model")
    def test_cpu_wake_path_is_used_when_hailo_wake_disabled(
        self, mock_load_model, mock_wake_runtime_cls, mock_trans_runtime_cls
    ):
        wake_model = Mock()
        wake_model.transcribe.return_value = {"text": "Computer"}
        mock_load_model.return_value = wake_model

        backend = voice_backends.HybridWhisperBackend(
            wake_model="tiny",
            transcription_model="base",
            use_hailo_wake=False,
            use_hailo_transcription=True,
        )

        wake_text = backend.transcribe_wake_audio([0.0, 0.1])

        self.assertEqual(wake_text, "computer")
        wake_model.transcribe.assert_called_once()
        mock_wake_runtime_cls.assert_not_called()
```

- [ ] **Step 3: Add wake-runtime asset tests**

Append:

```python
class HailoWakeRuntimeAssetTests(unittest.TestCase):
    def test_wake_self_check_rejects_missing_model_dir(self):
        from core.hailo_whisper_runtime import HailoWakeRuntime

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "wake model asset missing"):
                HailoWakeRuntime.self_check(
                    model_name="tiny",
                    assets_root=Path(tmp),
                )
```

- [ ] **Step 4: Run the backend tests**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_voice_backends.py -v`

Expected: FAIL because wake-specific helpers/classes do not exist yet and the startup selector only knows about transcription readiness.

- [ ] **Step 5: Commit the red wake-offload tests**

```bash
cd /home/daedalus/linux/miniclaw
git add tests/test_voice_backends.py tests/test_main_voice_backend_selection.py
git commit -m "test(voice): define hailo wake offload behavior"
```

## Task 3: Add wake-runtime support to `core/hailo_whisper_runtime.py`

**Files:**
- Modify: `core/hailo_whisper_runtime.py`
- Test: `tests/test_voice_backends.py`

- [ ] **Step 1: Add a shared base asset resolver**

Refactor `core/hailo_whisper_runtime.py` so the current asset-resolution logic is reusable by both transcription and wake runtimes. Introduce a helper shape like:

```python
def _resolve_runtime_assets(
    model_name: str,
    assets_root: Path,
    *,
    missing_model_message: str,
    missing_asset_message: str,
    missing_hef_message: str,
    hw_arch: str | None = None,
) -> RuntimeAssets:
    ...
```

`HailoTranscriptionRuntime` should keep using the current `"transcription model ..."` messages. The new wake runtime will use `"wake model ..."` messages.

- [ ] **Step 2: Add `HailoWakeRuntime`**

Add this class:

```python
class HailoWakeRuntime:
    def __init__(self, model_name: str, assets_root: Path, hw_arch: str | None = None):
        self.model_name = model_name
        self.assets_root = Path(assets_root)
        self.assets = _resolve_runtime_assets(
            model_name,
            self.assets_root,
            missing_model_message="wake model asset missing",
            missing_asset_message="wake model asset missing",
            missing_hef_message="wake model HEF missing",
            hw_arch=hw_arch,
        )
        self._transcription_runtime = HailoTranscriptionRuntime(
            model_name=model_name,
            assets_root=assets_root,
            hw_arch=hw_arch,
        )

    @classmethod
    def self_check(
        cls, model_name: str, assets_root: Path, hw_arch: str | None = None
    ) -> None:
        _resolve_runtime_assets(
            model_name,
            Path(assets_root),
            missing_model_message="wake model asset missing",
            missing_asset_message="wake model asset missing",
            missing_hef_message="wake model HEF missing",
            hw_arch=hw_arch,
        )
        if _hailo_platform_import_error is not None:
            raise RuntimeError("hailo_platform python module not installed")

    def transcribe_wake_audio(self, audio_float) -> str:
        audio = np.asarray(audio_float, dtype=np.float32)
        texts: list[str] = []
        for mel in self._transcription_runtime._iter_mel_chunks(audio):
            text = self._transcription_runtime._transcribe_mel_chunk(mel)
            if text:
                texts.append(text)
        return self._transcription_runtime._clean_transcription(" ".join(texts)).lower().strip()
```

- [ ] **Step 3: Run the wake-runtime asset tests**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_voice_backends.py::HailoWakeRuntimeAssetTests -v`

Expected: PASS

- [ ] **Step 4: Commit the wake runtime**

```bash
cd /home/daedalus/linux/miniclaw
git add core/hailo_whisper_runtime.py tests/test_voice_backends.py
git commit -m "feat(voice): add hailo wake runtime scaffold"
```

## Task 4: Extend `HybridWhisperBackend` for independent wake/transcription selection

**Files:**
- Modify: `core/voice_backends.py`
- Test: `tests/test_voice_backends.py`

- [ ] **Step 1: Update imports and supported-variant constants**

Near the top of `core/voice_backends.py`, change the Hailo imports and constants to:

```python
from core.hailo_whisper_runtime import HailoTranscriptionRuntime, HailoWakeRuntime

SUPPORTED_HAILO_WHISPER_TRANSCRIPTION_VARIANTS = {"base", "tiny", "tiny.en", "base.en"}
SUPPORTED_HAILO_WHISPER_WAKE_VARIANTS = {"tiny", "tiny.en", "base", "base.en"}
```

- [ ] **Step 2: Add wake-specific asset/self-check helpers**

Add:

```python
def hailo_wake_assets_available(wake_model: str) -> tuple[bool, str]:
    wake_dir = HAILO_WHISPER_ASSET_ROOT / wake_model
    if not wake_dir.exists():
        return False, "wake model asset missing"
    return True, ""


def hailo_wake_self_check(wake_model: str) -> None:
    HailoWakeRuntime.self_check(
        model_name=wake_model,
        assets_root=HAILO_WHISPER_ASSET_ROOT,
    )


def hailo_transcription_self_check(transcription_model: str) -> None:
    HailoTranscriptionRuntime.self_check(
        model_name=transcription_model,
        assets_root=HAILO_WHISPER_ASSET_ROOT,
    )
```

- [ ] **Step 3: Extend `HybridWhisperBackend` constructor**

Replace the backend class with:

```python
class HybridWhisperBackend:
    """Independent wake/transcription selection across CPU Whisper and Hailo."""

    def __init__(
        self,
        wake_model: str,
        transcription_model: str,
        *,
        use_hailo_wake: bool,
        use_hailo_transcription: bool,
    ):
        self.use_hailo_wake = use_hailo_wake
        self.use_hailo_transcription = use_hailo_transcription

        self.cpu_wake_model = None
        self.hailo_wake_runtime = None
        self.hailo_transcription_runtime = None

        if use_hailo_wake:
            self.hailo_wake_runtime = HailoWakeRuntime(
                model_name=wake_model,
                assets_root=HAILO_WHISPER_ASSET_ROOT,
            )
        else:
            logger.info("Loading Whisper wake model: %s", wake_model)
            self.cpu_wake_model = whisper.load_model(wake_model)

        if use_hailo_transcription:
            self.hailo_transcription_runtime = HailoTranscriptionRuntime(
                model_name=transcription_model,
                assets_root=HAILO_WHISPER_ASSET_ROOT,
            )
        else:
            logger.info("Loading Whisper transcription model: %s", transcription_model)
            self.cpu_transcription_model = whisper.load_model(transcription_model)

    def transcribe_wake_audio(self, audio_float) -> str:
        if self.hailo_wake_runtime is not None:
            return self.hailo_wake_runtime.transcribe_wake_audio(audio_float)
        result = self.cpu_wake_model.transcribe(
            audio_float,
            language="en",
            fp16=False,
        )
        return result["text"].lower().strip()

    def transcribe_file(self, audio_file: str) -> str:
        if self.hailo_transcription_runtime is not None:
            return self.hailo_transcription_runtime.transcribe_file(audio_file).strip()
        result = self.cpu_transcription_model.transcribe(audio_file)
        return result["text"].strip()
```

- [ ] **Step 4: Rewrite `build_stt_backend()` to select wake and transcription independently**

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
            f"STT backend: CPU Whisper fallback (wake=cpu:{wake_model}, transcription=cpu:{transcription_model}) — Hailo runtime unavailable",
        )

    use_hailo_wake = False
    use_hailo_transcription = False

    if wake_model in SUPPORTED_HAILO_WHISPER_WAKE_VARIANTS:
        wake_assets_ok, _ = hailo_wake_assets_available(wake_model)
        if wake_assets_ok:
            try:
                hailo_wake_self_check(wake_model)
                use_hailo_wake = True
            except Exception:
                use_hailo_wake = False

    if transcription_model in SUPPORTED_HAILO_WHISPER_TRANSCRIPTION_VARIANTS:
        transcription_assets_ok, _ = hailo_transcription_assets_available(transcription_model)
        if transcription_assets_ok:
            try:
                hailo_transcription_self_check(transcription_model)
                use_hailo_transcription = True
            except Exception:
                use_hailo_transcription = False

    if not use_hailo_wake and not use_hailo_transcription:
        return (
            WhisperBackend(
                wake_model=wake_model, transcription_model=transcription_model
            ),
            f"STT backend: CPU Whisper fallback (wake=cpu:{wake_model}, transcription=cpu:{transcription_model}) — Hailo unavailable for configured models",
        )

    backend = HybridWhisperBackend(
        wake_model=wake_model,
        transcription_model=transcription_model,
        use_hailo_wake=use_hailo_wake,
        use_hailo_transcription=use_hailo_transcription,
    )

    wake_backend = f"{'hailo' if use_hailo_wake else 'cpu'}:{wake_model}"
    transcription_backend = f"{'hailo' if use_hailo_transcription else 'cpu'}:{transcription_model}"
    return (
        backend,
        f"STT backend: Hybrid Whisper (wake={wake_backend}, transcription={transcription_backend})",
    )
```

- [ ] **Step 5: Run the backend tests**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_voice_backends.py -v`

Expected: PASS

- [ ] **Step 6: Commit the selector rewrite**

```bash
cd /home/daedalus/linux/miniclaw
git add core/voice_backends.py tests/test_voice_backends.py
git commit -m "feat(voice): add independent hailo wake selection"
```

## Task 5: Align startup status-line tests with the new selector

**Files:**
- Modify: `main.py` only if needed
- Test: `tests/test_main_voice_backend_selection.py`

- [ ] **Step 1: Run the startup tests**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_main_voice_backend_selection.py -v`

Expected: PASS if `main.py` still simply prints the selector's status line.

- [ ] **Step 2: If needed, keep `main.py` as a thin seam**

`main.py` should still look like:

```python
stt_backend, stt_status = build_stt_backend(
    wake_model=os.getenv("WAKE_MODEL", "tiny"),
    transcription_model=os.getenv("WHISPER_MODEL", "base"),
)
print(stt_status)
```

- [ ] **Step 3: Re-run the startup tests**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_main_voice_backend_selection.py -v`

Expected: `2 passed`

- [ ] **Step 4: Commit the startup verification**

```bash
cd /home/daedalus/linux/miniclaw
git add main.py tests/test_main_voice_backend_selection.py
git commit -m "test(voice): align startup status with hailo wake offload"
```

## Task 6: Final verification

**Files:**
- No code changes expected

- [ ] **Step 1: Run the Hailo-focused test set**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/test_voice_backends.py tests/test_main_voice_backend_selection.py tests/test_download_hailo_whisper_assets.py -v`

Expected: all tests pass.

- [ ] **Step 2: Run the full suite**

Run: `cd /home/daedalus/linux/miniclaw && .venv/bin/python -m pytest tests/`

Expected: full suite passes, modulo any already-known environment-only Unix-socket sandbox issue in `tests/test_soundcloud_handler.py`.

- [ ] **Step 3: Verify the mocked startup message manually**

Run:

```bash
cd /home/daedalus/linux/miniclaw
.venv/bin/python - <<'PY'
from unittest.mock import patch
import main

with patch(
    "main.build_stt_backend",
    return_value=(object(), "STT backend: Hybrid Whisper (wake=hailo:tiny, transcription=hailo:base)"),
):
    with patch("core.voice.VoiceInterface"):
        main.build_voice_interface()
PY
```

Expected output:

```text
STT backend: Hybrid Whisper (wake=hailo:tiny, transcription=hailo:base)
```

- [ ] **Step 4: Commit the finished wake-offload implementation**

```bash
cd /home/daedalus/linux/miniclaw
git add core/hailo_whisper_runtime.py core/voice_backends.py main.py tests/test_voice_backends.py tests/test_main_voice_backend_selection.py docs/superpowers/plans/2026-04-26-hailo-wake-offload.md
git commit -m "feat(voice): add hailo wake-word offload"
```

---

## Self-Review

**Spec coverage:**

- Hailo wake offload while preserving configurable `WAKE_PHRASE`: covered by Tasks 2, 3, 4.
- No dedicated keyword spotter and no TierRouter/TTS changes: preserved by Tasks 3 and 4.
- Independent wake/transcription fallback combinations: covered by Tasks 2 and 4.
- Startup line reports wake/transcription separately: covered by Tasks 1, 4, 5.
- `VoiceInterface` boundary unchanged: covered by Tasks 4 and 6.

**Placeholder scan:** no `TODO`, `TBD`, or vague “handle appropriately” steps remain.

**Type consistency:**

- `build_stt_backend(wake_model, transcription_model)` is used consistently in Tasks 1, 4, and 5.
- `HailoWakeRuntime.self_check()` and `hailo_wake_self_check()` are named consistently across Tasks 2, 3, and 4.
- `HybridWhisperBackend(..., use_hailo_wake=..., use_hailo_transcription=...)` is used consistently across Tasks 2 and 4.
