#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIO="${1:-}"
DATA_DIR="${SCRIPT_DIR}/data"
CACHE_DIR="${SCRIPT_DIR}/cache"

if [ -z "$SCENARIO" ]; then
    echo "Usage: $0 <scenario_name> [--only kubectl,loki,...]"
    echo ""
    echo "Available scenarios (cached):"
    if [ -d "$CACHE_DIR" ]; then
        for f in "$CACHE_DIR"/*.json; do
            [ -f "$f" ] && echo "  - $(basename "$f" .json | sed 's/^kubernetes-//')"
        done
    else
        echo "  (no cache directory found - run pre_generate.sh first)"
    fi
    echo ""
    echo "Available scenarios (databases):"
    if [ -d "$DATA_DIR/databases" ]; then
        for f in "$DATA_DIR/databases"/*.db; do
            [ -f "$f" ] && echo "  - $(basename "$f" .db | sed 's/^kubernetes-//')"
        done
    fi
    exit 1
fi

CACHE_FILE="${CACHE_DIR}/kubernetes-${SCENARIO}.json"
DB_FILE="${DATA_DIR}/databases/kubernetes-${SCENARIO}.db"
DATASET_FILE="${DATA_DIR}/datasets/kubernetes-${SCENARIO}.json"
EXTRA_ARGS="${@:2}"

if [ ! -f "$CACHE_FILE" ]; then
    echo "ERROR: Cache file not found: $CACHE_FILE"
    echo "Run: ./pre_generate.sh $SCENARIO"
    exit 1
fi

DB_ARG=""
if [ -f "$DB_FILE" ]; then
    DB_ARG="--db $DB_FILE"
fi

echo ""
if [ -f "$DATASET_FILE" ]; then
    ITEMS=$(python3 -c "import json; print(len(json.load(open('$DATASET_FILE'))))" 2>/dev/null || echo "?")
    echo "Ground truth: $DATASET_FILE ($ITEMS items)"
    echo ""
fi

exec uv run python -m event_system.mock_mcp_server \
    --cache "$CACHE_FILE" \
    $DB_ARG \
    $EXTRA_ARGS
