# ElevenLabs cloud TTS backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a selectable `TTS_BACKEND=elevenlabs` backend that streams ElevenLabs Flash v2.5 first-audio (~75ms) by reusing the existing Kokoro `speak_stream` pipeline, with a startup-only fallback to Kokoro.

**Architecture:** A new `ElevenLabsTTSBackend` subclasses `KokoroTTSBackend` and overrides only `_synth_audio(text)` — the documented extension point already used by `KokoroONNXBackend`. It requests `pcm_24000` (matching `KOKORO_SAMPLE_RATE`) so the inherited resample/writer/barge-in machinery works unchanged. `main._build_tts_backend()` gains an `elevenlabs` branch that runs a cheap self-check and falls back to `kokoro-onnx` on any failure.

**Tech Stack:** Python 3.13, `elevenlabs` Python SDK, numpy, `sounddevice`; tests are `unittest`-style run under `pytest`.

## Global Constraints

- Source sample rate for the ElevenLabs backend MUST be `pcm_24000` (matches `KOKORO_SAMPLE_RATE = 24000` so inherited `resample(..., KOKORO_SAMPLE_RATE, output_samplerate)` calls are correct).
- Model id default: `eleven_flash_v2_5`. Voice id default: `onwK4e9ZLuTAKqWW03F9` (Daniel).
- Fallback is **startup-only**: on missing `ELEVENLABS_API_KEY`, `elevenlabs` import failure, or self-check failure, fall back to `kokoro-onnx` and return a status string that names the reason. Never a silent downgrade — the startup status line is always printed.
- No live API calls in tests — the `elevenlabs` SDK is always mocked.
- `.stream()` returns a **plain iterator, not a context manager**. Iterate it and close it in a `finally` so barge-in (`GeneratorExit` from the consumer breaking) tears the HTTP stream down.
- Match existing code style in `core/voice_backends.py` and `main.py`. Backend-construction reads env via `_configured_int` (after `load_dotenv`), not at import.
- Run tests with: `.venv/bin/python -m pytest <path> -v` from `/home/daedalus/linux/kaizen`.

---

### Task 1: Add the `elevenlabs` dependency

**Files:**
- Modify: `requirements.txt`

Adds the SDK so later tasks can import it. No test cycle of its own — it's a one-line prerequisite folded in here and verified by import.

- [ ] **Step 1: Add the dependency**

