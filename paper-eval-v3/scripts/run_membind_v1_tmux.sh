#!/usr/bin/env bash
# Launch the isolated aligned MemBind-v1 development table in one tmux parent.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${MEMBIND_V1_PYTHON:-$PROJECT_DIR/../membind-validation/.venv/bin/python}"

if [[ $# -ne 2 ]]; then
  echo "usage: $0 ALIGNED_RUN_ID MAIN_TABLE_RUN_ID" >&2
  exit 2
fi

ALIGNED_RUN_ID="$1"
MAIN_TABLE_RUN_ID="$2"

if [[ ! "$ALIGNED_RUN_ID" =~ ^aligned-[a-z0-9][a-z0-9-]{2,63}$ ]]; then
  echo "invalid aligned run id" >&2
  exit 2
fi
if [[ ! "$MAIN_TABLE_RUN_ID" =~ ^main-table-[a-z0-9][a-z0-9-]{2,63}$ ]]; then
  echo "invalid main table run id" >&2
  exit 2
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "MemBind-v1 Python interpreter is unavailable" >&2
  exit 2
fi

SESSION="membind-v1-${ALIGNED_RUN_ID}"
LOG="$PROJECT_DIR/logs/MEMBIND_V1_${ALIGNED_RUN_ID}_${MAIN_TABLE_RUN_ID}.log"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "session already exists: $SESSION" >&2
  exit 3
fi

mkdir -p "$PROJECT_DIR/logs"
COMMAND="set -o pipefail; cd '$PROJECT_DIR' && env PYTHONUNBUFFERED=1 PYTHONPATH='$PROJECT_DIR/src:$PROJECT_DIR/../membind-validation/src' '$PYTHON' -u scripts/run_membind_v1.py --aligned-run-id '$ALIGNED_RUN_ID' --main-table-run-id '$MAIN_TABLE_RUN_ID' 2>&1 | tee -a '$LOG'"

tmux new-session -d -s "$SESSION" -c "$PROJECT_DIR" "$COMMAND"

echo "session=$SESSION"
echo "aligned_run_id=$ALIGNED_RUN_ID"
echo "main_table_run_id=$MAIN_TABLE_RUN_ID"
echo "log=$LOG"
echo "inspect=tmux capture-pane -pt $SESSION -S -100"
