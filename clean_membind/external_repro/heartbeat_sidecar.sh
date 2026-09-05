#!/usr/bin/env bash
set -u
pid="$1"
events="$2"
out="$3"
while kill -0 "$pid" 2>/dev/null; do
  last=$(awk -F'"global_sequence":' '/PUBLICATION_DURABLE/{split($2,a,",");v=a[1]} END{print v+0}' "$events")
  gpu=$(nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv,noheader 2>/dev/null | tr '\n' ';' || true)
  printf '{"pid":%s,"last_global_sequence":%s,"event_bytes":%s,"gpu":"%s","unix":%s}\n' "$pid" "$last" "$(wc -c < "$events")" "$gpu" "$(date +%s)" >> "$out"
  sleep 30
done
printf '{"pid":%s,"status":"EXITED","unix":%s}\n' "$pid" "$(date +%s)" >> "$out"
