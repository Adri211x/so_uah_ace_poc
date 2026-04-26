"""
Build multiple training split configurations for the event_system dataset.

Generates:
  training_stratified.json        - 80/20 stratified random split by root_cause_family
  training_leave_variation_out.json - 4 folds, each holds out one variation type
  training_leave_scenario_out.json  - 19 folds, each holds out one base scenario
  training_leave_family_out.json    - N folds, each holds out one root_cause_family

All splits prevent data leakage: variations of the same base alert always
move together.
"""

import json
import os
import random
import re
from collections import defaultdict
from pathlib import Path

DATASET_DIR = Path("data/datasets")
SEED = 2026
TRAIN_RATIO = 0.80

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


def extract_rc_key(expected_output) -> str:
    if isinstance(expected_output, dict):
        text = expected_output.get("root_cause", expected_output.get("summary", ""))
    else:
        text = str(expected_output)
    text = re.sub(r"^(Root cause:\s*)+", "Root cause: ", text)
    m = re.match(r"Root cause:\s*(\S+)", text)
    return m.group(1) if m else "unknown"


def rc_family(rc_key: str) -> str:
    for prefix, family in ROOT_CAUSE_FAMILIES.items():
        if rc_key.startswith(prefix):
            return family
    return rc_key


def load_all_samples():
    samples = []
    for f in sorted(os.listdir(DATASET_DIR)):
        if not f.endswith(".json"):
            continue
        with open(DATASET_DIR / f) as fh:
            ds = json.load(fh)
        name = f.replace(".json", "")
        base_scenario = name.split("--")[0] if "--" in name else name
        var_tag = name.split("--")[1] if "--" in name else "real"
        var_base = var_tag.split("-s")[0] if "-s" in var_tag else var_tag

        for entry in ds:
            full_id = entry["id"]
            base_alert = full_id.split("--")[0] if "--" in full_id else full_id
            rc_key = extract_rc_key(entry.get("expected_output", ""))
            samples.append({
                "id": full_id,
                "file": f,
                "base_scenario": base_scenario,
                "base_alert": base_alert,
                "variation": var_tag,
                "variation_base": var_base,
                "root_cause_key": rc_key,
                "root_cause_family": rc_family(rc_key),
            })
    return samples


def dist(items, key):
    c = defaultdict(int)
    for i in items:
        c[i[key]] += 1
    return dict(sorted(c.items()))


def fold_stats(assignments: list) -> dict:
    train = [a for a in assignments if a["split"] == "train"]
    test = [a for a in assignments if a["split"] == "test"]
    total = len(assignments)
    return {
        "total_samples": total,
        "train_samples": len(train),
        "test_samples": len(test),
        "train_ratio": round(len(train) / total, 4) if total else 0,
        "test_ratio": round(len(test) / total, 4) if total else 0,
        "train_by_variation_base": dist(train, "variation_base"),
        "test_by_variation_base": dist(test, "variation_base"),
        "train_by_root_cause_family": dist(train, "root_cause_family"),
        "test_by_root_cause_family": dist(test, "root_cause_family"),
        "train_by_scenario": dist(train, "base_scenario"),
        "test_by_scenario": dist(test, "base_scenario"),
    }


def make_assignment(sample: dict, split: str) -> dict:
    return {
        "id": sample["id"],
        "file": sample["file"],
        "split": split,
        "base_scenario": sample["base_scenario"],
        "variation": sample["variation"],
        "variation_base": sample["variation_base"],
        "root_cause_family": sample["root_cause_family"],
        "root_cause_key": sample["root_cause_key"],
    }


# ---------------------------------------------------------------------------
# 1. Stratified 80/20
# ---------------------------------------------------------------------------
def build_stratified(samples: list) -> dict:
    rng = random.Random(SEED)

    base_alert_info = {}
    for s in samples:
        key = s["base_alert"]
        if key not in base_alert_info:
            base_alert_info[key] = {
                "family": s["root_cause_family"],
                "scenario": s["base_scenario"],
            }

    families = defaultdict(list)
    for key, info in base_alert_info.items():
        families[info["family"]].append(key)

    train_keys, test_keys = [], []
    family_test = {}

    for fam, keys in sorted(families.items()):
        rng.shuffle(keys)
        n = len(keys)
        if n == 1:
            train_keys.extend(keys)
            family_test[fam] = []
        elif n <= 4:
            test_keys.append(keys[0])
            train_keys.extend(keys[1:])
            family_test[fam] = [keys[0]]
        else:
            n_test = max(1, int(n * (1 - TRAIN_RATIO)))
            test_keys.extend(keys[:n_test])
            train_keys.extend(keys[n_test:])
            family_test[fam] = list(keys[:n_test])

    total = len(train_keys) + len(test_keys)
    target_test = round(total * (1 - TRAIN_RATIO))
    while len(test_keys) > target_test:
        largest = max(
            (f for f, t in family_test.items() if len(t) > 1),
            key=lambda f: len(family_test[f]),
            default=None,
        )
        if largest is None:
            break
        moved = family_test[largest].pop()
        test_keys.remove(moved)
        train_keys.append(moved)

    train_set, test_set = set(train_keys), set(test_keys)
    assignments = [
        make_assignment(s, "train" if s["base_alert"] in train_set else "test")
        for s in samples
    ]

    train_only_families = [
        fam for fam in dist([a for a in assignments if a["split"] == "train"], "root_cause_family")
        if fam not in dist([a for a in assignments if a["split"] == "test"], "root_cause_family")
    ]

    return {
        "split_type": "stratified",
        "description": (
            "Stratified 80/20 train/test split. "
            "Unit: base alert (all variations grouped). "
            "Stratified by root_cause_family. "
            "Singleton families kept in train; require cross-validation for evaluation."
        ),
        "seed": SEED,
        "num_folds": 1,
        "train_only_families": train_only_families,
        "statistics": fold_stats(assignments),
        "assignments": assignments,
    }


