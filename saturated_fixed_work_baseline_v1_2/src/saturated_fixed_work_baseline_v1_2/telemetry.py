"""Pinned-stack telemetry attribution contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import Availability, MetricValue
from .reuse import import_paper_eval_module


@dataclass(frozen=True, slots=True)
class TelemetryObservation:
    availability: Availability
    value: Any | None
    reason: str | None = None


def parse_vllm_026_metrics(
    text: str, *, timestamp_ns: int, repository_root: Path
) -> TelemetryObservation:
    module = import_paper_eval_module(
        repository_root, "paper_eval.apc_vllm_telemetry"
    )
    try:
        snapshot = module.parse_vllm_prometheus(text, timestamp_ns=timestamp_ns)
    except ValueError as error:
        return TelemetryObservation(
            availability=Availability.INVALID,
            value=None,
            reason=str(error),
        )
    return TelemetryObservation(
        availability=Availability.MEASURED,
        value=snapshot,
    )


def telemetry_attribution(
    *,
    idle_before: bool,
    idle_after: bool,
    no_other_clients: bool,
    sampler_complete: bool,
) -> MetricValue:
    if idle_before and idle_after and no_other_clients and sampler_complete:
        return MetricValue(availability=Availability.MEASURED, value=1)
    failed = [
        name
        for name, passed in (
            ("idle_before", idle_before),
            ("idle_after", idle_after),
            ("no_other_clients", no_other_clients),
            ("sampler_complete", sampler_complete),
        )
        if not passed
    ]
    return MetricValue.unavailable(
        Availability.AMBIGUOUS_PROCESS_GLOBAL,
        "process_global_window_not_exclusive:" + ",".join(failed),
    )


__all__ = [
    "TelemetryObservation",
    "parse_vllm_026_metrics",
    "telemetry_attribution",
]

