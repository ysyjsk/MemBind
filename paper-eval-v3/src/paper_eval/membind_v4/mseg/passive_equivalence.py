"""Pure passive-equivalence records for MEG instrumentation qualification.

The semantic adapter is observational until a separately reviewed runtime
integration exists.  These records compare a baseline execution with an
instrumented execution without invoking either backend.  A certificate passes
only when every observable covered by the v0 protocol is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PassiveEquivalenceError(ValueError):
    """A passive-execution snapshot is malformed."""


def _fail(code: str) -> PassiveEquivalenceError:
    return PassiveEquivalenceError(code)


def _count(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _hash(value: object, code: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _fail(code)
    return value


def _hashes(values: object, code: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise _fail(code)
    return tuple(_hash(value, code) for value in values)


def _texts(values: object, code: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise _fail(code)
    for value in values:
        if not isinstance(value, str) or not value or value.strip() != value:
            raise _fail(code)
    return values


def _ordinals(values: object, code: str) -> tuple[int, ...]:
    if not isinstance(values, tuple):
        raise _fail(code)
    return tuple(_count(value, code) for value in values)


@dataclass(frozen=True, slots=True)
class PassiveExecutionSnapshot:
    """Semantic observables captured outside the memory algorithm."""

    request_count: int
    prompt_hashes: tuple[str, ...]
    model_ids: tuple[str, ...]
    response_hashes: tuple[str, ...]
    db_query_hashes: tuple[str, ...]
    published_graph_hash: str
    publication_order: tuple[int, ...]
    source_sequences: tuple[int, ...]
    llm_call_count: int
    embedding_call_count: int
    mutation_count: int
    source_exactly_once: bool

    def __post_init__(self) -> None:
        _count(self.request_count, "request_count_invalid")
        _hashes(self.prompt_hashes, "prompt_hash_invalid")
        _texts(self.model_ids, "model_id_invalid")
        _hashes(self.response_hashes, "response_hash_invalid")
        _hashes(self.db_query_hashes, "db_query_hash_invalid")
        _hash(self.published_graph_hash, "published_graph_hash_invalid")
        publications = _ordinals(self.publication_order, "publication_order_invalid")
        sources = _ordinals(self.source_sequences, "source_sequence_invalid")
        _count(self.llm_call_count, "llm_call_count_invalid")
        _count(self.embedding_call_count, "embedding_call_count_invalid")
        _count(self.mutation_count, "mutation_count_invalid")
        if not isinstance(self.source_exactly_once, bool):
            raise _fail("source_exactly_once_invalid")
        if len(publications) != len(set(publications)):
            raise _fail("duplicate_publication_position")
        if len(sources) != len(set(sources)) and self.source_exactly_once:
            raise _fail("source_exactly_once_contradiction")


class PassiveEquivalenceStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class PassiveEquivalenceCertificate:
    """Offline result; PASS is necessary, never sufficient, for live use."""

    certificate_type: str
    status: PassiveEquivalenceStatus
    violations: tuple[str, ...]
    baseline: PassiveExecutionSnapshot
    instrumented: PassiveExecutionSnapshot

    @property
    def passed(self) -> bool:
        return self.status is PassiveEquivalenceStatus.PASS


def compare_passive_execution(
    baseline: PassiveExecutionSnapshot,
    instrumented: PassiveExecutionSnapshot,
) -> PassiveEquivalenceCertificate:
    """Compare all v0 passive invariants with stable failure codes."""

    if not isinstance(baseline, PassiveExecutionSnapshot):
        raise _fail("baseline_snapshot_invalid")
    if not isinstance(instrumented, PassiveExecutionSnapshot):
        raise _fail("instrumented_snapshot_invalid")

    violations: list[str] = []

    for label, snapshot in (("baseline", baseline), ("instrumented", instrumented)):
        if not (
            len(snapshot.prompt_hashes)
            == len(snapshot.model_ids)
            == len(snapshot.response_hashes)
            == snapshot.request_count
        ):
            violations.append(f"{label}_request_evidence_count_mismatch")

    def changed(field: str, code: str) -> None:
        if getattr(baseline, field) != getattr(instrumented, field):
            violations.append(code)

    changed("request_count", "request_count_changed")
    changed("prompt_hashes", "prompt_hash_changed")
    changed("model_ids", "model_changed")
    changed("response_hashes", "response_changed")
    changed("db_query_hashes", "db_query_behavior_changed")
    changed("published_graph_hash", "published_graph_changed")
    changed("publication_order", "publication_order_changed")
    changed("source_sequences", "source_sequence_changed")
    changed("source_exactly_once", "source_exactly_once_changed")
    changed("mutation_count", "mutation_count_changed")

    if instrumented.llm_call_count > baseline.llm_call_count:
        violations.append("extra_llm_call")
    elif instrumented.llm_call_count < baseline.llm_call_count:
        violations.append("llm_call_count_changed")
    if instrumented.embedding_call_count > baseline.embedding_call_count:
        violations.append("extra_embedding_call")
    elif instrumented.embedding_call_count < baseline.embedding_call_count:
        violations.append("embedding_call_count_changed")

    stable_violations = tuple(dict.fromkeys(violations))
    status = (
        PassiveEquivalenceStatus.PASS
        if not stable_violations
        else PassiveEquivalenceStatus.FAIL
    )
    return PassiveEquivalenceCertificate(
        certificate_type="PASSIVE_EQUIVALENCE_CERTIFICATE",
        status=status,
        violations=stable_violations,
        baseline=baseline,
        instrumented=instrumented,
    )


__all__ = [
    "PassiveEquivalenceCertificate",
    "PassiveEquivalenceError",
    "PassiveEquivalenceStatus",
    "PassiveExecutionSnapshot",
    "compare_passive_execution",
]
