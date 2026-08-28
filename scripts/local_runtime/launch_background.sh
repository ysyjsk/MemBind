#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

pidfile="$MEMBIND_DATA_ROOT/run/membind-local/background-setup.pid"
status_file="$MEMBIND_DATA_ROOT/run/membind-local/background-setup.status"
log="$MEMBIND_DATA_ROOT/logs/membind-local/background-setup.log"

if [[ -s "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
  echo "Background setup is already running (PID $(cat "$pidfile"))."
  exit 0
fi

printf 'STARTING\n' >"$status_file"
setsid -f "$SCRIPT_DIR/background_setup.sh" >>"$log" 2>&1 </dev/null

for _ in {1..20}; do
  if [[ -s "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "Background setup started (PID $(cat "$pidfile"))."
    echo "Status: $status_file"
    echo "Log: $log"
    exit 0
  fi
  sleep 0.25
done

echo "Background setup did not remain running; inspect $log" >&2
exit 1
