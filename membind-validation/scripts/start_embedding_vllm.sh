#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/.venv/bin/python3" "$ROOT/src/current_state_gate.py" require --action service_admin

MODEL="${EMBEDDING_MODEL:-Qwen/Qwen3-Embedding-0.6B}"
PORT="${EMBEDDING_PORT:-8010}"
DTYPE="${EMBEDDING_DTYPE:-float16}"

exec python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --task embed \
  --dtype "$DTYPE" \
  --host 0.0.0.0 \
  --port "$PORT"
