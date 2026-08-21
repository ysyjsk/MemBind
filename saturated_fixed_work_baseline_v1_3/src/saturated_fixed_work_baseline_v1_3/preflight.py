"""Pure v1.3 L0 readiness checks for experiment-critical prerequisites."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class V1_3PreflightError(ValueError):
    """A required execution prerequisite is absent."""


_PREFLIGHT_GATES = (
    "construction_endpoint",
    "embedding_endpoint",
    "neo4j",
    "workload",
    "runner",
    "instrumentation",
    "warmup",
    "idle",
)


def validate_v1_3_preflight(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Validate only gates that can prevent a credible construction block."""

    if not isinstance(evidence, Mapping):
        raise V1_3PreflightError("PREFLIGHT_EVIDENCE_INVALID")
    errors = {
        "construction_endpoint": "CONSTRUCTION_ENDPOINT_UNAVAILABLE",
        "embedding_endpoint": "EMBEDDING_ENDPOINT_UNAVAILABLE",
        "neo4j": "NEO4J_UNAVAILABLE",
        "workload": "WORKLOAD_UNAVAILABLE",
        "runner": "RUNNER_UNAVAILABLE",
        "instrumentation": "INSTRUMENTATION_UNAVAILABLE",
        "warmup": "WARMUP_FAILED",
        "idle": "BACKEND_NOT_IDLE",
    }
    for gate in _PREFLIGHT_GATES:
        if evidence.get(gate) is not True:
            raise V1_3PreflightError(errors[gate])
    return {
        "schema_version": "sfwb.v1.3.preflight.v1",
        "protocol_version": "SATURATED_FIXED_WORK_CONSTRUCTION_PROTOCOL_V1_3",
        "status": "PASS",
        "formal_run_authorized": True,
        "required_gates": _PREFLIGHT_GATES,
        "evidence": dict(evidence),
    }


__all__ = ["V1_3PreflightError", "validate_v1_3_preflight"]
