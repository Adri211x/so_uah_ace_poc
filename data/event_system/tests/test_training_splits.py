"""T3: Training split tests — validates statistical properties and logic of each split."""

import json
from collections import defaultdict
from pathlib import Path

import pytest

from conftest import SPLITS_DIR, SPLIT_FILES


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_all_assignments(split_data):
    """Yield (fold_name_or_None, assignments_list) from any split format."""
    if "folds" in split_data:
        for fold in split_data["folds"]:
            yield fold.get("fold_name"), fold["assignments"]
    else:
        yield None, split_data["assignments"]


def _load_split(name):
    with open(SPLITS_DIR / name) as f:
        return json.load(f)


# ── Common properties (parametrized over all 4 split files) ───────────────────

@pytest.fixture(params=SPLIT_FILES)
def split_data(request):
    return _load_split(request.param), request.param


class TestCommonSplitProperties:

    def test_has_required_keys(self, split_data):
        data, fname = split_data
        assert "split_type" in data, f"{fname}: missing split_type"
        has_assignments = "assignments" in data
        has_folds = "folds" in data and all("assignments" in f for f in data["folds"])
        assert has_assignments or has_folds, f"{fname}: no assignments found"

    def test_assignments_cover_all_559_samples(self, split_data):
        data, fname = split_data
        for fold_name, assignments in _get_all_assignments(data):
            label = f"{fname}/{fold_name}" if fold_name else fname
            assert len(assignments) == 559, (
                f"{label}: expected 559 assignments, got {len(assignments)}"
            )

    def test_only_train_or_test(self, split_data):
        data, fname = split_data
        for fold_name, assignments in _get_all_assignments(data):
            label = f"{fname}/{fold_name}" if fold_name else fname
            bad = [a for a in assignments if a["split"] not in ("train", "test")]
            assert len(bad) == 0, (
                f"{label}: {len(bad)} assignments have invalid split value"
            )

    def test_all_ids_are_strings(self, split_data):
        data, fname = split_data
        for fold_name, assignments in _get_all_assignments(data):
            for a in assignments:
                assert isinstance(a["id"], str) and len(a["id"]) > 0


# ── Stratified ────────────────────────────────────────────────────────────────

class TestStratified:

    @pytest.fixture(scope="class")
    def strat(self):
        return _load_split("training_stratified.json")

    def test_ratio(self, strat):
        r = strat["statistics"]["train_ratio"]
        assert 0.75 <= r <= 0.85, f"Train ratio {r} outside [0.75, 0.85]"

    def test_no_leakage(self, strat):
        alert_splits = defaultdict(set)
        for a in strat["assignments"]:
            base_alert = a["id"].split("--")[0] if "--" in a["id"] else a["id"]
            alert_splits[base_alert].add(a["split"])
        leaked = {ba: splits for ba, splits in alert_splits.items() if len(splits) > 1}
        assert len(leaked) == 0, f"Data leakage: {len(leaked)} base alerts in both sets: {list(leaked.keys())[:5]}"

    def test_variation_proportional(self, strat):
        by_var = defaultdict(lambda: {"train": 0, "test": 0})
        for a in strat["assignments"]:
            by_var[a["variation_base"]][a["split"]] += 1

        ratios = {}
        for var, counts in by_var.items():
            total = counts["train"] + counts["test"]
            ratios[var] = counts["test"] / total if total else 0

        values = list(ratios.values())
        spread = max(values) - min(values)
        assert spread < 0.02, (
            f"Variation test ratios spread too wide ({spread:.4f}): {ratios}"
        )

    def test_singleton_families_in_train(self, strat):
        singletons = {"lock_error", "performance", "probe_error", "routing_error"}
        test_families = set()
        for a in strat["assignments"]:
            if a["split"] == "test":
                test_families.add(a["root_cause_family"])
        leaked_singletons = singletons & test_families
        assert len(leaked_singletons) == 0, (
            f"Singleton families found in test: {leaked_singletons}"
        )


