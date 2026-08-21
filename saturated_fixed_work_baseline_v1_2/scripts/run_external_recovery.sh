#!/usr/bin/env bash
set -euo pipefail

protocol_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repository_root="$(cd "${protocol_root}/.." && pwd)"
export PYTHONPATH="${protocol_root}/src${PYTHONPATH:+:${PYTHONPATH}}"
exec "${repository_root}/membind-validation/.venv/bin/python" \
  -m saturated_fixed_work_baseline_v1_2.cli external-recovery "$@"
