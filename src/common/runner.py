"""Runner utilities to execute agents against ``event_system`` training splits.

Expects a split file such as ``training_stratified.json`` (list of rows with
``split``, ``file``, etc.) and a parallel ``data/datasets/*.json`` tree with
full samples. The mock MCP server for each scenario must already be running
when you execute real agents; unit tests use a stub ``SyncAgent`` instead.

The runner produces one ``AgentResult`` per evaluated sample. When the
LLM-as-a-judge is enabled (default; opt-out via ``--no-judge``), the
``judge_verdict`` field is populated with ``[0.0, 1.0]`` qualitative scores
that complement the keyword-based ``score``. Judge failures are logged and
demoted to ``judge_verdict=None`` so an evaluation never aborts because of
evaluator infrastructure issues.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Protocol

from src.common.judge import JudgeVerdict, judge_rca
from src.common.schemas import AgentInput, AgentResult
from src.common.scoring import score_golden_entities

logger = logging.getLogger(__name__)


class SyncAgent(Protocol):
    """Minimal synchronous agent interface consumed by this runner.

    Matches ``pydantic_ai.Agent.run_sync`` for typing without importing Agent here.
    """

    def run_sync(self, user_prompt: str) -> Any:
        """Run the agent once and return a result or raw string.

        Args:
            user_prompt: Alert text (and optional context) as a single string.

        Returns:
            ``AgentRunResult``-like object with ``output``, or a plain string.
        """


def _load_json_file(path: Path) -> Any:
    """Load JSON from ``path`` with UTF-8 encoding.

    Args:
        path: File to read.

    Returns:
        Parsed JSON (typically ``list`` or ``dict``).
    """
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _extract_agent_output(agent_response: Any) -> str:
    """Normalize agent return values to a trimmed string.

    pydantic-ai returns an object with ``output``; tests may return plain strings.

    Args:
        agent_response: Return value of ``run_sync``.

    Returns:
        Non-empty stripped text used as ``AgentResult.rca_output``.
    """
    output = getattr(agent_response, "output", agent_response)
    return str(output).strip()


def _maybe_judge(
    rca_output: str,
    expected_output: str,
    sample_id: str,
    *,
    enabled: bool,
) -> JudgeVerdict | None:
    """Run the LLM judge for one sample, swallowing infra errors.

    Args:
        rca_output: Agent-produced RCA text.
        expected_output: Ground-truth RCA from the dataset.
        sample_id: Identifier used only for log context.
        enabled: When ``False`` (``--no-judge``) the judge is skipped entirely.

    Returns:
        ``JudgeVerdict`` on success, ``None`` when disabled or on failure.
    """
    if not enabled:
        return None

    try:
        return judge_rca(rca_output=rca_output, expected_output=expected_output)
    except Exception:
        logger.warning(
            "LLM judge failed for sample %s; storing judge_verdict=None.",
            sample_id,
            exc_info=True,
        )
        return None


def run_agent_on_training_split(
    agent: SyncAgent,
    training_split_path: Path,
    output_path: Path,
    dataset_dir: Path | None = None,
    *,
    judge_enabled: bool = True,
    limit: int | None = None,
) -> list[AgentResult]:
    """Execute an agent over **test** rows in a training split file.

    Only rows where ``split == "test"`` are evaluated. For each row, the
    companion dataset file named in ``file`` is loaded; ``input.alert_text``
    is sent to the agent.

    Args:
        agent: Callable agent exposing ``run_sync``.
        training_split_path: Path to ``training_*.json`` under ``data/event_system``.
        output_path: JSON file to write (list of ``AgentResult`` dicts).
        dataset_dir: Directory containing dataset JSON files; if omitted,
            defaults to ``<parent of training_split_path>/data/datasets``.
        judge_enabled: When ``True`` (default) each evaluated sample is also
            scored by the LLM-as-a-judge. Pass ``False`` from the CLI via
            ``--no-judge`` to disable for fast or offline runs.
        limit: Optional cap on the number of **evaluated** samples (rows with
            ``split == "test"``). ``None`` processes every test row.

    Returns:
        In-memory list of ``AgentResult`` instances (same order as iteration).

    Raises:
        FileNotFoundError: If the split file or dataset directory is missing.
        ValueError: If the split JSON is not a list, rows lack required fields,
            or ``limit`` is not a positive integer.
    """
    if limit is not None and limit <= 0:
        raise ValueError("limit must be a positive integer or None.")

    if not training_split_path.exists():
        raise FileNotFoundError(f"Training split file not found: {training_split_path}")

    split_rows = _load_json_file(training_split_path)
    if not isinstance(split_rows, list):
        raise ValueError("Training split file must contain a JSON list.")

    resolved_dataset_dir = dataset_dir or training_split_path.parent / "data" / "datasets"
    if not resolved_dataset_dir.exists():
        raise FileNotFoundError(f"Dataset directory not found: {resolved_dataset_dir}")

    results: list[AgentResult] = []
    for row in split_rows:
        if limit is not None and len(results) >= limit:
            logger.info("Limit reached (%d samples); stopping split iteration early.", limit)
            break

        if row.get("split") != "test":
            continue

        dataset_file = row.get("file")
        if not dataset_file:
            raise ValueError("Each split row must include a 'file' field.")

        sample_path = resolved_dataset_dir / dataset_file
        sample = _load_json_file(sample_path)

        alert_text = sample.get("input", {}).get("alert_text")
        if not alert_text:
            raise ValueError(f"Sample {sample.get('id')} does not contain input.alert_text.")

        agent_input = AgentInput(alert_text=alert_text, scenario=row.get("base_scenario"))
        agent_response = agent.run_sync(agent_input.alert_text)

        rca_output = _extract_agent_output(agent_response)
        expected_output = sample.get("expected_output", "")
        golden_entities = sample.get("golden_entities", [])
        scoring = score_golden_entities(rca_output, golden_entities)

        judge_verdict = _maybe_judge(
            rca_output=rca_output,
            expected_output=expected_output,
            sample_id=str(sample.get("id", "")),
            enabled=judge_enabled and bool(expected_output),
        )

        results.append(
            AgentResult(
                sample_id=sample["id"],
                rca_output=rca_output,
                expected_output=expected_output,
                golden_entities=golden_entities,
                score=scoring["score"],
                matched_entities=scoring["matched"],
                judge_verdict=judge_verdict,
            )
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump([item.model_dump() for item in results], handle, indent=2, ensure_ascii=False)

    return results


def _load_agent(agent_name: str) -> SyncAgent:
    """Import and construct the selected agent implementation.

    Args:
        agent_name: ``agent_a_ace`` or ``agent_b_baseline``.

    Returns:
        Cached agent from the chosen module's ``get_agent``.

    Raises:
        ValueError: If ``agent_name`` is not supported.
    """
    if agent_name == "agent_a_ace":
        from src.agents.agent_a_ace.agent import get_agent

        return get_agent()

    if agent_name == "agent_b_baseline":
        from src.agents.agent_b_baseline.agent import get_agent

        return get_agent()

    raise ValueError("Unsupported agent. Use 'agent_a_ace' or 'agent_b_baseline'.")


def _parse_args() -> argparse.Namespace:
    """Build the CLI argument parser for ``python -m src.common.runner``."""
    parser = argparse.ArgumentParser(
        description="Run RCA agent against training split test samples."
    )
    parser.add_argument("--agent", choices=["agent_a_ace", "agent_b_baseline"], required=True)
    parser.add_argument("--training-file", required=True, help="Path to training_*.json file.")
    parser.add_argument("--output-file", required=True, help="Path to output JSON results.")
    parser.add_argument(
        "--dataset-dir",
        default=None,
        help="Optional path to dataset JSON files (default: <training parent>/data/datasets).",
    )
    parser.add_argument(
        "--no-judge",
        dest="judge_enabled",
        action="store_false",
        help="Disable the LLM-as-a-judge evaluator (enabled by default).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Optional cap on the number of evaluated test samples. "
            "Useful for cheap smoke tests against real splits (e.g. --limit 3)."
        ),
    )
    parser.set_defaults(judge_enabled=True)
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint: parse flags, load the agent, run the split, write JSON."""
    args = _parse_args()
    agent = _load_agent(args.agent)
    run_agent_on_training_split(
        agent=agent,
        training_split_path=Path(args.training_file),
        output_path=Path(args.output_file),
        dataset_dir=Path(args.dataset_dir) if args.dataset_dir else None,
        judge_enabled=args.judge_enabled,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
