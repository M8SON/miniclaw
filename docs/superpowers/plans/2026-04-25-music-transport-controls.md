# Music Transport Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan. Batch tasks within a phase; checkpoint at phase boundaries. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `pause`, `resume`, `skip`, `volume_up`, `volume_down` actions to the soundcloud handler with mpv IPC; queue 20 tracks per `play`; rewire `intent_patterns.yaml` so the transport regexes dispatch correctly to the post-migration `soundcloud` skill.

**Architecture:** Spawn mpv with `--input-ipc-server=/tmp/miniclaw-mpv.sock` and all 20 yt-dlp results as positional args; mpv handles the queue natively. Transport actions speak JSON-IPC over the Unix socket. A new private `_send_mpv_command` helper centralizes the socket logic; each action is a thin wrapper. SKILL.md exposes the action enum so Claude routes correctly when the regex doesn't match (LLM fallback path); `intent_patterns.yaml` covers the regex dispatch path.

**Tech Stack:** Python 3.11+ stdlib (`socket`, `json`, `subprocess`), pytest, mpv with `--input-ipc-server` (since mpv 0.7.0; ubiquitous), yt-dlp.

**Related spec:** `docs/superpowers/specs/2026-04-25-music-transport-controls-design.md`

---

## File Structure Map

### New files

| Path | Responsibility |
|---|---|
| `tests/test_soundcloud_handler.py` | Unit tests for the soundcloud handler — IPC helper, every action, queue parsing |

### Modified files

| Path | Change |
|---|---|
| `core/container_manager.py` | Add `socket` import; add `_send_mpv_command` helper; rewrite `_execute_soundcloud` to dispatch the new actions and queue 20 tracks |
| `skills/soundcloud/SKILL.md` | Replace description, when-to-use, and input schema to expose the action enum |
| `config/intent_patterns.yaml` | Replace soundcloud-related dispatch entries: rename `soundcloud_play` → `soundcloud`, split stop/pause patterns, add resume + skip |
| `tests/test_tier_router.py` | Add coverage for the new transport patterns (pause is no longer lumped with stop, skip routes correctly, etc.) |

---

## Phase 1 — Soundcloud handler with mpv IPC

Phase-boundary checkpoint: `pytest tests/test_soundcloud_handler.py -v` passes; full suite green.

### Task 1: IPC helper

**Files:**
- Create: `tests/test_soundcloud_handler.py` (initial)
- Modify: `core/container_manager.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_soundcloud_handler.py
"""Unit tests for the soundcloud transport-control handler."""

import importlib
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _load_container_manager():
    """Reimport core.container_manager fresh for each test."""
    sys.modules.pop("core.container_manager", None)
    return importlib.import_module("core.container_manager")


class _FakeMpvServer:
    """Tiny Unix-socket server that records one request and replies once."""

    def __init__(self, sock_path: str, reply: bytes = b'{"error":"success"}\n'):
        self.sock_path = sock_path
        self.reply = reply
        self.received: bytes | None = None
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None

    def start(self):
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.sock_path)
        self._sock.listen(1)
        self._sock.settimeout(2.0)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        try:
            conn, _ = self._sock.accept()
            with conn:
                self.received = conn.recv(4096)
                conn.sendall(self.reply)
        except OSError:
            pass

    def stop(self):
        if self._sock:
            self._sock.close()
        if self._thread:
            self._thread.join(timeout=1.0)
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)


class TestIPCHelper(unittest.TestCase):
    def setUp(self):
        self.cm_module = _load_container_manager()
        self.manager = self.cm_module.ContainerManager()

    def test_returns_none_when_socket_missing(self):
        # Use a non-existent path.
        with patch.object(
            self.manager, "_mpv_socket_path", "/tmp/definitely-not-a-socket-xyz"
        ):
            result = self.manager._send_mpv_command(["set_property", "pause", True])
        self.assertIsNone(result)

    def test_round_trip_returns_parsed_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            sock_path = str(Path(tmp) / "test.sock")
            server = _FakeMpvServer(sock_path)
            server.start()
            try:
                with patch.object(self.manager, "_mpv_socket_path", sock_path):
                    result = self.manager._send_mpv_command(
                        ["set_property", "pause", True]
                    )
            finally:
                server.stop()

            self.assertIsNotNone(server.received)
            payload = json.loads(server.received.decode().strip())
            self.assertEqual(payload, {"command": ["set_property", "pause", True]})
            self.assertEqual(result, {"error": "success"})

    def test_malformed_response_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            sock_path = str(Path(tmp) / "test.sock")
            server = _FakeMpvServer(sock_path, reply=b"not json at all\n")
            server.start()
            try:
                with patch.object(self.manager, "_mpv_socket_path", sock_path):
                    result = self.manager._send_mpv_command(["set_property", "pause", True])
            finally:
                server.stop()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_soundcloud_handler.py -v 2>&1 | tail -10
```

