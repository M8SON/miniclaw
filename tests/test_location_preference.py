import tempfile
import unittest
from pathlib import Path

from core.location_preference import resolve_location


class LocationPreferenceTests(unittest.TestCase):
    def test_resolve_location_prefers_explicit_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "2026-04-02_location.md").write_text(
                "---\ndate: 2026-04-02\ntopic: location\n---\n\nDenver,CO\n",
                encoding="utf-8",
            )

            resolved = resolve_location("Seattle,WA", vault_path=vault, default="New York,NY")

        self.assertEqual(resolved, "Seattle,WA")

    def test_resolve_location_falls_back_to_memory_before_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "2026-04-02_location.md").write_text(
                "---\ndate: 2026-04-02\ntopic: location\n---\n\nDenver,CO\n",
                encoding="utf-8",
            )

            resolved = resolve_location("", vault_path=vault, default="New York,NY")

        self.assertEqual(resolved, "Denver,CO")

    def test_resolve_location_uses_default_when_memory_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)

            resolved = resolve_location("", vault_path=vault, default="New York,NY")

        self.assertEqual(resolved, "New York,NY")
