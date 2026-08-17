#!/usr/bin/env bash
# Wait for the sealed development report, then derive the offline decision.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <methodology-run-id> <report-run-id>" >&2
  exit 2
fi

methodology_run_id=$1
report_run_id=$2
if [[ ! ${methodology_run_id} =~ ^methodology-[a-z0-9][a-z0-9-]{2,63}$ ]]; then
  echo "invalid methodology run id" >&2
  exit 2
fi
if [[ ! ${report_run_id} =~ ^report-[a-z0-9][a-z0-9-]{2,63}$ ]]; then
  echo "invalid report run id" >&2
  exit 2
fi

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
repository_dir=$(cd "${project_dir}/.." && pwd)
session_name="membind-methodology-decision-${methodology_run_id}"
report_session="membind-development-report-${report_run_id}"
report_path="${project_dir}/artifacts/paper_eval/development_report/runs/${report_run_id}/REPORT.json"
output_path="${project_dir}/artifacts/paper_eval/methodology_finalization/runs/${methodology_run_id}/METHODOLOGY_DECISION.json"
log_path="${project_dir}/logs/METHODOLOGY_DECISION_${methodology_run_id}.log"
python_bin="${project_dir}/.venv/bin/python"
c3_path="${repository_dir}/membind-validation/artifacts/native_characterization/e2_dependency_opportunity.json"
c5_root="${repository_dir}/membind-validation/artifacts/native_characterization/runs/c5-e3867c66ba92e7da"

if tmux has-session -t "${session_name}" 2>/dev/null; then
  echo "tmux session already exists: ${session_name}" >&2
  exit 1
fi

command=$(printf '%q ' bash -c '
set -euo pipefail
project_dir=$1
report_session=$2
report_path=$3
python_bin=$4
methodology_run_id=$5
c3_path=$6
c5_path=$7
c5_events_path=$8
output_path=$9
log_path=${10}
while [[ ! -f "$report_path" ]]; do
  if ! tmux has-session -t "$report_session" 2>/dev/null; then
    echo "STOP report session ended without sealed REPORT.json" | tee -a "$log_path"
    exit 1
  fi
  sleep 60
done
cd "$project_dir"
PYTHONPATH="$project_dir/src" "$python_bin" -u \
  scripts/finalize_methodology_decision.py \
  "$methodology_run_id" \
  --report "$report_path" \
  --c3 "$c3_path" \
  --c5 "$c5_path" \
  --c5-events "$c5_events_path" \
  --output "$output_path" 2>&1 | tee -a "$log_path"
' _ "${project_dir}" "${report_session}" "${report_path}" "${python_bin}" \
  "${methodology_run_id}" "${c3_path}" "${c5_root}/e4_whole_parallel.json" \
  "${c5_root}/events.jsonl" "${output_path}" "${log_path}")

tmux new-session -d -s "${session_name}" "${command}"
echo "started ${session_name}"
echo "log ${log_path}"
echo "decision ${output_path}"
