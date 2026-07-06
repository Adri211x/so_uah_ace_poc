"""Unit tests for ``src.common.runner`` (split iteration and JSON output)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.common.cosine_similarity import CosineSimilarityResult
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
        cosine_enabled=False,
    )

    assert len(results) == 1
    assert results[0].sample_id == "sample-test"
    assert "ALERT test" in results[0].rca_output
    assert results[0].judge_verdict is None
    assert results[0].cosine_similarity is None
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
        cosine_enabled=False,
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


def test_runner_resolves_sample_from_list_dataset(tmp_path: Path) -> None:
    """Real datasets are lists of samples; runner must pick the one referenced by scenario_ref."""
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_stratified.json"
    output_file = tmp_path / "results" / "agent_a.json"

    _write_json(
        training_file,
        [
            {
                "scenario_ref": "kubernetes-data-pipeline-KubePodCrashLooping",
                "scenario_id": "kubernetes-data-pipeline",
                "file": "kubernetes-data-pipeline.json",
                "split": "test",
            }
        ],
    )
    _write_json(
        dataset_dir / "kubernetes-data-pipeline.json",
        [
            {
                "id": "kubernetes-data-pipeline-KubeDeploymentReplicasMismatch",
                "input": {"alert_text": "ALERT replicas"},
                "expected_output": "wrong sample",
                "golden_entities": ["should not match"],
            },
            {
                "id": "kubernetes-data-pipeline-KubePodCrashLooping",
                "input": {"alert_text": "ALERT crash"},
                "expected_output": "right sample",
                "golden_entities": ["broker-service"],
            },
        ],
    )

    results = run_agent_on_training_split(
        agent=FakeAgent(),
        training_split_path=training_file,
        output_path=output_file,
        dataset_dir=dataset_dir,
        judge_enabled=False,
        cosine_enabled=False,
    )

    assert len(results) == 1
    assert results[0].sample_id == "kubernetes-data-pipeline-KubePodCrashLooping"
    assert results[0].expected_output == "right sample"
    assert "ALERT crash" in results[0].rca_output


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
            cosine_enabled=False,
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
            cosine_enabled=False,
        )

    assert len(results) == 1
    assert results[0].judge_verdict is None
    assert output_file.exists()


def test_runner_attaches_cosine_similarity_when_enabled(tmp_path: Path) -> None:
    """With cosine enabled, every result must carry the structured similarity payload."""
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
            "expected_output": "broker_queue_full",
            "golden_entities": ["broker", "queue"],
        },
    )

    canned = CosineSimilarityResult(
        rca_similarity=0.81,
        golden_entity_similarities={"broker": 0.7, "queue": 0.6},
        golden_entities_avg=0.65,
        golden_entities_max=0.7,
    )

    with patch("src.common.runner.compute_cosine_scores", return_value=canned) as mocked:
        results = run_agent_on_training_split(
            agent=FakeAgent(),
            training_split_path=training_file,
            output_path=output_file,
            dataset_dir=dataset_dir,
            judge_enabled=False,
            cosine_enabled=True,
        )

    mocked.assert_called_once()
    assert results[0].cosine_similarity == canned

    persisted = json.loads(output_file.read_text(encoding="utf-8"))
    assert persisted[0]["cosine_similarity"]["rca_similarity"] == 0.81
    assert persisted[0]["cosine_similarity"]["golden_entities_avg"] == 0.65
    assert "embedding_model" not in persisted[0]["cosine_similarity"]


def test_runner_keeps_running_when_cosine_raises(tmp_path: Path) -> None:
    """A failing cosine scorer must demote ``cosine_similarity`` to ``None``."""
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

    with patch(
        "src.common.runner.compute_cosine_scores",
        side_effect=RuntimeError("encoder unavailable"),
    ):
        results = run_agent_on_training_split(
            agent=FakeAgent(),
            training_split_path=training_file,
            output_path=output_file,
            dataset_dir=dataset_dir,
            judge_enabled=False,
            cosine_enabled=True,
        )

    assert len(results) == 1
    assert results[0].cosine_similarity is None
    assert output_file.exists()


class ScenarioRecordingAgent:
    """Stub that records scenario-aware runner calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def run_sync(self, user_prompt: str, *, scenario: str | None = None) -> str:
        self.calls.append((user_prompt, scenario))
        return "RCA includes broker-service evidence."


