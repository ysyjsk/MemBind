"""Pure seals and exclusive one-shot authority for bounded S2 completion."""

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


POLICY_FREEZE_SCHEMA = "membind.paper-eval-v3.s2-completion-policy-freeze.v1"
QUALIFICATION_SCHEMA = "membind.paper-eval-v3.s2-completion-offline-qualification.v1"
AUTHORIZATION_SCHEMA = "membind.paper-eval-v3.s2-completion-authorization.v1"
CONSUMPTION_SCHEMA = "membind.paper-eval-v3.s2-completion-consumption.v1"
AUTHORIZATION_ACTION = "RUN_BOUNDED_S2_COMPLETION_ONCE"
POLICY_NAME = "graphiti-0.29.3-episode-bm25-session-v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_POLICY_EVIDENCE = {
    "parent_protocol",
    "retrieval_amendment",
    "execution_workplan",
    "research_basis",
    "completion_contract_source",
    "completion_contract_test",
    "session_policy_source",
    "session_policy_test",
    "session_reader_source",
    "session_reader_test",
    "completion_chain_source",
    "completion_chain_test",
    "completion_identity_source",
    "completion_identity_test",
    "formal_retrieval_source",
    "formal_retrieval_test",
    "completion_authority_source",
    "completion_authority_test",
    "completion_controller_source",
    "completion_controller_test",
    "completion_production_source",
    "completion_production_test",
    "finalize_script",
    "run_script",
    "focused_green",
    "full_offline_green",
}
_PREREQUISITE_STATUS = {
    "s1_smoke": "PASS",
    "u0_qualification": "PASS",
    "dataset_parity": "PASS",
    "evaluator_parity": "PASS",
    "development_roles": "PASS",
    "current_state": "VERIFIED",
    "judge_qualification": "PASS",
    "s2r0_chain": "VERIFIED",
}
_LIMITS = {
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


class CompletionAuthorityError(ValueError):
    """An S2 completion seal or one-shot authority is invalid."""


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CompletionAuthorityError(f"{field} is not a SHA256")
    return value


def _nonempty(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CompletionAuthorityError(f"{field} is invalid")
    return value


def _sealed(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CompletionAuthorityError(f"{label} envelope is invalid")
    envelope = deepcopy(dict(value))
    if set(envelope) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise CompletionAuthorityError(f"{label} envelope shape is invalid")
    payload = envelope.get("payload")
    if (
        envelope.get("protocol_version") != PROTOCOL_VERSION
        or envelope.get("status") != "finalized"
        or not isinstance(payload, Mapping)
        or envelope.get("payload_sha256") != payload_sha256(payload)
    ):
        raise CompletionAuthorityError(f"{label} envelope seal is invalid")
    _nonempty(envelope.get("git_commit"), field=f"{label} git commit")
    _nonempty(envelope.get("run_id"), field=f"{label} run ID")
    envelope["payload"] = dict(payload)
    return envelope


def _hash_map(value: object, *, expected: set[str], label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise CompletionAuthorityError(f"{label} evidence is incomplete")
    result = {str(key): _sha(item, field=f"{label} {key}") for key, item in value.items()}
    return dict(sorted(result.items()))


def build_completion_policy_freeze(
    *,
    contract_file_sha256: str,
    contract_sha256: str,
    adapter_identity_file_sha256: str,
    adapter_identity_sha256: str,
    evidence_sha256: Mapping[str, str],
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": POLICY_FREEZE_SCHEMA,
        "stage": "S2-COMPLETION-POLICY",
        "status": "FROZEN",
        "policy_name": POLICY_NAME,
        "retrieval_policy_selected": True,
        "diagnostic_only": False,
        "r0_outcome_previously_observed": True,
        "selection_not_blinded": True,
        "r0_numeric_score_used_for_policy_choice": False,
        "candidate_score_search_performed": False,
        "contract_file_sha256": _sha(
            contract_file_sha256, field="contract file"
        ),
        "contract_sha256": _sha(contract_sha256, field="contract"),
        "adapter_identity_file_sha256": _sha(
            adapter_identity_file_sha256, field="adapter identity file"
        ),
        "adapter_identity_sha256": _sha(
            adapter_identity_sha256, field="adapter identity"
        ),
        "evidence_sha256": _hash_map(
            evidence_sha256,
            expected=_POLICY_EVIDENCE,
            label="policy",
        ),
        "live_authorized": False,
        "s3_authorized": False,
    }
    return finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=_nonempty(git_commit, field="git commit"),
        run_id=_nonempty(run_id, field="run ID"),
    )


def verify_completion_policy_freeze(value: Mapping[str, Any]) -> dict[str, Any]:
    envelope = _sealed(value, label="policy freeze")
    payload = envelope["payload"]
    expected_keys = {
        "schema_version",
        "stage",
        "status",
        "policy_name",
        "retrieval_policy_selected",
        "diagnostic_only",
        "r0_outcome_previously_observed",
        "selection_not_blinded",
        "r0_numeric_score_used_for_policy_choice",
        "candidate_score_search_performed",
        "contract_file_sha256",
        "contract_sha256",
        "adapter_identity_file_sha256",
        "adapter_identity_sha256",
        "evidence_sha256",
        "live_authorized",
        "s3_authorized",
    }
    if set(payload) != expected_keys:
        raise CompletionAuthorityError("policy freeze shape or score field is invalid")
    if (
        payload.get("schema_version") != POLICY_FREEZE_SCHEMA
        or payload.get("stage") != "S2-COMPLETION-POLICY"
        or payload.get("status") != "FROZEN"
        or payload.get("policy_name") != POLICY_NAME
        or payload.get("retrieval_policy_selected") is not True
        or payload.get("diagnostic_only") is not False
        or payload.get("r0_outcome_previously_observed") is not True
        or payload.get("selection_not_blinded") is not True
        or payload.get("r0_numeric_score_used_for_policy_choice") is not False
        or payload.get("candidate_score_search_performed") is not False
        or payload.get("live_authorized") is not False
        or payload.get("s3_authorized") is not False
    ):
        raise CompletionAuthorityError("policy freeze semantics are invalid")
    for field in (
        "contract_file_sha256",
        "contract_sha256",
        "adapter_identity_file_sha256",
        "adapter_identity_sha256",
    ):
        _sha(payload.get(field), field=field)
    payload["evidence_sha256"] = _hash_map(
        payload.get("evidence_sha256"), expected=_POLICY_EVIDENCE, label="policy"
    )
    return envelope


def _prerequisites(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping) or set(value) != set(_PREREQUISITE_STATUS):
        raise CompletionAuthorityError("offline prerequisite evidence is incomplete")
    result: dict[str, dict[str, str]] = {}
    for name, expected_status in _PREREQUISITE_STATUS.items():
        binding = value.get(name)
        if not isinstance(binding, Mapping) or set(binding) != {
            "file_sha256",
            "payload_sha256",
            "status",
        }:
            raise CompletionAuthorityError("offline prerequisite binding is incomplete")
        if binding.get("status") != expected_status:
            raise CompletionAuthorityError(f"offline prerequisite is not green: {name}")
        result[name] = {
            "file_sha256": _sha(
                binding.get("file_sha256"), field=f"{name} file"
            ),
            "payload_sha256": _sha(
                binding.get("payload_sha256"), field=f"{name} payload"
            ),
            "status": expected_status,
        }
    return result


def build_completion_offline_qualification(
    *,
    policy_freeze: Mapping[str, Any],
    policy_freeze_file_sha256: str,
    prerequisites: Mapping[str, Mapping[str, str]],
    history_id: str,
    namespace: str,
    expected_session_count: int,
    expected_gold_count: int,
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    policy = verify_completion_policy_freeze(policy_freeze)
    if (
        expected_session_count != 49
        or expected_gold_count != 2
        or history_id != "07741c45"
        or namespace != "pev3-s1-20260814-001"
    ):
        raise CompletionAuthorityError("offline qualification corpus identity drift")
    payload = {
        "schema_version": QUALIFICATION_SCHEMA,
        "stage": "S2-COMPLETION-OFFLINE",
        "verdict": "PASS",
        "history_id": history_id,
        "namespace": namespace,
        "expected_session_count": expected_session_count,
        "expected_gold_count": expected_gold_count,
        "policy_freeze_file_sha256": _sha(
            policy_freeze_file_sha256, field="policy freeze file"
        ),
        "policy_freeze_payload_sha256": policy["payload_sha256"],
        "contract_sha256": policy["payload"]["contract_sha256"],
        "adapter_identity_sha256": policy["payload"]["adapter_identity_sha256"],
        "prerequisites": _prerequisites(prerequisites),
        "live_authorized": False,
        "s3_authorized": False,
    }
    return finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=_nonempty(git_commit, field="git commit"),
        run_id=_nonempty(run_id, field="run ID"),
    )


def verify_completion_offline_qualification(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    envelope = _sealed(value, label="offline qualification")
    payload = envelope["payload"]
    expected_keys = {
        "schema_version",
        "stage",
        "verdict",
        "history_id",
        "namespace",
        "expected_session_count",
        "expected_gold_count",
        "policy_freeze_file_sha256",
        "policy_freeze_payload_sha256",
        "contract_sha256",
        "adapter_identity_sha256",
        "prerequisites",
        "live_authorized",
        "s3_authorized",
    }
    if set(payload) != expected_keys:
        raise CompletionAuthorityError("offline qualification shape is invalid")
    if (
        payload.get("schema_version") != QUALIFICATION_SCHEMA
        or payload.get("stage") != "S2-COMPLETION-OFFLINE"
        or payload.get("verdict") != "PASS"
        or payload.get("history_id") != "07741c45"
        or payload.get("namespace") != "pev3-s1-20260814-001"
        or payload.get("expected_session_count") != 49
        or payload.get("expected_gold_count") != 2
        or payload.get("live_authorized") is not False
        or payload.get("s3_authorized") is not False
    ):
        raise CompletionAuthorityError("offline qualification semantics are invalid")
    for field in (
        "policy_freeze_file_sha256",
        "policy_freeze_payload_sha256",
        "contract_sha256",
        "adapter_identity_sha256",
    ):
        _sha(payload.get(field), field=field)
    payload["prerequisites"] = _prerequisites(payload.get("prerequisites"))
    return envelope


def build_completion_authorization(
    *,
    qualification: Mapping[str, Any],
    qualification_file_sha256: str,
    policy_freeze_file_sha256: str,
    adapter_identity_file_sha256: str,
    adapter_identity_sha256: str,
    run_id: str,
    history_id: str,
    namespace: str,
    consumption_path: Path,
    result_path: Path,
    failure_path: Path,
    git_commit: str,
) -> dict[str, Any]:
    qualified = verify_completion_offline_qualification(qualification)
    selected_run = _nonempty(run_id, field="run ID")
    paths = {
        "consumption_path": Path(consumption_path),
        "result_path": Path(result_path),
        "failure_path": Path(failure_path),
    }
    if (
        history_id != qualified["payload"]["history_id"]
        or namespace != qualified["payload"]["namespace"]
        or any(path.parent.name != selected_run for path in paths.values())
        or len({path.resolve() for path in paths.values()}) != 3
    ):
        raise CompletionAuthorityError("authorization execution identity drift")
    payload = {
        "schema_version": AUTHORIZATION_SCHEMA,
        "stage": "S2-COMPLETION",
        "authorization": AUTHORIZATION_ACTION,
        "run_id": selected_run,
        "history_id": history_id,
        "namespace": namespace,
        "qualification_file_sha256": _sha(
            qualification_file_sha256, field="qualification file"
        ),
        "qualification_payload_sha256": qualified["payload_sha256"],
        "policy_freeze_file_sha256": _sha(
            policy_freeze_file_sha256, field="policy freeze file"
        ),
        "adapter_identity_file_sha256": _sha(
            adapter_identity_file_sha256, field="adapter identity file"
        ),
        "adapter_identity_sha256": _sha(
            adapter_identity_sha256, field="adapter identity"
        ),
        **{name: str(path.resolve()) for name, path in paths.items()},
        "limits": dict(_LIMITS),
        "automatic_retry": False,
        "result_mergeable_on_failure": False,
        "s3_authorized": False,
    }
    return finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=_nonempty(git_commit, field="git commit"),
        run_id=selected_run,
    )


def verify_completion_authorization(value: Mapping[str, Any]) -> dict[str, Any]:
    envelope = _sealed(value, label="completion authorization")
    payload = envelope["payload"]
    expected_keys = {
        "schema_version",
        "stage",
        "authorization",
        "run_id",
        "history_id",
        "namespace",
        "qualification_file_sha256",
        "qualification_payload_sha256",
        "policy_freeze_file_sha256",
        "adapter_identity_file_sha256",
        "adapter_identity_sha256",
        "consumption_path",
        "result_path",
        "failure_path",
        "limits",
        "automatic_retry",
        "result_mergeable_on_failure",
        "s3_authorized",
    }
    if set(payload) != expected_keys:
        raise CompletionAuthorityError("authorization shape is invalid")
    if (
        payload.get("schema_version") != AUTHORIZATION_SCHEMA
        or payload.get("stage") != "S2-COMPLETION"
        or payload.get("authorization") != AUTHORIZATION_ACTION
        or payload.get("run_id") != envelope.get("run_id")
        or payload.get("history_id") != "07741c45"
        or payload.get("namespace") != "pev3-s1-20260814-001"
        or payload.get("automatic_retry") is not False
        or payload.get("result_mergeable_on_failure") is not False
        or payload.get("s3_authorized") is not False
    ):
        raise CompletionAuthorityError("authorization semantics are invalid")
    if payload.get("limits") != _LIMITS:
        raise CompletionAuthorityError("authorization live budget drift")
    for field in (
        "qualification_file_sha256",
        "qualification_payload_sha256",
        "policy_freeze_file_sha256",
        "adapter_identity_file_sha256",
        "adapter_identity_sha256",
    ):
        _sha(payload.get(field), field=field)
    for field in ("consumption_path", "result_path", "failure_path"):
        path = Path(str(payload.get(field, "")))
        if not path.is_absolute() or path.parent.name != payload["run_id"]:
            raise CompletionAuthorityError("authorization output path drift")
    return envelope


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except FileExistsError:
        raise CompletionAuthorityError("authorization already consumed") from None
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


def consume_completion_authorization(
    *,
    authorization: Mapping[str, Any],
    authorization_file_sha256: str,
    consumption_path: Path,
) -> dict[str, Any]:
    authorized = verify_completion_authorization(authorization)
    payload = authorized["payload"]
    selected_path = Path(consumption_path).resolve()
    if selected_path != Path(payload["consumption_path"]).resolve():
        raise CompletionAuthorityError("authorization consumption path drift")
    consumption_payload = {
        "schema_version": CONSUMPTION_SCHEMA,
        "stage": "S2-COMPLETION",
        "status": "CONSUMED_BEFORE_LIVE_IO",
        "run_id": payload["run_id"],
        "history_id": payload["history_id"],
        "namespace": payload["namespace"],
        "authorization_sha256": _sha(
            authorization_file_sha256, field="authorization file"
        ),
        "authorization_payload_sha256": authorized["payload_sha256"],
        "live_io_performed_at_consumption": False,
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
