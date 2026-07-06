"""Runner utilities to execute agents against ``event_system`` training splits.

Expects a split file such as ``training_stratified.json`` and a parallel
``data/datasets/*.json`` tree with full samples. Split files can be the legacy
flat list shape or the DVC-managed shapes with top-level ``assignments`` or
named ``folds``. Real-agent runs can either use already-running MCP servers or
``--with-mock-mcp`` to let ``event_system.SplitRunner`` manage them per cache
source file; unit tests use a stub ``SyncAgent`` instead.

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
import sys
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol

from src.common.cosine_similarity import CosineSimilarityResult, compute_cosine_scores
from src.common.judge import JudgeVerdict, judge_rca
from src.common.schemas import AgentInput, AgentResult
from src.common.scoring import score_golden_entities

logger = logging.getLogger(__name__)


_SPLIT_FILE_TO_TYPE = {
    "training_stratified.json": "stratified",
    "training_leave_family_out.json": "leave_family_out",
    "training_leave_scenario_out.json": "leave_scenario_out",
    "training_leave_variation_out.json": "leave_variation_out",
}


def _infer_split_type(training_split_path: Path) -> str:
    """Infer the event_system split type from split metadata or file name."""
    payload = _load_json_file(training_split_path)
    if isinstance(payload, dict) and payload.get("split_type"):
        return str(payload["split_type"])

    try:
        return _SPLIT_FILE_TO_TYPE[training_split_path.name]
    except KeyError as exc:
        raise ValueError(
            "Managed mock MCP mode requires a DVC training split with a known "
            f"split_type; got {training_split_path}."
        ) from exc


def _ensure_event_system_on_path(data_dir: Path) -> None:
    """Allow importing the local event_system package from the root project."""
    data_dir_text = str(data_dir)
    if data_dir_text not in sys.path:
        sys.path.insert(0, data_dir_text)


def _build_split_runner(
    *,
    data_dir: Path,
    dataset_dir: Path,
    cache_dir: Path,
) -> Any:
    """Build the local event_system SplitRunner used for managed MCP mocks."""
    _ensure_event_system_on_path(data_dir)

    from event_system.case_provider import LocalCaseProvider
    from event_system.split_runner import SplitRunner

    provider = LocalCaseProvider(data_dir=data_dir)
    return SplitRunner(provider=provider, cache_dir=cache_dir, dataset_dir=dataset_dir)


class SyncAgent(Protocol):
    """Minimal synchronous agent interface consumed by this runner.

    Matches ``pydantic_ai.Agent.run_sync`` for typing without importing Agent here.
    """

    def run_sync(self, user_prompt: str, *args: Any, **kwargs: Any) -> Any:
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


def load_training_split_rows(
    training_split_path: Path,
    *,
    fold_name: str | None = None,
) -> list[dict[str, Any]]:
    """Load split rows from flat and DVC-managed training split files.

    Args:
        training_split_path: Path to the JSON split file.
        fold_name: Optional fold name required when the split contains several
            named folds.

    Returns:
        List of split assignment rows.

    Raises:
        FileNotFoundError: If the split file does not exist.
        ValueError: If the split payload shape is unsupported, the requested
            fold does not exist, or assignment rows are not mappings.
    """
    if not training_split_path.exists():
        raise FileNotFoundError(f"Training split file not found: {training_split_path}")

    payload = _load_json_file(training_split_path)
    return _resolve_split_rows(payload, fold_name=fold_name)


def _resolve_split_rows(
    payload: Any,
    *,
    fold_name: str | None = None,
) -> list[dict[str, Any]]:
    """Normalize supported split payloads to assignment rows."""
    if isinstance(payload, list):
        return _validate_split_rows(payload)

    if not isinstance(payload, dict):
        raise ValueError("Training split file must contain a JSON list or object.")

    assignments = payload.get("assignments")
    if assignments is not None:
        return _validate_split_rows(assignments)

    folds = payload.get("folds")
    if folds is not None:
        fold_payload = _select_fold(folds, fold_name=fold_name)
        return _resolve_fold_assignments(fold_payload)

    raise ValueError("Training split object must include 'assignments' or 'folds'.")


def _validate_split_rows(rows: Any) -> list[dict[str, Any]]:
    """Validate assignment row shape after loading a split."""
    if not isinstance(rows, list):
        raise ValueError("Training split assignments must be a JSON list.")

    invalid_indexes = [index for index, row in enumerate(rows) if not isinstance(row, dict)]
    if invalid_indexes:
        raise ValueError(
            "Training split assignments must contain only JSON objects; "
            f"invalid row indexes: {invalid_indexes}."
        )

    return rows


def _select_fold(folds: Any, *, fold_name: str | None) -> Any:
    """Select one fold payload from DVC split metadata."""
    if isinstance(folds, dict):
        return _select_fold_from_mapping(folds, fold_name=fold_name)

    if isinstance(folds, list):
        return _select_fold_from_list(folds, fold_name=fold_name)

    raise ValueError("Training split 'folds' must be a JSON object or list.")


def _select_fold_from_mapping(folds: dict[str, Any], *, fold_name: str | None) -> Any:
    """Select a fold from a mapping of fold names to payloads."""
    if fold_name is None:
        if len(folds) == 1:
            return next(iter(folds.values()))
        if "default" in folds:
            return folds["default"]
        available = ", ".join(sorted(str(name) for name in folds))
        raise ValueError(
            f"Training split contains multiple folds. Pass --fold with one of: {available}."
        )

    try:
        return folds[fold_name]
    except KeyError as exc:
        available = ", ".join(sorted(str(name) for name in folds))
        raise ValueError(f"Fold {fold_name!r} not found. Available folds: {available}.") from exc


def _select_fold_from_list(folds: list[Any], *, fold_name: str | None) -> Any:
    """Select a fold from a list of fold objects."""
    if not folds:
        raise ValueError("Training split contains an empty 'folds' list.")

    if fold_name is None:
        if len(folds) == 1:
            return folds[0]
        default_fold = _find_named_fold(folds, "default")
        if default_fold is not None:
            return default_fold
        available = ", ".join(_iter_fold_names(folds))
        raise ValueError(
            f"Training split contains multiple folds. Pass --fold with one of: {available}."
        )

    selected = _find_named_fold(folds, fold_name)
    if selected is not None:
        return selected

    available = ", ".join(_iter_fold_names(folds))
    raise ValueError(f"Fold {fold_name!r} not found. Available folds: {available}.")


def _find_named_fold(folds: list[Any], fold_name: str) -> Any | None:
    """Return the fold whose ``fold_name`` matches, if present."""
    for fold in folds:
        if isinstance(fold, dict) and fold.get("fold_name") == fold_name:
            return fold
    return None


def _iter_fold_names(folds: list[Any]) -> list[str]:
    """Return display names for available folds."""
    names: list[str] = []
    for index, fold in enumerate(folds):
        if isinstance(fold, dict) and fold.get("fold_name"):
            names.append(str(fold["fold_name"]))
        else:
            names.append(str(index))
    return names


def _resolve_fold_assignments(fold_payload: Any) -> list[dict[str, Any]]:
    """Return assignments from a selected fold payload."""
    if isinstance(fold_payload, list):
        return _validate_split_rows(fold_payload)

    if isinstance(fold_payload, dict):
        assignments = fold_payload.get("assignments")
        if assignments is None:
            raise ValueError("Selected fold must include an 'assignments' list.")
        return _validate_split_rows(assignments)

    raise ValueError("Selected fold must be a JSON object or assignment list.")


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
    """Run an agent, forwarding scenario only when the agent supports it."""
    signature = inspect.signature(agent.run_sync)
    parameters = signature.parameters.values()
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters
    )
    if accepts_kwargs or "scenario" in signature.parameters:
        return agent.run_sync(agent_input.alert_text, scenario=agent_input.scenario)
    return agent.run_sync(agent_input.alert_text)


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


def _build_agent_result(
    *,
    agent: SyncAgent,
    sample_id: str,
    alert_text: str,
    expected_output: str,
    golden_entities: list[str],
    scenario: str | None,
    judge_enabled: bool,
    cosine_enabled: bool,
) -> AgentResult:
    """Run one sample through the agent and score the output."""
    agent_input = AgentInput(alert_text=alert_text, scenario=scenario)
    agent_response = _run_agent(agent, agent_input)

    rca_output = _extract_agent_output(agent_response)
    scoring = score_golden_entities(rca_output, golden_entities)

    judge_verdict = _maybe_judge(
        rca_output=rca_output,
        expected_output=expected_output,
        sample_id=sample_id,
        enabled=judge_enabled and bool(expected_output),
    )

    cosine_similarity = _maybe_cosine(
        rca_output=rca_output,
        expected_output=expected_output,
        golden_entities=golden_entities,
        sample_id=sample_id,
        enabled=cosine_enabled and bool(expected_output),
    )

    return AgentResult(
        sample_id=sample_id,
        rca_output=rca_output,
        expected_output=expected_output,
        golden_entities=golden_entities,
        score=scoring["score"],
        matched_entities=scoring["matched"],
        judge_verdict=judge_verdict,
        cosine_similarity=cosine_similarity,
    )


def _write_results(output_path: Path, results: list[AgentResult]) -> None:
    """Persist runner results in the standard JSON format."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump([item.model_dump() for item in results], handle, indent=2, ensure_ascii=False)


