"""Agent A (ACE): pydantic-ai RCA agent wired for LiteLLM, Langfuse, and MCP tools.

This module is intentionally parallel to ``agent_b_baseline.agent`` so both
agents share prompts and tool wiring; only the Langfuse / agent ``name`` and
future ACE behavior should diverge.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from langfuse import get_client
from pydantic_ai import Agent

from src.common.config import AgentSettings, get_settings
from src.common.llm import build_model
from src.common.mcp_client import build_mcp_servers
from src.common.prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Ensures Langfuse OTLP wiring runs at most once per process (both agents share instrumentation).
_LANGFUSE_READY = False


def _configure_langfuse(settings: AgentSettings) -> None:
    """Initialize Langfuse OpenTelemetry export for pydantic-ai.

    Langfuse reads standard env vars; we populate them from ``AgentSettings`` so
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

    # Langfuse Python SDK + pydantic-ai instrumentation expect these names.
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
    os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
    os.environ["LANGFUSE_BASE_URL"] = settings.langfuse_base_url

    get_client()
    Agent.instrument_all()
    _LANGFUSE_READY = True


@lru_cache
def get_agent() -> Agent[None, str]:
    """Return a cached pydantic-ai ``Agent`` configured for Agent A.

    The agent uses the shared system prompt, LiteLLM-backed model, and MCP
    toolsets. Caching avoids rebuilding clients when the smoke test or runner
    invokes this function repeatedly.

    Returns:
        Ready-to-run ``Agent`` with string output (plain RCA text).
    """
    settings = get_settings()
    _configure_langfuse(settings)

    return Agent(
        model=build_model(settings),
        output_type=str,
        system_prompt=SYSTEM_PROMPT,
        toolsets=build_mcp_servers(settings),
        name="agent_a_ace",
    )


def main() -> None:
    """Manual smoke test: run one fixed alert through the agent and log output.

    Start the mock MCP server for a matching scenario before calling this in a
    real environment; otherwise tool calls may fail at runtime.
    """
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
