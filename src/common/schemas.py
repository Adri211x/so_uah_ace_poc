"""Pydantic schemas for agent inputs and runner outputs.

``AgentInput`` describes what you send to an agent (or validate before a run).
``AgentResult`` is the stable JSON shape written by ``runner.run_agent_on_training_split``.
"""

from pydantic import BaseModel, Field


class AgentInput(BaseModel):
    """Structured input for root-cause analysis.

    Attributes:
        alert_text: Monitoring alert body (primary user message to the LLM).
        scenario: Optional dataset scenario hint (for example ``base_scenario``);
            not all call sites pass this into ``run_sync`` yet.
    """

    alert_text: str = Field(..., min_length=1)
    scenario: str | None = None


class AgentResult(BaseModel):
    """One evaluated sample: model output plus ground truth for offline review.

    Attributes:
        rca_output: Agent-produced root cause analysis text.
        sample_id: Dataset sample identifier (``id`` in the per-sample JSON).
        expected_output: Human-written reference RCA from the dataset.
        golden_entities: Three key strings used later for automated scoring.
    """

    rca_output: str = Field(..., min_length=1)
    sample_id: str = Field(..., min_length=1)
    expected_output: str = Field(..., min_length=1)
    golden_entities: list[str] = Field(default_factory=list)
