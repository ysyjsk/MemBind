#!/usr/bin/env bash
# Launch exactly one authority-bound S5 A0 smoke in detached tmux.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${S5_A0_PYTHON:-$PROJECT_DIR/../membind-validation/.venv/bin/python}"
TMUX_BIN="${S5_A0_TMUX_BIN:-tmux}"
CONTROLLER_MODULE="${S5_A0_CONTROLLER_MODULE:-paper_eval.s5_a0_controller}"
POSTPROCESS_MODULE="${S5_A0_POSTPROCESS_MODULE:-paper_eval.s5_a0_postprocess}"
RUNS_ROOT="${S5_A0_RUNS_ROOT:-$PROJECT_DIR/artifacts/paper_eval/native/runs}"
LOG_DIR="${S5_A0_LOG_DIR:-$PROJECT_DIR/logs}"
ARTIFACT_DIR="$PROJECT_DIR/artifacts/paper_eval/native"
PRODUCTION_IDENTITY="${S5_A0_PRODUCTION_IDENTITY:-$ARTIFACT_DIR/S5_A0_PRODUCTION_IDENTITY_20260816.json}"
PRODUCTION_IDENTITY_QUALIFICATION="${S5_A0_PRODUCTION_IDENTITY_QUALIFICATION:-$ARTIFACT_DIR/S5_A0_PRODUCTION_IDENTITY_QUALIFICATION_20260816.json}"
PREFLIGHT="${S5_A0_PREFLIGHT:-$ARTIFACT_DIR/S5_A0_LIVE_PREFLIGHT_20260816.json}"
RUNTIME_CONFIG="${S5_A0_RUNTIME_CONFIG:-$ARTIFACT_DIR/S5_A0_RUNTIME_CONFIG_20260816.json}"
IDENTITY_MATERIALIZATION="${S5_A0_IDENTITY_MATERIALIZATION:-$ARTIFACT_DIR/S5_A0_PRODUCTION_IDENTITY_MATERIALIZATION_20260816.json}"
CURRENT_STAGE_POINTER="${S5_A0_CURRENT_STAGE_POINTER:-$PROJECT_DIR/runtime/CURRENT_STAGE_STATUS.json}"
ENV_FILE="${S5_A0_ENV_FILE:-$PROJECT_DIR/../membind-validation/.env}"

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <sealed-s5-a0-authority.json>" >&2
  exit 2
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "S5 A0 Python interpreter is unavailable" >&2
  exit 2
fi
if [[ ! "$CONTROLLER_MODULE" =~ ^[A-Za-z_][A-Za-z0-9_.]*$ ]]; then
  echo "S5 A0 controller module is invalid" >&2
  exit 2
fi
if [[ ! "$POSTPROCESS_MODULE" =~ ^[A-Za-z_][A-Za-z0-9_.]*$ ]]; then
  echo "S5 A0 postprocess module is invalid" >&2
  exit 2
fi

AUTHORITY_INPUT="$1"
if [[ ! -f "$AUTHORITY_INPUT" ]]; then
  echo "sealed S5 A0 authority is unavailable" >&2
  exit 2
fi
AUTHORITY="$(cd "$(dirname "$AUTHORITY_INPUT")" && pwd)/$(basename "$AUTHORITY_INPUT")"

# The Python boundary owns JSON parsing and invokes the canonical verifier.
# Only the verifier-approved run identity crosses back into the shell.
if ! AUTHORITY_IDENTITY="$(
  PYTHONPATH="$PROJECT_DIR/src" "$PYTHON" - "$AUTHORITY" 2>/dev/null <<'PY'
import json
import sys
from pathlib import Path

from paper_eval.s5_live_authority import verify_s5_live_authority

path = Path(sys.argv[1])
artifact = json.loads(path.read_text(encoding="utf-8"))
verified = verify_s5_live_authority(artifact)
payload = verified["payload"]
run = payload["run"]
if payload["method"] != "A0" or run["method"] != "A0":
    raise SystemExit(2)
sys.stdout.write(f"{run['run_id']}\t{run['namespace']}\t{verified['git_commit']}")
PY
)"; then
  echo "sealed S5 A0 authority verification failed" >&2
  exit 2
fi

if [[ "$AUTHORITY_IDENTITY" == *$'\n'* ]]; then
  echo "verified A0 authority identity is invalid" >&2
  exit 2
