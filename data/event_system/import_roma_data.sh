#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROMA_SCIPIO="${1:-../roma/scipio}"

if [ ! -d "$ROMA_SCIPIO" ]; then
    echo "Usage: $0 <path-to-roma-scipio>"
    echo "  Default: ../roma/scipio"
    echo ""
    echo "This script pulls data from DVC and copies it to event_system/data/"
    exit 1
fi

ROMA_ABS="$(cd "$ROMA_SCIPIO" && pwd)"
DATA_DIR="${SCRIPT_DIR}/data"

mkdir -p "$DATA_DIR/databases" "$DATA_DIR/datasets"

echo "Pulling DVC data from: $ROMA_ABS"
cd "$ROMA_ABS"

echo ""
echo "=== Pulling dataset JSONs ==="
uv run dvc pull data/datasets/ 2>&1 || echo "(some pulls may have failed)"

echo ""
echo "=== Pulling SQLite databases ==="
uv run dvc pull data/databases/ 2>&1 || echo "(some pulls may have failed)"

echo ""
echo "=== Copying to event_system/data/ ==="

for f in "$ROMA_ABS/data/datasets"/*.json; do
    [ -f "$f" ] && cp -v "$f" "$DATA_DIR/datasets/"
done

for f in "$ROMA_ABS/data/databases"/*.db; do
    [ -f "$f" ] && cp -v "$f" "$DATA_DIR/databases/"
done

echo ""
echo "=== Summary ==="
echo "Datasets: $(ls "$DATA_DIR/datasets/"*.json 2>/dev/null | wc -l) files"
echo "Databases: $(ls "$DATA_DIR/databases/"*.db 2>/dev/null | wc -l) files"
echo ""
echo "Next steps:"
echo "  1. Pre-generate cache:  ./pre_generate.sh crashloop"
echo "  2. Start mock server:   ./start_scenario.sh crashloop"
