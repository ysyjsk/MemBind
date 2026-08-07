#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_DIR="$ROOT/runtime/neo4j"
VERSION="${NEO4J_VERSION:-5.26.0}"
DIST="neo4j-community-$VERSION"
ARCHIVE="$RUNTIME_DIR/$DIST-unix.tar.gz"
URL="${NEO4J_DOWNLOAD_URL:-https://dist.neo4j.org/$DIST-unix.tar.gz}"

mkdir -p "$RUNTIME_DIR"
if [ ! -d "$RUNTIME_DIR/$DIST" ]; then
  if [ ! -f "$ARCHIVE" ]; then
    curl -L "$URL" -o "$ARCHIVE"
  fi
  tar -xzf "$ARCHIVE" -C "$RUNTIME_DIR"
fi

NEO4J_HOME="$RUNTIME_DIR/$DIST"
CONF="$NEO4J_HOME/conf/neo4j.conf"

if ! grep -q '^server.default_listen_address=' "$CONF"; then
  {
    echo ''
    echo '# MemBind local validation settings'
    echo 'server.default_listen_address=127.0.0.1'
    echo 'server.bolt.listen_address=:7687'
    echo 'server.http.listen_address=:7474'
    echo 'dbms.security.auth_enabled=true'
  } >> "$CONF"
fi

if [ ! -f "$NEO4J_HOME/data/dbms/auth.ini" ]; then
  "$NEO4J_HOME/bin/neo4j-admin" dbms set-initial-password "${NEO4J_PASSWORD:-password}"
fi

echo "$NEO4J_HOME"
