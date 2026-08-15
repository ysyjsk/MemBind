#!/usr/bin/env bash
# Start the single authorized retry-007 pipeline in a detached tmux session.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="membind-pev3-s4-sidecar-007"
LOG="$PROJECT_DIR/logs/S4_D0_SIDECAR_SMOKE_20260815_007.log"
AUTHORITY="$PROJECT_DIR/artifacts/paper_eval/native/S4_SIDECAR_SMOKE_AUTHORIZATION_RETRY_007.json"
CONSUMPTION="$PROJECT_DIR/artifacts/paper_eval/native/runs/s4-sidecar-smoke-retry-007/S4_SIDECAR_AUTHORITY_CONSUMPTION.json"
RESULT="$PROJECT_DIR/artifacts/paper_eval/native/S4_D0_SIDECAR_SMOKE_RESULT_RETRY_007.json"

if [[ ! -f "$AUTHORITY" ]]; then
  echo "missing S4 sidecar authority: $AUTHORITY" >&2
  exit 2
fi
if [[ -f "$RESULT" ]]; then
  echo "S4 sidecar retry-007 already completed: $RESULT" >&2
  exit 4
fi
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "session already exists: $SESSION" >&2
  exit 3
fi

tmux new-session -d -s "$SESSION" -c "$PROJECT_DIR" \
  "set -o pipefail; env PYTHONUNBUFFERED=1 PYTHONPATH=src '../membind-validation/.venv/bin/python' -u -m paper_eval.s4_sidecar_controller --authority '$AUTHORITY' --consumption '$CONSUMPTION' --result '$RESULT' 2>&1 | tee -a '$LOG'"

echo "session=$SESSION"
echo "log=$LOG"
echo "inspect=tmux capture-pane -pt $SESSION"
