"""T4: Cross-consistency tests — validates relationships between cache, datasets and splits."""

import json
import os
from pathlib import Path

import pytest

from conftest import (
    CACHE_DIR, DATASET_DIR, SPLITS_DIR, SPLIT_FILES,
    extract_rc_key, rc_family,
)


def test_every_dataset_has_matching_cache(cache_files, dataset_files):
    cache_set = set(cache_files)
    dataset_set = set(dataset_files)
    missing_cache = dataset_set - cache_set
    missing_dataset = cache_set - dataset_set
    assert len(missing_cache) == 0, f"Datasets without cache: {missing_cache}"
    assert len(missing_dataset) == 0, f"Caches without dataset: {missing_dataset}"


def test_split_files_reference_existing_datasets(all_splits, dataset_files):
    ds_set = set(dataset_files)
    for split_name, data in all_splits.items():
        if "folds" in data:
            all_assignments = []
            for fold in data["folds"]:
                all_assignments.extend(fold["assignments"])
        else:
            all_assignments = data["assignments"]

        referenced_files = {a["file"] for a in all_assignments}
        missing = referenced_files - ds_set
        assert len(missing) == 0, (
            f"{split_name}: references non-existent dataset files: {missing}"
        )


def test_split_ids_exist_in_dataset_files(all_splits, all_datasets):
    for split_name, data in all_splits.items():
        if "folds" in data:
            assignments = data["folds"][0]["assignments"]
        else:
            assignments = data["assignments"]

        for a in assignments:
            ds = all_datasets.get(a["file"])
            assert ds is not None, (
                f"{split_name}: file '{a['file']}' not found in datasets"
            )
            ds_ids = {e["id"] for e in ds}
            assert a["id"] in ds_ids, (
                f"{split_name}: ID '{a['id']}' not found in {a['file']}"
            )


def test_base_alert_consistency(all_samples, all_datasets):
    real_ids = set()
    for fname, ds in all_datasets.items():
        if "--" in fname:
            continue
        for entry in ds:
            real_ids.add(entry["id"])

    for s in all_samples:
        base_alert = s["base_alert"]
        assert base_alert in real_ids, (
            f"Sample {s['id']}: base_alert '{base_alert}' not found in any real dataset"
        )


def test_root_cause_family_consistent(all_splits, all_datasets):
    data = all_splits.get("training_stratified.json")
    if data is None:
        pytest.skip("training_stratified.json not found")

    ds_family_map = {}
    for fname, ds in all_datasets.items():
        for entry in ds:
            rk = extract_rc_key(entry.get("expected_output", ""))
            ds_family_map[entry["id"]] = rc_family(rk)

    mismatches = []
    for a in data["assignments"]:
        expected_family = ds_family_map.get(a["id"])
        if expected_family is None:
            continue
        if a["root_cause_family"] != expected_family:
            mismatches.append((a["id"], a["root_cause_family"], expected_family))

    assert len(mismatches) == 0, (
        f"{len(mismatches)} family mismatches, first 5: {mismatches[:5]}"
    )


def test_variation_base_consistent(all_splits):
    data = all_splits.get("training_stratified.json")
    if data is None:
        pytest.skip("training_stratified.json not found")

    mismatches = []
    for a in data["assignments"]:
        fname = a["file"]
        name = fname.replace(".json", "")
        if "--" in name:
            var_tag = name.split("--")[1]
            expected_var_base = var_tag.split("-s")[0] if "-s" in var_tag else var_tag
        else:
            expected_var_base = "real"

        if a["variation_base"] != expected_var_base:
            mismatches.append((a["id"], a["variation_base"], expected_var_base))

    assert len(mismatches) == 0, (
        f"{len(mismatches)} variation_base mismatches, first 5: {mismatches[:5]}"
    )
