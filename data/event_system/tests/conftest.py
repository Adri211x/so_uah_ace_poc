"""Shared fixtures for the event_system test suite."""

import json
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache"
DATASET_DIR = ROOT / "data" / "datasets"
SPLITS_DIR = ROOT

ROOT_CAUSE_FAMILIES = {
    "invalid_container_command": "container_error",
    "broker_queue_full": "container_error",
    "memory_limit_exceeded": "resource_limit",
    "invalid_image_reference": "image_error",
    "port_mismatch": "config_error",
    "config_error": "config_error",
    "multiple_config_errors": "config_error",
    "false_alarm": "false_alarm",
    "payment_service_failure": "service_failure",
    "lock_not_released": "lock_error",
    "volume_not_found": "scheduling_error",
    "misconfigured_probes": "probe_error",
    "service_routing_mismatch": "routing_error",
    "invalid_node_selector": "scheduling_error",
    "high_response_time": "performance",
}

SYSTEM_NAMESPACES = frozenset([
    "kube-system", "kube-public", "kube-node-lease", "default",
    "cattle-system", "fleet-default", "fleet-local",
    "cattle-fleet-local-system", "cattle-fleet-system",
])

SPLIT_FILES = [
    "training_stratified.json",
    "training_leave_variation_out.json",
    "training_leave_scenario_out.json",
    "training_leave_family_out.json",
]


def extract_rc_key(expected_output) -> str:
    text = str(expected_output)
    text = re.sub(r"^(Root cause:\s*)+", "Root cause: ", text)
    m = re.match(r"Root cause:\s*(\S+)", text)
    return m.group(1) if m else "unknown"


def rc_family(rc_key: str) -> str:
    for prefix, family in ROOT_CAUSE_FAMILIES.items():
        if rc_key.startswith(prefix):
            return family
    return rc_key


# ── File listings ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def cache_files():
    return sorted(f for f in os.listdir(CACHE_DIR) if f.endswith(".json"))


@pytest.fixture(scope="session")
def dataset_files():
    return sorted(f for f in os.listdir(DATASET_DIR) if f.endswith(".json"))


@pytest.fixture(scope="session")
def base_scenario_names(cache_files):
    return sorted(f.replace(".json", "") for f in cache_files if "--" not in f)


# ── Loaded data ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def all_caches(cache_files):
    """Dict of {filename: parsed JSON} for every cache file."""
    out = {}
    for f in cache_files:
        with open(CACHE_DIR / f) as fh:
            out[f] = json.load(fh)
    return out


@pytest.fixture(scope="session")
def all_datasets(dataset_files):
    """Dict of {filename: parsed JSON list} for every dataset file."""
    out = {}
    for f in dataset_files:
        with open(DATASET_DIR / f) as fh:
            out[f] = json.load(fh)
    return out


@pytest.fixture(scope="session")
def all_samples(all_datasets):
    """Flat list of sample dicts with enriched fields."""
    samples = []
    for f, ds in sorted(all_datasets.items()):
        name = f.replace(".json", "")
        base_scenario = name.split("--")[0] if "--" in name else name
        var_tag = name.split("--")[1] if "--" in name else "real"
        var_base = var_tag.split("-s")[0] if "-s" in var_tag else var_tag
        for entry in ds:
            full_id = entry["id"]
            base_alert = full_id.split("--")[0] if "--" in full_id else full_id
            rk = extract_rc_key(entry.get("expected_output", ""))
            samples.append({
                "id": full_id,
                "file": f,
                "base_scenario": base_scenario,
                "base_alert": base_alert,
                "variation": var_tag,
                "variation_base": var_base,
                "root_cause_key": rk,
                "root_cause_family": rc_family(rk),
                "entry": entry,
            })
    return samples


# ── Splits ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def all_splits():
    """Dict of {filename: parsed JSON} for every training split file."""
    out = {}
    for f in SPLIT_FILES:
        path = SPLITS_DIR / f
        if path.exists():
            with open(path) as fh:
                out[f] = json.load(fh)
    return out
