#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export MEMBIND_DEPLOYMENT_POLICY_ID="P1_QWEN25_7B_AWQ"
export MEMBIND_RUNTIME_DIR_OVERRIDE="$SCRIPT_DIR"
exec "$SCRIPT_DIR/../local_runtime_8b_dual/start_all.sh" "$@"
