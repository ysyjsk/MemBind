"""Bounded read-only service and namespace preflight for S4."""

from __future__ import annotations

import inspect
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256


CONSTRUCTION_BASE_URL = "http://10.87.5.247:8000/v1/"
CONSTRUCTION_SERVER_URL = "http://10.87.5.247:8000"
EMBEDDING_BASE_URL = "http://10.87.5.247:8001/v1"
CAPTURE_NAMESPACE = "pev3-s4-u0-capture-20260814-001"
REPLAY_NAMESPACE = "pev3-s4-d0-replay-20260814-001"
HISTORICAL_S1_NAMESPACE = "pev3-s1-20260814-001"

_EXPECTED_OBSERVATION_FIELDS = {
    "construction",
    "embedding",
    "neo4j_connectivity",
    "namespace_states",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def parse_single_model_card(value: object) -> dict[str, Any]:
    """Select one public model card and reject ambiguous service identity."""

    response = _mapping(value, label="models response")
    data = response.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], Mapping):
        raise ValueError("models response must contain exactly one model card")
    card = dict(data[0])
    model_id = card.get("id")
    if not isinstance(model_id, str) or not model_id:
        raise ValueError("model card is missing its served ID")
    selected: dict[str, Any] = {"served_model_id": model_id}
    if "max_model_len" in card:
        try:
            selected["max_model_len"] = int(card["max_model_len"])
        except (TypeError, ValueError) as error:
            raise ValueError("model card max_model_len is invalid") from error
    return selected


def _namespace_state(value: object, *, label: str) -> dict[str, Any]:
    state = _mapping(value, label=label)
    if set(state) != {"node_count", "relationship_count", "episode_names"}:
        raise ValueError(f"{label} shape drift")
    names = state["episode_names"]
    if not isinstance(names, Sequence) or isinstance(names, (str, bytes)):
        raise ValueError(f"{label} episode names are invalid")
    return {
        "node_count": int(state["node_count"]),
        "relationship_count": int(state["relationship_count"]),
        "episode_names": sorted(str(name) for name in names),
    }


def _empty(state: Mapping[str, Any]) -> bool:
    return (
        int(state["node_count"]) == 0
        and int(state["relationship_count"]) == 0
        and list(state["episode_names"]) == []
    )


