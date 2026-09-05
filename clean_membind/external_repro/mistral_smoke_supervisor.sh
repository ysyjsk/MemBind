#!/usr/bin/env bash
set -u

# Wait for the already-authorized model pull, then run exactly one fresh
# Graphiti Native smoke. This supervisor never retries the pull or the smoke.
pull_pid="${1:?pull pid required}"
model="${2:?model required}"
root="${3:?artifact root required}"
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
python_bin="/data/predator/ly/Mem/envs/membind-local/bin/python"
mkdir -p "$root"
printf '%s\n' "mistral_smoke_supervisor_pid=$$" > "$root/supervisor.pid"
printf '%s\n' "pull_pid=$pull_pid" "model=$model" "root=$root" > "$root/identity.txt"

while kill -0 "$pull_pid" 2>/dev/null; do
  printf '{"ts":"%s","supervisor_pid":%s,"pull_pid":%s,"status":"WAITING_PULL"}\n' \
    "$(date -u +%FT%TZ)" "$$" "$pull_pid" >> "$root/heartbeat.jsonl"
  sleep 30
done

printf '{"ts":"%s","supervisor_pid":%s,"pull_pid":%s,"status":"PULL_EXITED"}\n' \
  "$(date -u +%FT%TZ)" "$$" "$pull_pid" >> "$root/heartbeat.jsonl"

if ! ollama list | awk 'NR > 1 {print $1}' | grep -Fxq "$model"; then
  printf '{"ts":"%s","status":"MODEL_NOT_PRESENT"}\n' "$(date -u +%FT%TZ)" >> "$root/heartbeat.jsonl"
  exit 20
fi

smoke_log="$root/smoke.log"
export PYTHONPATH="$repo_root/clean_membind/src:/data/predator/ly/Mem/envs/membind-local/lib/python3.12/site-packages"
printf '{"ts":"%s","status":"SMOKE_START","model":"%s"}\n' \
  "$(date -u +%FT%TZ)" "$model" >> "$root/heartbeat.jsonl"
"$python_bin" -u "$repo_root/clean_membind/external_repro/run_ollama_graphiti.py" \
  --model "$model" --max-tokens 16384 --structured-output-mode json_schema \
  > "$smoke_log" 2>&1 &
smoke_pid=$!
printf '%s\n' "$smoke_pid" > "$root/smoke.pid"
(
  while kill -0 "$smoke_pid" 2>/dev/null; do
    printf '{"ts":"%s","status":"SMOKE_RUNNING","pid":%s,"log_bytes":%s}\n' \
      "$(date -u +%FT%TZ)" "$smoke_pid" "$(wc -c < "$smoke_log")" >> "$root/heartbeat.jsonl"
    sleep 30
  done
) &
smoke_watcher=$!
if wait "$smoke_pid"; then
  status=0
else
  status=$?
fi
wait "$smoke_watcher" 2>/dev/null || true
if [ "$status" -eq 0 ]; then
  printf '{"ts":"%s","status":"SMOKE_PASS"}\n' "$(date -u +%FT%TZ)" >> "$root/heartbeat.jsonl"
else
  printf '{"ts":"%s","status":"SMOKE_FAIL","exit_code":%s}\n' "$(date -u +%FT%TZ)" "$status" >> "$root/heartbeat.jsonl"
  exit "$status"
fi

# Exercise the real adapter before H0: three chunks, then the historical
# global-sequence region around source 79. Both attempts use fresh namespaces.
for probe in prefix3 source79; do
  probe_root="$repo_root/clean_membind/external_repro/mistral_${probe}_$(date -u +%Y%m%dT%H%M%SZ)"
  events="$probe_root/workload.jsonl"
  log="$probe_root/workload.log"
  heartbeat="$probe_root/heartbeat.jsonl"
  mkdir -p "$probe_root"
  printf '%s\n' "probe=$probe" "model=$model" "supervisor_pid=$$" > "$probe_root/identity.txt"
  if [ "$probe" = prefix3 ]; then
    limits=(--smoke-count 3)
  else
    limits=(--max-global-sequence 80)
  fi
  printf '{"ts":"%s","status":"PROBE_START","probe":"%s"}\n' "$(date -u +%FT%TZ)" "$probe" >> "$root/heartbeat.jsonl"
  "$python_bin" -u "$repo_root/clean_membind/external_repro/run_mab_workload.py" \
    --context-index 0 --max-tokens 16384 --model "$model" \
    --structured-output-mode json_schema "${limits[@]}" --output "$events" \
    > "$log" 2>&1 &
  child=$!
  printf '%s\n' "$child" > "$probe_root/workload.pid"
  "$repo_root/clean_membind/external_repro/heartbeat_sidecar.sh" "$child" "$events" "$heartbeat" &
  watcher=$!
  if wait "$child"; then
    status=0
  else
    status=$?
  fi
  wait "$watcher" 2>/dev/null || true
  printf '{"ts":"%s","status":"PROBE_EXIT","probe":"%s","pid":%s,"exit_code":%s}\n' \
    "$(date -u +%FT%TZ)" "$probe" "$child" "$status" >> "$root/heartbeat.jsonl"
  if [ "$status" -ne 0 ]; then
    exit "$status"
  fi
done

h0_root="$repo_root/clean_membind/external_repro/mistral_h0_serial_$(date -u +%Y%m%dT%H%M%SZ)"
printf '{"ts":"%s","status":"H0_START","root":"%s"}\n' "$(date -u +%FT%TZ)" "$h0_root" >> "$root/heartbeat.jsonl"
if "$repo_root/clean_membind/external_repro/mistral_h0_serial.sh" "$model" "$h0_root" > "$h0_root.log" 2>&1; then
  printf '{"ts":"%s","status":"H0_PASS","root":"%s"}\n' "$(date -u +%FT%TZ)" "$h0_root" >> "$root/heartbeat.jsonl"
else
  status=$?
  printf '{"ts":"%s","status":"H0_FAIL","root":"%s","exit_code":%s}\n' "$(date -u +%FT%TZ)" "$h0_root" "$status" >> "$root/heartbeat.jsonl"
  exit "$status"
fi
