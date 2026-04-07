# so_ua_ace_poc

EXAMPLE REPOSITORY

## Prerequisites

Before starting, make sure you have these tools installed:

| Tool | What it does | Install |
|------|-------------|---------|
| **Python 3.12+** | Runtime | [python.org](https://www.python.org/downloads/) |
| **uv** | Package manager (replaces pip) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **just** | Command runner (replaces Makefile) | `cargo install just` or [other methods](https://github.com/casey/just#installation) |
| **pre-commit** | Git hooks framework | Installed automatically with `just setup` |
| **Docker** | Containerization (optional) | [docs.docker.com](https://docs.docker.com/get-docker/) |

## Quick start

```bash
# 1. Clone the repository
git clone <repo-url>
cd so_ua_ace_poc

# 2. Run the initial setup (installs deps + configures git hooks)
just setup

# 3. Start the dev server
just dev
```

That's it. Run `just` (without arguments) to see all available commands.

## Project structure

```
so_ua_ace_poc/
|
|-- src/                        # Application source code
|   |-- app/
|       |-- api/                # HTTP endpoints (FastAPI routes)
|       |-- services/           # Business logic layer
|       |-- repositories/       # Data access layer (database queries)
|       |-- models/             # Database models (SQLAlchemy)
|       |-- schemas/            # Request/response schemas (Pydantic)
|       |-- core/               # Shared config, exceptions, utilities
|
|-- tests/
|   |-- unit/                   # Unit tests (fast, no external deps)
|   |-- integration/            # Integration tests (may need DB, APIs)
|
|-- docs/                       # Project documentation and conventions
|
|-- .cursor/rules/              # AI assistant rules (Cursor IDE)
|-- AGENTS.md                   # AI assistant rules (universal, works in any IDE)
|-- .cursorrules                # AI assistant rules (Cursor-specific, simple format)
|-- .cursorignore               # Files excluded from AI assistant context
|
|-- .pre-commit-config.yaml     # Git hooks: linting, formatting, secret detection
|-- .gitleaks.toml              # Secret detection config
|-- .gitlab-ci.yml              # CI/CD pipeline
|-- .gitignore                  # Files excluded from Git
|
|-- pyproject.toml              # Project metadata, dependencies, tool config
|-- justfile                    # Project commands (run `just` to see them all)
|-- Dockerfile                  # Container build definition
|-- .env.example                # Template for environment variables
|
|-- deployments/                # Kubernetes/Helm deployment manifests
|   |-- ace-database/           # PostgreSQL + pgvector Helm chart
|       |-- Chart.yaml
|       |-- values.yaml
|       |-- secret.yaml         # K8s Secret (not tracked in git)
|       |-- files/
|       |   |-- init_databases.sql
|       |-- templates/
|           |-- _helpers.tpl
|           |-- configmap-init-sql.yaml
|           |-- service.yaml
|           |-- statefulset.yaml
```

## Architecture

The project follows a **layered architecture**. Each layer has a clear responsibility:

```
HTTP Request
    |
    v
API Layer (src/app/api/)            --> Receives requests, validates input
    |
    v
Service Layer (src/app/services/)   --> Business logic, orchestration
    |
    v
Repository Layer (src/app/repositories/)  --> Database access
    |
    v
Database
```

**Rules:**
- Each layer only talks to the layer directly below it.
- Business logic never goes in the API layer.
- Database queries never go in the service layer.

## Common commands

```bash
just              # Show all available commands
just setup        # First-time setup
just dev          # Start dev server with auto-reload
just test         # Run all tests
just test-cov     # Run tests with coverage report
just lint         # Run linter
just fmt          # Format code
just check        # Lint + format
just pre-commit   # Run all pre-commit hooks
just clean        # Remove cached/generated files
```

## Development workflow

### 1. Create a branch

```bash
git checkout -b feature/JIRA-123-short-description
```

Branch naming: `<prefix>/<JIRA-KEY>-<description>`

Valid prefixes: `feature`, `fix`, `hotfix`, `refactor`, `chore`, `docs`, `test`, `perf`

### 2. Write code

- All code, comments, docstrings, and logs **must be in English**
- Use `logging.getLogger(__name__)` instead of `print()`
- Add type hints to all functions
- Add docstrings to public functions (Google format)

### 3. Run checks before committing

```bash
just check        # Lint + format
just test         # Run tests
```

Or let pre-commit do it automatically when you commit (it runs on every `git commit`).

### 4. Commit

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```bash
git commit -m "feat(api): add user registration endpoint [JIRA-123]"
```

Format: `<type>(<scope>): <summary> [JIRA-KEY]`

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `ci`, `build`

### 5. Push and create Merge Request

```bash
git push -u origin feature/JIRA-123-short-description
```

Then create a Merge Request in GitLab linking the Jira ticket.

## What happens when you commit

Pre-commit hooks run automatically and will:

1. **Ruff lint** -- Checks code quality, auto-fixes what it can
2. **Ruff format** -- Formats code consistently
3. **Trailing whitespace** -- Removes trailing spaces
4. **End of file fixer** -- Ensures files end with a newline
5. **YAML/TOML/JSON check** -- Validates config file syntax
6. **Large file check** -- Blocks files > 1MB
7. **Gitleaks** -- Scans for passwords, API keys, or secrets

If any hook fails, the commit is blocked. Fix the issue and try again.

## What happens in CI/CD

When you push to GitLab, the pipeline runs:

1. **Lint** -- Ruff checks (must pass)
2. **Test** -- pytest with coverage (must pass)
3. **Security** -- Gitleaks, Trivy (vulnerability scan), Semgrep (SAST), Syft (SBOM)
4. **Report** -- Security and quality report
5. **Build** -- Docker image build and release

## Database deployment (ace-database)

The project includes a standalone Helm chart for deploying PostgreSQL with the pgvector extension.

**What it deploys:**

- PostgreSQL 16 with pgvector 0.8.1 (image: `pgvector/pgvector:0.8.1-pg16`)
- StatefulSet with 20Gi persistent volume
- LoadBalancer service (Azure internal) on port 5432
- Init script that creates the `ace` database, `scenarios` and `split_assignments` tables
- Post-install seed Job that populates the tables from training files stored in S3/MinIO (DVC cache)

**Prerequisites:**

- A Kubernetes cluster with Helm installed
- The target namespace must exist
- The `ace-credentials` Secret (PG_USER, PG_PASSWORD) must be applied before installing
- The `ace-s3-credentials` Secret (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY) must be applied for the seed job

**Deploy:**

```bash
# 1. Create the namespace
kubectl create namespace ace

# 2. Apply secrets (files not tracked in git)
kubectl apply -f deployments/ace-database/secret.yaml -n ace
kubectl apply -f deployments/ace-database/secret-s3.yaml -n ace

# 3. Install the Helm chart (seed job runs automatically)
helm install ace-database deployments/ace-database/ -n ace
```

**Verify:**

```bash
kubectl get all -n ace
kubectl get pvc -n ace
kubectl logs -n ace job/ace-database-seed-1
```

**Connection:**

| Parameter | Value |
|-----------|-------|
| Host | `db.ace.e2e.so.azure.datadope.co` |
| Port | `5432` |
| Database | `ace` |
| User | `ace-admin` (from secret) |
| Password | from secret `ace-credentials` |

**Upgrade after chart changes:**

```bash
helm upgrade ace-database deployments/ace-database/ -n ace
```

**Uninstall:**

```bash
helm uninstall ace-database -n ace
# PVC is retained by default, delete manually if needed:
# kubectl delete pvc data-ace-database-0 -n ace
```

## Database schema

The `ace` database contains two tables that act as a lightweight index of the scenarios stored in DVC/S3. Full case data (input, expected_output, fixtures, ground truth) lives in the object store.

**`scenarios`** -- Catalog of all 559 scenario variants:

| Column | Description |
|--------|-------------|
| `scenario_ref` (PK) | Unique case ID, e.g. `kubernetes-crashloop-KubeDeploymentReplicasMismatch--alert_rephrased-s1200` |
| `scenario_id` | Base scenario name, e.g. `kubernetes-crashloop` |
| `family` | Root cause family, e.g. `container_error`, `config_error` |
| `root_cause_key` | Specific root cause, e.g. `invalid_container_command` |
| `variation_type` | Variation applied: `alert_rephrased`, `namespace_shifted`, `noise_injected`, or `real` |
| `source_file` | JSON filename in DVC/S3, e.g. `kubernetes-crashloop--alert_rephrased-s1200.json` |
| `metadata` | Extra info as JSONB |

**`split_assignments`** -- Train/test splits per mode and fold:

| Column | Description |
|--------|-------------|
| `scenario_ref` (FK) | References `scenarios.scenario_ref` |
| `split_type` | Split mode: `stratified`, `leave_family_out`, `leave_scenario_out`, `leave_variation_out` |
| `fold_name` | Fold identifier, e.g. `default`, `hold_out_config_error`, `hold_out_kubernetes-crashloop` |
| `split` | `train` or `test` |

## Working with the dataset locally

The remote database is accessible from your local machine. Use it to query the scenario catalog, get train/test splits, and then fetch full case data from S3 when needed.

**Connect to the database:**

```bash
psql "postgresql://ace-admin:<password>@db.ace.e2e.so.azure.datadope.co:5432/ace"
```

**Example: Run a stratified training job**

The stratified split has a single fold (`default`) with an 80/20 train/test ratio:

```sql
-- Get all training scenarios for the stratified split
SELECT s.scenario_ref, s.scenario_id, s.family, s.source_file
FROM scenarios s
JOIN split_assignments sa ON s.scenario_ref = sa.scenario_ref
WHERE sa.split_type = 'stratified'
  AND sa.fold_name = 'default'
  AND sa.split = 'train';
```

In Python, the workflow would be:

```python
import psycopg2
import boto3
import json

conn = psycopg2.connect("postgresql://ace-admin:<password>@db.ace.e2e.so.azure.datadope.co:5432/ace")
cur = conn.cursor()

# 1. Query training set from the catalog
cur.execute("""
    SELECT s.scenario_ref, s.source_file
    FROM scenarios s
    JOIN split_assignments sa ON s.scenario_ref = sa.scenario_ref
    WHERE sa.split_type = 'stratified' AND sa.split = 'train'
""")
train_cases = cur.fetchall()

# 2. For each case, download the full data from S3/MinIO
s3 = boto3.client("s3",
    endpoint_url="https://minio-api.langfuse.e2e.so.azure.datadope.co:443",
    aws_access_key_id="...", aws_secret_access_key="...")

for ref, source_file in train_cases:
    # DVC stores datasets under a known prefix
    obj = s3.get_object(Bucket="dvc-ace", Key=f"data/datasets/{source_file}")
    case_data = json.loads(obj["Body"].read())
    # case_data has: input, expected_output, metadata, golden_entities
```

**Example: Leave-one-family-out cross-validation**

This mode has 11 folds, one per root cause family. Each fold holds out all scenarios of one family as the test set:

```sql
-- List available folds
SELECT DISTINCT fold_name, count(*) FILTER (WHERE split = 'train') as train,
                           count(*) FILTER (WHERE split = 'test') as test
FROM split_assignments
WHERE split_type = 'leave_family_out'
GROUP BY fold_name
ORDER BY fold_name;

-- Get test set for a specific fold
SELECT s.scenario_ref, s.family, s.source_file
FROM scenarios s
JOIN split_assignments sa ON s.scenario_ref = sa.scenario_ref
WHERE sa.split_type = 'leave_family_out'
  AND sa.fold_name = 'hold_out_config_error'
  AND sa.split = 'test';
```

**Example: Leave-one-scenario-out (19 folds)**

Each fold holds out all alerts from one base scenario:

```sql
SELECT s.scenario_ref, s.source_file
FROM scenarios s
JOIN split_assignments sa ON s.scenario_ref = sa.scenario_ref
WHERE sa.split_type = 'leave_scenario_out'
  AND sa.fold_name = 'hold_out_kubernetes-crashloop'
  AND sa.split = 'test';
```

**Example: Leave-one-variation-out (4 folds)**

Each fold holds out one variation type (`alert_rephrased`, `namespace_shifted`, `noise_injected`, `real`):

```sql
SELECT s.scenario_ref, s.source_file
FROM scenarios s
JOIN split_assignments sa ON s.scenario_ref = sa.scenario_ref
WHERE sa.split_type = 'leave_variation_out'
  AND sa.fold_name = 'hold_out_noise_injected'
  AND sa.split = 'test';
```

**Useful queries:**

```sql
-- Count scenarios by family
SELECT family, count(*) FROM scenarios GROUP BY family ORDER BY count DESC;

-- Count scenarios by variation type
SELECT variation_type, count(*) FROM scenarios GROUP BY variation_type;

-- List all base scenarios
SELECT DISTINCT scenario_id FROM scenarios ORDER BY scenario_id;

-- Summary of splits per mode
SELECT split_type, count(DISTINCT fold_name) as folds, count(*) as total_assignments
FROM split_assignments
GROUP BY split_type;
```

## Environment variables

Copy the template and fill in your values:

```bash
cp .env.example .env
```

Never commit `.env` files. They are in `.gitignore`.

## Key conventions

| Rule | Details |
|------|---------|
| Language | All code in English (variables, comments, logs, docstrings) |
| No emojis | Never use emojis in code, logs, or documentation |
| No print() | Use `logging.getLogger(__name__)` |
| Type hints | Required for all function parameters and return types |
| Docstrings | Google format, for all public functions |
| Exceptions | Use specific exceptions, not generic `Exception` |
| Logging format | `logger.info("User %s logged in", user_id)` (no f-strings) |

For full details, see the `docs/` directory.
