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
import inspect
import json
import logging
from pathlib import Path
from typing import Any, Protocol

from src.common.cosine_similarity import CosineSimilarityResult, compute_cosine_scores
from src.common.judge import JudgeVerdict, judge_rca
from src.common.schemas import AgentInput, AgentResult
from src.common.scoring import score_golden_entities

logger = logging.getLogger(__name__)


class SyncAgent(Protocol):
    """Minimal synchronous agent interface consumed by this runner.

    Matches ``pydantic_ai.Agent.run_sync`` for typing without importing Agent here.
    """

    def run_sync(self, user_prompt: str, *args: Any, **kwargs: Any) -> Any:
        """Run the agent once and return a result or raw string.

        Args:
            user_prompt: Alert text (and optional context) as a single string.
            args: Optional positional arguments supported by the concrete agent.
            kwargs: Optional keyword arguments supported by the concrete agent.

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


def _run_agent(agent: SyncAgent, agent_input: AgentInput) -> Any:
    """Run an agent with scenario context when the concrete implementation supports it.

    Args:
        agent: Agent exposing a synchronous ``run_sync`` method.
        agent_input: Validated alert text and optional scenario metadata.

    Returns:
        Raw response produced by the concrete agent.
    """
    parameters = inspect.signature(agent.run_sync).parameters
    if agent_input.scenario is not None and "scenario" in parameters:
        return agent.run_sync(agent_input.alert_text, scenario=agent_input.scenario)

    return agent.run_sync(agent_input.alert_text)


def _update_ace_playbook(
    *,
    results: list[AgentResult],
    playbook_path: Path | None,
    scenario_by_sample_id: dict[str, str] | None,
) -> None:
    """Update the ACE playbook from evaluated runner results.

    Imports are kept lazy so baseline runs do not import Agent A internals.

    Args:
        results: Evaluated agent outputs.
        playbook_path: Optional explicit playbook path. ``None`` uses ACE env/defaults.
        scenario_by_sample_id: Optional sample-to-scenario lookup.
    """
    from src.agents.agent_a_ace.loop import run_ace_learning_loop
    from src.agents.agent_a_ace.playbook_agent import resolve_playbook_path

    resolved_path = playbook_path if playbook_path is not None else resolve_playbook_path()
    run_ace_learning_loop(
        results,
        playbook_path=resolved_path,
        scenario_by_sample_id=scenario_by_sample_id,
    )


def _resolve_sample(loaded: Any, row: dict[str, Any], dataset_file: str) -> dict[str, Any]:
    """Return the single sample dict referenced by a split row.

    The dataset files produced by ``event-runner`` are **lists** of samples and
    each split row points to one of them via ``scenario_ref`` (or ``id``).
    Earlier fixtures used a single-sample-per-file layout (a plain dict), so
    this helper accepts both shapes for backward compatibility.

    Args:
        loaded: JSON payload already parsed from disk.
        row: Split row, used to find the right sample inside a list payload.
        dataset_file: File name, included in error messages for context.

    Returns:
        Sample mapping with ``id``, ``input``, ``expected_output``, ``golden_entities``.

    Raises:
        ValueError: If the payload type is unsupported or the referenced
            sample cannot be located inside a list payload.
    """
    if isinstance(loaded, dict):
        return loaded

    if not isinstance(loaded, list):
        raise ValueError(f"Dataset file {dataset_file!r} must contain a dict or a list of samples.")

    sample_id = row.get("scenario_ref") or row.get("id")
    if not sample_id:
        raise ValueError(
            f"Dataset file {dataset_file!r} is a list but split row has no "
            "'scenario_ref' or 'id' to look up the sample."
        )

    for candidate in loaded:
        if isinstance(candidate, dict) and candidate.get("id") == sample_id:
            return candidate

    raise ValueError(f"Sample id={sample_id!r} not found inside dataset file {dataset_file!r}.")


def _maybe_judge(
    rca_output: str,
    expected_output: str,
    sample_id: str,
    *,
    enabled: bool,
) -> JudgeVerdict | None:
    """Run the LLM judge for one sample, swallowing infra errors.

    The ticket explicitly forbids the judge from aborting a run, so any
    exception (timeouts, validation errors, network problems) is caught and
    logged, and the caller stores ``None``.

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
        # Broad except is intentional: the judge must never abort the run.
        logger.warning(
            "LLM judge failed for sample %s; storing judge_verdict=None.",
            sample_id,
            exc_info=True,
        )
        return None