Expected: failure (`AttributeError: 'ContainerManager' object has no attribute '_send_mpv_command'`).

- [ ] **Step 3: Add the IPC helper to `core/container_manager.py`**

At the top of the file, in the imports section (after `import urllib.parse`), add:

```python
import socket
```

In `ContainerManager.__init__`, near the existing `self._mpv_process` line, add:

```python
        self._mpv_socket_path: str = "/tmp/miniclaw-mpv.sock"
```

Add this method to `ContainerManager` (place it near the soundcloud handler — adjacent to `_execute_soundcloud` is fine):

```python
    def _send_mpv_command(self, args: list) -> dict | None:
        """Send a JSON IPC command to mpv. Returns parsed response or None on failure."""
        sock_path = self._mpv_socket_path
        if not os.path.exists(sock_path):
            return None
        payload = json.dumps({"command": args}).encode() + b"\n"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(2.0)
                s.connect(sock_path)
                s.sendall(payload)
                response = s.recv(4096).decode(errors="replace")
            if not response:
                return None
            return json.loads(response.split("\n")[0])
        except (OSError, json.JSONDecodeError):
            return None
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_soundcloud_handler.py -v 2>&1 | tail -10
```

Expected: 3 IPC tests pass.

- [ ] **Step 5: Commit**

```bash
cd ~/linux/miniclaw && git add core/container_manager.py tests/test_soundcloud_handler.py && git commit -m "feat(soundcloud): add mpv IPC helper for transport-control commands"
```

### Task 2: Action dispatch + 20-track queue

**Files:**
- Modify: `core/container_manager.py` — rewrite `_execute_soundcloud`
- Modify: `tests/test_soundcloud_handler.py` — append action tests

- [ ] **Step 1: Append failing tests**

Append to `tests/test_soundcloud_handler.py`:

