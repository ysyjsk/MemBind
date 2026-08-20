"""Offline MSEG qualification gates.

The functions in this module classify evidence and opportunity. They never
change execution order, admit work, contact a backend, or authorize a live run.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .semantic_contract import SemanticOperator
from .semantic_evidence import ExecutionEvidence
from .semantic_validator import (
    CertificationStatus,
    ReorderStatus,
    ValidationResult,
    certify_reorder,
    validate_evidence,
)


class MSEGQualificationError(ValueError):
    """Qualification input is malformed."""


def _fail(code: str) -> MSEGQualificationError:
    return MSEGQualificationError(code)


class SyntheticDecision(str, Enum):
    GO_OFFLINE_CERTIFIED = "GO_OFFLINE_CERTIFIED"
    STOP_INVALID_EVIDENCE = "STOP_INVALID_EVIDENCE"
    STOP_INCOMPLETE_EVIDENCE = "STOP_INCOMPLETE_EVIDENCE"
    STOP_NO_CERTIFIED_EVIDENCE = "STOP_NO_CERTIFIED_EVIDENCE"
    STOP_NO_REORDER_OPPORTUNITY = "STOP_NO_REORDER_OPPORTUNITY"


class GateDecision(str, Enum):
    GO_OFFLINE_ORACLE_ONLY = "GO_OFFLINE_ORACLE_ONLY"
    STOP_SYNTHETIC_QUALIFICATION = "STOP_SYNTHETIC_QUALIFICATION"
    STOP_REAL_TRACE_INSUFFICIENT_OBSERVABILITY = (
        "STOP_REAL_TRACE_INSUFFICIENT_OBSERVABILITY"
    )


@dataclass(frozen=True, slots=True)
class QualificationCase:
    label: str
    operator: SemanticOperator
    evidence: ExecutionEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label or self.label.strip() != self.label:
            raise _fail("case_label_invalid")
        if not isinstance(self.operator, SemanticOperator):
            raise _fail("case_operator_invalid")
        if not isinstance(self.evidence, ExecutionEvidence):
            raise _fail("case_evidence_invalid")


@dataclass(frozen=True, slots=True)
class SyntheticQualification:
    decision: SyntheticDecision
    status_counts: dict[str, int]
    reorder_counts: dict[str, int]
    case_results: dict[str, ValidationResult]
    reasons: tuple[str, ...]

    @property
    def certified_count(self) -> int:
        return self.status_counts[CertificationStatus.CERTIFIED_PRIVATE.value] + self.status_counts[
            CertificationStatus.CERTIFIED_PUBLISHABLE.value
        ]


@dataclass(frozen=True, slots=True)
class GateResult:
    decision: GateDecision
    reasons: tuple[str, ...]
    synthetic_decision: SyntheticDecision
    live_authorized: bool = False


def _counts(values: list[str], keys: tuple[str, ...]) -> dict[str, int]:
    counts = Counter(values)
    return {key: counts.get(key, 0) for key in keys}


def qualify_synthetic(
    cases: tuple[QualificationCase, ...],
    *,
    reorder_pairs: tuple[tuple[str, str], ...],
) -> SyntheticQualification:
    """Run the pure synthetic contract/opportunity gate."""

    if not isinstance(cases, tuple):
        raise _fail("cases_invalid")
    if not isinstance(reorder_pairs, tuple):
        raise _fail("reorder_pairs_invalid")
    labels = [case.label for case in cases]
    if len(labels) != len(set(labels)):
        raise _fail("case_label_duplicate")
    selected = {case.label: case for case in cases}
    results = {
        label: validate_evidence(case.operator, case.evidence)
        for label, case in selected.items()
    }
    status_counts = _counts(
        [result.status.value for result in results.values()],
        tuple(status.value for status in CertificationStatus),
    )
    reorder_values: list[str] = []
    for left_label, right_label in reorder_pairs:
        if left_label not in selected or right_label not in selected:
            raise _fail("reorder_case_missing")
        left_result = results[left_label]
        right_result = results[right_label]
        if left_result.status not in (
            CertificationStatus.CERTIFIED_PRIVATE,
            CertificationStatus.CERTIFIED_PUBLISHABLE,
        ) or right_result.status not in (
            CertificationStatus.CERTIFIED_PRIVATE,
            CertificationStatus.CERTIFIED_PUBLISHABLE,
        ):
            reorder_values.append(ReorderStatus.UNKNOWN.value)
            continue
        reorder_values.append(
            certify_reorder(
                selected[left_label].operator,
                selected[right_label].operator,
            ).value
        )
    reorder_counts = _counts(
        reorder_values,
        tuple(status.value for status in ReorderStatus),
    )

    reasons: list[str] = []
    invalid_count = status_counts[CertificationStatus.INVALID.value]
    opaque_count = status_counts[CertificationStatus.OPAQUE.value]
    if invalid_count:
        decision = SyntheticDecision.STOP_INVALID_EVIDENCE
        reasons.append("invalid_evidence_present")
    elif opaque_count:
        decision = SyntheticDecision.STOP_INCOMPLETE_EVIDENCE
        reasons.append("opaque_evidence_present")
    elif not results:
        decision = SyntheticDecision.STOP_NO_CERTIFIED_EVIDENCE
        reasons.append("no_cases")
    elif not (
        status_counts[CertificationStatus.CERTIFIED_PRIVATE.value]
        or status_counts[CertificationStatus.CERTIFIED_PUBLISHABLE.value]
    ):
        decision = SyntheticDecision.STOP_NO_CERTIFIED_EVIDENCE
        reasons.append("no_certified_evidence")
    elif reorder_counts[ReorderStatus.CERTIFIED.value] == 0:
        decision = SyntheticDecision.STOP_NO_REORDER_OPPORTUNITY
        reasons.append("no_certified_reorder_opportunity")
    else:
        decision = SyntheticDecision.GO_OFFLINE_CERTIFIED
    return SyntheticQualification(
        decision=decision,
        status_counts=status_counts,
        reorder_counts=reorder_counts,
        case_results=results,
        reasons=tuple(reasons),
    )


def gate_real_trace(
    synthetic: SyntheticQualification,
    observability: Mapping[str, object],
) -> GateResult:
    """Apply the real-trace observability gate after synthetic qualification."""

    if not isinstance(synthetic, SyntheticQualification):
        raise _fail("synthetic_result_invalid")
    if not isinstance(observability, Mapping):
        raise _fail("observability_invalid")
    if synthetic.decision is not SyntheticDecision.GO_OFFLINE_CERTIFIED:
        return GateResult(
            decision=GateDecision.STOP_SYNTHETIC_QUALIFICATION,
            reasons=synthetic.reasons or ("synthetic_gate_failed",),
            synthetic_decision=synthetic.decision,
        )
    if observability.get("mseg_recovered") is not True:
        raw_reasons = observability.get("blocking_reasons", ())
        if not isinstance(raw_reasons, (list, tuple)):
            raise _fail("blocking_reasons_invalid")
        reasons = tuple(
            str(reason) for reason in raw_reasons if isinstance(reason, str) and reason
        )
        if not reasons:
            reasons = ("real_trace_mseg_not_recovered",)
        return GateResult(
            decision=GateDecision.STOP_REAL_TRACE_INSUFFICIENT_OBSERVABILITY,
            reasons=reasons,
            synthetic_decision=synthetic.decision,
        )
    return GateResult(
        decision=GateDecision.GO_OFFLINE_ORACLE_ONLY,
        reasons=(),
        synthetic_decision=synthetic.decision,
    )


__all__ = [
    "GateDecision",
    "GateResult",
    "MSEGQualificationError",
    "QualificationCase",
    "SyntheticDecision",
    "SyntheticQualification",
    "gate_real_trace",
    "qualify_synthetic",
]

