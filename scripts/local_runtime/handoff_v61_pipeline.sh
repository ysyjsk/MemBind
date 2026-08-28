#!/usr/bin/env bash
set -euo pipefail

old_pid="${1:?usage: handoff_v61_pipeline.sh OLD_PIPELINE_PID}"
repo_root="/data/predator/ly/MemBind"
experiment_root="/data/predator/ly/Mem/experiments/local-qwen3-14b-awq-v1/v6_1_mab"
state_path="${experiment_root}/state/pipeline_state.json"

# Do not compete with the active supervisor or its timed construction child.
while [[ -r "/proc/${old_pid}/cmdline" ]]; do
    if ! tr '\0' ' ' < "/proc/${old_pid}/cmdline" | grep -q 'run_v61_pipeline_local.py'; then
        break
    fi
    sleep 30
done

stage=""
if [[ -f "${state_path}" ]]; then
    stage="$(jq -r '.stage // ""' "${state_path}" 2>/dev/null || true)"
fi
if [[ "${stage}" == "FULL5_SUPERVISOR" ]]; then
    exit 0
fi
if pgrep -f 'saturated_fixed_work_baseline_v1_3/scripts/run_v61_pipeline_local.py' >/dev/null; then
    exit 0
fi

cd "${repo_root}"
source "${repo_root}/scripts/local_runtime/activate.sh"
exec python saturated_fixed_work_baseline_v1_3/scripts/run_v61_pipeline_local.py \
    --autoresearch-id v61-ar-20260827-handoff \
    --full5-id v61-full5-20260827-handoff \
    --heartbeat-seconds 30
