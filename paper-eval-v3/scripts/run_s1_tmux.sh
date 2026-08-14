#!/usr/bin/env bash
# Start or resume exactly one isolated S1 controller in tmux.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${1:?usage: run_s1_tmux.sh RUN_ID NAMESPACE}"
NAMESPACE="${2:?usage: run_s1_tmux.sh RUN_ID NAMESPACE}"

if [[ ! "$RUN_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "invalid run id" >&2
  exit 2
fi
if [[ ! "$NAMESPACE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "invalid namespace" >&2
  exit 2
fi

SESSION="membind-pev3-s1-${RUN_ID}"
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "session already exists: $SESSION" >&2
  exit 3
fi

LOG="logs/S1_${RUN_ID}.log"
tmux new-session -d -s "$SESSION" \
  "cd '$ROOT_DIR' && PYTHONPATH=src '../membind-validation/.venv/bin/python' -m paper_eval.s1_controller --run-id '$RUN_ID' --namespace '$NAMESPACE' 2>&1 | tee '$LOG'"

echo "session=$SESSION"
echo "run_id=$RUN_ID"
echo "namespace=$NAMESPACE"
echo "log=$ROOT_DIR/$LOG"

