"""Sanity checks that the shared RCA system prompt covers expected topics."""

from src.common.prompt import SYSTEM_PROMPT


def test_system_prompt_is_shared_and_mentions_rca_scope() -> None:
    """Prompt should anchor the agent as an SRE doing evidence-based RCA."""
    assert "root cause analysis" in SYSTEM_PROMPT.lower()
    assert "kubernetes" in SYSTEM_PROMPT.lower()
    assert "evidence" in SYSTEM_PROMPT.lower()
