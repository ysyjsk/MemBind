"""Offline Graphiti v0.29.3 adapter contracts for MemBind v3.1.

The module deliberately imports neither Graphiti nor a backend client.  It
freezes the operator map observed in pinned Graphiti, exposes only arrived and
canonical source evidence to injected extraction callbacks, and verifies a
captured serial state transition without executing live semantic work.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from paper_eval.artifacts import canonical_bytes, payload_sha256
from paper_eval.membind_v31.certification import CertificationRecord
from paper_eval.membind_v31.contracts import (
    DependencyClass,
    EffectClass,
    OperatorContract,
)
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact


_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NODE_EXTRACT = "graphiti.extract_nodes"
_EDGE_EXTRACT = "graphiti.extract_edges"


class GraphitiV31AdapterError(ValueError):
    """An arrival, certification, extraction, or parity contract failed."""


def _fail(code: str) -> GraphitiV31AdapterError:
    return GraphitiV31AdapterError(code)


def _identity(value: object, code: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise _fail(code)
    return value


def _sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _version(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < -1:
        raise _fail(code)
    return value


def _canonical_mapping(value: object, code: str) -> tuple[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    try:
        encoded = canonical_bytes(dict(value)).decode("utf-8")
        decoded = json.loads(encoded)
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(decoded, dict):
        raise _fail(code)
    return encoded, decoded


def _decoded(value: str) -> dict[str, Any]:
    result = json.loads(value)
    assert isinstance(result, dict)
    return result


def graphiti_v0293_operator_map() -> dict[str, OperatorContract]:
    """Return the frozen v0.29.3 construction map in Native call order.

    Node and edge extraction use only the current episode, arrived evidence,
    fixed schemas, and the raw node output.  Every operation that consumes
    persistent identities or effects remains in the state-bound suffix.
    """

    declarations = (
        (_NODE_EXTRACT, DependencyClass.EVIDENCE_BOUND, EffectClass.PURE),
        (_EDGE_EXTRACT, DependencyClass.EVIDENCE_BOUND, EffectClass.PURE),
        ("graphiti.resolve_nodes", DependencyClass.STATE_BOUND, EffectClass.STATE_READ),
        (
            "graphiti.resolve_edge_pointers",
            DependencyClass.STATE_BOUND,
            EffectClass.PURE,
        ),
        ("graphiti.resolve_edges", DependencyClass.STATE_BOUND, EffectClass.STATE_READ),
        (
            "graphiti.attributes_summary",
            DependencyClass.STATE_BOUND,
            EffectClass.STATE_READ,
        ),
        (
            "graphiti.temporal_invalidation",
            DependencyClass.STATE_BOUND,
            EffectClass.STATE_WRITE,
        ),
        ("graphiti.persistence", DependencyClass.STATE_BOUND, EffectClass.STATE_WRITE),
        ("graphiti.publish", DependencyClass.STATE_BOUND, EffectClass.PUBLISH),
    )
    return {
        name: OperatorContract.create(
            operator_name=name,
            dependency_class=dependency,
            effect_class=effect,
        )
        for name, dependency, effect in declarations
    }


@dataclass(frozen=True, slots=True)
class ArrivedEvidence:
    """One canonical source record carrying its explicit wall-clock arrival."""

    stream_id: str
    source_sequence: int
    arrival_time_ns: int
    source_sha256: str
    _payload_json: str
    record_sha256: str

    @classmethod
    def create(
        cls,
        *,
        stream_id: str,
        source_sequence: int,
        arrival_time_ns: int,
        source_sha256: str,
        payload: Mapping[str, object],
    ) -> "ArrivedEvidence":
        stream = _identity(stream_id, "stream_id_invalid")
        sequence = _nonnegative_int(source_sequence, "source_sequence_invalid")
        arrival = _nonnegative_int(arrival_time_ns, "arrival_time_invalid")
        source = _sha256(source_sha256, "source_sha256_invalid")
        payload_json, decoded = _canonical_mapping(payload, "source_payload_invalid")
        body = {
            "arrival_time_ns": arrival,
            "payload": decoded,
            "source_sequence": sequence,
            "source_sha256": source,
            "stream_id": stream,
        }
        return cls(
            stream_id=stream,
            source_sequence=sequence,
            arrival_time_ns=arrival,
            source_sha256=source,
            _payload_json=payload_json,
            record_sha256=payload_sha256(body),
        )

    @property
    def payload(self) -> dict[str, Any]:
        return _decoded(self._payload_json)

    def document(self) -> dict[str, object]:
        return {
            "arrival_time_ns": self.arrival_time_ns,
            "payload": self.payload,
            "record_sha256": self.record_sha256,
            "source_sequence": self.source_sequence,
            "source_sha256": self.source_sha256,
            "stream_id": self.stream_id,
        }

    def verify(self) -> "ArrivedEvidence":
        recreated = self.create(
            stream_id=self.stream_id,
            source_sequence=self.source_sequence,
            arrival_time_ns=self.arrival_time_ns,
            source_sha256=self.source_sha256,
            payload=self.payload,
        )
        if recreated._payload_json != self._payload_json or recreated.record_sha256 != self.record_sha256:
            raise _fail("source_record_hash_mismatch")
        return self


@dataclass(frozen=True, slots=True)
class ArrivalFencedCompileInput:
    """The complete state-free capability supplied to Compile callbacks."""

    stream_id: str
    source_sequence: int
    source_arrival_time_ns: int
    observed_time_ns: int
    source_sha256: str
    evidence_sha256: str
    _source_payload_json: str
    _evidence_snapshot: tuple[ArrivedEvidence, ...]

    @property
    def source_payload(self) -> dict[str, Any]:
        return _decoded(self._source_payload_json)

    @property
    def evidence_snapshot(self) -> tuple[ArrivedEvidence, ...]:
        return tuple(self._evidence_snapshot)

    def verify(self) -> "ArrivalFencedCompileInput":
        """Re-derive the complete fence identity before semantic callbacks."""

        source = ArrivedEvidence.create(
            stream_id=self.stream_id,
            source_sequence=self.source_sequence,
            arrival_time_ns=self.source_arrival_time_ns,
            source_sha256=self.source_sha256,
            payload=self.source_payload,
        )
        recreated = build_arrival_fenced_input(
            source=source,
            evidence_snapshot=self._evidence_snapshot,
            observed_time_ns=self.observed_time_ns,
        )
        if recreated != self:
            raise _fail("compile_input_hash_mismatch")
        return self


def build_arrival_fenced_input(
    *,
    source: ArrivedEvidence,
    evidence_snapshot: Sequence[ArrivedEvidence],
    observed_time_ns: int,
) -> ArrivalFencedCompileInput:
    """Freeze only source records that had arrived by Compile admission."""

    if not isinstance(source, ArrivedEvidence):
        raise _fail("source_record_invalid")
    try:
        source.verify()
    except GraphitiV31AdapterError:
        raise
    observed = _nonnegative_int(observed_time_ns, "observed_time_invalid")
    if source.arrival_time_ns > observed:
        raise _fail("compile_before_arrival")
    if isinstance(evidence_snapshot, (str, bytes)) or not isinstance(
        evidence_snapshot, Sequence
    ):
        raise _fail("evidence_snapshot_invalid")
    selected: list[ArrivedEvidence] = []
    seen: set[int] = set()
    previous_sequence = -1
    for item in evidence_snapshot:
        if not isinstance(item, ArrivedEvidence):
            raise _fail("evidence_snapshot_invalid")
        item.verify()
        if (
            item.stream_id != source.stream_id
            or item.source_sequence >= source.source_sequence
            or item.arrival_time_ns > observed
        ):
            raise _fail("future_evidence_access")
        if item.source_sequence in seen or item.source_sequence <= previous_sequence:
            raise _fail("evidence_snapshot_order_invalid")
        seen.add(item.source_sequence)
        previous_sequence = item.source_sequence
        selected.append(item)
    evidence_body = {
        "observed_time_ns": observed,
        "records": [item.document() for item in selected],
        "source_record_sha256": source.record_sha256,
    }
    return ArrivalFencedCompileInput(
        stream_id=source.stream_id,
        source_sequence=source.source_sequence,
        source_arrival_time_ns=source.arrival_time_ns,
        observed_time_ns=observed,
        source_sha256=source.source_sha256,
        evidence_sha256=payload_sha256(evidence_body),
        _source_payload_json=source._payload_json,
        _evidence_snapshot=tuple(selected),
    )


class CompileRuntimeGuard:
    """Fail closed when a certified Compile invokes an undeclared capability."""

    def __init__(self, certification: CertificationRecord) -> None:
        if not isinstance(certification, CertificationRecord):
            raise _fail("certification_invalid")
        try:
            self.certification = certification.verify()
        except ValueError:
            raise _fail("certification_invalid") from None
        self._observed: list[str] = []
        self._failed = False

    def observe_api(self, api_name: str) -> None:
        if self._failed:
            raise _fail("state_cut_certification_failure")
        if not isinstance(api_name, str) or not api_name:
            self._failed = True
            raise _fail("state_cut_certification_failure")
        allowed = set(self.certification.allowed_apis)
        forbidden = set(self.certification.forbidden_apis)
        if api_name in forbidden or api_name not in allowed:
            self._failed = True
            raise _fail("state_cut_certification_failure")
        self._observed.append(api_name)

    def observation(self) -> dict[str, object]:
        return {
            "certification_sha256": self.certification.certification_sha256,
            "failed": self._failed,
            "observed_apis": list(self._observed),
        }


ExtractNodes = Callable[
    [ArrivalFencedCompileInput, CompileRuntimeGuard], Awaitable[object]
]
ExtractEdges = Callable[
    [ArrivalFencedCompileInput, Sequence[Mapping[str, object]], CompileRuntimeGuard],
    Awaitable[object],
]


def _extraction_rows(value: object, code: str) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    rows: list[dict[str, Any]] = []
    for item in value:
        _encoded, decoded = _canonical_mapping(item, code)
        rows.append(decoded)
    return rows


async def _await(value: object, code: str) -> object:
    if not inspect.isawaitable(value):
        raise _fail(code)
    try:
        return await value
    except GraphitiV31AdapterError:
        raise
    except Exception:
        raise _fail(code) from None


class GraphitiV31Adapter:
    """Capability-restricted v0.29.3 NodeExtract+EdgeExtract adapter."""

    def __init__(
        self,
        *,
        node_certification: CertificationRecord,
        edge_certification: CertificationRecord,
        extract_nodes: ExtractNodes,
        extract_edges: ExtractEdges,
    ) -> None:
        try:
            node = node_certification.verify()
            edge = edge_certification.verify()
        except (AttributeError, ValueError):
            raise _fail("certification_invalid") from None
        if (
            node.operator_contract.operator_name != _NODE_EXTRACT
            or edge.operator_contract.operator_name != _EDGE_EXTRACT
            or not node.operator_contract.compile_eligible
            or not edge.operator_contract.compile_eligible
        ):
            raise _fail("certification_invalid")
        shared_fields = (
            "memory_backend_identity_sha256",
            "adapter_identity_sha256",
            "code_revision_sha256",
            "schema_identity_sha256",
            "config_identity_sha256",
        )
        if any(getattr(node, field) != getattr(edge, field) for field in shared_fields):
            raise _fail("certification_identity_mismatch")
        if not callable(extract_nodes) or not callable(extract_edges):
            raise _fail("extraction_callback_invalid")
        self._node_certification = node
        self._edge_certification = edge
        self._extract_nodes = extract_nodes
        self._extract_edges = extract_edges
        self.certification_sha256 = payload_sha256(
            {
                "edge_extract": edge.certification_sha256,
                "node_extract": node.certification_sha256,
            }
        )

    async def compile(self, compile_input: ArrivalFencedCompileInput) -> PreparedArtifact:
        if not isinstance(compile_input, ArrivalFencedCompileInput):
            raise _fail("compile_input_invalid")
        try:
            compile_input.verify()
        except GraphitiV31AdapterError:
            raise _fail("compile_input_invalid") from None
        node_guard = CompileRuntimeGuard(self._node_certification)
        raw_nodes = _extraction_rows(
            await _await(
                self._extract_nodes(compile_input, node_guard),
                "extract_nodes_failed",
            ),
            "extract_nodes_result_invalid",
        )
        edge_guard = CompileRuntimeGuard(self._edge_certification)
        raw_edges = _extraction_rows(
            await _await(
                self._extract_edges(
                    compile_input,
                    tuple(dict(node) for node in raw_nodes),
                    edge_guard,
                ),
                "extract_edges_failed",
            ),
            "extract_edges_result_invalid",
        )
        return PreparedArtifact.create(
            source_sequence=compile_input.source_sequence,
            source_sha256=compile_input.source_sha256,
            evidence_sha256=compile_input.evidence_sha256,
            certification_sha256=self.certification_sha256,
            raw_nodes=raw_nodes,
            raw_edges=raw_edges,
        )


def coalesce_compatible_resolved_nodes(
    nodes: Sequence[Mapping[str, object]],
) -> tuple[dict[str, Any], ...]:
    """Merge same-UUID/same-projection rows and reject conflicting aliases."""

    if isinstance(nodes, (str, bytes)) or not isinstance(nodes, Sequence):
        raise _fail("resolved_nodes_invalid")
    selected: list[dict[str, Any]] = []
    projections: dict[str, str] = {}
    for node in nodes:
        encoded, decoded = _canonical_mapping(node, "resolved_node_projection_invalid")
        uuid = decoded.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            raise _fail("resolved_node_uuid_missing")
        previous = projections.get(uuid)
        if previous is None:
            projections[uuid] = encoded
            selected.append(decoded)
        elif previous != encoded:
            raise _fail("conflicting_duplicate_uuid")
    return tuple(selected)


def _canonical_resolved_nodes(
    nodes: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    coalesced = coalesce_compatible_resolved_nodes(nodes)
    by_uuid = sorted(coalesced, key=lambda node: str(node["uuid"]))
    return tuple(canonical_bytes(node).decode("utf-8") for node in by_uuid)


@dataclass(frozen=True, slots=True)
class CapturedStateTransition:
    """A self-hashing deterministic Bind/Publish state-transition witness."""

    stream_id: str
    source_sequence: int
    predecessor_version: int
    successor_version: int
    prepared_artifact_sha256: str
    predecessor_state_sha256: str
    successor_state_sha256: str
    _predecessor_state_json: str
    _successor_state_json: str
    _resolved_nodes_json: tuple[str, ...]
    transition_sha256: str

    @classmethod
    def create(
        cls,
        *,
        stream_id: str,
        source_sequence: int,
        predecessor_version: int,
        successor_version: int,
        predecessor_state: Mapping[str, object],
        successor_state: Mapping[str, object],
        prepared_artifact_sha256: str,
        resolved_nodes: Sequence[Mapping[str, object]],
    ) -> "CapturedStateTransition":
        stream = _identity(stream_id, "stream_id_invalid")
        sequence = _nonnegative_int(source_sequence, "source_sequence_invalid")
        predecessor = _version(predecessor_version, "predecessor_version_invalid")
        successor = _nonnegative_int(successor_version, "successor_version_invalid")
        if predecessor != sequence - 1 or successor != sequence:
            raise _fail("captured_version_contract_invalid")
        artifact = _sha256(
            prepared_artifact_sha256, "prepared_artifact_sha256_invalid"
        )
        predecessor_json, predecessor_decoded = _canonical_mapping(
            predecessor_state, "predecessor_state_invalid"
        )
        successor_json, successor_decoded = _canonical_mapping(
            successor_state, "successor_state_invalid"
        )
        node_json = _canonical_resolved_nodes(resolved_nodes)
        predecessor_sha = payload_sha256(predecessor_decoded)
        successor_sha = payload_sha256(successor_decoded)
        body = {
            "predecessor_state": predecessor_decoded,
            "predecessor_state_sha256": predecessor_sha,
            "predecessor_version": predecessor,
            "prepared_artifact_sha256": artifact,
            "resolved_nodes": [_decoded(item) for item in node_json],
            "source_sequence": sequence,
            "stream_id": stream,
            "successor_state": successor_decoded,
            "successor_state_sha256": successor_sha,
            "successor_version": successor,
        }
        return cls(
            stream_id=stream,
            source_sequence=sequence,
            predecessor_version=predecessor,
            successor_version=successor,
            prepared_artifact_sha256=artifact,
            predecessor_state_sha256=predecessor_sha,
            successor_state_sha256=successor_sha,
            _predecessor_state_json=predecessor_json,
            _successor_state_json=successor_json,
            _resolved_nodes_json=node_json,
            transition_sha256=payload_sha256(body),
        )

    @property
    def predecessor_state(self) -> dict[str, Any]:
        return _decoded(self._predecessor_state_json)

    @property
    def successor_state(self) -> dict[str, Any]:
        return _decoded(self._successor_state_json)

    @property
    def resolved_nodes(self) -> tuple[dict[str, Any], ...]:
        return tuple(_decoded(item) for item in self._resolved_nodes_json)

    def verify(self) -> "CapturedStateTransition":
        recreated = self.create(
            stream_id=self.stream_id,
            source_sequence=self.source_sequence,
            predecessor_version=self.predecessor_version,
            successor_version=self.successor_version,
            predecessor_state=self.predecessor_state,
            successor_state=self.successor_state,
            prepared_artifact_sha256=self.prepared_artifact_sha256,
            resolved_nodes=self.resolved_nodes,
        )
        if (
            recreated.transition_sha256 != self.transition_sha256
            or recreated.predecessor_state_sha256 != self.predecessor_state_sha256
            or recreated.successor_state_sha256 != self.successor_state_sha256
            or recreated._predecessor_state_json != self._predecessor_state_json
            or recreated._successor_state_json != self._successor_state_json
            or recreated._resolved_nodes_json != self._resolved_nodes_json
        ):
            raise _fail("captured_transition_hash_mismatch")
        return self


def verify_captured_transition_parity(
    serial_reference: CapturedStateTransition,
    candidate: CapturedStateTransition,
) -> dict[str, object]:
    """Require exact predecessor, semantic work, and successor-state parity."""

    if not isinstance(serial_reference, CapturedStateTransition) or not isinstance(
        candidate, CapturedStateTransition
    ):
        raise _fail("captured_transition_invalid")
    try:
        serial = serial_reference.verify()
        observed = candidate.verify()
    except GraphitiV31AdapterError:
        raise _fail("captured_transition_invalid") from None
    identity = (
        serial.stream_id,
        serial.source_sequence,
        serial.predecessor_version,
        serial.successor_version,
    )
    if identity != (
        observed.stream_id,
        observed.source_sequence,
        observed.predecessor_version,
        observed.successor_version,
    ):
        raise _fail("captured_transition_identity_mismatch")
    if (
        serial.predecessor_state_sha256 != observed.predecessor_state_sha256
        or serial.prepared_artifact_sha256 != observed.prepared_artifact_sha256
        or serial._resolved_nodes_json != observed._resolved_nodes_json
        or serial.successor_state_sha256 != observed.successor_state_sha256
    ):
        raise _fail("captured_state_parity_failure")
    return {
        "exact_canonical_state_parity": True,
        "exact_predecessor_state_parity": True,
        "exact_prepared_artifact_parity": True,
        "exact_resolved_node_parity": True,
        "source_sequence": serial.source_sequence,
        "stream_id": serial.stream_id,
        "successor_version": serial.successor_version,
    }


__all__ = [
    "ArrivalFencedCompileInput",
    "ArrivedEvidence",
    "CapturedStateTransition",
    "CompileRuntimeGuard",
    "GraphitiV31Adapter",
    "GraphitiV31AdapterError",
    "build_arrival_fenced_input",
    "coalesce_compatible_resolved_nodes",
    "graphiti_v0293_operator_map",
    "verify_captured_transition_parity",
]
