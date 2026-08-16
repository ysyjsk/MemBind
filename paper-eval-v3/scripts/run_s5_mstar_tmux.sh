#!/usr/bin/env bash
# Launch exactly one authority-bound S5 M*(C=2) smoke and post-observation.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${S5_MSTAR_PYTHON:-$PROJECT_DIR/../membind-validation/.venv/bin/python}"
TMUX_BIN="${S5_MSTAR_TMUX_BIN:-tmux}"
CONTROLLER_MODULE="${S5_MSTAR_CONTROLLER_MODULE:-paper_eval.s5_mstar_controller}"
POSTPROCESS_MODULE="${S5_MSTAR_POSTPROCESS_MODULE:-paper_eval.s5_mstar_postprocess}"
RUNS_ROOT="${S5_MSTAR_RUNS_ROOT:-$PROJECT_DIR/artifacts/paper_eval/native/runs}"
LOG_DIR="${S5_MSTAR_LOG_DIR:-$PROJECT_DIR/logs}"
ARTIFACT_DIR="$PROJECT_DIR/artifacts/paper_eval/native"
PRODUCTION_IDENTITY="${S5_MSTAR_PRODUCTION_IDENTITY:?S5_MSTAR_PRODUCTION_IDENTITY is required}"
PRODUCTION_IDENTITY_QUALIFICATION="${S5_MSTAR_PRODUCTION_IDENTITY_QUALIFICATION:?S5_MSTAR_PRODUCTION_IDENTITY_QUALIFICATION is required}"
PRODUCTION_CORE_IDENTITY="${S5_MSTAR_PRODUCTION_CORE_IDENTITY:?S5_MSTAR_PRODUCTION_CORE_IDENTITY is required}"
FX0_QUALIFICATION="${S5_MSTAR_FX0_QUALIFICATION:?S5_MSTAR_FX0_QUALIFICATION is required}"
PREFLIGHT="${S5_MSTAR_PREFLIGHT:?S5_MSTAR_PREFLIGHT is required}"
PREDECESSOR="${S5_MSTAR_PREDECESSOR:?S5_MSTAR_PREDECESSOR is required}"
CURRENT_STAGE_POINTER="${S5_MSTAR_CURRENT_STAGE_POINTER:-$PROJECT_DIR/runtime/CURRENT_STAGE_STATUS.json}"
ENV_FILE="${S5_MSTAR_ENV_FILE:-$PROJECT_DIR/../membind-validation/.env}"

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <sealed-s5-mstar-authority.json>" >&2
  exit 2
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "S5 M* Python interpreter is unavailable" >&2
  exit 2
fi
for module in "$CONTROLLER_MODULE" "$POSTPROCESS_MODULE"; do
  if [[ ! "$module" =~ ^[A-Za-z_][A-Za-z0-9_.]*$ ]]; then
    echo "S5 M* module identity is invalid" >&2
    exit 2
  fi
done

AUTHORITY_INPUT="$1"
if [[ ! -f "$AUTHORITY_INPUT" ]]; then
  echo "sealed S5 M* authority is unavailable" >&2
  exit 2
fi
AUTHORITY="$(cd "$(dirname "$AUTHORITY_INPUT")" && pwd)/$(basename "$AUTHORITY_INPUT")"

if ! AUTHORITY_IDENTITY="$(
  PYTHONPATH="$PROJECT_DIR/src" "$PYTHON" - "$AUTHORITY" 2>/dev/null <<'PY'
import json
import sys
from pathlib import Path

from paper_eval.s5_live_authority import verify_s5_live_authority

verified = verify_s5_live_authority(
    json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
)
payload = verified["payload"]
run = payload["run"]
if payload["method"] != "M*" or run["method"] != "M*":
    raise SystemExit(2)
sys.stdout.write(
    f"{run['run_id']}\t{run['namespace']}\t{verified['git_commit']}\t"
    f"{run['configured_concurrency']}"
)
PY
)"; then
  echo "sealed S5 M* authority verification failed" >&2
  exit 2
fi

if [[ "$AUTHORITY_IDENTITY" == *$'\n'* ]]; then
  echo "verified M* authority identity is invalid" >&2
  exit 2