```python
class TestActionDispatch(unittest.TestCase):
    def setUp(self):
        self.cm_module = _load_container_manager()
        self.manager = self.cm_module.ContainerManager()

    def test_default_action_is_play(self):
        # Empty action with empty query should hit the play branch and report no query.
        result = self.manager._execute_soundcloud({})
        self.assertEqual(result, "No search query provided.")

    def test_pause_when_socket_missing(self):
        with patch.object(self.manager, "_mpv_socket_path", "/tmp/definitely-not-a-socket-xyz"):
            result = self.manager._execute_soundcloud({"action": "pause"})
        self.assertEqual(result, "Nothing is playing.")

    def test_pause_calls_ipc_with_set_property(self):
        sent: list = []

        def fake_send(args):
            sent.append(args)
            return {"error": "success"}

        with patch.object(self.manager, "_send_mpv_command", side_effect=fake_send), \
             patch("os.path.exists", return_value=True):
            result = self.manager._execute_soundcloud({"action": "pause"})
        self.assertEqual(sent, [["set_property", "pause", True]])
        self.assertEqual(result, "Paused.")

    def test_resume_calls_ipc_with_set_property_false(self):
        sent: list = []

        def fake_send(args):
            sent.append(args)
            return {"error": "success"}

        with patch.object(self.manager, "_send_mpv_command", side_effect=fake_send), \
             patch("os.path.exists", return_value=True):
            result = self.manager._execute_soundcloud({"action": "resume"})
        self.assertEqual(sent, [["set_property", "pause", False]])
        self.assertEqual(result, "Resumed.")

    def test_skip_calls_ipc_with_playlist_next(self):
        sent: list = []

        def fake_send(args):
            sent.append(args)
            return {"error": "success"}

        with patch.object(self.manager, "_send_mpv_command", side_effect=fake_send), \
             patch("os.path.exists", return_value=True):
            result = self.manager._execute_soundcloud({"action": "skip"})
        self.assertEqual(sent, [["playlist-next"]])
        self.assertEqual(result, "Skipped.")

    def test_volume_up_calls_ipc_add_volume_5(self):
        sent: list = []

        def fake_send(args):
            sent.append(args)
            return {"error": "success"}

        with patch.object(self.manager, "_send_mpv_command", side_effect=fake_send), \
             patch("os.path.exists", return_value=True):
            result = self.manager._execute_soundcloud({"action": "volume_up"})
        self.assertEqual(sent, [["add", "volume", 5]])
        self.assertEqual(result, "Volume up.")

    def test_volume_down_calls_ipc_add_volume_minus_5(self):
        sent: list = []

        def fake_send(args):
            sent.append(args)
            return {"error": "success"}

        with patch.object(self.manager, "_send_mpv_command", side_effect=fake_send), \
             patch("os.path.exists", return_value=True):
            result = self.manager._execute_soundcloud({"action": "volume_down"})
        self.assertEqual(sent, [["add", "volume", -5]])
        self.assertEqual(result, "Volume down.")

    def test_unknown_action_is_rejected(self):
        result = self.manager._execute_soundcloud({"action": "explode"})
        self.assertIn("unknown action", result.lower())


class TestStop(unittest.TestCase):
    def setUp(self):
        self.cm_module = _load_container_manager()
        self.manager = self.cm_module.ContainerManager()

    def test_stop_with_no_process_returns_nothing_playing(self):
        self.manager._mpv_process = None
        result = self.manager._execute_soundcloud({"action": "stop"})
        self.assertEqual(result, "Nothing is playing.")

    def test_stop_terminates_process_and_cleans_files(self):
        proc = MagicMock()
        proc.poll.return_value = None  # still running
        self.manager._mpv_process = proc

        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            sock_path = str(tmp_p / "fake.sock")
            sock_path_obj = Path(sock_path)
            sock_path_obj.touch()

            now_playing_dir = tmp_p / ".miniclaw"
            now_playing_dir.mkdir()
            now_playing = now_playing_dir / "now_playing.json"
            now_playing.write_text('{"title": "test"}')

            with patch.object(self.manager, "_mpv_socket_path", sock_path), \
                 patch("pathlib.Path.home", return_value=tmp_p):
                result = self.manager._execute_soundcloud({"action": "stop"})

            self.assertEqual(result, "Stopped.")
            proc.terminate.assert_called_once()
            self.assertFalse(sock_path_obj.exists())
            self.assertFalse(now_playing.exists())
        self.assertIsNone(self.manager._mpv_process)


class TestPlayQueue(unittest.TestCase):
    def setUp(self):
        self.cm_module = _load_container_manager()
        self.manager = self.cm_module.ContainerManager()

    def _yt_dlp_output(self, n_pairs: int) -> str:
        lines = []
        for i in range(n_pairs):
            lines.append(f"Track {i}")
            lines.append(f"https://example.invalid/track-{i}.mp3")
        return "\n".join(lines) + "\n"

    def test_play_no_query_rejected(self):
        result = self.manager._execute_soundcloud({"action": "play"})
        self.assertEqual(result, "No search query provided.")

    def test_play_calls_ytdlp_with_scsearch20(self):
        run_calls: list = []

        def fake_run(cmd, **kwargs):
            run_calls.append(cmd)
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = self._yt_dlp_output(20)
            return mock

        with patch("shutil.which", return_value="/usr/bin/x"), \
             patch("subprocess.run", side_effect=fake_run), \
             patch("subprocess.Popen") as mock_popen, \
             patch("pathlib.Path.write_text"):
            self.manager._execute_soundcloud({"action": "play", "query": "country"})

        # First subprocess.run call is the yt-dlp search.
        self.assertTrue(any("scsearch20:country" in part for part in run_calls[0]))

    def test_play_passes_all_20_urls_to_mpv(self):
        def fake_run(cmd, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = self._yt_dlp_output(20)
            return mock

        with patch("shutil.which", return_value="/usr/bin/x"), \
             patch("subprocess.run", side_effect=fake_run), \
             patch("subprocess.Popen") as mock_popen, \
             patch("pathlib.Path.write_text"):
            self.manager._execute_soundcloud({"action": "play", "query": "country"})

        popen_args = mock_popen.call_args[0][0]
        # Should be mpv + flags + 20 URLs.
        urls_passed = [a for a in popen_args if a.startswith("https://example.invalid/")]
        self.assertEqual(len(urls_passed), 20)
        # IPC server flag must be present.
        self.assertTrue(any("--input-ipc-server=" in a for a in popen_args))

    def test_play_returns_first_track_title(self):
        def fake_run(cmd, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = self._yt_dlp_output(20)
            return mock

        with patch("shutil.which", return_value="/usr/bin/x"), \
             patch("subprocess.run", side_effect=fake_run), \
             patch("subprocess.Popen"), \
             patch("pathlib.Path.write_text"):
            result = self.manager._execute_soundcloud({"action": "play", "query": "country"})

        self.assertEqual(result, "Now playing: Track 0")

    def test_play_with_no_results_returns_no_results_message(self):
        def fake_run(cmd, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = ""
            return mock

        with patch("shutil.which", return_value="/usr/bin/x"), \
             patch("subprocess.run", side_effect=fake_run):
            result = self.manager._execute_soundcloud({"action": "play", "query": "asdfqwerzxcv"})

        self.assertIn("No results found", result)

    def test_play_with_odd_line_count_returns_could_not_retrieve(self):
        def fake_run(cmd, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            # 3 lines = 1.5 pairs → malformed.
            mock.stdout = "Track 0\nhttps://x/0.mp3\nOrphan title\n"
            return mock

        with patch("shutil.which", return_value="/usr/bin/x"), \
             patch("subprocess.run", side_effect=fake_run):
            result = self.manager._execute_soundcloud({"action": "play", "query": "x"})

        self.assertIn("Could not retrieve", result)

    def test_play_terminates_existing_mpv_before_starting_new(self):
        existing = MagicMock()
        existing.poll.return_value = None  # still running
        self.manager._mpv_process = existing

        def fake_run(cmd, **kwargs):
            mock = MagicMock()
            mock.returncode = 0
            mock.stdout = self._yt_dlp_output(20)
            return mock

        with patch("shutil.which", return_value="/usr/bin/x"), \
             patch("subprocess.run", side_effect=fake_run), \
             patch("subprocess.Popen"), \
             patch("pathlib.Path.write_text"):
            self.manager._execute_soundcloud({"action": "play", "query": "x"})

        existing.terminate.assert_called_once()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_soundcloud_handler.py -v 2>&1 | tail -20
```

