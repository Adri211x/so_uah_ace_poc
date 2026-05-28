# ACE -- Agentic Context Engineering

Platform for building self-improving AI agents through iterative context refinement and evaluation.

## Prerequisites

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

# 3. Configure environment variables
cp .env.example .env
# Edit .env and set at least LITELLM_API_KEY

# 4. Start the dev server
just dev
```

Run `just` (without arguments) to see all available commands.

## Project structure

```
so_ua_ace_poc/
|
|-- src/app/                       # FastAPI application
|   |-- api/                       #   HTTP endpoints (FastAPI routes)
|   |-- services/                  #   Business logic layer
|   |-- repositories/              #   Data access layer
|   |-- models/                    #   Database models (SQLAlchemy)
|   |-- schemas/                   #   Request/response schemas (Pydantic)
|   |-- core/                      #   Shared config, exceptions, utilities
|
|-- src/agents/                    # RCA agents under evaluation
|   |-- agent_a_ace/               #   ACE agent
|   |-- agent_b_baseline/          #   Baseline agent
|
|-- src/common/                    # Shared evaluation pipeline
|   |-- runner.py                  #   Runs agents over a split, writes results JSON
|   |-- scoring.py                 #   Literal golden-entity scoring (Levenshtein)
|   |-- judge.py                   #   LLM-as-a-judge scorer
|   |-- cosine_similarity.py       #   Embedding-based cosine similarity scorer
|   |-- cosine_backfill.py         #   Adds cosine scores to existing results JSON
|   |-- config.py                  #   Settings loaded from .env
|
|-- tests/                         # Unit and integration tests
|
|-- deployments/ace-database/      # Helm chart: PostgreSQL + pgvector
|
|-- data/event_system/             # Benchmark dataset (DVC-managed)
|
|-- docs/                          # Documentation
```

## Evaluation pipeline

The runner executes an RCA agent over the `test` rows of a split file and writes
one result per sample. Each result combines three complementary scorers:

| Scorer | Field(s) in results | What it measures |
|--------|---------------------|------------------|
| Literal (`scoring.py`) | `score`, `matched_entities` | Golden entities found literally in the output (normalized Levenshtein) |
| LLM judge (`judge.py`) | `judge_verdict` | Qualitative RCA quality vs. ground truth (root cause, evidence, completeness) |
| Cosine (`cosine_similarity.py`) | `cosine_similarity` | Semantic closeness via multilingual sentence embeddings |

The cosine scorer uses `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
(falls back to `paraphrase-multilingual-mpnet-base-v2`) and reports the
RCA-vs-expected similarity plus per-golden-entity similarities and aggregates.
All scorers degrade gracefully: a failure stores a null verdict instead of
aborting the run.

```bash
# Run an agent over a split (judge and cosine enabled by default)
uv run python -m src.common.runner \
    --agent agent_a_ace \
    --training-file tmp/test_3.json \
    --output-file tmp/test_3_results.json \
    --dataset-dir data/event_system/data/datasets

# Opt out of the heavier scorers for fast/offline runs
uv run python -m src.common.runner ... --no-judge --no-cosine

# Add cosine scores to an existing results file without re-running agents
uv run python -m src.common.cosine_backfill \
    --input tmp/test_3_results.json \
    --output tmp/test_3_results.json
```

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

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | Layered architecture, ACE components, data flow |
| [Development guide](docs/development-guide.md) | Workflow, branching, commits, CI/CD, conventions |
| [Database](docs/database.md) | Deployment, schema (cases/runs/contexts), connection |
| [Dataset](docs/dataset.md) | Scenarios, train/test splits, SQL queries, Python examples |
| [Dataset analysis](data/event_system/dataset.md) | Statistical analysis, composition, splits, and evaluation methodology |
| [ACE paper](docs/ace-original-paper-2510.04618v3.pdf) | Original research paper (arXiv:2510.04618v3) |
