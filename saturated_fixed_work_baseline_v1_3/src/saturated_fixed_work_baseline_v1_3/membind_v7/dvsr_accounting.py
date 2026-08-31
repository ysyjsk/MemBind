"""Pre-G4 frozen DVSR economic accounting identity."""

from __future__ import annotations


FAILED_WORK_LAMBDA = 0.5
FAILED_WORK_LAMBDA_SENSITIVITY = (0.0, 0.25, 0.5, 0.75, 1.0)
FAILED_WORK_LAMBDA_UNIT = "dimensionless opportunity-cost weight"


def accounting_identity() -> dict[str, object]:
    return {
        "schema_version": "membind.dvsr.accounting-identity.v1",
        "status": "SEALED_BEFORE_G4",
        "primary_failed_work_lambda": FAILED_WORK_LAMBDA,
        "unit": FAILED_WORK_LAMBDA_UNIT,
        "economic_interpretation": (
            "Each nanosecond of discarded speculative work is charged at half "
            "a nanosecond of offline opportunity cost; foreground interference "
            "is excluded until G6."
        ),
        "selection_time_role": (
            "Only the primary lambda contributes to G4 OfflineBenefit and "
            "operator selection."
        ),
        "sensitivity_set": list(FAILED_WORK_LAMBDA_SENSITIVITY),
        "sensitivity_role": "reporting only; never operator selection",
        "frozen_before_development_full_histories": True,
    }


__all__ = [
    "FAILED_WORK_LAMBDA",
    "FAILED_WORK_LAMBDA_SENSITIVITY",
    "FAILED_WORK_LAMBDA_UNIT",
    "accounting_identity",
]