Expected: many failures — actions not yet implemented, queue behavior wrong.

- [ ] **Step 3: Replace `_execute_soundcloud` in `core/container_manager.py`**

Find the existing `_execute_soundcloud` method and replace its entire body with:

```python
    def _execute_soundcloud(self, tool_input: dict) -> str:
        """Soundcloud transport: play / stop / pause / resume / skip / volume_up / volume_down."""
        import shutil
        action = str(tool_input.get("action") or "play").strip().lower()

        # Transport actions reach mpv via the IPC socket.
        if action == "pause":
            return self._mpv_action_or_idle(["set_property", "pause", True], "Paused.")
        if action == "resume":
            return self._mpv_action_or_idle(["set_property", "pause", False], "Resumed.")
        if action == "skip":
            return self._mpv_action_or_idle(["playlist-next"], "Skipped.")
        if action == "volume_up":
            return self._mpv_action_or_idle(["add", "volume", 5], "Volume up.")
        if action == "volume_down":
            return self._mpv_action_or_idle(["add", "volume", -5], "Volume down.")

        if action == "stop":
            return self._stop_mpv()

        if action != "play":
            return f"Unknown action: {action!r}"

        # Play branch — search and queue 20 tracks.
        query = str(tool_input.get("query", "")).strip()
        if not query:
            return "No search query provided."

        if not shutil.which("yt-dlp"):
            return "yt-dlp not found. Install with: pip install yt-dlp"
        if not shutil.which("mpv"):
            return "mpv not found. Install with: sudo apt install mpv"

        # Stop any currently playing track before queueing a new search.
        self._terminate_mpv_process()
        # Also clear a stale socket file if mpv crashed without cleaning up.
        if os.path.exists(self._mpv_socket_path):
            try:
                os.unlink(self._mpv_socket_path)
            except OSError:
                pass

        try:
            result = subprocess.run(
                [
                    "yt-dlp",
                    "--get-title", "--get-url",
                    "-f", "bestaudio",
                    "--no-playlist",
                    "--cache-dir", "/tmp/yt-dlp-cache",
                    f"scsearch20:{query}",
                ],
                capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return f"Search timed out for '{query}'."
        except FileNotFoundError:
            return "yt-dlp not found on PATH."

        if result.returncode != 0 or not result.stdout.strip():
            return f"No results found for '{query}' on SoundCloud."

        lines = result.stdout.strip().splitlines()
        if len(lines) < 2 or len(lines) % 2 != 0:
            return f"Could not retrieve stream for '{query}'."

        # Pair into (title, url) tuples.
        pairs = [(lines[i], lines[i + 1]) for i in range(0, len(lines), 2)]
        first_title = pairs[0][0]
        urls = [url for _, url in pairs]

        self._mpv_process = subprocess.Popen(
            [
                "mpv",
                "--no-video",
                "--really-quiet",
                f"--input-ipc-server={self._mpv_socket_path}",
                *urls,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Write now_playing.json for the dashboard music widget.
        now_playing_path = Path.home() / ".miniclaw" / "now_playing.json"
        try:
            import time as _time
            now_playing_path.parent.mkdir(parents=True, exist_ok=True)
            now_playing_path.write_text(
                json.dumps({"title": first_title, "timestamp": _time.time()}),
                encoding="utf-8",
            )
        except OSError:
            pass

        return f"Now playing: {first_title}"

    def _mpv_action_or_idle(self, command: list, success_msg: str) -> str:
        """Run an mpv IPC command if mpv is alive, else report idle."""
        if not os.path.exists(self._mpv_socket_path):
            return "Nothing is playing."
        self._send_mpv_command(command)
        return success_msg

    def _terminate_mpv_process(self) -> None:
        if self._mpv_process and self._mpv_process.poll() is None:
            self._mpv_process.terminate()
        self._mpv_process = None

    def _stop_mpv(self) -> str:
        """Terminate mpv, clean up socket and now-playing files."""
        was_running = self._mpv_process and self._mpv_process.poll() is None
        if not was_running and not os.path.exists(self._mpv_socket_path):
            return "Nothing is playing."
        if was_running:
            self._mpv_process.terminate()
        self._mpv_process = None
        if os.path.exists(self._mpv_socket_path):
            try:
                os.unlink(self._mpv_socket_path)
            except OSError:
                pass
        now_playing = Path.home() / ".miniclaw" / "now_playing.json"
        try:
            now_playing.unlink(missing_ok=True)
        except OSError:
            pass
        return "Stopped."
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_soundcloud_handler.py -v 2>&1 | tail -20
```

