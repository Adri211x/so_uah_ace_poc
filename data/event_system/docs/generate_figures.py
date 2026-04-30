"""Generate statistical figures for dataset documentation (dataset.md, Figures 1-11)."""

import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

DATASET_DIR = Path("data/datasets")
FIGURES_DIR = Path("docs/figures")
FIGURES_DIR.mkdir(parents=True, exist_ok=True)

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

PALETTE = {
    "real": "#2d3436",
    "namespace_shifted": "#0984e3",
    "noise_injected": "#00b894",
    "alert_rephrased": "#e17055",
    "train": "#0984e3",
    "test": "#d63031",
}

FAMILY_COLORS = {
    "config_error": "#636e72",
    "container_error": "#d63031",
    "false_alarm": "#00b894",
    "image_error": "#e17055",
    "lock_error": "#6c5ce7",
    "performance": "#fdcb6e",
    "probe_error": "#00cec9",
    "resource_limit": "#e84393",
    "routing_error": "#0984e3",
    "scheduling_error": "#fd79a8",
    "service_failure": "#a29bfe",
}

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
    "axes.labelsize": 10,
    "figure.dpi": 150,
    "savefig.dpi": 150,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.15,
})


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
        ds = json.load(open(DATASET_DIR / f))
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


def short_scenario(s: str) -> str:
    return s.replace("kubernetes-", "").replace("otel-demo-paymentFailure", "otel-payment")


# ─── Figure 1: Dataset composition (stacked bar) ────────────────────────────

