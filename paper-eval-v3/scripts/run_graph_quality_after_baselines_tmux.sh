#!/usr/bin/env bash
# Wait for the sealed baseline report, then run the read-only quality overlay.
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 <overlay-run-id> <native-run-id> <suite-run-id>" >&2
  exit 2
fi

overlay_run_id=$1
native_run_id=$2
suite_run_id=$3
project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
session_name="membind-graph-quality-${overlay_run_id}"
baseline_session="membind-three-baselines-${suite_run_id}"
baseline_result="${project_dir}/artifacts/paper_eval/baseline_suite/runs/${suite_run_id}/THREE_BASELINE_RESULTS.json"
log_path="${project_dir}/logs/GRAPH_QUALITY_${overlay_run_id}.log"
python_bin="${project_dir}/../membind-validation/.venv/bin/python"

if tmux has-session -t "${session_name}" 2>/dev/null; then
  echo "tmux session already exists: ${session_name}" >&2
  exit 1
fi

command=$(printf '%q ' bash -c '
set -euo pipefail
project_dir=$1
baseline_session=$2
baseline_result=$3
python_bin=$4
overlay_run_id=$5
native_run_id=$6
suite_run_id=$7
log_path=$8
while [[ ! -f "$baseline_result" ]]; do
  if ! tmux has-session -t "$baseline_session" 2>/dev/null; then
    echo "STOP baseline session ended without sealed result" | tee -a "$log_path"
    exit 1
  fi
  sleep 60
done
cd "$project_dir"
"$python_bin" -u scripts/run_three_baseline_graph_quality.py \
  "$overlay_run_id" \
  --native-run "$native_run_id" \
  --suite-run "$suite_run_id" 2>&1 | tee -a "$log_path"
' _ "${project_dir}" "${baseline_session}" "${baseline_result}" "${python_bin}" "${overlay_run_id}" "${native_run_id}" "${suite_run_id}" "${log_path}")

tmux new-session -d -s "${session_name}" "${command}"
echo "started ${session_name}"
echo "log ${log_path}"
