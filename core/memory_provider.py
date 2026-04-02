"""
Memory provider for MiniClaw.

Loads persisted markdown notes from the configured memory vault and returns
their body text for prompt injection.
"""

import json
import os
from pathlib import Path


class MemoryProvider:
    """Reads saved memory notes from the configured vault directory."""

    def __init__(
        self,
        vault_path: Path | None = None,
        max_tokens: int | None = 2000,
    ):
        self.vault_path = vault_path or Path(
            os.environ.get("MEMORY_VAULT_PATH", Path.home() / ".miniclaw" / "memory")
        )
        self.max_tokens = max_tokens

    def load_for_prompt(self) -> str:
        """Return the newest whole memory notes that fit the configured token budget."""
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
