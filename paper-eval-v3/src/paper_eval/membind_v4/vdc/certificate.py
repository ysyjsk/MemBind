"""Progressive, version-bound dependency certificates for MemBind-VDC."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import re

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v4.node_resolve_adapter import PreparedSemanticCall
from paper_eval.membind_v4.semantic_call import SemanticCall


class VDCDependencyCertificateError(ValueError):
    """A dependency certificate claimed evidence it did not carry."""


def _fail(code: str) -> VDCDependencyCertificateError:
    return VDCDependencyCertificateError(code)


def _sequence(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _identity(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(code)
    return value


def _identities(value: object, code: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise _fail(code)
    selected = tuple(value)
    if any(not isinstance(item, str) or not item for item in selected):
        raise _fail(code)
    if len(set(selected)) != len(selected):
        raise _fail(code)
    return selected


class DependencyClass(str, Enum):
    CERTIFIED_DISJOINT = "CERTIFIED_DISJOINT"
    CERTIFIED_CONFLICT = "CERTIFIED_CONFLICT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class VersionedReadCertificate:
    source_sequence: int
    state_version: int
    group_id: str
    semantic_call: SemanticCall
    candidate_ids: tuple[str, ...]
    semantic_keys: tuple[str, ...]
    candidate_scope_complete: bool
    previous_episode_scope_complete: bool

    @classmethod
    def create(
        cls,
        *,
        source_sequence: int,
        state_version: int,
        group_id: str,
        semantic_call: SemanticCall,
        candidate_ids: tuple[str, ...] | list[str],
        semantic_keys: tuple[str, ...] | list[str],
        candidate_scope_complete: bool,
        previous_episode_scope_complete: bool,
    ) -> "VersionedReadCertificate":
        selected = cls(
            source_sequence=_sequence(source_sequence, "source_sequence_invalid"),
            state_version=_sequence(state_version, "state_version_invalid"),
            group_id=_identity(group_id, "group_id_invalid"),
            semantic_call=semantic_call,
            candidate_ids=_identities(candidate_ids, "candidate_ids_invalid"),
            semantic_keys=_identities(semantic_keys, "semantic_keys_invalid"),
            candidate_scope_complete=candidate_scope_complete,
            previous_episode_scope_complete=previous_episode_scope_complete,
        )
        return selected.verify()

    def verify(self) -> "VersionedReadCertificate":
        _sequence(self.source_sequence, "source_sequence_invalid")
        _sequence(self.state_version, "state_version_invalid")
        _identity(self.group_id, "group_id_invalid")
        if not isinstance(self.semantic_call, SemanticCall):
            raise _fail("semantic_call_invalid")
        self.semantic_call.verify()
        if self.semantic_call.source_sequence != self.source_sequence:
            raise _fail("semantic_call_source_mismatch")
        if self.semantic_call.state_version != self.state_version:
            raise _fail("semantic_call_state_version_mismatch")
        _identities(self.candidate_ids, "candidate_ids_invalid")
        _identities(self.semantic_keys, "semantic_keys_invalid")
        if not isinstance(self.candidate_scope_complete, bool) or not isinstance(
            self.previous_episode_scope_complete, bool
        ):
            raise _fail("read_scope_completeness_invalid")
        call_candidates = tuple(
            str(binding.get("uuid")) for binding in self.semantic_call.candidate_bindings
        )
        if self.candidate_scope_complete and call_candidates != self.candidate_ids:
            raise _fail("candidate_scope_semantic_call_mismatch")
        return self

    @property
    def certificate_sha256(self) -> str:
        self.verify()
        return payload_sha256(
            {
                "source_sequence": self.source_sequence,
                "state_version": self.state_version,
                "group_id": self.group_id,
                "semantic_call_fingerprint": self.semantic_call.fingerprint,
                "candidate_ids": list(self.candidate_ids),
                "semantic_keys": list(self.semantic_keys),
                "candidate_scope_complete": self.candidate_scope_complete,
                "previous_episode_scope_complete": self.previous_episode_scope_complete,
            }
        )

    def to_document(self) -> dict[str, object]:
        self.verify()
        body = {
            "schema_version": "membind.paper-eval-v4.vdc-versioned-read-certificate.v1",
            "source_sequence": self.source_sequence,
            "state_version": self.state_version,
            "group_id": self.group_id,
            "semantic_call": self.semantic_call.to_record(),
            "candidate_ids": list(self.candidate_ids),
            "semantic_keys": list(self.semantic_keys),
            "candidate_scope_complete": self.candidate_scope_complete,
            "previous_episode_scope_complete": self.previous_episode_scope_complete,
            "certificate_identity_sha256": self.certificate_sha256,
        }
        return {**body, "document_sha256": payload_sha256(body)}

    @classmethod
    def from_document(cls, value: object) -> "VersionedReadCertificate":
        if not isinstance(value, Mapping):
            raise _fail("read_certificate_document_invalid")
        try:
            if (
                value["schema_version"]
                != "membind.paper-eval-v4.vdc-versioned-read-certificate.v1"
            ):
                raise _fail("read_certificate_schema_invalid")
            body = {
                key: deepcopy(item)
                for key, item in value.items()
                if key != "document_sha256"
            }
            if value.get("document_sha256") != payload_sha256(body):
                raise _fail("read_certificate_hash_mismatch")
            selected = cls.create(
                source_sequence=value["source_sequence"],
                state_version=value["state_version"],
                group_id=value["group_id"],
                semantic_call=SemanticCall.from_record(value["semantic_call"]),
                candidate_ids=value["candidate_ids"],
                semantic_keys=value["semantic_keys"],
                candidate_scope_complete=value["candidate_scope_complete"],
                previous_episode_scope_complete=value[
                    "previous_episode_scope_complete"
                ],
            )
        except VDCDependencyCertificateError:
            raise
        except (KeyError, TypeError, ValueError):
            raise _fail("read_certificate_document_invalid") from None
        if selected.certificate_sha256 != value["certificate_identity_sha256"]:
            raise _fail("read_certificate_identity_mismatch")
        return selected


@dataclass(frozen=True, slots=True)
class FrontierDependencyCertificate:
    source_sequence: int
    group_id: str
    semantic_keys: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    node_write_ids: tuple[str, ...]
    publishes_episode: bool
    published_episode_uuid: str | None
    effect_scope_complete: bool

    @classmethod
    def create(
        cls,
        *,
        source_sequence: int,
        group_id: str,
        semantic_keys: tuple[str, ...] | list[str],
        candidate_ids: tuple[str, ...] | list[str],
        node_write_ids: tuple[str, ...] | list[str],
        publishes_episode: bool,
        effect_scope_complete: bool,
        published_episode_uuid: str | None = None,
    ) -> "FrontierDependencyCertificate":
        selected = cls(
            source_sequence=_sequence(source_sequence, "source_sequence_invalid"),
            group_id=_identity(group_id, "group_id_invalid"),
            semantic_keys=_identities(semantic_keys, "semantic_keys_invalid"),
            candidate_ids=_identities(candidate_ids, "candidate_ids_invalid"),
            node_write_ids=_identities(node_write_ids, "node_write_ids_invalid"),
            publishes_episode=publishes_episode,
            published_episode_uuid=(
                None
                if published_episode_uuid is None
                else _identity(published_episode_uuid, "published_episode_uuid_invalid")
            ),
            effect_scope_complete=effect_scope_complete,
        )
        return selected.verify()

    def verify(self) -> "FrontierDependencyCertificate":
        _sequence(self.source_sequence, "source_sequence_invalid")
        _identity(self.group_id, "group_id_invalid")
        _identities(self.semantic_keys, "semantic_keys_invalid")
        _identities(self.candidate_ids, "candidate_ids_invalid")
        _identities(self.node_write_ids, "node_write_ids_invalid")
        if not isinstance(self.publishes_episode, bool) or not isinstance(
            self.effect_scope_complete, bool
        ):
            raise _fail("frontier_completeness_invalid")
        if self.publishes_episode and self.published_episode_uuid is None:
            # A writer without a stable episode identity cannot certify which
            # future previous-episode prompt inputs it changes.
            return self
        if self.published_episode_uuid is not None:
            _identity(self.published_episode_uuid, "published_episode_uuid_invalid")
        return self


@dataclass(frozen=True, slots=True)
class DependencyDecision:
    dependency_class: DependencyClass
    reason: str
    overlapping_ids: tuple[str, ...] = ()
    overlapping_semantic_keys: tuple[str, ...] = ()


def _semantic_key(node: dict[str, object], group_id: str) -> str:
    name = node.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _fail("extracted_node_name_missing")
    normalized = re.sub(r"\s+", " ", name.casefold()).strip()
    labels_value = node.get("labels", ())
    if isinstance(labels_value, (str, bytes)) or not isinstance(
        labels_value, (tuple, list)
    ):
        raise _fail("extracted_node_labels_invalid")
    labels = sorted(
        str(label).casefold()
        for label in labels_value
        if isinstance(label, str) and label
    )
    return f"{group_id}|{normalized}|{','.join(labels)}"


def read_certificate_from_prepared_call(
    prepared: PreparedSemanticCall,
    *,
    group_id: str,
) -> VersionedReadCertificate:
    """Materialize a complete read certificate from the factorized Probe."""

    if not isinstance(prepared, PreparedSemanticCall):
        raise _fail("prepared_semantic_call_invalid")
    prepared.call.verify()
    group = _identity(group_id, "group_id_invalid")
    candidate_ids: list[str] = []
    for binding in prepared.call.candidate_bindings:
        value = binding.get("uuid")
        if not isinstance(value, str) or not value:
            raise _fail("candidate_binding_uuid_missing")
        candidate_ids.append(value)
    semantic_keys = [
        _semantic_key(dict(node), group) for node in prepared.call.extracted_nodes
    ]
    return VersionedReadCertificate.create(
        source_sequence=prepared.call.source_sequence,
        state_version=prepared.call.state_version,
        group_id=group,
        semantic_call=prepared.call,
        candidate_ids=candidate_ids,
        semantic_keys=semantic_keys,
        candidate_scope_complete=True,
        previous_episode_scope_complete=True,
    )


def classify_early_execution(
    frontier: FrontierDependencyCertificate,
    candidate: VersionedReadCertificate,
) -> DependencyDecision:
    """Classify admission evidence; exact validation remains a separate gate."""

    if not isinstance(frontier, FrontierDependencyCertificate) or not isinstance(
        candidate, VersionedReadCertificate
    ):
        raise _fail("certificate_type_invalid")
    frontier.verify()
    candidate.verify()
    if candidate.source_sequence != frontier.source_sequence + 1:
        raise _fail("certificate_distance_invalid")
    if frontier.group_id != candidate.group_id:
        return DependencyDecision(
            DependencyClass.CERTIFIED_DISJOINT,
            "NAMESPACE_ISOLATION",
        )
    if not frontier.effect_scope_complete or not candidate.candidate_scope_complete:
        return DependencyDecision(DependencyClass.UNKNOWN, "INCOMPLETE_SCOPE")
    if not candidate.previous_episode_scope_complete:
        return DependencyDecision(
            DependencyClass.UNKNOWN,
            "PREVIOUS_EPISODE_SCOPE_INCOMPLETE",
        )
    # Graphiti 0.29.3 includes the latest episodes in the NodeResolve prompt.
    # Publishing the adjacent frontier therefore changes a true request input,
    # even when entity UUIDs and canonical names are disjoint.
    previous_ids = {
        str(item.get("uuid"))
        for item in candidate.semantic_call.previous_episodes
        if isinstance(item, Mapping) and isinstance(item.get("uuid"), str)
    }
    if frontier.publishes_episode and frontier.published_episode_uuid is None:
        return DependencyDecision(
            DependencyClass.UNKNOWN,
            "PUBLISHED_EPISODE_ID_UNOBSERVED",
        )
    if (
        frontier.publishes_episode
        and frontier.published_episode_uuid in previous_ids
    ):
        return DependencyDecision(
            DependencyClass.CERTIFIED_CONFLICT,
            "PREVIOUS_EPISODE_CONTEXT_WILL_CHANGE",
        )
    candidate_ids = set(candidate.candidate_ids)
    overlap_ids = tuple(
        sorted(candidate_ids & (set(frontier.candidate_ids) | set(frontier.node_write_ids)))
    )
    if overlap_ids:
        return DependencyDecision(
            DependencyClass.CERTIFIED_CONFLICT,
            "CANDIDATE_ID_OVERLAP",
            overlapping_ids=overlap_ids,
        )
    overlap_keys = tuple(sorted(set(frontier.semantic_keys) & set(candidate.semantic_keys)))
    if overlap_keys:
        return DependencyDecision(
            DependencyClass.CERTIFIED_CONFLICT,
            "SEMANTIC_KEY_OVERLAP",
            overlapping_semantic_keys=overlap_keys,
        )
    if not frontier.node_write_ids:
        return DependencyDecision(
            DependencyClass.CERTIFIED_DISJOINT,
            "NO_FRONTIER_NODE_OR_EPISODE_EFFECT",
        )
    # Candidate lookup is approximate cosine top-k. A new or updated frontier
    # node can enter the result even when its UUID was absent from the stale set.
    return DependencyDecision(
        DependencyClass.UNKNOWN,
        "APPROXIMATE_CANDIDATE_PHANTOM_NOT_EXCLUDED",
    )


__all__ = [
    "DependencyClass",
    "DependencyDecision",
    "FrontierDependencyCertificate",
    "VDCDependencyCertificateError",
    "VersionedReadCertificate",
    "classify_early_execution",
    "read_certificate_from_prepared_call",
]
