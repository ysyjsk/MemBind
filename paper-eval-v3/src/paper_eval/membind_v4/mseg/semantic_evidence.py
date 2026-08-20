"""Runtime evidence records for the design-only MSEG validator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .semantic_contract import EffectKind


class MSEGEvidenceError(ValueError):
    """An evidence record is malformed."""


def _fail(code: str) -> MSEGEvidenceError:
    return MSEGEvidenceError(code)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise _fail(code)
    return value


def _optional_text(value: object, code: str) -> str | None:
    return None if value is None else _text(value, code)


def _scope(value: object, code: str) -> frozenset[str]:
    if not isinstance(value, (set, frozenset)):
        raise _fail(code)
    selected = frozenset(value)
    if any(
        not isinstance(item, str) or not item or item.strip() != item
        for item in selected
    ):
        raise _fail(code)
    return selected


def _optional_scope(value: object, code: str) -> frozenset[str] | None:
    return None if value is None else _scope(value, code)


def _bool(value: object, code: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(code)
    return value


def _timestamp(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


class CertificationLevel(str, Enum):
    DECLARED = "DECLARED"
    OBSERVED = "OBSERVED"
    VALIDATED = "VALIDATED"


@dataclass(frozen=True, slots=True)
class AdapterProvenance:
    adapter_id: str
    backend_name: str
    backend_version: str
    contract_id: str
    schema_fingerprint: str
    source_fingerprint: str
    level: CertificationLevel

    def __post_init__(self) -> None:
        for value, code in (
            (self.adapter_id, "adapter_id_invalid"),
            (self.backend_name, "backend_name_invalid"),
            (self.backend_version, "backend_version_invalid"),
            (self.contract_id, "provenance_contract_id_invalid"),
            (self.schema_fingerprint, "schema_fingerprint_invalid"),
            (self.source_fingerprint, "source_fingerprint_invalid"),
        ):
            _text(value, code)
        if not isinstance(self.level, CertificationLevel):
            raise _fail("provenance_level_invalid")


@dataclass(frozen=True, slots=True)
class EffectJournalEntry:
    effect_id: str
    operator_instance_id: str
    kind: EffectKind
    namespace: str
    scope: frozenset[str] | None
    committed: bool
    transaction_id: str | None
    timestamp_ns: int
    durable: bool

    def __post_init__(self) -> None:
        _text(self.effect_id, "effect_id_invalid")
        _text(self.operator_instance_id, "effect_operator_id_invalid")
        if not isinstance(self.kind, EffectKind):
            raise _fail("effect_kind_invalid")
        _text(self.namespace, "effect_namespace_invalid")
        if self.kind is EffectKind.NONE:
            if self.scope != frozenset():
                raise _fail("none_effect_scope_invalid")
        elif self.scope is not None:
            _scope(self.scope, "effect_scope_invalid")
        _bool(self.committed, "effect_committed_invalid")
        _optional_text(self.transaction_id, "transaction_id_invalid")
        _timestamp(self.timestamp_ns, "effect_timestamp_invalid")
        _bool(self.durable, "effect_durable_invalid")
        if self.durable and not self.committed:
            raise _fail("durable_effect_not_committed")


@dataclass(frozen=True, slots=True)
class PublicationEvidence:
    publication_id: str
    operator_instance_id: str
    predecessor_version: str | None
    published_version: str
    durable: bool
    timestamp_ns: int
    frontier_position: int

    def __post_init__(self) -> None:
        _text(self.publication_id, "publication_id_invalid")
        _text(self.operator_instance_id, "publication_operator_id_invalid")
        if self.predecessor_version is not None:
            _text(self.predecessor_version, "publication_predecessor_invalid")
        _text(self.published_version, "publication_version_invalid")
        _bool(self.durable, "publication_durable_invalid")
        _timestamp(self.timestamp_ns, "publication_timestamp_invalid")
        _timestamp(self.frontier_position, "publication_frontier_invalid")
        if self.predecessor_version is None:
            raise _fail("publication_predecessor_missing")


@dataclass(frozen=True, slots=True)
class ExecutionEvidence:
    instance_id: str
    semantic_identity: str | None
    state_version: str | None
    read_scope: frozenset[str] | None
    provenance: AdapterProvenance | None
    effect_journal: tuple[EffectJournalEntry, ...]
    publication: PublicationEvidence | None
    terminal: bool
    child_identity_complete: bool
    hidden_effects_possible: bool

    def __post_init__(self) -> None:
        _text(self.instance_id, "evidence_instance_id_invalid")
        _optional_text(self.semantic_identity, "evidence_semantic_identity_invalid")
        _optional_text(self.state_version, "evidence_state_version_invalid")
        _optional_scope(self.read_scope, "evidence_read_scope_invalid")
        if self.provenance is not None and not isinstance(
            self.provenance, AdapterProvenance
        ):
            raise _fail("evidence_provenance_invalid")
        if not isinstance(self.effect_journal, tuple):
            raise _fail("effect_journal_invalid")
        if any(not isinstance(entry, EffectJournalEntry) for entry in self.effect_journal):
            raise _fail("effect_journal_entry_invalid")
        if self.publication is not None and not isinstance(
            self.publication, PublicationEvidence
        ):
            raise _fail("publication_evidence_invalid")
        _bool(self.terminal, "evidence_terminal_invalid")
        _bool(self.child_identity_complete, "child_identity_complete_invalid")
        _bool(self.hidden_effects_possible, "hidden_effects_possible_invalid")


__all__ = [
    "AdapterProvenance",
    "CertificationLevel",
    "EffectJournalEntry",
    "ExecutionEvidence",
    "MSEGEvidenceError",
    "PublicationEvidence",
]
