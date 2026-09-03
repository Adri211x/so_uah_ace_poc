"""Unit tests for the first Agent A ACE learning skeleton."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.agents.agent_a_ace.curator import (
    JsonPlaybookCurator,
    build_generator_prompt,
    render_playbook_context,
    select_playbook_entries,
)
from src.agents.agent_a_ace.loop import update_playbook_from_results
from src.agents.agent_a_ace.playbook_agent import PlaybookInjectingAgent
from src.agents.agent_a_ace.reflector import reflect_result
from src.common.cosine_similarity import CosineSimilarityResult
from src.common.judge import JudgeVerdict
from src.common.schemas import AceInsight, AgentResult, Playbook, PlaybookEntry


def _agent_result(
    *,
    sample_id: str = "sample-1",
    score: int,
    matched_entities: list[str],
    judge_overall: float | None = None,
    cosine_similarity: float | None = None,
) -> AgentResult:
    """Build a minimal evaluated result for ACE tests."""
    judge_verdict = None
    if judge_overall is not None:
        judge_verdict = JudgeVerdict(
            root_cause_match=judge_overall,
            evidence_quality=judge_overall,
            completeness=judge_overall,
            overall=judge_overall,
            reasoning="test",
            judge_model="test-model",
        )

    cosine_result = None
    if cosine_similarity is not None:
        cosine_result = CosineSimilarityResult(
            rca_similarity=cosine_similarity,
            golden_entity_similarities={},
            golden_entities_avg=0.0,
            golden_entities_max=0.0,
        )

    return AgentResult(
        sample_id=sample_id,
        rca_output="config_error: service targetPort mismatch.",
        expected_output="config_error: service targetPort mismatch.",
        golden_entities=["service targetPort", "container port", "connection refused"],
        score=score,
        matched_entities=matched_entities,
        judge_verdict=judge_verdict,
        cosine_similarity=cosine_result,
    )


class RecordingAgent:
    """Fake inner agent used to assert prompt injection."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run_sync(self, user_prompt: str, *args: Any, **kwargs: Any) -> str:
        self.prompts.append(user_prompt)
        return "ok"


def test_reflector_extracts_positive_insight_from_fully_successful_result() -> None:
    """Successful runs should pass golden, judge, and cosine checks."""
    result = _agent_result(
        score=3,
        matched_entities=["service targetPort", "container port", "connection refused"],
        judge_overall=0.8,
        cosine_similarity=0.9,
    )

    insights = reflect_result(result, scenario="kubernetes-service-routing")

    assert len(insights) == 1
    assert insights[0].outcome == "success"
    assert insights[0].scenario == "kubernetes-service-routing"
    assert "service targetPort" in insights[0].text
    assert insights[0].missing_entities == []


def test_reflector_extracts_corrective_insight_from_missing_golden_entities() -> None:
    """Failed runs should produce lessons about missing expected evidence."""
    result = _agent_result(score=1, matched_entities=["service targetPort"])

    insights = reflect_result(result, scenario="kubernetes-service-routing")

    assert len(insights) == 1
    assert insights[0].outcome == "failure"
    assert "container port" in insights[0].text
    assert "connection refused" in insights[0].text


def test_reflector_marks_full_golden_match_as_failure_when_judge_is_low() -> None:
    """Judge scores should be part of the success criteria when present."""
    result = _agent_result(
        score=3,
        matched_entities=["service targetPort", "container port", "connection refused"],
        judge_overall=0.4,
    )

    insights = reflect_result(result)

    assert insights[0].outcome == "failure"
    assert "ground-truth root cause" in insights[0].text


def test_reflector_marks_full_golden_match_as_failure_when_cosine_is_low() -> None:
    """Cosine similarity should be part of the success criteria when present."""
    result = _agent_result(
        score=3,
        matched_entities=["service targetPort", "container port", "connection refused"],
        cosine_similarity=0.2,
    )

    insights = reflect_result(result)

    assert insights[0].outcome == "failure"
    assert "semantically distant" in insights[0].text


