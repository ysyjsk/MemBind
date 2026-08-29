#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

if [[ ! -f "$MEMBIND_EMBED_MODEL_DIR/config.json" ]]; then
  echo "Embedding model is missing: $MEMBIND_EMBED_MODEL_DIR" >&2
  echo "Run scripts/local_runtime/download_models.sh first." >&2
  exit 2
fi
log="$MEMBIND_DATA_ROOT/logs/membind-local/embedding/qwen3-embedding-0.6b.log"
pidfile="$MEMBIND_DATA_ROOT/run/membind-local/embedding.pid"
mkdir -p "$(dirname "$log")" "$(dirname "$pidfile")"

if [[ "${1:-}" == "--foreground" ]]; then
  echo $$ >"$pidfile"
  exec > >(tee -a "$log") 2>&1
  echo "[$(date --iso-8601=seconds)] starting $MEMBIND_EMBED_MODEL_NAME on GPU 1"
  # Keep the project import path out of vLLM so stdlib ``statistics`` wins.
  exec env -u PYTHONPATH CUDA_VISIBLE_DEVICES=1 \
    "$MEMBIND_ENV/bin/vllm" serve "$MEMBIND_EMBED_MODEL_DIR" \
    --runner pooling \
    --served-model-name "$MEMBIND_EMBED_MODEL_NAME" \
    --host "$MEMBIND_EMBED_HOST" \
    --port "$MEMBIND_EMBED_PORT" \
    --api-key "$MEMBIND_LOCAL_API_KEY" \
    --dtype bfloat16 \
    --max-model-len 32768 \
    --max-num-seqs 128 \
    --max-num-batched-tokens 32768 \
    --gpu-memory-utilization 0.30
fi

if tmux has-session -t "=$MEMBIND_EMBED_TMUX_SESSION" 2>/dev/null; then
  echo "Embedding tmux session already exists: $MEMBIND_EMBED_TMUX_SESSION" >&2
  exit 3
fi
if ss -ltn | rg -q ":${MEMBIND_EMBED_PORT}\\b"; then
  echo "Embedding port is already in use: $MEMBIND_EMBED_PORT" >&2
  exit 3
fi

rm -f "$pidfile"
echo "Starting $MEMBIND_EMBED_MODEL_NAME on GPU 1 at http://$MEMBIND_EMBED_HOST:$MEMBIND_EMBED_PORT/v1"
tmux new-session -d -s "$MEMBIND_EMBED_TMUX_SESSION" -n server \
  "$SCRIPT_DIR/start_embedding.sh --foreground"
for _ in {1..20}; do
  [[ -s "$pidfile" ]] && break
  tmux has-session -t "=$MEMBIND_EMBED_TMUX_SESSION" 2>/dev/null || break
  sleep 0.25
done
if [[ ! -s "$pidfile" ]]; then
  echo "Embedding tmux process failed to start; log: $log" >&2
  exit 4
fi
echo "tmux: $MEMBIND_EMBED_TMUX_SESSION; PID $(cat "$pidfile"); log: $log"
