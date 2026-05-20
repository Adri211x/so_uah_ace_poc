"""Sanity checks that the shared RCA system prompt covers expected topics."""

from src.common.prompt import (
    JUDGE_SYSTEM_PROMPT,
    JUDGE_USER_PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
)


def test_system_prompt_is_shared_and_mentions_rca_scope() -> None:
    """Prompt should anchor the agent as an SRE doing evidence-based RCA."""
    lower = SYSTEM_PROMPT.lower()
    assert "kubernetes" in lower
    assert "evidence" in lower
    # Explicit RCA wording or established root-cause / causal-analysis framing
    assert "root cause analysis" in lower or ("root cause" in lower and "causal analysis" in lower)


def test_judge_system_prompt_defines_rubric_dimensions() -> None:
    """Judge system prompt must describe the four scoring dimensions and zero-rule."""
    lower = JUDGE_SYSTEM_PROMPT.lower()
    for dimension in ("root_cause_match", "evidence_quality", "completeness", "overall"):
        assert dimension in lower
    assert "unknown" in lower
    assert "0.0" in lower or "[0" in lower


def test_judge_user_prompt_template_contains_required_placeholders() -> None:
    """User template must accept ``{rca_output}`` and ``{expected_output}`` substitution."""
    formatted = JUDGE_USER_PROMPT_TEMPLATE.format(
        rca_output="AGENT_OUTPUT_PLACEHOLDER",
        expected_output="GROUND_TRUTH_PLACEHOLDER",
    )
    assert "AGENT_OUTPUT_PLACEHOLDER" in formatted
    assert "GROUND_TRUTH_PLACEHOLDER" in formatted
