"""Unit tests for ACE ablation experiment orchestration."""

from __future__ import annotations

import json
from pathlib import Path

from src.agents.agent_a_ace.ablations import AceAblationMode
from src.agents.agent_a_ace.experiments import run_ace_ablation_experiment


class StaticScenarioAgent:
    """Fake scenario-aware agent used by experiment runner tests."""

    def __init__(self, output: str) -> None:
        self.output = output
        self.calls: list[tuple[str, str | None]] = []

    def run_sync(self, user_prompt: str, *, scenario: str | None = None) -> str:
        """Return a fixed RCA while recording prompt and scenario."""
        self.calls.append((user_prompt, scenario))
        return self.output


def _write_json(path: Path, payload: object) -> None:
    """Write JSON test fixtures."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def _write_split_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Create a tiny train/test split for experiment runner tests."""
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_stratified.json"
    _write_json(
        training_file,
        [
            {
                "id": "sample-train",
                "file": "train_case.json",
                "split": "train",
                "base_scenario": "routing",
            },
            {
                "id": "sample-test",
                "file": "test_case.json",
                "split": "test",
                "base_scenario": "routing",
            },
        ],
    )
    sample = {
        "input": {"alert_text": "ALERT service unavailable"},
        "expected_output": "service targetPort container port connection refused",
        "golden_entities": ["service targetPort", "container port", "connection refused"],
    }
    _write_json(dataset_dir / "train_case.json", {"id": "sample-train", **sample})
    _write_json(dataset_dir / "test_case.json", {"id": "sample-test", **sample})
    return training_file, dataset_dir


def test_single_epoch_experiment_runs_one_warmup_and_writes_metadata(tmp_path: Path) -> None:
    """Single-epoch ablation should warm up once, evaluate, and write metadata."""
    training_file, dataset_dir = _write_split_fixture(tmp_path)
    output_root = tmp_path / "experiments"
    agent = StaticScenarioAgent("service targetPort container port connection refused")

    results = run_ace_ablation_experiment(
        AceAblationMode.SINGLE_EPOCH,
        training_split_path=training_file,
        output_root=output_root,
        dataset_dir=dataset_dir,
        judge_enabled=False,
        cosine_enabled=False,
        ace_agent_factory=lambda _path, _config: agent,
    )

    run_dir = output_root / "single_epoch"
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    playbook = json.loads((run_dir / "playbook.json").read_text(encoding="utf-8"))

    assert len(results) == 1
    assert (run_dir / "warmup_epoch_1.json").exists()
    assert metadata["learning_epochs"] == 1
    assert metadata["warmup_outputs"] == [str(run_dir / "warmup_epoch_1.json")]
    assert len(playbook["entries"]) == 1
    assert agent.calls[0] == ("ALERT service unavailable", "routing")


def test_online_no_warmup_experiment_evaluates_with_empty_playbook(tmp_path: Path) -> None:
    """Online-no-warmup ablation should skip offline playbook construction."""
    training_file, dataset_dir = _write_split_fixture(tmp_path)
    output_root = tmp_path / "experiments"
    agent = StaticScenarioAgent("service targetPort container port connection refused")

    results = run_ace_ablation_experiment(
        AceAblationMode.ONLINE_NO_WARMUP,
        training_split_path=training_file,
        output_root=output_root,
        dataset_dir=dataset_dir,
        judge_enabled=False,
        cosine_enabled=False,
        ace_agent_factory=lambda _path, _config: agent,
    )

    run_dir = output_root / "online_no_warmup"
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    playbook = json.loads((run_dir / "playbook.json").read_text(encoding="utf-8"))

    assert len(results) == 1
    assert metadata["warmup_enabled"] is False
    assert metadata["warmup_outputs"] == []
    assert playbook["entries"] == []
