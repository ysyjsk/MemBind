"""Canonical private capture for one exact, factorized Graphiti Bind.

The capture contains the inputs and output needed to replay NodeResolve without
Neo4j, an embedding service, or an LLM provider. It is intentionally a private
artifact: unlike public telemetry, it contains rendered request and response
content so deterministic replay can test the actual pinned operator.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

from paper_eval.artifacts import canonical_bytes, payload_sha256
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v4.graphiti_factorization import CapturedGraphitiRequest


_SCHEMA = "membind.paper-eval-v4.vdc-captured-bind-replay.v1"
_REQUEST_SCHEMA = "membind.paper-eval-v4.vdc-graphiti-request.v1"
_EFFECT_SCHEMA = "membind.paper-eval-v4.vdc-node-resolve-effect.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class VDCReplayCaptureError(ValueError):
    """A captured Bind is incomplete, non-canonical, or tampered."""


def _fail(code: str) -> VDCReplayCaptureError:
    return VDCReplayCaptureError(code)


def _nonnegative(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _identity(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(code)
    return value


def _json_round_trip(value: object, code: str) -> object:
    try:
        return json.loads(canonical_bytes(value).decode("utf-8"))
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None


def runtime_projection(value: object) -> object:
    """Project Graphiti/Pydantic runtime values into exact canonical JSON."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return runtime_projection(value.value)
    if isinstance(value, Mapping):
        projected: dict[str, object] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise _fail("runtime_mapping_key_invalid")
            projected[key] = runtime_projection(child)
        return _json_round_trip(projected, "runtime_projection_invalid")
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return _json_round_trip(
            [runtime_projection(item) for item in value],
            "runtime_projection_invalid",
        )
    if isinstance(value, type):
        schema = getattr(value, "model_json_schema", None)
        if callable(schema):
            return _json_round_trip(
                {
                    "__runtime_kind__": "pydantic_response_model",
                    "json_schema": runtime_projection(schema()),
                    "module": value.__module__,
                    "qualname": value.__qualname__,
                },
                "response_model_projection_invalid",
            )
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            selected = dump(mode="json")
        except TypeError:
            selected = dump()
        return runtime_projection(selected)
    raise _fail("runtime_projection_unsupported")


def runtime_mapping_projection(value: object, code: str) -> dict[str, object]:
    selected = runtime_projection(value)
    if not isinstance(selected, dict):
        raise _fail(code)
    return selected


def runtime_sequence_projection(value: object, code: str) -> list[object]:
    selected = runtime_projection(value)
    if not isinstance(selected, list):
        raise _fail(code)
    return selected


def graphiti_request_document(request: CapturedGraphitiRequest) -> dict[str, object]:
    if not isinstance(request, CapturedGraphitiRequest):
        raise _fail("captured_request_invalid")
    body = {
        "schema_version": _REQUEST_SCHEMA,
        "args": runtime_sequence_projection(request.args, "request_args_invalid"),
        "kwargs": runtime_mapping_projection(request.kwargs, "request_kwargs_invalid"),
    }
    return {**body, "request_sha256": payload_sha256(body)}


def interpreted_effect_document(interpreted: object) -> dict[str, object]:
    if not isinstance(interpreted, tuple) or len(interpreted) != 3:
        raise _fail("interpreted_effect_invalid")
    resolved, uuid_map, duplicates = interpreted
    if isinstance(resolved, (str, bytes)) or not isinstance(resolved, Sequence):
        raise _fail("resolved_nodes_invalid")
    if not isinstance(uuid_map, Mapping) or any(
        not isinstance(key, str)
        or not key
        or not isinstance(value, str)
        or not value
        for key, value in uuid_map.items()
    ):
        raise _fail("uuid_map_invalid")
    if isinstance(duplicates, (str, bytes)) or not isinstance(duplicates, Sequence):
        raise _fail("duplicate_pairs_invalid")
    pairs: list[dict[str, object]] = []
    for pair in duplicates:
        if (
            isinstance(pair, (str, bytes))
            or not isinstance(pair, Sequence)
            or len(pair) != 2
        ):
            raise _fail("duplicate_pairs_invalid")
        pairs.append(
            {
                "extracted_node": runtime_mapping_projection(
                    pair[0], "duplicate_node_projection_invalid"
                ),
                "resolved_node": runtime_mapping_projection(
                    pair[1], "duplicate_node_projection_invalid"
                ),
            }
        )
    body = {
        "schema_version": _EFFECT_SCHEMA,
        "resolved_nodes": [
            runtime_mapping_projection(node, "resolved_node_projection_invalid")
            for node in resolved
        ],
        "uuid_map": dict(sorted((str(key), str(value)) for key, value in uuid_map.items())),
        "duplicate_pairs": pairs,
    }
    return {**body, "effect_sha256": payload_sha256(body)}


