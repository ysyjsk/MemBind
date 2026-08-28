#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

if [[ ! -x "$MEMBIND_ENV/bin/python" ]]; then
  echo "Creating isolated environment at $MEMBIND_ENV"
  python3 -m venv "$MEMBIND_ENV"
fi

PIP_CACHE_DIR="$MEMBIND_DATA_ROOT/cache/pip" \
  "$MEMBIND_ENV/bin/python" -m pip install \
  --index-url https://pypi.org/simple \
  --retries 12 \
  --timeout 120 \
  "vllm==0.26.0" \
  "torch==2.11.0"

PIP_CACHE_DIR="$MEMBIND_DATA_ROOT/cache/pip" \
  "$MEMBIND_ENV/bin/python" -m pip install \
  --index-url https://pypi.org/simple \
  --retries 12 \
  --timeout 120 \
  -e "$MEMBIND_REPO_ROOT/membind-validation"

echo "Installed local environment: $MEMBIND_ENV"
