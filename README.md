# ACE - Agentic Context Engineering RCA POC

This repository contains a proof of concept for evaluating Root Cause Analysis
(RCA) agents over Kubernetes incident datasets. It compares a baseline agent
against an ACE-style agent that learns reusable context from previous evaluated
runs and injects that context into future RCA prompts.

The project is managed with `uv`, uses Python 3.12, and follows the SmartOps
quality conventions defined in `AGENTS.md`, `.cursorrules`, and the Ruff
configuration in `pyproject.toml`.

## What This Project Does

The project provides:

| Area | Purpose |
|------|---------|
| FastAPI application | Minimal application scaffold and health endpoint. |
| Baseline RCA agent | Reference pydantic-ai agent without ACE learning. |
| ACE RCA agent | RCA agent with learned playbook injection. |
| Evaluation runner | Runs agents over dataset splits and writes JSON results. |
| Scoring | Golden-entity matching, LLM-as-a-judge, and cosine similarity scoring. |
| ACE learning loop | Reflects evaluated results and curates a reusable playbook. |
| Ablation experiments | Runs controlled ACE variants to measure which parts help. |
| Event-system dataset | DVC-managed Kubernetes RCA benchmark data and MCP caches. |

## Prerequisites

| Tool | What it does | Install |
|------|--------------|---------|
| Python 3.12+ | Runtime | [python.org](https://www.python.org/downloads/) |
| uv | Package manager and virtual environment manager | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| just | Command runner | `cargo install just` or [just install docs](https://github.com/casey/just#installation) |
| pre-commit | Git hooks framework | Installed by `just setup` |
| Docker | Optional app and database containers | [docs.docker.com](https://docs.docker.com/get-docker/) |
| DVC | Dataset synchronization | Installed in the project environment when configured |

## Quick Start

```bash
# 1. Clone the repository
git clone <repo-url>
cd so_ua_ace_poc

# 2. Install dependencies and hooks
just setup

# 3. Configure environment variables
cp .env.example .env
# Edit .env and set the required LiteLLM settings.
# Langfuse settings are optional and only needed when tracing is desired.

# 4. Start the development server
just dev
```

Run `just` to list all available commands.

## Project Structure

```text
so_ua_ace_poc/
|-- src/app/                       # FastAPI application
|   |-- api/                       # HTTP endpoints
|   |-- services/                  # Business logic layer
|   |-- repositories/              # Data access layer
|   |-- models/                    # Database models
|   |-- schemas/                   # Pydantic schemas
|   |-- core/                      # Shared app exceptions and utilities
|
|-- src/agents/
|   |-- agent_a_ace/               # ACE agent and experiment logic
|   |   |-- agent.py               # Agent A factory and cached agent accessor
|   |   |-- playbook_agent.py      # Prompt wrapper that injects learned context
|   |   |-- reflector.py           # Turns evaluated results into lessons
|   |   |-- curator.py             # Deduplicates, ranks, persists playbook entries
|   |   |-- loop.py                # Reflects results and updates the playbook
|   |   |-- ablations.py           # Ablation modes and resolved config
|   |   |-- experiments.py         # Ablation experiment runner CLI
|   |
|   |-- agent_b_baseline/          # Baseline RCA agent
|
|-- src/common/
|   |-- runner.py                  # Runs agents over split rows
|   |-- scoring.py                 # Golden-entity scoring
|   |-- judge.py                   # LLM-as-a-judge evaluator
|   |-- cosine_similarity.py       # Embedding-based similarity evaluator
|   |-- cosine_backfill.py         # Adds cosine scores to existing result files
|   |-- config.py                  # Settings loaded from environment
|   |-- llm.py                     # LiteLLM-backed model construction
|   |-- mcp_client.py              # MCP server wiring
|
|-- tests/
|   |-- unit/                      # Fast unit tests with fake agents and fixtures
|   |-- integration/               # External-service tests, opt-in only
|
|-- data/event_system/             # DVC-managed Kubernetes RCA benchmark
|-- deployments/ace-database/      # Helm chart for PostgreSQL + pgvector
|-- docs/                          # Project documentation and ACE paper
```

## Agent Architecture

### Agent B: Baseline

`agent_b_baseline` is the reference RCA agent. It uses the shared system prompt,
LiteLLM model configuration, and MCP tools, but does not learn from previous
runs.

### Agent A: ACE

`agent_a_ace` wraps the same underlying pydantic-ai agent with a playbook
injection layer:

1. The runner executes Agent A on a split.
2. Scorers evaluate the output against ground truth.
3. The Reflector converts each evaluated result into an `AceInsight`.
4. The Curator deduplicates and persists insights as playbook entries.
5. Future Agent A calls prepend relevant playbook context to the RCA prompt.

The current playbook is JSON-backed for local experimentation:

```text
tmp/playbook.json
```

The intended production direction is to move this context into the database
schema under `contexts` with pgvector-backed retrieval.

## Evaluation Pipeline

The generic runner is:

```bash
uv run python -m src.common.runner \
    --agent agent_a_ace \
    --training-file tmp/test_3.json \
    --output-file tmp/test_3_results.json \
    --dataset-dir data/event_system/data/datasets
```

By default, the runner evaluates the `test` partition. It can also evaluate
other partitions, which is required for ACE warmup:

```bash
uv run python -m src.common.runner \
    --agent agent_a_ace \
    --training-file tmp/split.json \
    --output-file tmp/train_results.json \
    --dataset-dir data/event_system/data/datasets \
    --partition train
```

Use `--with-mock-mcp` when running real agents over DVC cases. This lets
`event_system.SplitRunner` read the real DVC split, start the correct mock MCP
servers for each cache source file, and stop them when the case changes. No
intermediate exported split file is required.

For smoke tests, clear Langfuse keys and disable OpenTelemetry export so local
runs are not slowed down by tracing network timeouts:

```bash
LANGFUSE_PUBLIC_KEY= LANGFUSE_SECRET_KEY= OTEL_SDK_DISABLED=true PYTHONPATH=. \
uv run python -m src.common.runner \
    --agent agent_b_baseline \
    --training-file data/event_system/training_stratified.json \
    --output-file tmp/dvc_baseline_results.json \
    --dataset-dir data/event_system/data/datasets \
    --cache-dir data/event_system/cache \
    --partition test \
    --with-mock-mcp \
    --max-cases 2 \
    --no-judge \
    --no-cosine
```

Heavy scorers can be disabled for fast local runs:

```bash
uv run python -m src.common.runner ... --no-judge --no-cosine
```

In a smoke run with `--max-cases 2 --no-judge --no-cosine`, the output JSON
should contain exactly two `AgentResult` items. The fields `judge_verdict` and
`cosine_similarity` should be `null`; the keyword-based `score` is still
computed.

Each `AgentResult` includes:

| Field | Source | Purpose |
|-------|--------|---------|
| `rca_output` | Agent | Raw RCA answer. |
| `expected_output` | Dataset | Human-written ground truth. |
| `golden_entities` | Dataset | Expected diagnostic entities. |
| `score` | `scoring.py` | Count of matched golden entities. |
| `matched_entities` | `scoring.py` | Which golden entities were found. |
| `judge_verdict` | `judge.py` | Optional LLM judge verdict. |
| `cosine_similarity` | `cosine_similarity.py` | Optional semantic similarity payload. |

## ACE Ablation Experiments

The ablation runner is in:

```text
src/agents/agent_a_ace/experiments.py
```

It creates isolated output directories per variant:

```text
tmp/ace_ablations/<mode>/
|-- results.json
|-- metadata.json
|-- playbook.json          # ACE modes only
|-- warmup_epoch_1.json    # ACE warmup modes only
|-- warmup_epoch_2.json    # Multi-epoch warmup modes only
|-- warmup_epoch_3.json    # Multi-epoch warmup modes only
```

`baseline` only writes final evaluation results and metadata because it runs
Agent B without ACE warmup or playbook injection. `online_no_warmup` writes an
empty playbook plus final results, but no warmup epoch files.

### Supported Modes

| Mode | What changes | What it measures |
|------|--------------|------------------|
| `baseline` | Runs Agent B without ACE. | Baseline RCA performance. |
| `full` | Runs Agent A with offline warmup and multi-epoch learning. | Expected best ACE configuration. |
| `single_epoch` | Runs only one warmup epoch. | Value of multi-epoch learning. |
| `online_no_warmup` | Starts with an empty playbook and skips offline warmup. | Cost of removing offline warmup. |
| `scenario_scoped_playbook` | Uses only same-scenario playbook entries. | Whether strict scenario context avoids noise. |
| `failure_only_reflection` | Keeps only insights from failed runs. | Whether failures provide stronger learning signal. |

Note: `online_no_warmup` currently means "evaluate with an empty playbook".
It does not yet update the playbook online during the test partition.

### Run One Ablation

```bash
LANGFUSE_PUBLIC_KEY= LANGFUSE_SECRET_KEY= OTEL_SDK_DISABLED=true PYTHONPATH=. \
uv run python -m src.agents.agent_a_ace.experiments \
    --mode single_epoch \
    --training-file data/event_system/training_stratified.json \
    --dataset-dir data/event_system/data/datasets \
    --cache-dir data/event_system/cache \
    --warmup-partition train \
    --eval-partition test \
    --with-mock-mcp \
    --max-cases 2 \
    --no-judge \
    --no-cosine
```

### Run All Ablations

```bash
LANGFUSE_PUBLIC_KEY= LANGFUSE_SECRET_KEY= OTEL_SDK_DISABLED=true PYTHONPATH=. \
uv run python -m src.agents.agent_a_ace.experiments \
    --mode all \
    --training-file data/event_system/training_stratified.json \
    --dataset-dir data/event_system/data/datasets \
    --cache-dir data/event_system/cache \
    --warmup-partition train \
    --eval-partition test \
    --with-mock-mcp \
    --max-cases 2 \
    --no-judge \
    --no-cosine
```

Use `--max-cases` for short smoke tests, and remove it for full runs. Use
`--no-judge --no-cosine` when measuring orchestration time with simulated or
fake agents.

### Check A Smoke Ablation Run

After a command such as `--mode single_epoch --max-cases 2`, check:

```bash
python3 - <<'PY'
import json
from pathlib import Path

run_dir = Path("tmp/ace_ablations/single_epoch")
for name in ["warmup_epoch_1.json", "results.json"]:
    data = json.loads((run_dir / name).read_text(encoding="utf-8"))
    print(name, len(data))

metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
print(metadata["mode"], metadata["result_count"], metadata["mock_mcp_enabled"])
PY
```

Expected smoke-test output:

```text
warmup_epoch_1.json 2
results.json 2
single_epoch 2 True
```

The CLI log should also include:

```text
Completed ACE ablation mode single_epoch with 2 evaluation results.
```

For DVC leave-out splits, select the fold explicitly:

```bash
LANGFUSE_PUBLIC_KEY= LANGFUSE_SECRET_KEY= OTEL_SDK_DISABLED=true PYTHONPATH=. \
uv run python -m src.agents.agent_a_ace.experiments \
    --mode single_epoch \
    --training-file data/event_system/training_leave_scenario_out.json \
    --fold hold_out_kubernetes-crashloop \
    --dataset-dir data/event_system/data/datasets \
    --cache-dir data/event_system/cache \
    --warmup-partition train \
    --eval-partition test \
    --with-mock-mcp \
    --max-cases 2 \
    --no-judge \
    --no-cosine
```

## Dataset And DVC

The Kubernetes RCA dataset lives under:

```text
data/event_system/
```

Pull DVC-managed data:

```bash
just dvc-pull
```

or:

```bash
dvc pull
```

The DVC dataset includes:

| Path | Purpose |
|------|---------|
| `data/event_system/data/datasets/` | Input alerts, expected RCA output, golden entities. |
| `data/event_system/cache/` | Pre-recorded MCP responses. |
| `data/event_system/training_stratified.json` | Stratified train/test split. |
| `data/event_system/training_leave_variation_out.json` | Leave-variation-out split. |
| `data/event_system/training_leave_scenario_out.json` | Leave-scenario-out split. |
| `data/event_system/training_leave_family_out.json` | Leave-family-out split. |

The generic runner and ACE ablation runner can read the DVC split files
directly when `--with-mock-mcp` is enabled. Stratified splits use top-level
`assignments`; leave-out splits use named `folds` and should be run with
`--fold <fold_name>`.

Useful dataset commands:

```bash
just cases-list
just cases-folds leave_family_out
just cases-summary stratified default
just cases-export stratified default test
just cases-serve dev
```

## Simulated Runtime Measurements

The ablation unit tests use fake agents and small in-memory fixtures. They are
intended to validate orchestration logic, not real LLM latency.

Measured locally:

| Scope | Command | Expected time |
|-------|---------|---------------|
| Ablation runner tests | `.venv/bin/pytest tests/unit/test_agents/test_ace_experiments.py -q` | About 2-3 seconds real time. |
| Ablation-related unit tests | `.venv/bin/pytest tests/unit/test_agents/test_ace_experiments.py tests/unit/test_agents/test_agent_a_ace_loop.py tests/unit/test_agents/test_runner.py -q` | About 2-3 seconds real time. |
| Full unit suite | `.venv/bin/pytest tests/unit -q` | About 2-4 seconds real time. |

Using the full local DVC `training_stratified.json` assignments with a fake
agent, no judge, and no cosine:

```text
total rows: 559
train rows: 442
test rows: 117
all 6 ablation modes: about 15-18 seconds real time
```

Estimated real-agent runtime depends on per-call latency. With 5,122 agent calls
for all six modes over the stratified split:

| Average agent call latency | Estimated total runtime |
|----------------------------|-------------------------|
| 5 seconds | About 7.1 hours |
| 10 seconds | About 14.2 hours |
| 20 seconds | About 28.5 hours |

For real-agent timing, start with bounded smoke tests:

```bash
/usr/bin/time -p env LANGFUSE_PUBLIC_KEY= LANGFUSE_SECRET_KEY= OTEL_SDK_DISABLED=true \
PYTHONPATH=. uv run python -m src.agents.agent_a_ace.experiments \
    --mode single_epoch \
    --training-file data/event_system/training_stratified.json \
    --dataset-dir data/event_system/data/datasets \
    --cache-dir data/event_system/cache \
    --with-mock-mcp \
    --max-cases 2 \
    --no-judge \
    --no-cosine
```

Then increase `--max-cases` gradually before removing it for a full run.

## Testing

Run all unit tests:

```bash
.venv/bin/pytest tests/unit -q
```

Run ablation-specific tests:

```bash
.venv/bin/pytest tests/unit/test_agents/test_ace_experiments.py -q
```

Run SplitRunner tests in the DVC dataset package:

```bash
cd data/event_system
.venv/bin/pytest tests/test_split_runner.py -q
```

Run all ablation-related tests:

```bash
.venv/bin/pytest \
    tests/unit/test_agents/test_ace_experiments.py \
    tests/unit/test_agents/test_agent_a_ace_loop.py \
    tests/unit/test_agents/test_runner.py \
    -q
```

Run lint and format checks:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format .
```

The project-level `pytest` configuration excludes integration tests by default:

```text
-m "not integration"
```

## Troubleshooting

| Symptom | Meaning | What to do |
|---------|---------|------------|
| `Exception while exporting Span` or timeout to `langfuse.e2e.so.azure.datadope.co` | The experiment finished, but tracing export failed. | For local runs, prefix commands with `LANGFUSE_PUBLIC_KEY= LANGFUSE_SECRET_KEY= OTEL_SDK_DISABLED=true`. |
| `Completed ACE ablation mode ... with N evaluation results` appears after tracing errors | The ablation run succeeded. | Check `tmp/ace_ablations/<mode>/results.json` and `metadata.json`. |
| `dvc pull` times out against `minio-api.langfuse.e2e.so.azure.datadope.co` | DVC storage is unreachable from the current network path. | Check VPN/Tailscale connectivity and retry `just dvc-pull`. |
| `Name or service not known` when calling LiteLLM | DNS or Tailscale resolution failed transiently. | Verify `getent hosts litellm.doi.azure.datadope.co` and retry after network recovers. |
| Mock MCP ports stay busy on `8090` or `8092-8095` | A previous mock server process is still alive. | Check with `ss -ltnp '( sport = :8090 or sport = :8092 or sport = :8093 or sport = :8094 or sport = :8095 )'`. |

## Common Commands

```bash
just              # Show all available commands
just setup        # First-time setup
just dev          # Start FastAPI dev server
just run          # Start FastAPI without reload
just test         # Run tests
just test-cov     # Run tests with coverage
just lint         # Run Ruff lint with fixes
just fmt          # Run Ruff format
just check        # Lint + format
just pre-commit   # Run all pre-commit hooks
just clean        # Remove generated Python caches
```

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | Layered architecture, ACE components, and data flow. |
| [Development guide](docs/development-guide.md) | Workflow, branching, commits, CI/CD, and conventions. |
| [Database](docs/database.md) | Deployment, schema, and connection information. |
| [Dataset](docs/dataset.md) | Dataset storage, split strategies, SQL examples, and DVC notes. |
| [Dataset analysis](data/event_system/dataset.md) | Dataset statistics and evaluation methodology. |
| [ACE paper](docs/ace-original-paper-2510.04618v3.pdf) | Original ACE research paper used as reference. |

## Current Limitations

| Limitation | Impact |
|------------|--------|
| `online_no_warmup` does not learn during test evaluation. | It currently measures empty-playbook evaluation, not true online adaptation. |
| Playbook storage is JSON-backed. | Good for local experiments, but database-backed context is still future work. |
| Mock MCP mode starts subprocesses per cache source file. | Real-agent runs are much more faithful, but still slow because each case calls the LLM. |
| Full LLM + MCP experiments can be long. | Use fake agents and disabled heavy scorers for orchestration timing first. |
