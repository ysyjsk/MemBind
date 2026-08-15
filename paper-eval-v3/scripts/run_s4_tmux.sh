#!/usr/bin/env bash
# Start or resume the one authorized S4 smoke pipeline in detached tmux.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="membind-pev3-s4-smoke"
LOG="$PROJECT_DIR/logs/S4_D0_SMOKE_20260814.log"
AUTHORITY="$PROJECT_DIR/artifacts/paper_eval/native/S4_SMOKE_AUTHORIZATION.json"
CONSUMPTION="$PROJECT_DIR/artifacts/paper_eval/native/runs/s4-smoke-20260814-001/S4_SMOKE_AUTHORIZATION_CONSUMPTION.json"

if [[ -f "$PROJECT_DIR/artifacts/paper_eval/native/S4_SMOKE_AUTHORIZATION_RETRY_002.json" ]]; then
  AUTHORITY="$PROJECT_DIR/artifacts/paper_eval/native/S4_SMOKE_AUTHORIZATION_RETRY_002.json"
  CONSUMPTION="$PROJECT_DIR/artifacts/paper_eval/native/runs/s4-smoke-retry-002/S4_SMOKE_AUTHORIZATION_CONSUMPTION.json"
fi
if [[ -f "$PROJECT_DIR/artifacts/paper_eval/native/S4_SMOKE_AUTHORIZATION_RETRY_003.json" ]]; then
  AUTHORITY="$PROJECT_DIR/artifacts/paper_eval/native/S4_SMOKE_AUTHORIZATION_RETRY_003.json"
  CONSUMPTION="$PROJECT_DIR/artifacts/paper_eval/native/runs/s4-smoke-retry-003/S4_SMOKE_AUTHORIZATION_CONSUMPTION.json"
fi
if [[ -f "$PROJECT_DIR/artifacts/paper_eval/native/S4_SMOKE_AUTHORIZATION_RETRY_004.json" ]]; then
  AUTHORITY="$PROJECT_DIR/artifacts/paper_eval/native/S4_SMOKE_AUTHORIZATION_RETRY_004.json"
  CONSUMPTION="$PROJECT_DIR/artifacts/paper_eval/native/runs/s4-smoke-retry-004/S4_SMOKE_AUTHORIZATION_CONSUMPTION.json"
fi

if [[ ! -f "$AUTHORITY" ]]; then
  echo "missing S4 smoke authority: $AUTHORITY" >&2
  exit 2
fi
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "session already exists: $SESSION" >&2
  exit 3
fi

tmux new-session -d -s "$SESSION" \
  "cd '$PROJECT_DIR' && env PYTHONUNBUFFERED=1 PYTHONPATH=src '../membind-validation/.venv/bin/python' -u -m paper_eval.s4_controller --authority '$AUTHORITY' --consumption '$CONSUMPTION' 2>&1 | tee -a '$LOG'"

echo "session=$SESSION"
echo "log=$LOG"
echo "attach=tmux attach -t $SESSION"
echo "inspect=tmux capture-pane -pt $SESSION"
