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


if __name__ == "__main__":
    unittest.main()
