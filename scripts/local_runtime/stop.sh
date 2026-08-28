#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local_env.sh"

for session in "$MEMBIND_LLM_TMUX_SESSION" "$MEMBIND_EMBED_TMUX_SESSION"; do
  if tmux has-session -t "=$session" 2>/dev/null; then
    echo "Stopping tmux session $session"
    tmux kill-session -t "=$session"
  fi
done

for name in llm embedding; do
  pidfile="$MEMBIND_DATA_ROOT/run/membind-local/$name.pid"
  if [[ -s "$pidfile" ]]; then
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "Stopping $name (PID $pid)"
      kill "$pid" || true
      for _ in {1..30}; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
      done
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  fi
done
