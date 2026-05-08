"""Scoring utilities for evaluating how well an RCA output recovers the
golden entities of a sample.

The module exposes two helpers:

``normalized_levenshtein``
    Levenshtein edit distance between two strings normalised by the longest
    length, so the result lives in ``[0, 1]`` (``0`` = identical).

``score_golden_entities``
    For each golden entity, scan substrings of ``rca_output`` whose length is
    around ``len(entity)`` (configurable margin) and consider the entity matched
    when ``1 - normalized_levenshtein(entity, best_substring) >= threshold``.
"""

from __future__ import annotations

import logging
from typing import TypedDict

from rapidfuzz.distance import Levenshtein

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_MARGIN = 2
DEFAULT_MATCH_THRESHOLD = 0.95


class GoldenEntityScore(TypedDict):
    """Result payload returned by ``score_golden_entities``."""

    score: int
    matched: list[str]
    missed: list[str]


def normalized_levenshtein(a: str, b: str) -> float:
    """Compute the Levenshtein distance normalised by the longest input length.

    Args:
        a: First string.
        b: Second string.

    Returns:
        Distance in ``[0.0, 1.0]``. ``0.0`` means the strings are identical
        (including the case where both are empty).
    """
    longest = max(len(a), len(b))
    if longest == 0:
        return 0.0
    return Levenshtein.distance(a, b) / longest


def _best_substring_distance(
    text: str,
    entity: str,
    margin: int,
) -> tuple[float, str]:
    """Find the closest substring of ``text`` to ``entity`` using normalised Levenshtein.

    The search slides windows whose length is ``len(entity) +/- margin`` over
    ``text`` and keeps track of the lowest distance found.

    Args:
        text: Haystack to search.
        entity: Needle whose best substring match is wanted.
        margin: Plus/minus characters around the entity length used to size
            the sliding window.

    Returns:
        Tuple ``(best_distance, best_substring)``. When ``text`` is shorter
        than the smallest window allowed, the function falls back to comparing
        against ``text`` as a whole.
    """
    if not entity:
        return 0.0, ""
    if not text:
        return 1.0, ""

    entity_len = len(entity)
    text_len = len(text)
    min_window = max(1, entity_len - margin)
    max_window = entity_len + margin

    if text_len < min_window:
        return normalized_levenshtein(entity, text), text

    best_distance = 1.0
    best_match = ""
    for window_size in range(min_window, max_window + 1):
        if window_size > text_len:
            break
        for start in range(text_len - window_size + 1):
            substring = text[start : start + window_size]
            distance = normalized_levenshtein(entity, substring)
            if distance < best_distance:
                best_distance = distance
                best_match = substring
                if best_distance == 0.0:
                    return best_distance, best_match

    return best_distance, best_match


def score_golden_entities(
    rca_output: str,
    golden_entities: list[str],
    threshold: float = DEFAULT_MATCH_THRESHOLD,
    window_margin: int = DEFAULT_WINDOW_MARGIN,
) -> GoldenEntityScore:
    """Score how many golden entities are present in an RCA output.

    For every entity the function looks for the best substring of
    ``rca_output`` using a sliding window of size ``len(entity) +/- window_margin``.
    An entity counts as matched when the similarity
    ``1 - normalized_levenshtein(entity, best_substring)`` is greater than or
    equal to ``threshold``.

    Args:
        rca_output: Text produced by the agent.
        golden_entities: Reference key strings expected to appear in the output.
        threshold: Similarity threshold in ``[0.0, 1.0]``. Defaults to ``0.95``.
        window_margin: Plus/minus characters around the entity length used to
            scan substring windows. Defaults to ``2``.

    Returns:
        A dict with keys ``score`` (number of matched entities), ``matched``
        (entities considered present) and ``missed`` (entities not found).

    Raises:
        ValueError: If ``threshold`` is outside ``[0.0, 1.0]`` or
            ``window_margin`` is negative.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be within [0.0, 1.0].")
    if window_margin < 0:
        raise ValueError("window_margin must be non-negative.")

    matched: list[str] = []
    missed: list[str] = []

    for entity in golden_entities:
        if not entity:
            missed.append(entity)
            continue

        distance, best_match = _best_substring_distance(
            text=rca_output,
            entity=entity,
            margin=window_margin,
        )
        similarity = 1.0 - distance
        if similarity >= threshold:
            matched.append(entity)
            logger.debug(
                "Entity matched: entity=%r best_match=%r similarity=%.4f",
                entity,
                best_match,
                similarity,
            )
        else:
            missed.append(entity)
            logger.debug(
                "Entity missed: entity=%r best_match=%r similarity=%.4f",
                entity,
                best_match,
                similarity,
            )

    return {
        "score": len(matched),
        "matched": matched,
        "missed": missed,
    }
