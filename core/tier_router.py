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
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
        except yaml.YAMLError as exc:
            logger.error("TierRouter: malformed YAML at %s — no patterns loaded: %s", path, exc)
            return
        for entry in data.get("dispatch", []):
            try:
                entry["_re"] = re.compile(entry["pattern"], re.IGNORECASE)
            except re.error as exc:
                logger.error("TierRouter: invalid dispatch regex %r — skipping: %s", entry.get("pattern"), exc)
                continue
            self._dispatch.append(entry)
        for pattern in data.get("escalate", []):
            try:
                self._escalate.append(re.compile(pattern, re.IGNORECASE))
            except re.error as exc:
                logger.error("TierRouter: invalid escalate regex %r — skipping: %s", pattern, exc)
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

        # 2. Escalate patterns — route to Claude immediately, skip Ollama latency
        for pattern in self._escalate:
            if pattern.search(text):
                logger.debug("TierRouter: escalate pattern matched → claude")
                return RouteResult(tier="claude")

        # 3. Skill prediction — if SkillSelector predicts a Claude-only skill, escalate
        if self._skill_selector and self._skill_selector.available:
            try:
                predicted = self._skill_selector.select(text)
            except Exception:
                logger.warning("TierRouter: skill_selector.select() raised — skipping prediction")
                predicted = None
            if predicted and predicted & self._claude_only:
                logger.debug(
                    "TierRouter: predicted claude-only skill(s) %s → claude", predicted
                )
                return RouteResult(tier="claude")

        # 4. Default — Ollama handles it
        logger.debug("TierRouter: no match → ollama")
        return RouteResult(tier="ollama")
