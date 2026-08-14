#!/usr/bin/env bash
# Launch the single authorized S2 live chain in a detached, durable session.
set -euo pipefail

if [[ $# -ne 1 ]] || [[ ! $1 =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$ ]]; then
  echo "usage: $0 <validated-run-id>" >&2
  exit 2
fi

RUN_ID=$1
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
REPO_DIR=$(cd -- "$PROJECT_DIR/.." && pwd)
PYTHON="$REPO_DIR/membind-validation/.venv/bin/python"
SESSION="pev3-${RUN_ID//./-}"
LOG_DIR="$PROJECT_DIR/logs"
LOG_PATH="$LOG_DIR/${RUN_ID}.log"

if [[ ! -x $PYTHON ]]; then
  echo "legacy runtime python is unavailable" >&2
  exit 2
fi
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session already exists: $SESSION" >&2
  exit 3
fi

mkdir -p -- "$LOG_DIR"
COMMAND="exec env PYTHONUNBUFFERED=1 PYTHONPATH='$PROJECT_DIR/src:$REPO_DIR/membind-validation/src' '$PYTHON' -u -m paper_eval.s2_controller --run-id '$RUN_ID' >>'$LOG_PATH' 2>&1"
tmux new-session -d -s "$SESSION" -c "$PROJECT_DIR" "$COMMAND"

echo "session=$SESSION"
echo "log=$LOG_PATH"