Expected: all soundcloud handler tests pass.

- [ ] **Step 5: Run full suite to catch regressions**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
cd ~/linux/miniclaw && git add core/container_manager.py tests/test_soundcloud_handler.py && git commit -m "feat(soundcloud): pause/resume/skip/volume actions + 20-track queue"
```

---

## Phase 2 — SKILL.md and intent_patterns

Phase-boundary checkpoint: skill loads cleanly with the new schema; tier-router pattern tests pass.

### Task 3: SKILL.md — expose action enum

**Files:**
- Modify: `skills/soundcloud/SKILL.md`

- [ ] **Step 1: Replace the entire SKILL.md content**

```markdown
---
name: soundcloud
description: Play, stop, pause, resume, skip, or adjust volume on SoundCloud music. A play
  request queues 20 tracks matching the query; subsequent skip commands advance through
  the queue.
---
# SoundCloud Skill

## When to use

- **Play music** — "play [song/artist/genre]", "put on some [genre]", "I want to hear [X]"
- **Stop** — "stop the music", "stop playing", "halt the audio"
- **Pause / resume** — "pause the music", "resume", "unpause", "continue music"
- **Skip** — "skip", "next song", "skip this track"
- **Volume** — "volume up", "louder", "turn it down"

## Inputs

```yaml
type: object
properties:
  action:
    type: string
    enum: [play, stop, pause, resume, skip, volume_up, volume_down]
    description: The transport command to issue. Defaults to play.
  query:
    type: string
    description: Song name, artist, or genre. Required when action is play.
required:
  - action
```

