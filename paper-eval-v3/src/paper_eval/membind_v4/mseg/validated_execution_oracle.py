"""Conservative reduction of a bounded validated-execution shadow capture."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

from .operator_readiness import (
    OperatorReadinessResult,
    OperatorReadinessStatus,
    audit_operator_readiness,
)
from .read_view import ReadViewStatus, SemanticReadView


class ValidatedExecutionOracleError(ValueError):
    """A shadow-oracle row or threshold is malformed."""


def _fail(code: str) -> ValidatedExecutionOracleError:
    return ValidatedExecutionOracleError(code)


def _count(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _fraction(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(code)
    selected = float(value)
    if selected < 0 or selected > 1:
        raise _fail(code)
    return selected


@dataclass(frozen=True, slots=True)
class OracleThresholds:
    minimum_early_fraction: float = 0.05
    minimum_stable_fraction: float = 0.50
    minimum_hit_fraction: float = 0.10
    minimum_net_value_ns: int = 1

    def __post_init__(self) -> None:
        _fraction(self.minimum_early_fraction, "minimum_early_fraction_invalid")
        _fraction(self.minimum_stable_fraction, "minimum_stable_fraction_invalid")
        _fraction(self.minimum_hit_fraction, "minimum_hit_fraction_invalid")
        _count(self.minimum_net_value_ns, "minimum_net_value_invalid")


@dataclass(frozen=True, slots=True)
class ValidatedExecutionRow:
    operator_instance_id: str
    operator_kind: str
    readiness: OperatorReadinessResult
    shadow_read_view: SemanticReadView | None
    exact_read_view: SemanticReadView | None
    exact_llm_service_ns: int
    readview_materialization_ns: int
    exact_revalidation_ns: int
    shadow_llm_calls: int = 0
    shadow_persistent_writes: int = 0
    publication_modifications: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.operator_instance_id, str) or not self.operator_instance_id:
            raise _fail("oracle_operator_id_invalid")
        if not isinstance(self.operator_kind, str) or not self.operator_kind:
            raise _fail("oracle_operator_kind_invalid")
        if not isinstance(self.readiness, OperatorReadinessResult):
            raise _fail("oracle_readiness_invalid")
        if self.readiness.operator_instance_id != self.operator_instance_id:
            raise _fail("oracle_readiness_operator_mismatch")
        if self.readiness.operator_kind != self.operator_kind:
            raise _fail("oracle_readiness_kind_mismatch")
        for view, code in (
            (self.shadow_read_view, "shadow_read_view_invalid"),
            (self.exact_read_view, "exact_read_view_invalid"),
        ):
            if view is not None and not isinstance(view, SemanticReadView):
                raise _fail(code)
            if view is not None and view.operator_instance_id != self.operator_instance_id:
                raise _fail("oracle_read_view_operator_mismatch")
        for value, code in (
            (self.exact_llm_service_ns, "exact_llm_service_invalid"),
            (self.readview_materialization_ns, "readview_materialization_invalid"),
            (self.exact_revalidation_ns, "exact_revalidation_invalid"),
            (self.shadow_llm_calls, "shadow_llm_calls_invalid"),
            (self.shadow_persistent_writes, "shadow_writes_invalid"),
            (self.publication_modifications, "publication_modifications_invalid"),
        ):
            _count(value, code)


@dataclass(frozen=True, slots=True)
class ValidatedExecutionReport:
    status: str
    reason: str
    total_semantic_operators: int
    locally_ready_operators: int
    local_ready_before_whole_prepared_artifact: int
    local_ready_before_exact_predecessor_publication: int
    readiness_advance_p50_ns: int | None
    readiness_advance_p95_ns: int | None
    readiness_advance_max_ns: int | None
    shadow_probe_attempts: int
    stable_shadow_readviews: int
    unstable_discarded_readviews: int
    exact_rematerializations: int
    opaque: int
    validation_hit: int
    validation_miss: int
    hit_rate: float | None
    validation_by_operator_kind: dict[str, dict[str, int]]
    potentially_hideable_llm_service_ns: int
    readview_materialization_ns: int
    exact_revalidation_ns: int
    potential_net_value_ns: int
    value_label: str
    writes_from_shadow: int
    shadow_llm_calls: int
    publication_modifications: int
    thresholds: OracleThresholds

    def to_document(self) -> dict[str, object]:
        return asdict(self)


def reduce_validated_execution_opportunity(
    rows: tuple[ValidatedExecutionRow, ...],
    *,
    offline_gates_passed: bool,
    capture_complete: bool,
    thresholds: OracleThresholds | None = None,
) -> ValidatedExecutionReport:
    if not isinstance(rows, tuple) or any(
        not isinstance(row, ValidatedExecutionRow) for row in rows
    ):
        raise _fail("validated_execution_rows_invalid")
    if not isinstance(offline_gates_passed, bool) or not isinstance(
        capture_complete, bool
    ):
        raise _fail("oracle_gate_flag_invalid")
    selected_thresholds = thresholds or OracleThresholds()
    if not isinstance(selected_thresholds, OracleThresholds):
        raise _fail("oracle_thresholds_invalid")

    readiness = audit_operator_readiness(tuple(row.readiness for row in rows))
    attempts = sum(row.shadow_read_view is not None for row in rows)
    stable = 0
    unstable = 0
    rematerializations = 0
    opaque = 0
    hits = 0
    misses = 0
    hideable = 0
    by_kind: dict[str, Counter[str]] = defaultdict(Counter)

    for row in rows:
        shadow = row.shadow_read_view
        exact = row.exact_read_view
        if shadow is None:
            continue
        if shadow.status is ReadViewStatus.INVALID_UNSTABLE_READ:
            unstable += 1
            by_kind[row.operator_kind]["unstable"] += 1
            continue
        if shadow.status is not ReadViewStatus.STABLE_READVIEW:
            opaque += 1
            by_kind[row.operator_kind]["opaque"] += 1
            continue
        stable += 1
        by_kind[row.operator_kind]["stable"] += 1
        if exact is None:
            opaque += 1
            by_kind[row.operator_kind]["opaque"] += 1
            continue
        rematerializations += 1
        if exact.status is ReadViewStatus.INVALID_UNSTABLE_READ:
            unstable += 1
            by_kind[row.operator_kind]["unstable"] += 1
            continue
        if (
            exact.status is not ReadViewStatus.STABLE_READVIEW
            or shadow.read_view_digest is None
            or exact.read_view_digest is None
        ):
            opaque += 1
            by_kind[row.operator_kind]["opaque"] += 1
            continue
        if shadow.read_view_digest == exact.read_view_digest:
            hits += 1
            hideable += row.exact_llm_service_ns
            by_kind[row.operator_kind]["hit"] += 1
        else:
            misses += 1
            by_kind[row.operator_kind]["miss"] += 1

    shadow_llm_calls = sum(row.shadow_llm_calls for row in rows)
    writes = sum(row.shadow_persistent_writes for row in rows)
    publication_modifications = sum(row.publication_modifications for row in rows)
    materialization_cost = sum(row.readview_materialization_ns for row in rows)
    revalidation_cost = sum(row.exact_revalidation_ns for row in rows)
    net = hideable - materialization_cost - revalidation_cost
    evaluable = hits + misses
    hit_rate = None if evaluable == 0 else hits / evaluable
    early_denominator = max(1, readiness.locally_ready_operators)
    early_fraction = (
        readiness.local_ready_before_whole_prepared_artifact / early_denominator
    )
    stable_fraction = 0.0 if attempts == 0 else stable / attempts

    if (
        not offline_gates_passed
        or not capture_complete
        or not rows
        or shadow_llm_calls
        or writes
        or publication_modifications
    ):
        status = "STOP_INSTRUMENTATION_FAILURE"
        reason = "OFFLINE_OR_CAPTURE_CORRECTNESS_GATE_FAILED"
    elif early_fraction < selected_thresholds.minimum_early_fraction:
        status = "STOP_MEG_NO_FINER_GRAINED_WINDOW"
        reason = "LOCAL_READINESS_NOT_MATERIALLY_EARLIER"
    elif stable_fraction < selected_thresholds.minimum_stable_fraction:
        status = "STOP_READVIEW_UNSTABLE"
        reason = "STABLE_READVIEW_FRACTION_BELOW_THRESHOLD"
    elif hit_rate is None or hit_rate < selected_thresholds.minimum_hit_fraction:
        status = "STOP_VALIDATED_CONTINUATION_LOW_STABILITY"
        reason = "VALIDATION_HIT_FRACTION_BELOW_THRESHOLD"
    elif net < selected_thresholds.minimum_net_value_ns:
        status = "STOP_VALIDATED_CONTINUATION_LOW_STABILITY"
        reason = "NON_POSITIVE_SHADOW_UPPER_BOUND_VALUE"
    else:
        status = "GO_VALIDATED_SEMANTIC_CONTINUATION"
        reason = "ALL_VALIDATED_CONTINUATION_GATES_PASSED"

    return ValidatedExecutionReport(
        status=status,
        reason=reason,
        total_semantic_operators=readiness.total_semantic_operators,
        locally_ready_operators=readiness.locally_ready_operators,
        local_ready_before_whole_prepared_artifact=(
            readiness.local_ready_before_whole_prepared_artifact
        ),
        local_ready_before_exact_predecessor_publication=(
            readiness.local_ready_before_exact_predecessor_publication
        ),
        readiness_advance_p50_ns=readiness.readiness_advance_p50_ns,
        readiness_advance_p95_ns=readiness.readiness_advance_p95_ns,
        readiness_advance_max_ns=readiness.readiness_advance_max_ns,
        shadow_probe_attempts=attempts,
        stable_shadow_readviews=stable,
        unstable_discarded_readviews=unstable,
        exact_rematerializations=rematerializations,
        opaque=opaque,
        validation_hit=hits,
        validation_miss=misses,
        hit_rate=hit_rate,
        validation_by_operator_kind={
            kind: dict(sorted(counts.items()))
            for kind, counts in sorted(by_kind.items())
        },
        potentially_hideable_llm_service_ns=hideable,
        readview_materialization_ns=materialization_cost,
        exact_revalidation_ns=revalidation_cost,
        potential_net_value_ns=net,
        value_label="OFFLINE/SHADOW UPPER-BOUND DIAGNOSTIC",
        writes_from_shadow=writes,
        shadow_llm_calls=shadow_llm_calls,
        publication_modifications=publication_modifications,
        thresholds=selected_thresholds,
    )


__all__ = [
    "OracleThresholds",
    "ValidatedExecutionOracleError",
    "ValidatedExecutionReport",
    "ValidatedExecutionRow",
    "reduce_validated_execution_opportunity",
]
