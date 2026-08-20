"""Legal, state-bounded conflict signals for v4 NodeResolve admission.

The PreparedArtifact extractor deliberately accepts no graph, state reader,
embedder, or provider capability. Existing entity UUIDs may be added only
from an already materialized :class:`SemanticCall`; this module never causes
that materialization itself.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Final

from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v4.semantic_call import SemanticCall


_SCHEMA: Final = "membind.paper-eval-v4.conflict-signature.v1"
_WHITESPACE = re.compile(r"[\s]+")


class ConflictSignatureError(ValueError):
    """A conflict signal crossed its legal or canonical boundary."""


def _fail(code: str) -> ConflictSignatureError:
    return ConflictSignatureError(code)


def normalize_entity_name(name: str) -> str:
    """Apply Graphiti 0.29.3 exact-name normalization."""

    if not isinstance(name, str):
        raise _fail("entity_name_invalid")
    return _WHITESPACE.sub(" ", name.lower()).strip()


@dataclass(frozen=True, slots=True)
class ConflictSignature:
    """A deterministic, non-authoritative prediction input.

    Canonical names and existing UUIDs remain internal runtime data. Public
    artifacts must use :meth:`content_safe_record`, which emits only hashes
    and counts.
    """

    source_sequence: int
    namespace: str | None
    canonical_names: tuple[str, ...]
    entity_types: tuple[tuple[str, tuple[str, ...]], ...]
    relation_endpoint_names: tuple[str, ...]
    existing_candidate_ids: tuple[str, ...] | None
    published_state_version: int | None
    complete: bool
    incomplete_reasons: tuple[str, ...]
    artifact_sha256: str

    @property
    def entity_keys(self) -> tuple[tuple[str, str], ...]:
        if self.namespace is None:
            return ()
        return tuple((self.namespace, name) for name in self.canonical_names)

    def content_safe_record(self) -> dict[str, object]:
        def digest(value: str) -> str:
            return hashlib.sha256(value.encode("utf-8")).hexdigest()

        return {
            "schema_version": _SCHEMA,
            "source_sequence": self.source_sequence,
            "artifact_sha256": self.artifact_sha256,
            "namespace_sha256": (
                None if self.namespace is None else digest(self.namespace)
            ),
            "canonical_entity_count": len(self.canonical_names),
            "canonical_entity_sha256s": tuple(
                digest(f"{self.namespace}\0{name}") for name in self.canonical_names
            ),
            "relation_endpoint_count": len(self.relation_endpoint_names),
            "existing_candidate_id_count": (
                None
                if self.existing_candidate_ids is None
                else len(self.existing_candidate_ids)
            ),
            "existing_candidate_id_sha256s": (
                None
                if self.existing_candidate_ids is None
                else tuple(digest(value) for value in self.existing_candidate_ids)
            ),
            "published_state_version": self.published_state_version,
            "complete": self.complete,
            "incomplete_reasons": self.incomplete_reasons,
        }


def extract_conflict_signature(artifact: PreparedArtifact) -> ConflictSignature:
    """Extract PreparedArtifact-only signals without reading any state."""

    if not isinstance(artifact, PreparedArtifact):
        raise _fail("prepared_artifact_invalid")
    try:
        artifact.verify()
    except Exception:
        raise _fail("prepared_artifact_invalid") from None

    reasons: set[str] = set()
    namespaces: set[str] = set()
    names: set[str] = set()
    labels_by_name: dict[str, set[str]] = {}
    raw_uuid_to_name: dict[str, str] = {}

    nodes = artifact.raw_nodes
    if not nodes:
        reasons.add("ENTITY_SET_EMPTY")
    for node in nodes:
        namespace = node.get("group_id")
        if not isinstance(namespace, str) or not namespace:
            reasons.add("NAMESPACE_INVALID")
        else:
            namespaces.add(namespace)

        raw_name = node.get("name")
        if not isinstance(raw_name, str):
            reasons.add("ENTITY_NAME_INVALID")
            continue
        canonical_name = normalize_entity_name(raw_name)
        if not canonical_name:
            reasons.add("ENTITY_NAME_INVALID")
            continue
        names.add(canonical_name)

        raw_labels = node.get("labels", ())
        if not isinstance(raw_labels, (list, tuple)) or any(
            not isinstance(label, str) or not label for label in raw_labels
        ):
            reasons.add("ENTITY_TYPES_INVALID")
        else:
            labels_by_name.setdefault(canonical_name, set()).update(raw_labels)

        raw_uuid = node.get("uuid")
        if not isinstance(raw_uuid, str) or not raw_uuid:
            reasons.add("EXTRACTED_UUID_INVALID")
        elif raw_uuid in raw_uuid_to_name:
            reasons.add("EXTRACTED_UUID_DUPLICATE")
        else:
            raw_uuid_to_name[raw_uuid] = canonical_name

    if len(namespaces) > 1:
        reasons.add("MIXED_NAMESPACES")
    namespace = next(iter(namespaces)) if len(namespaces) == 1 else None

    endpoint_names: set[str] = set()
    edges = artifact.raw_edges
    if edges is None:
        reasons.add("RELATION_SIGNAL_UNAVAILABLE")
    else:
        for edge in edges:
            edge_namespace = edge.get("group_id")
            if (
                edge_namespace is not None
                and isinstance(edge_namespace, str)
                and namespace is not None
                and edge_namespace != namespace
            ):
                reasons.add("MIXED_NAMESPACES")
            elif edge_namespace is not None and not isinstance(edge_namespace, str):
                reasons.add("NAMESPACE_INVALID")
            for field in ("source_node_uuid", "target_node_uuid"):
                endpoint_uuid = edge.get(field)
                if not isinstance(endpoint_uuid, str) or endpoint_uuid not in raw_uuid_to_name:
                    reasons.add("RELATION_ENDPOINT_UNRESOLVED")
                else:
                    endpoint_names.add(raw_uuid_to_name[endpoint_uuid])

    canonical_names = tuple(sorted(names))
    entity_types = tuple(
        (name, tuple(sorted(labels_by_name.get(name, ()))))
        for name in canonical_names
    )
    return ConflictSignature(
        source_sequence=artifact.source_sequence,
        namespace=namespace,
        canonical_names=canonical_names,
        entity_types=entity_types,
        relation_endpoint_names=tuple(sorted(endpoint_names)),
        existing_candidate_ids=None,
        published_state_version=None,
        complete=not reasons,
        incomplete_reasons=tuple(sorted(reasons)),
        artifact_sha256=artifact.artifact_sha256,
    )


def enrich_conflict_signature(
    signature: ConflictSignature,
    materialized_call: SemanticCall,
) -> ConflictSignature:
    """Add stable candidate UUIDs from one legal, already materialized call."""

    if not isinstance(signature, ConflictSignature):
        raise _fail("conflict_signature_invalid")
    if not isinstance(materialized_call, SemanticCall):
        raise _fail("semantic_call_invalid")
    try:
        materialized_call.verify()
    except Exception:
        raise _fail("semantic_call_invalid") from None
    if signature.source_sequence != materialized_call.source_sequence:
        raise _fail("source_sequence_mismatch")
    existing_ids = tuple(
        sorted({str(binding["uuid"]) for binding in materialized_call.candidate_bindings})
    )
    return replace(
        signature,
        existing_candidate_ids=existing_ids,
        published_state_version=materialized_call.state_version,
    )


__all__ = [
    "ConflictSignature",
    "ConflictSignatureError",
    "enrich_conflict_signature",
    "extract_conflict_signature",
    "normalize_entity_name",
]