def test_curator_deduplicates_insights_and_tracks_counters(tmp_path: Path) -> None:
    """Curator must merge repeated lessons into one playbook entry."""
    playbook_path = tmp_path / "playbook.json"
    curator = JsonPlaybookCurator(playbook_path)
    text = "For routing cases, verify Service selectors before finalizing the RCA."
    insights = [
        AceInsight(
            text=text,
            scenario="routing",
            source_sample_id="sample-1",
            outcome="failure",
            score=1,
        ),
        AceInsight(
            text=text,
            scenario="routing",
            source_sample_id="sample-2",
            outcome="success",
            score=3,
        ),
    ]

    playbook = curator.curate(insights)

    assert len(playbook.entries) == 1
    assert playbook.entries[0].helpful_count == 1
    assert playbook.entries[0].harmful_count == 1
    assert playbook.entries[0].source_sample_ids == ["sample-1", "sample-2"]
    persisted = json.loads(playbook_path.read_text(encoding="utf-8"))
    assert persisted["entries"][0]["text"] == text


def test_curator_keeps_same_text_separate_across_scenarios(tmp_path: Path) -> None:
    """Same lessons from different scenarios must remain independently scoped."""
    playbook_path = tmp_path / "playbook.json"
    curator = JsonPlaybookCurator(playbook_path)
    text = "Verify the expected evidence before finalizing the RCA."

    playbook = curator.curate(
        [
            AceInsight(
                text=text,
                scenario="routing",
                source_sample_id="sample-1",
                outcome="success",
            ),
            AceInsight(
                text=text,
                scenario="crashloop",
                source_sample_id="sample-2",
                outcome="success",
            ),
        ]
    )

    assert len(playbook.entries) == 2
    assert {entry.scenario for entry in playbook.entries} == {"routing", "crashloop"}


def test_update_playbook_from_results_runs_reflector_and_curator(tmp_path: Path) -> None:
    """A tiny offline ACE loop should create a playbook from evaluated results."""
    playbook_path = tmp_path / "ace_playbook.json"
    results = [
        _agent_result(
            sample_id="sample-success",
            score=3,
            matched_entities=["service targetPort", "container port", "connection refused"],
        ),
        _agent_result(sample_id="sample-failure", score=0, matched_entities=[]),
    ]

    playbook = update_playbook_from_results(
        results,
        playbook_path=playbook_path,
        scenario_by_sample_id={
            "sample-success": "kubernetes-service-routing",
            "sample-failure": "kubernetes-service-routing",
        },
    )

    assert playbook_path.exists()
    assert len(playbook.entries) == 2
    assert any(entry.helpful_count == 1 for entry in playbook.entries)
    assert any(entry.harmful_count == 1 for entry in playbook.entries)


def test_generator_prompt_selects_scenario_scoped_playbook_context() -> None:
    """Generator prompts should include learned playbook entries when provided."""
    playbook = Playbook(
        entries=[
            PlaybookEntry(
                id="entry-1",
                text="Check Service targetPort against the container port.",
                scenario="routing",
                helpful_count=2,
            ),
            PlaybookEntry(
                id="entry-2",
                text="Inspect CrashLoopBackOff container command first.",
                scenario="crashloop",
                helpful_count=10,
            ),
        ]
    )

    selected = select_playbook_entries(playbook, scenario="routing")
    prompt = build_generator_prompt("ALERT: service unavailable", playbook, scenario="routing")

    assert [entry.id for entry in selected] == ["entry-1"]
    assert "Learned RCA playbook" in prompt
    assert "Check Service targetPort" in prompt
    assert "CrashLoopBackOff" not in prompt
    assert "ALERT: service unavailable" in prompt
    assert (
        render_playbook_context(Playbook()) == "No learned RCA playbook entries are available yet."
    )


