#!/usr/bin/env bash
set -u

# Execute the complete five-history Serial Native workload after a successful
# smoke. Every history is a fresh namespace/process and failure is terminal.
model="${1:?model required}"
root="${2:?H0 root required}"
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
python_bin="/data/predator/ly/Mem/envs/membind-local/bin/python"
export PYTHONPATH="$repo_root/clean_membind/src:$repo_root/mab_quality_v2_final_qa/src:/data/predator/ly/Mem/envs/membind-local/lib/python3.12/site-packages"
mkdir -p "$root"
printf '%s\n' "h0_supervisor_pid=$$" "model=$model" "root=$root" > "$root/identity.txt"

for context_index in 0 1 2 3 4; do
  events="$root/context_${context_index}.jsonl"
  log="$root/context_${context_index}.log"
  heartbeat="$root/context_${context_index}.heartbeat.jsonl"
  if [ -e "$events" ] || [ -e "$log" ] || [ -e "$heartbeat" ]; then
    printf '{"ts":"%s","status":"DUPLICATE_ARTIFACT","context_index":%s}\n' \
      "$(date -u +%FT%TZ)" "$context_index" >> "$root/supervisor_events.jsonl"
    exit 21
  fi
  printf '{"ts":"%s","status":"HISTORY_START","context_index":%s}\n' \
    "$(date -u +%FT%TZ)" "$context_index" >> "$root/supervisor_events.jsonl"
  "$python_bin" -u "$repo_root/clean_membind/external_repro/run_mab_workload.py" \
    --context-index "$context_index" --max-tokens 16384 \
    --model "$model" --structured-output-mode json_schema --output "$events" \
    > "$log" 2>&1 &
  child=$!
  printf '%s\n' "$child" > "$root/context_${context_index}.pid"
  "$repo_root/clean_membind/external_repro/heartbeat_sidecar.sh" "$child" "$events" "$heartbeat" &
  watcher=$!
  if wait "$child"; then
    status=0
  else
    status=$?
  fi
  wait "$watcher" 2>/dev/null || true
  printf '{"ts":"%s","status":"HISTORY_EXIT","context_index":%s,"pid":%s,"exit_code":%s}\n' \
    "$(date -u +%FT%TZ)" "$context_index" "$child" "$status" >> "$root/supervisor_events.jsonl"
  if [ "$status" -ne 0 ]; then
    printf '{"ts":"%s","status":"H0_FAILED","context_index":%s,"exit_code":%s}\n' \
      "$(date -u +%FT%TZ)" "$context_index" "$status" >> "$root/supervisor_events.jsonl"
    exit "$status"
  fi
done

printf '{"ts":"%s","status":"H0_COMPLETE","history_count":5}\n' "$(date -u +%FT%TZ)" >> "$root/supervisor_events.jsonl"
