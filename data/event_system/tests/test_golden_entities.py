"""T5: Golden entities tests — validates correctness and consistency of golden_entities."""

import json
from collections import defaultdict
from pathlib import Path

from conftest import CACHE_DIR, DATASET_DIR


class TestGoldenEntitiesPresence:

    def test_all_entries_have_golden_entities(self, all_datasets):
        missing = []
        for fname, ds in all_datasets.items():
            for entry in ds:
                if "golden_entities" not in entry:
                    missing.append(f"{fname}/{entry['id']}")
        assert len(missing) == 0, (
            f"{len(missing)} entries missing golden_entities, first 5: {missing[:5]}"
        )

    def test_golden_entities_is_list_of_3(self, all_datasets):
        bad = []
        for fname, ds in all_datasets.items():
            for entry in ds:
                ge = entry.get("golden_entities", [])
                if not isinstance(ge, list) or len(ge) != 3:
                    bad.append(f"{fname}/{entry['id']}: {len(ge)} entities")
        assert len(bad) == 0, f"Entries with != 3 entities: {bad[:10]}"

    def test_golden_entities_are_nonempty_strings(self, all_datasets):
        for fname, ds in all_datasets.items():
            for entry in ds:
                for i, ge in enumerate(entry.get("golden_entities", [])):
                    assert isinstance(ge, str) and len(ge.strip()) > 0, (
                        f"{fname}/{entry['id']}: entity [{i}] is empty or not a string"
                    )


class TestGoldenEntitiesNotInAlert:

    def test_no_entity_in_alert_text(self, all_datasets):
        """Core invariant: golden entities must NOT appear in the alert text."""
        violations = []
        for fname, ds in all_datasets.items():
            for entry in ds:
                alert_lower = entry["input"]["alert_text"].lower()
                for ge in entry.get("golden_entities", []):
                    if ge.lower() in alert_lower:
                        violations.append(
                            f"{fname}/{entry['id']}: '{ge}' found in alert"
                        )
        assert len(violations) == 0, (
            f"{len(violations)} violations, first 10:\n"
            + "\n".join(violations[:10])
        )


class TestGoldenEntitiesInGroundTruth:

    def test_entities_present_in_expected_output(self, all_datasets):
        """At least 2 of 3 golden entities should appear in the expected_output."""
        weak = []
        for fname, ds in all_datasets.items():
            if "--" in fname:
                continue
            for entry in ds:
                eo_lower = str(entry.get("expected_output", "")).lower()
                matches = sum(
                    1 for ge in entry.get("golden_entities", [])
                    if ge.lower() in eo_lower
                )
                if matches < 2:
                    weak.append(
                        f"{fname}/{entry['id']}: only {matches}/3 in expected_output"
                    )
        assert len(weak) == 0, (
            f"{len(weak)} entries with <2 entities in expected_output:\n"
            + "\n".join(weak[:10])
        )


class TestGoldenEntitiesConsistency:

    def test_same_scenario_same_entities_for_non_ns_shifted(self, all_datasets):
        """noise_injected and alert_rephrased must have identical entities to real."""
        by_scenario = defaultdict(dict)
        for fname, ds in all_datasets.items():
            name = fname.replace(".json", "")
            base = name.split("--")[0] if "--" in name else name
            var = name.split("--")[1] if "--" in name else "real"
            var_base = var.split("-s")[0] if "-s" in var else var
            if var_base in ("real", "noise_injected", "alert_rephrased"):
                for entry in ds:
                    base_id = entry["id"].split("--")[0] if "--" in entry["id"] else entry["id"]
                    key = (base, base_id, var_base)
                    by_scenario[key] = entry.get("golden_entities", [])

        mismatches = []
        for (scenario, alert_id, var_base), entities in by_scenario.items():
            real_key = (scenario, alert_id, "real")
            real_entities = by_scenario.get(real_key, [])
            if var_base != "real" and entities != real_entities:
                mismatches.append(
                    f"{scenario}/{alert_id}/{var_base}: {entities} != {real_entities}"
                )
        assert len(mismatches) == 0, (
            f"{len(mismatches)} mismatches:\n" + "\n".join(mismatches[:10])
        )

    def test_ns_shifted_entities_consistent_with_mapping(self, all_datasets):
        """namespace_shifted entities are adapted when ns_map overlaps, else identical."""
        mismatches = []

        for fname, ds in all_datasets.items():
            if "namespace_shifted" not in fname:
                continue
            cache_path = CACHE_DIR / fname
            if not cache_path.exists():
                continue
            with open(cache_path) as fh:
                cache = json.load(fh)
            ns_map = cache.get("_meta", {}).get("mappings", {}).get("namespaces", {})

            base_fname = fname.split("--")[0] + ".json"
            if not (DATASET_DIR / base_fname).exists():
                continue
            with open(DATASET_DIR / base_fname) as fh:
                base_ds = json.load(fh)

            for entry, base_entry in zip(ds, base_ds):
                base_ge = base_entry.get("golden_entities", [])
                shifted_ge = entry.get("golden_entities", [])
                has_ns_in_entity = ns_map and any(
                    old_ns in ge for ge in base_ge for old_ns in ns_map
                )
                if has_ns_in_entity:
                    if shifted_ge == base_ge:
                        mismatches.append(
                            f"{fname}: entities NOT adapted despite ns overlap"
                        )
                else:
                    if shifted_ge != base_ge:
                        mismatches.append(
                            f"{fname}: entities changed but no ns overlap"
                        )

        assert len(mismatches) == 0, (
            f"{len(mismatches)} mismatches:\n" + "\n".join(mismatches[:10])
        )
