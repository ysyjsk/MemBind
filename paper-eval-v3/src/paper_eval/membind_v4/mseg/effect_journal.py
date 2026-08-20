"""Minimal backend-neutral memory effect journal.

The journal is the L2 proof boundary between a mutation intent and a durable
state change.  It may be populated by an adapter/transaction wrapper, but the
records and validators here are pure and can be exercised with synthetic
fixtures.  Unknown scopes are retained as evidence and classified OPAQUE;
they are never guessed into a set of entities or edges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .semantic_contract import EffectKind
from .version_token import MemoryVersionToken


class EffectJournalError(ValueError):
    """An effect journal record violates its causal contract."""


def _fail(code: str) -> EffectJournalError:
    return EffectJournalError(code)


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise _fail(code)
    return value


def _seq(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _hash(value: object, code: str) -> str:
    selected = _text(value, code).lower()
    if _HEX64.fullmatch(selected) is None:
        raise _fail(code)
    return selected


class EffectCertification(str, Enum):
    CERTIFIED = "CERTIFIED"
    OPAQUE = "OPAQUE"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class EffectValidationResult:
    status: EffectCertification
    codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EffectScope:
    """Typed memory identifiers, or an explicit unknown sentinel."""

    namespace: str
    entity_ids: frozenset[str] = frozenset()
    edge_ids: frozenset[str] = frozenset()
    episode_ids: frozenset[str] = frozenset()
    is_unknown: bool = False

    def __post_init__(self) -> None:
        _text(self.namespace, "effect_namespace_invalid")
        if not isinstance(self.entity_ids, frozenset):
            raise _fail("entity_scope_invalid")
        if not isinstance(self.edge_ids, frozenset):
            raise _fail("edge_scope_invalid")
        if not isinstance(self.episode_ids, frozenset):
            raise _fail("episode_scope_invalid")
        for selected, code in (
            (self.entity_ids, "entity_scope_invalid"),
            (self.edge_ids, "edge_scope_invalid"),
            (self.episode_ids, "episode_scope_invalid"),
        ):
            if any(not isinstance(item, str) or not item or item.strip() != item for item in selected):
                raise _fail(code)
        if not isinstance(self.is_unknown, bool):
            raise _fail("effect_scope_unknown_flag_invalid")
        if self.is_unknown and self.identifiers:
            raise _fail("unknown_scope_has_identifiers")

    @property
    def identifiers(self) -> frozenset[str]:
        return frozenset((*self.entity_ids, *self.edge_ids, *self.episode_ids))

    @classmethod
    def entities(cls, namespace: str, ids: set[str] | frozenset[str]) -> "EffectScope":
        return cls(namespace=_text(namespace, "effect_namespace_invalid"), entity_ids=frozenset(ids))

    @classmethod
    def edges(cls, namespace: str, ids: set[str] | frozenset[str]) -> "EffectScope":
        return cls(namespace=_text(namespace, "effect_namespace_invalid"), edge_ids=frozenset(ids))

    @classmethod
    def episodes(cls, namespace: str, ids: set[str] | frozenset[str]) -> "EffectScope":
        return cls(namespace=_text(namespace, "effect_namespace_invalid"), episode_ids=frozenset(ids))

    @classmethod
    def mixed(
        cls,
        namespace: str,
        *,
        entity_ids: set[str] | frozenset[str] = frozenset(),
        edge_ids: set[str] | frozenset[str] = frozenset(),
        episode_ids: set[str] | frozenset[str] = frozenset(),
    ) -> "EffectScope":
        return cls(
            namespace=_text(namespace, "effect_namespace_invalid"),
            entity_ids=frozenset(entity_ids),
            edge_ids=frozenset(edge_ids),
            episode_ids=frozenset(episode_ids),
        )

    @classmethod
    def unknown(cls, namespace: str) -> "EffectScope":
        return cls(namespace=_text(namespace, "effect_namespace_invalid"), is_unknown=True)


@dataclass(frozen=True, slots=True)
class MemoryEffectJournalEntry:
    """One mutation/effect observation at the persistence boundary."""

    effect_id: str
    graph_id: str
    source_sequence: int
    operator_instance_id: str
    state_version_before: MemoryVersionToken | None
    effect_type: EffectKind
    effect_scope: EffectScope
    mutation_started_ns: int
    mutation_committed_ns: int | None
    mutation_committed: bool
    publication_visible: bool
    state_version_after: MemoryVersionToken | None
    transaction_id: str | None
    evidence_hash: str
    durable: bool

    def __post_init__(self) -> None:
        _text(self.effect_id, "effect_id_invalid")
        _text(self.graph_id, "graph_id_invalid")
        _seq(self.source_sequence, "source_sequence_invalid")
        _text(self.operator_instance_id, "effect_operator_id_invalid")
        if self.state_version_before is not None and not isinstance(
            self.state_version_before, MemoryVersionToken
        ):
            raise _fail("state_version_before_invalid")
        if not isinstance(self.effect_type, EffectKind):
            raise _fail("effect_type_invalid")
        if not isinstance(self.effect_scope, EffectScope):
            raise _fail("effect_scope_invalid")
        if self.effect_scope.namespace != self.graph_id:
            raise _fail("effect_namespace_graph_mismatch")
        started = _seq(self.mutation_started_ns, "mutation_started_invalid")
        committed_ns = (
            None
            if self.mutation_committed_ns is None
            else _seq(self.mutation_committed_ns, "mutation_committed_time_invalid")
        )
        if not isinstance(self.mutation_committed, bool):
            raise _fail("mutation_committed_invalid")
        if not isinstance(self.publication_visible, bool):
            raise _fail("publication_visible_invalid")
        if self.state_version_after is not None and not isinstance(
            self.state_version_after, MemoryVersionToken
        ):
            raise _fail("state_version_after_invalid")
        if self.transaction_id is not None:
            _text(self.transaction_id, "transaction_id_invalid")
        _hash(self.evidence_hash, "effect_evidence_hash_invalid")
        if not isinstance(self.durable, bool):
            raise _fail("effect_durable_invalid")
        for token, code in (
            (self.state_version_before, "state_version_before_domain_mismatch"),
            (self.state_version_after, "state_version_after_domain_mismatch"),
        ):
            if token is not None and token.namespace != self.graph_id:
                raise _fail(code)
        if self.effect_type is EffectKind.NONE and self.effect_scope.identifiers:
            raise _fail("noop_effect_has_scope")
        if self.publication_visible and not self.mutation_committed:
            raise _fail("publication_without_commit")
        if self.publication_visible and not self.durable:
            raise _fail("publication_without_durable_effect")
        if self.mutation_committed:
            if committed_ns is None:
                raise _fail("committed_time_missing")
            if committed_ns < started:
                raise _fail("commit_before_mutation_start")
            if self.transaction_id is None:
                raise _fail("committed_transaction_missing")
            if self.state_version_after is None:
                raise _fail("committed_after_version_missing")
            if self.state_version_after.transaction_id != self.transaction_id:
                raise _fail("after_version_transaction_mismatch")
            if not self.durable:
                raise _fail("committed_effect_not_durable")
            if (
                self.state_version_before is not None
                and self.state_version_after.counter <= self.state_version_before.counter
            ):
                raise _fail("after_version_not_newer")
        else:
            if committed_ns is not None:
                raise _fail("uncommitted_has_commit_time")
            if self.state_version_after is not None:
                raise _fail("uncommitted_after_version_present")
            if self.durable:
                raise _fail("uncommitted_effect_durable")


def validate_effect_entry(entry: MemoryEffectJournalEntry) -> EffectValidationResult:
    """Classify an already-shaped entry without filling in missing evidence."""

    if not isinstance(entry, MemoryEffectJournalEntry):
        return EffectValidationResult(EffectCertification.INVALID, ("effect_entry_invalid",))
    opaque: list[str] = []
    if entry.effect_scope.is_unknown:
        opaque.append("effect_scope_unknown")
    elif entry.effect_type is not EffectKind.NONE and not entry.effect_scope.identifiers:
        opaque.append("effect_scope_namespace_only")
    if entry.mutation_committed and entry.state_version_after is None:
        opaque.append("after_version_not_observable")
    if entry.publication_visible and not entry.durable:
        opaque.append("publication_not_durable")
    if opaque:
        return EffectValidationResult(EffectCertification.OPAQUE, tuple(opaque))
    return EffectValidationResult(EffectCertification.CERTIFIED)


class MemoryEffectJournal:
    """Append-only in-memory journal used by offline compiler fixtures."""

    def __init__(self) -> None:
        self._entries: list[MemoryEffectJournalEntry] = []
        self._ids: set[str] = set()

    def append(self, entry: MemoryEffectJournalEntry) -> None:
        if not isinstance(entry, MemoryEffectJournalEntry):
            raise _fail("effect_entry_invalid")
        if entry.effect_id in self._ids:
            raise _fail("effect_id_duplicate")
        self._ids.add(entry.effect_id)
        self._entries.append(entry)

    @property
    def entries(self) -> tuple[MemoryEffectJournalEntry, ...]:
        return tuple(self._entries)

    def for_operator(self, operator_instance_id: str) -> tuple[MemoryEffectJournalEntry, ...]:
        _text(operator_instance_id, "effect_operator_id_invalid")
        return tuple(item for item in self._entries if item.operator_instance_id == operator_instance_id)

    def by_id(self, effect_id: str) -> MemoryEffectJournalEntry:
        _text(effect_id, "effect_id_invalid")
        for entry in self._entries:
            if entry.effect_id == effect_id:
                return entry
        raise _fail("effect_id_missing")


__all__ = [
    "EffectCertification",
    "EffectJournalError",
    "EffectScope",
    "EffectValidationResult",
    "MemoryEffectJournal",
    "MemoryEffectJournalEntry",
    "validate_effect_entry",
]
