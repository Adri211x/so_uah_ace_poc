"""ACE Reflector: turn evaluated ``AgentResult`` rows into candidate insights."""

from __future__ import annotations

import logging
from typing import Literal

from src.common.schemas import AceInsight, AgentResult

logger = logging.getLogger(__name__)

DEFAULT_JUDGE_SUCCESS_THRESHOLD = 0.7
DEFAULT_COSINE_SUCCESS_THRESHOLD = 0.5


def reflect_result(
    result: AgentResult,
    *,
    scenario: str | None = None,
    judge_success_threshold: float = DEFAULT_JUDGE_SUCCESS_THRESHOLD,
    cosine_success_threshold: float = DEFAULT_COSINE_SUCCESS_THRESHOLD,
) -> list[AceInsight]:
    """Extract one actionable insight from one evaluated sample.

    Args:
        result: Evaluated runner output.
        scenario: Optional scenario/family label to scope the insight.
        judge_success_threshold: Minimum judge ``overall`` score for success.
        cosine_success_threshold: Minimum RCA cosine similarity for success.

    Returns:
        One candidate playbook insight.
    """
    matched_entities = list(result.matched_entities or [])
    missing_entities = _missing_entities(result)
    outcome = _classify_outcome(
        result,
        judge_success_threshold=judge_success_threshold,
        cosine_success_threshold=cosine_success_threshold,
    )
    text = _build_insight_text(
        result=result,
        outcome=outcome,
        missing_entities=missing_entities,
        matched_entities=matched_entities,
        judge_success_threshold=judge_success_threshold,
        cosine_success_threshold=cosine_success_threshold,
    )

    return [
        AceInsight(
            text=text,
            scenario=scenario,
            source_sample_id=result.sample_id,
            outcome=outcome,
            score=result.score,
            matched_entities=matched_entities,
            missing_entities=missing_entities,
        )
    ]


def reflect_batch(
    results: list[AgentResult],
    *,
    scenario_by_sample_id: dict[str, str] | None = None,
    judge_success_threshold: float = DEFAULT_JUDGE_SUCCESS_THRESHOLD,
    cosine_success_threshold: float = DEFAULT_COSINE_SUCCESS_THRESHOLD,
) -> list[AceInsight]:
    """Reflect a batch of evaluated samples.

    Args:
        results: Evaluated runner outputs.
        scenario_by_sample_id: Optional lookup used to scope lessons.
        judge_success_threshold: Minimum judge ``overall`` score for success.
        cosine_success_threshold: Minimum RCA cosine similarity for success.

    Returns:
        Flattened list of candidate insights.
    """
    scenario_map = scenario_by_sample_id or {}
    insights: list[AceInsight] = []
    for result in results:
        insights.extend(
            reflect_result(
                result,
                scenario=scenario_map.get(result.sample_id),
                judge_success_threshold=judge_success_threshold,
                cosine_success_threshold=cosine_success_threshold,
            )
        )
    logger.info("Reflected %d results into %d insights.", len(results), len(insights))
    return insights


class RuleBasedReflector:
    """Backward-compatible facade over ``reflect_result``."""

    def __init__(
        self,
        success_threshold: int | None = None,
        *,
        judge_success_threshold: float = DEFAULT_JUDGE_SUCCESS_THRESHOLD,
        cosine_success_threshold: float = DEFAULT_COSINE_SUCCESS_THRESHOLD,
    ) -> None:
        """Initialize the reflector facade.

        Args:
            success_threshold: Deprecated golden-entity score threshold. Kept
                for existing call sites; full golden-entity match is now the
                success criterion.
            judge_success_threshold: Minimum judge ``overall`` score for success.
            cosine_success_threshold: Minimum RCA cosine similarity for success.
        """
        self._success_threshold = success_threshold
        self._judge_success_threshold = judge_success_threshold
        self._cosine_success_threshold = cosine_success_threshold

    def reflect(self, result: AgentResult, scenario: str | None = None) -> list[AceInsight]:
        """Create ACE insights from one evaluated agent result."""
        return reflect_result(
            result,
            scenario=scenario,
            judge_success_threshold=self._judge_success_threshold,
            cosine_success_threshold=self._cosine_success_threshold,
        )


def _missing_entities(result: AgentResult) -> list[str]:
    """Return golden entities that were not matched in the RCA output."""
    matched = set(result.matched_entities or [])
    return [entity for entity in result.golden_entities if entity not in matched]


def _classify_outcome(
    result: AgentResult,
    *,
    judge_success_threshold: float,
    cosine_success_threshold: float,
) -> Literal["success", "failure"]:
    """Classify one evaluated result using golden entities, judge, and cosine scores."""
    if (
        _golden_entities_success(result)
        and _judge_success(result, threshold=judge_success_threshold)
        and _cosine_success(result, threshold=cosine_success_threshold)
    ):
        return "success"
    return "failure"


def _golden_entities_success(result: AgentResult) -> bool:
    """Return whether all golden entities were matched."""
    if not result.golden_entities:
        return True
    matched_entities = result.matched_entities or []
    return len(matched_entities) == len(result.golden_entities)


def _judge_success(result: AgentResult, *, threshold: float) -> bool:
    """Return whether the optional LLM judge score passes the threshold."""
    if result.judge_verdict is None:
        return True
    return result.judge_verdict.overall >= threshold


def _cosine_success(result: AgentResult, *, threshold: float) -> bool:
    """Return whether the optional cosine RCA similarity passes the threshold."""
    if result.cosine_similarity is None:
        return True
    return result.cosine_similarity.rca_similarity >= threshold


def _build_insight_text(
    *,
    result: AgentResult,
    outcome: Literal["success", "failure"],
    missing_entities: list[str],
    matched_entities: list[str],
    judge_success_threshold: float,
    cosine_success_threshold: float,
) -> str:
    """Build a compact actionable insight for the playbook."""
    if outcome == "success":
        if matched_entities:
            return (
                "When investigating similar alerts, explicitly cite these "
                f"diagnostic signals in the RCA: {', '.join(matched_entities)}."
            )
        return "Reuse the diagnostic approach from this successful RCA when similar alerts appear."

    if missing_entities:
        return (
            "When the RCA misses the root cause, verify these expected "
            f"signals: {', '.join(missing_entities)}."
        )

    if result.judge_verdict is not None and result.judge_verdict.overall < judge_success_threshold:
        return (
            "Improve RCA alignment with the ground-truth root cause and cite "
            "concrete, verifiable evidence from logs, metrics, or pod state."
        )

    if (
        result.cosine_similarity is not None
        and result.cosine_similarity.rca_similarity < cosine_success_threshold
    ):
        return (
            "The RCA is semantically distant from the expected analysis. "
            "Restate the root cause and evidence using terminology closer to "
            "the incident symptoms."
        )

    return (
        "Review the expected root cause and ensure the RCA explains the "
        "underlying failure rather than only the surface symptoms."
    )