def test_runner_forwards_scenario_to_agents_that_accept_it(tmp_path: Path) -> None:
    """Scenario-aware agents should receive the split row scenario."""
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_stratified.json"
    output_file = tmp_path / "results" / "agent_a.json"

    _write_json(
        training_file,
        [
            {
                "id": "sample-test",
                "file": "test_case.json",
                "split": "test",
                "base_scenario": "kubernetes-data-pipeline",
            }
        ],
    )
    _write_json(
        dataset_dir / "test_case.json",
        {
            "id": "sample-test",
            "input": {"alert_text": "ALERT test"},
            "expected_output": "broker-service evidence",
            "golden_entities": ["broker-service"],
        },
    )

    agent = ScenarioRecordingAgent()
    results = run_agent_on_training_split(
        agent=agent,
        training_split_path=training_file,
        output_path=output_file,
        dataset_dir=dataset_dir,
        judge_enabled=False,
        cosine_enabled=False,
    )

    assert len(results) == 1
    assert agent.calls == [("ALERT test", "kubernetes-data-pipeline")]


def test_runner_can_evaluate_non_test_partition(tmp_path: Path) -> None:
    """Experiment warmup should be able to run over train rows."""
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_stratified.json"
    output_file = tmp_path / "results" / "train_results.json"

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
            "expected_output": "train evidence",
            "golden_entities": ["train"],
        },
    )
    _write_json(
        dataset_dir / "test_case.json",
        {
            "id": "sample-test",
            "input": {"alert_text": "ALERT test"},
            "expected_output": "test evidence",
            "golden_entities": ["test"],
        },
    )

    results = run_agent_on_training_split(
        agent=FakeAgent(),
        training_split_path=training_file,
        output_path=output_file,
        dataset_dir=dataset_dir,
        judge_enabled=False,
        cosine_enabled=False,
        partition="train",
    )

    assert len(results) == 1
    assert results[0].sample_id == "sample-train"
    assert "ALERT train" in results[0].rca_output


def test_runner_accepts_dvc_assignments_object(tmp_path: Path) -> None:
    """Runner should consume stratified DVC splits without exporting a flat file."""
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_stratified.json"
    output_file = tmp_path / "results" / "agent_a.json"

    _write_json(
        training_file,
        {
            "split_type": "stratified",
            "assignments": [
                {"id": "sample-train", "file": "train_case.json", "split": "train"},
                {
                    "id": "sample-test",
                    "file": "test_case.json",
                    "split": "test",
                    "base_scenario": "routing",
                },
            ],
        },
    )
    _write_json(
        dataset_dir / "test_case.json",
        {
            "id": "sample-test",
            "input": {"alert_text": "ALERT dvc"},
            "expected_output": "routing evidence",
            "golden_entities": ["routing"],
        },
    )

    results = run_agent_on_training_split(
        agent=FakeAgent(),
        training_split_path=training_file,
        output_path=output_file,
        dataset_dir=dataset_dir,
        judge_enabled=False,
        cosine_enabled=False,
    )

    assert len(results) == 1
    assert results[0].sample_id == "sample-test"
    assert "ALERT dvc" in results[0].rca_output