def test_playbook_injecting_agent_wraps_prompt_with_curated_context(tmp_path: Path) -> None:
    """Agent A wrapper should inject persisted playbook entries into run prompts."""
    playbook_path = tmp_path / "playbook.json"
    JsonPlaybookCurator(playbook_path).save(
        Playbook(
            entries=[
                PlaybookEntry(
                    id="entry-1",
                    text="Check Service targetPort against the container port.",
                    scenario="routing",
                    helpful_count=1,
                )
            ]
        )
    )
    inner = RecordingAgent()
    wrapper = PlaybookInjectingAgent(inner, playbook_path)

    response = wrapper.run_sync("ALERT: service unavailable", scenario="routing")

    assert response == "ok"
    assert "Learned RCA playbook" in inner.prompts[0]
    assert "Check Service targetPort" in inner.prompts[0]
    assert "ALERT: service unavailable" in inner.prompts[0]


def test_playbook_injecting_agent_leaves_prompt_unchanged_when_playbook_is_empty(
    tmp_path: Path,
) -> None:
    """An empty playbook should keep Agent A equivalent to the baseline prompt."""
    inner = RecordingAgent()
    wrapper = PlaybookInjectingAgent(inner, tmp_path / "missing-playbook.json")

    response = wrapper.run_sync("ALERT: service unavailable", scenario="routing")

    assert response == "ok"
    assert inner.prompts == ["ALERT: service unavailable"]


def test_ablation_configs_match_selected_experiment_modes() -> None:
    """The six selected variants should resolve to explicit experiment behavior."""
    from src.agents.agent_a_ace.ablations import AceAblationMode, build_experiment_config

    baseline = build_experiment_config(AceAblationMode.BASELINE)
    full = build_experiment_config(AceAblationMode.FULL, default_epochs=4)
    single_epoch = build_experiment_config(AceAblationMode.SINGLE_EPOCH, default_epochs=4)
    no_warmup = build_experiment_config(AceAblationMode.ONLINE_NO_WARMUP, default_epochs=4)
    scoped = build_experiment_config(AceAblationMode.SCENARIO_SCOPED_PLAYBOOK, default_epochs=4)
    failure_only = build_experiment_config(
        AceAblationMode.FAILURE_ONLY_REFLECTION, default_epochs=4
    )

    assert baseline.agent_name == "agent_b_baseline"
    assert baseline.warmup_enabled is False
    assert full.agent_name == "agent_a_ace"
    assert full.learning_epochs == 4
    assert single_epoch.learning_epochs == 1
    assert no_warmup.warmup_enabled is False
    assert scoped.playbook_scope == "strict"
    assert failure_only.reflection_outcomes == frozenset({"failure"})


def test_update_playbook_can_keep_only_failure_reflections(tmp_path: Path) -> None:
    """Failure-only ablation should exclude success insights from the playbook."""
    playbook_path = tmp_path / "failure_only_playbook.json"
    results = [
        _agent_result(
            sample_id="sample-success",
            score=3,
            matched_entities=["service targetPort", "container port", "connection refused"],
        ),
        _agent_result(sample_id="sample-failure", score=0, matched_entities=[]),
    ]

    playbook = update_playbook_from_results(
        results,
        playbook_path=playbook_path,
        reflection_outcomes=frozenset({"failure"}),
    )

    assert len(playbook.entries) == 1
    assert playbook.entries[0].helpful_count == 0
    assert playbook.entries[0].harmful_count == 1
    assert playbook.entries[0].source_sample_ids == ["sample-failure"]


def test_strict_scenario_scope_does_not_fallback_to_other_scenarios() -> None:
    """Scenario-scoped ablation should avoid unrelated playbook entries."""
    playbook = Playbook(
        entries=[
            PlaybookEntry(
                id="entry-routing",
                text="Check Service targetPort against the container port.",
                scenario="routing",
                helpful_count=2,
            ),
            PlaybookEntry(
                id="entry-crashloop",
                text="Inspect CrashLoopBackOff container command first.",
                scenario="crashloop",
                helpful_count=10,
            ),
        ]
    )

    selected = select_playbook_entries(
        playbook,
        scenario="storage",
        playbook_scope="strict",
    )
    prompt = build_generator_prompt(
        "ALERT: volume mount failed",
        playbook,
        scenario="storage",
        playbook_scope="strict",
    )

    assert selected == []
    assert prompt == "ALERT: volume mount failed"
