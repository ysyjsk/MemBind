#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

if [[ "${1:-}" == "--dry-run" ]]; then
  "$SCRIPT_DIR/start_native_llm.sh" --dry-run
  "$SCRIPT_DIR/start_prepare_llm.sh" --dry-run
  "$SCRIPT_DIR/start_embedding.sh" --dry-run
  "$MEMBIND_ENV/bin/python" "$SCRIPT_DIR/preflight.py" --mode static
  exit 0
elif [[ -n "${1:-}" ]]; then
  echo "usage: $0 [--dry-run]" >&2
  exit 2
fi

status_file="$MEMBIND_RUN_ROOT/background-setup.status"
lockfile="$MEMBIND_RUN_ROOT/start-all.lock"
preflight_file="$MEMBIND_RUN_ROOT/live-preflight.json"
manifest_result="$MEMBIND_RUN_ROOT/platform-manifest-result.json"

exec 9>"$lockfile"
if ! flock -n 9; then
  echo "Another $MEMBIND_PROFILE_ID startup is already running." >&2
  exit 3
fi

# Fail before touching any service. In particular, this reports the active 14B
# profile instead of stopping it or competing for its GPUs.
"$MEMBIND_ENV/bin/python" "$SCRIPT_DIR/preflight.py" --mode startup
printf 'STARTING profile=%s time=%s\n' "$MEMBIND_PROFILE_ID" "$(date --iso-8601=seconds)" >"$status_file"

cleanup_on_failure() {
  local code=$?
  trap - EXIT
  if [[ $code -ne 0 ]]; then
    "$SCRIPT_DIR/stop.sh" --keep-status >/dev/null 2>&1 || true
    printf 'FAILED profile=%s exit_code=%d time=%s\n' \
      "$MEMBIND_PROFILE_ID" "$code" "$(date --iso-8601=seconds)" >"$status_file"
  fi
  exit "$code"
}
trap cleanup_on_failure EXIT

wait_for_service() {
  local name="$1"
  local url="$2"
  local pidfile="$3"
  local deadline=$((SECONDS + 1200))
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
  echo "$name did not become ready within 1200 seconds" >&2
  return 1
}

# Recover and validate the existing database before paying the model-load cost.
"$SCRIPT_DIR/start_neo4j.sh"

# GPU 1 services are initialized sequentially so vLLM profiles each process
# against the settled memory footprint of its colocated peer.
"$SCRIPT_DIR/start_native_llm.sh"
"$SCRIPT_DIR/start_embedding.sh"
wait_for_service \
  "embedding" \
  "http://$MEMBIND_EMBED_HOST:$MEMBIND_EMBED_PORT/v1" \
  "$MEMBIND_RUN_ROOT/embedding.pid"
"$SCRIPT_DIR/start_prepare_llm.sh"

wait_for_service \
  "native LLM" \
  "http://$MEMBIND_NATIVE_LLM_HOST:$MEMBIND_NATIVE_LLM_PORT/v1" \
  "$MEMBIND_RUN_ROOT/native-llm.pid"
wait_for_service \
  "prepare LLM" \
  "http://$MEMBIND_PREPARE_LLM_HOST:$MEMBIND_PREPARE_LLM_PORT/v1" \
  "$MEMBIND_RUN_ROOT/prepare-llm.pid"

source "$SCRIPT_DIR/activate.sh" >/dev/null
"$MEMBIND_ENV/bin/python" "$SCRIPT_DIR/preflight.py" \
  --mode live \
  --timeout 120 \
  --output "$preflight_file" >/dev/null
"$MEMBIND_ENV/bin/python" "$SCRIPT_DIR/write_profile_manifest.py" \
  --preflight "$preflight_file" >"$manifest_result"

manifest_path="$(jq -r '.path' "$manifest_result")"
manifest_sha="$(jq -r '.payload_sha256' "$manifest_result")"
printf 'READY profile=%s manifest=%s payload_sha256=%s time=%s\n' \
  "$MEMBIND_PROFILE_ID" "$manifest_path" "$manifest_sha" "$(date --iso-8601=seconds)" >"$status_file"
trap - EXIT
echo "READY: $MEMBIND_PROFILE_ID"
echo "Manifest: $manifest_path"
echo "Payload SHA-256: $manifest_sha"