def test_runner_selects_named_dvc_fold(tmp_path: Path) -> None:
    """Runner should select the requested fold from leave-out DVC splits."""
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_leave_scenario_out.json"
    output_file = tmp_path / "results" / "agent_a.json"

    _write_json(
        training_file,
        {
            "split_type": "leave_scenario_out",
            "folds": [
                {
                    "fold_name": "hold_out_a",
                    "assignments": [{"id": "sample-a", "file": "case_a.json", "split": "test"}],
                },
                {
                    "fold_name": "hold_out_b",
                    "assignments": [{"id": "sample-b", "file": "case_b.json", "split": "test"}],
                },
            ],
        },
    )
    _write_json(
        dataset_dir / "case_a.json",
        {
            "id": "sample-a",
            "input": {"alert_text": "ALERT A"},
            "expected_output": "ignored",
            "golden_entities": [],
        },
    )
    _write_json(
        dataset_dir / "case_b.json",
        {
            "id": "sample-b",
            "input": {"alert_text": "ALERT B"},
            "expected_output": "selected",
            "golden_entities": [],
        },
    )

    results = run_agent_on_training_split(
        agent=FakeAgent(),
        training_split_path=training_file,
        output_path=output_file,
        dataset_dir=dataset_dir,
        judge_enabled=False,
        cosine_enabled=False,
        fold_name="hold_out_b",
    )

    assert len(results) == 1
    assert results[0].sample_id == "sample-b"
    assert "ALERT B" in results[0].rca_output


def test_runner_requires_fold_when_dvc_split_has_multiple_folds(tmp_path: Path) -> None:
    """Multi-fold DVC splits should fail clearly unless the caller chooses a fold."""
    dataset_dir = tmp_path / "data" / "datasets"
    dataset_dir.mkdir(parents=True)
    training_file = tmp_path / "training_leave_family_out.json"
    output_file = tmp_path / "results" / "agent_a.json"

    _write_json(
        training_file,
        {
            "split_type": "leave_family_out",
            "folds": [
                {"fold_name": "hold_out_config_error", "assignments": []},
                {"fold_name": "hold_out_container_error", "assignments": []},
            ],
        },
    )

    with pytest.raises(ValueError, match="Pass --fold"):
        run_agent_on_training_split(
            agent=FakeAgent(),
            training_split_path=training_file,
            output_path=output_file,
            dataset_dir=dataset_dir,
            judge_enabled=False,
            cosine_enabled=False,
        )


class MockMcpRecordingAgent:
    """Scenario-aware agent used to verify managed mock MCP mode."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def run_sync(self, user_prompt: str, *, scenario: str | None = None) -> str:
        self.calls.append((user_prompt, scenario))
        return "RCA includes selected-service evidence."


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
        self.input = {"alert_text": "ALERT managed mock"}
        self.expected_output = "selected-service evidence"
        self.golden_entities = ["selected-service"]


class FakeSplitRunner:
    """Records SplitRunner iteration arguments without starting real MCP servers."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def iter_cases(self, split_type: str, fold_name: str, split: str):
        self.calls.append((split_type, fold_name, split))
        yield FakeSplitRunnerCase()


def test_runner_can_use_splitrunner_managed_mock_mcp(tmp_path: Path) -> None:
    """Managed mock MCP mode should delegate case iteration to SplitRunner."""
    training_file = tmp_path / "training_stratified.json"
    dataset_dir = tmp_path / "data" / "datasets"
    cache_dir = tmp_path / "cache"
    output_file = tmp_path / "results" / "agent_b.json"
    dataset_dir.mkdir(parents=True)
    cache_dir.mkdir()
    _write_json(
        training_file,
        {
            "split_type": "stratified",
            "assignments": [{"id": "sample-test", "file": "case.json", "split": "test"}],
        },
    )

    fake_runner = FakeSplitRunner()
    agent = MockMcpRecordingAgent()
    with patch("src.common.runner._build_split_runner", return_value=fake_runner):
        results = run_agent_on_training_split(
            agent=agent,
            training_split_path=training_file,
            output_path=output_file,
            dataset_dir=dataset_dir,
            judge_enabled=False,
            cosine_enabled=False,
            partition="test",
            mock_mcp_enabled=True,
            cache_dir=cache_dir,
        )

    assert len(results) == 1
    assert results[0].sample_id == "sample-test"
    assert results[0].score == 1
    assert fake_runner.calls == [("stratified", "default", "test")]
    assert agent.calls == [("ALERT managed mock", "routing")]
    assert output_file.exists()