def evaluate_s4_preflight(
    *,
    observations: Mapping[str, Any],
    expected_historical_s1_state: Mapping[str, Any],
    capture_namespace: str = CAPTURE_NAMESPACE,
    replay_namespace: str = REPLAY_NAMESPACE,
) -> dict[str, Any]:
    """Evaluate selected public identities without retaining raw responses."""

    selected = _mapping(observations, label="S4 preflight observations")
    if set(selected) != _EXPECTED_OBSERVATION_FIELDS:
        raise ValueError("S4 preflight observation shape drift")
    construction = _mapping(selected["construction"], label="construction identity")
    embedding = _mapping(selected["embedding"], label="embedding identity")
    if set(construction) != {"served_model_id", "vllm_version", "max_model_len"}:
        raise ValueError("construction identity shape drift")
    if set(embedding) != {"served_model_id"}:
        raise ValueError("embedding identity shape drift")
    construction = {
        "served_model_id": str(construction["served_model_id"]),
        "vllm_version": str(construction["vllm_version"]),
        "max_model_len": int(construction["max_model_len"]),
    }
    embedding = {"served_model_id": str(embedding["served_model_id"])}

    raw_states = _mapping(selected["namespace_states"], label="namespace states")
    if (
        not capture_namespace.startswith("pev3-s4-u0-capture-")
        or not replay_namespace.startswith("pev3-s4-d0-replay-")
        or capture_namespace == replay_namespace
    ):
        raise ValueError("preflight isolated namespace identity drift")
    expected_namespaces = {
        capture_namespace,
        replay_namespace,
        HISTORICAL_S1_NAMESPACE,
    }
    if set(raw_states) != expected_namespaces:
        raise ValueError("preflight namespace inventory drift")
    states = {
        namespace: _namespace_state(raw_states[namespace], label=namespace)
        for namespace in sorted(raw_states)
    }
    expected_s1 = _namespace_state(
        expected_historical_s1_state,
        label="expected historical S1 state",
    )

    failures: list[str] = []
    if construction["served_model_id"] != "qwen3-32b-fp8":
        failures.append("construction_model")
    if construction["vllm_version"] != "0.26.0":
        failures.append("vllm_version")
    if construction["max_model_len"] < 65536:
        failures.append("max_model_len")
    if embedding["served_model_id"] != "qwen3-embedding-0.6b":
        failures.append("embedding_model")
    neo4j_ok = selected["neo4j_connectivity"] is True
    if not neo4j_ok:
        failures.append("neo4j_connectivity")

    capture_empty = _empty(states[capture_namespace])
    replay_empty = _empty(states[replay_namespace])
    historical_unchanged = states[HISTORICAL_S1_NAMESPACE] == expected_s1
    if not capture_empty:
        failures.append("capture_namespace_not_empty")
    if not replay_empty:
        failures.append("replay_namespace_not_empty")
    if not historical_unchanged:
        failures.append("historical_s1_namespace_drift")

    passed = not failures
    return {
        "schema_version": "membind.paper-eval-v3.s4-preflight-evaluation.v1",
        "verdict": "PASS" if passed else "FAIL",
        "failures": failures,
        "construction": construction,
        "embedding": embedding,
        "neo4j_connectivity": "PASS" if neo4j_ok else "FAIL",
        "namespace_checks": {
            "capture_empty": capture_empty,
            "replay_empty": replay_empty,
            "historical_s1_unchanged": historical_unchanged,
        },
        "namespace_state_sha256": {
            "capture": payload_sha256(states[capture_namespace]),
            "replay": payload_sha256(states[replay_namespace]),
            "historical_s1_actual": payload_sha256(
                states[HISTORICAL_S1_NAMESPACE]
            ),
            "historical_s1_expected": payload_sha256(expected_s1),
        },
        "authority": {
            "s4_authority_creation_authorized": passed,
            "s4_live_execution_authorized": False,
            "pilot_execution_authorized": False,
        },
    }


async def _await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def collect_s4_preflight(
    *,
    get_json: Callable[[str, str], Awaitable[Mapping[str, Any]] | Mapping[str, Any]],
    neo4j_connectivity: Callable[[], Awaitable[bool] | bool],
    namespace_state: Callable[[str], Awaitable[Mapping[str, Any]] | Mapping[str, Any]],
    expected_historical_s1_state: Mapping[str, Any],
    capture_namespace: str = CAPTURE_NAMESPACE,
    replay_namespace: str = REPLAY_NAMESPACE,
) -> dict[str, Any]:
    """Collect exactly three HTTP reads and four bounded Neo4j reads."""

    construction_models = parse_single_model_card(
        await _await(get_json(CONSTRUCTION_BASE_URL, "/models"))
    )
    version_response = _mapping(
        await _await(get_json(CONSTRUCTION_SERVER_URL, "/version")),
        label="vLLM version response",
    )
    if set(version_response) != {"version"} or not isinstance(
        version_response["version"], str
    ):
        raise ValueError("vLLM version response shape drift")
    embedding_models = parse_single_model_card(
        await _await(get_json(EMBEDDING_BASE_URL, "/models"))
    )
    if "max_model_len" not in construction_models:
        raise ValueError("construction model card lacks max_model_len")

    neo4j_ok = await _await(neo4j_connectivity())
    states: dict[str, Mapping[str, Any]] = {}
    for namespace in (
        capture_namespace,
        replay_namespace,
        HISTORICAL_S1_NAMESPACE,
    ):
        states[namespace] = await _await(namespace_state(namespace))

    return evaluate_s4_preflight(
        observations={
            "construction": {
                **construction_models,
                "vllm_version": version_response["version"],
            },
            "embedding": {
                "served_model_id": embedding_models["served_model_id"]
            },
            "neo4j_connectivity": neo4j_ok,
            "namespace_states": states,
        },
        expected_historical_s1_state=expected_historical_s1_state,
        capture_namespace=capture_namespace,
        replay_namespace=replay_namespace,
    )


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is not a SHA256")
    return value


