"""
Memory provider for MiniClaw.

Loads persisted memory for prompt injection. Supports the original markdown
vault flow and an optional MemPalace-backed wake-up flow.
"""

import json
import os
import re
from datetime import date
from pathlib import Path

from core.mempalace_bridge import MemPalaceBridge


class MemoryProvider:
    """Reads saved memory from the configured backend."""

    def __init__(
        self,
        vault_path: Path | None = None,
        max_tokens: int | None = 2000,
        backend: str | None = None,
        mempalace_path: Path | None = None,
        mempalace_wing: str | None = None,
        recall_max_tokens: int | None = 600,
        recall_max_results: int = 3,
    ):
        self.vault_path = vault_path or Path(
            os.environ.get("MEMORY_VAULT_PATH", Path.home() / ".miniclaw" / "memory")
        )
        self.max_tokens = max_tokens
        self.backend = (backend or os.environ.get("MEMORY_BACKEND", "auto")).strip().lower()
        self.recall_max_tokens = recall_max_tokens
        self.recall_max_results = recall_max_results
        self.mempalace = MemPalaceBridge(
            palace_path=mempalace_path,
            wing=mempalace_wing,
            max_tokens=max_tokens,
        )

        # Sync existing vault notes into chromadb on startup so all memories
        # are semantically searchable, not just ones saved after chromadb was added.
        if self.backend != "vault":
            try:
                self.mempalace.sync_vault(self.vault_path)
            except Exception:
                pass

    def load_for_prompt(self) -> str:
        """Return prompt-ready persisted memory from the selected backend."""
        if self.should_use_mempalace():
            mempalace_text = self.mempalace.load_wake_up()
            if mempalace_text:
                return mempalace_text

        return self._load_markdown_notes()

    def save_note(self, topic: str, content: str) -> str:
        """Write a memory note to the vault. Returns the filename on success, empty string on failure."""
        try:
            self.vault_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return ""

        date_str = date.today().isoformat()
        slug = re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")
        filename = f"{date_str}_{slug}.md"

        # Update in place if a note with the same topic slug already exists.
        existing = sorted(self.vault_path.glob(f"*_{slug}.md"))
        note_path = existing[-1] if existing else self.vault_path / filename

        note = f"---\ndate: {date_str}\ntopic: {topic}\n---\n\n{content}\n"
        try:
            note_path.write_text(note, encoding="utf-8")
        except OSError:
            return ""

        # Mirror to chromadb so skill remember-blocks are semantically searchable.
        # Use vault_{stem} as the ID so sync_vault() stays idempotent.
        if self.backend != "vault" and self.mempalace.is_available():
            try:
                self.mempalace.save_memory(
                    topic, content,
                    source_file=str(note_path),
                    note_id=f"vault_{note_path.stem}",
                )
            except Exception:
                pass

        return note_path.name

    def recall_for_message(self, user_message: str) -> str:
        """Return compact live memory recall for the current user message."""
        query = user_message.strip()
        if not query:
            return ""

        if self.should_use_mempalace():
            return self.mempalace.search(
                query=query,
                limit=self.recall_max_results,
                budget_tokens=self.recall_max_tokens,
            )

        return self._keyword_search_vault(query)

    def _keyword_search_vault(self, query: str) -> str:
        """Search vault notes by keyword. Simple fallback when MemPalace is not available."""
        if not self.vault_path.is_dir():
            return ""

        stop_words = {
            "what", "when", "where", "which", "that", "this", "with", "have",
            "from", "they", "been", "were", "will", "would", "could", "should",
            "about", "some", "than", "then", "just", "like", "into", "your",
            "does", "tell", "know", "want", "said", "also", "over", "back",
            "there", "here", "more", "very", "much", "such", "even", "most",
        }
        keywords = {
            w.lower().strip(".,?!'\"")
            for w in query.split()
            if len(w) > 3 and w.lower().strip(".,?!'\"") not in stop_words
        }
        if not keywords:
            return ""

        matched = []
        for md_file in sorted(self.vault_path.glob("*.md"), reverse=True):
            try:
                text = md_file.read_text(encoding="utf-8")
            except OSError:
                continue
            body = self._strip_frontmatter(text)
            if any(kw in body.lower() for kw in keywords):
                matched.append(body)
            if len(matched) >= self.recall_max_results:
                break

        if not matched:
            return ""

        # Trim to recall token budget
        retained = []
        retained_tokens = 0
        budget = self.recall_max_tokens
        for note in matched:
            note_tokens = self._estimate_tokens(note)
            if budget and retained_tokens + note_tokens > budget:
                break
            retained.append(note)
            retained_tokens += note_tokens

        return "\n".join(retained)

    def should_use_mempalace(self) -> bool:
        """Return True when MemPalace should be used as the preferred backend."""
        if self.backend == "mempalace":
            return True
        if self.backend == "vault":
            return False
        return self.mempalace.is_available()

    def _load_markdown_notes(self) -> str:
        """Return the newest whole markdown notes that fit the token budget."""
        if not self.vault_path.is_dir():
            return ""

        notes = []
        for md_file in sorted(self.vault_path.glob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8")
            except OSError:
                continue

            body = self._strip_frontmatter(text)
            if body:
                notes.append(body)

        return "\n".join(self._select_notes_for_prompt(notes))

    def _strip_frontmatter(self, text: str) -> str:
        """Remove optional YAML frontmatter and return the markdown body."""
        if text.startswith("---"):
            parts = text.split("---", 2)
            return parts[2].strip() if len(parts) >= 3 else text.strip()
        return text.strip()

    def _select_notes_for_prompt(self, notes: list[str]) -> list[str]:
        """Keep the newest whole notes that fit the configured memory budget."""
        if not notes:
            return []
        if self.max_tokens is None or self.max_tokens <= 0:
            return notes

        retained = []
        retained_tokens = 0

        for note in reversed(notes):
            note_tokens = self._estimate_tokens(note)
            if retained and retained_tokens + note_tokens > self.max_tokens:
                break
            retained.append(note)
            retained_tokens += note_tokens

        retained.reverse()
        return retained

    def _estimate_tokens(self, text: str) -> int:
        """Approximate token count for a memory note using serialized text length."""
        serialized = json.dumps(text, ensure_ascii=False)
        return max(1, len(serialized) // 4)
