#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

if [[ ! -f "$MEMBIND_LLM_MODEL_DIR/config.json" ]]; then
  echo "LLM model is missing: $MEMBIND_LLM_MODEL_DIR" >&2
  echo "Run scripts/local_runtime/download_models.sh first." >&2
  exit 2
fi
log="$MEMBIND_DATA_ROOT/logs/membind-local/construction/qwen3-14b-awq.log"
pidfile="$MEMBIND_DATA_ROOT/run/membind-local/llm.pid"
mkdir -p "$(dirname "$log")" "$(dirname "$pidfile")"

if [[ "${1:-}" == "--foreground" ]]; then
  echo $$ >"$pidfile"
  exec > >(tee -a "$log") 2>&1
  echo "[$(date --iso-8601=seconds)] starting $MEMBIND_LLM_MODEL_NAME on GPU 0"
  # The validation harness contains ``statistics.py``; do not let its
  # PYTHONPATH shadow the Python stdlib inside vLLM/torch startup.
  exec env -u PYTHONPATH CUDA_VISIBLE_DEVICES=0 \
    "$MEMBIND_ENV/bin/vllm" serve "$MEMBIND_LLM_MODEL_DIR" \
    --served-model-name "$MEMBIND_LLM_MODEL_NAME" \
    --host "$MEMBIND_LLM_HOST" \
    --port "$MEMBIND_LLM_PORT" \
    --api-key "$MEMBIND_LOCAL_API_KEY" \
    --dtype auto \
    --max-model-len 65536 \
    --max-num-seqs 8 \
    --max-num-batched-tokens 8192 \
    --gpu-memory-utilization 0.90 \
    --hf-overrides '{"rope_parameters":{"rope_type":"yarn","factor":1.6,"original_max_position_embeddings":40960,"rope_theta":1000000}}' \
    --structured-outputs-config '{"backend":"xgrammar"}' \
    --enable-prefix-caching \
    --enable-chunked-prefill \
    --scheduling-policy fcfs \
    --default-chat-template-kwargs '{"enable_thinking":false}'
fi

if tmux has-session -t "=$MEMBIND_LLM_TMUX_SESSION" 2>/dev/null; then
  echo "LLM tmux session already exists: $MEMBIND_LLM_TMUX_SESSION" >&2
  exit 3
fi
if ss -ltn | rg -q ":${MEMBIND_LLM_PORT}\\b"; then
  echo "LLM port is already in use: $MEMBIND_LLM_PORT" >&2
  exit 3
fi

rm -f "$pidfile"
echo "Starting $MEMBIND_LLM_MODEL_NAME on GPU 0 at http://$MEMBIND_LLM_HOST:$MEMBIND_LLM_PORT/v1"
tmux new-session -d -s "$MEMBIND_LLM_TMUX_SESSION" -n server \
  "$SCRIPT_DIR/start_llm.sh --foreground"
for _ in {1..20}; do
  [[ -s "$pidfile" ]] && break
  tmux has-session -t "=$MEMBIND_LLM_TMUX_SESSION" 2>/dev/null || break
  sleep 0.25
done
if [[ ! -s "$pidfile" ]]; then
  echo "LLM tmux process failed to start; log: $log" >&2
  exit 4
fi
echo "tmux: $MEMBIND_LLM_TMUX_SESSION; PID $(cat "$pidfile"); log: $log"
