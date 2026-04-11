"""
Skill Selector - Semantic skill relevance ranking.

Embeds skill descriptions using chromadb's built-in embedding function
(onnxruntime-based, no extra packages needed) and ranks them by cosine
similarity to the incoming user message.

PromptBuilder uses this to expand only relevant skills in full — all
others collapse to compact one-liners.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)


class SkillSelector:
    """
    Ranks skills by semantic relevance to a user message.

    Uses chromadb's DefaultEmbeddingFunction (all-MiniLM-L6-v2 via
    onnxruntime). Falls back gracefully if unavailable — callers treat
    an empty result set as "use all skills".
    """

    def __init__(self, top_k: int = 2):
        self.top_k = top_k
        self._ef = None
        self._skill_names: list[str] = []
        self._embeddings: np.ndarray | None = None
        self._load_model()

    def _load_model(self) -> None:
        try:
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
            self._ef = DefaultEmbeddingFunction()
            logger.info("SkillSelector: embedding function loaded")
        except (ImportError, ModuleNotFoundError) as exc:
            logger.warning(
                "SkillSelector: chromadb embedding function not available, falling back to all-skills-full: %s", exc
            )
        except Exception as exc:
            logger.error(
                "SkillSelector: unexpected error loading embedding function, falling back to all-skills-full: %s", exc
            )

    @property
    def available(self) -> bool:
        """True only when the model is loaded and skills have been indexed."""
        return self._ef is not None and self._embeddings is not None

    def index(self, skills: dict) -> None:
        """
        Embed all skill descriptions. Call after skill load or reload.

        skills: dict[str, Skill] — the loaded skills dict from SkillLoader.
        """
        if self._ef is None:
            return
        self._skill_names = list(skills.keys())
        if not self._skill_names:
            self._embeddings = None
            return
        texts = [f"{s.name}: {s.description}" for s in skills.values()]
        raw = self._ef(texts)
        self._embeddings = np.array(raw, dtype=np.float32)
        logger.debug("SkillSelector: indexed %d skills", len(self._skill_names))

    def select(self, user_message: str) -> set[str]:
        """
        Return up to top_k skill names most relevant to user_message.

        Returns empty set when unavailable — PromptBuilder treats this
        as "expand all skills" (existing behaviour).
        """
        if not self.available:
            return set()

        if not self._skill_names:
            return set()

        query_raw = self._ef([user_message])
        query_emb = np.array(query_raw[0], dtype=np.float32)
        query_norm = np.linalg.norm(query_emb)

        if query_norm < 1e-8:
            logger.warning("SkillSelector: query vector is near-zero, returning first %d skills", self.top_k)
            return set(self._skill_names[: self.top_k])

        norms = np.linalg.norm(self._embeddings, axis=1)
        similarities = self._embeddings @ query_emb / (norms * query_norm + 1e-8)

        top_indices = np.argsort(-similarities, kind="stable")[: self.top_k]
        return {self._skill_names[i] for i in top_indices}
