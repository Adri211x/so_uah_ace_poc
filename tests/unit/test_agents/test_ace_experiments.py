"""Unit tests for ACE ablation experiment orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

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


class FakeSplitRunnerCase:
    """Tiny stand-in for event_system RunCase."""

    def __init__(self) -> None:
        self.case = type(
            "Case",
            (),
            {
                "scenario_ref": "sample-test",
                "scenario_id": "routing",
            },
        )()
        self.input = {"alert_text": "ALERT service unavailable"}
        self.expected_output = "service targetPort container port connection refused"
        self.golden_entities = ["service targetPort", "container port", "connection refused"]


class FakeSplitRunner:
    """Fake SplitRunner used by experiment tests."""

    def iter_cases(self, split_type: str, fold_name: str, split: str):
        _ = (split_type, fold_name, split)
        yield FakeSplitRunnerCase()


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


def test_experiment_selects_named_dvc_fold(tmp_path: Path) -> None:
    """Ablation experiments should use the requested DVC fold for warmup and eval."""
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_leave_scenario_out.json"
    output_root = tmp_path / "experiments"
    agent = StaticScenarioAgent("service targetPort container port connection refused")

    _write_json(
        training_file,
        {
            "split_type": "leave_scenario_out",
            "folds": [
                {
                    "fold_name": "hold_out_a",
                    "assignments": [
                        {
                            "id": "sample-a",
                            "file": "case_a.json",
                            "split": "test",
                            "base_scenario": "ignored",
                        }
                    ],
                },
                {
                    "fold_name": "hold_out_b",
                    "assignments": [
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
                },
            ],
        },
    )
    sample = {
        "input": {"alert_text": "ALERT selected"},
        "expected_output": "service targetPort container port connection refused",
        "golden_entities": ["service targetPort", "container port", "connection refused"],
    }
    _write_json(dataset_dir / "train_case.json", {"id": "sample-train", **sample})
    _write_json(dataset_dir / "test_case.json", {"id": "sample-test", **sample})

    results = run_ace_ablation_experiment(
        AceAblationMode.SINGLE_EPOCH,
        training_split_path=training_file,
        output_root=output_root,
        dataset_dir=dataset_dir,
        judge_enabled=False,
        cosine_enabled=False,
        fold_name="hold_out_b",
        ace_agent_factory=lambda _path, _config: agent,
    )

    metadata = json.loads(
        (output_root / "single_epoch" / "metadata.json").read_text(encoding="utf-8")
    )

    assert len(results) == 1
    assert metadata["fold_name"] == "hold_out_b"
    assert agent.calls == [
        ("ALERT selected", "routing"),
        ("ALERT selected", "routing"),
    ]


def test_experiment_passes_mock_mcp_options_to_runner(tmp_path: Path) -> None:
    """Ablation experiments should pass managed mock MCP options to runner calls."""
    training_file, dataset_dir = _write_split_fixture(tmp_path)
    output_root = tmp_path / "experiments"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    agent = StaticScenarioAgent("service targetPort container port connection refused")

    with patch("src.common.runner._build_split_runner", return_value=FakeSplitRunner()):
        results = run_ace_ablation_experiment(
            AceAblationMode.ONLINE_NO_WARMUP,
            training_split_path=training_file,
            output_root=output_root,
            dataset_dir=dataset_dir,
            cache_dir=cache_dir,
            mock_mcp_enabled=True,
            judge_enabled=False,
            cosine_enabled=False,
            ace_agent_factory=lambda _path, _config: agent,
        )

    metadata = json.loads(
        (output_root / "online_no_warmup" / "metadata.json").read_text(encoding="utf-8")
    )

    assert len(results) == 1
    assert metadata["mock_mcp_enabled"] is True
    assert metadata["cache_dir"] == str(cache_dir)
