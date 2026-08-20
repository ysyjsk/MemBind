"""Fail-closed opportunity reducer for the fixed 12-source MemBind-VDC oracle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v4.semantic_call import validate_semantic_call_pair

from .certificate import DependencyClass, VersionedReadCertificate


class VDCOracleError(ValueError):
    """Oracle evidence is incomplete, duplicated, or internally inconsistent."""


def _fail(code: str) -> VDCOracleError:
    return VDCOracleError(code)


def _nonnegative(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


@dataclass(frozen=True, slots=True)
class VDCOracleRow:
    source_sequence: int
    prepared_durable_ns: int
    predecessor_publication_ns: int
    stale_probe_completed_ns: int | None
    dependency_class: DependencyClass
    stale_read: VersionedReadCertificate | None
    exact_read: VersionedReadCertificate
    exact_node_resolve_service_ns: int

    def verify(self) -> "VDCOracleRow":
        _nonnegative(self.source_sequence, "source_sequence_invalid")
        _nonnegative(self.prepared_durable_ns, "prepared_durable_ns_invalid")
        _nonnegative(
            self.predecessor_publication_ns,
            "predecessor_publication_ns_invalid",
        )
        if self.stale_probe_completed_ns is not None:
            _nonnegative(self.stale_probe_completed_ns, "stale_probe_completed_ns_invalid")
        if not isinstance(self.dependency_class, DependencyClass):
            raise _fail("dependency_class_invalid")
        if self.stale_read is not None:
            self.stale_read.verify()
            if self.stale_read.source_sequence != self.source_sequence:
                raise _fail("stale_read_source_mismatch")
        if not isinstance(self.exact_read, VersionedReadCertificate):
            raise _fail("exact_read_invalid")
        self.exact_read.verify()
        if self.exact_read.source_sequence != self.source_sequence:
            raise _fail("exact_read_source_mismatch")
        _nonnegative(
            self.exact_node_resolve_service_ns,
            "exact_node_resolve_service_ns_invalid",
        )
        return self


def reduce_vdc_oracle(
    rows: Iterable[VDCOracleRow],
    *,
    expected_source_sequences: tuple[int, ...] = tuple(range(1, 12)),
) -> dict[str, object]:
    """Reduce only legal, captured opportunities; never synthesize stale probes."""

    expected = tuple(expected_source_sequences)
    if len(set(expected)) != len(expected) or any(
        isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in expected
    ):
        raise _fail("expected_source_sequences_invalid")
    selected = list(rows)
    if any(not isinstance(row, VDCOracleRow) for row in selected):
        raise _fail("oracle_row_invalid")
    for row in selected:
        row.verify()
    by_source = {row.source_sequence: row for row in selected}
    if len(by_source) != len(selected):
        raise _fail("oracle_source_duplicate")
    if tuple(sorted(by_source)) != tuple(sorted(expected)):
        raise _fail("oracle_source_coverage_invalid")

    counts = {
        "source_pair_count": len(selected),
        "future_prepared_before_publication_count": 0,
        "stale_probe_ready_before_publication_count": 0,
        "certified_disjoint_count": 0,
        "certified_conflict_count": 0,
        "unknown_count": 0,
        "exact_validation_count": 0,
        "validation_hit_count": 0,
        "validation_miss_count": 0,
        "validatable_unknown_count": 0,
        "hideable_opportunity_count": 0,
    }
    output_rows: list[dict[str, object]] = []
    total_hideable = 0
    for row in sorted(selected, key=lambda item: item.source_sequence):
        counts[
            {
                DependencyClass.CERTIFIED_DISJOINT: "certified_disjoint_count",
                DependencyClass.CERTIFIED_CONFLICT: "certified_conflict_count",
                DependencyClass.UNKNOWN: "unknown_count",
            }[row.dependency_class]
        ] += 1
        prepared_early = row.prepared_durable_ns < row.predecessor_publication_ns
        if prepared_early:
            counts["future_prepared_before_publication_count"] += 1
        probe_early = (
            prepared_early
            and row.stale_read is not None
            and row.stale_probe_completed_ns is not None
            and row.stale_probe_completed_ns < row.predecessor_publication_ns
        )
        if probe_early:
            counts["stale_probe_ready_before_publication_count"] += 1
        validation: str | None = None
        reusable = row.dependency_class is DependencyClass.CERTIFIED_DISJOINT
        if probe_early and row.dependency_class is DependencyClass.UNKNOWN:
            assert row.stale_read is not None
            counts["exact_validation_count"] += 1
            decision = validate_semantic_call_pair(
                row.stale_read.semantic_call,
                row.exact_read.semantic_call,
            )
            if decision.decision == "REUSE":
                counts["validation_hit_count"] += 1
                counts["validatable_unknown_count"] += 1
                validation = "HIT"
                reusable = True
            else:
                counts["validation_miss_count"] += 1
                validation = "MISS"
                reusable = False
        hideable = 0
        if probe_early and reusable:
            assert row.stale_probe_completed_ns is not None
            hideable = min(
                row.exact_node_resolve_service_ns,
                row.predecessor_publication_ns - row.stale_probe_completed_ns,
            )
            if hideable > 0:
                counts["hideable_opportunity_count"] += 1
                total_hideable += hideable
        output_rows.append(
            {
                "source_sequence": row.source_sequence,
                "dependency_class": row.dependency_class.value,
                "prepared_before_predecessor_publication": prepared_early,
                "stale_probe_ready_before_predecessor_publication": probe_early,
                "exact_validation": validation,
                "exact_node_resolve_service_ns": row.exact_node_resolve_service_ns,
                "hideable_node_resolve_service_ns": hideable,
            }
        )

    if counts["future_prepared_before_publication_count"] == 0:
        status = "STOP_V4_VDC_NO_LEGAL_WINDOW"
        reason = "NO_FUTURE_PREPARED_ARTIFACT_BEFORE_PREDECESSOR_PUBLICATION"
    elif counts["stale_probe_ready_before_publication_count"] == 0:
        status = "STOP_V4_VDC_NO_LEGAL_WINDOW"
        reason = "NO_VERSIONED_STALE_PROBE_READY_BEFORE_PUBLICATION"
    elif (
        counts["certified_disjoint_count"] + counts["validatable_unknown_count"]
        == 0
    ):
        status = "STOP_V4_VDC_DEPENDENCY_BOUNDARY"
        reason = "NO_CERTIFIED_OR_EXACTLY_VALIDATABLE_STATEFUL_RESOLVE"
    elif total_hideable <= 0:
        status = "STOP_V4_VDC_NO_HIDEABLE_SERVICE"
        reason = "CERTIFICATES_DO_NOT_HIDE_REAL_NODE_RESOLVE_SERVICE"
    else:
        status = "GO_MEMBIND_VDC_IMPLEMENTATION"
        reason = "CAPTURED_CERTIFICATE_ORACLE_HAS_LEGAL_HIDEABLE_SERVICE"
    body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v4.vdc-certificate-oracle.v1",
        "counts": counts,
        "total_hideable_node_resolve_service_ns": total_hideable,
        "rows": output_rows,
        "decision": {
            "status": status,
            "reason": reason,
            "live_candidate_authorized": status == "GO_MEMBIND_VDC_IMPLEMENTATION",
        },
    }
    return {**body, "payload_sha256": payload_sha256(body)}


__all__ = ["VDCOracleError", "VDCOracleRow", "reduce_vdc_oracle"]

