"""Method-specific, read-only S5 live-preflight contract.

This module evaluates sanitized observations and seals a PASS artifact.  It
does not create clients, access a service, create a namespace, or grant live
execution.  Production reads are supplied through injected callables.
"""

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
from .s5_production_identity_qualification import (
    S5ProductionIdentityQualificationError,
    bind_s5_production_identity_qualification,
    verify_s5_production_identity_qualification_binding,
)


CONSTRUCTION_BASE_URL = "http://10.87.5.247:8000/v1/"
CONSTRUCTION_SERVER_URL = "http://10.87.5.247:8000"
EMBEDDING_BASE_URL = "http://10.87.5.247:8001/v1"
HISTORY_ID = "07741c45"
HISTORY_EPISODE_COUNT = 49

EVALUATION_SCHEMA = "membind.paper-eval-v3.s5-live-preflight-evaluation.v1"
ARTIFACT_SCHEMA = "membind.paper-eval-v3.s5-live-preflight-artifact.v1"

_METHOD_RUN_PREFIX = {
    "A0": "s5-a0-",
    "P*": "s5-p-star-",
    "M*": "s5-mstar-",
}
_PREDECESSOR = {"A0": None, "P*": "A0", "M*": "P*"}
_PREDECESSOR_VERDICT = {
    "A0": None,
    "P*": "PASS",
    "M*": "SCIENTIFIC_OUTCOME_COMPLETE",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_OBSERVATION_FIELDS = {
    "construction",
    "embedding",
    "neo4j_connectivity",
    "namespace",
    "namespace_state",
}
_AUTHORITY_PASS = {
    "s5_live_authority_creation_authorized": True,
    "s5_live_execution_authorized": False,
    "pilot_execution_authorized": False,
    "formal_execution_authorized": False,
    "current_stage_pointer_update_authorized": False,
}
_AUTHORITY_FAIL = {**_AUTHORITY_PASS, "s5_live_authority_creation_authorized": False}
_FX0_AUTHORITY = {
    "model_call_authorized": False,
    "neo4j_read_authorized": False,
    "neo4j_mutation_authorized": False,
    "s5_live_execution_authorized": False,
    "current_stage_pointer_update_authorized": False,
}
_PRIVATE_FIELDS = {
    "answer",
    "api_key",
    "authorization",
    "content",
    "credential",
    "episode_names",
    "messages",
    "password",
    "prompt",
    "question",
    "raw_output",
    "raw_response",
    "request",
    "response",
    "secret",
}


class S5LivePreflightError(ValueError):
    """The preflight input, binding, evaluation, or artifact is invalid."""


def _fail(code: str) -> S5LivePreflightError:
    return S5LivePreflightError(code)


def _mapping(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    return deepcopy(dict(value))


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_preflight_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


def _identity_qualification(
    *,
    method: str,
    value: object,
    file_sha256: str,
    current_pointer: Mapping[str, str],
) -> dict[str, object]:
    try:
        binding = bind_s5_production_identity_qualification(
            _mapping(value, "identity_qualification_invalid"),
            file_sha256=file_sha256,
        )
    except (S5ProductionIdentityQualificationError, S5LivePreflightError):
        raise _fail("production_identity_qualification_invalid") from None
    qualification_pointer = binding["current_stage_pointer"]
    qualification_freeze = binding["native_baseline_freeze"]
    if (
        binding["method"] != method
        or not isinstance(qualification_pointer, Mapping)
        or qualification_pointer.get("file_sha256")
        != current_pointer.get("file_sha256")
        or qualification_pointer.get("payload_sha256")
        != current_pointer.get("payload_sha256")
        or not isinstance(qualification_freeze, Mapping)
        or qualification_freeze.get("file_sha256")
        != current_pointer.get("native_baseline_freeze_file_sha256")
        or qualification_freeze.get("payload_sha256")
        != current_pointer.get("native_baseline_freeze_payload_sha256")
    ):
        raise _fail("production_identity_qualification_binding_mismatch")
    return binding


def _pointer(value: object, *, file_sha256: str) -> dict[str, str]:
    pointer = _mapping(value, "current_pointer_invalid")
    payload = _mapping(pointer.get("payload"), "current_pointer_invalid")
    if (
        set(pointer)
        != {
            "protocol_version",
            "git_commit",
            "run_id",
            "status",
            "payload",
            "payload_sha256",
        }
        or pointer.get("protocol_version") != PROTOCOL_VERSION
        or pointer.get("status") != "finalized"
        or pointer.get("payload_sha256") != payload_sha256(payload)
        or payload.get("schema_version")
        != "membind.paper-eval-v3.current-stage-pointer.v2"
        or payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or payload.get("live_preflight_required") is not True
        or _SHA256.fullmatch(
            str(payload.get("native_baseline_v2_freeze_file_sha256", ""))
        )
        is None
        or _SHA256.fullmatch(
            str(payload.get("native_baseline_v2_freeze_payload_sha256", ""))
        )
        is None
        or not isinstance(pointer.get("run_id"), str)
        or not pointer["run_id"]
    ):
        raise _fail("current_pointer_invalid")
    return {
        "run_id": str(pointer["run_id"]),
        "current_stage": "S3_CONFIGURATION_FROZEN",
        "file_sha256": _sha(file_sha256, "current_pointer_file_sha256_invalid"),
        "payload_sha256": str(pointer["payload_sha256"]),
        "native_baseline_freeze_file_sha256": str(
            payload["native_baseline_v2_freeze_file_sha256"]
        ),
        "native_baseline_freeze_payload_sha256": str(
            payload["native_baseline_v2_freeze_payload_sha256"]
        ),
    }


def _predecessor(method: str, value: object | None) -> dict[str, str] | None:
    expected = _PREDECESSOR[method]
    expected_verdict = _PREDECESSOR_VERDICT[method]
    if expected is None:
        if value is not None:
            raise _fail("predecessor_forbidden")
        return None
    predecessor = _mapping(value, "predecessor_invalid")
    if (
        set(predecessor) != {"method", "verdict", "artifact_sha256"}
        or predecessor.get("method") != expected
        or predecessor.get("verdict") != expected_verdict
    ):
        raise _fail("predecessor_invalid")
    return {
        "method": expected,
        "verdict": str(expected_verdict),
        "artifact_sha256": _sha(
            predecessor.get("artifact_sha256"), "predecessor_invalid"
        ),
    }


def _fx0_binding(
    *,
    method: str,
    value: object | None,
    current_pointer_file_sha256: str,
    production_identity_qualification: Mapping[str, object],
) -> dict[str, str] | None:
    if method != "M*":
        if value is not None:
            raise _fail("fx0_qualification_forbidden")
        return None
    if value is None:
        raise _fail("fx0_qualification_required")
    qualification_fx0 = production_identity_qualification.get("mstar_fx0")
    if not isinstance(qualification_fx0, Mapping):
        raise _fail("fx0_qualification_invalid")
    artifact = _mapping(value, "fx0_qualification_invalid")
    payload = _mapping(artifact.get("payload"), "fx0_qualification_invalid")
    if (
        set(artifact)
        != {
            "protocol_version",
            "git_commit",
            "run_id",
            "status",
            "payload",
            "payload_sha256",
        }
        or artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or payload.get("schema_version")
        != "membind.paper-eval-v3.s5-graphiti-fx0-production-qualification.v1"
        or payload.get("verdict") != "PRODUCTION_PATH_EXACT_PARITY_PASS"
        or payload.get("fixture_count") != 11
        or payload.get("authority") != _FX0_AUTHORITY
        or payload.get("current_stage_pointer_sha256")
        != current_pointer_file_sha256
        or payload.get("fx0_artifact_payload_sha256")
        != qualification_fx0.get("fx0_artifact_payload_sha256")
        or artifact.get("payload_sha256")
        != qualification_fx0.get("qualification_payload_sha256")
    ):
        raise _fail("fx0_qualification_invalid")
    for field in (
        "production_core_identity_sha256",
        "fx0_artifact_payload_sha256",
        "runtime_config_sha256",
    ):
        _sha(payload.get(field), "fx0_qualification_invalid")
    return {
        "run_id": str(artifact["run_id"]),
        "verdict": "PRODUCTION_PATH_EXACT_PARITY_PASS",
        "qualification_payload_sha256": str(artifact["payload_sha256"]),
        "fx0_artifact_payload_sha256": str(
            payload["fx0_artifact_payload_sha256"]
        ),
    }


def _model_card(value: object, *, require_context: bool) -> dict[str, Any]:
    response = _mapping(value, "models_response_invalid")
    data = response.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], Mapping):
        raise _fail("models_response_invalid")
    model_id = data[0].get("id")
    if not isinstance(model_id, str) or not model_id:
        raise _fail("models_response_invalid")
    result: dict[str, Any] = {"served_model_id": model_id}
    if require_context:
        try:
            result["max_model_len"] = int(data[0]["max_model_len"])
        except (KeyError, TypeError, ValueError):
            raise _fail("models_response_invalid") from None
    return result


