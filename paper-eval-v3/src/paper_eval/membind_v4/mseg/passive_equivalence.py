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


@dataclass(frozen=True, slots=True)
class InstrumentationExecutionSnapshot:
    """Observables required for shadow-instrumentation equivalence.

    Response hashes are deliberately absent.  The qualification checks that
    instrumentation preserves the exact request/model/schema/query/effect
    envelope without requiring a live provider to return byte-identical text.
    """

    request_count: int
    prompt_hashes: tuple[str, ...]
    model_schema_hashes: tuple[str, ...]
    db_query_semantics_hashes: tuple[str, ...]
    persistent_mutation_hashes: tuple[str, ...]
    source_sequences: tuple[int, ...]
    publication_order: tuple[int, ...]
    llm_call_count: int
    shadow_llm_call_count: int
    shadow_persistent_write_count: int
    publication_modification_count: int

    def __post_init__(self) -> None:
        _count(self.request_count, "instrumentation_request_count_invalid")
        _hashes(self.prompt_hashes, "instrumentation_prompt_hash_invalid")
        _hashes(
            self.model_schema_hashes,
            "instrumentation_model_schema_hash_invalid",
        )
        _hashes(
            self.db_query_semantics_hashes,
            "instrumentation_db_query_hash_invalid",
        )
        _hashes(
            self.persistent_mutation_hashes,
            "instrumentation_mutation_hash_invalid",
        )
        _ordinals(self.source_sequences, "instrumentation_source_order_invalid")
        _ordinals(
            self.publication_order,
            "instrumentation_publication_order_invalid",
        )
        _count(self.llm_call_count, "instrumentation_llm_count_invalid")
        _count(self.shadow_llm_call_count, "shadow_llm_count_invalid")
        _count(self.shadow_persistent_write_count, "shadow_write_count_invalid")
        _count(
            self.publication_modification_count,
            "publication_modification_count_invalid",
        )


@dataclass(frozen=True, slots=True)
class InstrumentationEquivalenceCertificate:
    certificate_type: str
    status: PassiveEquivalenceStatus
    violations: tuple[str, ...]
    compared_fields: tuple[str, ...]
    baseline: InstrumentationExecutionSnapshot
    instrumented: InstrumentationExecutionSnapshot

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


def compare_instrumentation_execution(
    baseline: InstrumentationExecutionSnapshot,
    instrumented: InstrumentationExecutionSnapshot,
) -> InstrumentationEquivalenceCertificate:
    """Qualify read-only instrumentation without comparing provider outputs."""

    if not isinstance(baseline, InstrumentationExecutionSnapshot):
        raise _fail("instrumentation_baseline_snapshot_invalid")
    if not isinstance(instrumented, InstrumentationExecutionSnapshot):
        raise _fail("instrumentation_candidate_snapshot_invalid")
    compared_fields = (
        "request_count",
        "prompt_hashes",
        "model_schema_hashes",
        "db_query_semantics_hashes",
        "persistent_mutation_hashes",
        "source_sequences",
        "publication_order",
        "llm_call_count",
        "shadow_llm_call_count",
        "shadow_persistent_write_count",
        "publication_modification_count",
    )
    violations: list[str] = []
    for label, snapshot in (("baseline", baseline), ("instrumented", instrumented)):
        if len(snapshot.prompt_hashes) != snapshot.request_count:
            violations.append(f"{label}_prompt_evidence_count_mismatch")
        if len(snapshot.model_schema_hashes) != snapshot.request_count:
            violations.append(f"{label}_model_schema_evidence_count_mismatch")

    for field, code in (
        ("request_count", "request_count_changed"),
        ("prompt_hashes", "prompt_hash_changed"),
        ("model_schema_hashes", "model_schema_changed"),
        ("db_query_semantics_hashes", "db_query_semantics_changed"),
        ("persistent_mutation_hashes", "persistent_mutation_changed"),
        ("source_sequences", "source_order_changed"),
        ("publication_order", "publication_order_changed"),
        ("llm_call_count", "llm_call_count_changed"),
    ):
        if getattr(baseline, field) != getattr(instrumented, field):
            violations.append(code)
    if instrumented.shadow_llm_call_count != 0:
        violations.append("shadow_llm_call_detected")
    if instrumented.shadow_persistent_write_count != 0:
        violations.append("shadow_write_detected")
    if instrumented.publication_modification_count != 0:
        violations.append("publication_modification_detected")

    stable_violations = tuple(dict.fromkeys(violations))
    status = (
        PassiveEquivalenceStatus.PASS
        if not stable_violations
        else PassiveEquivalenceStatus.FAIL
    )
    return InstrumentationEquivalenceCertificate(
        certificate_type="MEG_INSTRUMENTATION_EQUIVALENCE_CERTIFICATE",
        status=status,
        violations=stable_violations,
        compared_fields=compared_fields,
        baseline=baseline,
        instrumented=instrumented,
    )


__all__ = [
    "InstrumentationEquivalenceCertificate",
    "InstrumentationExecutionSnapshot",
    "PassiveEquivalenceCertificate",
    "PassiveEquivalenceError",
    "PassiveEquivalenceStatus",
    "PassiveExecutionSnapshot",
    "compare_instrumentation_execution",
    "compare_passive_execution",
]
