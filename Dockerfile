# ---------------------------------------------------------------------------
# Stage 1: base image with uv installed
# We use python:3.12-slim to keep the image small (~150MB vs ~900MB full).
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

WORKDIR /app

# Install system dependencies needed at build time
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Copy uv binary from its official Docker image (no need to pip install it)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# ---------------------------------------------------------------------------
# Stage 2: install Python dependencies
# We copy only pyproject.toml and uv.lock first so Docker can cache this
# layer. Dependencies are only reinstalled when these files change.
# ---------------------------------------------------------------------------
COPY pyproject.toml uv.lock* ./

RUN uv sync --no-dev --frozen

# ---------------------------------------------------------------------------
# Stage 3: copy application code
# This layer changes on every code change, but deps above stay cached.
# ---------------------------------------------------------------------------
COPY src/ ./src/

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
