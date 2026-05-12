#!/usr/bin/env bash
# Publish the most recent Substack draft live and queue all social media posts.
# Run this after reviewing the draft at https://substack.com/dashboard/posts

set -euo pipefail

AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$AGENT_DIR/.venv/bin/python"
CALENDAR="$AGENT_DIR/editorial_calendar.csv"
LOG_DIR="$AGENT_DIR/logs"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d)_approve.log"

mkdir -p "$LOG_DIR"

_notify() {
  local exit_code=$?
  if [ $exit_code -eq 0 ]; then
    osascript -e 'display notification "Article published and social posts queued in Buffer." with title "✅ IvyEdge Approved" sound name "Glass"' 2>/dev/null || true
  else
    osascript -e "display notification \"Approve failed with exit code $exit_code — check logs.\" with title \"❌ IvyEdge Approve Failed\" sound name \"Basso\"" 2>/dev/null || true
  fi
}
trap _notify EXIT

cd "$AGENT_DIR"

echo "========================================"
echo "IvyEdge approve — publishing draft live"
echo "========================================"

"$PYTHON" run_pipeline.py \
  --output output \
  approve \
  --calendar "$CALENDAR" \
  | tee -a "$LOG_FILE"

echo "========================================"
echo "Done. Check Buffer for scheduled posts."
echo "========================================"
