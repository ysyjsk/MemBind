#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"
source "$SCRIPT_DIR/_common.sh"

require_model "Embedding" "$MEMBIND_EMBED_MODEL_DIR"
log="$MEMBIND_LOG_ROOT/embedding/qwen3-embedding-0.6b.log"
pidfile="$MEMBIND_RUN_ROOT/embedding.pid"
command=(
  "$MEMBIND_ENV/bin/vllm" serve "$MEMBIND_EMBED_MODEL_DIR"
  --runner pooling
  --served-model-name "$MEMBIND_EMBED_MODEL_NAME"
  --host "$MEMBIND_EMBED_HOST"
  --port "$MEMBIND_EMBED_PORT"
  --api-key "$MEMBIND_LOCAL_API_KEY"
  --dtype bfloat16
  --max-model-len "$MEMBIND_EMBED_MAX_MODEL_LEN"
  --max-num-seqs "$MEMBIND_EMBED_MAX_NUM_SEQS"
  --max-num-batched-tokens "$MEMBIND_EMBED_MAX_BATCHED_TOKENS"
  --gpu-memory-utilization "$MEMBIND_EMBED_GPU_MEMORY_UTILIZATION"
  --enable-chunked-prefill
)

case "${1:-}" in
  --dry-run)
    print_command env -u PYTHONPATH "CUDA_VISIBLE_DEVICES=$MEMBIND_EMBED_GPU" "${command[@]}"
    exit 0
    ;;
  --foreground)
    echo $$ >"$pidfile"
    exec > >(tee -a "$log") 2>&1
    echo "[$(date --iso-8601=seconds)] starting $MEMBIND_EMBED_MODEL_NAME on physical GPU $MEMBIND_EMBED_GPU"
    exec env -u PYTHONPATH CUDA_VISIBLE_DEVICES="$MEMBIND_EMBED_GPU" "${command[@]}"
    ;;
  "") ;;
  *) echo "usage: $0 [--foreground|--dry-run]" >&2; exit 2 ;;
esac

echo "Starting embedding at http://$MEMBIND_EMBED_HOST:$MEMBIND_EMBED_PORT/v1"
launch_tmux_service \
  "Embedding" \
  "$MEMBIND_EMBED_TMUX_SESSION" \
  "$MEMBIND_EMBED_PORT" \
  "$pidfile" \
  "$log" \
  "$SCRIPT_DIR/start_embedding.sh"
