"""T2: Variation correctness tests — validates each variation type's invariants."""

import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from conftest import CACHE_DIR, DATASET_DIR, SYSTEM_NAMESPACES


# ── Helpers ───────────────────────────────────────────────────────────────────

def _variation_files(all_caches, var_prefix):
    """Return cache entries whose variation starts with var_prefix (e.g. 'namespace_shifted')."""
    out = {}
    for fname, cache in all_caches.items():
        if "--" not in fname:
            continue
        var_part = fname.replace(".json", "").split("--")[1]
        var_base = var_part.split("-s")[0] if "-s" in var_part else var_part
        if var_base == var_prefix:
            out[fname] = cache
    return out


def _real_cache(fname, all_caches):
    """Get the real (base) cache for a variation file."""
    base = fname.split("--")[0] + ".json"
    return all_caches.get(base)


def _real_dataset(fname, all_datasets):
    """Get the real (base) dataset for a variation file."""
    base = fname.split("--")[0] + ".json"
    return all_datasets.get(base)


# ── namespace_shifted ─────────────────────────────────────────────────────────

class TestNamespaceShifted:

    def test_has_mappings_in_meta(self, all_caches):
        for fname, cache in _variation_files(all_caches, "namespace_shifted").items():
            meta = cache["_meta"]
            assert "mappings" in meta, f"{fname}: no mappings in _meta"
            ns_map = meta["mappings"].get("namespaces", {})
            assert len(ns_map) > 0, f"{fname}: namespaces mapping is empty"

    def test_old_ns_not_in_kubectl(self, all_caches):
        for fname, cache in _variation_files(all_caches, "namespace_shifted").items():
            ns_map = cache["_meta"]["mappings"].get("namespaces", {})
            kubectl_text = json.dumps(cache.get("kubectl", {}))
            for old_ns, new_ns in ns_map.items():
                if old_ns in SYSTEM_NAMESPACES:
                    continue
                cleaned = kubectl_text.replace(new_ns, "")
                assert old_ns not in cleaned, (
                    f"{fname}: old namespace '{old_ns}' still found in kubectl data"
                )

    def test_new_ns_in_kubectl(self, all_caches):
        for fname, cache in _variation_files(all_caches, "namespace_shifted").items():
            ns_map = cache["_meta"]["mappings"].get("namespaces", {})
            kubectl_text = json.dumps(cache.get("kubectl", {}))
            found_any = any(new_ns in kubectl_text for new_ns in ns_map.values())
            assert found_any, (
                f"{fname}: none of the new namespaces found in kubectl data"
            )

    def test_ground_truth_adapted(self, all_caches, all_datasets):
        for fname, cache in _variation_files(all_caches, "namespace_shifted").items():
            ds_fname = fname.replace(".json", "").split("--")[0] + "--" + fname.replace(".json", "").split("--")[1] + ".json"
            ds = all_datasets.get(ds_fname, [])
            ns_map = cache["_meta"]["mappings"].get("namespaces", {})
            if not ns_map or not ds:
                continue
            for entry in ds:
                eo = str(entry.get("expected_output", ""))
                for old_ns, new_ns in ns_map.items():
                    if old_ns in SYSTEM_NAMESPACES:
                        continue
                    cleaned = eo.replace(new_ns, "")
                    if old_ns in cleaned:
                        pytest.fail(
                            f"{ds_fname}/{entry['id']}: old ns '{old_ns}' in expected_output"
                        )


# ── noise_injected ────────────────────────────────────────────────────────────