def fig_dataset_composition(samples):
    scenarios = sorted(set(s["base_scenario"] for s in samples))
    var_types = ["real", "namespace_shifted", "noise_injected", "alert_rephrased"]

    counts = defaultdict(lambda: defaultdict(int))
    for s in samples:
        counts[s["base_scenario"]][s["variation_base"]] += 1

    fig, ax = plt.subplots(figsize=(12, 5))
    x = np.arange(len(scenarios))
    width = 0.65
    bottom = np.zeros(len(scenarios))

    for vt in var_types:
        vals = [counts[sc][vt] for sc in scenarios]
        ax.bar(x, vals, width, bottom=bottom, label=vt.replace("_", " "),
               color=PALETTE[vt], edgecolor="white", linewidth=0.5)
        bottom += np.array(vals)

    ax.set_xticks(x)
    ax.set_xticklabels([short_scenario(s) for s in scenarios], rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Number of samples")
    ax.set_title("Figure 1. Dataset Composition by Scenario and Variation Type")
    ax.legend(loc="upper right", fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    for i, sc in enumerate(scenarios):
        total = sum(counts[sc].values())
        ax.text(i, total + 0.5, str(total), ha="center", va="bottom", fontsize=7, color="#555")

    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "fig1_dataset_composition.png")
    plt.close(fig)
    print("  fig1_dataset_composition.png")


# ─── Figure 2: Root cause family distribution ────────────────────────────────

def fig_root_cause_distribution(samples):
    family_counts = Counter(s["root_cause_family"] for s in samples)
    families = sorted(family_counts, key=lambda f: -family_counts[f])
    counts = [family_counts[f] for f in families]
    colors = [FAMILY_COLORS.get(f, "#b2bec3") for f in families]

    base_alert_counts = Counter()
    seen = set()
    for s in samples:
        if s["base_alert"] not in seen:
            base_alert_counts[s["root_cause_family"]] += 1
            seen.add(s["base_alert"])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    # Bar chart
    y = np.arange(len(families))
    ax1.barh(y, counts, color=colors, edgecolor="white", linewidth=0.5)
    ax1.set_yticks(y)
    ax1.set_yticklabels([f.replace("_", " ") for f in families], fontsize=9)
    ax1.set_xlabel("Number of samples")
    ax1.set_title("(a) Samples per Root Cause Family")
    ax1.invert_yaxis()
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)

    for i, (cnt, fam) in enumerate(zip(counts, families)):
        ba = base_alert_counts[fam]
        ax1.text(cnt + 2, i, f"{cnt} ({ba} base)", va="center", fontsize=8, color="#555")

    # Pie chart
    wedges, texts, autotexts = ax2.pie(
        counts, labels=None, autopct=lambda p: f"{p:.0f}%" if p > 4 else "",
        colors=colors, startangle=90, pctdistance=0.8,
        wedgeprops={"edgecolor": "white", "linewidth": 1.5},
    )
    for t in autotexts:
        t.set_fontsize(8)
    ax2.set_title("(b) Proportion")

    ax2.legend(
        [f.replace("_", " ") for f in families],
        loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=8,
    )

    fig.suptitle("Figure 2. Root Cause Family Distribution", fontweight="bold", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(FIGURES_DIR / "fig2_root_cause_distribution.png")
    plt.close(fig)
    print("  fig2_root_cause_distribution.png")


# ─── Figure 3: Stratified split ──────────────────────────────────────────────

def fig_stratified_split():
    data = json.load(open("training_stratified.json"))
    stats = data["statistics"]

    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))

    # (a) Overall ratio
    ax = axes[0]
    sizes = [stats["train_samples"], stats["test_samples"]]
    ax.pie(sizes, labels=["Train", "Test"], autopct="%1.1f%%",
           colors=[PALETTE["train"], PALETTE["test"]],
           startangle=90, wedgeprops={"edgecolor": "white", "linewidth": 2},
           textprops={"fontsize": 11, "fontweight": "bold"})
    ax.set_title(f"(a) Overall Split\n{sizes[0]} train / {sizes[1]} test", fontsize=10)

    # (b) By variation type
    ax = axes[1]
    var_types = sorted(set(list(stats["train_by_variation_base"]) + list(stats["test_by_variation_base"])))
    train_vals = [stats["train_by_variation_base"].get(v, 0) for v in var_types]
    test_vals = [stats["test_by_variation_base"].get(v, 0) for v in var_types]
    y = np.arange(len(var_types))
    h = 0.35
    ax.barh(y - h/2, train_vals, h, label="Train", color=PALETTE["train"], edgecolor="white")
    ax.barh(y + h/2, test_vals, h, label="Test", color=PALETTE["test"], edgecolor="white")
    ax.set_yticks(y)
    ax.set_yticklabels([v.replace("_", " ") for v in var_types], fontsize=8)
    ax.set_xlabel("Samples")
    ax.set_title("(b) By Variation Type", fontsize=10)
    ax.legend(fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # (c) By root cause family
    ax = axes[2]
    all_fams = sorted(set(list(stats["train_by_root_cause_family"]) + list(stats["test_by_root_cause_family"])))
    train_rc = [stats["train_by_root_cause_family"].get(f, 0) for f in all_fams]
    test_rc = [stats["test_by_root_cause_family"].get(f, 0) for f in all_fams]
    y = np.arange(len(all_fams))
    ax.barh(y - h/2, train_rc, h, label="Train", color=PALETTE["train"], edgecolor="white")
    ax.barh(y + h/2, test_rc, h, label="Test", color=PALETTE["test"], edgecolor="white")
    ax.set_yticks(y)
    ax.set_yticklabels([f.replace("_", " ") for f in all_fams], fontsize=8)
    ax.set_xlabel("Samples")
    ax.set_title("(c) By Root Cause Family", fontsize=10)
    ax.legend(fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Mark train-only families
    for i, fam in enumerate(all_fams):
        if stats["test_by_root_cause_family"].get(fam, 0) == 0:
            ax.text(train_rc[i] + 2, i, "train only", fontsize=7, color="#d63031", va="center", style="italic")

    fig.suptitle("Figure 3. Stratified Split (80/20)", fontweight="bold", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(FIGURES_DIR / "fig3_stratified_split.png")
    plt.close(fig)
    print("  fig3_stratified_split.png")


# ─── Figure 4: Leave-variation-out ────────────────────────────────────────────

def fig_leave_variation_out():
    data = json.load(open("training_leave_variation_out.json"))
    folds = data["folds"]

    fig, ax = plt.subplots(figsize=(10, 4))

    fold_names = [f["held_out_variation"].replace("_", " ") for f in folds]
    train_sizes = [f["statistics"]["train_samples"] for f in folds]
    test_sizes = [f["statistics"]["test_samples"] for f in folds]

    y = np.arange(len(fold_names))
    h = 0.35
    bars_train = ax.barh(y - h/2, train_sizes, h, label="Train", color=PALETTE["train"], edgecolor="white")
    bars_test = ax.barh(y + h/2, test_sizes, h, label="Test (held out)", color=PALETTE["test"], edgecolor="white")

    for i, (tr, te) in enumerate(zip(train_sizes, test_sizes)):
        ax.text(tr + 3, i - h/2, str(tr), va="center", fontsize=8, color=PALETTE["train"])
        ax.text(te + 3, i + h/2, str(te), va="center", fontsize=8, color=PALETTE["test"])

    ax.set_yticks(y)
    ax.set_yticklabels(fold_names, fontsize=9)
    ax.set_xlabel("Number of samples")
    ax.set_title("Figure 4. Leave-One-Variation-Out Cross-Validation (4 Folds)")
    ax.legend(loc="lower right", fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "fig4_leave_variation_out.png")
    plt.close(fig)
    print("  fig4_leave_variation_out.png")


# ─── Figure 5: Leave-scenario-out heatmap ────────────────────────────────────

def fig_leave_scenario_out():
    data = json.load(open("training_leave_scenario_out.json"))
    folds = data["folds"]

    scenarios = [short_scenario(f["held_out_scenario"]) for f in folds]
    test_sizes = [f["statistics"]["test_samples"] for f in folds]
    train_sizes = [f["statistics"]["train_samples"] for f in folds]

    # Heatmap: which RC families are in test for each fold?
    all_fams = sorted(set(
        fam for f in folds
        for fam in f["statistics"]["test_by_root_cause_family"]
    ))

    matrix = np.zeros((len(folds), len(all_fams)))
    for i, fold in enumerate(folds):
        for j, fam in enumerate(all_fams):
            matrix[i, j] = fold["statistics"]["test_by_root_cause_family"].get(fam, 0)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 7),
                                     gridspec_kw={"width_ratios": [1, 2.5]})

    # (a) Test set sizes
    colors_bar = ["#d63031" if t <= 20 else "#e17055" if t <= 40 else "#0984e3" for t in test_sizes]
    ax1.barh(np.arange(len(scenarios)), test_sizes, color=colors_bar, edgecolor="white")
    ax1.set_yticks(np.arange(len(scenarios)))
    ax1.set_yticklabels(scenarios, fontsize=8)
    ax1.set_xlabel("Test samples")
    ax1.set_title("(a) Test Set Size per Fold", fontsize=10)
    ax1.invert_yaxis()
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)

    for i, v in enumerate(test_sizes):
        ax1.text(v + 0.5, i, str(v), va="center", fontsize=7, color="#555")

    # (b) RC family coverage heatmap
    cmap = plt.cm.Blues
    cmap.set_under("white")
    im = ax2.imshow(matrix, cmap=cmap, aspect="auto", vmin=0.5)
    ax2.set_xticks(np.arange(len(all_fams)))
    ax2.set_xticklabels([f.replace("_", "\n") for f in all_fams], fontsize=7, rotation=0, ha="center")
    ax2.set_yticks(np.arange(len(scenarios)))
    ax2.set_yticklabels(scenarios, fontsize=8)
    ax2.set_title("(b) Root Cause Families in Test Set", fontsize=10)

    for i in range(len(scenarios)):
        for j in range(len(all_fams)):
            v = int(matrix[i, j])
            if v > 0:
                ax2.text(j, i, str(v), ha="center", va="center", fontsize=7,
                         color="white" if v > 20 else "black")

    fig.suptitle("Figure 5. Leave-One-Scenario-Out Cross-Validation (19 Folds)",
                 fontweight="bold", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(FIGURES_DIR / "fig5_leave_scenario_out.png")
    plt.close(fig)
    print("  fig5_leave_scenario_out.png")


# ─── Figure 6: Leave-family-out ───────────────────────────────────────────────

def fig_leave_family_out():
    data = json.load(open("training_leave_family_out.json"))
    folds = data["folds"]

    families = [f["held_out_family"].replace("_", " ") for f in folds]
    test_sizes = [f["statistics"]["test_samples"] for f in folds]
    train_sizes = [f["statistics"]["train_samples"] for f in folds]
    test_scenarios = [len(f["test_scenarios"]) for f in folds]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    y = np.arange(len(families))
    h = 0.35

    # (a) Train/test sizes
    ax1.barh(y - h/2, train_sizes, h, label="Train", color=PALETTE["train"], edgecolor="white")
    ax1.barh(y + h/2, test_sizes, h, label="Test (held out)", color=PALETTE["test"], edgecolor="white")
    ax1.set_yticks(y)
    ax1.set_yticklabels(families, fontsize=9)
    ax1.set_xlabel("Samples")
    ax1.set_title("(a) Split Sizes per Fold", fontsize=10)
    ax1.legend(fontsize=8)
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)

    for i, te in enumerate(test_sizes):
        pct = te / (train_sizes[i] + te) * 100
        ax1.text(te + 3, i + h/2, f"{te} ({pct:.0f}%)", va="center", fontsize=7, color=PALETTE["test"])

    # (b) Number of scenarios in test
    fam_colors = [FAMILY_COLORS.get(f["held_out_family"], "#b2bec3") for f in folds]
    ax2.barh(y, test_scenarios, color=fam_colors, edgecolor="white")
    ax2.set_yticks(y)
    ax2.set_yticklabels(families, fontsize=9)
    ax2.set_xlabel("Number of scenarios in test")
    ax2.set_title("(b) Scenario Coverage in Test", fontsize=10)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)

    for i, v in enumerate(test_scenarios):
        sc_names = ", ".join(short_scenario(s) for s in folds[i]["test_scenarios"])
        ax2.text(v + 0.1, i, sc_names, va="center", fontsize=6.5, color="#555")

    fig.suptitle("Figure 6. Leave-One-Root-Cause-Family-Out Cross-Validation (11 Folds)",
                 fontweight="bold", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(FIGURES_DIR / "fig6_leave_family_out.png")
    plt.close(fig)
    print("  fig6_leave_family_out.png")


# ─── Figure 7: Variation diversity (samples per seed) ────────────────────────

def fig_variation_seeds(samples):
    var_types = ["namespace_shifted", "noise_injected", "alert_rephrased"]
    seeds = ["(seed 42)", "s500", "s800", "s1200"]

    fig, ax = plt.subplots(figsize=(8, 4))

    data_matrix = []
    for vt in var_types:
        row = []
        for seed_label in seeds:
            if seed_label == "(seed 42)":
                count = sum(1 for s in samples if s["variation"] == vt)
            else:
                count = sum(1 for s in samples if s["variation"] == f"{vt}-{seed_label}")
            row.append(count)
        data_matrix.append(row)

    data_matrix = np.array(data_matrix)
    x = np.arange(len(seeds))
    width = 0.25

    for i, vt in enumerate(var_types):
        offset = (i - 1) * width
        bars = ax.bar(x + offset, data_matrix[i], width,
                      label=vt.replace("_", " "), color=PALETTE[vt],
                      edgecolor="white", linewidth=0.5)
        for bar, val in zip(bars, data_matrix[i]):
            if val > 0:
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
                        str(val), ha="center", fontsize=7, color="#555")

    ax.bar(x[-1] + width + 0.3, [0], width)  # spacer

    # Add real as separate annotation
    real_count = sum(1 for s in samples if s["variation_base"] == "real")
    ax.axhline(y=real_count, color=PALETTE["real"], linestyle="--", linewidth=1, alpha=0.7)
    ax.text(len(seeds) - 0.5, real_count + 1, f"real baseline = {real_count}",
            fontsize=8, color=PALETTE["real"], ha="right")

    ax.set_xticks(x)
    ax.set_xticklabels(seeds, fontsize=9)
    ax.set_ylabel("Number of samples")
    ax.set_xlabel("Random seed")
    ax.set_title("Figure 7. Sample Count per Variation Type and Random Seed")
    ax.legend(fontsize=8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "fig7_variation_seeds.png")
    plt.close(fig)
    print("  fig7_variation_seeds.png")


