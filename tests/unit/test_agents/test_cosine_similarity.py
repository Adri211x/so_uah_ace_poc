"""Unit tests for ``src.common.cosine_similarity``.

Tests rely on a deterministic ``FakeEncoder`` so they do not require the real
sentence-transformers weights. The encoder maps a vocabulary of tokens into a
small orthonormal basis, which makes cosine similarity equal to the share of
shared tokens between two strings.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.common.cosine_similarity import (
    CosineSimilarityResult,
    _cosine,
    compute_cosine_scores,
)


class FakeEncoder:
    """Deterministic encoder backed by a token vocabulary.

    Every token gets a fresh unit vector (one-hot) in a fixed-dimension space.
    A sentence is encoded as the L2-normalised sum of its token vectors. With
    this construction the cosine between two sentences is the cosine of the
    angle between their bag-of-words vectors, which is intuitive enough for
    assertions in unit tests.
    """

    def __init__(self, dimension: int = 32) -> None:
        self._dimension = dimension
        self._vocab: dict[str, int] = {}

    def _vector_for_token(self, token: str) -> np.ndarray:
        if token not in self._vocab:
            self._vocab[token] = len(self._vocab) % self._dimension
        vec = np.zeros(self._dimension, dtype=np.float64)
        vec[self._vocab[token]] = 1.0
        return vec

    def _embed(self, text: str) -> np.ndarray:
        tokens = [tok for tok in text.lower().split() if tok]
        if not tokens:
            return np.zeros(self._dimension, dtype=np.float64)
        embedding = np.zeros(self._dimension, dtype=np.float64)
        for token in tokens:
            embedding += self._vector_for_token(token)
        return embedding

    def encode(
        self,
        sentences: list[str],
        *,
        normalize_embeddings: bool = False,
    ) -> np.ndarray:
        rows = [self._embed(sentence) for sentence in sentences]
        matrix = np.vstack(rows)
        if normalize_embeddings:
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            norms[norms == 0.0] = 1.0
            matrix = matrix / norms
        return matrix


def test_cosine_helper_returns_one_for_identical_vectors() -> None:
    """``_cosine`` of a vector with itself must be exactly 1.0."""
    vec = np.array([1.0, 2.0, 3.0])

    assert _cosine(vec, vec) == pytest.approx(1.0)


def test_cosine_helper_handles_zero_vector() -> None:
    """Zero-norm inputs short-circuit to 0 instead of dividing by zero."""
    zero = np.zeros(3)
    other = np.array([1.0, 0.0, 0.0])

    assert _cosine(zero, other) == 0.0
    assert _cosine(other, zero) == 0.0


def test_compute_cosine_scores_matches_identical_text() -> None:
    """Identical RCA and expected output should yield similarity ~1.0."""
    encoder = FakeEncoder()
    text = "container nginx crashed memory limit"

    result = compute_cosine_scores(
        rca_output=text,
        expected_output=text,
        golden_entities=["container", "memory limit"],
        encoder=encoder,
    )

    assert isinstance(result, CosineSimilarityResult)
    assert result.rca_similarity == pytest.approx(1.0)
    assert set(result.golden_entity_similarities) == {"container", "memory limit"}
    assert result.golden_entities_avg > 0.0
    assert result.golden_entities_max > 0.0


def test_compute_cosine_scores_lower_when_text_differs() -> None:
    """Unrelated RCA / expected texts produce a lower similarity."""
    encoder = FakeEncoder()

    related = compute_cosine_scores(
        rca_output="broker queue full max capacity",
        expected_output="broker queue full max capacity",
        golden_entities=[],
        encoder=encoder,
    )
    unrelated = compute_cosine_scores(
        rca_output="completely unrelated alert about disk",
        expected_output="broker queue full max capacity",
        golden_entities=[],
        encoder=encoder,
    )

    assert related.rca_similarity > unrelated.rca_similarity


def test_compute_cosine_scores_empty_entities_returns_zero_aggregates() -> None:
    """Empty golden entities yield empty mapping and zero aggregates."""
    encoder = FakeEncoder()

    result = compute_cosine_scores(
        rca_output="anything",
        expected_output="anything",
        golden_entities=[],
        encoder=encoder,
    )

    assert result.golden_entity_similarities == {}
    assert result.golden_entities_avg == 0.0
    assert result.golden_entities_max == 0.0


def test_compute_cosine_scores_per_entity_matches_text_overlap() -> None:
    """An entity that appears in the RCA scores higher than one that does not."""
    encoder = FakeEncoder()

    result = compute_cosine_scores(
        rca_output="the broker queue is full at maximum capacity",
        expected_output="ground truth",
        golden_entities=["broker queue", "completely unrelated kernel panic"],
        encoder=encoder,
    )

    assert (
        result.golden_entity_similarities["broker queue"]
        > result.golden_entity_similarities["completely unrelated kernel panic"]
    )
    assert result.golden_entities_max == max(result.golden_entity_similarities.values())
    assert result.golden_entities_avg == pytest.approx(
        sum(result.golden_entity_similarities.values()) / 2
    )


def test_compute_cosine_scores_handles_empty_text() -> None:
    """Empty inputs must not raise and stay within ``[-1, 1]``."""
    encoder = FakeEncoder()

    result = compute_cosine_scores(
        rca_output="",
        expected_output="some expected text",
        golden_entities=["entity"],
        encoder=encoder,
    )

    assert -1.0 <= result.rca_similarity <= 1.0
    assert -1.0 <= result.golden_entity_similarities["entity"] <= 1.0