def _run_agent_on_training_split_with_mock_mcp(
    agent: SyncAgent,
    training_split_path: Path,
    output_path: Path,
    dataset_dir: Path | None,
    cache_dir: Path | None,
    *,
    judge_enabled: bool,
    cosine_enabled: bool,
    partition: str,
    fold_name: str | None,
    max_cases: int | None,
) -> list[AgentResult]:
    """Execute an agent while SplitRunner manages per-source mock MCP servers."""
    load_training_split_rows(training_split_path, fold_name=fold_name)
    split_type = _infer_split_type(training_split_path)
    resolved_data_dir = training_split_path.parent
    resolved_dataset_dir = dataset_dir or resolved_data_dir / "data" / "datasets"
    resolved_cache_dir = cache_dir or resolved_data_dir / "cache"

    if not resolved_dataset_dir.exists():
        raise FileNotFoundError(f"Dataset directory not found: {resolved_dataset_dir}")
    if not resolved_cache_dir.exists():
        raise FileNotFoundError(f"Cache directory not found: {resolved_cache_dir}")

    runner = _build_split_runner(
        data_dir=resolved_data_dir,
        dataset_dir=resolved_dataset_dir,
        cache_dir=resolved_cache_dir,
    )
    resolved_fold_name = fold_name or "default"

    results: list[AgentResult] = []
    with closing(runner.iter_cases(split_type, resolved_fold_name, partition)) as run_cases:
        for run_case in run_cases:
            alert_text = run_case.input.get("alert_text")
            if not alert_text:
                raise ValueError(
                    f"Case {run_case.case.scenario_ref} does not contain input.alert_text."
                )

            if max_cases is not None and len(results) >= max_cases:
                break

            results.append(
                _build_agent_result(
                    agent=agent,
                    sample_id=str(run_case.case.scenario_ref),
                    alert_text=alert_text,
                    expected_output=run_case.expected_output,
                    golden_entities=run_case.golden_entities,
                    scenario=run_case.case.scenario_id,
                    judge_enabled=judge_enabled,
                    cosine_enabled=cosine_enabled,
                )
            )

    _write_results(output_path, results)
    return results


