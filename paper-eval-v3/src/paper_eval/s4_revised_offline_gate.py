"""Fail-closed aggregate gate for the revised S4 offline validation lanes.

This gate records what has actually been qualified: the amendment, three
offline contracts/frameworks, and their regression evidence.  It also records
what has not happened yet, so framework tests cannot be mistaken for a real
TR0 result, M* exact parity, workload evidence, or live-execution authority.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256
from .real_workload_correctness_contract import (
    RealWorkloadCorrectnessError,
    verify_real_workload_correctness_contract,
)
from .s4_validation_boundary_amendment import (
    S4ValidationBoundaryError,
    verify_s4_validation_boundary_amendment,
)


SCHEMA = "membind.paper-eval-v3.s4-revised-offline-gate.v1"
RUN_ID = "s4-revised-offline-gate-20260815-001"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_NAMES = {
    "tr0_source",
    "tr0_test",
    "fx0_source",
    "fx0_test",
    "fx0_document",
    "real_workload_source",
    "real_workload_test",
    "gate_source",
    "gate_test",
    "gate_finalizer",
}
_LANE_MINIMUM_TESTS = {
    "TR0_SCHEDULING_TRACE_REPLAY": 17,
    "FX0_DETERMINISTIC_MECHANISM_FIXTURE": 17,
    "REAL_WORKLOAD_CORRECTNESS": 15,
    "S4_REVISED_OFFLINE_GATE": 11,
}
_GREEN_FIELDS = {"junit_file_sha256", "tests", "failures", "errors", "skipped"}
_TR0_STATUS = {
    "framework_status": "IMPLEMENTATION_QUALIFIED_ONLY",
    "measured_trace_status": "NOT_SEALED",
    "replay_result_status": "NOT_EXECUTED",
    "supporting_control_only": True,
    "headline_performance_evidence": False,
    "semantic_correctness_evidence": False,
    "real_system_calibration_status": "NOT_SATISFIED",
}
_FX0_STATUS = {
    "framework_status": "HARNESS_QUALIFIED_WITH_TEST_DOUBLE_ONLY",
    "production_m_star_identity_status": "NOT_FROZEN",
    "exact_parity_status": "NOT_EXECUTED",
    "performance_evidence": False,
    "semantic_correctness_evidence": False,
    "adapter_receives_expected_oracle": False,
    "s5_method_qualification_required": True,
}
_AUTHORITY = {
    "s5_offline_design_authorized": True,
    "revised_s4_offline_design_authorized": True,
    "result_generation_or_inspection_authorized": False,
    "model_call_authorized": False,
    "neo4j_read_authorized": False,
    "neo4j_mutation_authorized": False,
    "tr0_live_execution_authorized": False,
    "fx0_live_execution_authorized": False,
    "s5_live_execution_authorized": False,
    "pilot_execution_authorized": False,
    "formal_execution_authorized": False,
    "current_stage_pointer_update_authorized": False,
}
_LEGACY_BOUNDARY = {
    "retry_009_authorized": False,
    "retry_008_resume_authorized": False,
    "retry_008_cleanup_authorized": False,
    "legacy_d0_result_merge_authorized": False,
    "legacy_authority_inheritance_allowed": False,
}
_FORBIDDEN_KEYS = {
    "api_key",
    "credential",
    "messages",
    "password",
    "prompt",
    "raw_content",
    "raw_output",
    "raw_response",
    "secret",
}


class RevisedS4OfflineGateError(ValueError):
    """The revised S4 offline evidence or its authority boundary is invalid."""


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RevisedS4OfflineGateError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RevisedS4OfflineGateError(f"{label} must be a lowercase SHA256")
    return value


def _nonnegative_integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RevisedS4OfflineGateError(f"{label} must be a nonnegative integer")
    return value


def _green(value: object, *, label: str, minimum_tests: int) -> dict[str, Any]:
    evidence = _mapping(value, label=label)
    if set(evidence) != _GREEN_FIELDS:
        raise RevisedS4OfflineGateError(f"{label} evidence shape drift")
    _sha(evidence.get("junit_file_sha256"), label=f"{label} JUnit")
    tests = _nonnegative_integer(evidence.get("tests"), label=f"{label} tests")
    failures = _nonnegative_integer(
        evidence.get("failures"), label=f"{label} failures"
    )
    errors = _nonnegative_integer(evidence.get("errors"), label=f"{label} errors")
    skipped = _nonnegative_integer(
        evidence.get("skipped"), label=f"{label} skipped"
    )
    if tests < minimum_tests or failures or errors or skipped:
        raise RevisedS4OfflineGateError(f"{label} is not a complete GREEN run")
    return evidence


def _red(value: object, *, label: str) -> dict[str, Any]:
    evidence = _mapping(value, label=label)
    if set(evidence) != _GREEN_FIELDS:
        raise RevisedS4OfflineGateError(f"{label} evidence shape drift")
    _sha(evidence.get("junit_file_sha256"), label=f"{label} JUnit")
    tests = _nonnegative_integer(evidence.get("tests"), label=f"{label} tests")
    failures = _nonnegative_integer(
        evidence.get("failures"), label=f"{label} failures"
    )
    errors = _nonnegative_integer(evidence.get("errors"), label=f"{label} errors")
    skipped = _nonnegative_integer(
        evidence.get("skipped"), label=f"{label} skipped"
    )
    if tests < 1 or failures + errors < 1 or skipped:
        raise RevisedS4OfflineGateError(f"{label} is not an expected RED run")
    return evidence


def _reject_private(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_KEYS:
                raise RevisedS4OfflineGateError(
                    "revised S4 gate contains private runtime data"
                )
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def _verified_amendment(value: object) -> dict[str, Any]:
    try:
        amendment = verify_s4_validation_boundary_amendment(
            _mapping(value, label="S4 amendment artifact")
        )
    except (S4ValidationBoundaryError, ValueError) as exc:
        raise RevisedS4OfflineGateError("S4 amendment artifact is invalid") from exc
    payload = amendment["payload"]
    if (
        payload.get("decision")
        != "FULL_INTERNAL_D0_REPLAY_RETIRED_AS_QUALIFICATION_BOUNDARY"
        or payload.get("authority", {}).get("s5_offline_design_authorized") is not True
        or payload.get("authority", {}).get("s5_live_execution_authorized") is not False
    ):
        raise RevisedS4OfflineGateError("S4 amendment boundary drift")
    return amendment


def _verified_real_contract(value: object) -> dict[str, Any]:
    try:
        return verify_real_workload_correctness_contract(
            _mapping(value, label="real-workload correctness contract")
        )
    except (RealWorkloadCorrectnessError, ValueError) as exc:
        raise RevisedS4OfflineGateError(
            "real-workload correctness contract is invalid"
        ) from exc


def _historical_rx0(amendment: Mapping[str, Any]) -> dict[str, Any]:
    history = amendment["payload"]["historical_retry_008"]
    capture = history["capture"]
    replay = history["replay"]
    return {
        "operational_canary_status": capture["status"],
        "real_native_episode_coverage": (
            f"{capture['completed_episode_count']}/{capture['expected_episode_count']}"
        ),
        "headline_performance_evidence": False,
        "timing_exclusion_reason": "CANDIDATE_SIDECAR_CAPTURE_ADDED_WORK",
        "legacy_d0_status": replay["status"],
        "legacy_d0_mergeable": replay["mergeable"],
        "legacy_d0_error_code": replay["error_code"],
    }


def _cross_check_contract(
    contract: Mapping[str, Any],
    amendment: Mapping[str, Any],
    amendment_artifact_file_sha256: str,
) -> None:
    payload = contract["payload"]
    bindings = payload["input_bindings"]
    amendment_binding = bindings["s4_amendment_artifact"]
    amendment_bindings = amendment["payload"]["input_bindings"]
    if (
        amendment_binding.get("file_sha256") != amendment_artifact_file_sha256
        or amendment_binding.get("payload_sha256") != amendment["payload_sha256"]
        or amendment_binding.get("run_id") != amendment["run_id"]
        or bindings["parent_protocol"].get("file_sha256")
        != amendment_bindings["parent_protocol"].get("file_sha256")
        or bindings["s4_amendment_document"].get("file_sha256")
        != amendment_bindings["amendment_document"].get("file_sha256")
        or bindings["current_stage_pointer"].get("file_sha256")
        != amendment_bindings["current_stage_pointer"].get("file_sha256")
        or bindings["current_stage_pointer"].get("current_stage")
        != "S3_CONFIGURATION_FROZEN"
    ):
        raise RevisedS4OfflineGateError(
            "real-workload contract is not bound to the current S4 amendment"
        )


def build_revised_s4_offline_gate(
    *,
    amendment_artifact: Mapping[str, Any],
    amendment_artifact_file_sha256: str,
    real_workload_contract: Mapping[str, Any],
    source_file_sha256: Mapping[str, str],
    focused_green_evidence: Mapping[str, Mapping[str, Any]],
    red_evidence: Mapping[str, Mapping[str, Any]],
    full_regression_evidence: Mapping[str, Any],
    git_commit: str,
) -> dict[str, Any]:
    """Seal the revised S4 framework gate without granting live authority."""

    if not isinstance(git_commit, str) or not git_commit:
        raise RevisedS4OfflineGateError("git commit is required")
    amendment = _verified_amendment(amendment_artifact)
    amendment_file_sha = _sha(
        amendment_artifact_file_sha256, label="S4 amendment artifact file"
    )
    contract = _verified_real_contract(real_workload_contract)
    _cross_check_contract(contract, amendment, amendment_file_sha)

    sources = _mapping(source_file_sha256, label="source file inventory")
    if set(sources) != _SOURCE_NAMES:
        raise RevisedS4OfflineGateError("source file inventory drift")
    sources = {name: _sha(sources[name], label=name) for name in sorted(sources)}

    focused = _mapping(focused_green_evidence, label="focused GREEN inventory")
    if set(focused) != set(_LANE_MINIMUM_TESTS):
        raise RevisedS4OfflineGateError("focused GREEN lane inventory drift")
    focused = {
        lane: _green(
            focused[lane],
            label=f"{lane} focused GREEN",
            minimum_tests=minimum,
        )
        for lane, minimum in sorted(_LANE_MINIMUM_TESTS.items())
    }
    red = _mapping(red_evidence, label="RED inventory")
    if set(red) != set(_LANE_MINIMUM_TESTS):
        raise RevisedS4OfflineGateError("RED lane inventory drift")
    red = {
        lane: _red(red[lane], label=f"{lane} RED")
        for lane in sorted(_LANE_MINIMUM_TESTS)
    }
    full = _green(
        full_regression_evidence,
        label="paper-eval-v3 full regression",
        minimum_tests=900,
    )

    payload = {
        "schema_version": SCHEMA,
        "stage": "S4_REVISED_OFFLINE_GATE",
        "status": "OFFLINE_FRAMEWORKS_QUALIFIED_ONLY",
        "current_stage": "S3_CONFIGURATION_FROZEN",
        "boundary_amendment": {
            "artifact_file_sha256": amendment_file_sha,
            "artifact": amendment,
        },
        "historical_rx0": _historical_rx0(amendment),
        "tr0": deepcopy(_TR0_STATUS),
        "fx0": deepcopy(_FX0_STATUS),
        "real_workload_correctness": {
            "contract_status": "FROZEN_OFFLINE",
            "result_status": "NOT_EXECUTED",
            "matching_oracle_status": "NOT_FROZEN",
            "quality_margins_status": "NOT_FROZEN",
            "contract": contract,
        },
        "source_file_sha256": sources,
        "focused_green_evidence": focused,
        "red_evidence": red,
        "full_regression_evidence": full,
        "legacy_boundary": deepcopy(_LEGACY_BOUNDARY),
        "next_action": "S5_PRODUCTION_METHOD_QUALIFICATION_OFFLINE_DESIGN",
        "authority": deepcopy(_AUTHORITY),
    }
    _reject_private(payload)
    return verify_revised_s4_offline_gate(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=git_commit,
            run_id=RUN_ID,
        )
    )


def verify_revised_s4_offline_gate(value: Mapping[str, Any]) -> dict[str, Any]:
    """Re-verify every nested contract and reject authority inflation."""

    artifact = _mapping(value, label="revised S4 offline gate")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise RevisedS4OfflineGateError("revised S4 envelope shape drift")
    payload = _mapping(artifact.get("payload"), label="revised S4 payload")
    expected_payload_fields = {
        "schema_version",
        "stage",
        "status",
        "current_stage",
        "boundary_amendment",
        "historical_rx0",
        "tr0",
        "fx0",
        "real_workload_correctness",
        "source_file_sha256",
        "focused_green_evidence",
        "red_evidence",
        "full_regression_evidence",
        "legacy_boundary",
        "next_action",
        "authority",
    }
    if (
        set(payload) != expected_payload_fields
        or artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("run_id") != RUN_ID
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or payload.get("schema_version") != SCHEMA
        or payload.get("stage") != "S4_REVISED_OFFLINE_GATE"
        or payload.get("status") != "OFFLINE_FRAMEWORKS_QUALIFIED_ONLY"
        or payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
    ):
        raise RevisedS4OfflineGateError("revised S4 identity or envelope drift")

    boundary = _mapping(payload.get("boundary_amendment"), label="boundary amendment")
    if set(boundary) != {"artifact_file_sha256", "artifact"}:
        raise RevisedS4OfflineGateError("boundary amendment binding drift")
    amendment_file_sha = _sha(
        boundary.get("artifact_file_sha256"), label="boundary amendment file"
    )
    amendment = _verified_amendment(boundary.get("artifact"))
    if payload.get("historical_rx0") != _historical_rx0(amendment):
        raise RevisedS4OfflineGateError("historical RX0 interpretation drift")

    correctness = _mapping(
        payload.get("real_workload_correctness"),
        label="real-workload correctness status",
    )
    if set(correctness) != {
        "contract_status",
        "result_status",
        "matching_oracle_status",
        "quality_margins_status",
        "contract",
    }:
        raise RevisedS4OfflineGateError("real-workload correctness shape drift")
    contract = _verified_real_contract(correctness.get("contract"))
    _cross_check_contract(contract, amendment, amendment_file_sha)
    if (
        correctness.get("contract_status") != "FROZEN_OFFLINE"
        or correctness.get("result_status") != "NOT_EXECUTED"
        or correctness.get("matching_oracle_status") != "NOT_FROZEN"
        or correctness.get("quality_margins_status") != "NOT_FROZEN"
    ):
        raise RevisedS4OfflineGateError("real-workload result status drift")

    sources = _mapping(payload.get("source_file_sha256"), label="source inventory")
    if set(sources) != _SOURCE_NAMES:
        raise RevisedS4OfflineGateError("source file inventory drift")
    for name, digest in sources.items():
        _sha(digest, label=name)

    focused = _mapping(
        payload.get("focused_green_evidence"), label="focused GREEN inventory"
    )
    if set(focused) != set(_LANE_MINIMUM_TESTS):
        raise RevisedS4OfflineGateError("focused GREEN lane inventory drift")
    for lane, minimum in _LANE_MINIMUM_TESTS.items():
        _green(
            focused[lane],
            label=f"{lane} focused GREEN",
            minimum_tests=minimum,
        )
    red = _mapping(payload.get("red_evidence"), label="RED inventory")
    if set(red) != set(_LANE_MINIMUM_TESTS):
        raise RevisedS4OfflineGateError("RED lane inventory drift")
    for lane in _LANE_MINIMUM_TESTS:
        _red(red[lane], label=f"{lane} RED")
    _green(
        payload.get("full_regression_evidence"),
        label="paper-eval-v3 full regression",
        minimum_tests=900,
    )

    if (
        payload.get("tr0") != _TR0_STATUS
        or payload.get("fx0") != _FX0_STATUS
        or payload.get("legacy_boundary") != _LEGACY_BOUNDARY
        or payload.get("next_action")
        != "S5_PRODUCTION_METHOD_QUALIFICATION_OFFLINE_DESIGN"
        or payload.get("authority") != _AUTHORITY
    ):
        raise RevisedS4OfflineGateError("revised S4 claim or authority drift")
    _reject_private(artifact)
    artifact["payload"] = payload
    return artifact