CACHE_DIR = Path("cache")


def _get_mcp_text(cache: dict) -> str:
    texts = []
    for key, val in cache.items():
        if key == "_meta":
            continue
        if isinstance(val, dict):
            for subval in val.values():
                if isinstance(subval, str):
                    texts.append(subval)
                elif isinstance(subval, dict):
                    for v in subval.values():
                        if isinstance(v, str):
                            texts.append(v)
        elif isinstance(val, str):
            texts.append(val)
    return "\n".join(texts)


# ─── Figure 8: Golden Entities Length and Levenshtein Viability ──────────────

def fig_golden_entity_lengths(samples):
    entities_by_scenario = {}
    for s in samples:
        if s["variation_base"] != "real":
            continue
        sc = s["base_scenario"]
        if sc in entities_by_scenario:
            continue
        ds = json.load(open(DATASET_DIR / s["file"]))
        ge = ds[0].get("golden_entities", [])
        if ge:
            entities_by_scenario[sc] = ge

    all_entities = [(sc, ge) for sc, ges in sorted(entities_by_scenario.items()) for ge in ges]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))

    # (a) Entity lengths with Levenshtein edit tolerance
    lengths = [len(e) for _, e in all_entities]
    labels = [f"{short_scenario(sc)}: {e}" for sc, e in all_entities]
    colors_bar = []
    for l in lengths:
        max_edits = l * 0.05
        if max_edits >= 1.0:
            colors_bar.append("#00b894")  # >= 1 edit tolerance
        elif max_edits >= 0.5:
            colors_bar.append("#fdcb6e")  # near-exact
        else:
            colors_bar.append("#d63031")  # exact match required

    y = np.arange(len(all_entities))
    ax1.barh(y, lengths, color=colors_bar, edgecolor="white", linewidth=0.5)
    ax1.set_yticks(y)
    ax1.set_yticklabels(labels, fontsize=5.5)
    ax1.set_xlabel("Entity length (chars)")
    ax1.set_title("(a) Entity Length & Levenshtein Tolerance", fontsize=10)
    ax1.invert_yaxis()
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)

    ax1.axvline(x=20, color="#555", linestyle="--", linewidth=0.8, alpha=0.6)
    ax1.text(20.5, len(all_entities) - 1, "1-edit\ntolerance", fontsize=7, color="#555", va="bottom")

    legend_handles = [
        mpatches.Patch(color="#00b894", label="\u2265 1 edit tolerance (len \u2265 20)"),
        mpatches.Patch(color="#fdcb6e", label="Near-exact match (10\u201319)"),
        mpatches.Patch(color="#d63031", label="Exact match required (< 10)"),
    ]
    ax1.legend(handles=legend_handles, fontsize=7, loc="lower right")

    for i, l in enumerate(lengths):
        edits = l * 0.05
        ax1.text(l + 0.3, i, f"{l}c / {edits:.1f}ed", fontsize=5.5, va="center", color="#555")

    # (b) Entity length distribution histogram
    ax2.hist(lengths, bins=range(8, max(lengths) + 3), color="#0984e3",
             edgecolor="white", linewidth=0.5, alpha=0.85)
    ax2.axvline(x=np.median(lengths), color="#d63031", linewidth=1.5, linestyle="--")
    ax2.text(np.median(lengths) + 0.3, ax2.get_ylim()[1] * 0.9,
             f"median = {np.median(lengths):.0f}c", fontsize=9, color="#d63031")
    ax2.set_xlabel("Entity length (chars)")
    ax2.set_ylabel("Frequency")
    ax2.set_title("(b) Length Distribution", fontsize=10)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)

    fig.suptitle("Figure 8. Golden Entity Lengths and Levenshtein Viability (\u03b8 = 0.95)",
                 fontweight="bold", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(FIGURES_DIR / "fig8_golden_entity_lengths.png")
    plt.close(fig)
    print("  fig8_golden_entity_lengths.png")


# ─── Figure 9: Discriminative Power Heatmap ──────────────────────────────────

def fig_golden_discriminative_power(samples):
    base_scenarios = sorted(set(s["base_scenario"] for s in samples))

    mcp_texts = {}
    entities_by_scenario = {}
    for sc in base_scenarios:
        cache_file = CACHE_DIR / f"{sc}.json"
        if cache_file.exists():
            cache = json.load(open(cache_file))
            mcp_texts[sc] = _get_mcp_text(cache).lower()

        ds_file = DATASET_DIR / f"{sc}.json"
        if ds_file.exists():
            ds = json.load(open(ds_file))
            entities_by_scenario[sc] = ds[0].get("golden_entities", [])

    all_entities_flat = []
    for sc in base_scenarios:
        for ge in entities_by_scenario.get(sc, []):
            all_entities_flat.append((sc, ge))

    n_ent = len(all_entities_flat)
    n_sc = len(base_scenarios)
    matrix = np.zeros((n_ent, n_sc))

    for i, (owner_sc, entity) in enumerate(all_entities_flat):
        el = entity.lower()
        for j, sc in enumerate(base_scenarios):
            mcp = mcp_texts.get(sc, "")
            count = mcp.count(el)
            if count > 0:
                matrix[i, j] = min(count, 50)  # cap for colormap

    fig, ax = plt.subplots(figsize=(14, 12))

    cmap = plt.cm.YlOrRd
    cmap.set_under("white")
    im = ax.imshow(matrix, cmap=cmap, aspect="auto", vmin=0.5, interpolation="nearest")

    ax.set_xticks(np.arange(n_sc))
    ax.set_xticklabels([short_scenario(sc) for sc in base_scenarios],
                        rotation=45, ha="right", fontsize=7)
    ax.set_yticks(np.arange(n_ent))
    ax.set_yticklabels([f"{short_scenario(sc)}: {ge}" for sc, ge in all_entities_flat],
                        fontsize=5.5)

    # Mark the "home" scenario for each entity
    for i, (owner_sc, _) in enumerate(all_entities_flat):
        j = base_scenarios.index(owner_sc)
        ax.plot(j, i, marker="s", color="blue", markersize=4, markeredgecolor="white",
                markeredgewidth=0.5, zorder=5)

    # Discriminative score annotation
    for i, (owner_sc, entity) in enumerate(all_entities_flat):
        other_count = sum(1 for j, sc in enumerate(base_scenarios)
                          if sc != owner_sc and matrix[i, j] > 0)
        disc = 1.0 / (1 + other_count)
        color = "#00b894" if disc >= 0.5 else ("#fdcb6e" if disc >= 0.1 else "#d63031")
        ax.text(n_sc + 0.3, i, f"D={disc:.2f} ({other_count} other)",
                fontsize=5, va="center", color=color)

    ax.set_title("Figure 9. Golden Entity Discriminative Power Across Scenarios",
                 fontweight="bold", fontsize=11)
    ax.set_xlabel("Scenario (MCP data source)")

    cb = fig.colorbar(im, ax=ax, shrink=0.6, label="Occurrences in MCP data (capped at 50)")

    legend_handles = [
        plt.Line2D([0], [0], marker="s", color="w", markerfacecolor="blue",
                   markersize=6, label="Home scenario"),
    ]
    ax.legend(handles=legend_handles, loc="upper right", fontsize=8)

    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "fig9_golden_discriminative_power.png")
    plt.close(fig)
    print("  fig9_golden_discriminative_power.png")


