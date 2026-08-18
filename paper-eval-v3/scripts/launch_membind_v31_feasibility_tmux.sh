#!/usr/bin/env bash
# Reproducible long-run launcher. Graphiti is pinned in the legacy venv;
# paper-eval-v3 remains the source/artifact lane and is never copied into it.
set -euo pipefail

PROJECT_DIR="/data/predator/ly/MemBind/paper-eval-v3"
ROOT_DIR="/data/predator/ly/MemBind"
PYTHON_BIN="$ROOT_DIR/membind-validation/.venv/bin/python"
SESSION_NAME="${1:?tmux session name required}"
ATTEMPT_ID="${2:?attempt id required}"
ATTEMPT_ROOT="${3:?attempt root required}"

cd "$PROJECT_DIR"
test -x "$PYTHON_BIN"
"$PYTHON_BIN" -c 'import graphiti_core' >/dev/null

exec tmux new-session -d -s "$SESSION_NAME" \
  "cd '$PROJECT_DIR' && '$PYTHON_BIN' scripts/run_membind_v31_single_history.py \
    --smoke-gate artifacts/paper_eval/membind_v31/runs/membind-v31-smoke-20260818-004/SMOKE_GATE.json \
    --cleanup-evidence artifacts/paper_eval/membind_v31/feasibility/CLEANUP_EVIDENCE_AFTER_ATTEMPT_003.json \
    --provider-envelope artifacts/paper_eval/membind_v31/PROVIDER_EXECUTION_ENVELOPE_XGRAMMAR_20260819.json \
    --attempt-root '$ATTEMPT_ROOT' --attempt-id '$ATTEMPT_ID' \
    2>&1 | tee logs/${ATTEMPT_ID}.log"
