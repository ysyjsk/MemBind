"""Attempt-scoped execution identity for S4 bilateral-sidecar retries."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256
from .s4_d0_contract import verify_s4_d0_contract
from .s4_edge_identity_diagnosis import verify_edge_identity_diagnosis
from .s4_remap_retry_contract import verify_s4_remap_retry_contract


SCHEMA = "membind.paper-eval-v3.s4-sidecar-retry-contract.v1"
_RUN_DATE = "20260815"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BASE_SOURCE_NAMES = {
    "candidate_oracle",
    "candidate_projection",
    "candidate_sidecar",
    "candidate_sidecar_runtime",
    "contract",
    "production",
    "runner",
    "test",
}
_EDGE_IDENTITY_SOURCE = "edge_identity"


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is not a SHA256")
    return value


def _sources(value: object, *, attempt: str = "006") -> dict[str, str]:
    selected = _mapping(value, label="S4 sidecar contract sources")
    expected = set(_BASE_SOURCE_NAMES)
    if int(attempt) >= 7:
        expected.add(_EDGE_IDENTITY_SOURCE)
    if set(selected) != expected:
        raise ValueError("S4 sidecar contract source inventory drift")
    return {
        name: _sha(selected[name], field=f"source {name}")
        for name in sorted(selected)
    }


def _offline_evidence(value: object) -> dict[str, Any]:
    selected = _mapping(value, label="S4 sidecar offline evidence")
    if set(selected) != {
        "focused_junit_sha256",
        "focused_pass_count",
        "full_junit_sha256",
        "full_pass_count",
    }:
        raise ValueError("S4 sidecar offline evidence shape drift")
    for field in ("focused_junit_sha256", "full_junit_sha256"):
        _sha(selected[field], field=field)
    for field in ("focused_pass_count", "full_pass_count"):
        count = selected[field]
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise ValueError("S4 sidecar offline gate is incomplete")
    return selected


def _reject_private(value: object) -> None:
    forbidden = {
        "answer",
        "api_key",
        "body",
        "content",
        "fact",
        "messages",
        "password",
        "prompt",
        "prompt_parts",
        "question",
        "raw_output",
        "raw_response",
        "response",
        "secret",
        "uuid",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in forbidden:
                raise ValueError("S4 sidecar retry contract contains private data")
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def _runs(attempt: str = "006") -> dict[str, dict[str, str]]:
    cache_id = f"s4-d0-sidecar-07741c45-20260815-{attempt}"
    return {
        "U0_CAPTURE": {
            "cache_id": cache_id,
            "method": "U0",
            "mode": "capture",
            "namespace": f"pev3-s4-u0-capture-20260815-{attempt}",
            "run_id": f"s4-d0-capture-20260815-{attempt}",
        },
        "D0_READ_ONLY_REPLAY": {
            "cache_id": cache_id,
            "method": "D0",
            "mode": "replay",
            "namespace": f"pev3-s4-d0-replay-20260815-{attempt}",
            "run_id": f"s4-d0-replay-20260815-{attempt}",
        },
    }


def _private_cache(attempt: str = "006") -> dict[str, Any]:
    root = f"runtime/private/s4-d0-sidecar-07741c45-20260815-{attempt}"
    return {
        "prompt_relpath": f"{root}/prompt.jsonl",
        "embedding_relpath": f"{root}/embedding.jsonl",
        "candidate_sidecar_relpath": f"{root}/candidate-sidecar.jsonl",
        "reportable_contents": False,
    }


def _candidate_oracle() -> dict[str, Any]:
    return {
        "wrapper_order": [
            "GraphitiPromptCacheLLM",
            "NamespaceNormalizedPromptCache",
            "CandidateSidecarPromptCache",
            "CandidateAwareReplayCache",
            "PromptCache",
        ],
        "node_translation_kind": "VERIFIED_PROMPT_VISIBLE_CANDIDATE_ID_BIJECTION",
        "edge_translation_kind": (
            "BILATERAL_UUID_INDEPENDENT_LOGICAL_EDGE_BIJECTION"
        ),
        "edge_prompt_name": "dedupe_edges.resolve_edge",
        "capture_only_sidecar_allowed": False,
        "partition_preserving": True,
        "exact_edge_prompt_requires_sidecar_binding": True,
        "persistent_cache_mutation": False,
        "raw_or_parsed_cache_write": False,
        "position_rank_uuid_identity_allowed": False,
        "pre_publication_remaining_call_guard": True,
    }


def _hard_gates() -> dict[str, Any]:
    return {
        "cache_and_sidecar_mutation_during_replay": False,
        "candidate_remap_rejection_count": 0,
        "canonical_graph_parity": "EXACT_100_PERCENT",
        "edge_sidecar_resolution_accounting": "EXACT",
        "sidecar_consumed_equals_record_count": True,
        "sidecar_prepared_count": 0,
        "sidecar_rejection_count": 0,
        "sidecar_remaining_count": 0,
    }


def _authority() -> dict[str, bool]:
    return {
        "preflight_authorized": True,
        "live_execution_authorized": False,
        "s4_four_history_qualification_authorized": False,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }


def _diagnosis(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        identity = _mapping(
            value.get("execution_identity"), label="diagnosis execution identity"
        )
        selected = verify_edge_identity_diagnosis(
            value,
            expected_evidence_sha256=_mapping(
                value.get("evidence_sha256"), label="diagnosis evidence"
            ),
            expected_source_hash=identity.get("source_hash"),
            expected_episode_manifest_sha256=identity.get(
                "episode_manifest_sha256"
            ),
        )
    except Exception as error:
        raise ValueError("S4 sidecar diagnosis is invalid") from error
    if (
        selected.get("verdict") != "SIDECAR_AMENDMENT_JUSTIFIED"
        or selected.get("reason") != "REPLAY_PREFIX_IDENTITY_UNIQUE"
        or len(selected.get("candidate_call_diagnoses", [])) != 10
        or selected.get("claim_limits")
        != {
            "retry_005_capture_replay_bijection_proved": False,
            "retry_006_authorized": False,
            "cleanup_authorized": False,
            "fixed_four_qualification_authorized": False,
            "s5_authorized": False,
        }
    ):
        raise ValueError("S4 sidecar diagnosis does not justify the amendment")
    return selected


def build_s4_sidecar_retry_contract(
    *,
    parent_contract: Mapping[str, Any],
    parent_contract_file_sha256: str,
    prior_retry_contract: Mapping[str, Any],
    prior_retry_contract_file_sha256: str,
    diagnosis: Mapping[str, Any],
    diagnosis_file_sha256: str,
    amendment_file_sha256: str,
    projection_schema_sha256: str,
    offline_evidence: Mapping[str, Any],
    source_sha256: Mapping[str, str],
    attempt_number: int = 6,
) -> dict[str, Any]:
    parent = verify_s4_d0_contract(parent_contract)
    prior = verify_s4_remap_retry_contract(prior_retry_contract)
    diagnosed = _diagnosis(diagnosis)
    if prior.get("attempt_id") != "005":
        raise ValueError("S4 sidecar retry must follow retry-005")
    if (
        not isinstance(attempt_number, int)
        or isinstance(attempt_number, bool)
        or not 6 <= attempt_number <= 999
    ):
        raise ValueError("S4 sidecar retry attempt must be in [6, 999]")
    attempt = f"{attempt_number:03d}"
    body = {
        "schema_version": SCHEMA,
        "stage": "S4",
        "status": "BILATERAL_SIDECAR_RETRY_EXECUTION_IDENTITY_FROZEN",
        "attempt_id": attempt,
        "parent_contract_file_sha256": _sha(
            parent_contract_file_sha256, field="parent contract file"
        ),
        "parent_contract_sha256": parent["contract_sha256"],
        "prior_retry_contract_file_sha256": _sha(
            prior_retry_contract_file_sha256, field="prior retry contract file"
        ),
        "prior_retry_contract_sha256": prior["contract_sha256"],
        "diagnosis_file_sha256": _sha(
            diagnosis_file_sha256, field="diagnosis file"
        ),
        "diagnosis_artifact_sha256": diagnosed["artifact_sha256"],
        "amendment_file_sha256": _sha(
            amendment_file_sha256, field="amendment file"
        ),
        "projection_schema_sha256": _sha(
            projection_schema_sha256, field="projection schema"
        ),
        "offline_evidence": _offline_evidence(offline_evidence),
        "history": deepcopy(parent["history"]),
        "execution_order": ["U0_CAPTURE", "D0_READ_ONLY_REPLAY"],
        "common_method_policy_sha256": parent["common_method_policy_sha256"],
        "runs": _runs(attempt),
        "private_cache": _private_cache(attempt),
        "candidate_oracle": _candidate_oracle(),
        "sidecar_hard_gates": _hard_gates(),
        "source_sha256": _sources(source_sha256, attempt=attempt),
        "authority": _authority(),
    }
    return verify_s4_sidecar_retry_contract(
        {**body, "contract_sha256": payload_sha256(body)}
    )


def verify_s4_sidecar_retry_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    selected = _mapping(value, label="S4 sidecar retry contract")
    stored = selected.pop("contract_sha256", None)
    expected_fields = {
        "schema_version",
        "stage",
        "status",
        "attempt_id",
        "parent_contract_file_sha256",
        "parent_contract_sha256",
        "prior_retry_contract_file_sha256",
        "prior_retry_contract_sha256",
        "diagnosis_file_sha256",
        "diagnosis_artifact_sha256",
        "amendment_file_sha256",
        "projection_schema_sha256",
        "offline_evidence",
        "history",
        "execution_order",
        "common_method_policy_sha256",
        "runs",
        "private_cache",
        "candidate_oracle",
        "sidecar_hard_gates",
        "source_sha256",
        "authority",
    }
    if set(selected) != expected_fields or stored != payload_sha256(selected):
        raise ValueError("S4 sidecar retry contract shape or hash drift")
    attempt = selected.get("attempt_id")
    if (
        selected.get("schema_version") != SCHEMA
        or selected.get("stage") != "S4"
        or selected.get("status")
        != "BILATERAL_SIDECAR_RETRY_EXECUTION_IDENTITY_FROZEN"
        or not isinstance(attempt, str)
        or re.fullmatch(r"\d{3}", attempt) is None
        or int(attempt) < 6
        or selected.get("history")
        != {
            "data_role": "DEVELOPMENT_EXPOSED",
            "episode_count": 49,
            "history_id": "07741c45",
        }
        or selected.get("execution_order")
        != ["U0_CAPTURE", "D0_READ_ONLY_REPLAY"]
        or selected.get("runs") != _runs(attempt)
        or selected.get("private_cache") != _private_cache(attempt)
        or selected.get("candidate_oracle") != _candidate_oracle()
        or selected.get("sidecar_hard_gates") != _hard_gates()
        or selected.get("authority") != _authority()
    ):
        raise ValueError("S4 sidecar retry identity or policy drift")
    for field in (
        "parent_contract_file_sha256",
        "parent_contract_sha256",
        "prior_retry_contract_file_sha256",
        "prior_retry_contract_sha256",
        "diagnosis_file_sha256",
        "diagnosis_artifact_sha256",
        "amendment_file_sha256",
        "projection_schema_sha256",
        "common_method_policy_sha256",
    ):
        _sha(selected.get(field), field=field)
    _offline_evidence(selected.get("offline_evidence"))
    _sources(selected.get("source_sha256"), attempt=attempt)
    _reject_private(selected)
    return {**selected, "contract_sha256": stored}


def finalize_s4_sidecar_retry_contract(
    *, path: Path, contract: Mapping[str, Any]
) -> dict[str, Any]:
    verified = verify_s4_sidecar_retry_contract(contract)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(verified, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
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
    return verified
