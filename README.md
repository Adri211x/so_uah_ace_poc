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
# Edit .env and set the required LiteLLM, Langfuse, and MCP settings.

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

Heavy scorers can be disabled for fast local runs:

```bash
uv run python -m src.common.runner ... --no-judge --no-cosine
```

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
|-- playbook.json
|-- results.json
|-- metadata.json
|-- warmup_epoch_1.json
|-- warmup_epoch_2.json
|-- warmup_epoch_3.json
```

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
PYTHONPATH=. uv run python -m src.agents.agent_a_ace.experiments \
    --mode single_epoch \
    --training-file tmp/flat_split.json \
    --dataset-dir data/event_system/data/datasets \
    --warmup-partition train \
    --eval-partition test \
    --no-judge \
    --no-cosine
```

### Run All Ablations

```bash
PYTHONPATH=. uv run python -m src.agents.agent_a_ace.experiments \
    --mode all \
    --training-file tmp/flat_split.json \
    --dataset-dir data/event_system/data/datasets \
    --warmup-partition train \
    --eval-partition test \
    --no-judge \
    --no-cosine
```

Use `--no-judge --no-cosine` when measuring orchestration time with simulated
or fake agents.

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

The DVC split files are structured as dictionaries with an `assignments` field.
The current generic runner expects a flat list of rows. For now, use an exported
flat split or create one from `assignments` before running the generic runner.

Export a flat test split with `event-runner`:

```bash
cd data/event_system
uv run event-runner export \
    --split stratified \
    --fold default \
    --partition test \
    -o ../../tmp/stratified_test_cases.json
```

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

## Testing

Run all unit tests:

```bash
.venv/bin/pytest tests/unit -q
```

Run ablation-specific tests:

```bash
.venv/bin/pytest tests/unit/test_agents/test_ace_experiments.py -q
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
| DVC split files are dictionary-shaped. | The generic runner needs a flat list or a future loader that reads `assignments`. |
| `online_no_warmup` does not learn during test evaluation. | It currently measures empty-playbook evaluation, not true online adaptation. |
| Playbook storage is JSON-backed. | Good for local experiments, but database-backed context is still future work. |
| Full LLM + MCP experiments can be long. | Use fake agents and disabled heavy scorers for orchestration timing first. |
