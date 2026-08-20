"""Durable publication evidence for a Memory Semantic Execution Graph."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum

from .effect_journal import (
    EffectCertification,
    MemoryEffectJournal,
    validate_effect_entry,
)
from .version_token import MemoryVersionToken, validate_version_token


class PublicationError(ValueError):
    """A publication event is malformed or causally unsupported."""


def _fail(code: str) -> PublicationError:
    return PublicationError(code)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise _fail(code)
    return value


def _seq(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


class PublicationCertification(str, Enum):
    CERTIFIED = "CERTIFIED"
    OPAQUE = "OPAQUE"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class PublicationValidationResult:
    status: PublicationCertification
    codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PublicationEvent:
    """The only event a compiler may treat as durable state visibility."""

    event_id: str
    graph_id: str
    stream_id: str
    source_sequence: int
    predecessor_version: MemoryVersionToken | None
    publication_version: MemoryVersionToken
    effect_ids: tuple[str, ...]
    causal_operator_ids: tuple[str, ...]
    transaction_id: str | None
    durable_timestamp_ns: int
    frontier_position: int
    durable: bool

    def __post_init__(self) -> None:
        _text(self.event_id, "publication_event_id_invalid")
        _text(self.graph_id, "publication_graph_invalid")
        _text(self.stream_id, "publication_stream_invalid")
        _seq(self.source_sequence, "publication_source_invalid")
        if self.predecessor_version is not None and not isinstance(
            self.predecessor_version, MemoryVersionToken
        ):
            raise _fail("publication_predecessor_invalid")
        if not isinstance(self.publication_version, MemoryVersionToken):
            raise _fail("publication_version_invalid")
        if self.publication_version.namespace != self.graph_id:
            raise _fail("publication_version_domain_mismatch")
        if self.predecessor_version is not None and self.predecessor_version.namespace != self.graph_id:
            raise _fail("publication_predecessor_domain_mismatch")
        if not isinstance(self.effect_ids, tuple) or not self.effect_ids:
            raise _fail("publication_effect_ids_invalid")
        if len(set(self.effect_ids)) != len(self.effect_ids) or any(
            not isinstance(item, str) or not item for item in self.effect_ids
        ):
            raise _fail("publication_effect_ids_invalid")
        if not isinstance(self.causal_operator_ids, tuple) or not self.causal_operator_ids:
            raise _fail("publication_causal_ids_invalid")
        if len(set(self.causal_operator_ids)) != len(self.causal_operator_ids) or any(
            not isinstance(item, str) or not item for item in self.causal_operator_ids
        ):
            raise _fail("publication_causal_ids_invalid")
        if self.transaction_id is not None:
            _text(self.transaction_id, "publication_transaction_invalid")
        _seq(self.durable_timestamp_ns, "publication_timestamp_invalid")
        _seq(self.frontier_position, "publication_frontier_invalid")
        if not isinstance(self.durable, bool):
            raise _fail("publication_durable_invalid")
        if self.frontier_position > 0 and self.predecessor_version is None:
            raise _fail("publication_predecessor_missing")
        if self.durable and self.transaction_id is None:
            raise _fail("durable_publication_transaction_missing")
        if (
            self.transaction_id is not None
            and self.publication_version.transaction_id != self.transaction_id
        ):
            raise _fail("publication_version_transaction_mismatch")

    @classmethod
    def create(
        cls,
        *,
        graph_id: str,
        stream_id: str,
        source_sequence: int,
        predecessor_version: MemoryVersionToken | None,
        publication_version: MemoryVersionToken,
        effect_ids: tuple[str, ...],
        causal_operator_ids: tuple[str, ...],
        transaction_id: str | None,
        durable_timestamp_ns: int,
        frontier_position: int,
        durable: bool,
    ) -> "PublicationEvent":
        payload = {
            "causal_operator_ids": causal_operator_ids,
            "effect_ids": effect_ids,
            "frontier_position": frontier_position,
            "graph_id": graph_id,
            "predecessor": None if predecessor_version is None else predecessor_version.canonical,
            "publication": publication_version.canonical,
            "source_sequence": source_sequence,
            "stream_id": stream_id,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(
            event_id=f"meg-pub-{digest}",
            graph_id=graph_id,
            stream_id=stream_id,
            source_sequence=source_sequence,
            predecessor_version=predecessor_version,
            publication_version=publication_version,
            effect_ids=effect_ids,
            causal_operator_ids=causal_operator_ids,
            transaction_id=transaction_id,
            durable_timestamp_ns=durable_timestamp_ns,
            frontier_position=frontier_position,
            durable=durable,
        )


def validate_publication_event(
    event: PublicationEvent,
    journal: MemoryEffectJournal,
) -> PublicationValidationResult:
    """Require durable committed effects and explicit causal linkage."""

    if not isinstance(event, PublicationEvent):
        return PublicationValidationResult(PublicationCertification.INVALID, ("publication_invalid",))
    if not isinstance(journal, MemoryEffectJournal):
        return PublicationValidationResult(PublicationCertification.INVALID, ("effect_journal_invalid",))
    invalid: list[str] = []
    opaque: list[str] = []
    version_result = validate_version_token(
        event.publication_version,
        predecessor=event.predecessor_version,
    )
    if version_result.status is not version_result.status.CERTIFIED:
        (opaque if version_result.status.value == "OPAQUE" else invalid).extend(version_result.codes)
    for effect_id in event.effect_ids:
        try:
            entry = journal.by_id(effect_id)
        except ValueError:
            invalid.append("publication_effect_missing")
            continue
        effect_result = validate_effect_entry(entry)
        if effect_result.status is EffectCertification.INVALID:
            invalid.extend(effect_result.codes)
        elif effect_result.status is EffectCertification.OPAQUE:
            opaque.extend(effect_result.codes)
        if entry.graph_id != event.graph_id or entry.source_sequence != event.source_sequence:
            invalid.append("publication_effect_domain_mismatch")
        if entry.operator_instance_id not in event.causal_operator_ids:
            invalid.append("causal_operator_mismatch")
        if entry.effect_scope.is_unknown:
            opaque.append("effect_scope_unknown")
        if not entry.mutation_committed or not entry.durable:
            invalid.append("publication_without_committed_effect")
        if not entry.publication_visible:
            invalid.append("effect_not_publication_visible")
        if entry.state_version_after != event.publication_version:
            invalid.append("publication_version_effect_mismatch")
        if event.predecessor_version is not None and entry.state_version_before != event.predecessor_version:
            invalid.append("publication_predecessor_effect_mismatch")
        if event.transaction_id is not None and entry.transaction_id != event.transaction_id:
            invalid.append("publication_transaction_mismatch")
        if (
            entry.mutation_committed_ns is not None
            and event.durable_timestamp_ns < entry.mutation_committed_ns
        ):
            invalid.append("publication_before_effect_commit")
    if not event.durable:
        opaque.append("publication_not_durable")
    if invalid:
        return PublicationValidationResult(PublicationCertification.INVALID, tuple(dict.fromkeys(invalid)))
    if opaque:
        return PublicationValidationResult(PublicationCertification.OPAQUE, tuple(dict.fromkeys(opaque)))
    return PublicationValidationResult(PublicationCertification.CERTIFIED)


class PublicationJournal:
    """Ordered durable publication ledger used by the offline compiler."""

    def __init__(self) -> None:
        self._events: list[PublicationEvent] = []
        self._ids: set[str] = set()

    @property
    def events(self) -> tuple[PublicationEvent, ...]:
        return tuple(self._events)

    def append(self, event: PublicationEvent, *, journal: MemoryEffectJournal) -> None:
        result = validate_publication_event(event, journal)
        if result.status is not PublicationCertification.CERTIFIED:
            raise _fail("publication_not_certified")
        if event.event_id in self._ids:
            raise _fail("publication_duplicate")
        if self._events:
            previous = self._events[-1]
            if event.graph_id != previous.graph_id or event.stream_id != previous.stream_id:
                raise _fail("publication_stream_mismatch")
            if event.frontier_position <= previous.frontier_position:
                raise _fail("publication_frontier_not_monotonic")
            if event.source_sequence <= previous.source_sequence:
                raise _fail("publication_source_not_monotonic")
            if event.predecessor_version != previous.publication_version:
                raise _fail("publication_causal_gap")
        self._ids.add(event.event_id)
        self._events.append(event)


__all__ = [
    "PublicationCertification",
    "PublicationError",
    "PublicationEvent",
    "PublicationJournal",
    "PublicationValidationResult",
    "validate_publication_event",
]