# ─── Figure 10: MCP Evidence Frequency per Scenario ─────────────────────────

def fig_golden_mcp_frequency(samples):
    base_scenarios = sorted(set(s["base_scenario"] for s in samples))

    data = []
    for sc in base_scenarios:
        cache_file = CACHE_DIR / f"{sc}.json"
        ds_file = DATASET_DIR / f"{sc}.json"
        if not cache_file.exists() or not ds_file.exists():
            continue

        cache = json.load(open(cache_file))
        mcp = _get_mcp_text(cache).lower()
        ds = json.load(open(ds_file))
        entities = ds[0].get("golden_entities", [])
        alerts = " ".join(e["input"]["alert_text"] for e in ds).lower()

        for idx, ge in enumerate(entities):
            freq = mcp.count(ge.lower())
            in_alert = ge.lower() in alerts
            data.append({
                "scenario": sc,
                "entity": ge,
                "idx": idx,
                "freq": freq,
                "in_alert": in_alert,
            })

    fig, ax = plt.subplots(figsize=(14, 7))

    scenarios_uniq = sorted(set(d["scenario"] for d in data))
    x = np.arange(len(scenarios_uniq))
    width = 0.25
    entity_colors = ["#0984e3", "#00b894", "#e17055"]
    entity_labels = ["E1 (evidence)", "E2 (diagnostic)", "E3 (root cause id)"]

    for idx in range(3):
        vals = []
        annots = []
        for sc in scenarios_uniq:
            entries = [d for d in data if d["scenario"] == sc and d["idx"] == idx]
            if entries:
                freq = entries[0]["freq"]
                vals.append(min(freq, 300))
                annots.append((entries[0]["entity"], freq))
            else:
                vals.append(0)
                annots.append(("", 0))

        bars = ax.bar(x + (idx - 1) * width, vals, width,
                      label=entity_labels[idx], color=entity_colors[idx],
                      edgecolor="white", linewidth=0.5)

        for i, (bar, (ent, freq)) in enumerate(zip(bars, annots)):
            if freq > 0:
                display = f"{freq}" if freq <= 300 else f"{freq}+"
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                        display, ha="center", fontsize=5.5, color="#555", rotation=90)

    ax.set_xticks(x)
    ax.set_xticklabels([short_scenario(sc) for sc in scenarios_uniq],
                        rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Occurrences in MCP tool outputs (capped at 300)")
    ax.set_title("Figure 10. Golden Entity Frequency in MCP Evidence Data")
    ax.legend(fontsize=8, loc="upper right")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Separate zero-frequency group
    zero_line = ax.axhline(y=0.5, color="#d63031", linewidth=0.5, linestyle=":", alpha=0.5)

    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "fig10_golden_mcp_frequency.png")
    plt.close(fig)
    print("  fig10_golden_mcp_frequency.png")


