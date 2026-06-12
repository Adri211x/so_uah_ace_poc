"""Pydantic schemas for agent inputs, runner outputs, and ACE playbooks.

``AgentInput`` describes what you send to an agent (or validate before a run).
``AgentResult`` is the stable JSON shape written by ``runner.run_agent_on_training_split``.
ACE playbook schemas describe the small contract between Reflector, Curator,
and Generator.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.common.cosine_similarity import CosineSimilarityResult
from src.common.judge import JudgeVerdict


class AgentInput(BaseModel):
    """Structured input for root-cause analysis.

    Attributes:
        alert_text: Monitoring alert body (primary user message to the LLM).
        scenario: Optional dataset scenario hint (for example ``base_scenario``).
    """

    alert_text: str = Field(..., min_length=1)
    scenario: str | None = None


class TrajectoryStep(BaseModel):
    """One observable step in an agent execution trajectory.

    Attributes:
        step_order: One-based order inside the execution.
        component: ACE component that produced the step.
        action_type: Observable action type, for example ``alert``,
            ``tool_call``, ``tool_return``, or ``final_answer``.
        input_data: JSON-compatible action inputs.
        output_data: JSON-compatible action outputs or observations.
        duration_ms: Optional duration in milliseconds.
        tokens_in: Optional input token count associated with the step.
        tokens_out: Optional output token count associated with the step.
    """

    step_order: int = Field(..., ge=1)
    component: Literal["generator", "reflector", "curator"]
    action_type: str = Field(..., min_length=1)
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = Field(default=None, ge=0)
    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)


class AgentResult(BaseModel):
    """One evaluated sample: model output plus ground truth for offline review.

    Attributes:
        rca_output: Agent-produced root cause analysis text.
        sample_id: Dataset sample identifier (``id`` in the per-sample JSON).
        expected_output: Human-written reference RCA from the dataset.
        golden_entities: Three key strings used later for automated scoring.
        score: Number of golden entities found in ``rca_output`` (set by the
            scoring stage). ``None`` while the result has not been scored yet.
        matched_entities: Subset of ``golden_entities`` considered present in
            the RCA output. ``None`` while the result has not been scored yet.
        judge_verdict: Optional LLM-as-a-judge result comparing ``rca_output``
            to ``expected_output``. ``None`` when the judge was disabled
            (``--no-judge``) or when it raised an error during the run.
        cosine_similarity: Optional embedding-based cosine similarity between
            ``rca_output`` / ``expected_output`` and between each golden entity
            and ``rca_output``. ``None`` when the cosine evaluator was disabled
            (``--no-cosine``) or when it raised an error during the run.
        trajectory_steps: Observable Generator execution steps captured from
            pydantic-ai messages when available.
    """

    rca_output: str = Field(..., min_length=1)
    sample_id: str = Field(..., min_length=1)
    expected_output: str = Field(..., min_length=1)
    golden_entities: list[str] = Field(default_factory=list)
    score: int | None = None
    matched_entities: list[str] | None = None
    judge_verdict: JudgeVerdict | None = None
    cosine_similarity: CosineSimilarityResult | None = None
    trajectory_steps: list[TrajectoryStep] = Field(default_factory=list)


class AceInsight(BaseModel):
    """A reusable lesson extracted from one evaluated agent run.

    Attributes:
        text: Playbook-ready instruction or diagnostic heuristic.
        scenario: Optional scenario or family label that scopes the insight.
        source_sample_id: Dataset sample that produced this lesson.
        outcome: Whether the lesson came from a successful or failed run.
        score: Golden-entity score from the evaluation runner.
        matched_entities: Golden entities found in the RCA output.
        missing_entities: Golden entities not found in the RCA output.
    """

    text: str = Field(..., min_length=1)
    scenario: str | None = None
    source_sample_id: str = Field(..., min_length=1)
    outcome: Literal["success", "failure"]
    score: int | None = None
    matched_entities: list[str] = Field(default_factory=list)
    missing_entities: list[str] = Field(default_factory=list)


class PlaybookEntry(BaseModel):
    """One curated playbook entry with deterministic counters.

    Attributes:
        id: Stable identifier derived from the normalized text.
        text: Human-readable instruction injected into future Generator prompts.
        scenario: Optional scenario or family label that scopes the entry.
        helpful_count: Number of successful runs supporting this entry.
        harmful_count: Number of failed runs motivating this entry.
        source_sample_ids: Dataset sample ids that contributed to this entry.
    """

    id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    scenario: str | None = None
    helpful_count: int = Field(default=0, ge=0)
    harmful_count: int = Field(default=0, ge=0)
    source_sample_ids: list[str] = Field(default_factory=list)


class Playbook(BaseModel):
    """Versioned collection of curated ACE entries."""

    version: int = Field(default=1, ge=1)
    entries: list[PlaybookEntry] = Field(default_factory=list)
