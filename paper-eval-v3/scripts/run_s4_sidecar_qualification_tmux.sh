#!/usr/bin/env bash
# Resume the single authorized fixed-three pipeline in a detached tmux session.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="membind-pev3-s4-fixed-three-001"
LOG="$PROJECT_DIR/logs/S4_D0_FIXED_THREE_SIDECAR_20260815_001.log"
AUTHORITY="$PROJECT_DIR/artifacts/paper_eval/native/S4_D0_QUALIFICATION_EXECUTION_AUTHORITY_SIDECAR_V1.json"
RESULT="$PROJECT_DIR/artifacts/paper_eval/native/S4_D0_FIXED_THREE_RESULT_SIDECAR_V1.json"

if [[ ! -f "$AUTHORITY" ]]; then
  echo "missing fixed-three authority: $AUTHORITY" >&2
  exit 2
fi
if [[ -f "$RESULT" ]]; then
  echo "fixed-three qualification already completed: $RESULT" >&2
  exit 4
fi
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "session already exists: $SESSION" >&2
  exit 3
fi

tmux new-session -d -s "$SESSION" -c "$PROJECT_DIR" \
  "set -o pipefail; env PYTHONUNBUFFERED=1 PYTHONPATH=src '.venv/bin/python' -u -m paper_eval.s4_sidecar_qualification_controller --authority '$AUTHORITY' --result '$RESULT' 2>&1 | tee -a '$LOG'"

echo "session=$SESSION"
echo "log=$LOG"
echo "inspect=tmux capture-pane -pt $SESSION"
