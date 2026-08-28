#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

log="$MEMBIND_LOG_ROOT/background-setup.log"
pidfile="$MEMBIND_RUN_ROOT/background-setup.pid"
lockfile="$MEMBIND_RUN_ROOT/background-setup.lock"
exec 9>"$lockfile"
if ! flock -n 9; then
  echo "Another $MEMBIND_PROFILE_ID background setup is already running." >&2
  exit 3
fi
echo $$ >"$pidfile"
cleanup() {
  local code=$?
  rm -f "$pidfile"
  exit "$code"
}
trap cleanup EXIT
{
  echo "[$(date --iso-8601=seconds)] background startup requested"
  "$SCRIPT_DIR/start_all.sh"
  echo "[$(date --iso-8601=seconds)] background startup completed"
} >>"$log" 2>&1
