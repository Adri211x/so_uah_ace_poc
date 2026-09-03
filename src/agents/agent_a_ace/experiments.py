"""Run reproducible ACE ablation experiments."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Callable
from pathlib import Path

from src.agents.agent_a_ace.ablations import (
    DEFAULT_EXPERIMENT_ROOT,
    DEFAULT_LEARNING_EPOCHS,
    AceAblationMode,
    AceExperimentConfig,
    build_experiment_config,
    parse_ablation_mode,
)
from src.agents.agent_a_ace.curator import JsonPlaybookCurator
from src.agents.agent_a_ace.loop import update_playbook_from_results
from src.common.runner import (
    SyncAgent,
    _load_json_file,
    _resolve_sample,
    load_training_split_rows,
    run_agent_on_training_split,
)
from src.common.schemas import AgentResult, Playbook

logger = logging.getLogger(__name__)

_ALL_MODES = tuple(mode for mode in AceAblationMode)
AceAgentFactory = Callable[[Path, AceExperimentConfig], SyncAgent]
BaselineAgentFactory = Callable[[], SyncAgent]


def run_ace_ablation_experiment(
    mode: str | AceAblationMode,
    *,
    training_split_path: Path,
    output_root: Path = DEFAULT_EXPERIMENT_ROOT,
    dataset_dir: Path | None = None,
    warmup_partition: str = "train",
    eval_partition: str = "test",
    fold_name: str | None = None,
    mock_mcp_enabled: bool = False,
    cache_dir: Path | None = None,
    max_cases: int | None = None,
    default_epochs: int = DEFAULT_LEARNING_EPOCHS,
    judge_enabled: bool = True,
    cosine_enabled: bool = True,
    reset_playbook: bool = True,
    ace_agent_factory: AceAgentFactory | None = None,
    baseline_agent_factory: BaselineAgentFactory | None = None,
) -> list[AgentResult]:
    """Run one ACE ablation variant end to end.

    Args:
        mode: Ablation variant to run.
        training_split_path: Split file with train/dev/test rows.
        output_root: Root folder for per-mode outputs.
        dataset_dir: Optional dataset directory override.
        warmup_partition: Partition used to build the playbook before eval.
        eval_partition: Partition evaluated after warmup.
        fold_name: Optional fold name for DVC split files with multiple folds.
        mock_mcp_enabled: Whether to start mock MCP servers with SplitRunner.
        cache_dir: Optional directory containing mock MCP cache files.
        max_cases: Optional cap for short smoke tests.
        default_epochs: Multi-epoch count for modes that use the full loop.
        judge_enabled: Whether to run the LLM judge.
        cosine_enabled: Whether to run cosine scoring.
        reset_playbook: Whether to start this mode from an empty playbook.
        ace_agent_factory: Optional test seam for Agent A construction.
        baseline_agent_factory: Optional test seam for Agent B construction.

    Returns:
        Evaluation results for the selected ``eval_partition``.
    """
    config = build_experiment_config(mode, default_epochs=default_epochs)
    run_dir = output_root / config.mode.value
    run_dir.mkdir(parents=True, exist_ok=True)

    playbook_path = run_dir / "playbook.json"
    result_output_path = run_dir / "results.json"
    warmup_outputs: list[Path] = []

    if config.uses_ace and reset_playbook:
        JsonPlaybookCurator(playbook_path).save(Playbook())

    if config.uses_ace:
        agent = _build_ace_agent(playbook_path, config, ace_agent_factory)
        scenario_by_sample_id = _build_scenario_lookup(
            training_split_path=training_split_path,
            dataset_dir=dataset_dir,
            partition=warmup_partition,
            fold_name=fold_name,
        )
        _run_warmup_epochs(
            agent=agent,
            config=config,
            training_split_path=training_split_path,
            dataset_dir=dataset_dir,
            playbook_path=playbook_path,
            run_dir=run_dir,
            warmup_partition=warmup_partition,
            fold_name=fold_name,
            mock_mcp_enabled=mock_mcp_enabled,
            cache_dir=cache_dir,
            max_cases=max_cases,
            scenario_by_sample_id=scenario_by_sample_id,
            judge_enabled=judge_enabled,
            cosine_enabled=cosine_enabled,
            warmup_outputs=warmup_outputs,
        )
    else:
        agent = _build_baseline_agent(baseline_agent_factory)

    results = run_agent_on_training_split(
        agent=agent,
        training_split_path=training_split_path,
        output_path=result_output_path,
        dataset_dir=dataset_dir,
        judge_enabled=judge_enabled,
        cosine_enabled=cosine_enabled,
        partition=eval_partition,
        fold_name=fold_name,
        mock_mcp_enabled=mock_mcp_enabled,
        cache_dir=cache_dir,
        max_cases=max_cases,
    )
    _write_metadata(
        config=config,
        run_dir=run_dir,
        playbook_path=playbook_path if config.uses_ace else None,
        result_output_path=result_output_path,
        warmup_outputs=warmup_outputs,
        warmup_partition=warmup_partition,
        eval_partition=eval_partition,
        fold_name=fold_name,
        mock_mcp_enabled=mock_mcp_enabled,
        cache_dir=cache_dir,
        max_cases=max_cases,
        result_count=len(results),
    )
    logger.info(
        "Completed ACE ablation mode %s with %d evaluation results.",
        config.mode.value,
        len(results),
    )
    return results


def run_all_ace_ablation_experiments(
    *,
    training_split_path: Path,
    output_root: Path = DEFAULT_EXPERIMENT_ROOT,
    dataset_dir: Path | None = None,
    warmup_partition: str = "train",
    eval_partition: str = "test",
    fold_name: str | None = None,
    mock_mcp_enabled: bool = False,
    cache_dir: Path | None = None,
    max_cases: int | None = None,
    default_epochs: int = DEFAULT_LEARNING_EPOCHS,
    judge_enabled: bool = True,
    cosine_enabled: bool = True,
) -> dict[AceAblationMode, list[AgentResult]]:
    """Run all supported ACE ablation variants."""
    results_by_mode: dict[AceAblationMode, list[AgentResult]] = {}
    for mode in _ALL_MODES:
        results_by_mode[mode] = run_ace_ablation_experiment(
            mode,
            training_split_path=training_split_path,
            output_root=output_root,
            dataset_dir=dataset_dir,
            warmup_partition=warmup_partition,
            eval_partition=eval_partition,
            fold_name=fold_name,
            mock_mcp_enabled=mock_mcp_enabled,
            cache_dir=cache_dir,
            max_cases=max_cases,
            default_epochs=default_epochs,
            judge_enabled=judge_enabled,
            cosine_enabled=cosine_enabled,
        )
    return results_by_mode


def _build_ace_agent(
    playbook_path: Path,
    config: AceExperimentConfig,
    ace_agent_factory: AceAgentFactory | None,
) -> SyncAgent:
    """Build Agent A for an ablation run."""
    if ace_agent_factory is not None:
        return ace_agent_factory(playbook_path, config)

    from src.agents.agent_a_ace.agent import build_agent

    return build_agent(playbook_path=playbook_path, playbook_scope=config.playbook_scope)


def _build_baseline_agent(baseline_agent_factory: BaselineAgentFactory | None) -> SyncAgent:
    """Build Agent B for the baseline run."""
    if baseline_agent_factory is not None:
        return baseline_agent_factory()

    from src.agents.agent_b_baseline.agent import get_agent

    return get_agent()


def _run_warmup_epochs(
    *,
    agent: SyncAgent,
    config: AceExperimentConfig,
    training_split_path: Path,
    dataset_dir: Path | None,
    playbook_path: Path,
    run_dir: Path,
    warmup_partition: str,
    fold_name: str | None,
    mock_mcp_enabled: bool,
    cache_dir: Path | None,
    max_cases: int | None,
    scenario_by_sample_id: dict[str, str],
    judge_enabled: bool,
    cosine_enabled: bool,
    warmup_outputs: list[Path],
) -> None:
    """Run configured warmup epochs and update the ACE playbook."""
    if not config.warmup_enabled:
        return

    for epoch in range(1, config.learning_epochs + 1):
        output_path = run_dir / f"warmup_epoch_{epoch}.json"
        warmup_results = run_agent_on_training_split(
            agent=agent,
            training_split_path=training_split_path,
            output_path=output_path,
            dataset_dir=dataset_dir,
            judge_enabled=judge_enabled,
            cosine_enabled=cosine_enabled,
            partition=warmup_partition,
            fold_name=fold_name,
            mock_mcp_enabled=mock_mcp_enabled,
            cache_dir=cache_dir,
            max_cases=max_cases,
        )
        update_playbook_from_results(
            warmup_results,
            playbook_path=playbook_path,
            scenario_by_sample_id=scenario_by_sample_id,
            reflection_outcomes=config.reflection_outcomes,
        )
        warmup_outputs.append(output_path)


def _build_scenario_lookup(
    *,
    training_split_path: Path,
    dataset_dir: Path | None,
    partition: str,
    fold_name: str | None,
) -> dict[str, str]:
    """Build sample id to scenario lookup for one split partition."""
    split_rows = load_training_split_rows(training_split_path, fold_name=fold_name)

    resolved_dataset_dir = dataset_dir or training_split_path.parent / "data" / "datasets"
    scenario_by_sample_id: dict[str, str] = {}
    for row in split_rows:
        if row.get("split") != partition:
            continue

        dataset_file = row.get("file")
        scenario = row.get("base_scenario") or row.get("scenario_id")
        if not dataset_file or not scenario:
            continue

        sample_path = resolved_dataset_dir / dataset_file
        sample = _resolve_sample(_load_json_file(sample_path), row, dataset_file)
        scenario_by_sample_id[str(sample["id"])] = str(scenario)
    return scenario_by_sample_id


def _write_metadata(
    *,
    config: AceExperimentConfig,
    run_dir: Path,
    playbook_path: Path | None,
    result_output_path: Path,
    warmup_outputs: list[Path],
    warmup_partition: str,
    eval_partition: str,
    fold_name: str | None,
    mock_mcp_enabled: bool,
    cache_dir: Path | None,
    max_cases: int | None,
    result_count: int,
) -> None:
    """Persist a small metadata file next to experiment outputs."""
    metadata = {
        "mode": config.mode.value,
        "agent_name": config.agent_name,
        "warmup_enabled": config.warmup_enabled,
        "learning_epochs": config.learning_epochs,
        "reflection_outcomes": sorted(config.reflection_outcomes),
        "playbook_scope": config.playbook_scope,
        "warmup_partition": warmup_partition,
        "eval_partition": eval_partition,
        "fold_name": fold_name,
        "mock_mcp_enabled": mock_mcp_enabled,
        "cache_dir": str(cache_dir) if cache_dir is not None else None,
        "max_cases": max_cases,
        "playbook_path": str(playbook_path) if playbook_path is not None else None,
        "warmup_outputs": [str(path) for path in warmup_outputs],
        "result_output_path": str(result_output_path),
        "result_count": result_count,
    }
    (run_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _parse_args() -> argparse.Namespace:
    """Build CLI parser for ACE ablation experiments."""
    parser = argparse.ArgumentParser(description="Run ACE ablation experiments.")
    parser.add_argument(
        "--mode", required=True, choices=[*["all"], *(mode.value for mode in _ALL_MODES)]
    )
    parser.add_argument("--training-file", required=True, help="Path to training split JSON file.")
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
        help="Optional maximum number of cases per runner call.",
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_EXPERIMENT_ROOT),
        help="Directory where per-mode experiment outputs are written.",
    )
    parser.add_argument(
        "--dataset-dir",
        default=None,
        help="Optional path to dataset JSON files.",
    )
    parser.add_argument(
        "--warmup-partition",
        default="train",
        help="Split partition used to build ACE playbooks.",
    )
    parser.add_argument(
        "--eval-partition",
        default="test",
        help="Split partition used for final evaluation.",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=DEFAULT_LEARNING_EPOCHS,
        help="Default learning epochs for full multi-epoch variants.",
    )
    parser.add_argument(
        "--no-judge",
        dest="judge_enabled",
        action="store_false",
        help="Disable the LLM-as-a-judge evaluator.",
    )
    parser.add_argument(
        "--no-cosine",
        dest="cosine_enabled",
        action="store_false",
        help="Disable the embedding-based cosine-similarity scorer.",
    )
    parser.set_defaults(judge_enabled=True, cosine_enabled=True)
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint for ACE ablation experiments."""
    logging.basicConfig(level=logging.INFO)
    args = _parse_args()
    common_kwargs = {
        "training_split_path": Path(args.training_file),
        "output_root": Path(args.output_root),
        "dataset_dir": Path(args.dataset_dir) if args.dataset_dir else None,
        "warmup_partition": args.warmup_partition,
        "eval_partition": args.eval_partition,
        "fold_name": args.fold,
        "mock_mcp_enabled": args.mock_mcp_enabled,
        "cache_dir": Path(args.cache_dir) if args.cache_dir else None,
        "max_cases": args.max_cases,
        "default_epochs": args.epochs,
        "judge_enabled": args.judge_enabled,
        "cosine_enabled": args.cosine_enabled,
    }
    if args.mode == "all":
        run_all_ace_ablation_experiments(**common_kwargs)
        return

    run_ace_ablation_experiment(parse_ablation_mode(args.mode), **common_kwargs)


if __name__ == "__main__":
    main()
