"""Unit tests for ``src.common.runner`` (split iteration and JSON output)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

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
        judge_enabled=False,
    )

    assert len(results) == 1
    assert results[0].sample_id == "sample-test"
    assert "ALERT test" in results[0].rca_output
    assert results[0].judge_verdict is None
    assert output_file.exists()


def test_runner_writes_score_and_matched_entities_for_three_cases(tmp_path: Path) -> None:
    """E2E check: runner must score every test row and persist numeric scores in JSON."""
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
    agent = ScriptedAgent(responses)

    results = run_agent_on_training_split(
        agent=agent,
        training_split_path=training_file,
        output_path=output_file,
        dataset_dir=dataset_dir,
        judge_enabled=False,
    )

    assert len(results) == 3
    assert all(isinstance(result.score, int) for result in results)
    assert results[0].score == 3
    assert results[1].score == 3
    assert results[2].score == 2
    assert "memory limit" not in (results[2].matched_entities or [])

    persisted = json.loads(output_file.read_text(encoding="utf-8"))
    assert [entry["score"] for entry in persisted] == [3, 3, 2]
    for entry in persisted:
        assert isinstance(entry["score"], int)
        assert isinstance(entry["matched_entities"], list)
        assert entry["judge_verdict"] is None


def test_runner_attaches_judge_verdict_when_enabled(tmp_path: Path) -> None:
    """With the judge on, every result must carry the JudgeVerdict produced by the patched judge."""
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

    persisted = json.loads(output_file.read_text(encoding="utf-8"))
    assert persisted[0]["judge_verdict"]["overall"] == 0.8
    assert persisted[0]["judge_verdict"]["judge_model"] == "gpt-4o-mini"


def test_runner_keeps_running_when_judge_raises(tmp_path: Path) -> None:
    """A failing judge must demote ``judge_verdict`` to ``None`` without aborting the run."""
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
    assert output_file.exists()
