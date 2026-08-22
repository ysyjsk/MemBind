#!/usr/bin/env bash
set -u

STATE="/data/predator/ly/MemBind/saturated_fixed_work_baseline_v1_3/artifacts/hrep015/checkpoint_state.json"
MAIN_SESSION="memops-hazard-replication"
SUPERVISOR="/data/predator/ly/MemBind/saturated_fixed_work_baseline_v1_3/scripts/run_memops_hazard_tmux_supervisor.sh"

# Wait for the currently running B29 recovery attempt to reach its sample
# checkpoint, then restart the queue with the B0/B1 alternating order.
while ! test -s "$STATE" || ! grep -q '"sample_id": "B29__Update"' "$STATE"; do
  sleep 5
done
tmux send-keys -t "$MAIN_SESSION":0 C-c
sleep 5
tmux kill-session -t "$MAIN_SESSION" 2>/dev/null || true
tmux new-session -d -s "$MAIN_SESSION" "bash $SUPERVISOR"
