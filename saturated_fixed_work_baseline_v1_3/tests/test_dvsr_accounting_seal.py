"""The code and sealed pre-G4 lambda artifact must remain identical."""

from __future__ import annotations

import json
from pathlib import Path

from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_accounting import accounting_identity


def test_accounting_identity_matches_sealed_artifact() -> None:
    path = Path("saturated_fixed_work_baseline_v1_3/v7/dvsr_v7_831_phase0/DVSR_ACCOUNTING_IDENTITY_SEAL.json")
    assert json.loads(path.read_text(encoding="ascii")) == accounting_identity()


def test_primary_lambda_is_preregistered_and_selection_only() -> None:
    identity = accounting_identity()
    assert identity["primary_failed_work_lambda"] == 0.5
    assert identity["unit"] == "dimensionless opportunity-cost weight"
    assert identity["sensitivity_set"] == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert "Only the primary lambda" in str(identity["selection_time_role"])