# ── LOVO ──────────────────────────────────────────────────────────────────────

class TestLOVO:

    @pytest.fixture(scope="class")
    def lovo(self):
        return _load_split("training_leave_variation_out.json")

    def test_fold_count(self, lovo):
        assert lovo["num_folds"] == 4
        assert len(lovo["folds"]) == 4

    def test_held_out_complete(self, lovo):
        for fold in lovo["folds"]:
            held_out = fold["held_out_variation"]
            for a in fold["assignments"]:
                if a["variation_base"] == held_out:
                    assert a["split"] == "test", (
                        f"Fold {fold['fold_name']}: {a['id']} should be test (held out)"
                    )
                else:
                    assert a["split"] == "train", (
                        f"Fold {fold['fold_name']}: {a['id']} should be train"
                    )

    def test_all_scenarios_in_both(self, lovo):
        for fold in lovo["folds"]:
            train_scenarios = set()
            test_scenarios = set()
            for a in fold["assignments"]:
                if a["split"] == "train":
                    train_scenarios.add(a["base_scenario"])
                else:
                    test_scenarios.add(a["base_scenario"])
            assert train_scenarios == test_scenarios, (
                f"Fold {fold['fold_name']}: scenario mismatch between train and test"
            )


# ── LOSO ──────────────────────────────────────────────────────────────────────

class TestLOSO:

    @pytest.fixture(scope="class")
    def loso(self):
        return _load_split("training_leave_scenario_out.json")

    def test_fold_count(self, loso):
        assert loso["num_folds"] == 19
        assert len(loso["folds"]) == 19

    def test_held_out_complete(self, loso):
        for fold in loso["folds"]:
            held_out = fold["held_out_scenario"]
            test_ids = [a for a in fold["assignments"] if a["split"] == "test"]
            for a in test_ids:
                assert a["base_scenario"] == held_out, (
                    f"Fold {fold['fold_name']}: test sample {a['id']} has scenario "
                    f"'{a['base_scenario']}' but held out is '{held_out}'"
                )

    def test_no_scenario_in_train(self, loso):
        for fold in loso["folds"]:
            held_out = fold["held_out_scenario"]
            train_scenarios = {a["base_scenario"] for a in fold["assignments"] if a["split"] == "train"}
            assert held_out not in train_scenarios, (
                f"Fold {fold['fold_name']}: held-out scenario '{held_out}' found in train"
            )


# ── LOFO ──────────────────────────────────────────────────────────────────────

class TestLOFO:

    @pytest.fixture(scope="class")
    def lofo(self):
        return _load_split("training_leave_family_out.json")

    def test_fold_count(self, lofo):
        assert lofo["num_folds"] == 11
        assert len(lofo["folds"]) == 11

    def test_held_out_complete(self, lofo):
        for fold in lofo["folds"]:
            held_out = fold["held_out_family"]
            test_ids = [a for a in fold["assignments"] if a["split"] == "test"]
            for a in test_ids:
                assert a["root_cause_family"] == held_out, (
                    f"Fold {fold['fold_name']}: test sample {a['id']} has family "
                    f"'{a['root_cause_family']}' but held out is '{held_out}'"
                )

    def test_no_family_in_train(self, lofo):
        for fold in lofo["folds"]:
            held_out = fold["held_out_family"]
            train_families = {a["root_cause_family"] for a in fold["assignments"] if a["split"] == "train"}
            assert held_out not in train_families, (
                f"Fold {fold['fold_name']}: held-out family '{held_out}' found in train"
            )

    def test_train_test_disjoint(self, lofo):
        for fold in lofo["folds"]:
            train_ids = {a["id"] for a in fold["assignments"] if a["split"] == "train"}
            test_ids = {a["id"] for a in fold["assignments"] if a["split"] == "test"}
            overlap = train_ids & test_ids
            assert len(overlap) == 0, (
                f"Fold {fold['fold_name']}: {len(overlap)} IDs in both train and test"
            )
