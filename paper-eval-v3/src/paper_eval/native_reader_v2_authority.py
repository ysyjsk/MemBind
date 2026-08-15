"""Offline seal and exclusive authority for one Reader-v2 canary."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256
from .native_reader_v2_qualification import (
    CANARY_HISTORY_ID,
    CANARY_NAMESPACE,
    verify_reader_v2_contract,
)


READER_V2_QUALIFICATION_SCHEMA = (
    "membind.paper-eval-v3.native-reader-v2-offline-qualification.v1"
)
READER_V2_AUTHORIZATION_SCHEMA = (
    "membind.paper-eval-v3.native-reader-v2-authorization.v1"
)
READER_V2_CONSUMPTION_SCHEMA = (
    "membind.paper-eval-v3.native-reader-v2-consumption.v1"
)
READER_V2_AUTHORIZATION_ACTION = "RUN_NATIVE_READER_V2_CANARY_ONCE"

READER_V2_EVIDENCE_NAMES = frozenset(
    {
        "workplan",
        "parent_workplan",
        "reader_source",
        "reader_test",
        "qualification_source",
        "qualification_test",
        "authority_source",
        "authority_test",
        "controller_source",
        "controller_test",
        "production_source",
        "production_test",
        "focused_green",
        "full_offline_green",
        "historical_direct_result",
        "c2_manifest",
        "dataset_parity",
        "development_roles",
        "judge_qualification",
    }
)

READER_V2_PREREQUISITE_STATUS = {
    "historical_direct_result": "VERIFIED_REVIEW_REQUIRED",
    "dataset_parity": "PASS",
    "development_roles": "PASS",
    "judge_qualification": "PASS",
    "c2_canary_manifest": "VERIFIED_DRIFT_DISCLOSED",
    "reader_contract_tests": "PASS",
    "full_offline_tests": "PASS",
}

READER_V2_LIMITS = {
    "graphiti_search_calls": 1,
    "reader_requests": 1,
    "judge_requests": 1,
    "construction_llm_requests": 0,
    "embedding_requests": 0,
    "cross_encoder_requests": 0,
    "database_mutation_attempts": 0,
    "cleanup_calls": 0,
    "retry_count": 0,
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ReaderV2AuthorityError(ValueError):
    """A Reader-v2 seal or one-shot authority is incomplete or unsafe."""


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ReaderV2AuthorityError(f"{field} is not a SHA256")
    return value


def _nonempty(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReaderV2AuthorityError(f"{field} is invalid")
    return value


def _sealed(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReaderV2AuthorityError(f"{label} envelope is invalid")
    envelope = deepcopy(dict(value))
    expected = {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }
    payload = envelope.get("payload")
    if (
        set(envelope) != expected
        or envelope.get("protocol_version") != PROTOCOL_VERSION
        or envelope.get("status") != "finalized"
        or not isinstance(payload, Mapping)
        or envelope.get("payload_sha256") != payload_sha256(payload)
    ):
        raise ReaderV2AuthorityError(f"{label} envelope seal is invalid")
    _nonempty(envelope.get("git_commit"), field=f"{label} git commit")
    _nonempty(envelope.get("run_id"), field=f"{label} run ID")
    envelope["payload"] = deepcopy(dict(payload))
    return envelope


def _hash_map(value: object, *, expected: set[str] | frozenset[str], field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise ReaderV2AuthorityError(f"{field} set is invalid")
    result = {str(name): _sha(item, field=f"{field} {name}") for name, item in value.items()}
    return dict(sorted(result.items()))


def _prerequisites(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping) or set(value) != set(
        READER_V2_PREREQUISITE_STATUS
    ):
        raise ReaderV2AuthorityError("Reader-v2 prerequisite set is invalid")
    result: dict[str, dict[str, str]] = {}
    for name, expected_status in READER_V2_PREREQUISITE_STATUS.items():
        entry = value.get(name)
        if not isinstance(entry, Mapping) or set(entry) != {
            "file_sha256",
            "payload_sha256",
            "status",
        }:
            raise ReaderV2AuthorityError(
                f"Reader-v2 prerequisite {name} is invalid"
            )
        if entry.get("status") != expected_status:
            raise ReaderV2AuthorityError(
                f"Reader-v2 prerequisite {name} status drift"
            )
        result[name] = {
            "file_sha256": _sha(
                entry.get("file_sha256"), field=f"{name} file"
            ),
            "payload_sha256": _sha(
                entry.get("payload_sha256"), field=f"{name} payload"
            ),
            "status": expected_status,
        }
    return dict(sorted(result.items()))


def build_reader_v2_offline_qualification(
    *,
    contract: Mapping[str, Any],
    contract_file_sha256: str,
    evidence_sha256: Mapping[str, str],
    prerequisites: Mapping[str, Mapping[str, str]],
    history_id: str,
    namespace: str,
    expected_session_count: int,
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    """Seal only offline evidence; this function performs no live I/O."""

    verified_contract = verify_reader_v2_contract(contract)
    if (
        history_id != CANARY_HISTORY_ID
        or namespace != CANARY_NAMESPACE
        or expected_session_count != 49
    ):
        raise ReaderV2AuthorityError("Reader-v2 canary identity drift")
    payload = {
        "schema_version": READER_V2_QUALIFICATION_SCHEMA,
        "stage": "NATIVE-READER-V2-OFFLINE",
        "status": "PASS",
        "contract_file_sha256": _sha(
            contract_file_sha256, field="contract file"
        ),
        "contract_sha256": verified_contract["contract_sha256"],
        "evidence_sha256": _hash_map(
            evidence_sha256,
            expected=READER_V2_EVIDENCE_NAMES,
            field="evidence",
        ),
        "prerequisites": _prerequisites(prerequisites),
        "history_id": history_id,
        "namespace": namespace,
        "expected_session_count": expected_session_count,
        "live_io_performed": False,
        "quality_gate_used": False,
        "qualification_scope": "ADAPTER_COMPATIBILITY_ONLY",
        "native_quality_mergeable": False,
        "pilot_or_final_mergeable": False,
        "s3_authorized": False,
    }
    return finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=_nonempty(git_commit, field="git commit"),
        run_id=_nonempty(run_id, field="run ID"),
    )


def verify_reader_v2_offline_qualification(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    envelope = _sealed(value, label="Reader-v2 qualification")
    payload = envelope["payload"]
    expected_fields = {
        "schema_version",
        "stage",
        "status",
        "contract_file_sha256",
        "contract_sha256",
        "evidence_sha256",
        "prerequisites",
        "history_id",
        "namespace",
        "expected_session_count",
        "live_io_performed",
        "quality_gate_used",
        "qualification_scope",
        "native_quality_mergeable",
        "pilot_or_final_mergeable",
        "s3_authorized",
    }
    if set(payload) != expected_fields:
        raise ReaderV2AuthorityError("Reader-v2 qualification shape is invalid")
    if (
        payload.get("schema_version") != READER_V2_QUALIFICATION_SCHEMA
        or payload.get("stage") != "NATIVE-READER-V2-OFFLINE"
        or payload.get("status") != "PASS"
        or payload.get("history_id") != CANARY_HISTORY_ID
        or payload.get("namespace") != CANARY_NAMESPACE
        or payload.get("expected_session_count") != 49
        or payload.get("live_io_performed") is not False
        or payload.get("quality_gate_used") is not False
        or payload.get("qualification_scope") != "ADAPTER_COMPATIBILITY_ONLY"
        or payload.get("native_quality_mergeable") is not False
        or payload.get("pilot_or_final_mergeable") is not False
        or payload.get("s3_authorized") is not False
    ):
        raise ReaderV2AuthorityError(
            "Reader-v2 qualification semantics are invalid"
        )
    _sha(payload.get("contract_file_sha256"), field="contract file")
    _sha(payload.get("contract_sha256"), field="contract")
    payload["evidence_sha256"] = _hash_map(
        payload.get("evidence_sha256"),
        expected=READER_V2_EVIDENCE_NAMES,
        field="evidence",
    )
    payload["prerequisites"] = _prerequisites(payload.get("prerequisites"))
    return envelope


def build_reader_v2_authorization(
    *,
    qualification: Mapping[str, Any],
    qualification_file_sha256: str,
    contract_file_sha256: str,
    run_id: str,
    history_id: str,
    namespace: str,
    consumption_path: Path,
    result_path: Path,
    failure_path: Path,
    git_commit: str,
) -> dict[str, Any]:
    qualified = verify_reader_v2_offline_qualification(qualification)
    selected_run = _nonempty(run_id, field="run ID")
    paths = {
        "consumption_path": Path(consumption_path),
        "result_path": Path(result_path),
        "failure_path": Path(failure_path),
    }
    if (
        history_id != qualified["payload"]["history_id"]
        or namespace != qualified["payload"]["namespace"]
        or contract_file_sha256
        != qualified["payload"]["contract_file_sha256"]
        or any(path.parent.name != selected_run for path in paths.values())
        or len({path.resolve() for path in paths.values()}) != 3
    ):
        raise ReaderV2AuthorityError("Reader-v2 authorization identity drift")
    payload = {
        "schema_version": READER_V2_AUTHORIZATION_SCHEMA,
        "stage": "NATIVE-READER-V2-CANARY",
        "authorization": READER_V2_AUTHORIZATION_ACTION,
        "run_id": selected_run,
        "history_id": history_id,
        "namespace": namespace,
        "qualification_file_sha256": _sha(
            qualification_file_sha256, field="qualification file"
        ),
        "qualification_payload_sha256": qualified["payload_sha256"],
        "contract_file_sha256": _sha(
            contract_file_sha256, field="contract file"
        ),
        "contract_sha256": qualified["payload"]["contract_sha256"],
        **{name: str(path.resolve()) for name, path in paths.items()},
        "limits": dict(READER_V2_LIMITS),
        "automatic_retry": False,
        "quality_gate_used": False,
        "result_mergeable_on_failure": False,
        "s3_authorized": False,
    }
    return finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=_nonempty(git_commit, field="git commit"),
        run_id=selected_run,
    )


def verify_reader_v2_authorization(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    envelope = _sealed(value, label="Reader-v2 authorization")
    payload = envelope["payload"]
    expected_fields = {
        "schema_version",
        "stage",
        "authorization",
        "run_id",
        "history_id",
        "namespace",
        "qualification_file_sha256",
        "qualification_payload_sha256",
        "contract_file_sha256",
        "contract_sha256",
        "consumption_path",
        "result_path",
        "failure_path",
        "limits",
        "automatic_retry",
        "quality_gate_used",
        "result_mergeable_on_failure",
        "s3_authorized",
    }
    if set(payload) != expected_fields:
        raise ReaderV2AuthorityError("Reader-v2 authorization shape is invalid")
    if (
        payload.get("schema_version") != READER_V2_AUTHORIZATION_SCHEMA
        or payload.get("stage") != "NATIVE-READER-V2-CANARY"
        or payload.get("authorization") != READER_V2_AUTHORIZATION_ACTION
        or payload.get("run_id") != envelope.get("run_id")
        or payload.get("history_id") != CANARY_HISTORY_ID
        or payload.get("namespace") != CANARY_NAMESPACE
        or payload.get("limits") != READER_V2_LIMITS
        or payload.get("automatic_retry") is not False
        or payload.get("quality_gate_used") is not False
        or payload.get("result_mergeable_on_failure") is not False
        or payload.get("s3_authorized") is not False
    ):
        raise ReaderV2AuthorityError("Reader-v2 authorization semantics are invalid")
    for field in (
        "qualification_file_sha256",
        "qualification_payload_sha256",
        "contract_file_sha256",
        "contract_sha256",
    ):
        _sha(payload.get(field), field=field)
    for field in ("consumption_path", "result_path", "failure_path"):
        path = Path(str(payload.get(field, "")))
        if not path.is_absolute() or path.parent.name != payload["run_id"]:
            raise ReaderV2AuthorityError("Reader-v2 output path drift")
    if len(
        {
            Path(str(payload[field])).resolve()
            for field in ("consumption_path", "result_path", "failure_path")
        }
    ) != 3:
        raise ReaderV2AuthorityError("Reader-v2 terminal paths overlap")
    return envelope


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except FileExistsError:
        raise ReaderV2AuthorityError("Reader-v2 authorization already consumed") from None
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


def consume_reader_v2_authorization(
    *,
    authorization: Mapping[str, Any],
    authorization_file_sha256: str,
    consumption_path: Path,
) -> dict[str, Any]:
    authorized = verify_reader_v2_authorization(authorization)
    payload = authorized["payload"]
    selected_path = Path(consumption_path).resolve()
    if selected_path != Path(payload["consumption_path"]).resolve():
        raise ReaderV2AuthorityError("Reader-v2 consumption path drift")
    consumption_payload = {
        "schema_version": READER_V2_CONSUMPTION_SCHEMA,
        "stage": "NATIVE-READER-V2-CANARY",
        "status": "CONSUMED_BEFORE_LIVE_IO",
        "run_id": payload["run_id"],
        "history_id": payload["history_id"],
        "namespace": payload["namespace"],
        "authorization_sha256": _sha(
            authorization_file_sha256, field="authorization file"
        ),
        "authorization_payload_sha256": authorized["payload_sha256"],
        "live_io_performed_at_consumption": False,
        "quality_gate_used": False,
        "s3_authorized": False,
    }
    artifact = finalize_envelope(
        payload=consumption_payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=authorized["git_commit"],
        run_id=payload["run_id"],
    )
    _write_exclusive(selected_path, artifact)
    return artifact
