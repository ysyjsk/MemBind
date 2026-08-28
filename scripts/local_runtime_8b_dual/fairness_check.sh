#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"
exec "$MEMBIND_ENV/bin/python" "$SCRIPT_DIR/fairness_check.py" "$@"
