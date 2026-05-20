"""Unit tests for ``src.common.schemas`` (agent input / runner output models)."""

import pytest
from pydantic import ValidationError

from src.common.judge import JudgeVerdict
from src.common.schemas import AgentInput, AgentResult


def test_agent_input_accepts_alert_and_optional_scenario() -> None:
    payload = AgentInput(alert_text="ALERT: Example", scenario="kubernetes-crashloop")
    assert payload.alert_text == "ALERT: Example"
    assert payload.scenario == "kubernetes-crashloop"


def test_agent_input_requires_non_empty_alert_text() -> None:
    with pytest.raises(ValidationError):
        AgentInput(alert_text="")


def test_agent_result_requires_expected_fields() -> None:
    result = AgentResult(
        rca_output="Root cause: invalid container command.",
        sample_id="sample-1",
        expected_output="Root cause: invalid container command.",
        golden_entities=["invalid python3 command", "unterminated quoted string", "nginx image"],
    )
    assert result.sample_id == "sample-1"
    assert result.judge_verdict is None


def test_agent_result_round_trips_judge_verdict() -> None:
    verdict = JudgeVerdict(
        root_cause_match=0.9,
        evidence_quality=0.8,
        completeness=0.7,
        overall=0.83,
        reasoning="Most of the failure is covered.",
        judge_model="gpt-4o-mini",
    )
    result = AgentResult(
        rca_output="config_error: targetPort mismatch",
        sample_id="sample-1",
        expected_output="config_error: targetPort mismatch",
        golden_entities=["config_error"],
        score=1,
        matched_entities=["config_error"],
        judge_verdict=verdict,
    )
    assert result.judge_verdict is not None
    assert result.judge_verdict.overall == pytest.approx(0.83)
