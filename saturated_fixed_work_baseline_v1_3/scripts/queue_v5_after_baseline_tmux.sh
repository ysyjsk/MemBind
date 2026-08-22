#!/usr/bin/env bash
set -euo pipefail

repo_root="${1:?repo root required}"
baseline_root="${2:?baseline root required}"
queue_root="${3:?queue root required}"
session_name="${TMUX_SESSION_NAME:-membind-v5-gated}"
exec env PYTHONPATH="$repo_root/saturated_fixed_work_baseline_v1_3/src:$repo_root/saturated_fixed_work_baseline_v1_2/src:$repo_root/membind-validation/src" \
  "$repo_root/membind-validation/.venv/bin/python" \
  "$repo_root/saturated_fixed_work_baseline_v1_3/scripts/queue_v5_after_baseline.py" \
  --repo-root "$repo_root" --baseline-root "$baseline_root" --queue-root "$queue_root" \
  --session-name "$session_name" \
  --command "PYTHONPATH=$repo_root/saturated_fixed_work_baseline_v1_3/src run_v5_campaign.py --baseline-root $baseline_root"