## How to respond

For play, confirm the genre/song. For stop / pause / resume / skip / volume, brief
acknowledgement ("Stopped.", "Paused.", "Resumed.", "Skipped.", "Volume up."). If
nothing is playing for a transport command, say so plainly.
```

- [ ] **Step 2: Verify the skill still loads**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -c "
from core.skill_loader import SkillLoader
loader = SkillLoader()
loader.load_all()
s = loader.skills.get('soundcloud')
print('loaded:', bool(s))
print('schema enum:', s.tool_definition['input_schema']['properties']['action'].get('enum') if s else None)
"
```

Expected: `loaded: True`, schema enum lists all seven actions.

- [ ] **Step 3: Run full suite to ensure no regression**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
cd ~/linux/miniclaw && git add skills/soundcloud/SKILL.md && git commit -m "feat(soundcloud): expose action enum + transport routing hints"
```

### Task 4: intent_patterns.yaml — fix dispatch entries

**Files:**
- Modify: `config/intent_patterns.yaml`
- Modify: `tests/test_tier_router.py` (append)

- [ ] **Step 1: Append failing tests**

```python
# Append to tests/test_tier_router.py

import tempfile
from pathlib import Path

import yaml as _yaml

from core.tier_router import TierRouter as _TierRouter


_REPO_PATTERNS = (
    Path(__file__).resolve().parent.parent / "config" / "intent_patterns.yaml"
)


def _real_router():
    """Load the actual project patterns file (the one the orchestrator uses)."""
    return _TierRouter(patterns_path=_REPO_PATTERNS)


class TestMusicTransportPatterns(unittest.TestCase):
    def setUp(self):
        self.router = _real_router()

    def test_stop_routes_to_stop(self):
        r = self.router.route("stop the music")
        self.assertEqual(r.tier, "direct")
        self.assertEqual(r.skill, "soundcloud")
        self.assertEqual(r.args, {"action": "stop"})

    def test_halt_routes_to_stop(self):
        r = self.router.route("halt")
        self.assertEqual(r.tier, "direct")
        self.assertEqual(r.skill, "soundcloud")
        self.assertEqual(r.args, {"action": "stop"})

    def test_pause_routes_to_pause(self):
        r = self.router.route("pause the music")
        self.assertEqual(r.tier, "direct")
        self.assertEqual(r.skill, "soundcloud")
        self.assertEqual(r.args, {"action": "pause"})

    def test_pause_does_not_match_stop_pattern(self):
        # Critical: pause must NOT route to action=stop.
        r = self.router.route("pause")
        self.assertEqual(r.args.get("action"), "pause")

    def test_resume_routes_to_resume(self):
        r = self.router.route("resume")
        self.assertEqual(r.skill, "soundcloud")
        self.assertEqual(r.args, {"action": "resume"})

    def test_continue_routes_to_resume(self):
        r = self.router.route("continue music")
        self.assertEqual(r.args, {"action": "resume"})

    def test_skip_routes_to_skip(self):
        r = self.router.route("skip")
        self.assertEqual(r.skill, "soundcloud")
        self.assertEqual(r.args, {"action": "skip"})

    def test_next_song_routes_to_skip(self):
        r = self.router.route("next song")
        self.assertEqual(r.args, {"action": "skip"})

    def test_skip_this_track_routes_to_skip(self):
        r = self.router.route("skip this track")
        self.assertEqual(r.args, {"action": "skip"})

    def test_volume_up_routes_to_volume_up(self):
        r = self.router.route("volume up")
        self.assertEqual(r.args, {"action": "volume_up"})

    def test_louder_routes_to_volume_up(self):
        r = self.router.route("louder")
        self.assertEqual(r.args, {"action": "volume_up"})

    def test_volume_down_routes_to_volume_down(self):
        r = self.router.route("volume down")
        self.assertEqual(r.args, {"action": "volume_down"})

    def test_no_stale_soundcloud_play_skill_name(self):
        # Read raw file; ensure no entry references the pre-migration name.
        text = _REPO_PATTERNS.read_text(encoding="utf-8")
        self.assertNotIn("soundcloud_play", text)

    def test_stop_right_there_does_not_match(self):
        # Anchored regex shouldn't match phrases that just contain "stop".
        r = self.router.route("stop right there partner")
        self.assertNotEqual(r.tier, "direct")
