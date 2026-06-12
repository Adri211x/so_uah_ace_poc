"""Unit tests for ``src.common.runner`` (split iteration and JSON output)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.agents.agent_a_ace.playbook_agent import PlaybookInjectingAgent
from src.common.cosine_similarity import CosineSimilarityResult
from src.common.judge import JudgeVerdict
from src.common.runner import main, run_agent_on_training_split


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


class ScenarioAwareAgent:
    """Stub agent that records optional scenario metadata."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def run_sync(self, user_prompt: str, *, scenario: str | None = None) -> str:
        self.calls.append((user_prompt, scenario))
        return f"RCA for {scenario}: {user_prompt}"


class PromptRecordingAgent:
    """Inner agent that records the exact prompt it receives."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run_sync(self, user_prompt: str) -> str:
        self.prompts.append(user_prompt)
        return "The alert needs more investigation."


class MessageResponseAgent:
    """Stub agent returning a pydantic-ai-like response with message parts."""

    def run_sync(self, user_prompt: str) -> object:
        output = "Root cause: service targetPort mismatch."
        return SimpleNamespace(
            output=output,
            all_messages=lambda: [
                SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            part_kind="user-prompt",
                            content=user_prompt,
                        )
                    ]
                ),
                SimpleNamespace(
                    usage=SimpleNamespace(request_tokens=12, response_tokens=4),
                    parts=[
                        SimpleNamespace(
                            part_kind="tool-call",
                            tool_name="kubectl_get_service",
                            args={"namespace": "prod", "service": "api"},
                            tool_call_id="call-1",
                        )
                    ],
                ),
                SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            part_kind="tool-return",
                            tool_name="kubectl_get_service",
                            content={"targetPort": 8080, "containerPort": 8000},
                            tool_call_id="call-1",
                        )
                    ]
                ),
                SimpleNamespace(
                    usage=SimpleNamespace(request_tokens=6, response_tokens=9),
                    parts=[SimpleNamespace(part_kind="text", content=output)],
                ),
            ],
        )


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


def test_runner_passes_scenario_to_agents_that_accept_it(tmp_path: Path) -> None:
    """Runner should forward split scenario metadata to ACE-compatible agents."""
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
                "scenario_id": "kubernetes-data-pipeline",
            }
        ],
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
    agent = ScenarioAwareAgent()

    results = run_agent_on_training_split(
        agent=agent,
        training_split_path=training_file,
        output_path=output_file,
        dataset_dir=dataset_dir,
        judge_enabled=False,
        cosine_enabled=False,
    )

    assert agent.calls == [("ALERT test", "kubernetes-data-pipeline")]
    assert results[0].rca_output == "RCA for kubernetes-data-pipeline: ALERT test"


def test_runner_persists_observable_agent_trajectory(tmp_path: Path) -> None:
    """Runner should save alert, tool activity, observations, and final RCA."""
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
                "scenario_id": "kubernetes-service-routing",
            }
        ],
    )
    _write_json(
        dataset_dir / "test_case.json",
        {
            "id": "sample-test",
            "input": {"alert_text": "ALERT service routing"},
            "expected_output": "Service targetPort mismatch.",
            "golden_entities": ["service targetPort"],
        },
    )

    results = run_agent_on_training_split(
        agent=MessageResponseAgent(),
        training_split_path=training_file,
        output_path=output_file,
        dataset_dir=dataset_dir,
        judge_enabled=False,
        cosine_enabled=False,
    )

    steps = results[0].trajectory_steps
    assert [step.action_type for step in steps] == [
        "alert",
        "user_prompt",
        "tool_call",
        "tool_return",
        "model_response",
        "final_answer",
    ]
    assert steps[0].output_data["alert_text"] == "ALERT service routing"
    assert steps[2].input_data == {
        "tool_name": "kubectl_get_service",
        "args": {"namespace": "prod", "service": "api"},
    }
    assert steps[2].output_data == {"tool_call_id": "call-1"}
    assert steps[2].tokens_in == 12
    assert steps[2].tokens_out == 4
    assert steps[3].output_data["content"]["targetPort"] == 8080
    assert steps[-1].output_data["rca_output"] == "Root cause: service targetPort mismatch."

    persisted = json.loads(output_file.read_text(encoding="utf-8"))
    persisted_steps = persisted[0]["trajectory_steps"]
    assert [step["action_type"] for step in persisted_steps] == [
        "alert",
        "user_prompt",
        "tool_call",
        "tool_return",
        "model_response",
        "final_answer",
    ]
    assert persisted_steps[3]["output_data"]["content"]["containerPort"] == 8000


def test_runner_updates_ace_playbook_when_learning_is_enabled(tmp_path: Path) -> None:
    """ACE learning should run after result persistence when explicitly enabled."""
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_stratified.json"
    output_file = tmp_path / "results" / "agent_a.json"
    playbook_path = tmp_path / "playbook.json"

    _write_json(
        training_file,
        [
            {
                "id": "sample-test",
                "file": "test_case.json",
                "split": "test",
                "base_scenario": "kubernetes-service-routing",
            }
        ],
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

    with patch("src.common.runner._update_ace_playbook") as mocked_learning_loop:
        results = run_agent_on_training_split(
            agent=FakeAgent(),
            training_split_path=training_file,
            output_path=output_file,
            dataset_dir=dataset_dir,
            judge_enabled=False,
            cosine_enabled=False,
            ace_learning_enabled=True,
            ace_playbook_path=playbook_path,
        )

    mocked_learning_loop.assert_called_once_with(
        results=results,
        playbook_path=playbook_path,
        scenario_by_sample_id={"sample-test": "kubernetes-service-routing"},
    )
    assert output_file.exists()


def test_runner_ace_learning_updates_prompt_on_next_run(tmp_path: Path) -> None:
    """Running Agent A twice should use first-run lessons in the second prompt."""
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_stratified.json"
    first_output_file = tmp_path / "results" / "agent_a_first.json"
    second_output_file = tmp_path / "results" / "agent_a_second.json"
    playbook_path = tmp_path / "playbook.json"

    _write_json(
        training_file,
        [
            {
                "id": "sample-test",
                "file": "test_case.json",
                "split": "test",
                "scenario_id": "kubernetes-service-routing",
            }
        ],
    )
    _write_json(
        dataset_dir / "test_case.json",
        {
            "id": "sample-test",
            "input": {"alert_text": "ALERT service routing"},
            "expected_output": "Service targetPort mismatch caused connection refused errors.",
            "golden_entities": ["service targetPort", "connection refused"],
        },
    )

    first_inner_agent = PromptRecordingAgent()
    first_agent = PlaybookInjectingAgent(first_inner_agent, playbook_path=playbook_path)
    with patch(
        "src.agents.agent_a_ace.reflector._generate_llm_insight",
        return_value="Check service targetPort against observed container ports.",
    ):
        run_agent_on_training_split(
            agent=first_agent,
            training_split_path=training_file,
            output_path=first_output_file,
            dataset_dir=dataset_dir,
            judge_enabled=False,
            cosine_enabled=False,
            ace_learning_enabled=True,
            ace_playbook_path=playbook_path,
        )

    assert first_inner_agent.prompts == ["ALERT service routing"]
    assert playbook_path.exists()

    second_inner_agent = PromptRecordingAgent()
    second_agent = PlaybookInjectingAgent(second_inner_agent, playbook_path=playbook_path)
    run_agent_on_training_split(
        agent=second_agent,
        training_split_path=training_file,
        output_path=second_output_file,
        dataset_dir=dataset_dir,
        judge_enabled=False,
        cosine_enabled=False,
    )

    assert len(second_inner_agent.prompts) == 1
    assert "Learned RCA playbook:" in second_inner_agent.prompts[0]
    assert "service targetPort" in second_inner_agent.prompts[0]
    assert "Alert:\nALERT service routing" in second_inner_agent.prompts[0]


def test_runner_does_not_update_ace_playbook_by_default(tmp_path: Path) -> None:
    """Direct runner calls should not mutate ACE state unless learning is enabled."""
    dataset_dir = tmp_path / "data" / "datasets"
    training_file = tmp_path / "training_stratified.json"
    output_file = tmp_path / "results" / "agent_b.json"

    _write_json(
        training_file,
        [{"id": "sample-test", "file": "test_case.json", "split": "test"}],
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

    with patch("src.common.runner._update_ace_playbook") as mocked_learning_loop:
        run_agent_on_training_split(
            agent=FakeAgent(),
            training_split_path=training_file,
            output_path=output_file,
            dataset_dir=dataset_dir,
            judge_enabled=False,
            cosine_enabled=False,
        )

    mocked_learning_loop.assert_not_called()


def test_cli_enables_ace_learning_for_agent_a(tmp_path: Path) -> None:
    """CLI runs should update the ACE playbook automatically for Agent A."""
    args = SimpleNamespace(
        agent="agent_a_ace",
        training_file=str(tmp_path / "training.json"),
        output_file=str(tmp_path / "results.json"),
        dataset_dir=None,
        judge_enabled=False,
        cosine_enabled=False,
        ace_learning_disabled=False,
    )

    with (
        patch("src.common.runner._parse_args", return_value=args),
        patch("src.common.runner._load_agent", return_value=FakeAgent()),
        patch("src.common.runner.run_agent_on_training_split") as mocked_runner,
    ):
        main()

    mocked_runner.assert_called_once()
    assert mocked_runner.call_args.kwargs["ace_learning_enabled"] is True


def test_cli_can_disable_ace_learning_for_agent_a(tmp_path: Path) -> None:
    """The CLI should allow Agent A evaluations without mutating the playbook."""
    args = SimpleNamespace(
        agent="agent_a_ace",
        training_file=str(tmp_path / "training.json"),
        output_file=str(tmp_path / "results.json"),
        dataset_dir=None,
        judge_enabled=False,
        cosine_enabled=False,
        ace_learning_disabled=True,
    )

    with (
        patch("src.common.runner._parse_args", return_value=args),
        patch("src.common.runner._load_agent", return_value=FakeAgent()),
        patch("src.common.runner.run_agent_on_training_split") as mocked_runner,
    ):
        main()

    mocked_runner.assert_called_once()
    assert mocked_runner.call_args.kwargs["ace_learning_enabled"] is False


def test_cli_leaves_ace_learning_disabled_for_baseline(tmp_path: Path) -> None:
    """Baseline CLI runs must keep the ACE learning loop disabled."""
    args = SimpleNamespace(
        agent="agent_b_baseline",
        training_file=str(tmp_path / "training.json"),
        output_file=str(tmp_path / "results.json"),
        dataset_dir=None,
        judge_enabled=False,
        cosine_enabled=False,
        ace_learning_disabled=False,
    )

    with (
        patch("src.common.runner._parse_args", return_value=args),
        patch("src.common.runner._load_agent", return_value=FakeAgent()),
        patch("src.common.runner.run_agent_on_training_split") as mocked_runner,
    ):
        main()

    mocked_runner.assert_called_once()
    assert mocked_runner.call_args.kwargs["ace_learning_enabled"] is False


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
