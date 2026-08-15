#!/usr/bin/env bash
set -euo pipefail

# Repository-owned tmux keeps the one-shot canary independent of the SSH TTY.
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
root_dir="$(cd "${project_dir}/.." && pwd)"
session_name="${1:-pev3-native-reader-v2-20260814-001}"
log_path="${project_dir}/logs/NATIVE_READER_V2_LIVE_20260814.log"

if tmux has-session -t "${session_name}" 2>/dev/null; then
  echo "tmux session already exists: ${session_name}" >&2
  exit 2
fi

tmux new-session -d -s "${session_name}" \
  "cd '${project_dir}' && PYTHONPATH='${project_dir}/src' '${root_dir}/membind-validation/.venv/bin/python' scripts/run_native_reader_v2.py >> '${log_path}' 2>&1"

echo "started ${session_name}"
echo "monitor: tmux attach -t ${session_name}"
echo "log: tail -f ${log_path}"
