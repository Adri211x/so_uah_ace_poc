# Event System Dataset

A benchmark dataset for evaluating AI agents on Kubernetes root cause analysis (RCA) tasks.

## Overview

This dataset contains **559 labeled samples** derived from 19 distinct Kubernetes failure scenarios. Each sample pairs a monitoring alert with pre-recorded infrastructure state (kubectl output, logs, metrics, traces) and a human-written root cause analysis. The dataset is designed for training and evaluating LLM-based agents that diagnose infrastructure incidents using tool-based investigation.

The infrastructure state is served through [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) interfaces, allowing agents to query kubectl, Elasticsearch, Loki, Tempo, and Prometheus as if interacting with a live cluster. All responses are pre-recorded, ensuring deterministic and reproducible evaluation.

## Dataset Summary

| Property | Value |
|----------|-------|
| Total samples | 559 |
| Base scenarios | 19 |
| Unique alerts | 43 |
| Root cause families | 11 |
| Variation types | 4 (real, namespace_shifted, noise_injected, alert_rephrased) |
| Seeds per synthetic type | 4 (42, 500, 800, 1200) |
| Golden entities per scenario | 3 (57 total) |

## Dataset Structure

```
event_system/
├── data/datasets/           # 247 ground truth JSON files
├── cache/                   # 247 pre-recorded MCP response files
├── training_stratified.json
├── training_leave_variation_out.json
├── training_leave_scenario_out.json
├── training_leave_family_out.json
├── event_system/            # Python package (mock server, generation tools)
├── tests/                   # Reproducibility test suite (64 tests)
├── docs/                    # Figures and visualization scripts
└── dataset.md               # Full dataset documentation
```

### Data Fields

Each sample in `data/datasets/*.json` contains:

```json
{
  "id": "kubernetes-crashloop-KubePodCrashLooping",
  "input": {
    "alert_text": "ALERT: KubePodCrashLooping ..."
  },
  "expected_output": "Root cause: invalid_container_command - ...",
  "golden_entities": [
    "unterminated quoted string",
    "invalid python3 command",
    "nginx image"
  ],
  "metadata": {
    "scenario": "kubernetes-crashloop",
    "alert_name": "KubePodCrashLooping"
  }
}
```

Each corresponding MCP cache in `cache/*.json` contains pre-recorded responses keyed by tool name and query:

```json
{
  "_meta": { "scenario": "kubernetes-crashloop", "case_type": "real" },
  "kubectl": {
    "kubectl get pods -n crashloop-test": "NAME ... STATUS ...",
    "kubectl describe pod app-xxx -n crashloop-test": "..."
  },
  "loki": { ... },
  "elasticsearch": { ... }
}
```

### Data Splits

Four split configurations are provided for different evaluation objectives:

| File | Folds | Strategy | Purpose |
|------|-------|----------|---------|
| `training_stratified.json` | 1 | Stratified 80/20 by root cause family | General evaluation |
| `training_leave_variation_out.json` | 4 | Hold out one variation type | Robustness to perturbation types |
| `training_leave_scenario_out.json` | 19 | Hold out one scenario | Zero-shot scenario generalization |
| `training_leave_family_out.json` | 11 | Hold out one root cause family | Zero-shot failure type generalization |

All splits enforce no data leakage: all 13 variations of a base alert are always assigned to the same partition. See [dataset.md](dataset.md) for detailed statistical analysis.

### Golden Entities

Each scenario defines 3 **golden entities** for automated evaluation. These are short strings (median 14 characters) that:

- Do **not** appear in the alert text (the agent must discover them through investigation)
- Represent diagnostic evidence, synthesis, or root cause identification
- Can be matched against agent output using normalized Levenshtein similarity (threshold ≥ 0.95)

