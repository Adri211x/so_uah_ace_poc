"""Tests for case_provider, mock_manager, and split_runner modules."""

import json
from pathlib import Path

import pytest
from event_system.case_provider import Case, LocalCaseProvider
from event_system.mock_manager import MockManager

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def sample_training_stratified(tmp_path: Path) -> Path:
    """Create a minimal stratified training JSON file."""
    data = {
        "split_type": "stratified",
        "description": "test split",
        "seed": 42,
        "assignments": [
            {
                "id": "kubernetes-crashloop-KubePodCrashLooping",
                "file": "kubernetes-crashloop.json",
                "split": "train",
                "base_scenario": "kubernetes-crashloop",
                "variation": "real",
                "variation_base": "real",
                "root_cause_family": "container_error",
                "root_cause_key": "invalid_container_command",
            },
            {
                "id": "kubernetes-crashloop-KubePodCrashLooping--alert_rephrased",
                "file": "kubernetes-crashloop--alert_rephrased.json",
                "split": "test",
                "base_scenario": "kubernetes-crashloop",
                "variation": "alert_rephrased",
                "variation_base": "alert_rephrased",
                "root_cause_family": "container_error",
                "root_cause_key": "invalid_container_command",
            },
            {
                "id": "kubernetes-oomkilled-KubePodCrashLooping",
                "file": "kubernetes-oomkilled.json",
                "split": "train",
                "base_scenario": "kubernetes-oomkilled",
                "variation": "real",
                "variation_base": "real",
                "root_cause_family": "resource_limit",
                "root_cause_key": "memory_limit_exceeded",
            },
            {
                "id": "kubernetes-oomkilled-KubePodCrashLooping--noise_injected",
                "file": "kubernetes-oomkilled--noise_injected.json",
                "split": "test",
                "base_scenario": "kubernetes-oomkilled",
                "variation": "noise_injected",
                "variation_base": "noise_injected",
                "root_cause_family": "resource_limit",
                "root_cause_key": "memory_limit_exceeded",
            },
        ],
    }
    path = tmp_path / "training_stratified.json"
    path.write_text(json.dumps(data))
    return tmp_path


@pytest.fixture()
def sample_leave_family_out(tmp_path: Path) -> Path:
    """Create a minimal leave-family-out training JSON file."""
    data = {
        "split_type": "leave_family_out",
        "folds": [
            {
                "fold_name": "hold_out_container_error",
                "assignments": [
                    {
                        "id": "kubernetes-crashloop-Alert",
                        "file": "kubernetes-crashloop.json",
                        "split": "test",
                        "base_scenario": "kubernetes-crashloop",
                        "variation": "real",
                        "variation_base": "real",
                        "root_cause_family": "container_error",
                        "root_cause_key": "invalid_container_command",
                    },
                    {
                        "id": "kubernetes-oomkilled-Alert",
                        "file": "kubernetes-oomkilled.json",
                        "split": "train",
                        "base_scenario": "kubernetes-oomkilled",
                        "variation": "real",
                        "variation_base": "real",
                        "root_cause_family": "resource_limit",
                        "root_cause_key": "memory_limit_exceeded",
                    },
                ],
            },
        ],
    }
    path = tmp_path / "training_leave_family_out.json"
    path.write_text(json.dumps(data))
    return tmp_path


@pytest.fixture()
def sample_dataset(tmp_path: Path) -> Path:
    """Create sample dataset JSON files for ground truth."""
    datasets_dir = tmp_path / "data" / "datasets"
    datasets_dir.mkdir(parents=True)

    ds = [
        {
            "id": "kubernetes-crashloop-KubePodCrashLooping",
            "input": {"alert_text": "ALERT: KubePodCrashLooping on pod/broken-app"},
            "expected_output": "Root cause: invalid_container_command - broken entrypoint",
            "golden_entities": ["invalid command", "entrypoint", "python3"],
            "metadata": {"scenario": "kubernetes-crashloop"},
        },
    ]
    (datasets_dir / "kubernetes-crashloop.json").write_text(json.dumps(ds))
    return tmp_path