fi
IFS=$'\t' read -r RUN_ID NAMESPACE GIT_COMMIT <<< "$AUTHORITY_IDENTITY"
if [[ ! "$RUN_ID" =~ ^s5-a0-[0-9]{8}-[0-9]{3}$ ]] \
  || [[ "$NAMESPACE" != "pev3-$RUN_ID" ]] \
  || [[ ! "$GIT_COMMIT" =~ ^[0-9a-f]{40}$ ]] \
  || [[ "$AUTHORITY_IDENTITY" != "$RUN_ID"$'\t'"$NAMESPACE"$'\t'"$GIT_COMMIT" ]]; then
  echo "verified A0 authority identity is invalid" >&2
  exit 2
fi

for input in \
  "$PRODUCTION_IDENTITY" \
  "$PRODUCTION_IDENTITY_QUALIFICATION" \
  "$PREFLIGHT" \
  "$RUNTIME_CONFIG" \
  "$IDENTITY_MATERIALIZATION" \
  "$CURRENT_STAGE_POINTER" \
  "$ENV_FILE"; do
  if [[ ! -f "$input" ]]; then
    echo "S5 A0 controller input is unavailable" >&2
    exit 2
  fi
done

# The detached command uses single-quoted absolute paths; reject shell control
# characters before constructing it rather than weakening the quoting boundary.
for value in \
  "$PROJECT_DIR" "$PYTHON" "$AUTHORITY" "$PRODUCTION_IDENTITY" \
  "$PRODUCTION_IDENTITY_QUALIFICATION" "$PREFLIGHT" "$RUNTIME_CONFIG" \
  "$IDENTITY_MATERIALIZATION" "$CURRENT_STAGE_POINTER" "$ENV_FILE" \
  "$RUNS_ROOT" "$LOG_DIR"; do
  if [[ "$value" == *"'"* || "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "S5 A0 path is invalid" >&2
    exit 2
  fi
done

RUN_ROOT="$RUNS_ROOT/$RUN_ID"
CONSUMPTION="$RUN_ROOT/authority_consumption.json"
CONTROLLER_ROOT="$RUN_ROOT/controller"
ATTEMPT_ROOT="$RUN_ROOT/attempt"
NATIVE_RESULT="$ATTEMPT_ROOT/result.json"
POST_OBSERVATION="$RUN_ROOT/post_observation.json"
FINAL_RESULT="$RUN_ROOT/S5_A0_RESULT.json"
POSTPROCESS_CHECKPOINT="$RUN_ROOT/postprocess/checkpoint.json"
SESSION="membind-pev3-$RUN_ID"
LOG="$LOG_DIR/${RUN_ID}.log"

for target in \
  "$CONSUMPTION" \
  "$CONTROLLER_ROOT" \
  "$ATTEMPT_ROOT" \
  "$NATIVE_RESULT" \
  "$POST_OBSERVATION" \
  "$FINAL_RESULT" \
  "$POSTPROCESS_CHECKPOINT"; do
  if [[ -e "$target" || -L "$target" ]]; then
    echo "S5 A0 single-use output already exists" >&2
    exit 3
  fi
done
if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
  echo "S5 A0 tmux session already exists" >&2
  exit 3
fi
if [[ ! -d "$RUNS_ROOT" || ! -d "$LOG_DIR" ]]; then
  echo "S5 A0 output root is unavailable" >&2
  exit 2
fi
mkdir -p "$RUN_ROOT"

"$TMUX_BIN" new-session -d -s "$SESSION" -c "$PROJECT_DIR" \
  "set -o pipefail; { env PYTHONUNBUFFERED=1 PYTHONPATH='$PROJECT_DIR/src' '$PYTHON' -u -m '$CONTROLLER_MODULE' --production-identity '$PRODUCTION_IDENTITY' --production-identity-qualification '$PRODUCTION_IDENTITY_QUALIFICATION' --preflight '$PREFLIGHT' --authority '$AUTHORITY' --runtime-config '$RUNTIME_CONFIG' --identity-materialization '$IDENTITY_MATERIALIZATION' --current-stage-pointer '$CURRENT_STAGE_POINTER' --env-file '$ENV_FILE' --run-root '$RUN_ROOT' --git-commit '$GIT_COMMIT' && env PYTHONUNBUFFERED=1 PYTHONPATH='$PROJECT_DIR/src' '$PYTHON' -u -m '$POSTPROCESS_MODULE' --production-identity '$PRODUCTION_IDENTITY' --production-identity-qualification '$PRODUCTION_IDENTITY_QUALIFICATION' --preflight '$PREFLIGHT' --authority '$AUTHORITY' --current-stage-pointer '$CURRENT_STAGE_POINTER' --env-file '$ENV_FILE' --run-root '$RUN_ROOT' --git-commit '$GIT_COMMIT'; } 2>&1 | tee -a '$LOG'"

echo "session=$SESSION"
echo "log=$LOG"
echo "inspect=tmux capture-pane -pt $SESSION"