See [Section 5 of dataset.md](dataset.md#5-golden-entities-for-automated-evaluation) for the full entity table and evaluation protocol.

## Scenarios

| Scenario | Failure Type | Alerts | Primary Tools |
|----------|-------------|--------|---------------|
| crashloop | CrashLoopBackOff (invalid command) | 2 | kubectl |
| data-pipeline | Broker queue full | 2 | kubectl |
| elk-bad-port | Redis port mismatch | 5 | kubectl, elasticsearch |
| elk-commented-output | Commented ES output | 3 | kubectl, elasticsearch |
| elk-fake | False alarm (no real failure) | 3 | kubectl, elasticsearch |
| elk-malformed-pipeline | Logstash config typo | 6 | kubectl, elasticsearch |
| elk-mix-errors | Multiple config errors | 6 | kubectl, elasticsearch |
| image-pull | ImagePullBackOff | 3 | kubectl |
| locked-server | Unreleased server lock | 1 | kubectl |
| oomkilled | OOMKilled | 2 | kubectl |
| otel-demo-paymentFailure | Payment service failure | 1 | kubectl, tempo, prometheus |
| otel-demo-paymentFailure-no-flagd | Same, without flagd data | 1 | kubectl, tempo |
| otel-demo-paymentFailure-no-flagd-no-kube-logs | Same, minimal data | 1 | kubectl, tempo |
| pending-pod | PVC volume not found | 1 | kubectl |
| probes | Misconfigured probes | 1 | kubectl |
| service-routing | Service selector mismatch | 1 | kubectl |
| service-with-no-endpoints | Invalid node selector | 2 | kubectl |
| tracing-tree | Database latency | 1 | kubectl, tempo |
| unconfigured-server | Bad DB connection config | 1 | kubectl, elasticsearch |

## Variation Types

| Type | Samples | Description |
|------|---------|-------------|
| `real` | 43 | Original unmodified scenarios |
| `namespace_shifted` | 172 | Namespaces, pod names, IPs, and timestamps remapped |
| `noise_injected` | 172 | Healthy decoy pods and namespaces added to MCP data |
| `alert_rephrased` | 172 | Alert text rewritten with different phrasing |

## Usage

### Downloading data

Data files are managed with [DVC](https://dvc.org/). After cloning the repository:

```bash
# From the repository root
dvc pull
```

This downloads all cache files, datasets, and training splits from the configured MinIO remote (~1 GB). DVC credentials must be configured in `.dvc/config.local` (see repository setup instructions).

### Running the mock MCP server

```bash
cd data/event_system
uv sync
uv run event-mock --cache cache/kubernetes-crashloop.json
```

This starts MCP endpoints on ports 8090-8095 (kubectl, elasticsearch, loki, tempo, prometheus). Connect your agent to `http://127.0.0.1:8090/mcp` for kubectl, etc.

### Running tests

```bash
uv sync --extra dev
uv run pytest tests/ -v
```

The test suite (64 tests) validates data integrity, variation correctness, split properties, golden entity constraints, and cross-artifact consistency.

## Reproduction

All derived artifacts can be regenerated deterministically:

```bash
# Generate synthetic variations (requires base data in data/datasets/ and cache/)
for type in namespace_shifted noise_injected alert_rephrased; do
  uv run event-variations --type $type --all
  for seed in 500 800 1200; do
    uv run event-variations --type $type --all --seed $seed --suffix s$seed
  done
done

# Generate training splits
uv run event-splits

# Generate golden entities
uv run event-golden

# Generate documentation figures
uv run python docs/generate_figures.py

# Verify integrity
uv run pytest tests/ -v
```

## Documentation

- **[dataset.md](dataset.md)** — Full statistical analysis of the dataset, split strategies, golden entities, and methodological considerations. Includes 11 figures.

## Requirements

- Python ≥ 3.12
- [uv](https://github.com/astral-sh/uv) package manager
- [DVC](https://dvc.org/) with S3 support (`uv tool install dvc --with dvc-s3`)
- Docker (only for pre-generating caches from SQLite databases; not needed for evaluation)

## License

This dataset is provided for research purposes.

## Citation

If you use this dataset, please cite:

```bibtex
@misc{event_system_2026,
  title={Event System: A Benchmark Dataset for Kubernetes Root Cause Analysis Agents},
  year={2026},
  url={https://gitlab.datadope.co/smartops/roma}
}
```