fi
IFS=$'\t' read -r RUN_ID NAMESPACE GIT_COMMIT CONCURRENCY <<< "$AUTHORITY_IDENTITY"
if [[ ! "$RUN_ID" =~ ^s5-mstar-[0-9]{8}-[0-9]{3}$ ]] \
  || [[ "$NAMESPACE" != "pev3-$RUN_ID" ]] \
  || [[ ! "$GIT_COMMIT" =~ ^[0-9a-f]{40}$ ]] \
  || [[ "$CONCURRENCY" != "2" ]] \
  || [[ "$AUTHORITY_IDENTITY" != "$RUN_ID"$'\t'"$NAMESPACE"$'\t'"$GIT_COMMIT"$'\t'"$CONCURRENCY" ]]; then
  echo "verified M* authority identity is invalid" >&2
  exit 2
fi

for input in \
  "$PRODUCTION_IDENTITY" \
  "$PRODUCTION_IDENTITY_QUALIFICATION" \
  "$PRODUCTION_CORE_IDENTITY" \
  "$FX0_QUALIFICATION" \
  "$PREFLIGHT" \
  "$PREDECESSOR" \
  "$CURRENT_STAGE_POINTER" \
  "$ENV_FILE"; do
  if [[ ! -f "$input" ]]; then
    echo "S5 M* controller input is unavailable" >&2
    exit 2
  fi
done

for value in \
  "$PROJECT_DIR" "$PYTHON" "$AUTHORITY" "$PRODUCTION_IDENTITY" \
  "$PRODUCTION_IDENTITY_QUALIFICATION" "$PRODUCTION_CORE_IDENTITY" \
  "$FX0_QUALIFICATION" "$PREFLIGHT" "$PREDECESSOR" \
  "$CURRENT_STAGE_POINTER" "$ENV_FILE" "$RUNS_ROOT" "$LOG_DIR"; do
  if [[ "$value" == *"'"* || "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "S5 M* path is invalid" >&2
    exit 2
  fi
done

RUN_ROOT="$RUNS_ROOT/$RUN_ID"
CONSUMPTION="$RUN_ROOT/authority_consumption.json"
CONTROLLER_ROOT="$RUN_ROOT/controller"
ATTEMPT_ROOT="$RUN_ROOT/attempt"
POST_OBSERVATION="$RUN_ROOT/post_observation.json"
FINAL_RESULT="$RUN_ROOT/S5_MSTAR_RESULT.json"
POSTPROCESS_CHECKPOINT="$RUN_ROOT/postprocess/checkpoint.json"
SESSION="membind-pev3-$RUN_ID"
LOG="$LOG_DIR/${RUN_ID}.log"

for target in \
  "$CONSUMPTION" "$CONTROLLER_ROOT" "$ATTEMPT_ROOT" \
  "$POST_OBSERVATION" "$FINAL_RESULT" "$POSTPROCESS_CHECKPOINT" "$LOG"; do
  if [[ -e "$target" || -L "$target" ]]; then
    echo "S5 M* single-use output already exists" >&2
    exit 3
  fi
done
if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
  echo "S5 M* tmux session already exists" >&2
  exit 3
fi
if [[ ! -d "$RUNS_ROOT" || ! -d "$LOG_DIR" ]]; then
  echo "S5 M* output root is unavailable" >&2
  exit 2
fi
mkdir -p "$RUN_ROOT"

COMMON_ARGS="--production-identity '$PRODUCTION_IDENTITY' --production-identity-qualification '$PRODUCTION_IDENTITY_QUALIFICATION' --production-core-identity '$PRODUCTION_CORE_IDENTITY' --fx0-qualification '$FX0_QUALIFICATION' --preflight '$PREFLIGHT' --authority '$AUTHORITY' --predecessor '$PREDECESSOR' --current-stage-pointer '$CURRENT_STAGE_POINTER' --env-file '$ENV_FILE' --run-root '$RUN_ROOT' --git-commit '$GIT_COMMIT'"
"$TMUX_BIN" new-session -d -s "$SESSION" -c "$PROJECT_DIR" \
  "set -o pipefail; { env PYTHONUNBUFFERED=1 PYTHONPATH='$PROJECT_DIR/src' '$PYTHON' -u -m '$CONTROLLER_MODULE' $COMMON_ARGS && env PYTHONUNBUFFERED=1 PYTHONPATH='$PROJECT_DIR/src' '$PYTHON' -u -m '$POSTPROCESS_MODULE' $COMMON_ARGS; } 2>&1 | tee -a '$LOG'"

echo "session=$SESSION"
echo "log=$LOG"
echo "inspect=tmux capture-pane -pt $SESSION"
