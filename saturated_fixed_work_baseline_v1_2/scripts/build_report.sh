#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROTOCOL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPOSITORY_ROOT="$(cd "$PROTOCOL_ROOT/.." && pwd)"
export PYTHONPATH="$PROTOCOL_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$REPOSITORY_ROOT/paper-eval-v3/.venv/bin/python" -m saturated_fixed_work_baseline_v1_2.cli build-report "$@"
