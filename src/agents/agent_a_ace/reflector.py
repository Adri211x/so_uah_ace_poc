"""ACE Reflector: turn evaluated ``AgentResult`` rows into candidate insights."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from src.common.config import AgentSettings, get_settings
from src.common.llm import build_model
from src.common.prompt import REFLECTOR_SYSTEM_PROMPT, REFLECTOR_USER_PROMPT_TEMPLATE
from src.common.schemas import AceInsight, AgentResult, Playbook

logger = logging.getLogger(__name__)

DEFAULT_JUDGE_SUCCESS_THRESHOLD = 0.7
DEFAULT_COSINE_SUCCESS_THRESHOLD = 0.5
_REFLECTOR_TEMPERATURE = 0.0


class ReflectorInsight(BaseModel):
    """Structured LLM response for one ACE reflection."""

    insight_text: str = Field(..., min_length=1, max_length=2000)


@lru_cache(maxsize=4)
def _build_reflector_agent(model_name: str) -> Agent[None, ReflectorInsight]:
    """Build and cache the LLM-backed ACE Reflector agent.

    Args:
        model_name: LiteLLM model name used for reflection.

    Returns:
        Cached ``Agent`` producing ``ReflectorInsight``.
    """
    settings = get_settings()
    return Agent(
        model=build_model(settings, model_name=model_name),
        output_type=ReflectorInsight,
        system_prompt=REFLECTOR_SYSTEM_PROMPT,
        model_settings=ModelSettings(temperature=_REFLECTOR_TEMPERATURE),
        name="ace_reflector",
    )


def reflect_result(
    result: AgentResult,
    *,
    scenario: str | None = None,
    playbook: Playbook | None = None,
    llm_enabled: bool = True,
    settings: AgentSettings | None = None,
    judge_success_threshold: float = DEFAULT_JUDGE_SUCCESS_THRESHOLD,
    cosine_success_threshold: float = DEFAULT_COSINE_SUCCESS_THRESHOLD,
) -> list[AceInsight]:
    """Extract one actionable insight from one evaluated sample.

    Args:
        result: Evaluated runner output.
        scenario: Optional scenario/family label to scope the insight.
        playbook: Current playbook, used by the LLM to avoid duplicate lessons.
        llm_enabled: When ``True`` use the LLM Reflector and fall back to rules
            on failure.
        settings: Optional runtime settings; defaults to ``get_settings()``.
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
    text = ""
    if llm_enabled:
        try:
            text = _generate_llm_insight(
                result=result,
                scenario=scenario,
                outcome=outcome,
                missing_entities=missing_entities,
                matched_entities=matched_entities,
                playbook=playbook,
                settings=settings,
            )
        except Exception:
            logger.warning(
                "LLM reflector failed for sample %s; using rule-based fallback.",
                result.sample_id,
                exc_info=True,
            )

    if not text:
        text = _build_rule_based_insight_text(
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
    playbook: Playbook | None = None,
    llm_enabled: bool = True,
    settings: AgentSettings | None = None,
    judge_success_threshold: float = DEFAULT_JUDGE_SUCCESS_THRESHOLD,
    cosine_success_threshold: float = DEFAULT_COSINE_SUCCESS_THRESHOLD,
) -> list[AceInsight]:
    """Reflect a batch of evaluated samples.

    Args:
        results: Evaluated runner outputs.
        scenario_by_sample_id: Optional lookup used to scope lessons.
        playbook: Current playbook, included in the LLM reflection prompt.
        llm_enabled: When ``True`` use the LLM Reflector and fall back to rules
            on failure.
        settings: Optional runtime settings; defaults to ``get_settings()``.
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
                playbook=playbook,
                llm_enabled=llm_enabled,
                settings=settings,
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
            llm_enabled=False,
            judge_success_threshold=self._judge_success_threshold,
            cosine_success_threshold=self._cosine_success_threshold,
        )


class LlmReflector:
    """Facade for the LLM-backed ACE Reflector."""

    def reflect(
        self,
        result: AgentResult,
        *,
        scenario: str | None = None,
        playbook: Playbook | None = None,
        settings: AgentSettings | None = None,
    ) -> list[AceInsight]:
        """Create ACE insights from one evaluated agent result using an LLM."""
        return reflect_result(
            result,
            scenario=scenario,
            playbook=playbook,
            llm_enabled=True,
            settings=settings,
        )


def _to_prompt_json(value: Any) -> str:
    """Serialize prompt payloads as stable, readable JSON."""
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _playbook_payload(playbook: Playbook | None, *, max_entries: int = 20) -> list[dict[str, Any]]:
    """Return a compact representation of the current playbook."""
    if playbook is None or not playbook.entries:
        return []

    ranked_entries = sorted(
        playbook.entries,
        key=lambda entry: (entry.helpful_count - entry.harmful_count, entry.helpful_count),
        reverse=True,
    )
    return [
        {
            "text": entry.text,
            "scenario": entry.scenario,
            "helpful_count": entry.helpful_count,
            "harmful_count": entry.harmful_count,
        }
        for entry in ranked_entries[:max_entries]
    ]


def _format_reflector_prompt(
    *,
    result: AgentResult,
    scenario: str | None,
    outcome: Literal["success", "failure"],
    missing_entities: list[str],
    matched_entities: list[str],
    playbook: Playbook | None,
) -> str:
    """Build the LLM Reflector user prompt."""
    return REFLECTOR_USER_PROMPT_TEMPLATE.format(
        scenario=scenario or "unknown",
        outcome=outcome,
        current_playbook=_to_prompt_json(_playbook_payload(playbook)),
        expected_output=result.expected_output,
        rca_output=result.rca_output,
        golden_entities=_to_prompt_json(result.golden_entities),
        matched_entities=_to_prompt_json(matched_entities),
        missing_entities=_to_prompt_json(missing_entities),
        judge_verdict=_to_prompt_json(
            result.judge_verdict.model_dump() if result.judge_verdict is not None else None
        ),
        cosine_similarity=_to_prompt_json(
            result.cosine_similarity.model_dump() if result.cosine_similarity is not None else None
        ),
        trajectory_steps=_to_prompt_json([step.model_dump() for step in result.trajectory_steps]),
    )


def _generate_llm_insight(
    *,
    result: AgentResult,
    scenario: str | None,
    outcome: Literal["success", "failure"],
    missing_entities: list[str],
    matched_entities: list[str],
    playbook: Playbook | None,
    settings: AgentSettings | None,
) -> str:
    """Generate one playbook lesson with the LLM Reflector."""
    cfg = settings or get_settings()
    user_prompt = _format_reflector_prompt(
        result=result,
        scenario=scenario,
        outcome=outcome,
        missing_entities=missing_entities,
        matched_entities=matched_entities,
        playbook=playbook,
    )
    response = _build_reflector_agent(cfg.reflector_model_name).run_sync(user_prompt)
    output: ReflectorInsight = response.output
    return output.insight_text.strip()


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


def _build_rule_based_insight_text(
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
