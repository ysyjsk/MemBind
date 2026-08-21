"""Offline qualification certificates and their intentionally narrow scope."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


class QualificationError(ValueError):
    """A pre-live measurement or adapter qualification failed."""


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()


def qualify_instrumentation_aa(
    *,
    baseline_graph: Mapping[str, Any],
    instrumented_graph: Mapping[str, Any],
    baseline_duration_ns: int,
    instrumented_duration_ns: int,
    max_overhead_fraction: float,
) -> dict[str, Any]:
    if baseline_duration_ns <= 0 or instrumented_duration_ns <= 0:
        raise QualificationError("INSTRUMENTATION_DURATION_INVALID")
    if not 0 <= max_overhead_fraction < 1:
        raise QualificationError("INSTRUMENTATION_THRESHOLD_INVALID")
    if _hash(baseline_graph) != _hash(instrumented_graph):
        raise QualificationError("INSTRUMENTATION_OUTPUT_CHANGED")
    overhead = (instrumented_duration_ns - baseline_duration_ns) / baseline_duration_ns
    if overhead > max_overhead_fraction:
        raise QualificationError("INSTRUMENTATION_OVERHEAD_EXCEEDED")
    return {
        "qualified": True,
        "overhead_fraction": overhead,
        "max_overhead_fraction": max_overhead_fraction,
        "output_hash": _hash(baseline_graph),
    }


def serial_serial_12_diagnostic(
    first_graph: Mapping[str, Any], second_graph: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "episode_count": 12,
        "scope": "12_EPISODE_QUALIFICATION_ONLY",
        "first_hash": _hash(first_graph),
        "second_hash": _hash(second_graph),
        "exact_match": _hash(first_graph) == _hash(second_graph),
        "full_history_nondeterminism_floor": None,
    }


__all__ = [
    "QualificationError",
    "qualify_instrumentation_aa",
    "serial_serial_12_diagnostic",
]
