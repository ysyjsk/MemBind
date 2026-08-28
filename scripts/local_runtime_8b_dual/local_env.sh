#!/usr/bin/env bash
set -euo pipefail

# This profile is deliberately independent from scripts/local_runtime (14B).
# Only MEMBIND_8B_* variables may customize filesystem roots; experiment-facing
# identities, ports, GPU placement, and resource budgets are fixed below.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export MEMBIND_8B_RUNTIME_DIR="$SCRIPT_DIR"
export MEMBIND_DATA_ROOT="${MEMBIND_8B_DATA_ROOT:-/data/predator/ly/Mem}"
export MEMBIND_REPO_ROOT="${MEMBIND_8B_REPO_ROOT:-/data/predator/ly/MemBind}"
export MEMBIND_ENV="${MEMBIND_8B_ENV:-$MEMBIND_DATA_ROOT/envs/membind-local}"
export MEMBIND_MODEL_ROOT="$MEMBIND_DATA_ROOT/models"

export MEMBIND_PROFILE_ID="local-qwen3-8b-awq-dualreplica-v1"
export MEMBIND_PROFILE_ROOT="$MEMBIND_DATA_ROOT/profiles/$MEMBIND_PROFILE_ID"
export MEMBIND_LOG_ROOT="$MEMBIND_DATA_ROOT/logs/$MEMBIND_PROFILE_ID"
export MEMBIND_RUN_ROOT="$MEMBIND_DATA_ROOT/run/$MEMBIND_PROFILE_ID"
export MEMBIND_EXPERIMENT_ROOT="$MEMBIND_DATA_ROOT/experiments/$MEMBIND_PROFILE_ID"
export MEMBIND_NAMESPACE_PREFIX="$MEMBIND_PROFILE_ID-"

export MEMBIND_LLM_MODEL_DIR="$MEMBIND_MODEL_ROOT/Qwen3-8B-AWQ"
export MEMBIND_LLM_MODEL_NAME="qwen3-8b-awq"
export MEMBIND_LLM_MODEL_REVISION="4da05a8edb55c6046cce958586c33b61da07bb79"
export MEMBIND_EMBED_MODEL_DIR="$MEMBIND_MODEL_ROOT/Qwen3-Embedding-0.6B"
export MEMBIND_EMBED_MODEL_NAME="qwen3-embedding-0.6b"

export MEMBIND_NATIVE_LLM_HOST="127.0.0.1"
export MEMBIND_NATIVE_LLM_PORT="18200"
export MEMBIND_PREPARE_LLM_HOST="127.0.0.1"
export MEMBIND_PREPARE_LLM_PORT="18201"
export MEMBIND_EMBED_HOST="127.0.0.1"
export MEMBIND_EMBED_PORT="18202"
export MEMBIND_LOCAL_API_KEY="${MEMBIND_8B_LOCAL_API_KEY:-membind-local}"

export MEMBIND_NATIVE_LLM_GPU="0"
export MEMBIND_PREPARE_LLM_GPU="1"
export MEMBIND_EMBED_GPU="1"
export MEMBIND_NATIVE_LLM_GPU_MEMORY_UTILIZATION="0.90"
export MEMBIND_PREPARE_LLM_GPU_MEMORY_UTILIZATION="0.70"
export MEMBIND_EMBED_GPU_MEMORY_UTILIZATION="0.25"
export MEMBIND_GPU1_MAX_COMBINED_UTILIZATION="0.95"

export MEMBIND_LLM_MAX_MODEL_LEN="65536"
export MEMBIND_LLM_MAX_NUM_SEQS="8"
export MEMBIND_LLM_MAX_BATCHED_TOKENS="8192"
export MEMBIND_EMBED_MAX_MODEL_LEN="32768"
export MEMBIND_EMBED_MAX_NUM_SEQS="128"
export MEMBIND_EMBED_MAX_BATCHED_TOKENS="32768"
export MEMBIND_EMBED_DIMENSION="1024"

export MEMBIND_NATIVE_LLM_TMUX_SESSION="membind-8b-native"
export MEMBIND_PREPARE_LLM_TMUX_SESSION="membind-8b-prepare"
export MEMBIND_EMBED_TMUX_SESSION="membind-8b-embedding"
export MEMBIND_NEO4J_URI="${MEMBIND_8B_NEO4J_URI:-bolt://127.0.0.1:7687}"

export MEMBIND_NATIVE_ROUTING_CONFIG="$SCRIPT_DIR/routing/native_dual_resource_matched.json"
export MEMBIND_STATIC_ROLE_ROUTING_CONFIG="$SCRIPT_DIR/routing/native_dual_static_role.json"
# Frozen MemBind-Core route.  The previously explored critical-path route is
# retained as an explicit ablation, but is not the paper method after r67/r69
# showed no stable gain over the fixed work-conserving substrate.
export MEMBIND_V61_ROUTING_CONFIG="$SCRIPT_DIR/routing/v61_dual_elastic_affinity.json"
export MEMBIND_SINGLE_GPU_ROUTING_CONFIG="$SCRIPT_DIR/routing/single_gpu_ablation.json"

export HF_HOME="${HF_HOME:-$MEMBIND_DATA_ROOT/cache/huggingface}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-$HF_HOME/hub}"
export HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-$HF_HOME/datasets}"
export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-$HF_HUB_CACHE}"
export TORCH_HOME="${TORCH_HOME:-$MEMBIND_DATA_ROOT/cache/torch}"
export VLLM_CACHE_ROOT="${VLLM_CACHE_ROOT:-$MEMBIND_DATA_ROOT/cache/vllm}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$MEMBIND_DATA_ROOT/cache}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$MEMBIND_DATA_ROOT/cache/pip}"
export TMPDIR="${TMPDIR:-$MEMBIND_DATA_ROOT/tmp/$MEMBIND_PROFILE_ID}"
export CUDA_CACHE_PATH="${CUDA_CACHE_PATH:-$MEMBIND_DATA_ROOT/cache/cuda}"
export FLASHINFER_WORKSPACE_BASE="${FLASHINFER_WORKSPACE_BASE:-$MEMBIND_DATA_ROOT/cache/flashinfer}"
export VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR="${VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR:-$MEMBIND_DATA_ROOT/cache/flashinfer/autotune}"
export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"
# Enables the authenticated reset_prefix_cache endpoint used by the symmetric
# measured-attempt protocol. It does not alter request scheduling or decoding.
export VLLM_SERVER_DEV_MODE="1"

mkdir -p \
  "$MEMBIND_PROFILE_ROOT" \
  "$MEMBIND_LOG_ROOT/construction" \
  "$MEMBIND_LOG_ROOT/embedding" \
  "$MEMBIND_RUN_ROOT" \
  "$MEMBIND_EXPERIMENT_ROOT" \
  "$HF_HUB_CACHE" \
  "$TORCH_HOME" \
  "$VLLM_CACHE_ROOT" \
  "$PIP_CACHE_DIR" \
  "$TMPDIR" \
  "$CUDA_CACHE_PATH" \
  "$FLASHINFER_WORKSPACE_BASE" \
  "$VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR"

export PATH="$MEMBIND_ENV/bin:$PATH"

local_python() {
  "$MEMBIND_ENV/bin/python" "$@"
}
