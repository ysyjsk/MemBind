"""Direct-dependency readiness for semantic operators.

The calculation deliberately has no scheduler.  It compares a shadow MEG's
local semantic readiness with the observed whole-``PreparedArtifact`` barrier
using only direct evidence, direct control, and state requirements backed by
source, adapter-contract, or runtime-lineage evidence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class OperatorReadinessError(ValueError):
    """Readiness evidence is malformed or internally contradictory."""


def _fail(code: str) -> OperatorReadinessError:
    return OperatorReadinessError(code)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise _fail(code)
    return value


def _time(value: object, code: str, *, optional: bool = False) -> int | None:
    if optional and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


class ReadinessDependencyKind(str, Enum):
    EVIDENCE = "EVIDENCE"
    CONTROL = "CONTROL"
    STATE = "STATE"


class DependencyEvidenceSource(str, Enum):
    SOURCE_CODE = "SOURCE_CODE"
    ADAPTER_CONTRACT = "ADAPTER_CONTRACT"
    RUNTIME_LINEAGE = "RUNTIME_LINEAGE"
    UNKNOWN = "UNKNOWN"


class OperatorReadinessStatus(str, Enum):
    LOCALLY_READY = "LOCALLY_READY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class DirectReadinessDependency:
    dependency_id: str
    kind: ReadinessDependencyKind
    satisfied_ns: int | None
    evidence_source: DependencyEvidenceSource

    def __post_init__(self) -> None:
        _text(self.dependency_id, "readiness_dependency_id_invalid")
        if not isinstance(self.kind, ReadinessDependencyKind):
            raise _fail("readiness_dependency_kind_invalid")
        _time(self.satisfied_ns, "readiness_dependency_time_invalid", optional=True)
        if not isinstance(self.evidence_source, DependencyEvidenceSource):
            raise _fail("readiness_dependency_source_invalid")


@dataclass(frozen=True, slots=True)
class OperatorReadinessInput:
    operator_instance_id: str
    operator_kind: str
    operator_available_ns: int | None
    prepared_artifact_ready_ns: int | None
    exact_predecessor_publication_ns: int | None
    direct_dependencies: tuple[DirectReadinessDependency, ...]

    def __post_init__(self) -> None:
        _text(self.operator_instance_id, "readiness_operator_id_invalid")
        _text(self.operator_kind, "readiness_operator_kind_invalid")
        _time(self.operator_available_ns, "operator_available_time_invalid", optional=True)
        _time(
            self.prepared_artifact_ready_ns,
            "prepared_artifact_ready_time_invalid",
            optional=True,
        )
        _time(
            self.exact_predecessor_publication_ns,
            "predecessor_publication_time_invalid",
            optional=True,
        )
        if not isinstance(self.direct_dependencies, tuple) or any(
            not isinstance(item, DirectReadinessDependency)
            for item in self.direct_dependencies
        ):
            raise _fail("direct_readiness_dependencies_invalid")
        identities = tuple(item.dependency_id for item in self.direct_dependencies)
        if len(identities) != len(set(identities)):
            raise _fail("duplicate_direct_readiness_dependency")


@dataclass(frozen=True, slots=True)
class OperatorReadinessResult:
    operator_instance_id: str
    operator_kind: str
    status: OperatorReadinessStatus
    prepared_artifact_ready_time_ns: int | None
    local_operator_ready_time_ns: int | None
    exact_predecessor_publication_ns: int | None
    readiness_advance_ns: int | None
    local_ready_before_whole_prepared_artifact: bool | None
    local_ready_before_exact_predecessor_publication: bool | None
    direct_dependency_ids: tuple[str, ...]
    evidence_sources: tuple[str, ...]
    codes: tuple[str, ...] = ()


def compute_operator_readiness(
    item: OperatorReadinessInput,
) -> OperatorReadinessResult:
    if not isinstance(item, OperatorReadinessInput):
        raise _fail("operator_readiness_input_invalid")
    codes: list[str] = []
    if item.operator_available_ns is None:
        codes.append("operator_available_time_unknown")
    if item.prepared_artifact_ready_ns is None:
        codes.append("prepared_artifact_ready_time_unknown")
    if item.exact_predecessor_publication_ns is None:
        codes.append("predecessor_publication_time_unknown")
    for dependency in item.direct_dependencies:
        if dependency.evidence_source is DependencyEvidenceSource.UNKNOWN:
            codes.append(f"dependency_source_unknown:{dependency.dependency_id}")
        if dependency.satisfied_ns is None:
            codes.append(f"dependency_time_unknown:{dependency.dependency_id}")
    if codes:
        return OperatorReadinessResult(
            operator_instance_id=item.operator_instance_id,
            operator_kind=item.operator_kind,
            status=OperatorReadinessStatus.UNKNOWN,
            prepared_artifact_ready_time_ns=item.prepared_artifact_ready_ns,
            local_operator_ready_time_ns=None,
            exact_predecessor_publication_ns=item.exact_predecessor_publication_ns,
            readiness_advance_ns=None,
            local_ready_before_whole_prepared_artifact=None,
            local_ready_before_exact_predecessor_publication=None,
            direct_dependency_ids=tuple(
                dependency.dependency_id for dependency in item.direct_dependencies
            ),
            evidence_sources=tuple(
                dependency.evidence_source.value
                for dependency in item.direct_dependencies
            ),
            codes=tuple(codes),
        )
    assert item.operator_available_ns is not None
    assert item.prepared_artifact_ready_ns is not None
    assert item.exact_predecessor_publication_ns is not None
    readiness_times = (
        item.operator_available_ns,
        *(dependency.satisfied_ns for dependency in item.direct_dependencies),
    )
    local_ready = max(readiness_times)
    assert isinstance(local_ready, int)
    return OperatorReadinessResult(
        operator_instance_id=item.operator_instance_id,
        operator_kind=item.operator_kind,
        status=OperatorReadinessStatus.LOCALLY_READY,
        prepared_artifact_ready_time_ns=item.prepared_artifact_ready_ns,
        local_operator_ready_time_ns=local_ready,
        exact_predecessor_publication_ns=item.exact_predecessor_publication_ns,
        readiness_advance_ns=item.prepared_artifact_ready_ns - local_ready,
        local_ready_before_whole_prepared_artifact=(
            local_ready < item.prepared_artifact_ready_ns
        ),
        local_ready_before_exact_predecessor_publication=(
            local_ready < item.exact_predecessor_publication_ns
        ),
        direct_dependency_ids=tuple(
            dependency.dependency_id for dependency in item.direct_dependencies
        ),
        evidence_sources=tuple(
            dependency.evidence_source.value for dependency in item.direct_dependencies
        ),
    )


def _percentile(values: tuple[int, ...], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    position = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[position]


@dataclass(frozen=True, slots=True)
class OperatorReadinessAudit:
    total_semantic_operators: int
    whole_prepared_artifact_ready_count: int
    locally_ready_operators: int
    unknown_operators: int
    local_ready_before_whole_prepared_artifact: int
    local_ready_before_exact_predecessor_publication: int
    readiness_advance_p50_ns: int | None
    readiness_advance_p95_ns: int | None
    readiness_advance_max_ns: int | None
    operator_rows: tuple[OperatorReadinessResult, ...]


def audit_operator_readiness(
    rows: tuple[OperatorReadinessResult, ...],
) -> OperatorReadinessAudit:
    if not isinstance(rows, tuple) or any(
        not isinstance(row, OperatorReadinessResult) for row in rows
    ):
        raise _fail("operator_readiness_rows_invalid")
    advances = tuple(
        row.readiness_advance_ns
        for row in rows
        if row.status is OperatorReadinessStatus.LOCALLY_READY
        and row.readiness_advance_ns is not None
        and row.readiness_advance_ns > 0
    )
    return OperatorReadinessAudit(
        total_semantic_operators=len(rows),
        whole_prepared_artifact_ready_count=sum(
            row.prepared_artifact_ready_time_ns is not None for row in rows
        ),
        locally_ready_operators=sum(
            row.status is OperatorReadinessStatus.LOCALLY_READY for row in rows
        ),
        unknown_operators=sum(
            row.status is OperatorReadinessStatus.UNKNOWN for row in rows
        ),
        local_ready_before_whole_prepared_artifact=sum(
            row.local_ready_before_whole_prepared_artifact is True for row in rows
        ),
        local_ready_before_exact_predecessor_publication=sum(
            row.local_ready_before_exact_predecessor_publication is True
            for row in rows
        ),
        readiness_advance_p50_ns=_percentile(advances, 0.50),
        readiness_advance_p95_ns=_percentile(advances, 0.95),
        readiness_advance_max_ns=max(advances) if advances else None,
        operator_rows=rows,
    )


__all__ = [
    "DependencyEvidenceSource",
    "DirectReadinessDependency",
    "OperatorReadinessAudit",
    "OperatorReadinessError",
    "OperatorReadinessInput",
    "OperatorReadinessResult",
    "OperatorReadinessStatus",
    "ReadinessDependencyKind",
    "audit_operator_readiness",
    "compute_operator_readiness",
]
