#!/usr/bin/env bash
set -euo pipefail

port_is_listening() {
  local port="$1"
  ss -ltn | rg -q ":${port}\\b"
}

print_command() {
  printf 'DRY-RUN:'
  printf ' %q' "$@"
  printf '\n'
}

launch_tmux_service() {
  local label="$1"
  local session="$2"
  local port="$3"
  local pidfile="$4"
  local logfile="$5"
  local script="$6"

  if tmux has-session -t "=$session" 2>/dev/null; then
    echo "$label tmux session already exists: $session" >&2
    return 3
  fi
  if port_is_listening "$port"; then
    echo "$label port is already in use: $port" >&2
    return 3
  fi
  rm -f "$pidfile"
  tmux new-session -d -s "$session" -n server "$script --foreground"
  for _ in {1..40}; do
    [[ -s "$pidfile" ]] && break
    tmux has-session -t "=$session" 2>/dev/null || break
    sleep 0.25
  done
  if [[ ! -s "$pidfile" ]]; then
    echo "$label tmux process failed to start; log: $logfile" >&2
    return 4
  fi
  echo "$label started; tmux=$session pid=$(cat "$pidfile") log=$logfile"
}

require_model() {
  local label="$1"
  local model_dir="$2"
  if [[ ! -f "$model_dir/config.json" ]]; then
    echo "$label model is missing: $model_dir" >&2
    return 2
  fi
  if ! compgen -G "$model_dir/*.safetensors" >/dev/null; then
    echo "$label weights are missing: $model_dir/*.safetensors" >&2
    return 2
  fi
}