# ─── Figure 11: Golden Entity Taxonomy Summary ──────────────────────────────

def fig_golden_taxonomy(samples):
    base_scenarios = sorted(set(s["base_scenario"] for s in samples))

    scenario_families = {}
    all_entities = []
    for sc in base_scenarios:
        ds_file = DATASET_DIR / f"{sc}.json"
        if not ds_file.exists():
            continue
        ds = json.load(open(ds_file))
        entities = ds[0].get("golden_entities", [])
        rc = extract_rc_key(ds[0].get("expected_output", ""))
        fam = rc_family(rc)
        scenario_families[sc] = fam
        for ge in entities:
            all_entities.append({"scenario": sc, "entity": ge, "family": fam, "len": len(ge)})

    fig, axes = plt.subplots(2, 2, figsize=(13, 9))

    # (a) Entities per family
    ax = axes[0, 0]
    fam_counts = Counter(e["family"] for e in all_entities)
    fams_sorted = sorted(fam_counts, key=lambda f: -fam_counts[f])
    vals = [fam_counts[f] for f in fams_sorted]
    colors_fam = [FAMILY_COLORS.get(f, "#b2bec3") for f in fams_sorted]
    y = np.arange(len(fams_sorted))
    ax.barh(y, vals, color=colors_fam, edgecolor="white", linewidth=0.5)
    ax.set_yticks(y)
    ax.set_yticklabels([f.replace("_", " ") for f in fams_sorted], fontsize=8)
    ax.set_xlabel("Number of golden entities")
    ax.set_title("(a) Entities per Root Cause Family", fontsize=10)
    ax.invert_yaxis()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for i, v in enumerate(vals):
        n_sc = len(set(e["scenario"] for e in all_entities if e["family"] == fams_sorted[i]))
        ax.text(v + 0.1, i, f"{v} ({n_sc} sc.)", fontsize=7, va="center", color="#555")

    # (b) Entity length by family (box plot)
    ax = axes[0, 1]
    fam_lengths = defaultdict(list)
    for e in all_entities:
        fam_lengths[e["family"]].append(e["len"])
    bp_data = [fam_lengths[f] for f in fams_sorted]
    bp = ax.boxplot(bp_data, vert=False, patch_artist=True, tick_labels=[f.replace("_", " ") for f in fams_sorted])
    for patch, color in zip(bp["boxes"], colors_fam):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax.set_xlabel("Entity length (chars)")
    ax.set_title("(b) Entity Length by Family", fontsize=10)
    ax.invert_yaxis()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # (c) Alert vs MCP vs EO coverage summary
    ax = axes[1, 0]
    categories = ["Not in alert\n(required)", "In expected output\n(\u2265 2/3)", "In MCP data\n(> 0 freq)"]
    cat_vals = [0, 0, 0]
    total_ent = len(all_entities)

    for e in all_entities:
        sc = e["scenario"]
        ds = json.load(open(DATASET_DIR / f"{sc}.json"))
        alerts = " ".join(en["input"]["alert_text"] for en in ds).lower()
        eos = " ".join(str(en.get("expected_output", "")) for en in ds).lower()

        if e["entity"].lower() not in alerts:
            cat_vals[0] += 1
        if e["entity"].lower() in eos:
            cat_vals[1] += 1

        cache_file = CACHE_DIR / f"{sc}.json"
        if cache_file.exists():
            cache = json.load(open(cache_file))
            mcp = _get_mcp_text(cache).lower()
            if e["entity"].lower() in mcp:
                cat_vals[2] += 1

    pcts = [v / total_ent * 100 for v in cat_vals]
    bars = ax.bar(categories, pcts,
                  color=["#00b894", "#0984e3", "#e17055"],
                  edgecolor="white", linewidth=0.5)
    ax.set_ylabel("% of entities")
    ax.set_title("(c) Entity Validation Coverage", fontsize=10)
    ax.set_ylim(0, 115)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for bar, pct, cnt in zip(bars, pcts, cat_vals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                f"{pct:.0f}% ({cnt}/{total_ent})", ha="center", fontsize=9, fontweight="bold")

    # (d) Discriminative power distribution
    ax = axes[1, 1]
    disc_scores = []
    for e in all_entities:
        sc = e["scenario"]
        el = e["entity"].lower()
        other = sum(1 for osc in base_scenarios if osc != sc and el in
                    (_get_mcp_text(json.load(open(CACHE_DIR / f"{osc}.json"))).lower()
                     if (CACHE_DIR / f"{osc}.json").exists() else ""))
        disc = 1.0 / (1 + other)
        disc_scores.append(disc)

    bins = [0, 0.05, 0.1, 0.2, 0.5, 1.01]
    bin_labels = ["<0.05\n(low)", "0.05-0.1", "0.1-0.2", "0.2-0.5", "0.5-1.0\n(unique)"]
    hist_vals = [sum(1 for d in disc_scores if bins[i] <= d < bins[i + 1]) for i in range(len(bins) - 1)]
    bin_colors = ["#d63031", "#e17055", "#fdcb6e", "#00b894", "#0984e3"]
    ax.bar(bin_labels, hist_vals, color=bin_colors, edgecolor="white", linewidth=0.5)
    ax.set_xlabel("Discriminative power D = 1/(1+other_scenarios)")
    ax.set_ylabel("Number of entities")
    ax.set_title("(d) Discriminative Power Distribution", fontsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for i, v in enumerate(hist_vals):
        if v > 0:
            ax.text(i, v + 0.3, str(v), ha="center", fontsize=9, fontweight="bold")

    fig.suptitle("Figure 11. Golden Entity Taxonomy and Quality Metrics",
                 fontweight="bold", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(FIGURES_DIR / "fig11_golden_taxonomy.png")
    plt.close(fig)
    print("  fig11_golden_taxonomy.png")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    samples = load_all_samples()
    print(f"Loaded {len(samples)} samples. Generating figures...\n")

    fig_dataset_composition(samples)
    fig_root_cause_distribution(samples)
    fig_stratified_split()
    fig_leave_variation_out()
    fig_leave_scenario_out()
    fig_leave_family_out()
    fig_variation_seeds(samples)
    fig_golden_entity_lengths(samples)
    fig_golden_discriminative_power(samples)
    fig_golden_mcp_frequency(samples)
    fig_golden_taxonomy(samples)

    print(f"\nAll figures saved to {FIGURES_DIR}/")


if __name__ == "__main__":
    main()