def _reject_private_fields(value: object) -> None:
    forbidden = {
        "answer",
        "api_key",
        "content",
        "episode_names",
        "messages",
        "password",
        "prompt",
        "question",
        "raw_output",
        "raw_response",
        "secret",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in forbidden:
                raise ValueError("preflight artifact contains private runtime data")
            _reject_private_fields(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private_fields(child)


def verify_s4_preflight(value: Mapping[str, Any]) -> dict[str, Any]:
    """Verify the sealed PASS artifact and its non-live authority boundary."""

    artifact = _mapping(value, label="S4 preflight artifact")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise ValueError("S4 preflight envelope shape drift")
    payload = _mapping(artifact.get("payload"), label="S4 preflight payload")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise ValueError("S4 preflight envelope hash or identity drift")
    if set(payload) != {
        "schema_version",
        "stage",
        "verdict",
        "s4_contract_file_sha256",
        "s4_contract_sha256",
        "s1_checkpoint_file_sha256",
        "evaluation",
        "source_sha256",
        "authority",
    }:
        raise ValueError("S4 preflight payload shape drift")
    if (
        payload.get("schema_version")
        != "membind.paper-eval-v3.s4-preflight-artifact.v1"
        or payload.get("stage") != "S4_PREFLIGHT"
        or payload.get("verdict") != "PASS"
    ):
        raise ValueError("S4 preflight verdict or schema drift")
    for field in (
        "s4_contract_file_sha256",
        "s4_contract_sha256",
        "s1_checkpoint_file_sha256",
    ):
        _sha(payload.get(field), field=field)
    sources = _mapping(payload.get("source_sha256"), label="preflight sources")
    if set(sources) != {"preflight", "production", "test"}:
        raise ValueError("S4 preflight source inventory drift")
    for name, source_sha in sources.items():
        _sha(source_sha, field=f"source {name}")
    evaluation = _mapping(payload.get("evaluation"), label="preflight evaluation")
    if (
        evaluation.get("schema_version")
        != "membind.paper-eval-v3.s4-preflight-evaluation.v1"
        or evaluation.get("verdict") != "PASS"
        or evaluation.get("failures") != []
        or evaluation.get("authority")
        != {
            "s4_authority_creation_authorized": True,
            "s4_live_execution_authorized": False,
            "pilot_execution_authorized": False,
        }
    ):
        raise ValueError("S4 preflight evaluation is not a bounded PASS")
    authority = _mapping(payload.get("authority"), label="preflight authority")
    if authority != evaluation["authority"]:
        raise ValueError("S4 preflight authority drift")
    _reject_private_fields(payload)
    artifact["payload"] = payload
    return artifact


def finalize_s4_preflight(
    *,
    output_path: Any,
    evaluation: Mapping[str, Any],
    s4_contract_file_sha256: str,
    s4_contract_sha256: str,
    s1_checkpoint_file_sha256: str,
    source_sha256: Mapping[str, str],
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    """Seal one PASS preflight without storing raw service or graph records."""

    selected_evaluation = _mapping(evaluation, label="preflight evaluation")
    body = {
        "schema_version": "membind.paper-eval-v3.s4-preflight-artifact.v1",
        "stage": "S4_PREFLIGHT",
        "verdict": selected_evaluation.get("verdict"),
        "s4_contract_file_sha256": _sha(
            s4_contract_file_sha256,
            field="S4 contract file",
        ),
        "s4_contract_sha256": _sha(s4_contract_sha256, field="S4 contract"),
        "s1_checkpoint_file_sha256": _sha(
            s1_checkpoint_file_sha256,
            field="S1 checkpoint file",
        ),
        "evaluation": selected_evaluation,
        "source_sha256": dict(source_sha256),
        "authority": deepcopy(selected_evaluation.get("authority")),
    }
    artifact = verify_s4_preflight(
        finalize_envelope(
            payload=body,
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id=str(run_id),
        )
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(artifact, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return artifact
