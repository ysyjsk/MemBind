"""Experiment-critical test qualification without physical-resource gates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


class TestQualificationError(ValueError):
    """Test evidence is malformed or the qualification gate failed."""

    __test__ = False


def _failure_records(
    value: Sequence[Mapping[str, Any]], field: str
) -> list[dict[str, str]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TestQualificationError(f"{field.upper()}_INVALID")
    rows: list[dict[str, str]] = []
    for row in value:
        if not isinstance(row, Mapping):
            raise TestQualificationError("FAILURE_RECORD_INVALID")
        test_id, signature = row.get("test_id"), row.get("signature")
        if (
            not isinstance(test_id, str)
            or not test_id.strip()
            or not isinstance(signature, str)
            or not signature.strip()
        ):
            raise TestQualificationError("FAILURE_RECORD_INVALID")
        rows.append({"test_id": test_id, "signature": signature})
    return rows


def evaluate_test_qualification(
    *,
    sfwb_failures: Sequence[Mapping[str, Any]],
    targeted_failures: Sequence[Mapping[str, Any]],
    repository_failures: Sequence[Mapping[str, Any]],
    clean_head_failures: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return the fail-closed ``NEW_REGRESSION_COUNT == 0`` result."""

    sfwb = _failure_records(sfwb_failures, "sfwb_failures")
    targeted = _failure_records(targeted_failures, "targeted_failures")
    repository = _failure_records(repository_failures, "repository_failures")
    clean = _failure_records(clean_head_failures, "clean_head_failures")
    clean_keys = {(row["test_id"], row["signature"]) for row in clean}
    preexisting = [
        row for row in repository if (row["test_id"], row["signature"]) in clean_keys
    ]
    repository_new = [
        row for row in repository if (row["test_id"], row["signature"]) not in clean_keys
    ]
    unexpected = (
        [{"scope": "sfwb", **row} for row in sfwb]
        + [{"scope": "targeted", **row} for row in targeted]
        + [{"scope": "repository", **row} for row in repository_new]
    )
    return {
        "schema_version": "sfwb.v1.3.test-qualification.v1",
        "qualification_semantics": "NEW_REGRESSION_COUNT_ZERO",
        "sfwb_failure_count": len(sfwb),
        "targeted_failure_count": len(targeted),
        "repository_failure_count": len(repository),
        "clean_head_failure_count": len(clean),
        "preexisting_repository_failures": preexisting,
        "unexpected_failures": unexpected,
        "new_regression_count": len(unexpected),
        "repository_wide_status": (
            "PASS"
            if not repository
            else "PASS_WITH_PREEXISTING_FAILURES"
            if not repository_new
            else "FAIL_NEW_REGRESSIONS"
        ),
        "qualification_passed": not unexpected,
    }


def require_test_qualification(result: Mapping[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(result, Mapping)
        or result.get("qualification_semantics") != "NEW_REGRESSION_COUNT_ZERO"
        or result.get("qualification_passed") is not True
        or result.get("new_regression_count") != 0
    ):
        raise TestQualificationError("TEST_QUALIFICATION_FAILED")
    return {"authorized": True, "new_regression_count": 0}


__all__ = [
    "TestQualificationError",
    "evaluate_test_qualification",
    "require_test_qualification",
]
