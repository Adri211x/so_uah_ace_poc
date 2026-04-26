"""Runner utilities to execute agents against ``event_system`` training splits.

Expects a split file such as ``training_stratified.json`` (list of rows with
``split``, ``file``, etc.) and a parallel ``data/datasets/*.json`` tree with
full samples. The mock MCP server for each scenario must already be running
when you execute real agents; unit tests use a stub ``SyncAgent`` instead.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Protocol

from src.common.schemas import AgentInput, AgentResult


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


def run_agent_on_training_split(
    agent: SyncAgent,
    training_split_path: Path,
    output_path: Path,
    dataset_dir: Path | None = None,
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

    Returns:
        In-memory list of ``AgentResult`` instances (same order as iteration).

    Raises:
        FileNotFoundError: If the split file or dataset directory is missing.
        ValueError: If the split JSON is not a list or rows lack required fields.
    """
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
        # Train rows are skipped; the ticket evaluates generalization on test only.
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
        # The LLM currently receives alert text only; scenario is validated for future use.
        agent_response = agent.run_sync(agent_input.alert_text)

        results.append(
            AgentResult(
                sample_id=sample["id"],
                rca_output=_extract_agent_output(agent_response),
                expected_output=sample.get("expected_output", ""),
                golden_entities=sample.get("golden_entities", []),
            )
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump([item.model_dump() for item in results], handle, indent=2, ensure_ascii=False)

    return results


def _load_agent(agent_name: str) -> SyncAgent:
    """Import and construct the selected agent implementation.

    Imports are deferred so running the CLI for one agent does not eagerly
    import the other package.

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
    )


if __name__ == "__main__":
    main()