Append to `requirements.txt` (match the file's existing unpinned style; if the file pins versions, pin to the latest available instead):

```
elevenlabs
```

- [ ] **Step 2: Install it into the venv**

Run: `.venv/bin/python -m pip install elevenlabs`
Expected: installs successfully.

- [ ] **Step 3: Verify the import and client symbol**

Run: `.venv/bin/python -c "from elevenlabs.client import ElevenLabs; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "build: add elevenlabs SDK dependency"
```

---

### Task 2: `ElevenLabsTTSBackend._synth_audio` — streaming PCM → float32

**Files:**
- Modify: `core/voice_backends.py` (add class near the Kokoro backends, after `KokoroONNXBackend`)
- Test: `tests/test_voice_backends.py` (add `ElevenLabsTTSBackendTests`)

**Interfaces:**
- Consumes: `KokoroTTSBackend` (base class, its `speak_stream`/`speak`/`_configured_int`), `KOKORO_SAMPLE_RATE`, numpy as `np`.
- Produces:
  - `ElevenLabsTTSBackend(voice_id: str, api_key: str, model_id: str = "eleven_flash_v2_5", speed: float = 1.0, output_device: int | None = None, output_samplerate: int | None = None, client=None)` — if `client` is None it builds `ElevenLabs(api_key=api_key)`; the `client` param exists for test injection.
  - `ElevenLabsTTSBackend._synth_audio(text) -> Iterator[np.ndarray]` yielding float32 arrays in [-1, 1] at 24000 Hz.
  - Class attributes: `sample_rate = KOKORO_SAMPLE_RATE`, `PREBUFFER_MS = 0`, `MODEL_ID = "eleven_flash_v2_5"`, `DEFAULT_VOICE_ID = "onwK4e9ZLuTAKqWW03F9"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_voice_backends.py`:

```python
class ElevenLabsTTSBackendTests(unittest.TestCase):
    def _backend(self, stream_return):
        mock_client = MagicMock()
        mock_client.text_to_speech.stream.return_value = stream_return
        return voice_backends.ElevenLabsTTSBackend(
            voice_id="onwK4e9ZLuTAKqWW03F9",
            api_key="k",
            client=mock_client,
        ), mock_client

    def test_synth_audio_converts_pcm_bytes_to_float32(self):
        # int16 samples 0, 16384, -16384 → float32 0.0, 0.5, -0.5
        pcm = np.array([0, 16384, -16384], dtype=np.int16).tobytes()
        backend, mock_client = self._backend([pcm])

        chunks = list(backend._synth_audio("hi"))

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].dtype, np.float32)
        np.testing.assert_allclose(chunks[0], [0.0, 0.5, -0.5], atol=1e-4)

    def test_synth_audio_requests_flash_pcm_24000(self):
        backend, mock_client = self._backend([b""])
        list(backend._synth_audio("hello"))
        _, kwargs = mock_client.text_to_speech.stream.call_args
        self.assertEqual(kwargs["voice_id"], "onwK4e9ZLuTAKqWW03F9")
        self.assertEqual(kwargs["model_id"], "eleven_flash_v2_5")
        self.assertEqual(kwargs["output_format"], "pcm_24000")
        self.assertEqual(kwargs["text"], "hello")

    def test_synth_audio_skips_non_bytes_and_empty_chunks(self):
        pcm = np.array([16384], dtype=np.int16).tobytes()
        backend, _ = self._backend(["metadata-not-bytes", b"", pcm])
        chunks = list(backend._synth_audio("hi"))
        self.assertEqual(len(chunks), 1)

    def test_synth_audio_closes_stream_on_break(self):
        # Simulate a barge-in: consumer stops early. The generator's finally
        # must call the underlying stream's close().
        closed = {"n": 0}

        class ClosableStream:
            def __init__(self):
                self._data = [np.array([1], dtype=np.int16).tobytes()] * 5
            def __iter__(self):
                return iter(self._data)
            def close(self):
                closed["n"] += 1

        backend, _ = self._backend(ClosableStream())
        gen = backend._synth_audio("hi")
        next(gen)          # pull one chunk
        gen.close()        # consumer abandons (as a break would)
        self.assertEqual(closed["n"], 1)

    def test_prebuffer_defaults_to_zero(self):
        backend, _ = self._backend([b""])
        self.assertEqual(backend.PREBUFFER_MS, 0)
        self.assertEqual(backend.sample_rate, voice_backends.KOKORO_SAMPLE_RATE)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::ElevenLabsTTSBackendTests -v`
Expected: FAIL — `AttributeError: module 'core.voice_backends' has no attribute 'ElevenLabsTTSBackend'`.

- [ ] **Step 3: Implement the backend**

In `core/voice_backends.py`, add the guarded import near the other optional-import blocks (after the `kokoro_onnx` block around line 378):

```python
try:
    from elevenlabs.client import ElevenLabs as _ElevenLabsClient
    _ELEVENLABS_AVAILABLE = True
except ImportError:
    _ElevenLabsClient = None  # type: ignore[assignment]
    _ELEVENLABS_AVAILABLE = False
```

Then add the class after `KokoroONNXBackend`:

```python
class ElevenLabsTTSBackend(KokoroTTSBackend):
    """Cloud TTS via ElevenLabs Flash v2.5 (~75ms first-audio).

    Reuses KokoroTTSBackend's parallel speak_stream pipeline (sentence flush,
    synth + writer threads, barge-in, on_first_audio cue-stop) and overrides
    only _synth_audio. Requests pcm_24000 so the audio matches KOKORO_SAMPLE_RATE
    and the inherited resample path is correct with no changes.
    """

    sample_rate = KOKORO_SAMPLE_RATE
    # ElevenLabs streams faster than realtime, so the 1500ms Kokoro jitter
    # buffer would only re-add latency. Default to no prebuffer.
    PREBUFFER_MS = 0
    MODEL_ID = "eleven_flash_v2_5"
    DEFAULT_VOICE_ID = "onwK4e9ZLuTAKqWW03F9"  # Daniel — British, Jarvis-like

    def __init__(
        self,
        voice_id: str,
        api_key: str,
        model_id: str = MODEL_ID,
        speed: float = 1.0,
        output_device: int | None = None,
        output_samplerate: int | None = None,
        client=None,
    ):
        # Do NOT call super().__init__ — there is no Kokoro pipeline to load.
        # Set only the attributes speak_stream/speak read.
        if client is None:
            if not _ELEVENLABS_AVAILABLE:
                raise ImportError("elevenlabs not installed")
            client = _ElevenLabsClient(api_key=api_key)
        self._client = client
        self._voice_id = voice_id
        self._model_id = model_id
        self.voice = voice_id
        self.speed = speed
        self.output_device = output_device
        self.output_samplerate = output_samplerate or KOKORO_SAMPLE_RATE
        self.MIN_FIRST_FLUSH = _configured_min_first_flush(type(self).MIN_FIRST_FLUSH)
        self.PREBUFFER_MS = _configured_int(
            "KOKORO_PREBUFFER_MS", type(self).PREBUFFER_MS, 0
        )
        logger.info(
            "Loading ElevenLabs TTS (voice_id: %s, model: %s)", voice_id, model_id
        )

    def _synth_audio(self, text: str):
        audio_stream = self._client.text_to_speech.stream(
            voice_id=self._voice_id,
            text=text,
            model_id=self._model_id,
            output_format="pcm_24000",
        )
        try:
            for chunk in audio_stream:
                if isinstance(chunk, bytes) and chunk:
                    yield np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
        finally:
            close = getattr(audio_stream, "close", None)
            if callable(close):
                close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::ElevenLabsTTSBackendTests -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add core/voice_backends.py tests/test_voice_backends.py
git commit -m "feat(tts): ElevenLabsTTSBackend streaming pcm_24000 via _synth_audio override"
```

---

### Task 2b: Self-check helper — `elevenlabs_self_check`

**Files:**
- Modify: `core/voice_backends.py`
- Test: `tests/test_voice_backends.py` (add to `ElevenLabsTTSBackendTests` or a new class)

**Interfaces:**
- Consumes: an `ElevenLabsTTSBackend` instance (its `_synth_audio`).
- Produces: `elevenlabs_self_check(backend) -> None` — pulls the first chunk of a minimal `_synth_audio(".")` to prove key + connectivity; raises on any failure. Kept as a module function (mirrors `hailo_transcription_self_check`) so `_build_tts_backend` can call and catch it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_voice_backends.py`:

```python
class ElevenLabsSelfCheckTests(unittest.TestCase):
    def test_self_check_consumes_one_chunk(self):
        mock_client = MagicMock()
        mock_client.text_to_speech.stream.return_value = [
            np.array([1], dtype=np.int16).tobytes()
        ]
        backend = voice_backends.ElevenLabsTTSBackend(
            voice_id="v", api_key="k", client=mock_client
        )
        voice_backends.elevenlabs_self_check(backend)  # should not raise
        mock_client.text_to_speech.stream.assert_called_once()

    def test_self_check_raises_when_stream_errors(self):
        mock_client = MagicMock()
        mock_client.text_to_speech.stream.side_effect = RuntimeError("401 bad key")
        backend = voice_backends.ElevenLabsTTSBackend(
            voice_id="v", api_key="k", client=mock_client
        )
        with self.assertRaises(RuntimeError):
            voice_backends.elevenlabs_self_check(backend)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::ElevenLabsSelfCheckTests -v`
Expected: FAIL — `AttributeError: ... has no attribute 'elevenlabs_self_check'`.

- [ ] **Step 3: Implement the helper**

In `core/voice_backends.py`, add near the other module-level helpers:

```python
def elevenlabs_self_check(backend: "ElevenLabsTTSBackend") -> None:
    """Prove key + connectivity by synthesising one character and consuming
    the first streamed chunk. Raises on any failure so the caller can fall
    back to a local backend."""
    for _ in backend._synth_audio("."):
        break
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_voice_backends.py::ElevenLabsSelfCheckTests -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add core/voice_backends.py tests/test_voice_backends.py
git commit -m "feat(tts): elevenlabs_self_check via minimal one-char stream"
```

---

### Task 3: `_build_tts_backend` — `elevenlabs` branch + startup fallback

**Files:**
- Modify: `main.py:155-233` (`_build_tts_backend`)
- Test: `tests/test_main_voice_backend_selection.py` (add `BuildTtsBackendElevenLabsTests`)

**Interfaces:**
- Consumes: `voice_backends.ElevenLabsTTSBackend`, `voice_backends.elevenlabs_self_check` (Tasks 2 & 2b); existing `KokoroONNXBackend` fallback path already in `_build_tts_backend`.
- Produces: `_build_tts_backend(enable_tts, voice, speed)` handles `TTS_BACKEND=elevenlabs`, returning `(backend, status_message)` where the message contains `"elevenlabs"` on success or names the fallback reason and contains `"kokoro"` on fallback.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_main_voice_backend_selection.py`:

```python
class BuildTtsBackendElevenLabsTests(unittest.TestCase):
    @patch("core.audio_devices.output_samplerate", return_value=48000)
    @patch("core.audio_devices.resolve_output_device", return_value=0)
    def test_elevenlabs_selected_when_key_present_and_selfcheck_ok(
        self, _dev, _sr
    ):
        with patch("core.voice_backends.ElevenLabsTTSBackend") as mock_be, \
             patch("core.voice_backends.elevenlabs_self_check") as mock_check, \
             patch.dict("os.environ", {"TTS_BACKEND": "elevenlabs",
                                       "ELEVENLABS_API_KEY": "k"}, clear=False):
            instance = object()
            mock_be.return_value = instance
            backend, msg = main._build_tts_backend(True, "af_heart", 1.2)
            self.assertIs(backend, instance)
            self.assertIn("elevenlabs", msg.lower())
            mock_check.assert_called_once_with(instance)

    @patch("core.audio_devices.output_samplerate", return_value=48000)
    @patch("core.audio_devices.resolve_output_device", return_value=0)
    def test_falls_back_to_kokoro_when_no_key(self, _dev, _sr):
        with patch.dict("os.environ", {"TTS_BACKEND": "elevenlabs"}, clear=False):
            import os as _os
            _os.environ.pop("ELEVENLABS_API_KEY", None)
            backend, msg = main._build_tts_backend(True, "af_heart", 1.2)
            self.assertIn("kokoro", msg.lower())

    @patch("core.audio_devices.output_samplerate", return_value=48000)
    @patch("core.audio_devices.resolve_output_device", return_value=0)
    def test_falls_back_to_kokoro_when_selfcheck_raises(self, _dev, _sr):
        with patch("core.voice_backends.ElevenLabsTTSBackend"), \
             patch("core.voice_backends.elevenlabs_self_check",
                   side_effect=RuntimeError("offline")), \
             patch.dict("os.environ", {"TTS_BACKEND": "elevenlabs",
                                       "ELEVENLABS_API_KEY": "k"}, clear=False):
            backend, msg = main._build_tts_backend(True, "af_heart", 1.2)
            self.assertIn("kokoro", msg.lower())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_main_voice_backend_selection.py::BuildTtsBackendElevenLabsTests -v`
Expected: FAIL — the `elevenlabs` branch does not exist yet, so `elevenlabs` is treated as an unknown backend and returns the kokoro-default message (the success test fails; the fallback tests may accidentally pass — all three must pass after Step 3).

- [ ] **Step 3: Implement the branch**

In `main.py`, inside `_build_tts_backend`, add this branch immediately after the `output_sr = output_samplerate(output_device)` line and before the `backend_name = ...` read (so the elevenlabs branch and the existing kokoro branches share the resolved device). Insert:

```python
    backend_name = os.getenv("TTS_BACKEND", "kokoro").strip().lower()

    if backend_name == "elevenlabs":
        api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        voice_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip() or \
            voice_backends.ElevenLabsTTSBackend.DEFAULT_VOICE_ID
        model_id = os.getenv("ELEVENLABS_MODEL_ID", "").strip() or \
            voice_backends.ElevenLabsTTSBackend.MODEL_ID
        if not api_key:
            return _build_kokoro_onnx_fallback(
                voice, speed, output_device, output_sr,
                "ELEVENLABS_API_KEY not set",
            )
        try:
            backend = voice_backends.ElevenLabsTTSBackend(
                voice_id=voice_id,
                api_key=api_key,
                model_id=model_id,
                speed=speed,
                output_device=output_device,
                output_samplerate=output_sr,
            )
            voice_backends.elevenlabs_self_check(backend)
            return backend, (
                f"TTS backend: elevenlabs ({model_id}, voice_id={voice_id} "
                f"@ {output_sr} Hz)"
            )
        except Exception as exc:
            return _build_kokoro_onnx_fallback(
                voice, speed, output_device, output_sr,
                f"elevenlabs unavailable: {exc}",
            )
```

Note: the existing function reads `backend_name` lower down (around line 183). Move that single read up to the position shown and delete the later duplicate so `backend_name` is defined once. Add `from core import voice_backends` to `main.py`'s imports if not already present (it imports specific names today — add the module import alongside).

Then extract the existing `kokoro-onnx` construction (the body of the current `if backend_name == "kokoro-onnx":` block, lines ~184-221) into a helper so the elevenlabs fallback and the direct `kokoro-onnx` selection share one implementation:

```python
def _build_kokoro_onnx_fallback(voice, speed, output_device, output_sr, reason):
    """Build kokoro-onnx for the given device, or return the PyTorch-fallback
    (None) sentinel if its assets/package are missing. `reason` is prefixed to
    the status message so the trigger (e.g. an elevenlabs failure) is visible."""
    try:
        from core.voice_backends import KokoroONNXBackend, KOKORO_ONNX_ASSET_ROOT
        threads_env = os.getenv("TTS_ONNX_THREADS")
        intra_op_threads = int(threads_env) if threads_env else None
        variant = os.getenv("KOKORO_ONNX_VARIANT", "fp32").strip().lower()
        model_filename = {
            "int8": "kokoro-v1.0.int8.onnx",
            "fp32": "kokoro-v1.0.onnx",
        }.get(variant, "kokoro-v1.0.onnx")
        backend = KokoroONNXBackend(
            voice=voice,
            speed=speed,
            output_device=output_device,
            output_samplerate=output_sr,
            intra_op_threads=intra_op_threads,
            model_path=KOKORO_ONNX_ASSET_ROOT / model_filename,
        )
        return backend, (
            f"TTS backend: kokoro-onnx ({voice}, {variant} @ {output_sr} Hz, "
            f"{backend.intra_op_threads} thread(s)) — {reason}"
        )
    except (FileNotFoundError, ImportError) as exc:
        return None, (
            f"TTS backend: kokoro PyTorch fallback ({voice}) — {reason}; "
            f"kokoro-onnx also unavailable: {exc}"
        )
```

Update the existing `if backend_name == "kokoro-onnx":` block to call `_build_kokoro_onnx_fallback(voice, speed, output_device, output_sr, "requested")` instead of its inline body, preserving current behavior. Keep the existing preamble comments.

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_main_voice_backend_selection.py::BuildTtsBackendElevenLabsTests -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full selection + backend suites (no regressions)**

Run: `.venv/bin/python -m pytest tests/test_main_voice_backend_selection.py tests/test_voice_backends.py -v`
Expected: PASS — including the pre-existing `test_build_voice_interface_prints_backend_status_lines` which asserts a `TTS backend:` line is still printed.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main_voice_backend_selection.py
git commit -m "feat(tts): TTS_BACKEND=elevenlabs selection with startup kokoro-onnx fallback"
```

---

### Task 4: Config surface — `.env.example`, requirements note, docs

**Files:**
- Modify: `.env.example`
- Modify: `CLAUDE.md` (env-var table)

Documentation + config task; no separate test cycle (verified by the earlier suites and by reading the rendered files).

- [ ] **Step 1: Add env vars to `.env.example`**

Add (near the existing `TTS_*` entries; if `.env.example` documents `TTS_BACKEND`, extend its comment to list `elevenlabs`):

```bash
# TTS backend: kokoro | kokoro-onnx | elevenlabs
TTS_BACKEND=kokoro
# ElevenLabs cloud TTS (only used when TTS_BACKEND=elevenlabs).
# Missing key or no connectivity at startup falls back to kokoro-onnx.
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=onwK4e9ZLuTAKqWW03F9   # Daniel (British, Jarvis-like); George = JBFqnCBsd6RMkjVDRZzb
ELEVENLABS_MODEL_ID=eleven_flash_v2_5
```

- [ ] **Step 2: Add the env vars to the `CLAUDE.md` table**

In the "Key Environment Variables" table in `CLAUDE.md`, add rows:

```
| `TTS_BACKEND` | `kokoro` | `kokoro` \| `kokoro-onnx` \| `elevenlabs` |
| `ELEVENLABS_API_KEY` | — | Required for `TTS_BACKEND=elevenlabs`; absent/unreachable at startup → kokoro-onnx fallback |
| `ELEVENLABS_VOICE_ID` | `onwK4e9ZLuTAKqWW03F9` | ElevenLabs voice (Daniel, British) |
| `ELEVENLABS_MODEL_ID` | `eleven_flash_v2_5` | ElevenLabs Flash model (~75ms first-audio) |
```

- [ ] **Step 3: Verify docs reference reality**

Run: `grep -n "elevenlabs\|ELEVENLABS" .env.example CLAUDE.md`
Expected: shows the new lines in both files.

- [ ] **Step 4: Commit**

```bash
git add .env.example CLAUDE.md
git commit -m "docs(tts): document elevenlabs backend env vars"
```

---

### Task 5: Full suite + import sanity

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass (no regressions from the new backend or the `_build_kokoro_onnx_fallback` refactor).

- [ ] **Step 2: Import sanity for the new symbols**

Run: `.venv/bin/python -c "from core.voice_backends import ElevenLabsTTSBackend, elevenlabs_self_check; import main; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: (On-device, manual — do not run in CI) note for the operator**

On the Pi: set `TTS_BACKEND=elevenlabs` and `ELEVENLABS_API_KEY=...` in `.env`, `systemctl --user restart kaizen`, confirm the startup log shows `TTS backend: elevenlabs (...)` (not a fallback line), say "hey jarvis", and confirm the reply plays in Daniel's voice with fast first-audio and that saying the wake word mid-reply still cuts playback (barge-in). This step is operator-run; there is no automated assertion.

---

## Notes for the implementer

- **Do not call `super().__init__()`** in `ElevenLabsTTSBackend` — the base `__init__` loads a Kokoro pipeline that this backend doesn't use. Set the read attributes explicitly as shown.
- The inherited `speak_stream`/`speak` reference `KOKORO_SAMPLE_RATE` (24000) as the source rate for `resample`. Because `_synth_audio` yields 24kHz audio, this is correct — do not change those call sites.
- Keep everything network-related mocked in tests. The `client=` injection parameter on `ElevenLabsTTSBackend` exists precisely so tests never construct a real SDK client.
