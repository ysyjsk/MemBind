#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"
source "$SCRIPT_DIR/_common.sh"

require_model "Prepare LLM" "$MEMBIND_LLM_MODEL_DIR"
log="$MEMBIND_LOG_ROOT/construction/prepare-$MEMBIND_LLM_MODEL_NAME.log"
pidfile="$MEMBIND_RUN_ROOT/prepare-llm.pid"
command=(
  "$MEMBIND_ENV/bin/vllm" serve "$MEMBIND_LLM_MODEL_DIR"
  --served-model-name "$MEMBIND_LLM_MODEL_NAME"
  --host "$MEMBIND_PREPARE_LLM_HOST"
  --port "$MEMBIND_PREPARE_LLM_PORT"
  --api-key "$MEMBIND_LOCAL_API_KEY"
  --dtype auto
  --max-model-len "$MEMBIND_LLM_MAX_MODEL_LEN"
  --max-num-seqs "$MEMBIND_LLM_MAX_NUM_SEQS"
  --max-num-batched-tokens "$MEMBIND_LLM_MAX_BATCHED_TOKENS"
  --gpu-memory-utilization "$MEMBIND_PREPARE_LLM_GPU_MEMORY_UTILIZATION"
  --structured-outputs-config '{"backend":"xgrammar","disable_any_whitespace":true}'
  --enable-prefix-caching
  --enable-chunked-prefill
  --scheduling-policy fcfs
  --seed 20260806
)
if [[ -n "${MEMBIND_LLM_HF_OVERRIDES:-}" ]]; then
  command+=(--hf-overrides "$MEMBIND_LLM_HF_OVERRIDES")
fi
if [[ -n "${MEMBIND_LLM_DEFAULT_CHAT_TEMPLATE_KWARGS:-}" ]]; then
  command+=(--default-chat-template-kwargs "$MEMBIND_LLM_DEFAULT_CHAT_TEMPLATE_KWARGS")
fi

case "${1:-}" in
  --dry-run)
    print_command env -u PYTHONPATH "CUDA_VISIBLE_DEVICES=$MEMBIND_PREPARE_LLM_GPU" "${command[@]}"
    exit 0
    ;;
  --foreground)
    echo $$ >"$pidfile"
    exec > >(tee -a "$log") 2>&1
    echo "[$(date --iso-8601=seconds)] starting prepare $MEMBIND_LLM_MODEL_NAME on physical GPU $MEMBIND_PREPARE_LLM_GPU"
    exec env -u PYTHONPATH CUDA_VISIBLE_DEVICES="$MEMBIND_PREPARE_LLM_GPU" "${command[@]}"
    ;;
  "") ;;
  *) echo "usage: $0 [--foreground|--dry-run]" >&2; exit 2 ;;
esac

echo "Starting prepare replica at http://$MEMBIND_PREPARE_LLM_HOST:$MEMBIND_PREPARE_LLM_PORT/v1"
launch_tmux_service \
  "Prepare LLM" \
  "$MEMBIND_PREPARE_LLM_TMUX_SESSION" \
  "$MEMBIND_PREPARE_LLM_PORT" \
  "$pidfile" \
  "$log" \
  "$SCRIPT_DIR/start_prepare_llm.sh"
