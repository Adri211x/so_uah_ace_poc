"""Unit tests for ``src.common.runner`` (split iteration and JSON output)."""

from __future__ import annotations

import json
from pathlib import Path

from src.common.runner import run_agent_on_training_split


class FakeAgent:
    """Minimal stub matching ``SyncAgent`` without calling an LLM."""

    def run_sync(self, user_prompt: str) -> str:
        return f"RCA for: {user_prompt}"


def _write_json(path: Path, payload: object) -> None:
    """Write ``payload`` as UTF-8 JSON, creating parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def test_runner_processes_only_test_split_rows(tmp_path: Path) -> None:
    """Runner must ignore train rows and persist one result for a single test row."""
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_stratified.json"
    output_file = tmp_path / "results" / "agent_a.json"

    _write_json(
        training_file,
        [
            {"id": "sample-train", "file": "train_case.json", "split": "train"},
            {
                "id": "sample-test",
                "file": "test_case.json",
                "split": "test",
                "base_scenario": "crashloop",
            },
        ],
    )

    _write_json(
        dataset_dir / "train_case.json",
        {
            "id": "sample-train",
            "input": {"alert_text": "ALERT train"},
            "expected_output": "ignored",
            "golden_entities": ["a", "b", "c"],
        },
    )
    _write_json(
        dataset_dir / "test_case.json",
        {
            "id": "sample-test",
            "input": {"alert_text": "ALERT test"},
            "expected_output": "Root cause expected.",
            "golden_entities": ["x", "y", "z"],
        },
    )

    results = run_agent_on_training_split(
        agent=FakeAgent(),
        training_split_path=training_file,
        output_path=output_file,
        dataset_dir=dataset_dir,
    )

    assert len(results) == 1
    assert results[0].sample_id == "sample-test"
    assert "ALERT test" in results[0].rca_output
    assert output_file.exists()
