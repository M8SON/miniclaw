"""Tests for TierRouter — transcript → tier routing."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Ensure core/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

PATTERNS_FILE = Path(__file__).parent.parent / "config" / "intent_patterns.yaml"


def _make_router(claude_only=None, skill_selector=None):
    from core.tier_router import TierRouter
    return TierRouter(
        patterns_path=PATTERNS_FILE,
        skill_selector=skill_selector,
        claude_only_skills=claude_only,
    )


class TestDispatchPatterns(unittest.TestCase):

    def test_stop_routes_direct(self):
        router = _make_router()
        result = router.route("stop")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.skill, "soundcloud_play")
        self.assertEqual(result.args, {"action": "stop"})

    def test_stop_music_routes_direct(self):
        router = _make_router()
        result = router.route("stop music")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.skill, "soundcloud_play")

    def test_pause_routes_direct(self):
        router = _make_router()
        result = router.route("pause")
        self.assertEqual(result.tier, "direct")

    def test_volume_up_routes_direct(self):
        router = _make_router()
        result = router.route("volume up")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.args, {"action": "volume_up"})

    def test_volume_down_routes_direct(self):
        router = _make_router()
        result = router.route("volume down")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.args, {"action": "volume_down"})

    def test_goodbye_routes_direct_with_action(self):
        router = _make_router()
        result = router.route("goodbye")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.action, "close_session")
        self.assertIsNone(result.skill)

    def test_bye_routes_direct_session_close(self):
        router = _make_router()
        result = router.route("bye")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.action, "close_session")

    def test_unmatched_routes_ollama(self):
        router = _make_router()
        result = router.route("play some jazz")
        self.assertEqual(result.tier, "ollama")

    def test_missing_patterns_file_does_not_crash(self):
        from core.tier_router import TierRouter
        router = TierRouter(
            patterns_path=Path("/nonexistent/patterns.yaml"),
            skill_selector=None,
        )
        result = router.route("stop")
        # No patterns loaded — falls through to ollama
        self.assertEqual(result.tier, "ollama")


if __name__ == "__main__":
    unittest.main()
