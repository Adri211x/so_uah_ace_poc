"""Unit tests for ``src.common.scoring`` (entity-recovery scoring helpers)."""

from __future__ import annotations

import pytest

from src.common.scoring import normalized_levenshtein, score_golden_entities


def test_normalized_levenshtein_identical_strings_returns_zero() -> None:
    """Identical inputs should yield a normalised distance of ``0.0``."""
    assert normalized_levenshtein("payload", "payload") == 0.0


def test_normalized_levenshtein_both_empty_returns_zero() -> None:
    """The function must not divide by zero when both strings are empty."""
    assert normalized_levenshtein("", "") == 0.0


def test_normalized_levenshtein_one_empty_returns_one() -> None:
    """Comparing against an empty string yields the maximum normalised distance."""
    assert normalized_levenshtein("abc", "") == 1.0
    assert normalized_levenshtein("", "abc") == 1.0


def test_normalized_levenshtein_single_substitution() -> None:
    """One substitution over a 3-char string is ``1/3`` of the maximum length."""
    assert normalized_levenshtein("abc", "abd") == pytest.approx(1 / 3)


def test_score_three_out_of_three_when_entities_appear_literally() -> None:
    """All literal occurrences must yield ``score == 3`` and empty ``missed``."""
    rca_output = (
        "The pod nginx-deploy crashed because of an unterminated quoted string "
        "raised by an invalid python3 command in the entrypoint."
    )
    golden_entities = [
        "invalid python3 command",
        "unterminated quoted string",
        "nginx-deploy",
    ]

    result = score_golden_entities(rca_output, golden_entities)

    assert result["score"] == 3
    assert sorted(result["matched"]) == sorted(golden_entities)
    assert result["missed"] == []


def test_score_two_out_of_three_when_one_entity_missing() -> None:
    """A missing entity must reduce the score and appear in ``missed``."""
    rca_output = (
        "The pod nginx-deploy failed due to an unterminated quoted string in the entrypoint script."
    )
    golden_entities = [
        "invalid python3 command",
        "unterminated quoted string",
        "nginx-deploy",
    ]

    result = score_golden_entities(rca_output, golden_entities)

    assert result["score"] == 2
    assert "invalid python3 command" in result["missed"]
    assert "unterminated quoted string" in result["matched"]
    assert "nginx-deploy" in result["matched"]


def test_score_matches_long_entity_with_single_char_typo() -> None:
    """A 1-char typo within a long (>20 chars) entity must still be matched."""
    long_entity = "container-engine-runtime-failure"
    assert len(long_entity) > 20
    rca_output = "The cluster reported a container-engime-runtime-failure during the last rollout."

    result = score_golden_entities(rca_output, [long_entity])

    assert result["score"] == 1
    assert result["matched"] == [long_entity]
    assert result["missed"] == []


def test_score_does_not_match_short_entity_with_typo() -> None:
    """A 1-char typo on a short entity falls below the default 0.95 threshold."""
    short_entity = "nginx"
    rca_output = "The container running ngonx terminated unexpectedly."

    result = score_golden_entities(rca_output, [short_entity])

    assert result["score"] == 0
    assert result["matched"] == []
    assert result["missed"] == [short_entity]


def test_score_with_empty_golden_entities_returns_zero() -> None:
    """Empty entity list should produce a zero score with empty lists."""
    result = score_golden_entities("any text", [])

    assert result == {"score": 0, "matched": [], "missed": []}


def test_score_skips_empty_entities_as_missed() -> None:
    """Empty strings inside ``golden_entities`` count as missed, never matched."""
    result = score_golden_entities("payload text", ["", "payload"])

    assert result["score"] == 1
    assert result["matched"] == ["payload"]
    assert result["missed"] == [""]


def test_score_with_empty_rca_output_marks_all_as_missed() -> None:
    """An empty RCA output cannot match any non-empty entity."""
    result = score_golden_entities("", ["one", "two"])

    assert result["score"] == 0
    assert result["matched"] == []
    assert result["missed"] == ["one", "two"]


def test_score_threshold_zero_matches_anything_non_empty() -> None:
    """A zero similarity threshold should treat any non-empty entity as matched."""
    result = score_golden_entities(
        "completely unrelated content",
        ["abc", "xyz"],
        threshold=0.0,
    )

    assert result["score"] == 2


def test_score_invalid_threshold_raises() -> None:
    """``threshold`` must lie within ``[0.0, 1.0]``."""
    with pytest.raises(ValueError):
        score_golden_entities("text", ["entity"], threshold=1.5)


def test_score_invalid_window_margin_raises() -> None:
    """``window_margin`` must be non-negative."""
    with pytest.raises(ValueError):
        score_golden_entities("text", ["entity"], window_margin=-1)


def test_score_handles_text_shorter_than_min_window() -> None:
    """When ``rca_output`` is shorter than the smallest window, the comparison
    still falls back to scoring the whole text against the entity."""
    result = score_golden_entities(
        "abc",
        ["abcdefghijklmnopqrstu"],
        window_margin=2,
    )

    assert result["score"] == 0
    assert result["missed"] == ["abcdefghijklmnopqrstu"]
