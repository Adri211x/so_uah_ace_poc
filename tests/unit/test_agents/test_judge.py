"""Unit tests for ``src.common.judge`` (LLM-as-a-judge evaluator).

The judge talks to the corporate LiteLLM proxy through ``pydantic_ai.Agent``;
we never let the unit suite reach the network. Every test that exercises the
agent path patches the cached ``Agent.run_sync`` to return a canned
``JudgeVerdict`` so behavior is deterministic in CI.

A single ``@pytest.mark.integration`` test is provided as documentation for the
opt-in real-LLM path; it is excluded from the default pytest run by
``addopts = "-m 'not integration'"`` in ``pyproject.toml``.
"""

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
    """Return a fake ``run_sync`` that yields the supplied verdict.

    pydantic-ai's ``run_sync`` returns an object with an ``output`` attribute,
    so we wrap the verdict in ``SimpleNamespace`` to keep the runner happy.
    """

    def _run_sync(_user_prompt: str) -> SimpleNamespace:
        return SimpleNamespace(output=verdict)

    return _run_sync


def test_judge_verdict_rejects_out_of_range_scores() -> None:
    """Verdict scores must live in ``[0.0, 1.0]`` (Pydantic ``ge``/``le`` bounds)."""
    with pytest.raises(ValidationError):
        JudgeVerdict(
            root_cause_match=1.5,
            evidence_quality=0.5,
            completeness=0.5,
            overall=0.5,
            reasoning="invalid",
        )

    with pytest.raises(ValidationError):
        JudgeVerdict(
            root_cause_match=0.5,
            evidence_quality=-0.1,
            completeness=0.5,
            overall=0.5,
            reasoning="invalid",
        )


def test_judge_short_circuits_on_unknown_output() -> None:
    """The rubric mandates all-zero verdicts for ``unknown`` agent outputs."""
    settings = SimpleNamespace(judge_model_name="gpt-4o-mini")

    verdict = judge_rca(
        rca_output="unknown",
        expected_output="config_error: targetPort mismatch.",
        settings=settings,  # type: ignore[arg-type]
    )

    assert verdict.root_cause_match == 0.0
    assert verdict.evidence_quality == 0.0
    assert verdict.completeness == 0.0
    assert verdict.overall == 0.0
    assert verdict.judge_model == "gpt-4o-mini"


def test_judge_short_circuits_on_empty_output() -> None:
    """Whitespace-only outputs receive the same all-zero verdict as ``unknown``."""
    settings = SimpleNamespace(judge_model_name="gpt-4o-mini")

    verdict = judge_rca(
        rca_output="   \n  ",
        expected_output="some ground truth",
        settings=settings,  # type: ignore[arg-type]
    )

    assert verdict.overall == 0.0
    assert verdict.reasoning


def test_judge_requires_expected_output() -> None:
    """Without a ground truth the judge cannot score anything."""
    settings = SimpleNamespace(judge_model_name="gpt-4o-mini")

    with pytest.raises(ValueError, match="expected_output"):
        judge_rca(
            rca_output="config_error: port mismatch",
            expected_output="",
            settings=settings,  # type: ignore[arg-type]
        )


def test_judge_returns_perfect_match_verdict() -> None:
    """When the judge agent returns 1.0s, ``judge_rca`` propagates them verbatim."""
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
            rca_output="config_error: port mismatch backend-api targetPort 8080 vs 3000",
            expected_output="config_error: Service backend-api targetPort 8080 vs container 3000",
            settings=settings,  # type: ignore[arg-type]
        )

    assert verdict.root_cause_match == 1.0
    assert verdict.overall == 1.0
    # The judge module must backfill the model name when the LLM omits it.
    assert verdict.judge_model == "gpt-4o-mini"


def test_judge_returns_partial_match_verdict() -> None:
    """Partial matches keep root_cause_match high while completeness drops."""
    settings = SimpleNamespace(judge_model_name="gpt-4o-mini")
    partial = JudgeVerdict(
        root_cause_match=0.9,
        evidence_quality=0.6,
        completeness=0.3,
        overall=0.69,
        reasoning="Right cause, only one of the two failures covered.",
    )

    fake_agent = SimpleNamespace(run_sync=_make_agent_run_sync(partial))

    with patch.object(judge_module, "_build_judge_agent", return_value=fake_agent):
        verdict = judge_rca(
            rca_output="config_error: port mismatch only",
            expected_output="config_error: port mismatch; capacity_issue: memory limit too low",
            settings=settings,  # type: ignore[arg-type]
        )

    assert verdict.root_cause_match == pytest.approx(0.9)
    assert verdict.completeness == pytest.approx(0.3)
    assert verdict.overall == pytest.approx(0.69)


def test_judge_preserves_explicit_judge_model() -> None:
    """If the LLM already filled ``judge_model``, the runner must not overwrite it."""
    settings = SimpleNamespace(judge_model_name="gpt-4o-mini")
    verdict_with_model = JudgeVerdict(
        root_cause_match=0.5,
        evidence_quality=0.5,
        completeness=0.5,
        overall=0.5,
        reasoning="ok",
        judge_model="gpt-4o",
    )

    fake_agent = SimpleNamespace(run_sync=_make_agent_run_sync(verdict_with_model))

    with patch.object(judge_module, "_build_judge_agent", return_value=fake_agent):
        verdict = judge_rca(
            rca_output="config_error: something",
            expected_output="config_error: something else",
            settings=settings,  # type: ignore[arg-type]
        )

    assert verdict.judge_model == "gpt-4o"


@pytest.mark.integration
def test_judge_against_real_llm_dev_split() -> None:
    """Smoke test that hits the real LiteLLM proxy on one ``dev`` split case.

    Skipped by default. Run with ``uv run pytest -m integration`` and a valid
    ``LITELLM_API_KEY`` exported in the environment.
    """
    rca = (
        "config_error: Service backend-api targetPort=8080 but container listens on 3000. "
        "Evidence: pod logs bind 3000; svc spec shows targetPort 8080."
    )
    expected = "config_error: Service backend-api targetPort mismatch (8080 vs container 3000)."

    verdict = judge_rca(rca_output=rca, expected_output=expected)

    assert 0.0 <= verdict.root_cause_match <= 1.0
    assert 0.0 <= verdict.overall <= 1.0
    assert verdict.judge_model
