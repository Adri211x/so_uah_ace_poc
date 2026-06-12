"""ACE learning loop: reflect evaluated runs and curate the playbook."""

from __future__ import annotations

import logging
from pathlib import Path

from src.agents.agent_a_ace.curator import JsonPlaybookCurator
from src.agents.agent_a_ace.reflector import reflect_batch
from src.common.schemas import AgentResult, Playbook

logger = logging.getLogger(__name__)

DEFAULT_PLAYBOOK_PATH = Path("tmp/playbook.json")


def run_ace_learning_loop(
    results: list[AgentResult],
    playbook_path: Path | None = None,
    *,
    scenario_by_sample_id: dict[str, str] | None = None,
) -> Playbook:
    """Reflect evaluated results and merge lessons into the playbook.

    Args:
        results: Evaluated outputs from ``src.common.runner``.
        playbook_path: JSON playbook destination.
        scenario_by_sample_id: Optional sample-to-scenario lookup.

    Returns:
        Updated playbook.
    """
    resolved_path = playbook_path or DEFAULT_PLAYBOOK_PATH
    insights = reflect_batch(results, scenario_by_sample_id=scenario_by_sample_id)
    curator = JsonPlaybookCurator(resolved_path)
    playbook = curator.curate(insights)
    logger.info("Updated ACE playbook at %s with %d entries.", resolved_path, len(playbook.entries))
    return playbook


def update_playbook_from_results(
    results: list[AgentResult],
    playbook_path: Path,
    scenario_by_sample_id: dict[str, str] | None = None,
) -> Playbook:
    """Backward-compatible alias for ``run_ace_learning_loop``."""
    return run_ace_learning_loop(
        results,
        playbook_path=playbook_path,
        scenario_by_sample_id=scenario_by_sample_id,
    )