# ---------------------------------------------------------------------------
# 2. Leave-one-variation-out (LOVO)
# ---------------------------------------------------------------------------
def build_leave_variation_out(samples: list) -> dict:
    variation_bases = sorted(set(s["variation_base"] for s in samples))

    folds = []
    for held_out in variation_bases:
        assignments = [
            make_assignment(s, "test" if s["variation_base"] == held_out else "train")
            for s in samples
        ]
        folds.append({
            "fold_name": f"hold_out_{held_out}",
            "held_out_variation": held_out,
            "statistics": fold_stats(assignments),
            "assignments": assignments,
        })

    return {
        "split_type": "leave_variation_out",
        "description": (
            "Leave-one-variation-type-out cross-validation. "
            "Each fold holds out all samples of one variation type (across all seeds). "
            "Tests generalization across variation types: can the agent handle "
            "namespace shifts if only trained on noise-injected and rephrased, etc."
        ),
        "num_folds": len(folds),
        "variation_types": variation_bases,
        "folds": folds,
    }


# ---------------------------------------------------------------------------
# 3. Leave-one-scenario-out (LOSO)
# ---------------------------------------------------------------------------
def build_leave_scenario_out(samples: list) -> dict:
    scenarios = sorted(set(s["base_scenario"] for s in samples))

    folds = []
    for held_out in scenarios:
        assignments = [
            make_assignment(s, "test" if s["base_scenario"] == held_out else "train")
            for s in samples
        ]
        stats = fold_stats(assignments)
        test_families = list(stats["test_by_root_cause_family"].keys())
        folds.append({
            "fold_name": f"hold_out_{held_out}",
            "held_out_scenario": held_out,
            "test_root_cause_families": test_families,
            "statistics": stats,
            "assignments": assignments,
        })

    return {
        "split_type": "leave_scenario_out",
        "description": (
            "Leave-one-scenario-out cross-validation. "
            "Each fold holds out all alerts (and their variations) from one base scenario. "
            "Tests generalization to entirely unseen infrastructure failure patterns."
        ),
        "num_folds": len(folds),
        "scenarios": scenarios,
        "folds": folds,
    }


# ---------------------------------------------------------------------------
# 4. Leave-one-family-out (LOFO)
# ---------------------------------------------------------------------------
def build_leave_family_out(samples: list) -> dict:
    families = sorted(set(s["root_cause_family"] for s in samples))

    folds = []
    for held_out in families:
        assignments = [
            make_assignment(s, "test" if s["root_cause_family"] == held_out else "train")
            for s in samples
        ]
        stats = fold_stats(assignments)
        folds.append({
            "fold_name": f"hold_out_{held_out}",
            "held_out_family": held_out,
            "test_scenarios": list(stats["test_by_scenario"].keys()),
            "statistics": stats,
            "assignments": assignments,
        })

    return {
        "split_type": "leave_family_out",
        "description": (
            "Leave-one-root-cause-family-out cross-validation. "
            "Each fold holds out all alerts whose root cause belongs to one family. "
            "Tests zero-shot generalization: can the agent diagnose a root cause "
            "category it has never seen in training."
        ),
        "num_folds": len(folds),
        "families": families,
        "folds": folds,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def write_split(data: dict, path: Path):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

    n_folds = data["num_folds"]
    stype = data["split_type"]

    if n_folds == 1:
        s = data["statistics"]
        print(f"  {stype}: {s['train_samples']} train / {s['test_samples']} test "
              f"({s['train_ratio']*100:.1f}/{s['test_ratio']*100:.1f}%)")
    else:
        fold_list = data.get("folds", [])
        test_sizes = [f["statistics"]["test_samples"] for f in fold_list]
        print(f"  {stype}: {n_folds} folds, test size range [{min(test_sizes)}-{max(test_sizes)}]")
        for fold in fold_list:
            name = fold["fold_name"]
            s = fold["statistics"]
            print(f"    {name}: {s['train_samples']} train / {s['test_samples']} test")


def main():
    samples = load_all_samples()
    print(f"Loaded {len(samples)} samples from {len(set(s['base_scenario'] for s in samples))} scenarios\n")

    stratified = build_stratified(samples)
    write_split(stratified, Path("training_stratified.json"))

    lovo = build_leave_variation_out(samples)
    write_split(lovo, Path("training_leave_variation_out.json"))

    loso = build_leave_scenario_out(samples)
    write_split(loso, Path("training_leave_scenario_out.json"))

    lofo = build_leave_family_out(samples)
    write_split(lofo, Path("training_leave_family_out.json"))

    print(f"\nDone. 4 split files written.")


if __name__ == "__main__":
    main()
