# Dataset

## How it works

The dataset follows a **split storage** strategy:

- **PostgreSQL** stores a lightweight index (catalog of scenarios, train/test split assignments). This is the entry point for any job.
- **S3/MinIO** (via DVC) stores the full case data: input alerts, expected output, fixtures, ground truth, and variations.

A job queries the catalog in the database to know _which_ cases to process (and their train/test split), then downloads the actual data from S3 on demand.

## Scenarios

There are **559 scenario variants** derived from **19 base scenarios** across **11 root cause families**, with **4 variation types** (`alert_rephrased`, `namespace_shifted`, `noise_injected`, `real`).

Each scenario in S3 contains:

- `input` -- The alert with text, severity, source, and metadata
- `expected_output` -- The root cause analysis with evidence
- `metadata` -- Tech, scenario name
- `golden_entities` -- Key entities for evaluation

## Split modes

The dataset supports 4 train/test split strategies, all pre-computed and stored in `cases.split_assignments`:

| Split type | Folds | Description |
|------------|-------|-------------|
| `stratified` | 1 | 80/20 train/test, stratified by root cause family |
| `leave_family_out` | 11 | Each fold holds out one root cause family |
| `leave_scenario_out` | 19 | Each fold holds out one base scenario |
| `leave_variation_out` | 4 | Each fold holds out one variation type |
| `dev` | 1 | 19 cases (1 real per base scenario), for local development |

## Querying scenarios

### Dev split (quick iteration)

The `dev` split contains 1 real (non-synthetic) case per base scenario -- 19 cases total. Use it during development to iterate quickly without running the full dataset:

```sql
SELECT s.scenario_ref, s.scenario_id, s.family, s.source_file
FROM cases.scenarios s
JOIN cases.split_assignments sa ON s.scenario_ref = sa.scenario_ref
WHERE sa.split_type = 'dev';
```

When ready to validate properly, switch to `stratified` or one of the cross-validation splits.

### Stratified split (simple train/test)

```sql
SELECT s.scenario_ref, s.scenario_id, s.family, s.source_file
FROM cases.scenarios s
JOIN cases.split_assignments sa ON s.scenario_ref = sa.scenario_ref
WHERE sa.split_type = 'stratified'
  AND sa.fold_name = 'default'
  AND sa.split = 'train';
```

### Leave-one-family-out cross-validation

```sql
-- List available folds
SELECT fold_name,
       count(*) FILTER (WHERE split = 'train') as train,
       count(*) FILTER (WHERE split = 'test') as test
FROM cases.split_assignments
WHERE split_type = 'leave_family_out'
GROUP BY fold_name ORDER BY fold_name;

-- Get test set for a specific fold
SELECT s.scenario_ref, s.family, s.source_file
FROM cases.scenarios s
JOIN cases.split_assignments sa ON s.scenario_ref = sa.scenario_ref
WHERE sa.split_type = 'leave_family_out'
  AND sa.fold_name = 'hold_out_config_error'
  AND sa.split = 'test';
```

### Leave-one-scenario-out (19 folds)

```sql
SELECT s.scenario_ref, s.source_file
FROM cases.scenarios s
JOIN cases.split_assignments sa ON s.scenario_ref = sa.scenario_ref
WHERE sa.split_type = 'leave_scenario_out'
  AND sa.fold_name = 'hold_out_kubernetes-crashloop'
  AND sa.split = 'test';
```

### Leave-one-variation-out (4 folds)

```sql
SELECT s.scenario_ref, s.source_file
FROM cases.scenarios s
JOIN cases.split_assignments sa ON s.scenario_ref = sa.scenario_ref
WHERE sa.split_type = 'leave_variation_out'
  AND sa.fold_name = 'hold_out_noise_injected'
  AND sa.split = 'test';
```

## Python workflow

Typical pattern for loading cases from the database + S3:

