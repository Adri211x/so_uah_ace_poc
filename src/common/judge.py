"""LLM-as-a-judge scorer for agent root cause analyses.

Implements ticket SO-1589: a second scorer that compares the agent's
``rca_output`` against the human-written ``expected_output`` and produces a
``JudgeVerdict`` with four ``[0.0, 1.0]`` scores plus reasoning.

The judge runs through the same LiteLLM corporate proxy as the agents and uses
``pydantic_ai.Agent`` with ``output_type=JudgeVerdict``. Because pydantic-ai is
instrumented globally (``Agent.instrument_all()``), each judge call appears as a
child span of the parent run in Langfuse.

Failures here are non-fatal: the runner catches exceptions and stores
``judge_verdict=None`` so an evaluation pipeline never aborts because of judge
infrastructure issues.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from src.common.config import AgentSettings, get_settings
from src.common.llm import build_model
from src.common.prompt import JUDGE_SYSTEM_PROMPT, JUDGE_USER_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)

_JUDGE_TEMPERATURE = 0.0
# Sentinel agent outputs that should short-circuit to an all-zero verdict
# without spending an LLM call.
_EMPTY_OUTPUT_TOKENS = frozenset({"", "unknown", "n/a", "none", "null"})


class JudgeVerdict(BaseModel):
    """Cualitative score returned by the LLM judge for one (rca, ground_truth) pair.

    Attributes:
        root_cause_match: ``[0.0, 1.0]`` score on whether the agent identifies
            the same underlying cause (penalises symptom vs root cause confusion).
        evidence_quality: ``[0.0, 1.0]`` score on how specific and verifiable
            the cited signals are (logs, exit codes, configs, pod states).
        completeness: ``[0.0, 1.0]`` coverage score for multi-cause scenarios.
        overall: ``[0.0, 1.0]`` weighted summary
            (root_cause_match 0.5, evidence_quality 0.3, completeness 0.2).
        reasoning: Short natural-language explanation of the score (1-2 sentences).
        judge_model: Name of the LLM used to produce this verdict; populated by
            ``judge_rca`` so the persistence layer can record it alongside scores.
    """

    root_cause_match: float = Field(..., ge=0.0, le=1.0)
    evidence_quality: float = Field(..., ge=0.0, le=1.0)
    completeness: float = Field(..., ge=0.0, le=1.0)
    overall: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(default="", max_length=2000)
    judge_model: str | None = None


def _empty_verdict(judge_model: str | None) -> JudgeVerdict:
    """Build a zeroed verdict for empty / ``unknown`` agent outputs.

    Returning early keeps the behavior deterministic and avoids burning tokens
    on cases where the rubric mandates a 0 anyway.

    Args:
        judge_model: Name of the model that would have run, recorded for traceability.

    Returns:
        ``JudgeVerdict`` with all numeric scores at ``0.0``.
    """
    return JudgeVerdict(
        root_cause_match=0.0,
        evidence_quality=0.0,
        completeness=0.0,
        overall=0.0,
        reasoning="Agent output is empty or 'unknown'; rubric mandates all zeros.",
        judge_model=judge_model,
    )


@lru_cache(maxsize=4)
def _build_judge_agent(judge_model_name: str) -> Agent[None, JudgeVerdict]:
    """Build (and cache) the pydantic-ai judge agent for a given model name.

    The agent is parameterised by ``output_type=JudgeVerdict`` so pydantic-ai
    enforces the structured output. Caching avoids re-creating the OpenAI client
    on every call when the runner evaluates many samples in a row.

    Args:
        judge_model_name: LiteLLM model name to use for evaluation.

    Returns:
        Cached ``Agent`` instance ready for ``run_sync``.
    """
    settings = get_settings()
    return Agent(
        model=build_model(settings, model_name=judge_model_name),
        output_type=JudgeVerdict,
        system_prompt=JUDGE_SYSTEM_PROMPT,
        model_settings=ModelSettings(temperature=_JUDGE_TEMPERATURE),
        name="judge_rca",
    )


def _is_effectively_empty(text: str) -> bool:
    """Return ``True`` for outputs the rubric treats as no answer at all."""
    return text.strip().lower() in _EMPTY_OUTPUT_TOKENS


def judge_rca(
    rca_output: str,
    expected_output: str,
    settings: AgentSettings | None = None,
) -> JudgeVerdict:
    """Score one agent RCA against the ground truth using the LLM judge.

    The function:
        1. Short-circuits to a zero verdict when ``rca_output`` is empty or one
           of the well-known "no answer" tokens (``unknown``, ``n/a``, ...).
        2. Otherwise, runs the cached pydantic-ai judge agent at temperature 0
           with structured ``JudgeVerdict`` output.

    Errors are not swallowed here: the caller (typically the runner) is
    responsible for catching exceptions and demoting failures to
    ``judge_verdict=None`` without aborting the run.

    Args:
        rca_output: Agent-produced root cause analysis text.
        expected_output: Human-written reference RCA from the dataset.
        settings: Optional pre-built settings; defaults to ``get_settings()``.

    Returns:
        ``JudgeVerdict`` with four scores in ``[0.0, 1.0]`` and a short
        reasoning string.

    Raises:
        ValueError: When ``expected_output`` is empty (cannot score without
            a ground truth).
    """
    if not expected_output or not expected_output.strip():
        raise ValueError("expected_output is required to score with the LLM judge.")

    cfg = settings or get_settings()
    judge_model = cfg.judge_model_name

    if _is_effectively_empty(rca_output):
        logger.info("Judge short-circuit: rca_output is empty or 'unknown'.")
        return _empty_verdict(judge_model)

    user_prompt = JUDGE_USER_PROMPT_TEMPLATE.format(
        rca_output=rca_output.strip(),
        expected_output=expected_output.strip(),
    )

    agent = _build_judge_agent(judge_model)
    response = agent.run_sync(user_prompt)
    verdict: JudgeVerdict = response.output
    # The structured output may omit the model name; backfill it so persistence
    # always knows which judge produced the score.
    if not verdict.judge_model:
        verdict = verdict.model_copy(update={"judge_model": judge_model})

    logger.debug(
        "Judge verdict: rcm=%.3f eq=%.3f cmp=%.3f overall=%.3f model=%s",
        verdict.root_cause_match,
        verdict.evidence_quality,
        verdict.completeness,
        verdict.overall,
        verdict.judge_model,
    )
    return verdict
