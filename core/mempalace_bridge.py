"""
Optional MemPalace integration helpers.

This module lets MiniClaw read compact wake-up memory from MemPalace and,
when enabled, file saved memories into the palace without making MemPalace a
hard dependency.
"""

import importlib
import importlib.util
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
        """Return wake-up memory text trimmed to the configured token budget."""
        text = self._load_via_python_package()
        if not text:
            text = self._load_via_cli()
        if not text:
            text = self._load_via_chromadb_wake()
        return self._trim_to_budget(text)

    def search(self, query: str, limit: int = 5, budget_tokens: int | None = None) -> str:
        """Return compact semantic search results for a live user message."""
        text = self._search_via_python_package(query=query, limit=limit)
        if not text:
            text = self._search_via_cli(query=query, limit=limit)
        if not text:
            text = self._search_via_chromadb(query=query, limit=limit)

        original_budget = self.max_tokens
        try:
            self.max_tokens = budget_tokens
            return self._trim_to_budget(text)
        finally:
            self.max_tokens = original_budget

    def is_available(self) -> bool:
        """Return True when MemPalace or chromadb is available for semantic memory."""
        try:
            if importlib.util.find_spec("mempalace.layers") is not None:
                return True
        except ModuleNotFoundError:
            pass
        if shutil.which("mempalace") is not None:
            return True
        return self._import_chromadb() is not None

    def save_memory(
        self, topic: str, content: str, source_file: str = "", note_id: str = ""
    ) -> bool:
        """File a saved memory into chromadb.

        note_id: deterministic ID derived from the vault filename (e.g. vault_2026-04-07_coffee).
        If omitted a hash-based ID is generated (legacy path — avoid for vault-backed notes).
        Uses upsert so calling with the same note_id replaces the existing entry.
        """
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
        drawer_id = note_id or f"drawer_{wing}_{room}_{abs(hash((topic, content, timestamp)))}"
        document = f"{topic}\n\n{content}".strip()

        collection.upsert(
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

    def _load_via_python_package(self) -> str:
        """Use the locally installed MemPalace Python package when available."""
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

    def _search_via_python_package(self, query: str, limit: int) -> str:
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

    def sync_vault(self, vault_path) -> int:
        """Upsert all markdown vault notes into chromadb using deterministic IDs.

        Safe to call repeatedly — upsert overwrites only when content changes.
        Returns the number of notes synced.
        """
        chromadb = self._import_chromadb()
        if chromadb is None:
            return 0

        vault_path = Path(vault_path)
        if not vault_path.is_dir():
            return 0

        try:
            self.palace_path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(self.palace_path))
            collection_name = os.environ.get(
                "MEMPALACE_COLLECTION_NAME", self.DEFAULT_COLLECTION_NAME
            )
            collection = client.get_or_create_collection(collection_name)

            wing = self.wing or os.environ.get("MEMPALACE_MEMORY_WING", self.DEFAULT_MEMORY_WING)
            room = self.room or self.DEFAULT_MEMORY_ROOM

            synced = 0
            for md_file in sorted(vault_path.glob("*.md")):
                try:
                    text = md_file.read_text(encoding="utf-8")
                    topic = md_file.stem
                    body = text.strip()
                    if text.startswith("---"):
                        parts = text.split("---", 2)
                        if len(parts) >= 3:
                            body = parts[2].strip()
                            for line in parts[1].splitlines():
                                if line.startswith("topic:"):
                                    topic = line.split(":", 1)[1].strip()
                                    break
                    if not body:
                        continue

                    stem_parts = md_file.stem.split("_", 1)
                    filed_at = stem_parts[0] if len(stem_parts) > 1 else ""

                    collection.upsert(
                        ids=[f"vault_{md_file.stem}"],
                        documents=[f"{topic}\n\n{body}"],
                        metadatas=[{
                            "wing": wing,
                            "room": room,
                            "source_file": str(md_file),
                            "topic": topic,
                            "filed_at": filed_at,
                            "added_by": "miniclaw_vault_sync",
                        }],
                    )
                    synced += 1
                except Exception:
                    continue
            return synced
        except Exception:
            return 0

    def _chromadb_collection(self, chromadb):
        """Return the chromadb collection, or None if it doesn't exist yet."""
        try:
            self.palace_path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(self.palace_path))
            collection_name = os.environ.get(
                "MEMPALACE_COLLECTION_NAME", self.DEFAULT_COLLECTION_NAME
            )
            return client.get_collection(collection_name)
        except Exception:
            return None

    def _load_via_chromadb_wake(self) -> str:
        """Load all memories from chromadb sorted by recency."""
        chromadb = self._import_chromadb()
        if chromadb is None:
            return ""
        collection = self._chromadb_collection(chromadb)
        if collection is None or collection.count() == 0:
            return ""
        try:
            results = collection.get(include=["documents", "metadatas"])
            pairs = list(zip(results["documents"], results["metadatas"]))
            pairs.sort(key=lambda x: x[1].get("filed_at", ""), reverse=True)
            return "\n\n".join(doc for doc, _ in pairs).strip()
        except Exception:
            return ""

    def _search_via_chromadb(self, query: str, limit: int) -> str:
        """Semantic search over memories stored in chromadb."""
        chromadb = self._import_chromadb()
        if chromadb is None:
            return ""
        collection = self._chromadb_collection(chromadb)
        if collection is None:
            return ""
        count = collection.count()
        if count == 0:
            return ""
        try:
            results = collection.query(
                query_texts=[query],
                n_results=min(limit, count),
            )
            docs = results.get("documents", [[]])[0]
            return "\n\n".join(docs).strip()
        except Exception:
            return ""

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