```

- [ ] **Step 2: Run tests to verify failure**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_tier_router.py::TestMusicTransportPatterns -v 2>&1 | tail -15
```

Expected: failures because the patterns file still references `soundcloud_play` and lumps pause with stop.

- [ ] **Step 3: Replace soundcloud-related entries in `config/intent_patterns.yaml`**

Open `config/intent_patterns.yaml`. Find the existing entries that begin with the stop/pause/halt pattern and the volume_up / volume_down entries. Replace them all with the block below. Keep the existing `close_session` entry and the entire `escalate:` section unchanged.

```yaml
  - pattern: "^(stop|halt)(\\s+music|\\s+playing|\\s+audio)?[.!?]?$"
    skill: soundcloud
    args: {action: stop}

  - pattern: "^pause(\\s+music|\\s+playing|\\s+audio)?[.!?]?$"
    skill: soundcloud
    args: {action: pause}

  - pattern: "^(resume|continue|unpause)(\\s+music|\\s+playing|\\s+audio)?[.!?]?$"
    skill: soundcloud
    args: {action: resume}

  - pattern: "^(skip|next)(\\s+song|\\s+track|\\s+this)?[.!?]?$"
    skill: soundcloud
    args: {action: skip}

  - pattern: "^(volume up|turn it up|louder)[.!?]?$"
    skill: soundcloud
    args: {action: volume_up}

  - pattern: "^(volume down|turn it down|quieter|lower the volume)[.!?]?$"
    skill: soundcloud
    args: {action: volume_down}
```

- [ ] **Step 4: Run tests to verify pass**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/test_tier_router.py -v 2>&1 | tail -25
```

Expected: all tier_router tests (including the 14 new transport-pattern tests) pass. Existing tier_router tests must also continue to pass.

- [ ] **Step 5: Run full suite**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
cd ~/linux/miniclaw && git add config/intent_patterns.yaml tests/test_tier_router.py && git commit -m "feat(intent-patterns): rewire music transport regexes for soundcloud + skip/pause/resume"
```

---

## Phase 3 — Documentation + integration sanity

Phase-boundary checkpoint: full suite green; on-Pi smoke verifies the regex dispatch path actually fires (separate manual step).

### Task 5: WORKING_MEMORY.md — close the gap

**Files:**
- Modify: `WORKING_MEMORY.md`

- [ ] **Step 1: Locate the known-gaps section**

```bash
grep -n "Voice control for music\|Voice stop\|music stop" ~/linux/miniclaw/WORKING_MEMORY.md
```

Expected: a line under known gaps mentioning music stop/pause is incomplete.

- [ ] **Step 2: Update the entry**

Open `WORKING_MEMORY.md`. Find the bullet that reads:

```
- Voice stop/pause control for music is still incomplete.
```

(The exact wording may differ — search for "music" in the gaps section.)

Replace it with:

```
- ~~Voice stop/pause control for music is still incomplete.~~ Closed 2026-04-25.
  soundcloud handler now supports play / stop / pause / resume / skip / volume_up / volume_down via mpv IPC. play queues 20 tracks; transport actions are regex-dispatched through TierRouter (no LLM round-trip). On-Pi validation pending Ollama setup so TierRouter activates.
```

Also add a milestone bullet under "Recent Durable Milestones":

```
- 2026-04-25: shipped voice transport for SoundCloud music
  pause / resume / skip / volume on top of existing play / stop
  20-track queue per play query; mpv IPC for in-flight control
  intent_patterns.yaml regex dispatch; SKILL.md exposes action enum
```

