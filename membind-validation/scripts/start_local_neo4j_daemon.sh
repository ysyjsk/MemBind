#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/.venv/bin/python3" "$ROOT/src/current_state_gate.py" require --action service_admin
ARTIFACT_DIR="$ROOT/artifacts/environment"
VERSION="${NEO4J_VERSION:-5.26.0}"
NEO4J_HOME="${NEO4J_HOME:-$ROOT/runtime/neo4j/neo4j-community-$VERSION}"
STATUS_JSON="$ARTIFACT_DIR/neo4j_daemon_status.json"
MAX_WAIT_SECONDS="${NEO4J_START_TIMEOUT_SECONDS:-120}"
REQUIRED_STABLE_CHECKS="${NEO4J_REQUIRED_STABLE_CHECKS:-5}"

mkdir -p "$ARTIFACT_DIR"

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

# The database process does not need credentials for either remote model.
unset VLLM_API_KEY CONSTRUCTION_LLM_API_KEY EMBEDDING_API_KEY OPENAI_API_KEY || true

write_status() {
  local ok="$1"
  local phase="$2"
  local http_open="$3"
  local bolt_open="$4"
  local waited_seconds="$5"
  local start_exit_code="$6"
  local error="${7:-}"
  python - "$STATUS_JSON" "$ok" "$phase" "$http_open" "$bolt_open" "$waited_seconds" "$start_exit_code" "$error" "$NEO4J_HOME" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

status_path, ok, phase, http_open, bolt_open, waited, start_exit, error, neo4j_home = sys.argv[1:10]
payload = {
    "ok": ok == "true",
    "phase": phase,
    "neo4j_home": neo4j_home,
    "http": {"host": "127.0.0.1", "port": 7474, "open": http_open == "true"},
    "bolt": {"host": "127.0.0.1", "port": 7687, "open": bolt_open == "true"},
    "waited_seconds": int(waited),
    "start_exit_code": int(start_exit),
    "checked_at": datetime.now(timezone.utc).isoformat(),
}
if error:
    payload["error"] = error[-2000:]
Path(status_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

port_open() {
  local port="$1"
  python - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect(("127.0.0.1", port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
}

neo4j_running() {
  "$NEO4J_HOME/bin/neo4j" status >/dev/null 2>&1
}

case "$MAX_WAIT_SECONDS:$REQUIRED_STABLE_CHECKS" in
  *[!0-9:]*|:*|*:0)
    write_status false "invalid_configuration" false false 0 0 \
      "NEO4J_START_TIMEOUT_SECONDS and NEO4J_REQUIRED_STABLE_CHECKS must be positive integers"
    exit 2
    ;;
esac

if [ ! -x "$NEO4J_HOME/bin/neo4j" ]; then
  "$ROOT/scripts/install_local_neo4j.sh" >/dev/null
fi

http_flag=false
bolt_flag=false
if port_open 7474; then http_flag=true; fi
if port_open 7687; then bolt_flag=true; fi

already_running=false
if neo4j_running; then
  already_running=true
elif [ "$http_flag" = true ] || [ "$bolt_flag" = true ]; then
  write_status false "foreign_listener" "$http_flag" "$bolt_flag" 0 0 \
    "Neo4j is not running, but one or more configured ports are already in use"
  exit 1
fi

export NEO4J_server_memory_heap_initial__size="${NEO4J_HEAP_INITIAL:-1G}"
export NEO4J_server_memory_heap_max__size="${NEO4J_HEAP_MAX:-2G}"

start_output=""
start_rc=0
if [ "$already_running" = false ]; then
  set +e
  if command -v setsid >/dev/null 2>&1; then
    start_output="$(nohup setsid "$NEO4J_HOME/bin/neo4j" start </dev/null 2>&1)"
  else
    start_output="$(nohup "$NEO4J_HOME/bin/neo4j" start </dev/null 2>&1)"
  fi
  start_rc=$?
  set -e
fi

waited=0
stable_checks=0
while [ "$waited" -le "$MAX_WAIT_SECONDS" ]; do
  http_flag=false
  bolt_flag=false
  process_flag=false
  if port_open 7474; then http_flag=true; fi
  if port_open 7687; then bolt_flag=true; fi
  if neo4j_running; then process_flag=true; fi
  if [ "$process_flag" = true ] && [ "$http_flag" = true ] && [ "$bolt_flag" = true ]; then
    stable_checks=$((stable_checks + 1))
    if [ "$stable_checks" -ge "$REQUIRED_STABLE_CHECKS" ]; then
      phase="ready"
      if [ "$already_running" = true ]; then phase="already_ready"; fi
      write_status true "$phase" "$http_flag" "$bolt_flag" "$waited" "$start_rc"
      exit 0
    fi
  else
    stable_checks=0
  fi
  sleep 1
  waited=$((waited + 1))
done

error="Neo4j did not remain healthy for $REQUIRED_STABLE_CHECKS consecutive checks within $MAX_WAIT_SECONDS seconds"
if [ "$start_rc" -ne 0 ]; then
  error="$error; start output: $start_output"
fi
write_status false "wait_for_stable_health" "$http_flag" "$bolt_flag" "$waited" "$start_rc" "$error"
exit 1