def _prepared_from_document(value: object) -> PreparedArtifact:
    if not isinstance(value, Mapping):
        raise _fail("prepared_artifact_invalid")
    try:
        artifact = PreparedArtifact.create(
            source_sequence=value["source_sequence"],
            source_sha256=value["source_sha256"],
            evidence_sha256=value["evidence_sha256"],
            certification_sha256=value["certification_sha256"],
            raw_nodes=value["raw_nodes"],
            raw_edges=value["raw_edges"],
            pure_intermediates=value["pure_intermediates"],
        )
    except (KeyError, TypeError, ValueError):
        raise _fail("prepared_artifact_invalid") from None
    if value.get("artifact_sha256") != artifact.artifact_sha256:
        raise _fail("prepared_artifact_hash_mismatch")
    return artifact.verify()


@dataclass(frozen=True, slots=True)
class CapturedBindReplay:
    """Private, self-verifying inputs and output for exact NodeResolve replay."""

    prepared_artifact: PreparedArtifact
    state_version: int
    group_id: str
    episode: dict[str, object]
    previous_episodes: tuple[dict[str, object], ...]
    extracted_nodes: tuple[dict[str, object], ...]
    candidate_nodes_by_extracted: tuple[tuple[dict[str, object], ...], ...]
    request: dict[str, object] | None
    llm_response: object | None
    effect: dict[str, object]
    execution_mode: str
    node_resolve_service_ns: int
    capture_sha256: str

    @classmethod
    def create(
        cls,
        *,
        prepared_artifact: PreparedArtifact,
        state_version: int,
        group_id: str,
        episode: object,
        previous_episodes: Sequence[object],
        extracted_nodes: Sequence[object],
        candidate_nodes_by_extracted: Sequence[Sequence[object]],
        captured_request: CapturedGraphitiRequest | None,
        llm_response: object | None,
        interpreted: object,
        node_resolve_service_ns: int,
    ) -> "CapturedBindReplay":
        if not isinstance(prepared_artifact, PreparedArtifact):
            raise _fail("prepared_artifact_invalid")
        prepared_artifact.verify()
        state = _nonnegative(state_version, "state_version_invalid")
        group = _identity(group_id, "group_id_invalid")
        if isinstance(previous_episodes, (str, bytes)) or not isinstance(
            previous_episodes, Sequence
        ):
            raise _fail("previous_episodes_invalid")
        if isinstance(extracted_nodes, (str, bytes)) or not isinstance(
            extracted_nodes, Sequence
        ):
            raise _fail("extracted_nodes_invalid")
        if isinstance(candidate_nodes_by_extracted, (str, bytes)) or not isinstance(
            candidate_nodes_by_extracted, Sequence
        ):
            raise _fail("candidate_rows_invalid")
        selected_extracted = tuple(
            runtime_mapping_projection(node, "extracted_node_projection_invalid")
            for node in extracted_nodes
        )
        if len(candidate_nodes_by_extracted) != len(selected_extracted):
            raise _fail("candidate_rows_shape_invalid")
        selected_candidates: list[tuple[dict[str, object], ...]] = []
        for row in candidate_nodes_by_extracted:
            if isinstance(row, (str, bytes)) or not isinstance(row, Sequence):
                raise _fail("candidate_rows_invalid")
            selected_candidates.append(
                tuple(
                    runtime_mapping_projection(node, "candidate_projection_invalid")
                    for node in row
                )
            )
        mode = "NO_LLM" if captured_request is None else "LLM"
        if mode == "NO_LLM" and llm_response is not None:
            raise _fail("no_llm_response_forbidden")
        if mode == "LLM" and llm_response is None:
            raise _fail("llm_response_missing")
        request = (
            None if captured_request is None else graphiti_request_document(captured_request)
        )
        response = None if llm_response is None else runtime_projection(llm_response)
        selected = cls(
            prepared_artifact=prepared_artifact,
            state_version=state,
            group_id=group,
            episode=runtime_mapping_projection(episode, "episode_projection_invalid"),
            previous_episodes=tuple(
                runtime_mapping_projection(item, "previous_episode_projection_invalid")
                for item in previous_episodes
            ),
            extracted_nodes=selected_extracted,
            candidate_nodes_by_extracted=tuple(selected_candidates),
            request=request,
            llm_response=response,
            effect=interpreted_effect_document(interpreted),
            execution_mode=mode,
            node_resolve_service_ns=_nonnegative(
                node_resolve_service_ns, "node_resolve_service_ns_invalid"
            ),
            capture_sha256="0" * 64,
        )
        return cls(
            **{
                **selected._parts(),
                "capture_sha256": payload_sha256(selected._body()),
            }
        ).verify()

    def _parts(self) -> dict[str, object]:
        return {
            "prepared_artifact": self.prepared_artifact,
            "state_version": self.state_version,
            "group_id": self.group_id,
            "episode": deepcopy(self.episode),
            "previous_episodes": tuple(deepcopy(self.previous_episodes)),
            "extracted_nodes": tuple(deepcopy(self.extracted_nodes)),
            "candidate_nodes_by_extracted": tuple(
                tuple(deepcopy(row)) for row in self.candidate_nodes_by_extracted
            ),
            "request": deepcopy(self.request),
            "llm_response": deepcopy(self.llm_response),
            "effect": deepcopy(self.effect),
            "execution_mode": self.execution_mode,
            "node_resolve_service_ns": self.node_resolve_service_ns,
        }

    def _body(self) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA,
            "source_sequence": self.prepared_artifact.source_sequence,
            "state_version": self.state_version,
            "group_id": self.group_id,
            "prepared_artifact": self.prepared_artifact.to_document(),
            "episode": deepcopy(self.episode),
            "previous_episodes": [deepcopy(item) for item in self.previous_episodes],
            "extracted_nodes": [deepcopy(item) for item in self.extracted_nodes],
            "candidate_nodes_by_extracted": [
                [deepcopy(item) for item in row]
                for row in self.candidate_nodes_by_extracted
            ],
            "request": deepcopy(self.request),
            "llm_response": deepcopy(self.llm_response),
            "effect": deepcopy(self.effect),
            "execution_mode": self.execution_mode,
            "node_resolve_service_ns": self.node_resolve_service_ns,
        }

    def verify(self) -> "CapturedBindReplay":
        self.prepared_artifact.verify()
        _nonnegative(self.state_version, "state_version_invalid")
        _identity(self.group_id, "group_id_invalid")
        _nonnegative(self.node_resolve_service_ns, "node_resolve_service_ns_invalid")
        if self.execution_mode not in {"LLM", "NO_LLM"}:
            raise _fail("execution_mode_invalid")
        if len(self.candidate_nodes_by_extracted) != len(self.extracted_nodes):
            raise _fail("candidate_rows_shape_invalid")
        if self.execution_mode == "NO_LLM" and (
            self.request is not None or self.llm_response is not None
        ):
            raise _fail("no_llm_payload_forbidden")
        if self.execution_mode == "LLM" and (
            not isinstance(self.request, dict) or self.llm_response is None
        ):
            raise _fail("llm_payload_missing")
        if self.request is not None:
            request_body = {
                key: deepcopy(value)
                for key, value in self.request.items()
                if key != "request_sha256"
            }
            if self.request.get("request_sha256") != payload_sha256(request_body):
                raise _fail("request_hash_mismatch")
        effect_body = {
            key: deepcopy(value)
            for key, value in self.effect.items()
            if key != "effect_sha256"
        }
        if self.effect.get("effect_sha256") != payload_sha256(effect_body):
            raise _fail("effect_hash_mismatch")
        if not isinstance(self.capture_sha256, str) or _SHA256.fullmatch(
            self.capture_sha256
        ) is None:
            raise _fail("capture_hash_invalid")
        if self.capture_sha256 != payload_sha256(self._body()):
            raise _fail("capture_hash_mismatch")
        return self

    def to_document(self) -> dict[str, object]:
        self.verify()
        return {**self._body(), "capture_sha256": self.capture_sha256}

    @classmethod
    def from_document(cls, value: object) -> "CapturedBindReplay":
        if not isinstance(value, Mapping):
            raise _fail("capture_document_invalid")
        try:
            if value["schema_version"] != _SCHEMA:
                raise _fail("capture_schema_invalid")
            prepared = _prepared_from_document(value["prepared_artifact"])
            if value["source_sequence"] != prepared.source_sequence:
                raise _fail("capture_source_sequence_mismatch")
            selected = cls(
                prepared_artifact=prepared,
                state_version=value["state_version"],
                group_id=value["group_id"],
                episode=deepcopy(value["episode"]),
                previous_episodes=tuple(deepcopy(value["previous_episodes"])),
                extracted_nodes=tuple(deepcopy(value["extracted_nodes"])),
                candidate_nodes_by_extracted=tuple(
                    tuple(deepcopy(row)) for row in value["candidate_nodes_by_extracted"]
                ),
                request=deepcopy(value["request"]),
                llm_response=deepcopy(value["llm_response"]),
                effect=deepcopy(value["effect"]),
                execution_mode=value["execution_mode"],
                node_resolve_service_ns=value["node_resolve_service_ns"],
                capture_sha256=value["capture_sha256"],
            )
        except VDCReplayCaptureError:
            raise
        except (KeyError, TypeError):
            raise _fail("capture_document_invalid") from None
        return selected.verify()


__all__ = [
    "CapturedBindReplay",
    "VDCReplayCaptureError",
    "graphiti_request_document",
    "interpreted_effect_document",
    "runtime_mapping_projection",
    "runtime_projection",
]

