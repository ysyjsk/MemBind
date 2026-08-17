#!/usr/bin/env bash
# Wait for the sealed graph-quality result, then derive the offline report.
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "usage: $0 <report-run-id> <native-run-id> <suite-run-id> <overlay-run-id>" >&2
  exit 2
fi

report_run_id=$1
native_run_id=$2
suite_run_id=$3
overlay_run_id=$4

if [[ ! ${report_run_id} =~ ^report-[a-z0-9][a-z0-9-]{2,63}$ ]]; then
  echo "invalid report run id" >&2
  exit 2
fi
if [[ ! ${native_run_id} =~ ^nb-[a-z0-9][a-z0-9-]{2,63}$ ]]; then
  echo "invalid native run id" >&2
  exit 2
fi
if [[ ! ${suite_run_id} =~ ^bs-[a-z0-9][a-z0-9-]{2,63}$ ]]; then
  echo "invalid suite run id" >&2
  exit 2
fi
if [[ ! ${overlay_run_id} =~ ^gq-[a-z0-9][a-z0-9-]{2,63}$ ]]; then
  echo "invalid overlay run id" >&2
  exit 2
fi

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
repository_dir=$(cd "${project_dir}/.." && pwd)
session_name="membind-development-report-${report_run_id}"
quality_session="membind-graph-quality-${overlay_run_id}"
quality_result="${project_dir}/artifacts/paper_eval/graph_quality_overlay/runs/${overlay_run_id}/GRAPH_QUALITY_RESULTS.json"
markdown_output="${repository_dir}/MemBind_THREE_BASELINE_DEVELOPMENT_EXPERIMENT_REPORT_20260817.md"
log_path="${project_dir}/logs/DEVELOPMENT_REPORT_${report_run_id}.log"
python_bin="${project_dir}/.venv/bin/python"

if tmux has-session -t "${session_name}" 2>/dev/null; then
  echo "tmux session already exists: ${session_name}" >&2
  exit 1
fi

command=$(printf '%q ' bash -c '
set -euo pipefail
project_dir=$1
quality_session=$2
quality_result=$3
python_bin=$4
report_run_id=$5
native_run_id=$6
suite_run_id=$7
overlay_run_id=$8
markdown_output=$9
log_path=${10}
while [[ ! -f "$quality_result" ]]; do
  if ! tmux has-session -t "$quality_session" 2>/dev/null; then
    echo "STOP graph-quality session ended without sealed result" | tee -a "$log_path"
    exit 1
  fi
  sleep 60
done
cd "$project_dir"
"$python_bin" -u scripts/write_three_baseline_development_report.py \
  "$report_run_id" \
  --native-run "$native_run_id" \
  --suite-run "$suite_run_id" \
  --overlay-run "$overlay_run_id" \
  --markdown-output "$markdown_output" 2>&1 | tee -a "$log_path"
' _ "${project_dir}" "${quality_session}" "${quality_result}" "${python_bin}" "${report_run_id}" "${native_run_id}" "${suite_run_id}" "${overlay_run_id}" "${markdown_output}" "${log_path}")

tmux new-session -d -s "${session_name}" "${command}"
echo "started ${session_name}"
echo "log ${log_path}"
echo "report ${markdown_output}"
