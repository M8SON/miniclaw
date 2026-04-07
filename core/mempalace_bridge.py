"""
Optional MemPalace integration helpers.

This module lets MiniClaw read compact wake-up memory from MemPalace and,
when enabled, file saved memories into the palace without making MemPalace a
hard dependency.
"""

import importlib
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


class MemPalaceBridge:
    """Best-effort bridge to an optional local MemPalace installation."""

    DEFAULT_PALACE_PATH = Path.home() / ".mempalace" / "palace"
    DEFAULT_COLLECTION_NAME = "mempalace_drawers"
    DEFAULT_MEMORY_WING = "wing_miniclaw"
    DEFAULT_MEMORY_ROOM = "assistant-memory"

    def __init__(
        self,
        palace_path: Path | None = None,
        wing: str | None = None,
        room: str | None = None,
        max_tokens: int | None = None,
    ):
        self.palace_path = Path(
            palace_path
            or os.environ.get("MEMPALACE_PALACE_PATH")
            or os.environ.get("MEMPAL_PALACE_PATH")
            or self.DEFAULT_PALACE_PATH
        )
        self.wing = wing or os.environ.get("MEMPALACE_WING")
        self.room = room or os.environ.get("MEMPALACE_MEMORY_ROOM", self.DEFAULT_MEMORY_ROOM)
        self.max_tokens = max_tokens

    def load_wake_up(self) -> str:
        """Return MemPalace wake-up text trimmed to the configured token budget."""
        text = self._load_via_python_api()
        if not text:
            text = self._load_via_cli()
        return self._trim_to_budget(text)

    def search(self, query: str, limit: int = 5, budget_tokens: int | None = None) -> str:
        """Return compact MemPalace search results for a live user message."""
        text = self._search_via_python_api(query=query, limit=limit)
        if not text:
            text = self._search_via_cli(query=query, limit=limit)

        original_budget = self.max_tokens
        try:
            self.max_tokens = budget_tokens
            return self._trim_to_budget(text)
        finally:
            self.max_tokens = original_budget

    def is_available(self) -> bool:
        """Return True when MemPalace is installed or its CLI is available."""
        try:
            if importlib.util.find_spec("mempalace.layers") is not None:
                return True
        except ModuleNotFoundError:
            pass
        return shutil.which("mempalace") is not None

    def save_memory(self, topic: str, content: str, source_file: str = "") -> bool:
        """File a saved memory into the configured palace wing and room."""
        wing = self.wing or os.environ.get("MEMPALACE_MEMORY_WING", self.DEFAULT_MEMORY_WING)
        room = self.room
        if not wing or not room:
            return False

        chromadb = self._import_chromadb()
        if chromadb is None:
            return False

        self.palace_path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(self.palace_path))

        collection_name = os.environ.get(
            "MEMPALACE_COLLECTION_NAME",
            self.DEFAULT_COLLECTION_NAME,
        )
        collection = client.get_or_create_collection(collection_name)

        timestamp = datetime.now().isoformat()
        drawer_id = f"drawer_{wing}_{room}_{abs(hash((topic, content, timestamp)))}"
        document = f"{topic}\n\n{content}".strip()

        collection.add(
            ids=[drawer_id],
            documents=[document],
            metadatas=[
                {
                    "wing": wing,
                    "room": room,
                    "source_file": source_file,
                    "chunk_index": 0,
                    "added_by": "miniclaw",
                    "topic": topic,
                    "filed_at": timestamp,
                }
            ],
        )
        return True

    def _load_via_python_api(self) -> str:
        """Use MemPalace's Python API when the package is installed."""
        try:
            layers_module = importlib.import_module("mempalace.layers")
        except ImportError:
            return ""

        memory_stack_cls = getattr(layers_module, "MemoryStack", None)
        if memory_stack_cls is None:
            return ""

        try:
            stack = memory_stack_cls(palace_path=str(self.palace_path))
            return str(stack.wake_up(wing=self.wing)).strip()
        except Exception:
            return ""

    def _load_via_cli(self) -> str:
        """Fallback to the MemPalace CLI when available in PATH."""
        cli_path = shutil.which("mempalace")
        if not cli_path:
            return ""

        cmd = [cli_path, "wake-up", "--palace", str(self.palace_path)]
        if self.wing:
            cmd.extend(["--wing", self.wing])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""

        if result.returncode != 0:
            return ""
        return result.stdout.strip()

    def _search_via_python_api(self, query: str, limit: int) -> str:
        try:
            layers_module = importlib.import_module("mempalace.layers")
        except ImportError:
            return ""

        memory_stack_cls = getattr(layers_module, "MemoryStack", None)
        if memory_stack_cls is None:
            return ""

        try:
            stack = memory_stack_cls(palace_path=str(self.palace_path))
            return str(stack.search(query=query, wing=self.wing, n_results=limit)).strip()
        except Exception:
            return ""

    def _search_via_cli(self, query: str, limit: int) -> str:
        cli_path = shutil.which("mempalace")
        if not cli_path:
            return ""

        cmd = [cli_path, "search", query, "--palace", str(self.palace_path)]
        if self.wing:
            cmd.extend(["--wing", self.wing])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""

        if result.returncode != 0:
            return ""

        lines = result.stdout.splitlines()
        if limit > 0:
            capped = []
            hits_seen = 0
            for line in lines:
                if line.strip().startswith("["):
                    hits_seen += 1
                    if hits_seen > limit:
                        break
                capped.append(line)
            return "\n".join(capped).strip()
        return result.stdout.strip()

    def _trim_to_budget(self, text: str) -> str:
        """Trim text by line while preserving a rough token budget."""
        if not text:
            return ""
        if self.max_tokens is None or self.max_tokens <= 0:
            return text
        if self._estimate_tokens(text) <= self.max_tokens:
            return text

        retained_lines = []
        retained_tokens = 0
        for line in text.splitlines():
            line_tokens = self._estimate_tokens(line)
            if retained_lines and retained_tokens + line_tokens > self.max_tokens:
                break
            retained_lines.append(line)
            retained_tokens += line_tokens
        return "\n".join(retained_lines).strip()

    def _import_chromadb(self):
        try:
            return importlib.import_module("chromadb")
        except ImportError:
            return None

    def _estimate_tokens(self, text: str) -> int:
        serialized = json.dumps(text, ensure_ascii=False)
        return max(1, len(serialized) // 4)
