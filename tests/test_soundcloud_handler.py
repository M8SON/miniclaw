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


class TestActionDispatch(unittest.TestCase):
    def setUp(self):
        self.cm_module = _load_container_manager()
        self.manager = self.cm_module.ContainerManager()

    def test_default_action_is_play(self):
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
        proc.poll.return_value = None
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
        urls_passed = [a for a in popen_args if a.startswith("https://example.invalid/")]
        self.assertEqual(len(urls_passed), 20)
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
            mock.stdout = "Track 0\nhttps://x/0.mp3\nOrphan title\n"
            return mock

        with patch("shutil.which", return_value="/usr/bin/x"), \
             patch("subprocess.run", side_effect=fake_run):
            result = self.manager._execute_soundcloud({"action": "play", "query": "x"})

        self.assertIn("Could not retrieve", result)

    def test_play_terminates_existing_mpv_before_starting_new(self):
        existing = MagicMock()
        existing.poll.return_value = None
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


if __name__ == "__main__":
    unittest.main()
