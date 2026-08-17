#!/usr/bin/env bash
# Detached launcher for the isolated Native U0 baseline.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="${1:?usage: run_native_baseline_tmux.sh RUN_ID [HISTORY_LIMIT]}"
HISTORY_LIMIT="${2:-4}"
if [[ ! "$RUN_ID" =~ ^nb-[a-z0-9][a-z0-9-]{2,63}$ ]]; then
  echo "invalid run id" >&2
  exit 2
fi
if [[ ! "$HISTORY_LIMIT" =~ ^[1-4]$ ]]; then
  echo "history limit must be 1..4" >&2
  exit 2
fi

SESSION="membind-native-baseline-${RUN_ID}"
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "session already exists: $SESSION" >&2
  exit 3
fi
LOG="logs/NATIVE_BASELINE_${RUN_ID}.log"
tmux new-session -d -s "$SESSION" \
  "cd '$ROOT_DIR' && PYTHONPATH=src '../membind-validation/.venv/bin/python' scripts/run_native_baseline.py --run-id '$RUN_ID' --history-limit '$HISTORY_LIMIT' 2>&1 | tee -a '$LOG'"
echo "session=$SESSION"
echo "run_id=$RUN_ID"
echo "history_limit=$HISTORY_LIMIT"
echo "log=$ROOT_DIR/$LOG"
