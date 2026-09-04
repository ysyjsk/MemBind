#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/activate.sh" >/dev/null
output_root="${1:?usage: $0 OUTPUT_ROOT}"
if [[ -e "$output_root" ]]; then
  echo "P1 compatibility replay root already exists: $output_root" >&2
  exit 3
fi
exec "$MEMBIND_ENV/bin/python" \
  "$MEMBIND_REPO_ROOT/scripts/run_mab8192_compatibility_replay.py" \
  --output-root "$output_root"
