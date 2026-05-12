#!/usr/bin/env bash
# IvyEdge Monday pipeline — run once every Monday morning.
# Order: extend calendar → inject trending topics → generate + publish ONE queued post
# Buffer posts are scheduled: image cards → Tuesday noon UTC, videos → Thursday noon UTC
# One article per week, one article per run. Never run this more than once on the same Monday.

set -euo pipefail

# Notify on exit — success or failure
_notify() {
  local exit_code=$?
  if [ $exit_code -eq 0 ]; then
    osascript -e 'display notification "Content pipeline finished — check Buffer for scheduled posts." with title "✅ IvyEdge Pipeline Done" sound name "Glass"' 2>/dev/null || true
  else
    osascript -e "display notification \"Pipeline failed with exit code $exit_code — check logs.\" with title \"❌ IvyEdge Pipeline Failed\" sound name \"Basso\"" 2>/dev/null || true
  fi
}
trap _notify EXIT

AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$AGENT_DIR/.venv/bin/python"
CALENDAR="$AGENT_DIR/editorial_calendar.csv"
LOG_DIR="$AGENT_DIR/logs"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d)_monday.log"

mkdir -p "$LOG_DIR"

log() {
  echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

cd "$AGENT_DIR"

log "========================================"
log "IvyEdge Monday pipeline starting"
log "========================================"

# ── Step 1: Extend the editorial calendar (4 weeks ahead, 1 post/week)
log "Step 1: Extending editorial calendar..."
"$PYTHON" calendar_agent.py \
  --weeks 4 \
  --posts-per-week 1 \
  --output "$CALENDAR" \
  >> "$LOG_FILE" 2>&1
log "Step 1 done."

# ── Step 2: Inject any trending topics from Google News
log "Step 2: Checking for trending topics..."
"$PYTHON" trend_monitor.py \
  --suggest-posts \
  --add-to-calendar "$CALENDAR" \
  >> "$LOG_FILE" 2>&1
log "Step 2 done."

# ── Step 3: Generate article and save to Substack as a DRAFT (not live)
#    Review it at https://substack.com/dashboard/posts
#    Then run:  bash approve.sh   (publishes + queues social)
#          or:  bash reject.sh    (removes topic, rerun this script for a new one)
log "Step 3: Running content pipeline (draft only)..."
"$PYTHON" run_pipeline.py \
  batch \
  --calendar "$CALENDAR" \
  >> "$LOG_FILE" 2>&1
log "Step 3 done — article saved as Substack draft. Run approve.sh or reject.sh."

log "========================================"
log "Monday pipeline complete. Log: $LOG_FILE"
log "========================================"
