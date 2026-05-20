"""Unit tests for ``src.common.scoring`` (golden entity matching)."""

from __future__ import annotations

import pytest

from src.common.scoring import normalized_levenshtein, score_golden_entities


def test_normalized_levenshtein_identical_strings_returns_zero() -> None:
    assert normalized_levenshtein("hello", "hello") == 0.0


def test_normalized_levenshtein_both_empty_returns_zero() -> None:
    assert normalized_levenshtein("", "") == 0.0


def test_normalized_levenshtein_one_empty_returns_one() -> None:
    assert normalized_levenshtein("hello", "") == 1.0


def test_normalized_levenshtein_single_substitution() -> None:
    assert normalized_levenshtein("kitten", "sitten") == pytest.approx(1 / 6)


def test_score_three_out_of_three_when_entities_appear_literally() -> None:
    rca = (
        "nginx-deploy crashed with exit code 1 due to an "
        "unterminated quoted string in the entrypoint."
    )
    entities = ["nginx-deploy", "unterminated quoted string", "exit code 1"]
    result = score_golden_entities(rca, entities)
    assert result["score"] == 3
    assert len(result["matched"]) == 3


def test_score_two_out_of_three_when_one_entity_missing() -> None:
    rca = "redis-cache went OOMKilled after exceeding the configured limit."
    entities = ["redis-cache", "OOMKilled", "memory limit"]
    result = score_golden_entities(rca, entities)
    assert result["score"] == 2
    assert "memory limit" not in result["matched"]


def test_score_matches_long_entity_with_single_char_typo() -> None:
    entity = "unterminated quoted string"
    rca = "failure caused by unterminated quoted strinG in the shell entrypoint"
    result = score_golden_entities(rca, [entity])
    assert result["score"] == 1


def test_score_does_not_match_short_entity_with_typo() -> None:
    result = score_golden_entities("OOMKilld pod restart", ["OOMKilled"])
    assert result["score"] == 0


def test_score_with_empty_golden_entities_returns_zero() -> None:
    result = score_golden_entities("some output", [])
    assert result["score"] == 0
    assert result["matched"] == []
    assert result["missed"] == []


def test_score_skips_empty_entities_as_missed() -> None:
    result = score_golden_entities("output with valid entity present", ["", "valid", ""])
    assert result["score"] == 1
    assert result["missed"] == ["", ""]


def test_score_with_empty_rca_output_marks_all_as_missed() -> None:
    result = score_golden_entities("", ["entity-a", "entity-b"])
    assert result["score"] == 0
    assert result["missed"] == ["entity-a", "entity-b"]


def test_score_threshold_zero_matches_anything_non_empty() -> None:
    result = score_golden_entities("xyz", ["abc"], threshold=0.0)
    assert result["score"] == 1


def test_score_invalid_threshold_raises() -> None:
    with pytest.raises(ValueError, match="threshold"):
        score_golden_entities("text", ["entity"], threshold=1.5)


def test_score_invalid_window_margin_raises() -> None:
    with pytest.raises(ValueError, match="window_margin"):
        score_golden_entities("text", ["entity"], window_margin=-1)


def test_score_handles_text_shorter_than_min_window() -> None:
    result = score_golden_entities("OOM", ["OOMKilled"])
    assert result["score"] == 0
