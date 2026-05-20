"""Unit tests for ``src.common.judge`` (LLM-as-a-judge evaluator)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.common import judge as judge_module
from src.common.judge import JudgeVerdict, judge_rca


@pytest.fixture(autouse=True)
def _clear_judge_agent_cache() -> None:
    """Reset the cached judge agent so patches in one test do not leak to the next."""
    judge_module._build_judge_agent.cache_clear()
    yield
    judge_module._build_judge_agent.cache_clear()


def _make_agent_run_sync(verdict: JudgeVerdict):
    def _run_sync(_user_prompt: str) -> SimpleNamespace:
        return SimpleNamespace(output=verdict)

    return _run_sync


def test_judge_verdict_rejects_out_of_range_scores() -> None:
    with pytest.raises(ValidationError):
        JudgeVerdict(
            root_cause_match=1.5,
            evidence_quality=0.5,
            completeness=0.5,
            overall=0.5,
            reasoning="invalid",
        )


def test_judge_short_circuits_on_unknown_output() -> None:
    settings = SimpleNamespace(judge_model_name="gpt-4o-mini")
    verdict = judge_rca(
        rca_output="unknown",
        expected_output="config_error: targetPort mismatch.",
        settings=settings,  # type: ignore[arg-type]
    )
    assert verdict.overall == 0.0
    assert verdict.judge_model == "gpt-4o-mini"


def test_judge_short_circuits_on_empty_output() -> None:
    settings = SimpleNamespace(judge_model_name="gpt-4o-mini")
    verdict = judge_rca(
        rca_output="   \n  ",
        expected_output="some ground truth",
        settings=settings,  # type: ignore[arg-type]
    )
    assert verdict.overall == 0.0


def test_judge_requires_expected_output() -> None:
    settings = SimpleNamespace(judge_model_name="gpt-4o-mini")
    with pytest.raises(ValueError, match="expected_output"):
        judge_rca(rca_output="config_error", expected_output="", settings=settings)  # type: ignore[arg-type]


def test_judge_returns_perfect_match_verdict() -> None:
    settings = SimpleNamespace(judge_model_name="gpt-4o-mini")
    perfect = JudgeVerdict(
        root_cause_match=1.0,
        evidence_quality=1.0,
        completeness=1.0,
        overall=1.0,
        reasoning="Identical root cause and evidence.",
    )
    fake_agent = SimpleNamespace(run_sync=_make_agent_run_sync(perfect))
    with patch.object(judge_module, "_build_judge_agent", return_value=fake_agent):
        verdict = judge_rca(
            rca_output="config_error: port mismatch",
            expected_output="config_error: port mismatch",
            settings=settings,  # type: ignore[arg-type]
        )
    assert verdict.overall == 1.0
    assert verdict.judge_model == "gpt-4o-mini"


def test_judge_returns_partial_match_verdict() -> None:
    settings = SimpleNamespace(judge_model_name="gpt-4o-mini")
    partial = JudgeVerdict(
        root_cause_match=0.9,
        evidence_quality=0.6,
        completeness=0.3,
        overall=0.69,
        reasoning="Right cause, only one failure covered.",
    )
    fake_agent = SimpleNamespace(run_sync=_make_agent_run_sync(partial))
    with patch.object(judge_module, "_build_judge_agent", return_value=fake_agent):
        verdict = judge_rca(
            rca_output="config_error: port mismatch only",
            expected_output="config_error: port mismatch; capacity_issue: memory limit",
            settings=settings,  # type: ignore[arg-type]
        )
    assert verdict.completeness == pytest.approx(0.3)


@pytest.mark.integration
def test_judge_against_real_llm_dev_split() -> None:
    """Smoke test with real LiteLLM; run via ``pytest -m integration``."""
    verdict = judge_rca(
        rca_output=(
            "config_error: Service backend-api targetPort=8080 but container listens on 3000."
        ),
        expected_output=(
            "config_error: Service backend-api targetPort mismatch (8080 vs container 3000)."
        ),
    )
    assert 0.0 <= verdict.overall <= 1.0
    assert verdict.judge_model
