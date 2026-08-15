#!/usr/bin/env bash
# Start or resume the one authorized S4 candidate-remap smoke in detached tmux.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="membind-pev3-s4-remap-005"
LOG="$PROJECT_DIR/logs/S4_D0_REMAP_SMOKE_20260815_005.log"
AUTHORITY="$PROJECT_DIR/artifacts/paper_eval/native/S4_REMAP_SMOKE_AUTHORIZATION_RETRY_005.json"
RESULT="$PROJECT_DIR/artifacts/paper_eval/native/S4_D0_REMAP_SMOKE_RESULT.json"

if [[ ! -f "$AUTHORITY" ]]; then
  echo "missing S4 remap authority: $AUTHORITY" >&2
  exit 2
fi
if [[ -f "$RESULT" ]]; then
  echo "S4 remap smoke already completed: $RESULT" >&2
  exit 4
fi
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "session already exists: $SESSION" >&2
  exit 3
fi

tmux new-session -d -s "$SESSION" -c "$PROJECT_DIR" \
  "set -o pipefail; env PYTHONUNBUFFERED=1 PYTHONPATH=src '../membind-validation/.venv/bin/python' -u -m paper_eval.s4_remap_controller 2>&1 | tee -a '$LOG'"

echo "session=$SESSION"
echo "log=$LOG"
echo "attach=tmux attach -t $SESSION"
echo "inspect=tmux capture-pane -pt $SESSION"
