"""
TierRouter - Fast pre-LLM routing for MiniClaw's tiered intelligence system.

Classifies each STT transcript as direct | ollama | claude in <5ms before
any LLM is invoked. Checked in order:

  1. Dispatch patterns  → direct skill call or session action (no LLM)
  2. Escalate patterns  → Claude immediately (skip Ollama)
  3. Skill prediction   → claude_only set → Claude, else Ollama
  4. Default            → Ollama
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

logger = logging.getLogger(__name__)

Tier = Literal["direct", "ollama", "claude"]


@dataclass
class RouteResult:
    tier: Tier
    skill: str | None = None    # set for tier=="direct" skill dispatches
    args: dict = field(default_factory=dict)
    action: str | None = None   # set for tier=="direct" session actions


class TierRouter:
    """
    Routes a transcript to the appropriate processing tier without invoking any LLM.

    Patterns are loaded from a YAML file at startup. A missing file logs a
    warning and falls back to routing everything to Ollama.
    """

    def __init__(
        self,
        patterns_path: Path,
        skill_selector=None,
        claude_only_skills: set[str] | None = None,
    ):
        self._dispatch: list[dict] = []
        self._escalate: list[re.Pattern] = []
        self._skill_selector = skill_selector
        self._claude_only: set[str] = claude_only_skills or {"install_skill"}
        self._load_patterns(patterns_path)

    def _load_patterns(self, path: Path) -> None:
        if not path.exists():
            logger.warning("TierRouter: patterns file not found at %s — no patterns loaded", path)
            return
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        for entry in data.get("dispatch", []):
            entry["_re"] = re.compile(entry["pattern"], re.IGNORECASE)
            self._dispatch.append(entry)
        for pattern in data.get("escalate", []):
            self._escalate.append(re.compile(pattern, re.IGNORECASE))
        logger.info(
            "TierRouter: loaded %d dispatch, %d escalate patterns",
            len(self._dispatch),
            len(self._escalate),
        )

    def route(self, transcript: str) -> RouteResult:
        """Classify a transcript into direct | ollama | claude."""
        text = transcript.strip()

        # 1. Dispatch patterns
        for entry in self._dispatch:
            if entry["_re"].search(text):
                if "action" in entry:
                    return RouteResult(tier="direct", action=entry["action"])
                return RouteResult(
                    tier="direct",
                    skill=entry.get("skill"),
                    args=dict(entry.get("args", {})),
                )

        # 2. Escalate patterns — checked in Task 3
        # 3. Skill prediction — checked in Task 3

        return RouteResult(tier="ollama")
