#!/usr/bin/env python3
"""Replay the exact growing-history failure through a candidate deployment."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SFWB = ROOT / "saturated_fixed_work_baseline_v1_3"
MAB = ROOT / "mab_quality_v2_final_qa"
for source in (SFWB / "src", MAB / "src", SFWB / "scripts"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from jsonschema import validate as validate_json_schema  # noqa: E402
from mab_quality_v2_final_qa.mab8192_adapter import (  # noqa: E402
    MAB8192_ADAPTER_VERSION,
    MAB8192Manifest,
)
from mab_quality_v2_final_qa.mab_main_dataset import build_authority  # noqa: E402
from saturated_fixed_work_baseline_v1_3.mab_live_runner import (  # noqa: E402
    _episode_node,
    _logical_identity,
    _mab_graphiti_kwargs,
    episode_from_input,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.upstream_runtime import (  # noqa: E402
    FORMAL_ARM_A,
    DeploymentPolicy,
    P2_DEPLOYMENT_POLICY,
    _canonical,
    build_formal_upstream_runtime,
    close_formal_upstream_runtime,
    current_logical_request_identity,
    deployment_wire_fields,
    formal_runtime_identity,
    logical_request_context,
    logical_request_seed,
    request_hash,
)


PRESERVED_NAMESPACE = (
    "local-qwen25-7b-awq-dualreplica-v1-upstream-l2-h0-20260903T164111Z-"
    "graphiti-serial-upstream-core-mab8192-1107077ed04e"
)
EXPECTED_MANIFEST_SHA256 = (
    "8034164331215793c26a56ae91dc026ffd4ff759648738871da5814d9dfa3fa4"
)
EXPECTED_MESSAGES_SHA256 = (
    "5e2cbd3409655c7cc93dcb86c2a1b4819c5e5b6145898d21111078b252b49690"
)
EXPECTED_SEMANTIC_REQUEST_SHA256 = (
    "2a8e39d8b371ddea49391ddf99d0841c5ed5d4f7449ff90b5c37508dda66e29c"
)
EXPECTED_AFTER_REQUEST_SHA256 = (
    "9339d1885558da40f8f3da6d5fae82108f19d3d06d934a1883d6b9b160bc30ae"
)
EXPECTED_STABLE_SEED = 3248099774
HISTORICAL_CAPTURE = Path(
    "/data/predator/ly/Mem/experiments/local-qwen25-7b-awq-dualreplica-v1/"
    "strict-upstream-l1-exact-20260904T092622Z/captured_request.json"
)
TARGET_GLOBAL_SEQUENCE = 13
TARGET_ORIGINAL_SOURCE = 3
TARGET_CHUNK_ORDINAL = 2
TARGET_CHUNK_ID = "chunk-629c22ceea0b38f1137fb847aa36c4c7"
EXPECTED_INITIAL_STATE = {
    "node_count": 39,
    "relationship_count": 59,
    "episodic_count": 13,
}

# The historical growing-history target above is retained as a diagnostic
# artifact, but its current message yields only the mandatory ``Assistant``
# speaker node.  It cannot prove that the edge model accepts content-bearing
# output.  This preregistered witness is an official H0 chunk whose text names
# both endpoints and whose relation was independently observed in the sealed
# canary graph artifact below.
WITNESS_GLOBAL_SEQUENCE = 0
WITNESS_ORIGINAL_SOURCE = 0
WITNESS_CHUNK_ORDINAL = 0
WITNESS_CHUNK_ID = "chunk-e3555fef8015377fdcee3936c1528831"
WITNESS_ENTITY_NAMES = ("JetBlue", "San Francisco")
WITNESS_EXPECTED_EDGE = {
    "source_entity_key": "jetblue",
    "target_entity_key": "san francisco",
    "relation_type": "FLY_OUT_OF",
    "source_episode_sequence": 0,
}
WITNESS_PROVENANCE_ARTIFACT = Path(
    "/data/predator/ly/Mem/experiments/local-qwen3-8b-awq-dualreplica-v1/"
    "engineering-canary-resource-credit-20260902T162519Z/context-0/"
    "GRAPHITI_SERIAL_SHARED_BOUNDED_SO/9918e7ffdd7a/block/graph_diagnostics.json"
)


class TargetRequestCaptured(RuntimeError):
    """Stop Graphiti before publication after capturing the exact target wire call."""


class TargetRequestIdentityMismatch(RuntimeError):
    """The real target call was reached but did not match the historical request."""


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(dict(value), stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _wire_messages_sha256(messages: Any) -> str:
    payload = []
    for message in messages:
        if isinstance(message, Mapping):
            payload.append(
                {"role": message.get("role"), "content": message.get("content")}
            )
        else:
            payload.append(
                {
                    "role": getattr(message, "role", None),
                    "content": getattr(message, "content", None),
                }
            )
    return request_hash({"messages": payload})


def _semantic_request_sha256(kwargs: Mapping[str, Any]) -> str:
    fields = ("model", "messages", "max_tokens", "response_format")
    return request_hash({field: kwargs.get(field) for field in fields})


def _changed_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        paths: list[str] = []
        for key in sorted(set(before) | set(after)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in before or key not in after:
                paths.append(path)
            else:
                paths.extend(_changed_paths(before[key], after[key], path))
        return paths
    return [] if before == after else [prefix]


def _expected_candidate_wire_request(
    historical: Mapping[str, Any],
    deployment: DeploymentPolicy,
) -> tuple[dict[str, Any], list[str]]:
    expected = _canonical(historical)
    if not isinstance(expected, dict):
        raise TypeError("historical wire request must be a mapping")
    seed = expected.get("seed")
    if not isinstance(seed, int):
        raise RuntimeError("historical wire request has no stable seed")
    fields = deployment_wire_fields(deployment, seed=seed)
    expected["model"] = deployment.served_model
    for name in ("temperature", "top_p", "seed", "presence_penalty"):
        expected[name] = fields[name]
    expected["extra_body"] = fields.get("extra_body", {})
    return expected, _changed_paths(historical, expected)


def _load_target_episode() -> tuple[Any, MAB8192Manifest]:
    authority = build_authority(MAB / "data/official_5_contexts.json")
    context = tuple(authority["contexts"])[0]
    manifest = MAB8192Manifest.from_context(
        context, dataset_revision=str(authority["revision"])
    )
    if manifest.manifest_sha256 != EXPECTED_MANIFEST_SHA256:
        raise RuntimeError("official H0 MAB8192 manifest identity drift")
    chunk = manifest.chunks[TARGET_GLOBAL_SEQUENCE]
    raw = SimpleNamespace(
        context_id=chunk.context_id,
        source_sequence=chunk.global_sequence,
        original_source_sequence=chunk.source_sequence,
        episode_id=chunk.chunk_id,
        session_id=chunk.session_id,
        reference_time=chunk.reference_time,
        body=chunk.body,
        dataset_revision=chunk.dataset_revision,
        chunk_ordinal=chunk.chunk_ordinal,
        chunk_count=chunk.chunk_count,
        chunk_id=chunk.chunk_id,
        previous_chunk_id=chunk.previous_chunk_id,
        adapter_version=MAB8192_ADAPTER_VERSION,
    )
    episode = episode_from_input(raw)
    observed = (
        episode.source_sequence,
        episode.original_source_sequence,
        episode.chunk_ordinal,
        episode.chunk_id,
    )
    expected = (
        TARGET_GLOBAL_SEQUENCE,
        TARGET_ORIGINAL_SOURCE,
        TARGET_CHUNK_ORDINAL,
        TARGET_CHUNK_ID,
    )
    if observed != expected:
        raise RuntimeError("strict L1 target chunk identity drift")
    return episode, manifest


def _load_witness_episode() -> tuple[Any, MAB8192Manifest]:
    """Load the fixed official chunk used for the content-bearing L1 witness."""

    authority = build_authority(MAB / "data/official_5_contexts.json")
    context = tuple(authority["contexts"])[0]
    manifest = MAB8192Manifest.from_context(
        context, dataset_revision=str(authority["revision"])
    )
    if manifest.manifest_sha256 != EXPECTED_MANIFEST_SHA256:
        raise RuntimeError("official H0 MAB8192 manifest identity drift")
    chunk = manifest.chunks[WITNESS_GLOBAL_SEQUENCE]
    raw = SimpleNamespace(
        context_id=chunk.context_id,
        source_sequence=chunk.global_sequence,
        original_source_sequence=chunk.source_sequence,
        episode_id=chunk.chunk_id,
        session_id=chunk.session_id,
        reference_time=chunk.reference_time,
        body=chunk.body,
        dataset_revision=chunk.dataset_revision,
        chunk_ordinal=chunk.chunk_ordinal,
        chunk_count=chunk.chunk_count,
        chunk_id=chunk.chunk_id,
        previous_chunk_id=chunk.previous_chunk_id,
        adapter_version=MAB8192_ADAPTER_VERSION,
    )
    episode = episode_from_input(raw)
    observed = (
        episode.source_sequence,
        episode.original_source_sequence,
        episode.chunk_ordinal,
        episode.chunk_id,
    )
    expected = (
        WITNESS_GLOBAL_SEQUENCE,
        WITNESS_ORIGINAL_SOURCE,
        WITNESS_CHUNK_ORDINAL,
        WITNESS_CHUNK_ID,
    )
    if observed != expected:
        raise RuntimeError("strict L1 witness chunk identity drift")
    return episode, manifest


def validate_witness_selection(episode: Any) -> dict[str, Any]:
    """Fail closed unless the preregistered witness has two eligible endpoints."""

    names = tuple(WITNESS_ENTITY_NAMES)
    normalized = tuple(" ".join(name.split()).casefold() for name in names)
    if len(names) < 2 or len(set(normalized)) != len(normalized):
        raise RuntimeError("L1 witness requires at least two distinct entities")
    body = str(getattr(episode, "body", "")).casefold()
    missing = [name for name in names if name.casefold() not in body]
    if missing:
        raise RuntimeError(
            "L1 witness entities are not explicitly present in CURRENT_MESSAGE: "
            + ", ".join(missing)
        )
    if WITNESS_EXPECTED_EDGE["source_entity_key"] not in normalized:
        raise RuntimeError("witness source endpoint is not registered")
    if WITNESS_EXPECTED_EDGE["target_entity_key"] not in normalized:
        raise RuntimeError("witness target endpoint is not registered")
    return {
        "entity_names": list(names),
        "distinct_entity_count": len(normalized),
        "current_message_contains_all_entities": True,
        "expected_edge": dict(WITNESS_EXPECTED_EDGE),
        "provenance_artifact": str(WITNESS_PROVENANCE_ARTIFACT),
    }


def load_witness_provenance() -> dict[str, Any]:
    """Verify the fixed pair against a preserved, sealed semantic graph."""

    if not WITNESS_PROVENANCE_ARTIFACT.is_file():
        raise RuntimeError("preserved witness provenance artifact is missing")
    payload = _read_json(WITNESS_PROVENANCE_ARTIFACT)
    edges = payload.get("edges")
    if not isinstance(edges, list):
        raise RuntimeError("witness provenance graph has no edge list")
    for edge in edges:
        if not isinstance(edge, Mapping):
            continue
        if all(edge.get(key) == value for key, value in WITNESS_EXPECTED_EDGE.items()):
            return {
                "artifact": str(WITNESS_PROVENANCE_ARTIFACT),
                "artifact_graph_hash": payload.get("canonical_graph_hash"),
                "expected_edge": dict(WITNESS_EXPECTED_EDGE),
                "status": "PASS",
            }
    raise RuntimeError("witness provenance artifact does not contain expected edge")


def _witness_edge_context(episode: Any, *, namespace: str) -> dict[str, Any]:
    """Build the unmodified upstream extract_edges.edge prompt context."""

    current = _episode_node(episode, namespace=namespace)
    from graphiti_core.utils.text_utils import concatenate_episodes

    return {
        "episode_content": concatenate_episodes([current]),
        "nodes": [
            {"name": name, "entity_types": ["Entity"]}
            for name in WITNESS_ENTITY_NAMES
        ],
        "previous_episodes": [],
        "reference_time": current.valid_at,
        "edge_types": [],
        "custom_extraction_instructions": "",
    }


def _namespace_state(namespace: str) -> dict[str, Any]:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    try:
        with driver.session(
            database=os.environ.get("NEO4J_DATABASE", "neo4j")
        ) as session:
            node_count = session.run(
                "MATCH (n) WHERE n.group_id=$namespace RETURN count(n) AS count",
                namespace=namespace,
            ).single(strict=True)["count"]
            relationship_count = session.run(
                "MATCH ()-[r]->() WHERE r.group_id=$namespace RETURN count(r) AS count",
                namespace=namespace,
            ).single(strict=True)["count"]
            episodes = session.run(
                """
                MATCH (n:Episodic) WHERE n.group_id=$namespace
                RETURN n.name AS name, n.uuid AS uuid, toString(n.valid_at) AS valid_at
                ORDER BY n.name
                """,
                namespace=namespace,
            ).data()
    finally:
        driver.close()
    state = {
        "node_count": int(node_count),
        "relationship_count": int(relationship_count),
        "episodic_count": len(episodes),
        "episodes": episodes,
    }
    state["state_sha256"] = request_hash(state)
    return state


class _CaptureCompletions:
    def __init__(
        self,
        delegate: Any,
        *,
        endpoint_id: str,
        target_messages_sha256: str,
        capture: dict[str, Any],
    ) -> None:
        self._delegate = delegate
        self._endpoint_id = endpoint_id
        self._target_messages_sha256 = target_messages_sha256
        self._capture = capture

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        messages_sha256 = _wire_messages_sha256(kwargs.get("messages", ()))
        logical = current_logical_request_identity() or {}
        reached_logical_target = (
            logical.get("source_sequence") == TARGET_ORIGINAL_SOURCE
            and logical.get("chunk_ordinal") == TARGET_CHUNK_ORDINAL
            and logical.get("chunk_id") == TARGET_CHUNK_ID
            and logical.get("prompt_name") == "extract_edges.edge"
        )
        if messages_sha256 == self._target_messages_sha256:
            if self._capture:
                raise RuntimeError("strict L1 target request was observed more than once")
            self._capture.update(
                {
                    "endpoint_id": self._endpoint_id,
                    "wire_messages_sha256": messages_sha256,
                    "semantic_request_sha256": _semantic_request_sha256(kwargs),
                    "after_request_sha256": request_hash(kwargs),
                    "logical_identity": dict(logical),
                    "wire_request": _canonical(kwargs),
                    "_wire_kwargs": dict(kwargs),
                    "_delegate": self._delegate,
                }
            )
            raise TargetRequestCaptured("captured exact strict L1 target request")
        if reached_logical_target:
            raise TargetRequestIdentityMismatch(
                f"target edge messages drifted: {messages_sha256}"
            )
        return await self._delegate.create(*args, **kwargs)


class _CaptureClient:
    def __init__(
        self,
        delegate: Any,
        *,
        endpoint_id: str,
        capture: dict[str, Any],
    ) -> None:
        self._delegate = delegate
        self.chat = SimpleNamespace(
            completions=_CaptureCompletions(
                delegate.chat.completions,
                endpoint_id=endpoint_id,
                target_messages_sha256=EXPECTED_MESSAGES_SHA256,
                capture=capture,
            )
        )

    async def close(self) -> None:
        result = self._delegate.close()
        if asyncio.iscoroutine(result):
            await result


def _install_target_capture(runtime: Any, capture: dict[str, Any]) -> None:
    router = runtime._membind_route_client
    for endpoint_id, transparent in router.endpoint_clients.items():
        transparent._client = _CaptureClient(
            transparent._client,
            endpoint_id=endpoint_id,
            capture=capture,
        )


class _RecordingCompletions:
    """Observe one native edge request while delegating it unchanged."""

    def __init__(self, delegate: Any, *, endpoint_id: str, capture: dict[str, Any]):
        self._delegate = delegate
        self._endpoint_id = endpoint_id
        self._capture = capture

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        logical = current_logical_request_identity() or {}
        is_edge_request = logical.get("prompt_name") == "extract_edges.edge"
        if is_edge_request:
            count = int(self._capture.get("provider_request_count", 0)) + 1
            self._capture["provider_request_count"] = count
            if count > 1:
                raise RuntimeError("L1 witness edge request was observed more than once")
            self._capture.update(
                {
                    "endpoint_id": self._endpoint_id,
                    "wire_messages_sha256": _wire_messages_sha256(
                        kwargs.get("messages", ())
                    ),
                    "semantic_request_sha256": _semantic_request_sha256(kwargs),
                    "after_request_sha256": request_hash(kwargs),
                    "logical_identity": dict(logical),
                    "wire_request": _canonical(kwargs),
                }
            )
        response = await self._delegate.create(*args, **kwargs)
        if is_edge_request:
            self._capture["_response"] = response
        return response


class _RecordingClient:
    def __init__(self, delegate: Any, *, endpoint_id: str, capture: dict[str, Any]):
        self._delegate = delegate
        self.chat = SimpleNamespace(
            completions=_RecordingCompletions(
                delegate.chat.completions,
                endpoint_id=endpoint_id,
                capture=capture,
            )
        )

    async def close(self) -> None:
        result = self._delegate.close()
        if asyncio.iscoroutine(result):
            await result


def _install_recording_capture(runtime: Any, capture: dict[str, Any]) -> None:
    router = runtime._membind_route_client
    for endpoint_id, transparent in router.endpoint_clients.items():
        transparent._client = _RecordingClient(
            transparent._client,
            endpoint_id=endpoint_id,
            capture=capture,
        )


def _usage(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    return {
        name: getattr(usage, name, None) if usage is not None else None
        for name in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


def _evaluate_target_response(response: Any) -> dict[str, Any]:
    from graphiti_core.prompts.extract_edges import ExtractedEdges

    choices = getattr(response, "choices", ()) or ()
    choice = choices[0] if choices else None
    finish_reason = getattr(choice, "finish_reason", None)
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None)
    usage = _usage(response)
    parsed: Any = None
    json_valid = False
    pydantic_valid = False
    schema_valid = False
    edge_count = 0
    errors: list[str] = []
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            json_valid = True
        except (TypeError, ValueError) as exc:
            errors.append(f"JSON:{exc}")
    else:
        errors.append("response content is not text")
    if json_valid:
        if isinstance(parsed, Mapping) and isinstance(parsed.get("edges"), list):
            edge_count = len(parsed["edges"])
        try:
            ExtractedEdges.model_validate(parsed)
            pydantic_valid = True
        except Exception as exc:
            errors.append(f"Pydantic:{exc}")
        try:
            validate_json_schema(instance=parsed, schema=ExtractedEdges.model_json_schema())
            schema_valid = True
        except Exception as exc:
            errors.append(f"JSONSchema:{exc}")
    completion_tokens = usage.get("completion_tokens")
    reached_token_limit = finish_reason == "length" or (
        isinstance(completion_tokens, int) and completion_tokens >= 16384
    )
    passed = (
        finish_reason == "stop"
        and json_valid
        and pydantic_valid
        and schema_valid
        and edge_count > 0
        and not reached_token_limit
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "finish_reason": finish_reason,
        "json_valid": json_valid,
        "pydantic_valid": pydantic_valid,
        "schema_valid": schema_valid,
        "edge_count": edge_count,
        "empty_edge_result": edge_count == 0,
        "content_bearing_witness": edge_count > 0,
        "reached_token_limit": reached_token_limit,
        "response_repair_enabled": False,
        "response_characters": len(content) if isinstance(content, str) else None,
        "response_bytes": len(content.encode("utf-8")) if isinstance(content, str) else None,
        "response_content_sha256": (
            hashlib.sha256(content.encode("utf-8")).hexdigest()
            if isinstance(content, str)
            else None
        ),
        "response_sha256": request_hash({"response": _canonical(response)}),
        "usage": usage,
        "errors": errors,
    }


async def _heartbeat(path: Path, stop: asyncio.Event, phase: dict[str, Any]) -> None:
    while not stop.is_set():
        _write_atomic(
            path,
            {
                "status": "RUNNING",
                "pid": os.getpid(),
                "phase": phase.get("value"),
                "updated_unix": time.time(),
            },
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except TimeoutError:
            pass


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON object required: {path}")
    return value


async def run(
    *,
    root: Path,
    namespace: str,
    route_path: Path,
    platform_manifest: Path,
) -> dict[str, Any]:
    root = root.resolve()
    if root.exists():
        raise RuntimeError("strict L1 root must be fresh")
    root.mkdir(parents=True)
    phase = {"value": "INITIALIZING"}
    stop = asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat(root / "heartbeat.json", stop, phase))
    runtime: Any = None
    result: dict[str, Any] = {
        "schema_version": "membind.strict-upstream-l1.v1",
        "status": "FAIL",
        "scope": "EXACT_GROWING_HISTORY_REQUEST_QUALIFICATION",
        "pid": os.getpid(),
        "namespace": namespace,
        "started_unix": time.time(),
    }
    try:
        deployment = P2_DEPLOYMENT_POLICY
        if os.environ.get("MEMBIND_DEPLOYMENT_POLICY_ID") != deployment.policy_id:
            raise RuntimeError("strict L1 requires the P2 candidate deployment")
        if os.environ.get("MEMBIND_PROFILE_ID") != deployment.profile_id:
            raise RuntimeError("strict L1 requires the P2 candidate profile")
        platform = _read_json(platform_manifest.resolve())
        if (
            platform.get("profile_id") != deployment.profile_id
            or platform.get("deployment_policy_id") != deployment.policy_id
            or platform.get("platform_status") != "LIVE_VALIDATED_RESOURCE_MATCHED"
            or platform.get("platform_formal_eligible") is not True
            or platform.get("llm_model", {}).get("revision")
            != deployment.revision
        ):
            raise RuntimeError("strict L1 platform identity mismatch")
        route = _read_json(route_path.resolve())
        target, manifest = _load_witness_episode()
        witness_selection = validate_witness_selection(target)
        witness_provenance = load_witness_provenance()
        before = _namespace_state(namespace)
        if {
            key: before[key] for key in EXPECTED_INITIAL_STATE
        } != EXPECTED_INITIAL_STATE:
            raise RuntimeError(f"preserved namespace state drift: {before}")
        _write_new(root / "namespace_before.json", before)
        phase["value"] = "AUTHENTICATING_WITNESS_SELECTION"
        runtime = build_formal_upstream_runtime(
            routing_contract=route,
            arm=FORMAL_ARM_A,
        )
        runtime_identity = formal_runtime_identity(
            runtime,
            mab8192_manifest_sha256=manifest.manifest_sha256,
        )
        _write_new(root / "runtime_identity.json", runtime_identity)
        from graphiti_core.prompts import prompt_library
        from graphiti_core.prompts.extract_edges import ExtractedEdges
        from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.provider_admission import (
            provider_scope,
        )

        schema = ExtractedEdges.model_json_schema()
        schema_text = json.dumps(
            schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        _write_new(root / "witness_selection.json", {
            "status": "PASS",
            **witness_selection,
            "provenance": witness_provenance,
            "target": {
                "global_sequence": target.source_sequence,
                "source_sequence": target.original_source_sequence,
                "chunk_ordinal": target.chunk_ordinal,
                "chunk_id": target.chunk_id,
            },
        })
        capture: dict[str, Any] = {}
        _install_recording_capture(runtime, capture)
        context = _witness_edge_context(target, namespace=namespace)
        messages = prompt_library.extract_edges.edge(context)
        phase["value"] = "SUBMITTING_NATIVE_EDGE_WITNESS"
        response_error: BaseException | None = None
        response_payload: Any = None
        with logical_request_context(_logical_identity(target)):
            with provider_scope(region="PREPARE", source_sequence=target.source_sequence):
                try:
                    response_payload = await runtime.llm_client.generate_response(
                        messages,
                        response_model=ExtractedEdges,
                        max_tokens=16384,
                        group_id=namespace,
                        prompt_name="extract_edges.edge",
                    )
                except BaseException as exc:
                    response_error = exc
        raw_response = capture.pop("_response", None)
        if raw_response is None:
            if response_error is not None:
                raise response_error
            raise RuntimeError("native edge witness produced no provider response")
        _write_new(root / "provider_response.json", _canonical(raw_response))
        evaluation = _evaluate_target_response(raw_response)
        wire_request = capture.get("wire_request")
        if not isinstance(wire_request, Mapping):
            raise RuntimeError("native edge witness request was not captured")
        wire_messages_sha256 = _wire_messages_sha256(wire_request.get("messages", ()))
        logical_identity = capture.get("logical_identity")
        response_schema = (
            wire_request.get("response_format", {})
            .get("json_schema", {})
            .get("schema")
            if isinstance(wire_request.get("response_format"), Mapping)
            else None
        )
        expected_seed = logical_request_seed({
            **_logical_identity(target),
            "prompt_name": "extract_edges.edge",
            "canonical_messages_hash": wire_messages_sha256,
        })
        telemetry = [
            row
            for row in getattr(runtime, "_membind_transport_telemetry", [])
            if isinstance(row, Mapping)
            and row.get("logical_identity", {}).get("prompt_name")
            == "extract_edges.edge"
        ]
        extra_body = wire_request.get("extra_body")
        extra_body = extra_body if isinstance(extra_body, Mapping) else {}
        observed_witness_edge = False
        try:
            parsed = json.loads(str(getattr(raw_response.choices[0].message, "content", "")))
            for edge in parsed.get("edges", []) if isinstance(parsed, Mapping) else []:
                if not isinstance(edge, Mapping):
                    continue
                source = str(edge.get("source_entity_name", "")).casefold()
                target_name = str(edge.get("target_entity_name", "")).casefold()
                if source in {name.casefold() for name in WITNESS_ENTITY_NAMES} and target_name in {
                    name.casefold() for name in WITNESS_ENTITY_NAMES
                } and source != target_name:
                    observed_witness_edge = True
                    break
        except (AttributeError, IndexError, TypeError, ValueError):
            observed_witness_edge = False
        request_checks = {
            "witness_entities_distinct": witness_selection["distinct_entity_count"] >= 2,
            "witness_entities_in_current_message": witness_selection[
                "current_message_contains_all_entities"
            ],
            "witness_provenance_authenticated": witness_provenance.get("status") == "PASS",
            "actual_upstream_schema": response_schema == schema,
            "model": wire_request.get("model") == deployment.served_model,
            "max_tokens": wire_request.get("max_tokens") == 16384,
            "structured_output_json_schema": wire_request.get("response_format", {}).get(
                "type"
            ) == "json_schema",
            "p2_temperature": wire_request.get("temperature") == deployment.sampling["temperature"],
            "p2_top_p": wire_request.get("top_p") == deployment.sampling["top_p"],
            "p2_presence_penalty": wire_request.get("presence_penalty")
            == deployment.sampling["presence_penalty"],
            "p2_extra_body": dict(extra_body) == {
                "chat_template_kwargs": {"enable_thinking": False},
                "min_p": 0,
                "top_k": deployment.sampling["top_k"],
            },
            "logical_identity": logical_identity
            == {
                **_logical_identity(target),
                "prompt_name": "extract_edges.edge",
            },
            "stable_seed": wire_request.get("seed") == expected_seed
            and (not telemetry or telemetry[0].get("seed") == expected_seed),
            "one_transport_attempt": len(telemetry) == 1,
            "observed_witness_edge": observed_witness_edge,
        }
        public_capture = {
            "source_capture": "official_mab8192_witness",
            "endpoint_id": capture.get("endpoint_id"),
            "wire_messages_sha256": wire_messages_sha256,
            "semantic_request_sha256": _semantic_request_sha256(wire_request),
            "after_request_sha256": request_hash(wire_request),
            "logical_identity": logical_identity,
            "wire_request": wire_request,
            "witness_selection": witness_selection,
            "witness_provenance": witness_provenance,
            "provider_request_count": capture.get("provider_request_count", 0),
        }
        _write_new(root / "captured_request.json", public_capture)
        if response_error is not None:
            evaluation["errors"].append(
                f"provider_parse:{type(response_error).__module__}.{type(response_error).__qualname__}"
            )
            evaluation["status"] = "FAIL"
        if not all(request_checks.values()):
            evaluation["status"] = "FAIL"
            evaluation["errors"].append(
                "witness_request_checks=" + json.dumps(request_checks, sort_keys=True)
            )
        phase["value"] = "VERIFYING_NO_MUTATION"
        after = _namespace_state(namespace)
        _write_new(root / "namespace_after_capture.json", after)
        if after != before:
            raise RuntimeError("direct L1 provider request mutated the preserved namespace")
        result.update(
            {
                "status": evaluation["status"],
                "target": {
                    "global_sequence": target.source_sequence,
                    "source_sequence": target.original_source_sequence,
                    "chunk_ordinal": target.chunk_ordinal,
                    "chunk_id": target.chunk_id,
                    "prompt_name": "extract_edges.edge",
                },
                "request_identity": public_capture,
                "request_checks": request_checks,
                "response": evaluation,
                "actual_upstream_schema": schema,
                "actual_upstream_schema_sha256": hashlib.sha256(
                    schema_text.encode("utf-8")
                ).hexdigest(),
                "runtime_identity": runtime_identity,
                "provider_retry_count": max(
                    0, int(capture.get("provider_request_count", 0)) - 1
                ),
                "target_provider_request_count": capture.get("provider_request_count", 0),
                "witness_provenance": witness_provenance,
                "namespace_unchanged_before_replay": True,
                "namespace_unchanged_after_provider_request": after == before,
                "ended_unix": time.time(),
            }
        )
    except Exception as exc:
        result.update(
            {
                "status": "FAIL",
                "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                "error": str(exc),
                "ended_unix": time.time(),
            }
        )
    finally:
        phase["value"] = "CLOSING"
        if runtime is not None:
            await close_formal_upstream_runtime(runtime)
        stop.set()
        await heartbeat
        _write_atomic(root / "heartbeat.json", {
            "status": "TERMINAL",
            "result_status": result["status"],
            "pid": os.getpid(),
            "phase": "TERMINAL",
            "updated_unix": time.time(),
        })
        _write_new(root / "L1_RESULT.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--namespace", default=PRESERVED_NAMESPACE)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--platform-manifest", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        run(
            root=args.root,
            namespace=args.namespace,
            route_path=args.route,
            platform_manifest=args.platform_manifest,
        )
    )
    print(json.dumps({"status": result["status"], "root": str(args.root)}, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
