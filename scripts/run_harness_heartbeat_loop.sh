#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROMPT_FILE="$PROJECT_ROOT/docs/harness/HEARTBEAT_PROMPT.md"
LOG_DIR="$PROJECT_ROOT/logs/harness-heartbeat"
INTERVAL_SECONDS="${1:-600}"
MAX_IDLE_ROUNDS="${2:-3}"
QTF_ACTIVATE="/opt/homebrew/Caskroom/miniforge/base/bin/activate"
QTF_ENV="QTF"
TASK_SYSTEM_FILE="$PROJECT_ROOT/docs/harness/task-system.md"
HARNESS_ALLOW_LOCAL_PORTS="${HARNESS_ALLOW_LOCAL_PORTS:-0}"

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

if [[ ! -f "$TASK_SYSTEM_FILE" ]]; then
  echo "Task system file not found: $TASK_SYSTEM_FILE" >&2
  exit 1
fi

if ! [[ "$MAX_IDLE_ROUNDS" =~ ^[0-9]+$ ]]; then
  echo "MAX_IDLE_ROUNDS must be a non-negative integer: $MAX_IDLE_ROUNDS" >&2
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

count_actionable_tasks() {
  python - "$TASK_SYSTEM_FILE" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
match = re.search(r"## 4\. 当前任务板\n(?P<body>.*?)(?:\n## 4\.1\b|\Z)", text, re.S)
body = match.group("body") if match else text
count = 0
for line in body.splitlines():
    if not line.startswith("|") or "---" in line:
        continue
    cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
    if len(cells) >= 3 and cells[2] in {"TODO", "DOING", "REVIEW"}:
        count += 1
print(count)
PY
}

echo "SmartQTF Harness heartbeat loop"
echo "Project: $PROJECT_ROOT"
echo "Prompt:  $PROMPT_FILE"
echo "Logs:    $LOG_DIR"
echo "Interval: ${INTERVAL_SECONDS}s"
echo "Max idle rounds: ${MAX_IDLE_ROUNDS}"
echo "Python:  $(command -v python)"
echo "Env:     ${CONDA_DEFAULT_ENV:-unknown}"
echo "Local ports: ${HARNESS_ALLOW_LOCAL_PORTS}"
echo "Press Ctrl+C to stop."
echo

update_dashboard

idle_rounds=0

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

  actionable_tasks="$(count_actionable_tasks)"
  echo "Actionable tasks in current board: ${actionable_tasks}" | tee -a "$log_file"

  if [[ "$actionable_tasks" -eq 0 ]]; then
    idle_rounds=$((idle_rounds + 1))
    echo "No TODO/DOING/REVIEW tasks found; idle round ${idle_rounds}/${MAX_IDLE_ROUNDS}." | tee -a "$log_file"
    if [[ "$MAX_IDLE_ROUNDS" -eq 0 || "$idle_rounds" -ge "$MAX_IDLE_ROUNDS" ]]; then
      echo "Max idle rounds reached; stopping heartbeat loop without another Codex run." | tee -a "$log_file"
      echo "===== Heartbeat $timestamp idle-stopped =====" | tee -a "$log_file"
      exit 0
    fi
    echo "Next idle check in ${INTERVAL_SECONDS}s." | tee -a "$log_file"
    sleep "$INTERVAL_SECONDS"
    continue
  fi

  idle_rounds=0

  CODEX_SANDBOX_ARGS=(--full-auto)
  if [[ "$HARNESS_ALLOW_LOCAL_PORTS" == "1" ]]; then
    CODEX_SANDBOX_ARGS+=(--sandbox danger-full-access -c sandbox_workspace_write.network_access=true)
    echo "Local port listening enabled for this Codex run via HARNESS_ALLOW_LOCAL_PORTS=1." | tee -a "$log_file"
  fi

  if codex exec \
    --cd "$PROJECT_ROOT" \
    "${CODEX_SANDBOX_ARGS[@]}" \
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
