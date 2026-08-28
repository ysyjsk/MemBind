#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

"$SCRIPT_DIR/start_embedding.sh"
"$SCRIPT_DIR/start_llm.sh"

wait_for_service() {
  local name="$1"
  local url="$2"
  local pidfile="$3"
  local deadline=$((SECONDS + 900))
  while (( SECONDS < deadline )); do
    if [[ ! -s "$pidfile" ]] || ! kill -0 "$(cat "$pidfile")" 2>/dev/null; then
      echo "$name exited before becoming ready" >&2
      return 1
    fi
    if curl --silent --show-error --fail --max-time 5 \
      -H "Authorization: Bearer $MEMBIND_LOCAL_API_KEY" \
      "$url/models" >/dev/null 2>&1; then
      echo "$name is ready: $url"
      return 0
    fi
    sleep 2
  done
  echo "$name did not become ready within 900 seconds" >&2
  return 1
}

wait_for_service \
  "embedding" \
  "http://$MEMBIND_EMBED_HOST:$MEMBIND_EMBED_PORT/v1" \
  "$MEMBIND_DATA_ROOT/run/membind-local/embedding.pid" &
embedding_wait_pid=$!
wait_for_service \
  "LLM" \
  "http://$MEMBIND_LLM_HOST:$MEMBIND_LLM_PORT/v1" \
  "$MEMBIND_DATA_ROOT/run/membind-local/llm.pid"
wait "$embedding_wait_pid"

"$MEMBIND_ENV/bin/python" "$SCRIPT_DIR/preflight.py" \
  --llm-base-url "http://$MEMBIND_LLM_HOST:$MEMBIND_LLM_PORT/v1" \
  --llm-model "$MEMBIND_LLM_MODEL_NAME" \
  --embedding-base-url "http://$MEMBIND_EMBED_HOST:$MEMBIND_EMBED_PORT/v1" \
  --embedding-model "$MEMBIND_EMBED_MODEL_NAME" \
  --api-key "$MEMBIND_LOCAL_API_KEY" \
  --timeout 120
