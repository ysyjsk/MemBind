"""Canonical durable data for the node-only MemBind-v1 compile result."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from paper_eval.artifacts import canonical_bytes, payload_sha256


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class MemBindV1DeltaError(ValueError):
    """Prepared node artifact data are invalid or integrity checks fail."""


def _fail(code: str) -> MemBindV1DeltaError:
    return MemBindV1DeltaError(code)


def _sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _canonical_json(value: object, code: str) -> tuple[str, Any]:
    try:
        encoded = canonical_bytes(value)
        decoded = json.loads(encoded.decode("utf-8"))
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    return encoded.decode("utf-8"), decoded


def _canonical_nodes(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail("extracted_nodes_invalid")
    nodes: list[str] = []
    for node in value:
        if not isinstance(node, Mapping):
            raise _fail("extracted_nodes_invalid")
        encoded, decoded = _canonical_json(dict(node), "extracted_nodes_invalid")
        if not isinstance(decoded, dict):
            raise _fail("extracted_nodes_invalid")
        nodes.append(encoded)
    return tuple(nodes)


def _canonical_index_map(value: object) -> tuple[str, dict[str, list[int]]]:
    if not isinstance(value, Mapping):
        raise _fail("node_episode_index_map_invalid")
    normalized: dict[str, list[int]] = {}
    for node_id, raw_indexes in value.items():
        if not isinstance(node_id, str) or not node_id:
            raise _fail("node_episode_index_map_invalid")
        if isinstance(raw_indexes, (str, bytes)) or not isinstance(raw_indexes, Sequence):
            raise _fail("node_episode_index_map_invalid")
        indexes = list(raw_indexes)
        if any(
            isinstance(index, bool) or not isinstance(index, int) or index < 0
            for index in indexes
        ):
            raise _fail("node_episode_index_map_invalid")
        if len(set(indexes)) != len(indexes):
            raise _fail("node_episode_index_map_invalid")
        normalized[node_id] = indexes
    encoded, decoded = _canonical_json(normalized, "node_episode_index_map_invalid")
    if not isinstance(decoded, dict):
        raise _fail("node_episode_index_map_invalid")
    return encoded, decoded


@dataclass(frozen=True, slots=True)
class PreparedNodeArtifact:
    """A self-verifying JSON-only result from node-only semantic compilation."""

    source_sequence: int
    source_sha256: str
    evidence_prefix_sha256: str
    episode_projection_sha256: str
    operation_identity_sha256: str
    model_identity_sha256: str
    prompt_identity_sha256: str
    schema_identity_sha256: str
    config_identity_sha256: str
    _extracted_nodes_json: tuple[str, ...]
    _node_episode_index_map_json: str
    artifact_sha256: str

    @classmethod
    def create(
        cls,
        *,
        source_sequence: int,
        source_sha256: str,
        evidence_prefix_sha256: str,
        episode_projection_sha256: str,
        operation_identity_sha256: str,
        model_identity_sha256: str,
        prompt_identity_sha256: str,
        schema_identity_sha256: str,
        config_identity_sha256: str,
        extracted_nodes: Sequence[Mapping[str, object]],
        node_episode_index_map: Mapping[str, Sequence[int]],
    ) -> "PreparedNodeArtifact":
        sequence = _nonnegative_int(source_sequence, "source_sequence_invalid")
        identities = {
            "source_sha256": _sha256(source_sha256, "source_sha256_invalid"),
            "evidence_prefix_sha256": _sha256(
                evidence_prefix_sha256, "evidence_prefix_sha256_invalid"
            ),
            "episode_projection_sha256": _sha256(
                episode_projection_sha256, "episode_projection_sha256_invalid"
            ),
            "operation_identity_sha256": _sha256(
                operation_identity_sha256, "operation_identity_sha256_invalid"
            ),
            "model_identity_sha256": _sha256(model_identity_sha256, "model_identity_sha256_invalid"),
            "prompt_identity_sha256": _sha256(prompt_identity_sha256, "prompt_identity_sha256_invalid"),
            "schema_identity_sha256": _sha256(schema_identity_sha256, "schema_identity_sha256_invalid"),
            "config_identity_sha256": _sha256(config_identity_sha256, "config_identity_sha256_invalid"),
        }
        nodes_json = _canonical_nodes(extracted_nodes)
        index_map_json, _ = _canonical_index_map(node_episode_index_map)
        payload = cls._payload_from_parts(
            source_sequence=sequence,
            identities=identities,
            extracted_nodes_json=nodes_json,
            index_map_json=index_map_json,
        )
        return cls(
            source_sequence=sequence,
            _extracted_nodes_json=nodes_json,
            _node_episode_index_map_json=index_map_json,
            artifact_sha256=payload_sha256(payload),
            **identities,
        )

    @staticmethod
    def _payload_from_parts(
        *,
        source_sequence: int,
        identities: Mapping[str, str],
        extracted_nodes_json: tuple[str, ...],
        index_map_json: str,
    ) -> dict[str, object]:
        return {
            "config_identity_sha256": identities["config_identity_sha256"],
            "episode_projection_sha256": identities["episode_projection_sha256"],
            "evidence_prefix_sha256": identities["evidence_prefix_sha256"],
            "extracted_nodes": [json.loads(item) for item in extracted_nodes_json],
            "model_identity_sha256": identities["model_identity_sha256"],
            "node_episode_index_map": json.loads(index_map_json),
            "operation_identity_sha256": identities["operation_identity_sha256"],
            "prompt_identity_sha256": identities["prompt_identity_sha256"],
            "schema_identity_sha256": identities["schema_identity_sha256"],
            "source_sequence": source_sequence,
            "source_sha256": identities["source_sha256"],
        }

    def payload(self) -> dict[str, object]:
        return self._payload_from_parts(
            source_sequence=self.source_sequence,
            identities={
                "source_sha256": self.source_sha256,
                "evidence_prefix_sha256": self.evidence_prefix_sha256,
                "episode_projection_sha256": self.episode_projection_sha256,
                "operation_identity_sha256": self.operation_identity_sha256,
                "model_identity_sha256": self.model_identity_sha256,
                "prompt_identity_sha256": self.prompt_identity_sha256,
                "schema_identity_sha256": self.schema_identity_sha256,
                "config_identity_sha256": self.config_identity_sha256,
            },
            extracted_nodes_json=self._extracted_nodes_json,
            index_map_json=self._node_episode_index_map_json,
        )

    @property
    def extracted_nodes(self) -> list[dict[str, Any]]:
        return [json.loads(item) for item in self._extracted_nodes_json]

    @property
    def node_episode_index_map(self) -> dict[str, list[int]]:
        return json.loads(self._node_episode_index_map_json)

    def verify(self) -> "PreparedNodeArtifact":
        for field in (
            "source_sha256",
            "evidence_prefix_sha256",
            "episode_projection_sha256",
            "operation_identity_sha256",
            "model_identity_sha256",
            "prompt_identity_sha256",
            "schema_identity_sha256",
            "config_identity_sha256",
            "artifact_sha256",
        ):
            _sha256(getattr(self, field), f"{field}_invalid")
        _nonnegative_int(self.source_sequence, "source_sequence_invalid")
        _canonical_nodes([json.loads(item) for item in self._extracted_nodes_json])
        _canonical_index_map(json.loads(self._node_episode_index_map_json))
        if payload_sha256(self.payload()) != self.artifact_sha256:
            raise _fail("artifact_hash_mismatch")
        return self


__all__ = ["MemBindV1DeltaError", "PreparedNodeArtifact"]
