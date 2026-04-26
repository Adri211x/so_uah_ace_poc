# ACE -- Onboarding guide

## Table of contents

1. [What is ACE](#1-what-is-ace)
2. [The problem we solve](#2-the-problem-we-solve)
3. [Repository structure](#3-repository-structure)
4. [The dataset](#4-the-dataset)
5. [How mock MCP works](#5-how-mock-mcp-works)
6. [Data splits and evaluation strategies](#6-data-splits-and-evaluation-strategies)
7. [The ACE loop (Generator / Reflector / Curator)](#7-the-ace-loop-generator--reflector--curator)
8. [Infrastructure: database and storage](#8-infrastructure-database-and-storage)
9. [Working with the Split Runner](#9-working-with-the-split-runner)
10. [Your first task: building an agent](#10-your-first-task-building-an-agent)
11. [Local setup](#11-local-setup)
12. [Reference links](#12-reference-links)

---

## 1. What is ACE

ACE (Agentic Context Engineering) is a research project that investigates whether an LLM-based agent can learn to diagnose Kubernetes infrastructure failures through iterative practice -- accumulating a "playbook" of strategies over time, rather than relying on a fixed prompt.

The core hypothesis is that an agent can improve its root cause analysis (RCA) ability by:

- Solving cases and recording reasoning trajectories.
- Reflecting on successes and failures to extract reusable insights.
- Building a structured context store (playbook) that evolves with each iteration.

This repository contains the dataset, the evaluation framework, and the code to run the ACE loop.

---

## 2. The problem we solve

When a monitoring alert fires in a Kubernetes cluster (e.g. "KubePodCrashLooping"), an SRE investigates using tools like `kubectl`, log systems (Loki, Elasticsearch), metrics (Prometheus), and traces (Tempo). They form hypotheses, gather evidence, and arrive at a root cause.

We want an AI agent to do the same thing. The agent receives an alert text and has access to the same tools via MCP (Model Context Protocol). It must produce a root cause analysis that matches the human-written ground truth.

The challenge: there are 19 different failure scenarios across 11 root cause families, with different infrastructure topologies, log patterns, and diagnostic paths. A naive agent memorizes surface patterns. ACE aims to produce an agent that builds transferable diagnostic reasoning.

---

## 3. Repository structure

```
so_ua_ace_poc/
├── src/app/                   # FastAPI application (ACE API, future)
├── data/event_system/         # The dataset and mock infrastructure
│   ├── cache/                 #   247 pre-recorded MCP response files (via DVC)
│   ├── data/datasets/         #   247 ground truth JSON files (via DVC)
│   ├── training_*.json        #   Split assignment files (via DVC)
│   ├── event_system/          #   Python package
│   │   ├── mock_mcp_server.py #     Mock MCP server
│   │   ├── case_provider.py   #     Case resolution (DB / local)
│   │   ├── mock_manager.py    #     Mock server lifecycle
│   │   ├── split_runner.py    #     High-level iteration API
│   │   └── cli_runner.py      #     CLI (event-runner)
│   ├── tests/                 #   Dataset integrity tests
│   └── dataset.md             #   Full statistical documentation
├── deployments/               # Helm charts (ace-database)
├── docs/                      # Project documentation
│   ├── architecture.md        #   Architecture and data flow
│   ├── database.md            #   Database schema reference
│   ├── dataset.md             #   Dataset access patterns (SQL, Python)
│   └── development-guide.md   #   Git workflow, conventions
└── justfile                   # Task runner (just <command>)
```

---

## 4. The dataset

### What it contains

**559 labeled samples** derived from **19 base Kubernetes failure scenarios**. Each sample has:

| Component | Description | Location |
|-----------|-------------|----------|
| Alert text | The monitoring alert the agent receives as input | `data/datasets/*.json` field `input.alert_text` |
| MCP cache | Pre-recorded tool responses (kubectl, Loki, ES, Tempo, Prometheus) | `cache/*.json` |
| Expected output | Human-written root cause analysis | `data/datasets/*.json` field `expected_output` |
| Golden entities | 3 short strings for automated scoring | `data/datasets/*.json` field `golden_entities` |

### How 19 scenarios become 559 samples

Each of the 19 base scenarios has between 1 and 6 distinct alerts (43 alerts total). Each alert is expanded with 3 synthetic variation types, each with 4 random seeds:

```
1 base alert  x  (1 real + 3 types x 4 seeds)  =  13 samples per alert
43 alerts  x  13  =  559 samples
```

| Variation type | What changes | Why it matters |
|----------------|-------------|----------------|
| `real` | Nothing (original recording) | Ground truth baseline |
| `namespace_shifted` | Namespaces, pod names, IPs, timestamps | Tests naming invariance |
| `noise_injected` | Healthy decoy pods added | Tests signal-to-noise filtering |
| `alert_rephrased` | Alert text rewritten differently | Tests robustness to wording |

### The 11 root cause families

| Family | Scenarios | Samples | What goes wrong |
|--------|-----------|---------|-----------------|
| config_error | 5 | 273 | ELK/Logstash pipeline misconfigurations |
| container_error | 2 | 52 | Bad commands, broker queues full |
| false_alarm | 1 | 39 | Nothing is actually broken |
| image_error | 1 | 39 | Invalid container image references |
| service_failure | 3 | 39 | Payment service issues |
| scheduling_error | 2 | 39 | Bad volumes or node selectors |
| resource_limit | 1 | 26 | OOMKilled |
| lock_error | 1 | 13 | Server lock not released |
| performance | 1 | 13 | Database latency |
| probe_error | 1 | 13 | Bad liveness/readiness probes |
| routing_error | 1 | 13 | Service selector mismatch |

The dataset is intentionally imbalanced (21x ratio between largest and smallest family). This reflects real-world incident distributions.

### Data files at a glance

**`data/datasets/kubernetes-crashloop.json`** (ground truth):

```json
[
  {
    "id": "kubernetes-crashloop-KubePodCrashLooping",
    "input": {
      "alert_text": "ALERT: KubePodCrashLooping - Pod app-xxx in namespace crashloop-test..."
    },
    "expected_output": "Root cause: invalid_container_command - The pod fails because ...",
    "golden_entities": [
      "unterminated quoted string",
      "invalid python3 command",
      "nginx image"
    ]
  }
]
```

**`cache/kubernetes-crashloop.json`** (mock MCP responses):

```json
{
  "_meta": {"scenario": "kubernetes-crashloop"},
  "kubectl": {
    "kubectl get pods -A": "NAMESPACE   NAME   READY   STATUS ...",
    "kubectl describe pod app-xxx -n crashloop-test": "..."
  },
  "loki": { "...query...": "...log lines..." },
  "elasticsearch": { "...": "..." }
}
```

The agent queries the mock MCP server, which looks up the command in the cache and returns the pre-recorded response. It behaves exactly like querying a real cluster, but deterministically.

---

## 5. How mock MCP works

MCP (Model Context Protocol) is an open standard that lets LLM agents call external tools. Our mock server exposes 5 MCP endpoints on localhost:

| Port | Tool | What it serves |
|------|------|----------------|
| 8090 | kubectl | Kubernetes commands (get pods, describe, logs...) |
| 8092 | Elasticsearch | Index listing, mappings, search, ES\|QL |
| 8093 | Loki | Label names/values, LogQL queries |
| 8094 | Tempo | TraceQL queries, tag values |
| 8095 | Prometheus | Metric listing, PromQL instant/range queries |

The agent connects to `http://127.0.0.1:8090/mcp` and calls tools like `kubectl_impl(command="get pods -A")`. The mock server matches the command against its cache and returns the recorded response.

This means:

- No real cluster needed for development or evaluation.
- Every run produces identical results (deterministic).
- All 559 samples can be evaluated locally.

---

## 6. Data splits and evaluation strategies

The dataset comes with 4 pre-computed split strategies. All enforce **no data leakage**: all 13 variations of the same base alert always go to the same partition.

### Overview

| Split type | Folds | Research question |
|------------|-------|-------------------|
| `stratified` | 1 | General accuracy on a representative test set |
| `leave_variation_out` | 4 | Does the agent generalize to unseen perturbation types? |
| `leave_scenario_out` | 19 | Can the agent diagnose an infrastructure it never saw? |
| `leave_family_out` | 11 | Can the agent handle failure types absent from training? |
| `dev` | 1 | Quick iteration during development (19 real cases) |

### Which split to use when

- **Starting out / debugging**: Use `dev` (19 cases, fast feedback).
- **Validating your agent works**: Use `stratified` (442 train / 117 test).
- **Measuring generalization**: Use `leave_scenario_out` or `leave_family_out` (zero-shot evaluation on unseen scenarios or failure types).
- **Checking variation robustness**: Use `leave_variation_out` (e.g. train on real+noise, test on namespace_shifted).

### Golden entities and automated scoring

Each scenario defines 3 golden entities for automated evaluation:

| Type | Role | Example (crashloop) |
|------|------|---------------------|
| E1 -- Technical evidence | Exact error found via tools | `unterminated quoted string` |
| E2 -- Diagnostic synthesis | Agent connects the dots | `invalid python3 command` |
| E3 -- Root cause identifier | Nature of the problem | `nginx image` |

Scoring uses normalized Levenshtein similarity (threshold 0.95):

- **3/3**: Agent fully identified root cause with evidence.
- **2/3**: Partial diagnosis.
- **0-1/3**: Agent failed.

---

## 7. The ACE loop (Generator / Reflector / Curator)

ACE has three components that run in a loop:

```
                    +-----------+
                    | Generator |  Solves cases using current playbook
                    +-----+-----+
                          |
                          v
                    +-----------+
                    | Reflector |  Compares trajectories, extracts insights
                    +-----+-----+
                          |
                          v
                    +-----------+
                    |  Curator  |  Updates the playbook (adds/refines bullets)
                    +-----+-----+
                          |
                          +-----------> back to Generator (next iteration)
```

- **Generator**: Receives an alert + current playbook context, uses MCP tools to investigate, and produces a root cause analysis. Records every step (reasoning, tool calls, observations) as a trajectory.
- **Reflector**: Compares successful and failed trajectories for the same scenario types. Extracts actionable insights ("When you see CrashLoopBackOff, always check the container command first").
- **Curator**: Maintains a structured playbook stored in PostgreSQL with pgvector embeddings. Deduplicates insights, updates usage counts, and produces the next version of the context.

Each iteration of the loop should improve agent performance on subsequent cases.

---

## 8. Infrastructure: database and storage

### PostgreSQL (ACE database)

Deployed in the `ace` Kubernetes namespace. Three schemas:

| Schema | Purpose |
|--------|---------|
| `cases` | Scenario catalog (559 entries) and split assignments. Read-only after seeding. |
| `runs` | Execution tracking: which scenario was run, with which model, step-by-step trajectory, evaluation scores. |
| `contexts` | The evolving playbook: versioned context snapshots with pgvector embeddings for semantic retrieval. |

Connection: `postgresql://ace-admin:<password>@10.122.0.9:5432/ace`
(credentials in K8s secret `ace-credentials` in namespace `ace`)

### S3/MinIO (DVC storage)

Full dataset files (cache JSONs, dataset JSONs, training splits) are stored in MinIO and versioned with DVC. Running `dvc pull` from the repo root downloads everything locally.

### How they connect

1. The database `cases.*` tables have the **catalog** (scenario_ref, family, source_file, split assignments).
2. S3/MinIO has the **actual data** (alert text, expected output, MCP responses).
3. The `source_file` column in `cases.scenarios` maps directly to files in `cache/` and `data/datasets/`.

---

## 9. Working with the Split Runner

The Split Runner is the tool you will use most. It bridges the database catalog with the mock MCP server.

### From the command line

```bash
cd data/event_system
uv sync

# What splits are available?
uv run event-runner list-splits

# What folds does leave_family_out have?
uv run event-runner list-folds leave_family_out

# How many train/test cases in stratified?
uv run event-runner summary stratified default

# Export dev cases to a file
uv run event-runner export --split dev --partition dev -o dev_cases.json
```

### From Python (how agents use it)

```python
from event_system.split_runner import SplitRunner
from event_system.case_provider import DatabaseCaseProvider

# 1. Connect to the catalog
provider = DatabaseCaseProvider()  # reads ACE_DATABASE_URL env var

# 2. Create the runner
runner = SplitRunner(
    provider=provider,
    cache_dir="cache/",
    dataset_dir="data/datasets/",
)

# 3. Iterate through cases
for run_case in runner.iter_cases("dev"):
    print(f"Case: {run_case.case.scenario_ref}")
    print(f"  Alert: {run_case.input['alert_text'][:80]}...")
    print(f"  kubectl endpoint: {run_case.endpoints.kubectl}")
    print(f"  Expected: {run_case.expected_output[:80]}...")

    # Your agent goes here:
    # result = my_agent.solve(run_case.input, run_case.endpoints)
    # score = evaluate(result, run_case.expected_output, run_case.golden_entities)
```

What happens under the hood:

1. The runner queries the database for all cases in the requested split/fold.
2. It groups cases by `source_file` (many cases share the same cache).
3. For each group: it starts the mock MCP server (5 endpoints), yields each case, then stops the mock before moving to the next group.
4. Each `RunCase` comes with live MCP endpoints and the ground truth already loaded.

### Offline mode (no database)

If you cannot reach the database, use `LocalCaseProvider` which reads the training JSON files:

```python
from event_system.case_provider import LocalCaseProvider

provider = LocalCaseProvider(data_dir=".")  # after dvc pull
```

---

## 10. Your first task: building an agent

The goal is to build an agent that:

1. Receives an alert text.
2. Uses MCP tools (kubectl, logs, metrics, traces) to investigate.
3. Produces a root cause analysis.
4. Gets scored against the ground truth.

### Minimal skeleton

```python
from event_system.split_runner import SplitRunner
from event_system.case_provider import DatabaseCaseProvider
# from your_agent import MyAgent  # your implementation

provider = DatabaseCaseProvider()
runner = SplitRunner(provider, cache_dir="cache/", dataset_dir="data/datasets/")

results = []
for run_case in runner.iter_cases("dev"):
    # result = MyAgent(run_case.endpoints).analyze(run_case.input["alert_text"])
    # score = score_response(result, run_case.golden_entities)
    # results.append({"ref": run_case.case.scenario_ref, "score": score})
    pass

# Aggregate results, compute metrics, etc.
```

### Suggested iteration plan

1. **Start with `dev` split** (19 cases). Get a single case working end-to-end.
2. Pick one easy scenario (e.g. `crashloop`) and get 3/3 golden entities.
3. Run all 19 dev cases. Identify failure patterns.
4. Move to `stratified` split (117 test cases) for proper evaluation.
5. Try `leave_family_out` to measure zero-shot generalization.

### What "solving a case" means

The agent must:

- Parse the alert to understand what kind of problem it might be.
- Query the right tools (e.g. `kubectl get pods -A` to see pod status).
- Follow evidence chains (e.g. pod in CrashLoopBackOff -> describe pod -> check logs).
- Synthesize findings into a root cause statement.

The evaluation checks whether the agent's output contains the 3 golden entities (evidence, synthesis, root cause identifier).

---

## 11. Local setup

```bash
# 1. Clone the repository
git clone git@gitlab.datadope.co:smartops/ace-universidad-alcal/so_ua_ace_poc.git
cd so_ua_ace_poc

# 2. Install Python dependencies
uv sync

# 3. Install pre-commit hooks
pre-commit install

# 4. Pull dataset files from MinIO
dvc pull

# 5. Set database credentials (get password from K8s secret ace-credentials)
export ACE_DATABASE_URL="postgresql://ace-admin:<password>@10.122.0.9:5432/ace"

# 6. Verify everything works
cd data/event_system
uv sync
uv run event-runner list-splits
uv run pytest tests/ -v
```

### Useful just commands

| Command | Description |
|---------|-------------|
| `just setup` | Install all dependencies + pre-commit |
| `just test` | Run all tests |
| `just check` | Lint + format |
| `just mock-server crashloop` | Start mock MCP for a single scenario |
| `just cases-list` | List available splits |
| `just cases-summary stratified` | Show split case counts |
| `just cases-serve dev` | Interactive case-by-case with mock |

---

## 12. Reference links

| Document | Description |
|----------|-------------|
| [data/event_system/README.md](../data/event_system/README.md) | Dataset README with full structure, scenarios, variation types |
| [data/event_system/dataset.md](../data/event_system/dataset.md) | Statistical analysis (11 figures), golden entities, methodology |
| [docs/architecture.md](architecture.md) | ACE architecture, data flow diagram |
| [docs/database.md](database.md) | Database schema (cases, runs, contexts), connection, SQL examples |
| [docs/dataset.md](dataset.md) | SQL queries for splits, Python workflow, DVC setup |
| [docs/development-guide.md](development-guide.md) | Git workflow, conventions, CI/CD pipeline |
