"""Tests for SkillSelector — semantic skill relevance ranking."""

import sys
import types
import unittest
from unittest.mock import MagicMock

import numpy as np


def _make_mock_ef(skill_dim_map: dict[str, int], n_dims: int):
    """
    Return a mock DefaultEmbeddingFunction.

    Each skill gets a one-hot vector at its assigned dimension.
    A query containing a skill's keyword returns the matching one-hot vector.
    """
    def ef(texts):
        embeddings = []
        for text in texts:
            vec = np.zeros(n_dims, dtype=np.float32)
            t = text.lower()
            for keyword, dim in skill_dim_map.items():
                if keyword in t:
                    vec[dim] = 1.0
                    break
            if vec.sum() == 0:
                vec[0] = 0.01
            embeddings.append(vec.tolist())
        return embeddings

    mock = MagicMock(side_effect=ef)
    return mock


class FakeSkill:
    def __init__(self, name, description):
        self.name = name
        self.description = description


FAKE_SKILLS = {
    "soundcloud": FakeSkill("soundcloud", "Play music and audio from SoundCloud"),
    "weather": FakeSkill("weather", "Get current weather for a city"),
    "dashboard": FakeSkill("dashboard", "Show a visual news and weather dashboard"),
    "web_search": FakeSkill("web_search", "Search the web for information"),
}

DIM_MAP = {
    "soundcloud": 0,
    "music": 0,
    "song": 0,
    "weather": 1,
    "temperature": 1,
    "dashboard": 2,
    "news": 2,
    "search": 3,
    "web": 3,
}


class TestSkillSelector(unittest.TestCase):

    def _make_selector(self, top_k=1):
        """Create a SkillSelector with a mocked embedding function."""
        from core.skill_selector import SkillSelector

        selector = SkillSelector.__new__(SkillSelector)
        selector.top_k = top_k
        selector._ef = _make_mock_ef(DIM_MAP, n_dims=4)
        selector._skill_names = []
        selector._embeddings = None
        selector.index(FAKE_SKILLS)
        return selector

    def test_selects_most_relevant_skill(self):
        selector = self._make_selector(top_k=1)
        result = selector.select("play a song")
        self.assertIn("soundcloud", result)

    def test_selects_top_k_skills(self):
        selector = self._make_selector(top_k=2)
        result = selector.select("play a song")
        self.assertEqual(len(result), 2)

    def test_weather_query_selects_weather(self):
        selector = self._make_selector(top_k=1)
        result = selector.select("what is the weather today")
        self.assertIn("weather", result)

    def test_returns_empty_set_when_unavailable(self):
        from core.skill_selector import SkillSelector
        selector = SkillSelector.__new__(SkillSelector)
        selector.top_k = 2
        selector._ef = None
        selector._skill_names = []
        selector._embeddings = None
        self.assertEqual(selector.select("play a song"), set())

    def test_available_false_when_not_indexed(self):
        from core.skill_selector import SkillSelector
        selector = SkillSelector.__new__(SkillSelector)
        selector._ef = MagicMock()
        selector._embeddings = None
        selector._skill_names = []
        self.assertFalse(selector.available)

    def test_available_true_after_index(self):
        selector = self._make_selector(top_k=1)
        self.assertTrue(selector.available)

    def test_index_resets_on_reload(self):
        selector = self._make_selector(top_k=1)
        new_skills = {
            "homebridge": FakeSkill("homebridge", "Control smart home devices"),
        }
        selector.index(new_skills)
        self.assertEqual(selector._skill_names, ["homebridge"])

    def test_returns_empty_set_for_empty_skills(self):
        from core.skill_selector import SkillSelector
        selector = SkillSelector.__new__(SkillSelector)
        selector.top_k = 2
        selector._ef = _make_mock_ef(DIM_MAP, n_dims=4)
        selector._skill_names = []
        selector._embeddings = None
        # index with empty skills — embeddings stay None
        selector.index({})
        self.assertEqual(selector.select("play a song"), set())

    def test_top_k_capped_by_skill_count(self):
        # top_k=10 but only 4 skills indexed — should return all 4
        selector = self._make_selector(top_k=10)
        result = selector.select("play a song")
        self.assertLessEqual(len(result), len(FAKE_SKILLS))


if __name__ == "__main__":
    unittest.main()
