"""Prospective v1.3 protocol gates.

This module is deliberately small.  The v1.2 runner, dataset, namespace,
instrumentation, QA, and reducer remain the implementation dependencies; v1.3
only changes the two gates that made a new campaign depend on historical
runtime identity and on an ambiguous ``tests_all_green`` boolean.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .resource_evidence import _neo4j_missing, _provider_missing


V1_3_PROTOCOL_VERSION = "SATURATED_FIXED_WORK_CONSTRUCTION_PROTOCOL_V1_3"
RESOURCE_ENVELOPE_SCHEMA = "membind.saturated-fixed-work.resource-envelope.v2"


class CampaignResourceError(ValueError):
    """The current campaign resource envelope is invalid or mismatched."""


class TestQualificationError(ValueError):
    """Test evidence is malformed or the qualification gate failed."""

    __test__ = False


class V1_3PreflightError(ValueError):
    """A current-campaign L0 prerequisite is absent."""


@dataclass(frozen=True)
class ResourceIdentitySnapshot:
    """Low-frequency, envelope-bound identity observation."""

    envelope: Mapping[str, Any]
    captured_at_ns: int


@dataclass(frozen=True)
class ProviderTelemetrySample:
    """Lightweight 1 Hz provider observation; it carries no identity scan."""

    gpus: Sequence[Mapping[str, Any]]
    captured_at_ns: int


_GPU_UUID = re.compile(
    r"^GPU-[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}$"
)
_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CAMPAIGN_METADATA_FIELDS = (
    "runner_commit",
    "workload_manifest_sha256",
    "protocol_config_sha256",
)
_EPHEMERAL_KEYS = {
    "boot_id",
    "observed_at",
    "compute_processes",
    "process_tree",
    "telemetry",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _normalize_provider(provider: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the v2 provider snapshot to the v1 resource contract.

    The provider collector reports listener PID and MiB because those are the
    natural procfs/nvidia-smi representations.  The resource validator keeps
    those live values as evidence while accepting the v1.2 envelope shape for
    shared validation.  No historical values are synthesized.
    """

    selected = copy.deepcopy(dict(provider))
    gpus = selected.get("gpus")
    if isinstance(gpus, list):
        for gpu in gpus:
            if not isinstance(gpu, dict):
                continue
            if "memory_total_bytes" not in gpu and isinstance(
                gpu.get("memory_total_mib"), (int, float)
            ):
                gpu["memory_total_bytes"] = int(float(gpu["memory_total_mib"]) * 1024 * 1024)
    services = selected.get("services")
    if isinstance(services, Mapping):
        normalized: dict[str, Any] = {}
        for port, value in services.items():
            if not isinstance(value, Mapping):
                normalized[str(port)] = value
                continue
            service = dict(value)
            if "pid" not in service and isinstance(service.get("listener_pid"), int):
                service["pid"] = service["listener_pid"]
            normalized[str(port)] = service
        selected["services"] = normalized
    return selected


def _is_ephemeral_key(key: str) -> bool:
    lowered = key.lower()
    return (
        lowered in _EPHEMERAL_KEYS
        or lowered.endswith("_pid")
        or lowered == "pid"
        or "start_time" in lowered
    )


def _stable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _stable(item)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
            if not _is_ephemeral_key(str(key))
        }
    if isinstance(value, list):
        return [_stable(item) for item in value]
    if isinstance(value, tuple):
        return [_stable(item) for item in value]
    return value


def _mismatch_paths(left: Any, right: Any, prefix: str = "resource") -> list[str]:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return [
            path
            for key in sorted(set(left) | set(right), key=str)
            for path in _mismatch_paths(
                left.get(key), right.get(key), f"{prefix}.{key}"
            )
        ]
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return [prefix]
        return [
            path
            for index, (l_item, r_item) in enumerate(zip(left, right, strict=True))
            for path in _mismatch_paths(l_item, r_item, f"{prefix}[{index}]")
        ]
    return [] if left == right else [prefix]