class TestNoiseInjected:

    def test_has_noise_namespaces(self, all_caches):
        for fname, cache in _variation_files(all_caches, "noise_injected").items():
            nn = cache["_meta"].get("noise_namespaces", [])
            assert isinstance(nn, list) and len(nn) > 0, (
                f"{fname}: noise_namespaces missing or empty"
            )

    def test_noise_pods_are_running(self, all_caches):
        for fname, cache in _variation_files(all_caches, "noise_injected").items():
            noise_ns = cache["_meta"].get("noise_namespaces", [])
            kubectl = cache.get("kubectl", {})
            for ns in noise_ns:
                pods_key = f"kubectl get pods -n {ns} -o wide"
                if pods_key not in kubectl:
                    continue
                output = kubectl[pods_key]
                assert "Running" in output, (
                    f"{fname}: noise namespace '{ns}' has no Running pods"
                )

    def test_ground_truth_unchanged(self, all_caches, all_datasets):
        for fname in _variation_files(all_caches, "noise_injected"):
            ds = all_datasets.get(fname, [])
            real_ds = _real_dataset(fname, all_datasets)
            if not ds or not real_ds:
                continue
            real_eo_map = {e["id"].split("--")[0]: str(e["expected_output"]) for e in real_ds}
            for entry in ds:
                base_id = entry["id"].split("--")[0]
                real_eo = real_eo_map.get(base_id, "")
                assert str(entry["expected_output"]) == real_eo, (
                    f"{fname}/{entry['id']}: expected_output differs from real"
                )

    def test_alert_text_unchanged(self, all_caches, all_datasets):
        for fname in _variation_files(all_caches, "noise_injected"):
            ds = all_datasets.get(fname, [])
            real_ds = _real_dataset(fname, all_datasets)
            if not ds or not real_ds:
                continue
            real_alert_map = {e["id"].split("--")[0]: e["input"]["alert_text"] for e in real_ds}
            for entry in ds:
                base_id = entry["id"].split("--")[0]
                assert entry["input"]["alert_text"] == real_alert_map.get(base_id, ""), (
                    f"{fname}/{entry['id']}: alert_text differs from real"
                )


# ── alert_rephrased ───────────────────────────────────────────────────────────

class TestAlertRephrased:

    def test_alert_text_different(self, all_caches, all_datasets):
        for fname in _variation_files(all_caches, "alert_rephrased"):
            ds = all_datasets.get(fname, [])
            real_ds = _real_dataset(fname, all_datasets)
            if not ds or not real_ds:
                continue
            real_alert_map = {e["id"].split("--")[0]: e["input"]["alert_text"] for e in real_ds}
            for entry in ds:
                base_id = entry["id"].split("--")[0]
                real_alert = real_alert_map.get(base_id, "")
                assert entry["input"]["alert_text"] != real_alert, (
                    f"{fname}/{entry['id']}: alert_text is identical to real"
                )

    def test_ground_truth_unchanged(self, all_caches, all_datasets):
        for fname in _variation_files(all_caches, "alert_rephrased"):
            ds = all_datasets.get(fname, [])
            real_ds = _real_dataset(fname, all_datasets)
            if not ds or not real_ds:
                continue
            real_eo_map = {e["id"].split("--")[0]: str(e["expected_output"]) for e in real_ds}
            for entry in ds:
                base_id = entry["id"].split("--")[0]
                assert str(entry["expected_output"]) == real_eo_map.get(base_id, ""), (
                    f"{fname}/{entry['id']}: expected_output differs from real"
                )

    def test_cache_kubectl_identical_to_real(self, all_caches):
        for fname, cache in _variation_files(all_caches, "alert_rephrased").items():
            real_cache = _real_cache(fname, all_caches)
            if real_cache is None:
                continue
            assert cache.get("kubectl") == real_cache.get("kubectl"), (
                f"{fname}: kubectl section differs from real cache"
            )


# ── Multi-seed ────────────────────────────────────────────────────────────────

class TestMultiSeed:

    def test_different_seeds_produce_different_outputs(self, all_caches):
        ns_files = _variation_files(all_caches, "namespace_shifted")
        by_scenario = defaultdict(dict)
        for fname, cache in ns_files.items():
            base = fname.split("--")[0]
            var_part = fname.replace(".json", "").split("--")[1]
            by_scenario[base][var_part] = cache["_meta"].get("mappings", {})

        for scenario, variants in by_scenario.items():
            keys = list(variants.keys())
            if len(keys) < 2:
                continue
            first = variants[keys[0]]
            second = variants[keys[1]]
            assert first != second, (
                f"{scenario}: seeds '{keys[0]}' and '{keys[1]}' produced identical mappings"
            )

    def test_seed_count_per_variation_type(self, cache_files):
        var_counts = defaultdict(lambda: defaultdict(int))
        for f in cache_files:
            if "--" not in f:
                continue
            base = f.split("--")[0]
            var_part = f.replace(".json", "").split("--")[1]
            var_base = var_part.split("-s")[0] if "-s" in var_part else var_part
            var_counts[var_base][base] += 1

        for var_type, scenarios in var_counts.items():
            for scenario, count in scenarios.items():
                assert count == 4, (
                    f"{var_type}/{scenario}: expected 4 seeds, got {count}"
                )
