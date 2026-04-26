"""Unit tests for ``src.common.schemas`` (agent input / runner output models)."""

import pytest
from pydantic import ValidationError

from src.common.schemas import AgentInput, AgentResult


def test_agent_input_accepts_alert_and_optional_scenario() -> None:
    """``AgentInput`` should accept a non-empty alert and optional scenario label."""
    payload = AgentInput(alert_text="ALERT: Example", scenario="kubernetes-crashloop")

    assert payload.alert_text == "ALERT: Example"
    assert payload.scenario == "kubernetes-crashloop"


def test_agent_input_requires_non_empty_alert_text() -> None:
    """Empty ``alert_text`` must fail validation (min_length=1)."""
    with pytest.raises(ValidationError):
        AgentInput(alert_text="")


def test_agent_result_requires_expected_fields() -> None:
    """``AgentResult`` should round-trip core RCA evaluation fields."""
    result = AgentResult(
        rca_output="Root cause: invalid container command.",
        sample_id="sample-1",
        expected_output="Root cause: invalid container command.",
        golden_entities=["invalid python3 command", "unterminated quoted string", "nginx image"],
    )

    assert result.sample_id == "sample-1"
    assert len(result.golden_entities) == 3
