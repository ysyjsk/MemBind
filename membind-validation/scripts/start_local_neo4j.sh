#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${NEO4J_VERSION:-5.26.0}"
NEO4J_HOME="${NEO4J_HOME:-$ROOT/runtime/neo4j/neo4j-community-$VERSION}"

if [ "${1:-}" != "--console" ]; then
  exec "$ROOT/scripts/start_local_neo4j_daemon.sh"
fi
shift

if [ ! -x "$NEO4J_HOME/bin/neo4j" ]; then
  "$ROOT/scripts/install_local_neo4j.sh" >/dev/null
fi

export NEO4J_server_memory_heap_initial__size="${NEO4J_HEAP_INITIAL:-1G}"
export NEO4J_server_memory_heap_max__size="${NEO4J_HEAP_MAX:-2G}"
exec "$NEO4J_HOME/bin/neo4j" console "$@"
