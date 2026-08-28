#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/activate.sh" >/dev/null
exec "$MEMBIND_ENV/bin/python" "$SCRIPT_DIR/preflight.py" "$@"
