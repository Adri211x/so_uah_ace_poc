#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${SCRIPT_DIR}/data"
CACHE_DIR="${SCRIPT_DIR}/cache"
IMAGE="${CATAFRACTO_IMAGE:-nexusregistry.datadope.io/smartops/catafracto:latest}"

mkdir -p "$CACHE_DIR"

SCENARIOS=("$@")

if [ ${#SCENARIOS[@]} -eq 0 ]; then
    echo "Usage: $0 <scenario1> [scenario2] [scenario3] ..."
    echo "       $0 --all"
    echo ""
    echo "Examples:"
    echo "  $0 crashloop oomkilled"
    echo "  $0 --all"
    echo ""
    echo "Environment variables:"
    echo "  CATAFRACTO_IMAGE  Docker image (default: $IMAGE)"
    echo "  NEXUS_REGISTRY_USERNAME / NEXUS_REGISTRY_PASSWORD  for private registry"
    echo ""
    echo "Available databases:"
    if [ -d "$DATA_DIR/databases" ]; then
        for f in "$DATA_DIR/databases"/*.db; do
            [ -f "$f" ] && echo "  - $(basename "$f" .db | sed 's/^kubernetes-//')"
        done
    else
        echo "  (no databases found - run dvc pull first)"
    fi
    exit 1
fi

if [ "${SCENARIOS[0]}" = "--all" ]; then
    SCENARIOS=()
    for f in "$DATA_DIR/databases"/*.db; do
        [ -f "$f" ] && SCENARIOS+=("$(basename "$f" .db | sed 's/^kubernetes-//')")
    done
fi

echo "Pre-generating cache for ${#SCENARIOS[@]} scenarios using image: $IMAGE"
echo ""

for SCENARIO in "${SCENARIOS[@]}"; do
    DB_FILE="${DATA_DIR}/databases/kubernetes-${SCENARIO}.db"
    CACHE_FILE="${CACHE_DIR}/kubernetes-${SCENARIO}.json"

    if [ ! -f "$DB_FILE" ]; then
        echo "SKIP: $SCENARIO (no DB at $DB_FILE)"
        continue
    fi

    if [ -f "$CACHE_FILE" ]; then
        echo "SKIP: $SCENARIO (cache already exists at $CACHE_FILE)"
        continue
    fi

    echo "=== Recording: $SCENARIO ==="
    uv run python -m event_system.recorder \
        --db "$DB_FILE" \
        --output "$CACHE_FILE" \
        --catafracto-image "$IMAGE" \
        --session-id "rec-${SCENARIO}"
    echo "=== Done: $SCENARIO ==="
    echo ""
done

echo "All done. Caches in: $CACHE_DIR"