```python
import psycopg2
import boto3
import json

conn = psycopg2.connect("postgresql://ace-admin:<password>@db.ace.e2e.so.azure.datadope.co:5432/ace")
cur = conn.cursor()

# 1. Query training set from the catalog
cur.execute("""
    SELECT s.scenario_ref, s.source_file
    FROM cases.scenarios s
    JOIN cases.split_assignments sa ON s.scenario_ref = sa.scenario_ref
    WHERE sa.split_type = 'stratified' AND sa.split = 'train'
""")
train_cases = cur.fetchall()

# 2. For each case, download the full data from S3/MinIO
s3 = boto3.client("s3",
    endpoint_url="https://minio-api.langfuse.e2e.so.azure.datadope.co:443",
    aws_access_key_id="...", aws_secret_access_key="...")

for ref, source_file in train_cases:
    obj = s3.get_object(Bucket="dvc-ace", Key=f"data/datasets/{source_file}")
    case_data = json.loads(obj["Body"].read())
    # case_data has: input, expected_output, metadata, golden_entities
```

## Recording executions and evaluations

```sql
-- Register a new execution
INSERT INTO runs.executions (scenario_ref, split_type, fold_name, model_name, status, started_at)
VALUES ('kubernetes-crashloop-KubeDeploymentReplicasMismatch--alert_rephrased-s1200',
        'stratified', 'default', 'gpt-4o', 'running', NOW())
RETURNING id;

-- Record trajectory steps
INSERT INTO runs.trajectory_steps (execution_id, step_order, component, action_type, output_data)
VALUES ('<execution-id>', 1, 'generator', 'reasoning', '{"trace": "..."}'::jsonb);

-- Record evaluation metric
INSERT INTO runs.evaluations (execution_id, metric_name, score, details)
VALUES ('<execution-id>', 'root_cause_accuracy', 0.85, '{"match": true}'::jsonb);
```

## Querying results

```sql
-- Average score per family
SELECT s.family, e.metric_name, avg(e.score) as avg_score, count(*) as runs
FROM runs.evaluations e
JOIN runs.executions ex ON e.execution_id = ex.id
JOIN cases.scenarios s ON ex.scenario_ref = s.scenario_ref
GROUP BY s.family, e.metric_name
ORDER BY avg_score DESC;

-- Compare model performance
SELECT ex.model_name, avg(e.score) as avg_score, count(*) as runs
FROM runs.evaluations e
JOIN runs.executions ex ON e.execution_id = ex.id
WHERE e.metric_name = 'root_cause_accuracy'
GROUP BY ex.model_name;

-- Recent executions with scores
SELECT ex.id, s.scenario_id, ex.model_name, ex.status, e.metric_name, e.score
FROM runs.executions ex
JOIN cases.scenarios s ON ex.scenario_ref = s.scenario_ref
LEFT JOIN runs.evaluations e ON ex.id = e.execution_id
ORDER BY ex.created_at DESC LIMIT 20;
```

## Working with the context store

```sql
-- Create a new context version
INSERT INTO contexts.context_versions (version_tag, description)
VALUES ('v1.0', 'Initial playbook from stratified training')
RETURNING id;

-- Add a bullet with embedding
INSERT INTO contexts.bullets (context_version_id, content, category, tool_name, embedding)
VALUES (1, 'When pods are in CrashLoopBackOff, check container command syntax first',
        'diagnosis', 'kubectl', '<embedding-vector>');

-- Semantic search for relevant bullets
SELECT content, category, 1 - (embedding <=> '<query-vector>') as similarity
FROM contexts.bullets
WHERE context_version_id = 1
ORDER BY embedding <=> '<query-vector>'
LIMIT 5;
```

## Useful queries

```sql
-- Count scenarios by family
SELECT family, count(*) FROM cases.scenarios GROUP BY family ORDER BY count DESC;

-- Count scenarios by variation type
SELECT variation_type, count(*) FROM cases.scenarios GROUP BY variation_type;

-- List all base scenarios
SELECT DISTINCT scenario_id FROM cases.scenarios ORDER BY scenario_id;

-- Summary of splits per mode
SELECT split_type, count(DISTINCT fold_name) as folds, count(*) as assignments
FROM cases.split_assignments GROUP BY split_type;
```

## DVC

The full dataset files are managed with [DVC](https://dvc.org/) and stored in MinIO (S3-compatible). To pull the raw files locally:

```bash
AWS_ACCESS_KEY_ID=<key> AWS_SECRET_ACCESS_KEY=<secret> dvc pull
```

The DVC remote is configured in `.dvc/config` pointing to `s3://dvc-ace/dvc`.
