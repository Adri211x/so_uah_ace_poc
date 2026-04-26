"""T1: Data integrity tests — validates files exist and have correct structure."""

from collections import Counter


def test_cache_and_dataset_count_match(cache_files, dataset_files):
    assert len(cache_files) == 247, f"Expected 247 cache files, got {len(cache_files)}"
    assert len(dataset_files) == 247, f"Expected 247 dataset files, got {len(dataset_files)}"
    assert set(cache_files) == set(dataset_files), (
        f"Mismatch: {set(cache_files) ^ set(dataset_files)}"
    )


def test_total_sample_count(all_samples):
    assert len(all_samples) == 559, f"Expected 559 samples, got {len(all_samples)}"


def test_base_scenario_count(base_scenario_names):
    assert len(base_scenario_names) == 19, (
        f"Expected 19 base scenarios, got {len(base_scenario_names)}: {base_scenario_names}"
    )


def test_cache_structure(all_caches):
    for fname, cache in all_caches.items():
        assert "_meta" in cache, f"{fname}: missing _meta"
        assert "case_type" in cache["_meta"], f"{fname}: _meta missing case_type"
        assert "kubectl" in cache, f"{fname}: missing kubectl section"
        assert isinstance(cache["kubectl"], dict), f"{fname}: kubectl is not a dict"


def test_dataset_structure(all_datasets):
    for fname, ds in all_datasets.items():
        assert isinstance(ds, list), f"{fname}: dataset is not a list"
        assert len(ds) > 0, f"{fname}: dataset is empty"
        for i, entry in enumerate(ds):
            assert "id" in entry, f"{fname}[{i}]: missing id"
            assert "input" in entry, f"{fname}[{i}]: missing input"
            assert "alert_text" in entry["input"], f"{fname}[{i}]: missing input.alert_text"
            assert "expected_output" in entry, f"{fname}[{i}]: missing expected_output"
            assert "metadata" in entry, f"{fname}[{i}]: missing metadata"


def test_cache_meta_case_type(all_caches):
    for fname, cache in all_caches.items():
        ct = cache["_meta"]["case_type"]
        if "--" not in fname:
            assert ct == "real", f"{fname}: base cache has case_type '{ct}', expected 'real'"
        else:
            var_part = fname.replace(".json", "").split("--")[1]
            var_base = var_part.split("-s")[0] if "-s" in var_part else var_part
            assert var_base in ct, (
                f"{fname}: case_type '{ct}' does not contain variation base '{var_base}'"
            )


def test_no_empty_files(all_caches, all_datasets):
    for fname, cache in all_caches.items():
        assert len(cache.get("kubectl", {})) > 0, f"{fname}: kubectl section is empty"

    for fname, ds in all_datasets.items():
        assert len(ds) > 0, f"{fname}: dataset is empty"
        for entry in ds:
            assert entry["input"]["alert_text"].strip(), (
                f"{fname}/{entry['id']}: alert_text is empty"
            )


def test_all_ids_unique(all_samples):
    ids = [s["id"] for s in all_samples]
    dupes = [k for k, v in Counter(ids).items() if v > 1]
    assert len(dupes) == 0, f"Duplicate IDs found: {dupes}"
    assert len(set(ids)) == 559, f"Expected 559 unique IDs, got {len(set(ids))}"