def run_agent_on_training_split(
    agent: SyncAgent,
    training_split_path: Path,
    output_path: Path,
    dataset_dir: Path | None = None,
    *,
    judge_enabled: bool = True,
    cosine_enabled: bool = True,
    partition: str = "test",
    fold_name: str | None = None,
    mock_mcp_enabled: bool = False,
    cache_dir: Path | None = None,
    max_cases: int | None = None,
) -> list[AgentResult]:
    """Execute an agent over one partition in a training split file.

    Only rows where ``split`` matches ``partition`` are evaluated. For each row, the
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
        cosine_enabled: When ``True`` (default) each evaluated sample is also
            scored with embedding-based cosine similarity (RCA vs expected
            output and per-golden-entity vs RCA). Pass ``False`` via
            ``--no-cosine`` to skip the sentence-transformer load.
        partition: Split partition to evaluate, such as ``train``, ``dev``, or ``test``.
        fold_name: Optional fold name for DVC split files with multiple folds.
        mock_mcp_enabled: When ``True``, use ``event_system.SplitRunner`` to
            start the mock MCP servers for each cache source file.
        cache_dir: Optional directory containing DVC mock MCP cache files.
        max_cases: Optional cap for short smoke tests.

    Returns:
        In-memory list of ``AgentResult`` instances (same order as iteration).

    Raises:
        FileNotFoundError: If the split file or dataset directory is missing.
        ValueError: If the split JSON shape is unsupported or rows lack required fields.
    """
    if mock_mcp_enabled:
        return _run_agent_on_training_split_with_mock_mcp(
            agent=agent,
            training_split_path=training_split_path,
            output_path=output_path,
            dataset_dir=dataset_dir,
            cache_dir=cache_dir,
            judge_enabled=judge_enabled,
            cosine_enabled=cosine_enabled,
            partition=partition,
            fold_name=fold_name,
            max_cases=max_cases,
        )

    split_rows = load_training_split_rows(training_split_path, fold_name=fold_name)

    resolved_dataset_dir = dataset_dir or training_split_path.parent / "data" / "datasets"
    if not resolved_dataset_dir.exists():
        raise FileNotFoundError(f"Dataset directory not found: {resolved_dataset_dir}")

    results: list[AgentResult] = []
    for row in split_rows:
        if row.get("split") != partition:
            continue
        if max_cases is not None and len(results) >= max_cases:
            break

        dataset_file = row.get("file")
        if not dataset_file:
            raise ValueError("Each split row must include a 'file' field.")

        sample_path = resolved_dataset_dir / dataset_file
        sample = _resolve_sample(_load_json_file(sample_path), row, dataset_file)

        alert_text = sample.get("input", {}).get("alert_text")
        if not alert_text:
            raise ValueError(f"Sample {sample.get('id')} does not contain input.alert_text.")

        results.append(
            _build_agent_result(
                agent=agent,
                sample_id=str(sample["id"]),
                alert_text=alert_text,
                expected_output=sample.get("expected_output", ""),
                golden_entities=sample.get("golden_entities", []),
                scenario=row.get("base_scenario") or row.get("scenario_id"),
                judge_enabled=judge_enabled,
                cosine_enabled=cosine_enabled,
            )
        )

    _write_results(output_path, results)
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
        "--partition",
        default="test",
        help="Split partition to evaluate (default: test).",
    )
    parser.add_argument(
        "--fold",
        default=None,
        help="Optional fold name for DVC split files that contain named folds.",
    )
    parser.add_argument(
        "--with-mock-mcp",
        dest="mock_mcp_enabled",
        action="store_true",
        help="Use event_system SplitRunner to start mock MCP servers per cache file.",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Optional path to event_system cache JSON files.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Optional maximum number of cases to evaluate.",
    )
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
    parser.set_defaults(judge_enabled=True, cosine_enabled=True)
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
        cosine_enabled=args.cosine_enabled,
        partition=args.partition,
        fold_name=args.fold,
        mock_mcp_enabled=args.mock_mcp_enabled,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        max_cases=args.max_cases,
    )


if __name__ == "__main__":
    main()
