"""Tests for PromptBuilder with SkillSelector wired in."""

import sys
import types
import unittest

sys.modules.setdefault("anthropic", types.SimpleNamespace(NOT_GIVEN=object()))


class FakeSkill:
    def __init__(self, name, description, instructions="detailed instructions for " + "x" * 50):
        self.name = name
        self.description = description
        self.instructions = instructions


class MockSelector:
    """Always selects 'soundcloud' as the relevant skill."""

    @property
    def available(self):
        return True

    def select(self, user_message: str) -> set[str]:
        return {"soundcloud"}


class UnavailableSelector:
    @property
    def available(self):
        return False

    def select(self, user_message: str) -> set[str]:
        return set()


SKILLS = {
    "soundcloud": FakeSkill("soundcloud", "Play music from SoundCloud"),
    "weather": FakeSkill("weather", "Get current weather"),
    "dashboard": FakeSkill("dashboard", "Show a visual dashboard"),
    "set_env_var": FakeSkill("set_env_var", "Set an environment variable"),
    "save_memory": FakeSkill("save_memory", "Save a memory note"),
    "install_skill": FakeSkill("install_skill", "Install a new skill"),
}


class TestPromptBuilderSelector(unittest.TestCase):

    def _make_builder(self, selector=None):
        from core.prompt_builder import PromptBuilder
        return PromptBuilder(memory_provider=None, skill_selector=selector)

    def test_with_selector_expands_selected_skill_in_full(self):
        builder = self._make_builder(selector=MockSelector())
        prompt = builder.build(SKILLS, {}, user_message="play a song")
        # soundcloud should appear with its full instructions block
        self.assertIn("### soundcloud", prompt)
        # detailed instructions must appear after the soundcloud header
        soundcloud_pos = prompt.index("### soundcloud")
        instructions_pos = prompt.index("detailed instructions", soundcloud_pos)
        self.assertGreater(instructions_pos, soundcloud_pos)

    def test_with_selector_collapses_other_skills_to_one_liner(self):
        builder = self._make_builder(selector=MockSelector())
        prompt = builder.build(SKILLS, {}, user_message="play a song")
        # weather is not selected — should NOT have a full ### header
        self.assertNotIn("### weather", prompt)
        # but should still appear somewhere as a one-liner
        self.assertIn("weather", prompt)

    def test_always_full_skills_always_expanded(self):
        builder = self._make_builder(selector=MockSelector())
        prompt = builder.build(SKILLS, {}, user_message="play a song")
        for always_full in ("set_env_var", "save_memory", "install_skill"):
            self.assertIn(f"### {always_full}", prompt)

    def test_without_user_message_expands_all_skills(self):
        builder = self._make_builder(selector=MockSelector())
        prompt = builder.build(SKILLS, {}, user_message=None)
        for name in SKILLS:
            self.assertIn(f"### {name}", prompt)

    def test_unavailable_selector_expands_all_skills(self):
        builder = self._make_builder(selector=UnavailableSelector())
        prompt = builder.build(SKILLS, {}, user_message="play a song")
        for name in SKILLS:
            self.assertIn(f"### {name}", prompt)

    def test_no_selector_expands_all_skills(self):
        builder = self._make_builder(selector=None)
        prompt = builder.build(SKILLS, {}, user_message="play a song")
        for name in SKILLS:
            self.assertIn(f"### {name}", prompt)


if __name__ == "__main__":
    unittest.main()