- [ ] **Step 3: Run full suite**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
cd ~/linux/miniclaw && git add WORKING_MEMORY.md && git commit -m "docs: close music stop/pause gap in WORKING_MEMORY"
```

### Task 6: Integration sanity (no commit unless something needs fixing)

- [ ] **Step 1: Local smoke — load orchestrator and inspect routing**

```bash
cd ~/linux/miniclaw && source .venv/bin/activate && OLLAMA_ENABLED=true python3 -c "
import os
os.environ['ANTHROPIC_API_KEY'] = os.environ.get('ANTHROPIC_API_KEY', 'test')
from core.tier_router import TierRouter
from pathlib import Path
patterns = Path('config/intent_patterns.yaml')
router = TierRouter(patterns_path=patterns)
for phrase in ['stop the music', 'pause', 'resume', 'skip', 'next song', 'volume up', 'turn it down']:
    r = router.route(phrase)
    print(f'{phrase!r:<25s} → tier={r.tier!s:<8s} skill={r.skill!s:<12s} args={r.args}')
"
```

Expected: every phrase routes `tier=direct skill=soundcloud` with the correct action arg.

- [ ] **Step 2: On-Pi smoke (manual, only when Mason is on the Pi with Ollama running)**

This step is for the user to run on the actual Raspberry Pi after enabling `OLLAMA_ENABLED=true` in `.env`:

```bash
./run.sh --voice
# then say:
#   "computer, play country"        → expect 20-track queue starts playing
#   "computer, pause the music"     → expect playback pauses, mpv stays alive
#   "computer, resume"              → expect playback resumes from same spot
#   "computer, skip"                → expect next track in queue starts immediately
#   "computer, volume down"         → expect volume drops by 5
#   "computer, stop"                → expect mpv terminates, silence returns
```

If any phrase fails to route correctly or the action doesn't take effect, capture the orchestrator log and open a follow-up.

- [ ] **Step 3: No commit**

This task is verification-only. Only commit if Step 1 surfaces a bug requiring a fix.

---

## Self-Review

**Spec coverage:**

- ✅ IPC helper (`_send_mpv_command`) — Task 1.
- ✅ `_mpv_socket_path` attribute — Task 1.
- ✅ `pause`, `resume`, `skip`, `volume_up`, `volume_down` actions — Task 2.
- ✅ `stop` cleans socket + now_playing — Task 2.
- ✅ `play` queues 20 tracks via `scsearch20:` — Task 2.
- ✅ `play` passes `--input-ipc-server=...` — Task 2.
- ✅ `play` terminates existing mpv first — Task 2.
- ✅ `play` clears stale socket file — Task 2.
- ✅ Default action `play` for backward compat — Task 2.
- ✅ Unknown action returns error — Task 2.
- ✅ SKILL.md action enum + when-to-use — Task 3.
- ✅ intent_patterns.yaml: rename + split + add skip — Task 4.
- ✅ "pause" no longer maps to stop — Task 4 (`test_pause_does_not_match_stop_pattern`).
- ✅ No stale `soundcloud_play` references — Task 4 (`test_no_stale_soundcloud_play_skill_name`).
- ✅ Anchored regex (no false-positive on "stop right there") — Task 4 (`test_stop_right_there_does_not_match`).
- ✅ WORKING_MEMORY.md update — Task 5.
- ✅ On-Pi smoke instructions — Task 6.

**Placeholder scan:** no TBD/TODO/"implement later". Every code block is complete.

**Type consistency:**
- `_mpv_socket_path: str` defined in Task 1 init; used by `_send_mpv_command` (Task 1), `_mpv_action_or_idle` (Task 2), `_stop_mpv` (Task 2), and play branch (Task 2).
- `_send_mpv_command(args: list) -> dict | None` consistent across helper definition (Task 1), action handlers (Task 2), and tests (both tasks).
- `_mpv_action_or_idle(command: list, success_msg: str) -> str` defined Task 2; called five times within `_execute_soundcloud` Task 2.
- `_terminate_mpv_process()` defined Task 2; called from `_execute_soundcloud` play branch Task 2.
- `_stop_mpv()` defined Task 2; called when `action == "stop"` Task 2.
- Skill name `soundcloud` consistent across SKILL.md (Task 3) and intent_patterns.yaml (Task 4) and tests (Task 4 assertion).
- Action strings (`pause`, `resume`, `skip`, `volume_up`, `volume_down`) match between handler dispatch (Task 2), SKILL.md enum (Task 3), and intent_patterns.yaml args (Task 4) and pattern tests (Task 4).

**Scope:** one cohesive plan, ~30 new test cases, ~150 LOC of handler changes plus YAML and SKILL.md updates.
