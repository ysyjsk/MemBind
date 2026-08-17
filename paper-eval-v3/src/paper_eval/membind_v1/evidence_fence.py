"""Immutable Native-equivalent previous-episode evidence fences.

The fence is built before compilation and contains only source-log snapshots.
It intentionally cannot carry a Graphiti instance, Neo4j driver, retrieval
callable, or any other mutable graph-state capability.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v1.source_log import SourceLog, SourceRecord


class MemBindV1EvidenceFenceError(ValueError):
    """Native-equivalent evidence selection cannot be established exactly."""


def _fail(code: str) -> MemBindV1EvidenceFenceError:
    return MemBindV1EvidenceFenceError(code)


def _positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _fail(code)
    return value


def _sequence_of_source_sequences(value: object, code: str) -> tuple[int, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    selected = tuple(value)
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in selected):
        raise _fail(code)
    if len(set(selected)) != len(selected):
        raise _fail(code)
    return selected


@dataclass(frozen=True, slots=True)
class EvidenceFence:
    """A source-only snapshot of the Native previous-episode query result."""

    target_source_sequence: int
    target_source_sha256: str
    group_id: str
    source_filter: str
    reference_time_ns: int
    last_n: int
    evidence_records: tuple[SourceRecord, ...]
    evidence_prefix_sha256: str
    selection_mode: str

    @classmethod
    def capture(
        cls,
        source_log: SourceLog,
        *,
        target_source_sequence: int,
        last_n: int,
        explicit_capture_source_sequences: Sequence[int] | None = None,
    ) -> "EvidenceFence":
        """Capture the exact source prefix available to a compile operation.

        The normal path emulates the Native group/source/time/last-N predicate.
        A timestamp tie straddling the last-N cutoff has no stable Native order,
        so it fails closed unless an explicit pre-captured source identity set is
        supplied by the qualified caller.
        """

        if not isinstance(source_log, SourceLog):
            raise _fail("source_log_invalid")
        target = source_log.record(target_source_sequence)
        limit = _positive_int(last_n, "last_n_invalid")
        candidates = [
            record
            for record in source_log.records
            if record.source_sequence < target.source_sequence
            and record.group_id == target.group_id
            and record.source_filter == target.source_filter
            and record.reference_time_ns <= target.reference_time_ns
        ]
        chronological = sorted(
            candidates,
            key=lambda record: (record.reference_time_ns, record.source_sequence),
        )
        selection_mode = "native_equivalent"
        if explicit_capture_source_sequences is None:
            if len(chronological) > limit:
                first_selected = chronological[-limit]
                first_excluded = chronological[-limit - 1]
                if first_selected.reference_time_ns == first_excluded.reference_time_ns:
                    raise _fail("equal_timestamp_cutoff_ambiguous")
            selected = chronological[-limit:]
        else:
            captured_sequences = _sequence_of_source_sequences(
                explicit_capture_source_sequences, "explicit_capture_invalid"
            )
            expected_count = min(limit, len(chronological))
            if len(captured_sequences) != expected_count:
                raise _fail("explicit_capture_count_invalid")
            candidates_by_sequence = {
                record.source_sequence: record for record in chronological
            }
            try:
                selected = [candidates_by_sequence[sequence] for sequence in captured_sequences]
            except KeyError:
                raise _fail("explicit_capture_not_native_eligible") from None
            selected.sort(key=lambda record: (record.reference_time_ns, record.source_sequence))
            selection_mode = "explicit_capture"
        prefix_payload = {
            "evidence_records": [record.inventory_projection() for record in selected],
            "group_id": target.group_id,
            "last_n": limit,
            "reference_time_ns": target.reference_time_ns,
            "source_filter": target.source_filter,
            "target_source_sequence": target.source_sequence,
            "target_source_sha256": target.source_sha256,
        }
        return cls(
            target_source_sequence=target.source_sequence,
            target_source_sha256=target.source_sha256,
            group_id=target.group_id,
            source_filter=target.source_filter,
            reference_time_ns=target.reference_time_ns,
            last_n=limit,
            evidence_records=tuple(selected),
            evidence_prefix_sha256=payload_sha256(prefix_payload),
            selection_mode=selection_mode,
        )

    @property
    def evidence_source_sequences(self) -> tuple[int, ...]:
        return tuple(record.source_sequence for record in self.evidence_records)

    @property
    def evidence_source_sha256s(self) -> tuple[str, ...]:
        return tuple(record.source_sha256 for record in self.evidence_records)


@dataclass(frozen=True, slots=True)
class CompileInput:
    """The complete pure-data capability passed to a MemBind-v1 compiler."""

    source: SourceRecord
    evidence: EvidenceFence

    def __post_init__(self) -> None:
        if self.source.source_sequence != self.evidence.target_source_sequence:
            raise _fail("compile_source_sequence_mismatch")
        if self.source.source_sha256 != self.evidence.target_source_sha256:
            raise _fail("compile_source_identity_mismatch")


def build_compile_input(source: SourceRecord, evidence: EvidenceFence) -> CompileInput:
    """Construct the only data object supplied to compile code."""

    if not isinstance(source, SourceRecord):
        raise _fail("compile_source_invalid")
    if not isinstance(evidence, EvidenceFence):
        raise _fail("compile_evidence_invalid")
    return CompileInput(source=source, evidence=evidence)


__all__ = [
    "CompileInput",
    "EvidenceFence",
    "MemBindV1EvidenceFenceError",
    "build_compile_input",
]
