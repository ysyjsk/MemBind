"""Pure Gate-C builder and exclusive finalizer for the Native U0 freeze.

Gate C consumes only already sealed S1/S2 evidence plus an explicit one-shot
authority.  It performs no network, database, model, Reader, or Judge I/O.
The builder is pure; the optional finalizer performs one exclusive file create
and therefore cannot overwrite a historical freeze.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .artifacts import finalize_envelope, payload_sha256


FREEZE_SCHEMA = "membind.paper-eval-v3.native-baseline-freeze.v1"
S2_COMPLETION_SCHEMA = "membind.paper-eval-v3.s2-completion.v1"
AUTHORITY_SCHEMA = "membind.paper-eval-v3.s3-freeze-authority.v1"
ADAPTER_SCHEMA_V2 = "membind.paper-eval-v3.s2-adapter-identity.v2"
FREEZE_ACTION = "FINALIZE_NATIVE_U0_FREEZE_ONCE"

REQUIRED_RUNTIME_IDENTITIES = (
    "graphiti",
    "construction",
    "embedding",
    "neo4j",
    "vllm",
)
REQUIRED_SOURCE_BINDINGS = (
    "graphiti_source",
    "construction_adapter_source",
    "embedding_adapter_source",
    "retrieval_adapter_source",
    "reader_adapter_source",
    "judge_adapter_source",
    "instrumentation_source",
    "s3_freeze_source",
)
REQUIRED_EXECUTION_BINDINGS = (
    "prompt_schema_sha256",
    "retry_policy_sha256",
    "pooling_config_sha256",
    "cache_policy_sha256",
    "instrumentation_config_sha256",
)
ROLE_NAMES = ("DEVELOPMENT_EXPOSED", "PILOT", "FINAL_PAPER_TEST")

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET_OR_RAW_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "password",
        "secret",
        "access_token",
        "bearer_token",
        "authorization_header",
        "question",
        "answer",
        "prompt",
        "messages",
        "raw_output",
        "raw_content",
        "episode_content",
        "content",
    }
)
_OUTCOME_KEYS = frozenset(
    {
        "outcome",
        "result",
        "method_result",
        "method_results",
        "winner",
        "selected_method",
        "score",
        "accuracy",
        "recall",
        "latency",
        "throughput",
        "makespan",
        "quality",
        "performance",
        "treatment_effect",
    }
)


def _sha(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is not a SHA256")
    return value


def _nonempty(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be nonempty")
    return value


def _sealed_envelope(value: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} envelope is invalid")
    envelope = dict(value)
    payload = envelope.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} envelope is invalid")
    body = dict(payload)
    if envelope.get("status") != "finalized":
        raise ValueError(f"{label} envelope is not finalized")
    if envelope.get("payload_sha256") != payload_sha256(body):
        raise ValueError(f"{label} payload hash mismatch")
    envelope["payload"] = body
    return envelope


def _walk_keys(value: Any) -> Sequence[str]:
    keys: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            keys.append(str(key).lower())
            keys.extend(_walk_keys(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            keys.extend(_walk_keys(child))
    return keys


def _reject_unsafe_binding_content(bindings: Mapping[str, Any]) -> None:
    keys = set(_walk_keys(bindings))
    if keys & _SECRET_OR_RAW_KEYS:
        raise ValueError("Gate C binding contains secret or raw content")
    if keys & _OUTCOME_KEYS:
        raise ValueError("Gate C outcome or method-result contamination")
    serialized = json.dumps(bindings, ensure_ascii=False, sort_keys=True).lower()
    if "bearer " in serialized or "-----begin private key-----" in serialized:
        raise ValueError("Gate C binding contains secret or raw content")


def _runtime_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Gate C runtime identity is incomplete")
    result = dict(value)
    if any(
        not isinstance(result.get(name), Mapping) or not result[name]
        for name in REQUIRED_RUNTIME_IDENTITIES
    ):
        raise ValueError("Gate C runtime identity is incomplete")
    return result


def _source_bindings(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(REQUIRED_SOURCE_BINDINGS):
        raise ValueError("Gate C source bindings are incomplete")
    result = {str(name): value[name] for name in REQUIRED_SOURCE_BINDINGS}
    if any(not isinstance(item, str) or _SHA256.fullmatch(item) is None for item in result.values()):
        raise ValueError("Gate C source bindings contain an invalid SHA256")
    return result


def _adapter_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Gate C requires adapter identity v2")
    result = dict(value)
    if result.get("schema_version") != ADAPTER_SCHEMA_V2:
        raise ValueError("Gate C requires adapter identity v2")
    for field in ("config_sha256", "source_sha256"):
        try:
            _sha(result.get(field), field=f"adapter identity {field}")
        except ValueError:
            raise ValueError("Gate C requires complete adapter identity v2") from None
    return result


def _component_identity(
    value: Mapping[str, Any], *, label: str, required: Sequence[str]
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"Gate C {label} identity is incomplete")
    result = dict(value)
    if any(not result.get(field) for field in required):
        raise ValueError(f"Gate C {label} identity is incomplete")
    for field in required:
        if field.endswith("_sha256"):
            try:
                _sha(result[field], field=f"{label} {field}")
            except ValueError:
                raise ValueError(f"Gate C {label} identity is incomplete") from None
    return result


def _role_registry(value: Mapping[str, Any]) -> dict[str, list[str]]:
    if not isinstance(value, Mapping) or set(value) != set(ROLE_NAMES):
        raise ValueError("Gate C role registry is incomplete")
    result: dict[str, list[str]] = {}
    for role in ROLE_NAMES:
        members = value[role]
        if (
            not isinstance(members, list)
            or (role == "DEVELOPMENT_EXPOSED" and not members)
            or any(not isinstance(item, str) or not item for item in members)
            or len(members) != len(set(members))
        ):
            raise ValueError("Gate C role registry is invalid")
        result[role] = list(members)
    sets = {role: set(result[role]) for role in ROLE_NAMES}
    if any(
        sets[left] & sets[right]
        for index, left in enumerate(ROLE_NAMES)
        for right in ROLE_NAMES[index + 1 :]
    ):
        raise ValueError("Gate C role registry has overlap")
    return result


def _binding_projection(
    *,
    runtime_identity: Mapping[str, Any],
    source_sha256: Mapping[str, Any],
    adapter_identity: Mapping[str, Any],
    retrieval_identity: Mapping[str, Any],
    reader_identity: Mapping[str, Any],
    judge_identity: Mapping[str, Any],
    execution_identity: Mapping[str, Any],
    role_registry: Mapping[str, Any],
) -> dict[str, Any]:
    raw = {
        "runtime_identity": dict(runtime_identity),
        "source_sha256": dict(source_sha256),
        "adapter_identity": dict(adapter_identity),
        "retrieval_identity": dict(retrieval_identity),
        "reader_identity": dict(reader_identity),
        "judge_identity": dict(judge_identity),
        "execution_identity": dict(execution_identity),
        "role_registry": dict(role_registry),
    }
    _reject_unsafe_binding_content(raw)
    runtime = _runtime_identity(raw["runtime_identity"])
    sources = _source_bindings(raw["source_sha256"])
    adapter = _adapter_identity(raw["adapter_identity"])
    retrieval = _component_identity(
        raw["retrieval_identity"],
        label="retrieval",
        required=("policy", "config_sha256", "source_sha256"),
    )
    reader = _component_identity(
        raw["reader_identity"],
        label="reader",
        required=("adapter", "model", "prompt_sha256", "config_sha256", "source_sha256"),
    )
    judge = _component_identity(
        raw["judge_identity"],
        label="judge",
        required=("adapter", "model", "prompt_sha256", "config_sha256", "source_sha256"),
    )
    execution = _component_identity(
        raw["execution_identity"],
        label="execution",
        required=REQUIRED_EXECUTION_BINDINGS,
    )
    roles = _role_registry(raw["role_registry"])
    return {
        "runtime_identity": runtime,
        "source_sha256": sources,
        "adapter_identity": adapter,
        "retrieval_identity": retrieval,
        "reader_identity": reader,
        "judge_identity": judge,
        "execution_identity": execution,
        "role_registry": roles,
    }


def _validate_s1(value: Mapping[str, Any]) -> dict[str, Any]:
    envelope = _sealed_envelope(value, label="S1")
    payload = envelope["payload"]
    coverage = payload.get("coverage")
    if (
        payload.get("stage") != "S1"
        or payload.get("method") != "U0"
        or payload.get("verdict") != "PASS"
        or payload.get("failure_count") != 0
        or not isinstance(coverage, Mapping)
        or coverage.get("expected") != coverage.get("published")
        or coverage.get("lost") != []
        or coverage.get("duplicates") != []
    ):
        raise ValueError("Gate C requires S1 U0 PASS")
    return envelope


def _validate_s2(value: Mapping[str, Any]) -> dict[str, Any]:
    envelope = _sealed_envelope(value, label="S2 completion")
    payload = envelope["payload"]
    if payload.get("stage") == "S2-R0" or "s2-r0" in str(payload.get("schema_version", "")):
        raise ValueError("Gate C rejects diagnostic-only S2-R0 evidence")
    if (
        payload.get("schema_version") != S2_COMPLETION_SCHEMA
        or payload.get("stage") != "S2"
        or payload.get("method") != "U0"
        or payload.get("verdict") != "PASS"
        or payload.get("completion_scope") != "FULL_S2_COMPLETION"
        or payload.get("diagnostic_only") is not False
        or payload.get("retrieval_policy_selected") is not True
        or payload.get("reader_judge_executed") is not True
        or payload.get("reference_alignment_status")
        not in {"EXACT_NUMERIC_REPRODUCTION", "PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED"}
        or payload.get("reference_sanity_status") != "PASS"
        or payload.get("s3_ready") is not True
    ):
        raise ValueError("Gate C requires full S2 completion PASS")
    _sha(payload.get("numeric_sanity_sha256"), field="S2 numeric sanity")
    _sha(payload.get("s1_payload_sha256"), field="S2 S1 binding")
    _sha(payload.get("bindings_sha256"), field="S2 bindings")
    _sha(payload.get("role_registry_sha256"), field="S2 role registry")
    return envelope


def _validate_authority(
    value: Mapping[str, Any], *, output_path: Path, run_id: str
) -> dict[str, Any]:
    envelope = _sealed_envelope(value, label="S3 authority")
    payload = envelope["payload"]
    if (
        payload.get("schema_version") != AUTHORITY_SCHEMA
        or payload.get("stage") != "S3"
        or payload.get("method") != "U0"
        or payload.get("authorization") != FREEZE_ACTION
        or payload.get("run_id") != run_id
        or envelope.get("run_id") != run_id
        or Path(str(payload.get("expected_output_path", ""))).resolve()
        != output_path.resolve()
        or payload.get("outcome_observed") is not False
        or payload.get("method_results_observed") is not False
    ):
        raise ValueError("Gate C requires exact outcome-independent one-shot S3 authority")
    for field in (
        "s1_payload_sha256",
        "s2_completion_payload_sha256",
        "bindings_sha256",
        "role_registry_sha256",
    ):
        _sha(payload.get(field), field=f"S3 authority {field}")
    return envelope


def build_native_baseline_freeze(
    *,
    s1_artifact: Mapping[str, Any],
    s2_completion_artifact: Mapping[str, Any],
    authority_artifact: Mapping[str, Any],
    runtime_identity: Mapping[str, Any],
    source_sha256: Mapping[str, Any],
    adapter_identity: Mapping[str, Any],
    retrieval_identity: Mapping[str, Any],
    reader_identity: Mapping[str, Any],
    judge_identity: Mapping[str, Any],
    execution_identity: Mapping[str, Any],
    role_registry: Mapping[str, Any],
    expected_output_path: Path,
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    """Build a Native U0 freeze without writing or performing live I/O."""

    output_path = Path(expected_output_path)
    expected_run_id = _nonempty(run_id, field="run_id")
    s1 = _validate_s1(s1_artifact)
    s2 = _validate_s2(s2_completion_artifact)
    bindings = _binding_projection(
        runtime_identity=runtime_identity,
        source_sha256=source_sha256,
        adapter_identity=adapter_identity,
        retrieval_identity=retrieval_identity,
        reader_identity=reader_identity,
        judge_identity=judge_identity,
        execution_identity=execution_identity,
        role_registry=role_registry,
    )
    authority = _validate_authority(
        authority_artifact, output_path=output_path, run_id=expected_run_id
    )

    s1_hash = s1["payload_sha256"]
    s2_hash = s2["payload_sha256"]
    role_hash = payload_sha256(bindings["role_registry"])
    bindings_hash = payload_sha256(bindings)
    s2_payload = s2["payload"]
    authority_payload = authority["payload"]
    if s2_payload.get("s1_payload_sha256") != s1_hash:
        raise ValueError("Gate C S1/S2 binding drift")
    if s2_payload.get("role_registry_sha256") != role_hash:
        raise ValueError("Gate C role drift after S2 completion")
    if authority_payload.get("role_registry_sha256") != role_hash:
        raise ValueError("Gate C role drift after S3 authority")
    if s2_payload.get("bindings_sha256") != bindings_hash:
        raise ValueError("Gate C binding drift after S2 completion")
    if authority_payload.get("bindings_sha256") != bindings_hash:
        raise ValueError("Gate C binding drift after S3 authority")
    if (
        authority_payload.get("s1_payload_sha256") != s1_hash
        or authority_payload.get("s2_completion_payload_sha256") != s2_hash
    ):
        raise ValueError("Gate C one-shot S3 authority evidence drift")

    payload = {
        "schema_version": FREEZE_SCHEMA,
        "stage": "S3",
        "method": "U0",
        "verdict": "PASS",
        "freeze_status": "FROZEN",
        "immutable": True,
        "outcome_independent": True,
        "method_results_observed": False,
        "s4_authorized": False,
        "run_id": expected_run_id,
        "expected_output_path": str(output_path.resolve()),
        "s1_payload_sha256": s1_hash,
        "s2_completion_payload_sha256": s2_hash,
        "authority_payload_sha256": authority["payload_sha256"],
        "bindings": bindings,
        "bindings_sha256": bindings_hash,
        "role_registry_sha256": role_hash,
        "reference_alignment_status": s2_payload.get("reference_alignment_status"),
        "reference_sanity_status": s2_payload.get("reference_sanity_status"),
        "numeric_sanity_sha256": s2_payload["numeric_sanity_sha256"],
    }
    return finalize_envelope(
        payload=payload,
        protocol_version=FREEZE_SCHEMA,
        git_commit=_nonempty(git_commit, field="git_commit"),
        run_id=expected_run_id,
    )


def verify_native_baseline_freeze(value: Mapping[str, Any]) -> dict[str, Any]:
    """Verify a built or finalized Gate-C artifact without filesystem I/O."""

    envelope = _sealed_envelope(value, label="Native baseline freeze")
    payload = envelope["payload"]
    if (
        payload.get("schema_version") != FREEZE_SCHEMA
        or payload.get("stage") != "S3"
        or payload.get("method") != "U0"
        or payload.get("verdict") != "PASS"
        or payload.get("freeze_status") != "FROZEN"
        or payload.get("immutable") is not True
        or payload.get("outcome_independent") is not True
        or payload.get("method_results_observed") is not False
        or payload.get("s4_authorized") is not False
        or payload.get("run_id") != envelope.get("run_id")
    ):
        raise ValueError("Native baseline freeze identity is invalid")
    for field in (
        "s1_payload_sha256",
        "s2_completion_payload_sha256",
        "authority_payload_sha256",
        "bindings_sha256",
        "role_registry_sha256",
        "numeric_sanity_sha256",
    ):
        _sha(payload.get(field), field=f"Native baseline freeze {field}")
    bindings = payload.get("bindings")
    if not isinstance(bindings, Mapping):
        raise ValueError("Native baseline freeze bindings are missing")
    validated = _binding_projection(
        runtime_identity=bindings.get("runtime_identity", {}),
        source_sha256=bindings.get("source_sha256", {}),
        adapter_identity=bindings.get("adapter_identity", {}),
        retrieval_identity=bindings.get("retrieval_identity", {}),
        reader_identity=bindings.get("reader_identity", {}),
        judge_identity=bindings.get("judge_identity", {}),
        execution_identity=bindings.get("execution_identity", {}),
        role_registry=bindings.get("role_registry", {}),
    )
    if payload["bindings_sha256"] != payload_sha256(validated):
        raise ValueError("Native baseline freeze binding hash mismatch")
    if payload["role_registry_sha256"] != payload_sha256(validated["role_registry"]):
        raise ValueError("Native baseline freeze role hash mismatch")
    return {
        "verdict": "PASS",
        "run_id": payload["run_id"],
        "payload_sha256": envelope["payload_sha256"],
        "bindings_sha256": payload["bindings_sha256"],
    }


def finalize_native_baseline_freeze(
    output_path: Path, artifact: Mapping[str, Any]
) -> dict[str, Any]:
    """Persist a verified freeze with O_EXCL; never overwrite an artifact."""

    verified = verify_native_baseline_freeze(artifact)
    value = dict(artifact)
    payload = value["payload"]
    path = Path(output_path)
    if path.resolve() != Path(str(payload.get("expected_output_path", ""))).resolve():
        raise ValueError("Gate C authority target does not match output path")
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except FileExistsError:
        raise ValueError("Native baseline freeze already exists") from None
    try:
        os.write(descriptor, serialized)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return value
