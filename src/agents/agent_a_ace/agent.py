"""Agent A (ACE): pydantic-ai RCA agent wired for LiteLLM, Langfuse, and MCP tools.

This module is intentionally parallel to agent_b_baseline.agent so both agents
share prompts and tool wiring; only the Langfuse / agent name and ACE behavior
should diverge.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from langfuse import get_client
from pydantic_ai import Agent

from src.agents.agent_a_ace.ablations import PlaybookScope
from src.agents.agent_a_ace.playbook_agent import PlaybookInjectingAgent, wrap_with_playbook
from src.common.config import AgentSettings, get_settings
from src.common.llm import build_model
from src.common.mcp_client import build_mcp_servers
from src.common.prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Ensures Langfuse OTLP wiring runs at most once per process.
_LANGFUSE_READY = False


def _configure_langfuse(settings: AgentSettings) -> None:
    """Initialize Langfuse OpenTelemetry export for pydantic-ai.

    Langfuse reads standard env vars; we populate them from AgentSettings so
    secrets never live in source code. If keys are missing, tracing is skipped
    so local development without Langfuse still works.

    Args:
        settings: Parsed application settings including optional Langfuse keys.
    """
    global _LANGFUSE_READY
    if _LANGFUSE_READY:
        return

    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        logger.warning("Langfuse keys are not set. Tracing is disabled.")
        return

    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
    os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
    os.environ["LANGFUSE_BASE_URL"] = settings.langfuse_base_url

    get_client()
    Agent.instrument_all()
    _LANGFUSE_READY = True


def build_agent(
    *,
    playbook_path: Path | None = None,
    playbook_scope: PlaybookScope | None = None,
) -> PlaybookInjectingAgent:
    """Build a fresh Agent A instance with optional playbook settings.

    Args:
        playbook_path: JSON playbook path used for ACE prompt injection.
        playbook_scope: Selection behavior for scenario-scoped entries.

    Returns:
        Ready-to-run Agent A wrapper.
    """
    settings = get_settings()
    _configure_langfuse(settings)

    agent = Agent(
        model=build_model(settings),
        output_type=str,
        system_prompt=SYSTEM_PROMPT,
        toolsets=build_mcp_servers(settings),
        name="agent_a_ace",
    )
    return wrap_with_playbook(agent, playbook_path=playbook_path, playbook_scope=playbook_scope)


@lru_cache
def get_agent() -> PlaybookInjectingAgent:
    """Return a cached pydantic-ai Agent configured for Agent A.

    Returns:
        Ready-to-run Agent with string output and ACE playbook injection.
    """
    return build_agent()


def main() -> None:
    """Manual smoke test: run one fixed alert through the agent and log output."""
    logging.basicConfig(level=logging.INFO)
    demo_alert = (
        "ALERT: KubeDeploymentReplicasMismatch\n"
        "Severity: WARNING\n"
        "Status: firing\n"
        "Summary: Deployment has not matched the expected number of replicas."
    )
    result = get_agent().run_sync(demo_alert)
    logger.info("Agent A RCA output:\n%s", result.output)


if __name__ == "__main__":
    main()
