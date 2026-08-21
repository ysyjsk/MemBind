"""Prospective v1.3 adapter with no import-time legacy validity gate."""

from .preflight import V1_3PreflightError, validate_v1_3_preflight
from .test_qualification import (
    TestQualificationError,
    evaluate_test_qualification,
    require_test_qualification,
)

__all__ = [
    "TestQualificationError",
    "V1_3PreflightError",
    "evaluate_test_qualification",
    "require_test_qualification",
    "validate_v1_3_preflight",
]
