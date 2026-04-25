"""PromptBuilder injects standing self-update guidance only when an opted-in skill is loaded."""

import unittest

from core.prompt_builder import PromptBuilder


class _StubSkill:
    def __init__(self, name, frontmatter):
        self.name = name
        self.frontmatter = frontmatter
        self.description = ""
        self.instructions = ""


def _opted_in(name):
    return _StubSkill(name, {"metadata": {"miniclaw": {"self_update": {"allow_body": True}}}})


def _opted_out(name):
    return _StubSkill(name, {"metadata": {"miniclaw": {"self_update": {"allow_body": False}}}})


class TestSelfUpdateGuidance(unittest.TestCase):
    def test_no_opted_in_skill_omits_guidance(self):
        builder = PromptBuilder()
        prompt = builder.add_self_update_guidance("base prompt", skills={"a": _opted_out("a")})
        self.assertEqual(prompt, "base prompt")

    def test_opted_in_skill_appends_guidance(self):
        builder = PromptBuilder()
        prompt = builder.add_self_update_guidance(
            "base prompt",
            skills={"a": _opted_in("a"), "b": _opted_out("b")},
        )
        self.assertIn("update_skill_hints", prompt)
        self.assertIn("NOVEL SUCCESSFUL PHRASING", prompt)
        self.assertTrue(prompt.startswith("base prompt"))


if __name__ == "__main__":
    unittest.main()
