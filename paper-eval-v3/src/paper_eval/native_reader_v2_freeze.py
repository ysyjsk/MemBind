"""Freeze the qualified Reader-v2 as the common evaluation-layer policy."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256


READER_V2_FREEZE_SCHEMA = "membind.paper-eval-v3.native-reader-v2-freeze.v1"
_METHODS = {"U0", "A0", "P*", "M*"}
_SOURCE_NAMES = {
    "workplan",
    "reader_source",
    "contract_file",
    "qualification_file",
    "result_file",
    "postlive_tests",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UNSAFE_KEYS = {
    "api_key",
    "password",
    "raw_prompt",
    "raw_output",
    "raw_question",
    "raw_answer",
    "content",
    "secret",
}


class ReaderV2FreezeError(ValueError):
    """Reader-v2 cannot be frozen or the freeze has drifted."""


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ReaderV2FreezeError(f"{field} is not a SHA256")
    return value


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReaderV2FreezeError(f"{field} must be a mapping")
    return deepcopy(dict(value))


def _reject_unsafe(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _UNSAFE_KEYS:
                raise ReaderV2FreezeError("Reader-v2 freeze contains unsafe data")
            _reject_unsafe(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_unsafe(child)


def _bindings(value: object, *, expected_sha: str, field: str) -> dict[str, str]:
    bindings = _mapping(value, field=field)
    if set(bindings) != _METHODS or any(
        item != expected_sha for item in bindings.values()
    ):
        raise ReaderV2FreezeError(f"{field} drift")
    return {key: str(value) for key, value in sorted(bindings.items())}


def build_reader_v2_freeze(
    *,
    contract: Mapping[str, Any],
    qualification_payload_sha256: str,
    result: Mapping[str, Any],
    result_file_sha256: str,
    judge_config_sha256: str,
    source_sha256: Mapping[str, str],
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    """Build a result-independent common-policy freeze after compatibility PASS."""

    selected_contract = _mapping(contract, field="Reader-v2 contract")
    selected_result = _mapping(result, field="Reader-v2 result")
    result_payload = _mapping(selected_result.get("payload"), field="result payload")
    if selected_result.get("payload_sha256") != payload_sha256(result_payload):
        raise ReaderV2FreezeError("Reader-v2 result payload hash mismatch")
    classification = _mapping(
        result_payload.get("classification"), field="result classification"
    )
    if (
        result_payload.get("status") != "PASS"
        or result_payload.get("compatibility_status") != "PASS"
        or classification.get("compatibility_status") != "PASS"
        or result_payload.get("quality_gate_used") is not False
        or classification.get("quality_gate_used") is not False
        or result_payload.get("native_quality_mergeable") is not False
        or classification.get("native_quality_mergeable") is not False
        or result_payload.get("pilot_or_final_mergeable") is not False
        or classification.get("pilot_or_final_mergeable") is not False
        or result_payload.get("s3_authorized") is not False
        or classification.get("s3_authorized") is not False
    ):
        raise ReaderV2FreezeError("Reader-v2 compatibility result is not freezeable")
    qa = classification.get("qa_accuracy_diagnostic")
    if qa not in {0.0, 1.0} or isinstance(qa, bool):
        raise ReaderV2FreezeError("Reader-v2 QA diagnostic is invalid")

    reader_sha = _sha(
        selected_contract.get("reader_config_sha256"), field="Reader config"
    )
    method_readers = _bindings(
        selected_contract.get("method_reader_bindings"),
        expected_sha=reader_sha,
        field="method Reader bindings",
    )
    if classification.get("reader_config_sha256") != reader_sha:
        raise ReaderV2FreezeError("qualified Reader config drift")
    judge_sha = _sha(judge_config_sha256, field="Judge config")
    sources = _mapping(source_sha256, field="source hashes")
    if set(sources) != _SOURCE_NAMES:
        raise ReaderV2FreezeError("Reader-v2 freeze source inventory drift")
    for name, value in sources.items():
        _sha(value, field=f"source {name}")
    if sources["result_file"] != result_file_sha256:
        raise ReaderV2FreezeError("Reader-v2 result source drift")

    payload = {
        "schema_version": READER_V2_FREEZE_SCHEMA,
        "stage": "NATIVE-READER-V2-FREEZE",
        "status": "PASS",
        "reader_config_sha256": reader_sha,
        "method_reader_bindings": method_readers,
        "judge_identity_sha256": _sha(
            selected_contract.get("judge_identity_sha256"),
            field="Judge identity",
        ),
        "judge_config_sha256": judge_sha,
        "method_judge_bindings": {
            method: judge_sha for method in sorted(_METHODS)
        },
        "contract_sha256": _sha(
            selected_contract.get("contract_sha256"), field="contract"
        ),
        "qualification_payload_sha256": _sha(
            qualification_payload_sha256, field="qualification payload"
        ),
        "result_payload_sha256": _sha(
            selected_result.get("payload_sha256"), field="result payload"
        ),
        "result_file_sha256": _sha(result_file_sha256, field="result file"),
        "historical_direct_result_sha256": _sha(
            selected_contract.get("historical_direct_result_sha256"),
            field="historical direct result",
        ),
        "compatibility_status": "PASS",
        "quality_gate_used": False,
        "qa_accuracy_diagnostic": qa,
        "qualification_scope": "ADAPTER_COMPATIBILITY_ONLY",
        "native_quality_mergeable": False,
        "pilot_or_final_mergeable": False,
        "s3_configuration_update_authorized": True,
        "pilot_execution_authorized": False,
        "s3_authorized": False,
        "source_sha256": dict(sorted(sources.items())),
    }
    _reject_unsafe(payload)
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=str(git_commit),
        run_id=str(run_id),
    )
    return verify_reader_v2_freeze(artifact)


def verify_reader_v2_freeze(value: Mapping[str, Any]) -> dict[str, Any]:
    artifact = _mapping(value, field="Reader-v2 freeze")
    expected_envelope = {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }
    payload = artifact.get("payload")
    if (
        set(artifact) != expected_envelope
        or artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or not isinstance(payload, Mapping)
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise ReaderV2FreezeError("Reader-v2 freeze envelope drift")
    body = deepcopy(dict(payload))
    expected_fields = {
        "schema_version",
        "stage",
        "status",
        "reader_config_sha256",
        "method_reader_bindings",
        "judge_identity_sha256",
        "judge_config_sha256",
        "method_judge_bindings",
        "contract_sha256",
        "qualification_payload_sha256",
        "result_payload_sha256",
        "result_file_sha256",
        "historical_direct_result_sha256",
        "compatibility_status",
        "quality_gate_used",
        "qa_accuracy_diagnostic",
        "qualification_scope",
        "native_quality_mergeable",
        "pilot_or_final_mergeable",
        "s3_configuration_update_authorized",
        "pilot_execution_authorized",
        "s3_authorized",
        "source_sha256",
    }
    if set(body) != expected_fields:
        raise ReaderV2FreezeError("Reader-v2 freeze payload shape drift")
    if (
        body.get("schema_version") != READER_V2_FREEZE_SCHEMA
        or body.get("stage") != "NATIVE-READER-V2-FREEZE"
        or body.get("status") != "PASS"
        or body.get("compatibility_status") != "PASS"
        or body.get("quality_gate_used") is not False
        or body.get("qa_accuracy_diagnostic") not in {0.0, 1.0}
        or body.get("native_quality_mergeable") is not False
        or body.get("pilot_or_final_mergeable") is not False
        or body.get("s3_configuration_update_authorized") is not True
        or body.get("pilot_execution_authorized") is not False
        or body.get("s3_authorized") is not False
    ):
        raise ReaderV2FreezeError("Reader-v2 freeze semantics drift")
    reader_sha = _sha(body.get("reader_config_sha256"), field="Reader config")
    body["method_reader_bindings"] = _bindings(
        body.get("method_reader_bindings"),
        expected_sha=reader_sha,
        field="method Reader bindings",
    )
    judge_sha = _sha(body.get("judge_config_sha256"), field="Judge config")
    body["method_judge_bindings"] = _bindings(
        body.get("method_judge_bindings"),
        expected_sha=judge_sha,
        field="method Judge bindings",
    )
    for field in (
        "judge_identity_sha256",
        "contract_sha256",
        "qualification_payload_sha256",
        "result_payload_sha256",
        "result_file_sha256",
        "historical_direct_result_sha256",
    ):
        _sha(body.get(field), field=field)
    sources = _mapping(body.get("source_sha256"), field="source hashes")
    if set(sources) != _SOURCE_NAMES:
        raise ReaderV2FreezeError("Reader-v2 freeze source inventory drift")
    for name, source_hash in sources.items():
        _sha(source_hash, field=f"source {name}")
    if sources["result_file"] != body["result_file_sha256"]:
        raise ReaderV2FreezeError("Reader-v2 result source binding drift")
    _reject_unsafe(body)
    artifact["payload"] = body
    return artifact


def finalize_reader_v2_freeze(
    *, path: Path, artifact: Mapping[str, Any]
) -> dict[str, Any]:
    verified = verify_reader_v2_freeze(artifact)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(verified, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except FileExistsError:
        raise ReaderV2FreezeError("Reader-v2 freeze already exists") from None
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
    return verified
