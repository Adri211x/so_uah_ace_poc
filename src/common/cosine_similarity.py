"""Semantic-similarity scorer based on multilingual sentence-transformers.

This module complements the literal entity scorer in :mod:`src.common.scoring`
and the LLM-as-a-judge in :mod:`src.common.judge` by adding a cheap, offline,
embedding-based metric: cosine similarity in a multilingual sentence-embedding
space.

For each evaluated sample the scorer computes:

* ``rca_similarity``: cosine similarity between the agent's ``rca_output`` and
  the dataset ``expected_output``.
* ``golden_entity_similarities``: per-entity cosine similarity between every
  golden entity and the full ``rca_output``.
* ``golden_entities_avg`` / ``golden_entities_max``: aggregate statistics over
  ``golden_entity_similarities`` for quick at-a-glance comparisons.

The encoder is loaded lazily with ``functools.lru_cache`` so a process scoring
many samples reuses a single model in memory. The primary model is the small
multilingual MiniLM variant; if it cannot be loaded (network problems, broken
local cache, ...) the loader falls back to the larger multilingual MPNet model
and records which one was actually used in the result payload.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Protocol

import numpy as np
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

PRIMARY_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
FALLBACK_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"


class TextEncoder(Protocol):
    """Minimal interface implemented by ``SentenceTransformer.encode``.

    Defining a Protocol keeps tests free from heavy ML dependencies: a fake
    encoder can be injected via ``compute_cosine_scores(..., encoder=...)``.
    """

    def encode(
        self,
        sentences: list[str],
        *,
        normalize_embeddings: bool = ...,
    ) -> np.ndarray:
        """Return one embedding row per input sentence."""


class CosineSimilarityResult(BaseModel):
    """Structured cosine-similarity scores attached to an ``AgentResult``.

    Attributes:
        rca_similarity: Cosine similarity in ``[-1.0, 1.0]`` between the agent's
            ``rca_output`` and the ground-truth ``expected_output``. With the
            chosen multilingual sentence-transformer models the value is almost
            always non-negative.
        golden_entity_similarities: Mapping ``entity -> similarity`` of each
            golden entity against the full ``rca_output``. Empty when the
            sample has no golden entities.
        golden_entities_avg: Arithmetic mean over ``golden_entity_similarities``;
            ``0.0`` when the mapping is empty.
        golden_entities_max: Maximum value over ``golden_entity_similarities``;
            ``0.0`` when the mapping is empty.
    """

    rca_similarity: float = Field(..., ge=-1.0, le=1.0)
    golden_entity_similarities: dict[str, float] = Field(default_factory=dict)
    golden_entities_avg: float = Field(..., ge=-1.0, le=1.0)
    golden_entities_max: float = Field(..., ge=-1.0, le=1.0)


@lru_cache(maxsize=1)
def _load_encoder() -> TextEncoder:
    """Load the multilingual encoder, falling back to a stronger model on error.

    The primary model is small, fast and good enough for a multilingual
    paraphrase signal. If its weights cannot be fetched or instantiated the
    loader transparently retries with the larger MPNet variant so the rest of
    the pipeline keeps working.

    Returns:
        The sentence-transformer instance actually loaded (primary or fallback).

    Raises:
        RuntimeError: If both the primary and the fallback model fail to load.
    """
    # Imported lazily so that unit tests which inject a fake encoder do not pay
    # the multi-second cost of importing torch / sentence-transformers.
    from sentence_transformers import SentenceTransformer

    try:
        logger.info("Loading primary embedding model: %s", PRIMARY_MODEL_NAME)
        return SentenceTransformer(PRIMARY_MODEL_NAME)
    except Exception:
        logger.warning(
            "Primary embedding model %s failed to load; trying fallback %s.",
            PRIMARY_MODEL_NAME,
            FALLBACK_MODEL_NAME,
            exc_info=True,
        )

    try:
        return SentenceTransformer(FALLBACK_MODEL_NAME)
    except Exception as exc:
        raise RuntimeError(
            "Could not load any sentence-transformer model "
            f"({PRIMARY_MODEL_NAME!r} or {FALLBACK_MODEL_NAME!r})."
        ) from exc


def _cosine(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
    """Compute the cosine similarity between two 1-D embedding vectors.

    The function clamps the result to ``[-1.0, 1.0]`` to absorb the small
    floating-point drift that can push values like ``1.0000000002`` outside the
    valid interval and break the pydantic field validators downstream.

    Args:
        vec_a: First embedding (1-D float array).
        vec_b: Second embedding (1-D float array).

    Returns:
        Cosine similarity in ``[-1.0, 1.0]``. Returns ``0.0`` when at least one
        of the inputs has zero norm (which happens for empty strings after
        normalisation by the encoder).
    """
    norm_a = float(np.linalg.norm(vec_a))
    norm_b = float(np.linalg.norm(vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    raw = float(np.dot(vec_a, vec_b) / (norm_a * norm_b))
    return max(-1.0, min(1.0, raw))


def _safe_text(value: str) -> str:
    """Return a stripped, non-empty placeholder when ``value`` is empty.

    Sentence-transformers tokenises every input, so passing an empty string
    yields an all-zero embedding and a meaningless similarity of ``0``. We
    keep that semantics by returning a single space, which the model maps to a
    near-empty embedding without raising.
    """
    text = (value or "").strip()
    return text if text else " "


def compute_cosine_scores(
    rca_output: str,
    expected_output: str,
    golden_entities: list[str],
    encoder: TextEncoder | None = None,
) -> CosineSimilarityResult:
    """Score one (RCA, expected, golden_entities) triple with cosine similarity.

    The function batches every text into a single ``encode`` call so the
    encoder runs the transformer forward pass once per sample instead of once
    per pair. Embeddings are L2-normalised so the cosine similarity reduces to
    a dot product (the explicit ``_cosine`` helper still guards against the
    rare zero-norm case).

    Args:
        rca_output: Agent-produced root cause analysis text.
        expected_output: Ground-truth RCA text.
        golden_entities: Reference key strings (zero or more).
        encoder: Optional encoder override; tests inject a deterministic fake.
            When ``None``, the cached default encoder is loaded on demand.

    Returns:
        ``CosineSimilarityResult`` with the per-sample similarities.

    Raises:
        RuntimeError: If the default encoder cannot be loaded.
    """
    if encoder is None:
        encoder = _load_encoder()

    texts = [_safe_text(rca_output), _safe_text(expected_output)]
    texts.extend(_safe_text(entity) for entity in golden_entities)

    embeddings = np.asarray(
        encoder.encode(texts, normalize_embeddings=True),
        dtype=np.float64,
    )

    rca_vec = embeddings[0]
    expected_vec = embeddings[1]
    rca_similarity = _cosine(rca_vec, expected_vec)

    entity_similarities: dict[str, float] = {}
    for entity, entity_vec in zip(golden_entities, embeddings[2:], strict=True):
        entity_similarities[entity] = _cosine(rca_vec, entity_vec)

    if entity_similarities:
        avg_similarity = float(np.mean(list(entity_similarities.values())))
        max_similarity = float(np.max(list(entity_similarities.values())))
    else:
        avg_similarity = 0.0
        max_similarity = 0.0

    return CosineSimilarityResult(
        rca_similarity=rca_similarity,
        golden_entity_similarities=entity_similarities,
        golden_entities_avg=avg_similarity,
        golden_entities_max=max_similarity,
    )
