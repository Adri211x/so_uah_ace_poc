# Database

## Overview

The project deploys PostgreSQL 16 with pgvector 0.8.1 via a standalone Helm chart (`deployments/ace-database/`). The `ace` database is organized in three schemas:

```
ace (database)
  |-- cases.*          Scenario catalog and train/test splits (seeded from DVC/S3)
  |-- runs.*           Execution tracking, trajectories, evaluation metrics
  |-- contexts.*       Evolving playbook with versioned insights (pgvector)
```

## Deployment

### Prerequisites

- A Kubernetes cluster with Helm installed
- The target namespace must exist
- Two secrets must be applied before installing:
  - `ace-credentials` (keys: `PG_USER`, `PG_PASSWORD`)
  - `ace-s3-credentials` (keys: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)

### Install

```bash
# 1. Create the namespace
kubectl create namespace ace

# 2. Apply secrets (files not tracked in git)
kubectl apply -f deployments/ace-database/secret.yaml -n ace
kubectl apply -f deployments/ace-database/secret-s3.yaml -n ace

# 3. Install the Helm chart (seed job runs automatically)
helm install ace-database deployments/ace-database/ -n ace
```

### Verify

```bash
kubectl get all -n ace
kubectl get pvc -n ace
kubectl logs -n ace job/ace-database-seed-1
```

### Connection

| Parameter | Value |
|-----------|-------|
| Host | `db.ace.e2e.so.azure.datadope.co` |
| Port | `5432` |
| Database | `ace` |
| User | `ace-admin` (from secret) |
| Password | from secret `ace-credentials` |

```bash
psql "postgresql://ace-admin:<password>@db.ace.e2e.so.azure.datadope.co:5432/ace"
```

### Upgrade

```bash
helm upgrade ace-database deployments/ace-database/ -n ace
```

### Uninstall

```bash
helm uninstall ace-database -n ace
# PVC is retained by default, delete manually if needed:
# kubectl delete pvc data-ace-database-0 -n ace
```

## Schema: `cases`

Lightweight index of scenarios stored in DVC/S3. Full case data (input, expected_output, fixtures) lives in the object store. Populated automatically by the seed job on install/upgrade.

**`cases.scenarios`** -- Catalog of all scenario variants:

| Column | Description |
|--------|-------------|
| `scenario_ref` (PK) | Unique case ID, e.g. `kubernetes-crashloop-KubeDeploymentReplicasMismatch--alert_rephrased-s1200` |
| `scenario_id` | Base scenario name, e.g. `kubernetes-crashloop` |
| `family` | Root cause family, e.g. `container_error`, `config_error` |
| `root_cause_key` | Specific root cause, e.g. `invalid_container_command` |
| `variation_type` | `alert_rephrased`, `namespace_shifted`, `noise_injected`, or `real` |
| `source_file` | JSON filename in DVC/S3 |

**`cases.split_assignments`** -- Train/test splits per mode and fold:

| Column | Description |
|--------|-------------|
| `scenario_ref` (FK) | References `cases.scenarios` |
| `split_type` | `stratified`, `leave_family_out`, `leave_scenario_out`, `leave_variation_out` |
| `fold_name` | Fold identifier, e.g. `default`, `hold_out_config_error` |
| `split` | `train` or `test` |

## Schema: `runs`

Tracks agent executions, step-by-step reasoning trajectories, and evaluation metrics.

**`runs.executions`** -- Each agent run on a scenario:

| Column | Description |
|--------|-------------|
| `id` (PK, UUID) | Unique execution ID |
| `scenario_ref` (FK) | References `cases.scenarios` |
| `context_version_id` (FK) | References `contexts.context_versions` |
| `model_name` | Model used for the run |
| `model_config` | JSONB with model parameters |
| `status` | `pending`, `running`, `completed`, `failed` |
| `started_at` / `finished_at` | Timing |

**`runs.trajectory_steps`** -- Individual reasoning/action steps within a run:

| Column | Description |
|--------|-------------|
| `execution_id` (FK) | References `runs.executions` |
| `step_order` | Step sequence number |
| `component` | ACE component: `generator`, `reflector`, `curator` |
| `action_type` | What happened: tool call, reasoning, etc. |
| `input_data` / `output_data` | JSONB with step I/O |
| `duration_ms`, `tokens_in`, `tokens_out` | Cost tracking |

**`runs.evaluations`** -- Metrics per execution:

| Column | Description |
|--------|-------------|
| `execution_id` (FK) | References `runs.executions` |
| `metric_name` | Metric identifier |
| `score` | Numeric score |
| `details` | JSONB with breakdown |

## Schema: `contexts`

The evolving playbook: versioned context snapshots with insight bullets and pgvector embeddings for semantic deduplication and retrieval.

**`contexts.context_versions`** -- Versioned snapshots:

| Column | Description |
|--------|-------------|
| `id` (PK) | Version ID |
| `version_tag` | Human-readable tag (unique) |
| `parent_id` (FK, self) | Previous version (tracks lineage) |
| `description` | What changed in this version |

**`contexts.bullets`** -- Individual insights:

| Column | Description |
|--------|-------------|
| `context_version_id` (FK) | References `contexts.context_versions` |
| `content` | The insight/strategy text |
| `category` | Classification of the insight |
| `tool_name` | Related tool (if applicable) |
| `embedding` | `vector(1536)` for semantic search |
| `usage_count` | How many times this insight has been used |
| `origin_execution_id` (FK) | Which run produced this insight |
