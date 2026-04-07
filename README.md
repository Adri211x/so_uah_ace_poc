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

# 3. Start the dev server
just dev
```

Run `just` (without arguments) to see all available commands.

## Project structure

```
so_ua_ace_poc/
|
|-- src/app/                       # Application source code
|   |-- api/                       #   HTTP endpoints (FastAPI routes)
|   |-- services/                  #   Business logic layer
|   |-- repositories/              #   Data access layer
|   |-- models/                    #   Database models (SQLAlchemy)
|   |-- schemas/                   #   Request/response schemas (Pydantic)
|   |-- core/                      #   Shared config, exceptions, utilities
|
|-- tests/                         # Unit and integration tests
|
|-- deployments/ace-database/      # Helm chart: PostgreSQL + pgvector
|
|-- data/event_system/             # Benchmark dataset (DVC-managed)
|
|-- docs/                          # Documentation
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
