"""Sanity checks that the shared RCA system prompt covers expected topics."""

from src.common.prompt import SYSTEM_PROMPT


def test_system_prompt_is_shared_and_mentions_rca_scope() -> None:
    """Prompt should anchor the agent as an SRE doing evidence-based RCA."""
    lower = SYSTEM_PROMPT.lower()
    assert "kubernetes" in lower
    assert "evidence" in lower
    # Explicit RCA wording or established root-cause / causal-analysis framing
    assert "root cause analysis" in lower or (
        "root cause" in lower and "causal analysis" in lower
    )
