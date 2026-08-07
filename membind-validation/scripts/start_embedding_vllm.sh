#!/usr/bin/env bash
set -euo pipefail

MODEL="${EMBEDDING_MODEL:-Qwen/Qwen3-Embedding-0.6B}"
PORT="${EMBEDDING_PORT:-8010}"
DTYPE="${EMBEDDING_DTYPE:-float16}"

exec python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --task embed \
  --dtype "$DTYPE" \
  --host 0.0.0.0 \
  --port "$PORT"
