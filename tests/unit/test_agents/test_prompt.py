"""Sanity checks that the shared RCA system prompt covers expected topics."""

from src.common.prompt import (
    ACE_CURATOR_SYSTEM_PROMPT,
    ACE_CURATOR_USER_PROMPT_TEMPLATE,
    ACE_GENERATOR_PLAYBOOK_PROMPT,
    JUDGE_SYSTEM_PROMPT,
    JUDGE_USER_PROMPT_TEMPLATE,
    REFLECTOR_SYSTEM_PROMPT,
    REFLECTOR_USER_PROMPT_TEMPLATE,
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


def test_reflector_prompts_cover_trajectory_and_playbook_context() -> None:
    """Reflector prompts must accept trajectory, result, and playbook context."""
    assert "trajectory" in REFLECTOR_SYSTEM_PROMPT.lower()
    assert "playbook" in REFLECTOR_SYSTEM_PROMPT.lower()

    formatted = REFLECTOR_USER_PROMPT_TEMPLATE.format(
        scenario="SCENARIO_PLACEHOLDER",
        outcome="OUTCOME_PLACEHOLDER",
        current_playbook="PLAYBOOK_PLACEHOLDER",
        expected_output="GROUND_TRUTH_PLACEHOLDER",
        rca_output="RCA_PLACEHOLDER",
        golden_entities="GOLDEN_PLACEHOLDER",
        matched_entities="MATCHED_PLACEHOLDER",
        missing_entities="MISSING_PLACEHOLDER",
        judge_verdict="JUDGE_PLACEHOLDER",
        cosine_similarity="COSINE_PLACEHOLDER",
        trajectory_steps="TRAJECTORY_PLACEHOLDER",
    )
    assert "SCENARIO_PLACEHOLDER" in formatted
    assert "PLAYBOOK_PLACEHOLDER" in formatted
    assert "TRAJECTORY_PLACEHOLDER" in formatted


def test_ace_generator_prompt_requests_stable_playbook_usage() -> None:
    """ACE Generator prompt must explain playbook usage and citation format."""
    formatted = ACE_GENERATOR_PLAYBOOK_PROMPT.format(
        scenario="SCENARIO_PLACEHOLDER",
        playbook_entries="PLAYBOOK_ENTRIES_PLACEHOLDER",
    )
    assert "SCENARIO_PLACEHOLDER" in formatted
    assert "PLAYBOOK_ENTRIES_PLACEHOLDER" in formatted
    assert "<playbook_usage>" in formatted
    assert "entry_id" in formatted
    assert "verified evidence" in formatted


def test_ace_curator_prompts_cover_playbook_and_candidate_insights() -> None:
    """ACE Curator prompts should support a future LLM proposal step."""
    assert "ACE Curator" in ACE_CURATOR_SYSTEM_PROMPT
    assert "deterministic" in ACE_CURATOR_SYSTEM_PROMPT.lower()

    formatted = ACE_CURATOR_USER_PROMPT_TEMPLATE.format(
        current_playbook="PLAYBOOK_PLACEHOLDER",
        candidate_insights="INSIGHTS_PLACEHOLDER",
    )
    assert "PLAYBOOK_PLACEHOLDER" in formatted
    assert "INSIGHTS_PLACEHOLDER" in formatted
