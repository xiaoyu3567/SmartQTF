#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROMPT_FILE="$PROJECT_ROOT/docs/harness/HEARTBEAT_PROMPT.md"
LOG_DIR="$PROJECT_ROOT/logs/harness-heartbeat"
INTERVAL_SECONDS="${1:-600}"
QTF_ACTIVATE="/opt/homebrew/Caskroom/miniforge/base/bin/activate"
QTF_ENV="QTF"

mkdir -p "$LOG_DIR"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI not found in PATH" >&2
  exit 1
fi

if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "Prompt file not found: $PROMPT_FILE" >&2
  exit 1
fi

if [[ ! -f "$QTF_ACTIVATE" ]]; then
  echo "QTF activate script not found: $QTF_ACTIVATE" >&2
  exit 1
fi

source "$QTF_ACTIVATE" "$QTF_ENV"

update_dashboard() {
  local log_file="${1:-}"
  if [[ -n "$log_file" ]]; then
    echo "Refreshing harness dashboard data..." | tee -a "$log_file"
    if python "$PROJECT_ROOT/scripts/update_harness_dashboard.py" 2>&1 | tee -a "$log_file"; then
      return 0
    fi
    echo "Dashboard refresh failed." | tee -a "$log_file"
    return 0
  fi

  echo "Refreshing harness dashboard data..."
  if ! python "$PROJECT_ROOT/scripts/update_harness_dashboard.py"; then
    echo "Dashboard refresh failed."
  fi
}

echo "SmartQTF Harness heartbeat loop"
echo "Project: $PROJECT_ROOT"
echo "Prompt:  $PROMPT_FILE"
echo "Logs:    $LOG_DIR"
echo "Interval: ${INTERVAL_SECONDS}s"
echo "Python:  $(command -v python)"
echo "Env:     ${CONDA_DEFAULT_ENV:-unknown}"
echo "Press Ctrl+C to stop."
echo

update_dashboard

while true; do
  timestamp="$(date '+%Y%m%d-%H%M%S')"
  log_file="$LOG_DIR/heartbeat-$timestamp.log"
  final_file="$LOG_DIR/heartbeat-$timestamp.final.md"

  {
    echo "===== Heartbeat $timestamp started ====="
    date
    echo "Python: $(command -v python)"
    echo "Env: ${CONDA_DEFAULT_ENV:-unknown}"
    echo
  } | tee "$log_file"

  update_dashboard "$log_file"

  if codex exec \
    --cd "$PROJECT_ROOT" \
    --full-auto \
    --output-last-message "$final_file" \
    - < "$PROMPT_FILE" 2>&1 | tee -a "$log_file"; then
    update_dashboard "$log_file"
    echo "===== Heartbeat $timestamp completed =====" | tee -a "$log_file"
  else
    update_dashboard "$log_file"
    echo "===== Heartbeat $timestamp failed =====" | tee -a "$log_file"
  fi

  echo "Next heartbeat in ${INTERVAL_SECONDS}s." | tee -a "$log_file"
  sleep "$INTERVAL_SECONDS"
done
