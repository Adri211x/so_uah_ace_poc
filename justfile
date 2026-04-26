# =============================================================================
# Project commands
# Run `just` to see all available commands with descriptions.
# Install just: https://github.com/casey/just#installation
# =============================================================================

# Show all available commands
default:
    @just --list

# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------

# First-time project setup (install deps + pre-commit hooks)
setup:
    uv sync
    pre-commit install
    @echo ""
    @echo "Project ready. Run 'just dev' to start the app."

# Install dependencies only
install:
    uv sync

# --------------------------------------------------------------------------
# Development
# --------------------------------------------------------------------------

# Start the development server with auto-reload
dev:
    uv run uvicorn src.app.main:app --reload --host 0.0.0.0 --port 8000

# Run the app without auto-reload (production-like)
run:
    uv run uvicorn src.app.main:app --host 0.0.0.0 --port 8000

# --------------------------------------------------------------------------
# Code quality
# --------------------------------------------------------------------------

# Run linter (Ruff) and auto-fix issues
lint:
    uv run ruff check --fix .

# Format code with Ruff
fmt:
    uv run ruff format .

# Run linter + formatter together
check: lint fmt

# --------------------------------------------------------------------------
# Testing
# --------------------------------------------------------------------------

# Run all tests
test:
    uv run pytest

# Run tests with coverage report
test-cov:
    uv run pytest --cov=src --cov-report=term-missing

# Run only unit tests
test-unit:
    uv run pytest tests/unit/

# Run only integration tests
test-integration:
    uv run pytest tests/integration/

# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------

# Add a production dependency (usage: just add fastapi)
add package:
    uv add {{ package }}

# Add a development dependency (usage: just add-dev pytest-mock)
add-dev package:
    uv add --dev {{ package }}

# Update all dependencies
update:
    uv lock --upgrade && uv sync

# --------------------------------------------------------------------------
# Pre-commit & security
# --------------------------------------------------------------------------

# Run all pre-commit hooks on all files
pre-commit:
    pre-commit run --all-files

# Run gitleaks to check for secrets
gitleaks:
    gitleaks detect --source . --config .gitleaks.toml

# --------------------------------------------------------------------------
# Data (event_system)
# --------------------------------------------------------------------------

# Pull dataset files from MinIO via DVC
dvc-pull:
    dvc pull

# Push dataset files to MinIO via DVC
dvc-push:
    dvc push

# Run event_system dataset tests (requires dvc pull first)
test-data:
    cd data/event_system && uv sync --extra dev && uv run pytest tests/ -v

# Start mock MCP server for a scenario (usage: just mock-server crashloop)
mock-server scenario:
    cd data/event_system && uv sync && uv run event-mock --cache cache/kubernetes-{{ scenario }}.json

# List available splits and folds from the ACE database or local files
cases-list:
    cd data/event_system && uv run event-runner list-splits

# List folds for a split type (usage: just cases-folds leave_family_out)
cases-folds split_type:
    cd data/event_system && uv run event-runner list-folds {{ split_type }}

# Show summary for a split/fold (usage: just cases-summary stratified default)
cases-summary split fold="default":
    cd data/event_system && uv run event-runner summary {{ split }} {{ fold }}

# Export cases to JSON (usage: just cases-export stratified default test)
cases-export split fold="default" partition="test":
    cd data/event_system && uv run event-runner export --split {{ split }} --fold {{ fold }} --partition {{ partition }}

# Run mock interactively through all cases in a split (usage: just cases-serve dev)
cases-serve split="dev" fold="default" partition="dev":
    cd data/event_system && uv run event-runner serve --split {{ split }} --fold {{ fold }} --partition {{ partition }}

# --------------------------------------------------------------------------
# Docker
# --------------------------------------------------------------------------

# Build the Docker image
docker-build:
    docker build -t so_ua_ace_poc:latest .

# Run the Docker container
docker-run:
    docker run --rm -p 8000:8000 --env-file .env so_ua_ace_poc:latest

# --------------------------------------------------------------------------
# Cleanup
# --------------------------------------------------------------------------

# Remove generated/cached files
clean:
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    rm -f .coverage
