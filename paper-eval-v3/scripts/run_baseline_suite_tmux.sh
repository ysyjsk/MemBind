#!/usr/bin/env bash
# Reuse U0, then run A0 and P(C=2) under one durable parent process.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${BASELINE_SUITE_PYTHON:-$PROJECT_DIR/../membind-validation/.venv/bin/python}"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 RUN_ID --reuse-u0-run RUN_ID" >&2
  exit 2
fi

RUN_ID="$1"
shift
REUSE_U0_RUN=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reuse-u0-run)
      [[ $# -ge 2 ]] || { echo "--reuse-u0-run requires a value" >&2; exit 2; }
      REUSE_U0_RUN="$2"
      shift 2
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! "$RUN_ID" =~ ^bs-[a-z0-9][a-z0-9-]{2,63}$ ]]; then
  echo "invalid run id" >&2
  exit 2
fi
if [[ ! "$REUSE_U0_RUN" =~ ^nb-[a-z0-9][a-z0-9-]{2,63}$ ]]; then
  echo "invalid reuse U0 run id" >&2
  exit 2
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "baseline suite Python interpreter is unavailable" >&2
  exit 2
fi

SESSION="membind-three-baselines-${RUN_ID}"
LOG="$PROJECT_DIR/logs/THREE_BASELINES_${RUN_ID}.log"
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "session already exists: $SESSION" >&2
  exit 3
fi

COMMAND="cd '$PROJECT_DIR' && env PYTHONUNBUFFERED=1 PYTHONPATH='$PROJECT_DIR/src:$PROJECT_DIR/../membind-validation/src' '$PYTHON' -u scripts/run_three_baselines.py '$RUN_ID' --reuse-u0-run $REUSE_U0_RUN"
COMMAND="$COMMAND 2>&1 | tee -a '$LOG'"

tmux new-session -d -s "$SESSION" "$COMMAND"
echo "session=$SESSION"
echo "run_id=$RUN_ID"
echo "reuse_u0_run=$REUSE_U0_RUN"
echo "log=$LOG"
echo "inspect=tmux capture-pane -pt $SESSION -S -100"
