#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

export VLLM_API_KEY="$MEMBIND_LOCAL_API_KEY"
export CONSTRUCTION_LLM_API_KEY="$MEMBIND_LOCAL_API_KEY"
export CONSTRUCTION_LLM_BASE_URL="http://$MEMBIND_LLM_HOST:$MEMBIND_LLM_PORT/v1"
export CONSTRUCTION_LLM_MODEL="$MEMBIND_LLM_MODEL_NAME"
# Hub commit for Qwen/Qwen3-14B-AWQ; the profile id still keeps caches and
# experiment artifacts separate from the frozen 32B identity.
export CONSTRUCTION_MODEL_REVISION="${CONSTRUCTION_MODEL_REVISION:-31c69efc29464b6bb0aee1398b5a7b50a99340c3}"
export CONSTRUCTION_EXPECTED_VLLM_VERSION="0.26.0"
export CONSTRUCTION_MIN_CONTEXT_TOKENS="65536"
export CONSTRUCTION_TEMPERATURE="0.0"
export CONSTRUCTION_TOP_P="1.0"
# Context 0 source 25 overflowed 2K, 8K, and 16K completion budgets. Use the
# remaining qualified power-of-two budget while staying inside the 64K context.
export CONSTRUCTION_MAX_TOKENS="32768"
export CONSTRUCTION_OVERFLOW_MAX_TOKENS="32768"
export CONSTRUCTION_CONTEXT_SAFETY_TOKENS="32"
# The source-25 structured extraction takes longer than the OpenAI SDK's
# 600-second default read timeout. Use one bounded long request with no hidden
# SDK retry so transport evidence and makespan remain attributable.
export CONSTRUCTION_HTTP_TIMEOUT_SECONDS="3600"
export CONSTRUCTION_SDK_MAX_RETRIES="0"
export CONSTRUCTION_SEED="20260806"

export EMBEDDING_API_KEY="$MEMBIND_LOCAL_API_KEY"
export EMBEDDING_BASE_URL="http://$MEMBIND_EMBED_HOST:$MEMBIND_EMBED_PORT/v1"
export EMBEDDING_MODEL="$MEMBIND_EMBED_MODEL_NAME"
export EMBEDDING_DIM="1024"

export NEO4J_URI="$MEMBIND_NEO4J_URI"
export NEO4J_USER="${NEO4J_USER:-neo4j}"
export NEO4J_PASSWORD="${NEO4J_PASSWORD:-password}"
export NEO4J_DATABASE="${NEO4J_DATABASE:-neo4j}"
export GRAPHITI_MAX_COROUTINES="${GRAPHITI_MAX_COROUTINES:-8}"
export GRAPHITI_TELEMETRY_ENABLED="false"

export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,$NO_PROXY}"
export no_proxy="127.0.0.1,localhost${no_proxy:+,$no_proxy}"
export PYTHONPATH="$MEMBIND_REPO_ROOT/membind-validation/src${PYTHONPATH:+:$PYTHONPATH}"

echo "Activated $MEMBIND_PROFILE_ID"
echo "Python: $MEMBIND_ENV/bin/python"
echo "LLM: $CONSTRUCTION_LLM_BASE_URL ($CONSTRUCTION_LLM_MODEL)"
echo "Embedding: $EMBEDDING_BASE_URL ($EMBEDDING_MODEL, ${EMBEDDING_DIM}d)"
echo "Neo4j: $NEO4J_URI"
