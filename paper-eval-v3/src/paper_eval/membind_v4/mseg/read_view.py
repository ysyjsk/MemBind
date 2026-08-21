"""Exact semantic read views guarded by persistent mutation epochs.

The semantic digest covers the ordered, mutable state-derived inputs consumed
by a deterministic decision or LLM request.  Logical version and mutation
epoch evidence live in the separate provenance hash.  This distinction is
intentional: exact rematerialization at a later published version may be a
validation HIT only when its decision inputs are byte-identical, while the
capture still retains proof of where and when each view was observed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from .mutation_epoch import MutationEpochToken, StateMutationEpoch
from .version_token import MemoryVersionToken


class SemanticReadViewError(ValueError):
    """A read-view input is incomplete, mutable, or non-canonical."""


def _fail(code: str) -> SemanticReadViewError:
    return SemanticReadViewError(code)


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise _fail(code)
    return value


def _hash(value: object, code: str) -> str:
    selected = _text(value, code).lower()
    if _HEX64.fullmatch(selected) is None:
        raise _fail(code)
    return selected


def _sequence(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _canonical_value(value: object, code: str) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _fail(code)
        return value
    if isinstance(value, Mapping):
        selected: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or key.strip() != key:
                raise _fail(code)
            selected[key] = _canonical_value(item, code)
        return {key: selected[key] for key in sorted(selected)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_value(item, code) for item in value]
    raise _fail(code)


def _canonical_json(value: object, code: str) -> str:
    canonical = _canonical_value(value, code)
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"))


def _sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class ReadKind(str, Enum):
    NODE_CANDIDATE = "NODE_CANDIDATE"
    NODE_RESOLUTION = "NODE_RESOLUTION"
    EDGE_CANDIDATE = "EDGE_CANDIDATE"
    EDGE_RESOLUTION = "EDGE_RESOLUTION"
    ATTRIBUTE = "ATTRIBUTE"
    TIMESTAMP = "TIMESTAMP"
    SUMMARY = "SUMMARY"


class ReadViewStatus(str, Enum):
    STABLE_READVIEW = "STABLE_READVIEW"
    INVALID_UNSTABLE_READ = "INVALID_UNSTABLE_READ"
    OPAQUE = "OPAQUE"


@dataclass(frozen=True, slots=True)
class CandidateSemanticRecord:
    """One candidate in exact prompt/decision order."""

    candidate_id: str
    semantic_fields_json: str
    order_evidence_json: str | None = None

    def __post_init__(self) -> None:
        _text(self.candidate_id, "candidate_id_invalid")
        _text(self.semantic_fields_json, "candidate_semantic_fields_invalid")
        try:
            semantic_fields = json.loads(self.semantic_fields_json)
        except (TypeError, ValueError):
            raise _fail("candidate_semantic_fields_invalid") from None
        if not isinstance(semantic_fields, dict) or not semantic_fields:
            raise _fail("candidate_semantic_fields_empty")
        if _canonical_json(semantic_fields, "candidate_semantic_fields_invalid") != self.semantic_fields_json:
            raise _fail("candidate_semantic_fields_not_canonical")
        if self.order_evidence_json is not None:
            try:
                evidence = json.loads(self.order_evidence_json)
            except (TypeError, ValueError):
                raise _fail("candidate_order_evidence_invalid") from None
            if not isinstance(evidence, dict) or not evidence:
                raise _fail("candidate_order_evidence_invalid")
            if _canonical_json(evidence, "candidate_order_evidence_invalid") != self.order_evidence_json:
                raise _fail("candidate_order_evidence_not_canonical")

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        semantic_fields: Mapping[str, object],
        order_evidence: Mapping[str, object] | None = None,
    ) -> "CandidateSemanticRecord":
        if not isinstance(semantic_fields, Mapping):
            raise _fail("candidate_semantic_fields_invalid")
        if order_evidence is not None and not isinstance(order_evidence, Mapping):
            raise _fail("candidate_order_evidence_invalid")
        return cls(
            candidate_id=_text(candidate_id, "candidate_id_invalid"),
            semantic_fields_json=_canonical_json(
                dict(semantic_fields), "candidate_semantic_fields_invalid"
            ),
            order_evidence_json=(
                None
                if order_evidence is None
                else _canonical_json(
                    dict(order_evidence), "candidate_order_evidence_invalid"
                )
            ),
        )

    def semantic_document(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "semantic_fields": json.loads(self.semantic_fields_json),
            "order_evidence": (
                None
                if self.order_evidence_json is None
                else json.loads(self.order_evidence_json)
            ),
        }


@dataclass(frozen=True, slots=True)
class ReadMaterialization:
    """Pure output of all state-derived reads for one semantic operator."""

    query_identity: str
    search_configuration_hash: str
    candidates: tuple[CandidateSemanticRecord, ...]
    mutable_context_fragment_hash: str
    source_provenance_hash: str
    unknown_state_fields: tuple[str, ...] = ()
    irrelevant_metadata_json: str = "{}"
    excluded_metadata_reasons: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _text(self.query_identity, "query_identity_invalid")
        _hash(self.search_configuration_hash, "search_configuration_hash_invalid")
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(item, CandidateSemanticRecord) for item in self.candidates
        ):
            raise _fail("candidate_records_invalid")
        _hash(self.mutable_context_fragment_hash, "context_fragment_hash_invalid")
        _hash(self.source_provenance_hash, "source_provenance_hash_invalid")
        if not isinstance(self.unknown_state_fields, tuple):
            raise _fail("unknown_state_fields_invalid")
        for field in self.unknown_state_fields:
            _text(field, "unknown_state_field_invalid")
        if len(self.unknown_state_fields) != len(set(self.unknown_state_fields)):
            raise _fail("duplicate_unknown_state_field")
        try:
            irrelevant = json.loads(self.irrelevant_metadata_json)
        except (TypeError, ValueError):
            raise _fail("irrelevant_metadata_invalid") from None
        if not isinstance(irrelevant, dict):
            raise _fail("irrelevant_metadata_invalid")
        if _canonical_json(irrelevant, "irrelevant_metadata_invalid") != self.irrelevant_metadata_json:
            raise _fail("irrelevant_metadata_not_canonical")
        reasons = dict(self.excluded_metadata_reasons)
        if len(reasons) != len(self.excluded_metadata_reasons):
            raise _fail("duplicate_metadata_exclusion_reason")
        for field, reason in self.excluded_metadata_reasons:
            _text(field, "metadata_exclusion_field_invalid")
            _text(reason, "metadata_exclusion_reason_invalid")
        if set(irrelevant) != set(reasons):
            raise _fail("irrelevant_metadata_reason_mismatch")

    @classmethod
    def create(
        cls,
        *,
        query_identity: str,
        search_configuration_hash: str,
        candidates: tuple[CandidateSemanticRecord, ...],
        mutable_context_fragment_hash: str,
        provenance_hash: str,
        unknown_state_fields: tuple[str, ...] = (),
        irrelevant_metadata: Mapping[str, object] | None = None,
        excluded_metadata_reasons: Mapping[str, str] | None = None,
    ) -> "ReadMaterialization":
        irrelevant = {} if irrelevant_metadata is None else dict(irrelevant_metadata)
        reasons = (
            {} if excluded_metadata_reasons is None else dict(excluded_metadata_reasons)
        )
        return cls(
            query_identity=query_identity,
            search_configuration_hash=search_configuration_hash,
            candidates=candidates,
            mutable_context_fragment_hash=mutable_context_fragment_hash,
            source_provenance_hash=provenance_hash,
            unknown_state_fields=tuple(unknown_state_fields),
            irrelevant_metadata_json=_canonical_json(
                irrelevant, "irrelevant_metadata_invalid"
            ),
            excluded_metadata_reasons=tuple(sorted(reasons.items())),
        )


@dataclass(frozen=True, slots=True)
class SemanticReadView:
    graph_id: str
    stream_id: str
    source_sequence: int
    operator_instance_id: str
    memory_version_token: MemoryVersionToken
    mutation_epoch_before: MutationEpochToken
    mutation_epoch_after: MutationEpochToken
    read_kind: ReadKind
    query_identity: str
    search_configuration_hash: str
    candidates: tuple[CandidateSemanticRecord, ...]
    mutable_context_fragment_hash: str
    read_view_digest: str | None
    provenance_hash: str
    status: ReadViewStatus
    unknown_state_fields: tuple[str, ...] = ()
    excluded_metadata_reasons: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _text(self.graph_id, "read_view_graph_id_invalid")
        _text(self.stream_id, "read_view_stream_id_invalid")
        _sequence(self.source_sequence, "read_view_source_sequence_invalid")
        _text(self.operator_instance_id, "read_view_operator_id_invalid")
        if not isinstance(self.memory_version_token, MemoryVersionToken):
            raise _fail("memory_version_token_invalid")
        if not isinstance(self.mutation_epoch_before, MutationEpochToken) or not isinstance(
            self.mutation_epoch_after, MutationEpochToken
        ):
            raise _fail("mutation_epoch_token_invalid")
        if not isinstance(self.read_kind, ReadKind):
            raise _fail("read_kind_invalid")
        _text(self.query_identity, "query_identity_invalid")
        _hash(self.search_configuration_hash, "search_configuration_hash_invalid")
        if not isinstance(self.candidates, tuple) or any(
            not isinstance(item, CandidateSemanticRecord) for item in self.candidates
        ):
            raise _fail("candidate_records_invalid")
        _hash(self.mutable_context_fragment_hash, "context_fragment_hash_invalid")
        if not isinstance(self.unknown_state_fields, tuple):
            raise _fail("unknown_state_fields_invalid")
        for field in self.unknown_state_fields:
            _text(field, "unknown_state_field_invalid")
        if not isinstance(self.excluded_metadata_reasons, tuple):
            raise _fail("metadata_exclusion_reasons_invalid")
        for field, reason in self.excluded_metadata_reasons:
            _text(field, "metadata_exclusion_field_invalid")
            _text(reason, "metadata_exclusion_reason_invalid")
        before_domain = (
            self.mutation_epoch_before.namespace,
            self.mutation_epoch_before.backend_id,
            self.mutation_epoch_before.epoch,
        )
        after_domain = (
            self.mutation_epoch_after.namespace,
            self.mutation_epoch_after.backend_id,
            self.mutation_epoch_after.epoch,
        )
        if before_domain != after_domain:
            raise _fail("mutation_epoch_domain_changed")
        if self.memory_version_token.namespace != self.graph_id:
            raise _fail("read_view_version_namespace_mismatch")
        if self.mutation_epoch_before.namespace != self.graph_id:
            raise _fail("read_view_epoch_namespace_mismatch")
        if not isinstance(self.status, ReadViewStatus):
            raise _fail("read_view_status_invalid")
        _hash(self.provenance_hash, "read_view_provenance_hash_invalid")
        if self.status is ReadViewStatus.STABLE_READVIEW:
            _hash(self.read_view_digest, "read_view_digest_required")
            if self.read_view_digest != _sha256(self.semantic_document()):
                raise _fail("read_view_digest_mismatch")
            if self.mutation_epoch_before != self.mutation_epoch_after:
                raise _fail("stable_readview_epoch_changed")
            if self.unknown_state_fields:
                raise _fail("stable_readview_has_unknown_fields")
        elif self.read_view_digest is not None:
            raise _fail("uncertified_readview_has_digest")

    @property
    def ordered_candidate_ids(self) -> tuple[str, ...]:
        return tuple(candidate.candidate_id for candidate in self.candidates)

    def semantic_document(self) -> dict[str, object]:
        return {
            "graph_id": self.graph_id,
            "mutable_context_fragment_hash": self.mutable_context_fragment_hash,
            "operator_instance_id": self.operator_instance_id,
            "ordered_candidates": [item.semantic_document() for item in self.candidates],
            "query_identity": self.query_identity,
            "read_kind": self.read_kind.value,
            "search_configuration_hash": self.search_configuration_hash,
            "source_sequence": self.source_sequence,
            "stream_id": self.stream_id,
        }


def capture_semantic_read_view(
    *,
    graph_id: str,
    stream_id: str,
    source_sequence: int,
    operator_instance_id: str,
    memory_version_token: MemoryVersionToken,
    mutation_epoch: StateMutationEpoch,
    read_kind: ReadKind,
    materialize: Callable[[], ReadMaterialization],
) -> SemanticReadView:
    """Capture e0/read/e1 and issue a digest only for a complete stable view."""

    if not isinstance(memory_version_token, MemoryVersionToken):
        raise _fail("memory_version_token_invalid")
    if not isinstance(mutation_epoch, StateMutationEpoch):
        raise _fail("mutation_epoch_guard_invalid")
    if not isinstance(read_kind, ReadKind):
        raise _fail("read_kind_invalid")
    if not callable(materialize):
        raise _fail("read_materializer_invalid")
    before = mutation_epoch.snapshot()
    materialized = materialize()
    after = mutation_epoch.snapshot()
    if not isinstance(materialized, ReadMaterialization):
        raise _fail("read_materialization_invalid")

    return semantic_read_view_from_materialization(
        graph_id=graph_id,
        stream_id=stream_id,
        source_sequence=source_sequence,
        operator_instance_id=operator_instance_id,
        memory_version_token=memory_version_token,
        mutation_epoch_before=before,
        mutation_epoch_after=after,
        read_kind=read_kind,
        materialized=materialized,
    )


def semantic_read_view_from_materialization(
    *,
    graph_id: str,
    stream_id: str,
    source_sequence: int,
    operator_instance_id: str,
    memory_version_token: MemoryVersionToken,
    mutation_epoch_before: MutationEpochToken,
    mutation_epoch_after: MutationEpochToken,
    read_kind: ReadKind,
    materialized: ReadMaterialization,
) -> SemanticReadView:
    """Build a view from epoch tokens captured around the actual async read."""

    if not isinstance(memory_version_token, MemoryVersionToken):
        raise _fail("memory_version_token_invalid")
    if not isinstance(mutation_epoch_before, MutationEpochToken) or not isinstance(
        mutation_epoch_after, MutationEpochToken
    ):
        raise _fail("mutation_epoch_token_invalid")
    if not isinstance(read_kind, ReadKind):
        raise _fail("read_kind_invalid")
    if not isinstance(materialized, ReadMaterialization):
        raise _fail("read_materialization_invalid")
    before = mutation_epoch_before
    after = mutation_epoch_after
    graph = _text(graph_id, "read_view_graph_id_invalid")
    stream = _text(stream_id, "read_view_stream_id_invalid")
    sequence = _sequence(source_sequence, "read_view_source_sequence_invalid")
    operator = _text(operator_instance_id, "read_view_operator_id_invalid")
    semantic_document = {
        "graph_id": graph,
        "mutable_context_fragment_hash": materialized.mutable_context_fragment_hash,
        "operator_instance_id": operator,
        "ordered_candidates": [
            candidate.semantic_document() for candidate in materialized.candidates
        ],
        "query_identity": materialized.query_identity,
        "read_kind": read_kind.value,
        "search_configuration_hash": materialized.search_configuration_hash,
        "source_sequence": sequence,
        "stream_id": stream,
    }
    provenance_document = {
        "memory_version_token": memory_version_token.canonical,
        "mutation_epoch_after": after.canonical,
        "mutation_epoch_before": before.canonical,
        "semantic_document_sha256": _sha256(semantic_document),
        "source_provenance_hash": materialized.source_provenance_hash,
    }
    stable = before == after
    complete = not materialized.unknown_state_fields
    status = (
        ReadViewStatus.INVALID_UNSTABLE_READ
        if not stable
        else ReadViewStatus.STABLE_READVIEW
        if complete
        else ReadViewStatus.OPAQUE
    )
    return SemanticReadView(
        graph_id=graph,
        stream_id=stream,
        source_sequence=sequence,
        operator_instance_id=operator,
        memory_version_token=memory_version_token,
        mutation_epoch_before=before,
        mutation_epoch_after=after,
        read_kind=read_kind,
        query_identity=materialized.query_identity,
        search_configuration_hash=materialized.search_configuration_hash,
        candidates=materialized.candidates,
        mutable_context_fragment_hash=materialized.mutable_context_fragment_hash,
        read_view_digest=_sha256(semantic_document) if stable and complete else None,
        provenance_hash=_sha256(provenance_document),
        status=status,
        unknown_state_fields=materialized.unknown_state_fields,
        excluded_metadata_reasons=materialized.excluded_metadata_reasons,
    )


__all__ = [
    "CandidateSemanticRecord",
    "ReadKind",
    "ReadMaterialization",
    "ReadViewStatus",
    "SemanticReadView",
    "SemanticReadViewError",
    "capture_semantic_read_view",
    "semantic_read_view_from_materialization",
]