@pytest.fixture()
def sample_cache(tmp_path: Path) -> Path:
    """Create a minimal cache JSON file."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    cache = {
        "_meta": {"case_type": "kubernetes-crashloop"},
        "kubectl": {"kubectl get pods -A": "NAME   READY  STATUS\nbroken  0/1  CrashLoopBackOff"},
    }
    (cache_dir / "kubernetes-crashloop.json").write_text(json.dumps(cache))
    return tmp_path


# ── LocalCaseProvider tests ───────────────────────────────────────────────────


class TestLocalCaseProvider:
    def test_list_split_types(self, sample_training_stratified: Path):
        provider = LocalCaseProvider(data_dir=sample_training_stratified)
        types = provider.list_split_types()
        assert "stratified" in types

    def test_list_folds_stratified(self, sample_training_stratified: Path):
        provider = LocalCaseProvider(data_dir=sample_training_stratified)
        folds = provider.list_folds("stratified")
        assert folds == ["default"]

    def test_list_folds_leave_family(self, sample_leave_family_out: Path):
        provider = LocalCaseProvider(data_dir=sample_leave_family_out)
        folds = provider.list_folds("leave_family_out")
        assert "hold_out_container_error" in folds

    def test_get_cases_train(self, sample_training_stratified: Path):
        provider = LocalCaseProvider(data_dir=sample_training_stratified)
        cases = provider.get_cases("stratified", "default", "train")
        assert len(cases) == 2
        assert all(c.split == "train" for c in cases)

    def test_get_cases_test(self, sample_training_stratified: Path):
        provider = LocalCaseProvider(data_dir=sample_training_stratified)
        cases = provider.get_cases("stratified", "default", "test")
        assert len(cases) == 2
        assert all(c.split == "test" for c in cases)

    def test_case_fields(self, sample_training_stratified: Path):
        provider = LocalCaseProvider(data_dir=sample_training_stratified)
        cases = provider.get_cases("stratified", "default", "train")
        c = cases[0]
        assert c.scenario_id == "kubernetes-crashloop"
        assert c.family == "container_error"
        assert c.source_file == "kubernetes-crashloop.json"

    def test_summary(self, sample_training_stratified: Path):
        provider = LocalCaseProvider(data_dir=sample_training_stratified)
        counts = provider.summary("stratified", "default")
        assert counts == {"train": 2, "test": 2}

    def test_unknown_split_type(self, sample_training_stratified: Path):
        provider = LocalCaseProvider(data_dir=sample_training_stratified)
        with pytest.raises(ValueError, match="Unknown split_type"):
            provider.get_cases("nonexistent", "default", "test")

    def test_missing_file(self, tmp_path: Path):
        provider = LocalCaseProvider(data_dir=tmp_path)
        with pytest.raises(FileNotFoundError, match="dvc pull"):
            provider.get_cases("stratified", "default", "test")

    def test_dev_split(self, sample_training_stratified: Path):
        provider = LocalCaseProvider(data_dir=sample_training_stratified)
        dev = provider.get_cases("dev")
        assert len(dev) == 2
        assert all(c.split == "dev" for c in dev)
        assert all(c.variation_type == "real" for c in dev)
        scenario_ids = {c.scenario_id for c in dev}
        assert scenario_ids == {"kubernetes-crashloop", "kubernetes-oomkilled"}

    def test_fold_not_found(self, sample_leave_family_out: Path):
        provider = LocalCaseProvider(data_dir=sample_leave_family_out)
        cases = provider.get_cases("leave_family_out", "nonexistent_fold", "test")
        assert cases == []


# ── MockManager tests ─────────────────────────────────────────────────────────


class TestMockManager:
    def test_load_ground_truth(self, sample_dataset: Path):
        manager = MockManager(
            cache_dir=sample_dataset / "cache",
            dataset_dir=sample_dataset / "data" / "datasets",
        )
        gt = manager.load_ground_truth(
            "kubernetes-crashloop.json",
            "kubernetes-crashloop-KubePodCrashLooping",
        )
        assert gt is not None
        assert gt.case_id == "kubernetes-crashloop-KubePodCrashLooping"
        assert "invalid_container_command" in gt.expected_output
        assert len(gt.golden_entities) == 3

    def test_load_ground_truth_with_variation_suffix(self, sample_dataset: Path):
        manager = MockManager(
            cache_dir=sample_dataset / "cache",
            dataset_dir=sample_dataset / "data" / "datasets",
        )
        gt = manager.load_ground_truth(
            "kubernetes-crashloop.json",
            "kubernetes-crashloop-KubePodCrashLooping--alert_rephrased-s500",
        )
        assert gt is not None
        assert gt.case_id == "kubernetes-crashloop-KubePodCrashLooping"

    def test_load_ground_truth_missing_file(self, tmp_path: Path):
        manager = MockManager(
            cache_dir=tmp_path, dataset_dir=tmp_path / "nonexistent",
        )
        gt = manager.load_ground_truth("missing.json", "any-ref")
        assert gt is None

    def test_load_ground_truth_missing_case(self, sample_dataset: Path):
        manager = MockManager(
            cache_dir=sample_dataset / "cache",
            dataset_dir=sample_dataset / "data" / "datasets",
        )
        gt = manager.load_ground_truth(
            "kubernetes-crashloop.json",
            "nonexistent-case-ref",
        )
        assert gt is None

    def test_ref_to_case_id(self):
        assert MockManager._ref_to_case_id(
            "kubernetes-crashloop-KubePodCrashLooping--alert_rephrased-s1200"
        ) == "kubernetes-crashloop-KubePodCrashLooping"

        assert MockManager._ref_to_case_id(
            "kubernetes-crashloop-KubePodCrashLooping"
        ) == "kubernetes-crashloop-KubePodCrashLooping"

    def test_start_missing_cache(self, tmp_path: Path):
        manager = MockManager(
            cache_dir=tmp_path / "empty",
            dataset_dir=tmp_path,
        )
        with pytest.raises(FileNotFoundError):
            manager.start("nonexistent.json")


# ── SplitRunner tests ─────────────────────────────────────────────────────────


class TestSplitRunner:
    def test_group_by_source(self):
        from event_system.split_runner import SplitRunner

        cases = [
            Case("ref-a", "scen-a", "fam", "rk", "real", "file1.json", "test"),
            Case("ref-b", "scen-b", "fam", "rk", "real", "file1.json", "test"),
            Case("ref-c", "scen-c", "fam", "rk", "real", "file2.json", "test"),
        ]
        groups = SplitRunner._group_by_source(cases)
        assert len(groups) == 2
        assert len(groups["file1.json"]) == 2
        assert len(groups["file2.json"]) == 1

    def test_discovery_methods(self, sample_training_stratified: Path):
        from event_system.split_runner import SplitRunner

        provider = LocalCaseProvider(data_dir=sample_training_stratified)
        runner = SplitRunner(
            provider=provider,
            cache_dir=sample_training_stratified / "cache",
            dataset_dir=sample_training_stratified / "data" / "datasets",
        )
        assert "stratified" in runner.list_split_types()
        assert runner.list_folds("stratified") == ["default"]
        assert runner.summary("stratified", "default") == {"train": 2, "test": 2}

    def test_get_cases_without_mock(self, sample_training_stratified: Path):
        from event_system.split_runner import SplitRunner

        provider = LocalCaseProvider(data_dir=sample_training_stratified)
        runner = SplitRunner(
            provider=provider,
            cache_dir=sample_training_stratified / "cache",
            dataset_dir=sample_training_stratified / "data" / "datasets",
        )
        cases = runner.get_cases("stratified", "default", "test")
        assert len(cases) == 2
