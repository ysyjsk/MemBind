#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"
exec env -u PYTHONPATH CUDA_VISIBLE_DEVICES="$MEMBIND_PREPARE_LLM_GPU" "$MEMBIND_ENV/bin/vllm" serve "$MEMBIND_LLM_MODEL_DIR" \
  --served-model-name "$MEMBIND_LLM_MODEL_NAME" --host 127.0.0.1 --port 18201 \
  --api-key "$MEMBIND_LOCAL_API_KEY" --dtype auto --max-model-len 65536 \
  --max-num-seqs 8 --max-num-batched-tokens 8192 --gpu-memory-utilization 0.70 \
  --hf-overrides '{"rope_parameters":{"rope_type":"yarn","factor":1.6,"original_max_position_embeddings":40960,"rope_theta":1000000}}' \
  --structured-outputs-config '{"backend":"guidance"}' --enable-prefix-caching \
  --enable-chunked-prefill --scheduling-policy fcfs --seed 20260806 \
  --default-chat-template-kwargs '{"enable_thinking":false}'