def _namespace_state(value: object) -> dict[str, int]:
    state = _mapping(value, "namespace_state_invalid")
    if set(state) != {"node_count", "relationship_count"}:
        raise _fail("namespace_state_invalid")
    try:
        selected = {
            "node_count": int(state["node_count"]),
            "relationship_count": int(state["relationship_count"]),
        }
    except (TypeError, ValueError):
        raise _fail("namespace_state_invalid") from None
    if any(value < 0 for value in selected.values()):
        raise _fail("namespace_state_invalid")
    return selected


def _workload(value: Sequence[str]) -> dict[str, object]:
    hashes = tuple(value)
    if (
        len(hashes) != HISTORY_EPISODE_COUNT
        or any(not isinstance(item, str) or _SHA256.fullmatch(item) is None for item in hashes)
    ):
        raise _fail("source_manifest_invalid")
    return {
        "history_id": HISTORY_ID,
        "episode_count": HISTORY_EPISODE_COUNT,
        "source_manifest_sha256": payload_sha256(
            [
                {"source_sequence": index, "source_sha256": digest}
                for index, digest in enumerate(hashes)
            ]
        ),
    }


def evaluate_s5_live_preflight(
    *,
    method: str,
    run_id: str,
    namespace: str,
    episode_source_sha256s: Sequence[str],
    observations: Mapping[str, Any],
    production_identity_qualification: Mapping[str, object],
    production_identity_qualification_file_sha256: str,
    current_stage_pointer: Mapping[str, object],
    current_stage_pointer_file_sha256: str,
    predecessor: Mapping[str, object] | None = None,
    fx0_qualification: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Evaluate one method-specific S5 preflight from sanitized observations."""

    if method not in _METHOD_RUN_PREFIX:
        raise _fail("method_invalid")
    if (
        not isinstance(run_id, str)
        or _RUN_ID.fullmatch(run_id) is None
        or not run_id.startswith(_METHOD_RUN_PREFIX[method])
        or namespace != f"pev3-{run_id}"
    ):
        raise _fail("namespace_identity_invalid")
    selected = _mapping(observations, "observations_invalid")
    if set(selected) != _OBSERVATION_FIELDS or selected.get("namespace") != namespace:
        raise _fail("observation_shape_invalid")
    construction = _mapping(selected["construction"], "construction_invalid")
    embedding = _mapping(selected["embedding"], "embedding_invalid")
    if set(construction) != {"served_model_id", "vllm_version", "max_model_len"}:
        raise _fail("construction_invalid")
    if set(embedding) != {"served_model_id"}:
        raise _fail("embedding_invalid")
    try:
        construction_public = {
            "served_model_id": str(construction["served_model_id"]),
            "vllm_version": str(construction["vllm_version"]),
            "max_model_len": int(construction["max_model_len"]),
        }
    except (TypeError, ValueError):
        raise _fail("construction_invalid") from None
    embedding_public = {"served_model_id": str(embedding["served_model_id"])}
    state = _namespace_state(selected["namespace_state"])
    workload = _workload(episode_source_sha256s)
    pointer = _pointer(
        current_stage_pointer, file_sha256=current_stage_pointer_file_sha256
    )
    identity_qualification = _identity_qualification(
        method=method,
        value=production_identity_qualification,
        file_sha256=production_identity_qualification_file_sha256,
        current_pointer=pointer,
    )
    predecessor_binding = _predecessor(method, predecessor)
    fx0_binding = _fx0_binding(
        method=method,
        value=fx0_qualification,
        current_pointer_file_sha256=current_stage_pointer_file_sha256,
        production_identity_qualification=identity_qualification,
    )

    failures: list[str] = []
    if construction_public["served_model_id"] != "qwen3-32b-fp8":
        failures.append("construction_model")
    if construction_public["vllm_version"] != "0.26.0":
        failures.append("vllm_version")
    if construction_public["max_model_len"] < 65536:
        failures.append("max_model_len")
    if embedding_public["served_model_id"] != "qwen3-embedding-0.6b":
        failures.append("embedding_model")
    if selected["neo4j_connectivity"] is not True:
        failures.append("neo4j_connectivity")
    namespace_empty = state == {"node_count": 0, "relationship_count": 0}
    if not namespace_empty:
        failures.append("namespace_not_empty")
    passed = not failures
    evaluation = {
        "schema_version": EVALUATION_SCHEMA,
        "verdict": "PASS" if passed else "FAIL",
        "failures": failures,
        "method": method,
        "run_id": run_id,
        "namespace": namespace,
        "workload": workload,
        "production_identity_qualification": identity_qualification,
        "current_stage_pointer": pointer,
        "predecessor": predecessor_binding,
        "fx0_qualification": fx0_binding,
        "construction": construction_public,
        "embedding": embedding_public,
        "neo4j_connectivity": "PASS" if selected["neo4j_connectivity"] is True else "FAIL",
        "namespace_check": {
            "empty": namespace_empty,
            "state_sha256": payload_sha256(state),
        },
        "authority": deepcopy(_AUTHORITY_PASS if passed else _AUTHORITY_FAIL),
    }
    _assert_public(evaluation)
    return evaluation


async def _await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def collect_s5_live_preflight(
    *,
    method: str,
    run_id: str,
    namespace: str,
    episode_source_sha256s: Sequence[str],
    production_identity_qualification: Mapping[str, object],
    production_identity_qualification_file_sha256: str,
    current_stage_pointer: Mapping[str, object],
    current_stage_pointer_file_sha256: str,
    get_json: Callable[
        [str, str], Awaitable[Mapping[str, Any]] | Mapping[str, Any]
    ],
    neo4j_connectivity: Callable[[], Awaitable[bool] | bool],
    namespace_state: Callable[
        [str], Awaitable[Mapping[str, Any]] | Mapping[str, Any]
    ],
    predecessor: Mapping[str, object] | None = None,
    fx0_qualification: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Collect exactly three HTTP reads and two bounded Neo4j reads."""

    pointer = _pointer(
        current_stage_pointer, file_sha256=current_stage_pointer_file_sha256
    )
    _identity_qualification(
        method=method,
        value=production_identity_qualification,
        file_sha256=production_identity_qualification_file_sha256,
        current_pointer=pointer,
    )
    construction = _model_card(
        await _await(get_json(CONSTRUCTION_BASE_URL, "/models")),
        require_context=True,
    )
    version = _mapping(
        await _await(get_json(CONSTRUCTION_SERVER_URL, "/version")),
        "version_response_invalid",
    )
    if set(version) != {"version"} or not isinstance(version["version"], str):
        raise _fail("version_response_invalid")
    embedding = _model_card(
        await _await(get_json(EMBEDDING_BASE_URL, "/models")),
        require_context=False,
    )
    connectivity = await _await(neo4j_connectivity())
    state = await _await(namespace_state(namespace))
    return evaluate_s5_live_preflight(
        method=method,
        run_id=run_id,
        namespace=namespace,
        episode_source_sha256s=episode_source_sha256s,
        observations={
            "construction": {**construction, "vllm_version": version["version"]},
            "embedding": embedding,
            "neo4j_connectivity": connectivity,
            "namespace": namespace,
            "namespace_state": state,
        },
        production_identity_qualification=production_identity_qualification,
        production_identity_qualification_file_sha256=(
            production_identity_qualification_file_sha256
        ),
        current_stage_pointer=current_stage_pointer,
        current_stage_pointer_file_sha256=current_stage_pointer_file_sha256,
        predecessor=predecessor,
        fx0_qualification=fx0_qualification,
    )


def _validate_evaluation(value: object, *, require_pass: bool) -> dict[str, Any]:
    evaluation = _mapping(value, "evaluation_invalid")
    expected_fields = {
        "schema_version",
        "verdict",
        "failures",
        "method",
        "run_id",
        "namespace",
        "workload",
        "production_identity_qualification",
        "current_stage_pointer",
        "predecessor",
        "fx0_qualification",
        "construction",
        "embedding",
        "neo4j_connectivity",
        "namespace_check",
        "authority",
    }
    if set(evaluation) != expected_fields or evaluation.get("schema_version") != EVALUATION_SCHEMA:
        raise _fail("evaluation_invalid")
    method = evaluation.get("method")
    run_id = evaluation.get("run_id")
    if (
        method not in _METHOD_RUN_PREFIX
        or not isinstance(run_id, str)
        or not run_id.startswith(_METHOD_RUN_PREFIX[method])
        or evaluation.get("namespace") != f"pev3-{run_id}"
    ):
        raise _fail("evaluation_binding_invalid")
    workload = _mapping(evaluation.get("workload"), "evaluation_workload_invalid")
    if (
        set(workload)
        != {"history_id", "episode_count", "source_manifest_sha256"}
        or workload.get("history_id") != HISTORY_ID
        or workload.get("episode_count") != HISTORY_EPISODE_COUNT
    ):
        raise _fail("evaluation_workload_invalid")
    _sha(workload.get("source_manifest_sha256"), "evaluation_workload_invalid")
    try:
        identity_qualification = verify_s5_production_identity_qualification_binding(
            _mapping(
                evaluation.get("production_identity_qualification"),
                "evaluation_identity_qualification_invalid",
            )
        )
    except S5ProductionIdentityQualificationError:
        raise _fail("evaluation_identity_qualification_invalid") from None
    pointer = _mapping(evaluation.get("current_stage_pointer"), "evaluation_pointer_invalid")
    if set(pointer) != {
        "run_id",
        "current_stage",
        "file_sha256",
        "payload_sha256",
        "native_baseline_freeze_file_sha256",
        "native_baseline_freeze_payload_sha256",
    }:
        raise _fail("evaluation_pointer_invalid")
    _sha(pointer.get("file_sha256"), "evaluation_pointer_invalid")
    _sha(pointer.get("payload_sha256"), "evaluation_pointer_invalid")
    qualification_pointer = identity_qualification["current_stage_pointer"]
    qualification_freeze = identity_qualification["native_baseline_freeze"]
    if (
        pointer.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or identity_qualification.get("method") != method
        or qualification_pointer.get("file_sha256") != pointer.get("file_sha256")
        or qualification_pointer.get("payload_sha256")
        != pointer.get("payload_sha256")
        or qualification_freeze.get("file_sha256")
        != pointer.get("native_baseline_freeze_file_sha256")
        or qualification_freeze.get("payload_sha256")
        != pointer.get("native_baseline_freeze_payload_sha256")
    ):
        raise _fail("evaluation_pointer_invalid")
    expected_predecessor = _PREDECESSOR[method]
    expected_predecessor_verdict = _PREDECESSOR_VERDICT[method]
    actual_predecessor = evaluation.get("predecessor")
    if expected_predecessor is None:
        if actual_predecessor is not None:
            raise _fail("evaluation_predecessor_invalid")
    else:
        selected_predecessor = _mapping(
            actual_predecessor, "evaluation_predecessor_invalid"
        )
        if (
            selected_predecessor.get("method") != expected_predecessor
            or selected_predecessor.get("verdict")
            != expected_predecessor_verdict
        ):
            raise _fail("evaluation_predecessor_invalid")
        _sha(
            selected_predecessor.get("artifact_sha256"),
            "evaluation_predecessor_invalid",
        )
    if (method == "M*") != (evaluation.get("fx0_qualification") is not None):
        raise _fail("evaluation_fx0_invalid")
    if method == "M*":
        fx0 = _mapping(evaluation["fx0_qualification"], "evaluation_fx0_invalid")
        if fx0.get("verdict") != "PRODUCTION_PATH_EXACT_PARITY_PASS":
            raise _fail("evaluation_fx0_invalid")
        for field in ("qualification_payload_sha256", "fx0_artifact_payload_sha256"):
            _sha(fx0.get(field), "evaluation_fx0_invalid")
    verdict = evaluation.get("verdict")
    failures = evaluation.get("failures")
    authority = evaluation.get("authority")
    if require_pass and (
        verdict != "PASS" or failures != [] or authority != _AUTHORITY_PASS
    ):
        raise _fail("evaluation_not_pass")
    if verdict == "FAIL" and authority != _AUTHORITY_FAIL:
        raise _fail("evaluation_fail_authority_invalid")
    _assert_public(evaluation)
    return evaluation


def verify_s5_live_preflight(value: Mapping[str, Any]) -> dict[str, Any]:
    """Verify one sanitized, exclusive PASS preflight artifact."""

    artifact = _mapping(value, "artifact_invalid")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise _fail("artifact_shape_invalid")
    payload = _mapping(artifact.get("payload"), "artifact_payload_invalid")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise _fail("artifact_identity_invalid")
    if (
        set(payload)
        != {
            "schema_version",
            "stage",
            "verdict",
            "method",
            "run_id",
            "namespace",
            "workload",
            "production_identity_qualification",
            "current_stage_pointer",
            "predecessor",
            "fx0_qualification",
            "evaluation",
            "evaluation_sha256",
            "source_sha256",
            "authority",
        }
        or payload.get("schema_version") != ARTIFACT_SCHEMA
        or payload.get("stage") != "S5_LIVE_PREFLIGHT"
        or payload.get("verdict") != "PASS"
    ):
        raise _fail("artifact_identity_invalid")
    evaluation = _validate_evaluation(payload.get("evaluation"), require_pass=True)
    if (
        payload.get("evaluation_sha256") != payload_sha256(evaluation)
        or artifact.get("run_id") != evaluation["run_id"]
        or payload.get("run_id") != evaluation["run_id"]
        or payload.get("method") != evaluation["method"]
        or payload.get("namespace") != evaluation["namespace"]
        or payload.get("workload") != evaluation["workload"]
        or payload.get("production_identity_qualification")
        != evaluation["production_identity_qualification"]
        or payload.get("current_stage_pointer")
        != evaluation["current_stage_pointer"]
        or payload.get("predecessor") != evaluation["predecessor"]
        or payload.get("fx0_qualification") != evaluation["fx0_qualification"]
        or payload.get("authority") != _AUTHORITY_PASS
    ):
        raise _fail("artifact_binding_invalid")
    sources = _mapping(payload.get("source_sha256"), "artifact_sources_invalid")
    if set(sources) != {
        "contract",
        "production",
        "contract_test",
        "production_test",
    }:
        raise _fail("artifact_sources_invalid")
    for name, digest in sources.items():
        _sha(digest, f"artifact_source_invalid:{name}")
    _assert_public(payload)
    artifact["payload"] = payload
    return artifact


def finalize_s5_live_preflight(
    *,
    output_path: Path,
    evaluation: Mapping[str, Any],
    source_sha256: Mapping[str, str],
    git_commit: str,
) -> dict[str, Any]:
    """Seal one PASS preflight with O_EXCL; never overwrite prior evidence."""

    checked = _validate_evaluation(evaluation, require_pass=True)
    payload = {
        "schema_version": ARTIFACT_SCHEMA,
        "stage": "S5_LIVE_PREFLIGHT",
        "verdict": "PASS",
        "method": checked["method"],
        "run_id": checked["run_id"],
        "namespace": checked["namespace"],
        "workload": deepcopy(checked["workload"]),
        "production_identity_qualification": deepcopy(
            checked["production_identity_qualification"]
        ),
        "current_stage_pointer": deepcopy(checked["current_stage_pointer"]),
        "predecessor": deepcopy(checked["predecessor"]),
        "fx0_qualification": deepcopy(checked["fx0_qualification"]),
        "evaluation": checked,
        "evaluation_sha256": payload_sha256(checked),
        "source_sha256": deepcopy(dict(source_sha256)),
        "authority": deepcopy(_AUTHORITY_PASS),
    }
    artifact = verify_s5_live_preflight(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id=str(checked["run_id"]),
        )
    )
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(artifact, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return artifact


__all__ = [
    "ARTIFACT_SCHEMA",
    "CONSTRUCTION_BASE_URL",
    "CONSTRUCTION_SERVER_URL",
    "EMBEDDING_BASE_URL",
    "EVALUATION_SCHEMA",
    "HISTORY_EPISODE_COUNT",
    "HISTORY_ID",
    "S5LivePreflightError",
    "collect_s5_live_preflight",
    "evaluate_s5_live_preflight",
    "finalize_s5_live_preflight",
    "verify_s5_live_preflight",
]
