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
        self.assertEqual(result.skill, "soundcloud")
        self.assertEqual(result.args, {"action": "stop"})

    def test_stop_music_routes_direct(self):
        router = _make_router()
        result = router.route("stop music")
        self.assertEqual(result.tier, "direct")
        self.assertEqual(result.skill, "soundcloud")

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


class TestEscalatePatterns(unittest.TestCase):

    def test_install_skill_escalates_to_claude(self):
        router = _make_router()
        result = router.route("install a skill that checks my calendar")
        self.assertEqual(result.tier, "claude")

    def test_add_tool_escalates(self):
        router = _make_router()
        result = router.route("add a tool that does X")
        self.assertEqual(result.tier, "claude")

    def test_remember_escalates(self):
        router = _make_router()
        result = router.route("remember that I prefer dark mode")
        self.assertEqual(result.tier, "claude")

    def test_long_explain_escalates(self):
        router = _make_router()
        result = router.route("explain how the skill system works in detail")
        self.assertEqual(result.tier, "claude")

    def test_short_explain_does_not_escalate(self):
        router = _make_router()
        result = router.route("explain this")
        # Short — no escalate, goes to ollama
        self.assertEqual(result.tier, "ollama")


class TestSkillPrediction(unittest.TestCase):

    def _make_selector_predicting(self, skill_name: str):
        """Return a mock SkillSelector that always predicts skill_name."""
        sel = MagicMock()
        sel.available = True
        sel.select = MagicMock(return_value={skill_name})
        return sel

    def test_claude_only_skill_routes_to_claude(self):
        sel = self._make_selector_predicting("install_skill")
        router = _make_router(claude_only={"install_skill"}, skill_selector=sel)
        result = router.route("make me a new skill")
        self.assertEqual(result.tier, "claude")

    def test_non_claude_only_skill_routes_to_ollama(self):
        sel = self._make_selector_predicting("weather")
        router = _make_router(claude_only={"install_skill"}, skill_selector=sel)
        result = router.route("what is the weather in London")
        self.assertEqual(result.tier, "ollama")

    def test_no_skill_selector_defaults_to_ollama(self):
        router = _make_router(skill_selector=None)
        result = router.route("some unknown request")
        self.assertEqual(result.tier, "ollama")

    def test_unavailable_selector_defaults_to_ollama(self):
        sel = MagicMock()
        sel.available = False
        router = _make_router(skill_selector=sel)
        result = router.route("some unknown request")
        self.assertEqual(result.tier, "ollama")

    def test_selector_returning_none_defaults_to_ollama(self):
        sel = MagicMock()
        sel.available = True
        sel.select.return_value = None
        router = _make_router(claude_only={"install_skill"}, skill_selector=sel)
        result = router.route("do something")
        self.assertEqual(result.tier, "ollama")

    def test_selector_raising_defaults_to_ollama(self):
        sel = MagicMock()
        sel.available = True
        sel.select.side_effect = RuntimeError("model error")
        router = _make_router(claude_only={"install_skill"}, skill_selector=sel)
        result = router.route("do something")
        self.assertEqual(result.tier, "ollama")

    def test_escalate_pattern_skips_skill_selector(self):
        sel = MagicMock()
        sel.available = True
        router = _make_router(claude_only={"install_skill"}, skill_selector=sel)
        router.route("remember to buy milk")
        sel.select.assert_not_called()


class TestMusicTransportPatterns(unittest.TestCase):
    def setUp(self):
        self.router = _make_router()

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
        text = PATTERNS_FILE.read_text(encoding="utf-8")
        self.assertNotIn("soundcloud_play", text)

    def test_stop_right_there_does_not_match(self):
        r = self.router.route("stop right there partner")
        self.assertNotEqual(r.tier, "direct")


if __name__ == "__main__":
    unittest.main()