def _maybe_cosine(
    rca_output: str,
    expected_output: str,
    golden_entities: list[str],
    sample_id: str,
    *,
    enabled: bool,
) -> CosineSimilarityResult | None:
    """Compute the embedding-based cosine similarity for one sample.

    Behaves like ``_maybe_judge``: any exception (model download problems,
    encoder failures) is logged and demoted to ``None`` so the run continues.
    The expensive sentence-transformer load happens lazily inside
    ``compute_cosine_scores``; the cached encoder is reused across samples.

    Args:
        rca_output: Agent-produced RCA text.
        expected_output: Ground-truth RCA from the dataset.
        golden_entities: Reference entities scored against ``rca_output``.
        sample_id: Identifier used only for log context.
        enabled: When ``False`` (``--no-cosine``) the scorer is skipped.

    Returns:
        ``CosineSimilarityResult`` on success, ``None`` when disabled or on failure.
    """
    if not enabled:
        return None

    try:
        return compute_cosine_scores(
            rca_output=rca_output,
            expected_output=expected_output,
            golden_entities=golden_entities,
        )
    except Exception:
        # Broad except is intentional: cosine scoring must never abort the run.
        logger.warning(
            "Cosine scorer failed for sample %s; storing cosine_similarity=None.",
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
    cosine_enabled: bool = True,
    ace_learning_enabled: bool = False,
    ace_playbook_path: Path | None = None,
) -> list[AgentResult]:
    """Execute an agent over **test** rows in a training split file.

    Only rows where ``split == "test"`` are evaluated. For each row, the
    companion dataset file named in ``file`` is loaded; ``input.alert_text``
    is sent to the agent, with scenario metadata forwarded when supported.

    Args:
        agent: Callable agent exposing ``run_sync``.
        training_split_path: Path to ``training_*.json`` under ``data/event_system``.
        output_path: JSON file to write (list of ``AgentResult`` dicts).
        dataset_dir: Directory containing dataset JSON files; if omitted,
            defaults to ``<parent of training_split_path>/data/datasets``.
        judge_enabled: When ``True`` (default) each evaluated sample is also
            scored by the LLM-as-a-judge. Pass ``False`` from the CLI via
            ``--no-judge`` to disable for fast or offline runs.
        cosine_enabled: When ``True`` (default) each evaluated sample is also
            scored with embedding-based cosine similarity (RCA vs expected
            output and per-golden-entity vs RCA). Pass ``False`` via
            ``--no-cosine`` to skip the sentence-transformer load.
        ace_learning_enabled: When ``True`` update the ACE playbook after
            writing evaluation results.
        ace_playbook_path: Optional playbook destination for ACE learning. If
            omitted, ACE resolves ``ACE_PLAYBOOK_PATH`` or ``tmp/playbook.json``.

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
    scenario_by_sample_id: dict[str, str] = {}
    for row in split_rows:
        # Train rows are skipped; the ticket evaluates generalization on test only.
        if row.get("split") != "test":
            continue

        dataset_file = row.get("file")
        if not dataset_file:
            raise ValueError("Each split row must include a 'file' field.")

        sample_path = resolved_dataset_dir / dataset_file
        sample = _resolve_sample(_load_json_file(sample_path), row, dataset_file)

        alert_text = sample.get("input", {}).get("alert_text")
        if not alert_text:
            raise ValueError(f"Sample {sample.get('id')} does not contain input.alert_text.")

        agent_input = AgentInput(
            alert_text=alert_text,
            scenario=row.get("base_scenario") or row.get("scenario_id"),
        )
        if agent_input.scenario is not None:
            scenario_by_sample_id[str(sample["id"])] = agent_input.scenario

        agent_response = _run_agent(agent, agent_input)

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

        cosine_similarity = _maybe_cosine(
            rca_output=rca_output,
            expected_output=expected_output,
            golden_entities=golden_entities,
            sample_id=str(sample.get("id", "")),
            enabled=cosine_enabled and bool(expected_output),
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
                cosine_similarity=cosine_similarity,
            )
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump([item.model_dump() for item in results], handle, indent=2, ensure_ascii=False)

    if ace_learning_enabled:
        _update_ace_playbook(
            results=results,
            playbook_path=ace_playbook_path,
            scenario_by_sample_id=scenario_by_sample_id or None,
        )

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
    parser.add_argument(
        "--no-judge",
        dest="judge_enabled",
        action="store_false",
        help="Disable the LLM-as-a-judge evaluator (enabled by default).",
    )
    parser.add_argument(
        "--no-cosine",
        dest="cosine_enabled",
        action="store_false",
        help="Disable the embedding-based cosine-similarity scorer (enabled by default).",
    )
    parser.add_argument(
        "--no-ace-learning",
        dest="ace_learning_disabled",
        action="store_true",
        help="Do not update the ACE playbook after Agent A evaluation.",
    )
    parser.set_defaults(judge_enabled=True, cosine_enabled=True)
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint: parse flags, load the agent, run the split, write JSON."""
    args = _parse_args()
    agent = _load_agent(args.agent)
    ace_learning_enabled = args.agent == "agent_a_ace" and not args.ace_learning_disabled
    run_agent_on_training_split(
        agent=agent,
        training_split_path=Path(args.training_file),
        output_path=Path(args.output_file),
        dataset_dir=Path(args.dataset_dir) if args.dataset_dir else None,
        judge_enabled=args.judge_enabled,
        cosine_enabled=args.cosine_enabled,
        ace_learning_enabled=ace_learning_enabled,
    )


if __name__ == "__main__":
    main()
