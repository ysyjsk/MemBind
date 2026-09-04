#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"
source "$SCRIPT_DIR/_common.sh"

require_model "Native LLM" "$MEMBIND_LLM_MODEL_DIR"
log="$MEMBIND_LOG_ROOT/construction/native-$MEMBIND_LLM_MODEL_NAME.log"
pidfile="$MEMBIND_RUN_ROOT/native-llm.pid"
command=(
  "$MEMBIND_ENV/bin/vllm" serve "$MEMBIND_LLM_MODEL_DIR"
  --served-model-name "$MEMBIND_LLM_MODEL_NAME"
  --host "$MEMBIND_NATIVE_LLM_HOST"
  --port "$MEMBIND_NATIVE_LLM_PORT"
  --api-key "$MEMBIND_LOCAL_API_KEY"
  --dtype auto
  --max-model-len "$MEMBIND_LLM_MAX_MODEL_LEN"
  --max-num-seqs "$MEMBIND_LLM_MAX_NUM_SEQS"
  --max-num-batched-tokens "$MEMBIND_LLM_MAX_BATCHED_TOKENS"
  --gpu-memory-utilization "$MEMBIND_NATIVE_LLM_GPU_MEMORY_UTILIZATION"
  --structured-outputs-config '{"backend":"xgrammar","disable_any_whitespace":true}'
  --enable-prefix-caching
  --enable-chunked-prefill
  --scheduling-policy fcfs
  --seed 20260806
)
command+=(--hf-overrides '{"rope_parameters":{"rope_type":"yarn","factor":2.0,"original_max_position_embeddings":32768,"rope_theta":1000000}}')
if [[ -n "${MEMBIND_LLM_DEFAULT_CHAT_TEMPLATE_KWARGS:-}" ]]; then
  command+=(--default-chat-template-kwargs "$MEMBIND_LLM_DEFAULT_CHAT_TEMPLATE_KWARGS")
fi

case "${1:-}" in
  --dry-run)
    print_command env -u PYTHONPATH "CUDA_VISIBLE_DEVICES=$MEMBIND_NATIVE_LLM_GPU" "${command[@]}"
    exit 0
    ;;
  --foreground)
    echo $$ >"$pidfile"
    exec > >(tee -a "$log") 2>&1
    echo "[$(date --iso-8601=seconds)] starting native $MEMBIND_LLM_MODEL_NAME on physical GPU $MEMBIND_NATIVE_LLM_GPU"
    exec env -u PYTHONPATH CUDA_VISIBLE_DEVICES="$MEMBIND_NATIVE_LLM_GPU" "${command[@]}"
    ;;
  "") ;;
  *) echo "usage: $0 [--foreground|--dry-run]" >&2; exit 2 ;;
esac

echo "Starting native replica at http://$MEMBIND_NATIVE_LLM_HOST:$MEMBIND_NATIVE_LLM_PORT/v1"
launch_tmux_service \
  "Native LLM" \
  "$MEMBIND_NATIVE_LLM_TMUX_SESSION" \
  "$MEMBIND_NATIVE_LLM_PORT" \
  "$pidfile" \
  "$log" \
  "$SCRIPT_DIR/start_native_llm.sh"
