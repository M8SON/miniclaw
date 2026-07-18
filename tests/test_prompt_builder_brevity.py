"""Guard: the base spoken-answer prompt asks for short-by-default answers
with depth only on explicit request."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.prompt_builder import PromptBuilder


def test_base_prompt_requests_brevity_with_depth_on_request():
    tmpl = PromptBuilder.BASE_PROMPT_TEMPLATE.lower()
    # short by default
    assert "one" in tmpl and "sentence" in tmpl
    assert "short" in tmpl
    # depth on explicit request
    assert "elaborate" in tmpl or "explain" in tmpl
