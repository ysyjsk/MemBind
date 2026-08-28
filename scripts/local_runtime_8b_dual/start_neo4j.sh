#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

neo4j_home="$MEMBIND_REPO_ROOT/membind-validation/runtime/neo4j/neo4j-community-5.26.0"
neo4j_bin="$neo4j_home/bin/neo4j"
evidence_path="$MEMBIND_RUN_ROOT/neo4j-startup.json"
timeout_seconds="${MEMBIND_8B_NEO4J_START_TIMEOUT_SECONDS:-120}"
required_stable_checks="${MEMBIND_8B_NEO4J_STABLE_CHECKS:-5}"

if [[ ! -x "$neo4j_bin" ]]; then
  echo "Expected existing Neo4j 5.26.0 executable is missing: $neo4j_bin" >&2
  exit 1
fi
if [[ ! "$timeout_seconds" =~ ^[1-9][0-9]*$ ]] || \
   [[ ! "$required_stable_checks" =~ ^[1-9][0-9]*$ ]]; then
  echo "Neo4j timeout and stable-check count must be positive integers" >&2
  exit 2
fi

port_open() {
  local port="$1"
  "$MEMBIND_ENV/bin/python" - "$port" <<'PY'
import socket
import sys

with socket.socket() as sock:
    sock.settimeout(0.5)
    try:
        sock.connect(("127.0.0.1", int(sys.argv[1])))
    except OSError:
        raise SystemExit(1)
PY
}

neo4j_running() {
  "$neo4j_bin" status >/dev/null 2>&1
}

http_open=false
bolt_open=false
port_open 7474 && http_open=true
port_open 7687 && bolt_open=true

already_running=false
if neo4j_running; then
  already_running=true
elif [[ "$http_open" == true || "$bolt_open" == true ]]; then
  echo "Refusing to start Neo4j because 7474 or 7687 has a foreign listener" >&2
  exit 1
fi

start_output=""
start_exit_code=0
if [[ "$already_running" == false ]]; then
  export NEO4J_server_memory_heap_initial__size="${NEO4J_HEAP_INITIAL:-1G}"
  export NEO4J_server_memory_heap_max__size="${NEO4J_HEAP_MAX:-2G}"
  set +e
  # The profile setup itself runs in a short-lived tmux session.  Detach the
  # database into a new session so tmux teardown cannot deliver SIGHUP to Java.
  start_output="$(nohup setsid env -u PYTHONPATH "$neo4j_bin" start </dev/null 2>&1)"
  start_exit_code=$?
  set -e
fi

waited=0
stable_checks=0
while (( waited <= timeout_seconds )); do
  http_open=false
  bolt_open=false
  process_open=false
  port_open 7474 && http_open=true
  port_open 7687 && bolt_open=true
  neo4j_running && process_open=true
  if [[ "$http_open" == true && "$bolt_open" == true && "$process_open" == true ]]; then
    stable_checks=$((stable_checks + 1))
    if (( stable_checks >= required_stable_checks )); then
      break
    fi
  else
    stable_checks=0
  fi
  sleep 1
  waited=$((waited + 1))
done

if (( stable_checks < required_stable_checks )); then
  echo "Neo4j failed sustained health checks; start_exit_code=$start_exit_code output=${start_output:0:1000}" >&2
  exit 1
fi

source "$SCRIPT_DIR/activate.sh" >/dev/null
"$MEMBIND_ENV/bin/python" - "$evidence_path" "$neo4j_home" "$already_running" \
  "$waited" "$start_exit_code" <<'PY'
import json
import os
import sys
import time
from pathlib import Path

from neo4j import GraphDatabase

output, home, already_running, waited, start_exit_code = sys.argv[1:]
driver = GraphDatabase.driver(
    os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
)
try:
    with driver.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as session:
        value = session.run("RETURN 1 AS value").single(strict=True)["value"]
finally:
    driver.close()
if value != 1:
    raise RuntimeError("Neo4j read-only canary returned an unexpected value")
payload = {
    "schema_version": "membind.8b-neo4j-startup.v1",
    "status": "PASS",
    "profile_id": os.environ["MEMBIND_PROFILE_ID"],
    "neo4j_home": str(Path(home).resolve()),
    "uri": os.environ["NEO4J_URI"],
    "database": os.environ.get("NEO4J_DATABASE", "neo4j"),
    "already_running": already_running == "true",
    "stable_checks": int(os.environ.get("MEMBIND_8B_NEO4J_STABLE_CHECKS", "5")),
    "waited_seconds": int(waited),
    "start_exit_code": int(start_exit_code),
    "read_only_canary": value,
    "completed_unix": time.time(),
}
path = Path(output)
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.with_suffix(".tmp")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temporary.replace(path)
print(json.dumps(payload, sort_keys=True))
PY