def build_campaign_resource_envelope(
    *,
    live_provider: Mapping[str, Any],
    runner_neo4j: Mapping[str, Any],
    campaign_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the v1.3 *current campaign* envelope.

    Historical provider evidence is intentionally not an argument.  The
    resulting ID is a hash of stable physical/software/configuration identity;
    PIDs, boot IDs, process trees, and telemetry are retained only as
    ephemeral observations and cannot change the campaign ID.
    """

    if not isinstance(live_provider, Mapping) or not isinstance(runner_neo4j, Mapping):
        raise CampaignResourceError("RESOURCE_INPUT_INVALID")
    provider = _normalize_provider(live_provider)
    neo4j = copy.deepcopy(dict(runner_neo4j))
    metadata = (
        copy.deepcopy(dict(campaign_metadata))
        if isinstance(campaign_metadata, Mapping)
        else {}
    )
    provider_missing = _provider_missing(provider, "live_provider")
    neo4j_missing = _neo4j_missing(neo4j)
    metadata_missing: list[str] = []
    for field in _CAMPAIGN_METADATA_FIELDS:
        value = metadata.get(field)
        valid = isinstance(value, str) and bool(value.strip())
        if field == "runner_commit":
            valid = valid and _COMMIT.fullmatch(value) is not None
        else:
            valid = valid and _SHA256.fullmatch(value) is not None
        if not valid:
            metadata_missing.append(f"campaign_metadata.{field}")
    missing = sorted(set([*provider_missing, *neo4j_missing, *metadata_missing]))
    stable_identity = {
        "provider": _stable(provider),
        "runner_neo4j": _stable(neo4j),
        "campaign_metadata": _stable(metadata),
    }
    verified = not missing
    return {
        "schema_version": RESOURCE_ENVELOPE_SCHEMA,
        "protocol_version": V1_3_PROTOCOL_VERSION,
        "status": "PASS" if verified else "INVALID",
        "current_campaign_only": True,
        "historical_resource_parity_required": False,
        "historical_resource_match": "NOT_APPLICABLE",
        "live_resource_envelope_verified": verified,
        "resource_envelope_id": _hash(stable_identity) if verified else None,
        "stable_resource_identity": stable_identity,
        "provider_gpu_uuids": sorted(
            gpu["uuid"]
            for gpu in provider.get("gpus", [])
            if isinstance(gpu, Mapping)
            and isinstance(gpu.get("uuid"), str)
            and _GPU_UUID.fullmatch(gpu["uuid"]) is not None
        ),
        "ephemeral_runtime_identity": {
            "provider": {
                "boot_id": provider.get("boot_id"),
                "services": {
                    str(port): {
                        key: value
                        for key, value in service.items()
                        if _is_ephemeral_key(str(key))
                    }
                    for port, service in (provider.get("services") or {}).items()
                    if isinstance(service, Mapping)
                },
            },
            "runner_neo4j": {"pid": neo4j.get("pid")},
        },
        "missing_evidence": missing,
        "live_provider": provider,
        "runner_neo4j": neo4j,
        "campaign_metadata": metadata,
    }


def require_campaign_resource_gate(envelope: Mapping[str, Any]) -> dict[str, Any]:
    stable_identity = envelope.get("stable_resource_identity") if isinstance(envelope, Mapping) else None
    if (
        not isinstance(envelope, Mapping)
        or envelope.get("status") != "PASS"
        or envelope.get("protocol_version") != V1_3_PROTOCOL_VERSION
        or envelope.get("current_campaign_only") is not True
        or envelope.get("historical_resource_parity_required") is not False
        or envelope.get("historical_resource_match") != "NOT_APPLICABLE"
        or envelope.get("live_resource_envelope_verified") is not True
        or not isinstance(envelope.get("resource_envelope_id"), str)
        or envelope.get("missing_evidence") != []
        or not isinstance(stable_identity, Mapping)
        or envelope.get("resource_envelope_id") != _hash(stable_identity)
    ):
        raise CampaignResourceError("RESOURCE_GATE_FAILED")
    return {
        "schema_version": "membind.saturated-fixed-work.resource-gate.v2",
        "authorized": True,
        "resource_envelope_id": envelope["resource_envelope_id"],
    }


def compare_campaign_resource_envelopes(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare B0/B1 (or restart) envelopes while ignoring ephemeral PIDs."""

    try:
        require_campaign_resource_gate(left)
        require_campaign_resource_gate(right)
    except CampaignResourceError:
        return {"passed": False, "mismatches": ["resource_envelope_invalid"]}
    left_identity = left.get("stable_resource_identity")
    right_identity = right.get("stable_resource_identity")
    mismatches = _mismatch_paths(left_identity, right_identity)
    return {
        "passed": not mismatches,
        "mismatches": mismatches,
        "left_resource_envelope_id": left.get("resource_envelope_id"),
        "right_resource_envelope_id": right.get("resource_envelope_id"),
    }


def require_same_campaign_resource_envelope(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> dict[str, Any]:
    comparison = compare_campaign_resource_envelopes(left, right)
    if comparison["passed"] is not True:
        raise CampaignResourceError("RESOURCE_ENVELOPE_MISMATCH")
    return comparison


def requalify_after_restart(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    """Allow ephemeral PID changes only after stable identity comparison."""

    comparison = compare_campaign_resource_envelopes(previous, current)
    return {
        **comparison,
        "restart_requalification_required": True,
        "pid_change_allowed": comparison["passed"] is True,
    }


def capture_resource_identity(
    *,
    live_provider: Mapping[str, Any],
    runner_neo4j: Mapping[str, Any],
    campaign_metadata: Mapping[str, Any],
    captured_at_ns: int,
) -> ResourceIdentitySnapshot:
    if isinstance(captured_at_ns, bool) or not isinstance(captured_at_ns, int) or captured_at_ns <= 0:
        raise CampaignResourceError("RESOURCE_CAPTURE_TIMESTAMP_INVALID")
    envelope = build_campaign_resource_envelope(
        live_provider=live_provider,
        runner_neo4j=runner_neo4j,
        campaign_metadata=campaign_metadata,
    )
    require_campaign_resource_gate(envelope)
    return ResourceIdentitySnapshot(envelope=envelope, captured_at_ns=captured_at_ns)


def _failure_records(value: Sequence[Mapping[str, Any]], *, field: str) -> list[dict[str, str]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TestQualificationError(f"{field.upper()}_INVALID")
    rows: list[dict[str, str]] = []
    for row in value:
        if not isinstance(row, Mapping):
            raise TestQualificationError("FAILURE_RECORD_INVALID")
        test_id, signature = row.get("test_id"), row.get("signature")
        if not isinstance(test_id, str) or not test_id.strip() or not isinstance(signature, str) or not signature.strip():
            raise TestQualificationError("FAILURE_RECORD_INVALID")
        rows.append({"test_id": test_id, "signature": signature})
    return rows


def evaluate_test_qualification(
    *,
    sfwb_failures: Sequence[Mapping[str, Any]],
    targeted_failures: Sequence[Mapping[str, Any]],
    repository_failures: Sequence[Mapping[str, Any]],
    clean_head_failures: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate the v1.3 ``NEW_REGRESSION_COUNT == 0`` contract.

    Repository-wide failures are accepted only when the exact test ID and
    failure signature are reproduced at clean HEAD.  SFWB-owned and targeted
    failures are always unexpected, even if a caller tries to label them as
    pre-existing.
    """

    sfwb = _failure_records(sfwb_failures, field="sfwb_failures")
    targeted = _failure_records(targeted_failures, field="targeted_failures")
    repository = _failure_records(repository_failures, field="repository_failures")
    clean = _failure_records(clean_head_failures, field="clean_head_failures")
    clean_keys = {(row["test_id"], row["signature"]) for row in clean}
    preexisting = [row for row in repository if (row["test_id"], row["signature"]) in clean_keys]
    repository_new = [row for row in repository if (row["test_id"], row["signature"]) not in clean_keys]
    unexpected = [
        {"scope": "sfwb", **row} for row in sfwb
    ] + [
        {"scope": "targeted", **row} for row in targeted
    ] + [
        {"scope": "repository", **row} for row in repository_new
    ]
    count = len(unexpected)
    if repository and preexisting and not repository_new:
        repository_status = "PASS_WITH_PREEXISTING_FAILURES"
    elif repository:
        repository_status = "FAIL_NEW_REGRESSIONS"
    else:
        repository_status = "PASS"
    return {
        "schema_version": "membind.saturated-fixed-work.test-qualification.v1",
        "qualification_semantics": "NEW_REGRESSION_COUNT_ZERO",
        "sfwb_failure_count": len(sfwb),
        "targeted_failure_count": len(targeted),
        "repository_failure_count": len(repository),
        "clean_head_failure_count": len(clean),
        "preexisting_repository_failures": preexisting,
        "unexpected_failures": unexpected,
        "new_regression_count": count,
        "repository_wide_status": repository_status,
        "qualification_passed": count == 0,
    }


def require_test_qualification(result: Mapping[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(result, Mapping)
        or result.get("qualification_semantics") != "NEW_REGRESSION_COUNT_ZERO"
        or result.get("qualification_passed") is not True
        or result.get("new_regression_count") != 0
    ):
        raise TestQualificationError("TEST_QUALIFICATION_FAILED")
    return {"authorized": True, "new_regression_count": 0}


_PREFLIGHT_GATES = (
    "workload_manifest_valid",
    "resource_envelope_captured",
    "resource_envelope_shared",
    "construction_healthy",
    "embedding_healthy",
    "neo4j_healthy",
    "models_config_correct",
    "services_idle",
    "warmup_passed",
    "telemetry_available",
    "test_qualification_passed",
)


def validate_v1_3_preflight(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Pure L0 readiness check for a new campaign; it performs no I/O."""

    if not isinstance(evidence, Mapping):
        raise V1_3PreflightError("PREFLIGHT_EVIDENCE_INVALID")
    for field in _PREFLIGHT_GATES:
        if evidence.get(field) is not True:
            raise V1_3PreflightError(field.upper() + "_FAILED")
    count = evidence.get("new_regression_count")
    if isinstance(count, bool) or not isinstance(count, int) or count != 0:
        raise V1_3PreflightError("NEW_REGRESSION_COUNT_NONZERO")
    return {
        "schema_version": "membind.saturated-fixed-work.preflight.v2",
        "protocol_version": V1_3_PROTOCOL_VERSION,
        "status": "PASS",
        "formal_run_authorized": True,
        "required_gates": list(_PREFLIGHT_GATES) + ["new_regression_count_zero"],
    }


def build_v1_3_sampler_layers(
    *,
    identity_probe: Any,
    telemetry_probes: Mapping[str, Any],
) -> dict[str, Any]:
    """Return separate low-frequency identity and high-frequency probe lanes.

    ``identity_probe`` is intentionally returned as a callable and is never
    invoked while building or sampling the telemetry lane.  The caller runs it
    at campaign preflight and at restart boundaries, then feeds only
    ``telemetry`` to the existing 1 Hz :class:`PeriodicSampler`.
    """

    if not callable(identity_probe) or not isinstance(telemetry_probes, Mapping):
        raise CampaignResourceError("SAMPLER_LAYER_CONFIGURATION_INVALID")
    selected = dict(telemetry_probes)
    if not selected or any(not isinstance(name, str) or not callable(probe) for name, probe in selected.items()):
        raise CampaignResourceError("SAMPLER_TELEMETRY_PROBES_INVALID")
    return {"identity": identity_probe, "telemetry": selected}


__all__ = [
    "CampaignResourceError",
    "ProviderTelemetrySample",
    "RESOURCE_ENVELOPE_SCHEMA",
    "ResourceIdentitySnapshot",
    "TestQualificationError",
    "V1_3PreflightError",
    "V1_3_PROTOCOL_VERSION",
    "build_campaign_resource_envelope",
    "build_v1_3_sampler_layers",
    "compare_campaign_resource_envelopes",
    "capture_resource_identity",
    "evaluate_test_qualification",
    "require_campaign_resource_gate",
    "require_same_campaign_resource_envelope",
    "require_test_qualification",
    "requalify_after_restart",
    "validate_v1_3_preflight",
]
