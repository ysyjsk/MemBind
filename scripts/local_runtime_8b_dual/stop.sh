#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

keep_status=false
if [[ "${1:-}" == "--keep-status" ]]; then
  keep_status=true
elif [[ -n "${1:-}" ]]; then
  echo "usage: $0 [--keep-status]" >&2
  exit 2
fi

for session in \
  "$MEMBIND_NATIVE_LLM_TMUX_SESSION" \
  "$MEMBIND_PREPARE_LLM_TMUX_SESSION" \
  "$MEMBIND_EMBED_TMUX_SESSION"; do
  if tmux has-session -t "=$session" 2>/dev/null; then
    echo "Stopping tmux session $session"
    tmux kill-session -t "=$session"
  fi
done

stop_failed=false
for item in \
  "native-llm:$MEMBIND_NATIVE_LLM_PORT:$MEMBIND_LLM_MODEL_DIR" \
  "prepare-llm:$MEMBIND_PREPARE_LLM_PORT:$MEMBIND_LLM_MODEL_DIR" \
  "embedding:$MEMBIND_EMBED_PORT:$MEMBIND_EMBED_MODEL_DIR"; do
  IFS=: read -r name expected_port expected_model <<<"$item"
  pidfile="$MEMBIND_RUN_ROOT/$name.pid"
  if [[ -s "$pidfile" ]]; then
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
      if [[ "$cmdline" != *"vllm"* || "$cmdline" != *"$expected_model"* || "$cmdline" != *"--port $expected_port"* ]]; then
        echo "Refusing to signal PID $pid: it does not match $name on port $expected_port" >&2
        stop_failed=true
        continue
      fi
      echo "Stopping $name (PID $pid)"
      kill "$pid" 2>/dev/null || true
      for _ in {1..30}; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
      done
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  fi
done

if [[ "$stop_failed" == true ]]; then
  printf 'STOP_FAILED profile=%s time=%s\n' "$MEMBIND_PROFILE_ID" "$(date --iso-8601=seconds)" \
    >"$MEMBIND_RUN_ROOT/background-setup.status"
  exit 1
elif [[ "$keep_status" == false ]]; then
  printf 'STOPPED profile=%s time=%s\n' "$MEMBIND_PROFILE_ID" "$(date --iso-8601=seconds)" \
    >"$MEMBIND_RUN_ROOT/background-setup.status"
fi
