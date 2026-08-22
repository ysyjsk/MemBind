#!/usr/bin/env bash
set -u

# Operational supervisor only: each replication still goes through the
# existing sample-level Python entry point and the frozen cohort.  Short root
# names keep every derived run_id below the pinned v1.2 80-character limit.
REPO_ROOT="/data/predator/ly/MemBind"
AUDIT_ROOT="$REPO_ROOT/saturated_fixed_work_baseline_v1_3/artifacts/sfwb-v1-3-memops-hazard-audit-20260822-001"
PYTHON_BIN="$REPO_ROOT/membind-validation/.venv/bin/python"
SCRIPT="$REPO_ROOT/saturated_fixed_work_baseline_v1_3/scripts/run_memops_hazard_sample_replication.py"
PYTHONPATH_VALUE="$REPO_ROOT/saturated_fixed_work_baseline_v1_3/src:$REPO_ROOT/saturated_fixed_work_baseline_v1_2/src:$REPO_ROOT/saturated_fixed_work_baseline_v1_3/scripts"
ARTIFACT_ROOT="$REPO_ROOT/saturated_fixed_work_baseline_v1_3/artifacts"

run_one() {
  local method="$1"
  local ordinal="$2"
  local root="$3"
  printf 'SUPERVISOR_START method=%s ordinal=%s root=%s\n' "$method" "$ordinal" "$root"
  PYTHONPATH="$PYTHONPATH_VALUE" "$PYTHON_BIN" "$SCRIPT" \
    --audit-root "$AUDIT_ROOT" \
    --replication-root "$root" \
    --repository-root "$REPO_ROOT" \
    --method "$method" \
    --ordinal "$ordinal" \
    --sample-timeout-s 180
  local rc=$?
  printf 'SUPERVISOR_DONE method=%s ordinal=%s rc=%s root=%s\n' "$method" "$ordinal" "$rc" "$root"
  return 0
}

run_recovery() {
  printf 'SUPERVISOR_RECOVERY_START method=%s ordinal=%s source=%s recovery=%s\n' "$1" "$2" "$3" "$4"
  PYTHONPATH="$PYTHONPATH_VALUE" "$PYTHON_BIN" "$REPO_ROOT/saturated_fixed_work_baseline_v1_3/scripts/resume_memops_hazard_replication.py" \
    --audit-root "$AUDIT_ROOT" \
    --source-root "$3" \
    --recovery-root "$4" \
    --repository-root "$REPO_ROOT" \
    --method "$1" \
    --ordinal "$2" \
    --sample-timeout-s 180
  local rc=$?
  printf 'SUPERVISOR_RECOVERY_DONE method=%s ordinal=%s rc=%s recovery=%s\n' "$1" "$2" "$rc" "$4"
  return 0
}

run_recovery B0_NATIVE_SERIAL 1 "$ARTIFACT_ROOT/sfwb-v1-3-memops-hazard-replication-20260822-009/b0-r001" "$ARTIFACT_ROOT/hrep015"
run_one B1_NAIVE_WHOLE_UPDATE_ASYNC 1 "$ARTIFACT_ROOT/hrep018/b1r001"
run_one B0_NATIVE_SERIAL 2 "$ARTIFACT_ROOT/hrep016/b0r002"
run_one B1_NAIVE_WHOLE_UPDATE_ASYNC 2 "$ARTIFACT_ROOT/hrep019/b1r002"
run_one B0_NATIVE_SERIAL 3 "$ARTIFACT_ROOT/hrep017/b0r003"
run_one B1_NAIVE_WHOLE_UPDATE_ASYNC 3 "$ARTIFACT_ROOT/hrep020/b1r003"
run_one B1_NAIVE_WHOLE_UPDATE_ASYNC 2 "$ARTIFACT_ROOT/hrep019/b1r002"
run_one B1_NAIVE_WHOLE_UPDATE_ASYNC 3 "$ARTIFACT_ROOT/hrep020/b1r003"

printf 'SUPERVISOR_ALL_DONE\n'
