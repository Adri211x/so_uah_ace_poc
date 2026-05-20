"""Unit tests for ``src.common.runner`` (split iteration and JSON output)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.common.judge import JudgeVerdict
from src.common.runner import run_agent_on_training_split


class FakeAgent:
    """Minimal stub matching ``SyncAgent`` without calling an LLM."""

    def run_sync(self, user_prompt: str) -> str:
        return f"RCA for: {user_prompt}"


class ScriptedAgent:
    """Stub agent that returns a canned response keyed by the alert text."""

    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses

    def run_sync(self, user_prompt: str) -> str:
        return self._responses[user_prompt]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def test_runner_processes_only_test_split_rows(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_stratified.json"
    output_file = tmp_path / "results" / "agent_a.json"

    _write_json(
        training_file,
        [
            {"id": "sample-train", "file": "train_case.json", "split": "train"},
            {"id": "sample-test", "file": "test_case.json", "split": "test"},
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
        judge_enabled=False,
    )

    assert len(results) == 1
    assert results[0].sample_id == "sample-test"
    assert results[0].judge_verdict is None
    assert output_file.exists()


def test_runner_writes_score_and_matched_entities_for_three_cases(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_stratified.json"
    output_file = tmp_path / "results" / "agent_a.json"

    split_rows = [
        {"id": f"sample-{i}", "file": f"case_{i}.json", "split": "test"} for i in range(1, 4)
    ]
    _write_json(training_file, split_rows)

    samples = {
        1: {
            "id": "sample-1",
            "input": {"alert_text": "ALERT 1"},
            "expected_output": "Root cause one.",
            "golden_entities": ["nginx-deploy", "unterminated quoted string", "exit code 1"],
        },
        2: {
            "id": "sample-2",
            "input": {"alert_text": "ALERT 2"},
            "expected_output": "Root cause two.",
            "golden_entities": ["postgres-db", "connection refused", "port 5432"],
        },
        3: {
            "id": "sample-3",
            "input": {"alert_text": "ALERT 3"},
            "expected_output": "Root cause three.",
            "golden_entities": ["redis-cache", "OOMKilled", "memory limit"],
        },
    }
    for index, sample in samples.items():
        _write_json(dataset_dir / f"case_{index}.json", sample)

    responses = {
        "ALERT 1": (
            "The pod nginx-deploy crashed with exit code 1 because of an "
            "unterminated quoted string in the entrypoint."
        ),
        "ALERT 2": "postgres-db rejected connections (connection refused) on port 5432.",
        "ALERT 3": "redis-cache went OOMKilled after exceeding the configured limit.",
    }

    results = run_agent_on_training_split(
        agent=ScriptedAgent(responses),
        training_split_path=training_file,
        output_path=output_file,
        dataset_dir=dataset_dir,
        judge_enabled=False,
    )

    assert len(results) == 3
    assert results[0].score == 3
    assert results[1].score == 3
    assert results[2].score == 2


def test_runner_attaches_judge_verdict_when_enabled(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_stratified.json"
    output_file = tmp_path / "results" / "agent_a.json"

    _write_json(
        training_file,
        [{"id": "sample-test", "file": "test_case.json", "split": "test"}],
    )
    _write_json(
        dataset_dir / "test_case.json",
        {
            "id": "sample-test",
            "input": {"alert_text": "ALERT test"},
            "expected_output": "config_error: targetPort mismatch.",
            "golden_entities": ["config_error"],
        },
    )

    canned = JudgeVerdict(
        root_cause_match=0.8,
        evidence_quality=0.7,
        completeness=0.9,
        overall=0.8,
        reasoning="ok",
        judge_model="gpt-4o-mini",
    )

    with patch("src.common.runner.judge_rca", return_value=canned) as mocked_judge:
        results = run_agent_on_training_split(
            agent=FakeAgent(),
            training_split_path=training_file,
            output_path=output_file,
            dataset_dir=dataset_dir,
            judge_enabled=True,
        )

    mocked_judge.assert_called_once()
    assert results[0].judge_verdict == canned


def test_runner_limit_caps_evaluated_samples(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_stratified.json"
    output_file = tmp_path / "results" / "agent_a.json"

    rows = [{"id": "train-only", "file": "train.json", "split": "train"}]
    rows.extend(
        {"id": f"sample-{i}", "file": f"case_{i}.json", "split": "test"} for i in range(1, 6)
    )
    _write_json(training_file, rows)

    _write_json(
        dataset_dir / "train.json",
        {
            "id": "train-only",
            "input": {"alert_text": "ALERT train"},
            "expected_output": "ignored",
            "golden_entities": [],
        },
    )
    for index in range(1, 6):
        _write_json(
            dataset_dir / f"case_{index}.json",
            {
                "id": f"sample-{index}",
                "input": {"alert_text": f"ALERT {index}"},
                "expected_output": f"ground truth {index}",
                "golden_entities": [],
            },
        )

    results = run_agent_on_training_split(
        agent=FakeAgent(),
        training_split_path=training_file,
        output_path=output_file,
        dataset_dir=dataset_dir,
        judge_enabled=False,
        limit=3,
    )

    assert len(results) == 3
    assert [r.sample_id for r in results] == ["sample-1", "sample-2", "sample-3"]


def test_runner_limit_must_be_positive(tmp_path: Path) -> None:
    training_file = tmp_path / "training_stratified.json"
    output_file = tmp_path / "results" / "agent_a.json"
    _write_json(training_file, [])

    with pytest.raises(ValueError, match="limit"):
        run_agent_on_training_split(
            agent=FakeAgent(),
            training_split_path=training_file,
            output_path=output_file,
            dataset_dir=tmp_path,
            judge_enabled=False,
            limit=0,
        )


def test_runner_keeps_running_when_judge_raises(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_stratified.json"
    output_file = tmp_path / "results" / "agent_a.json"

    _write_json(
        training_file,
        [{"id": "sample-test", "file": "test_case.json", "split": "test"}],
    )
    _write_json(
        dataset_dir / "test_case.json",
        {
            "id": "sample-test",
            "input": {"alert_text": "ALERT test"},
            "expected_output": "ground truth",
            "golden_entities": [],
        },
    )

    with patch("src.common.runner.judge_rca", side_effect=RuntimeError("LiteLLM is down")):
        results = run_agent_on_training_split(
            agent=FakeAgent(),
            training_split_path=training_file,
            output_path=output_file,
            dataset_dir=dataset_dir,
            judge_enabled=True,
        )

    assert len(results) == 1
    assert results[0].judge_verdict is None
