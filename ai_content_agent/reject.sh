#!/usr/bin/env bash
# Remove the most recent drafted article from the calendar and delete its folder.
# Run this if you don't want to publish the article.
# Then rerun run_monday.sh to generate a replacement.

set -euo pipefail

AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$AGENT_DIR/.venv/bin/python"
CALENDAR="$AGENT_DIR/editorial_calendar.csv"

cd "$AGENT_DIR"

echo "========================================"
echo "IvyEdge reject — removing drafted article"
echo "========================================"

"$PYTHON" run_pipeline.py \
  --output output \
  reject \
  --calendar "$CALENDAR"

echo "========================================"
echo "Topic removed. Run 'bash run_monday.sh' to generate a replacement."
echo "========================================"
