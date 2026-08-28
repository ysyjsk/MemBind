#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

log="$MEMBIND_DATA_ROOT/logs/membind-local/background-setup.log"
pidfile="$MEMBIND_DATA_ROOT/run/membind-local/background-setup.pid"
status_file="$MEMBIND_DATA_ROOT/run/membind-local/background-setup.status"
lockfile="$MEMBIND_DATA_ROOT/run/membind-local/background-setup.lock"

exec 9>"$lockfile"
if ! flock -n 9; then
  echo "Another MemBind local setup is already running." >&2
  exit 3
fi

echo $$ >"$pidfile"
printf 'RUNNING\n' >"$status_file"
cleanup() {
  local code=$?
  trap - EXIT
  if [[ $code -eq 0 ]]; then
    printf 'READY\n' >"$status_file"
  else
    printf 'FAILED exit_code=%d\n' "$code" >"$status_file"
  fi
  rm -f "$pidfile"
  exit "$code"
}
trap cleanup EXIT

{
  echo "[$(date --iso-8601=seconds)] local setup started"
  "$SCRIPT_DIR/download_models.sh"
  echo "[$(date --iso-8601=seconds)] model validation completed"
  "$SCRIPT_DIR/start_all.sh"
  echo "[$(date --iso-8601=seconds)] services are ready"
} >>"$log" 2>&1
