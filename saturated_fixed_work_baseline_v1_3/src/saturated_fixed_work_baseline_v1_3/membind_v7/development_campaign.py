"""Temporary-provider V7 development campaign contracts and materializer.

This module deliberately separates implementation selection from formal live
authorization.  A real Graphiti campaign may characterize the provisional
provider and authorize one implementation, but it cannot mutate the scientific
``METHOD_SELECTION.json`` or authorize a live treatment campaign.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .gates import evaluate_opportunity_gates
from .observer_campaign import (
    classify_observer_failure,
    verify_observer_manifest,
    write_observer_artifacts,
)


class DevelopmentCampaignError(RuntimeError):
    """The development campaign cannot establish its frozen contract."""


_DIGEST = re.compile(r"[0-9a-f]{64}")
_COMPOSITE_FREEZE_SHA256 = (
    "428826c09bf0ed33e72cbdb220721e0714124222c740f7af37a0d158538d4742"
)
_SOURCE_PROTOCOL_SHA256 = (
    "a3abb7e6ea481952ed868886bfd958bad9060812e42ca1eb3d96e46a1d77dd0a"
)
_SCIENTIFIC_METHOD_SELECTION_SHA256 = (
    "0a2958aeeedc2b7b8762247d5d6cf15252e2b6b737b5807a51a127461a632c53"
)
_DATASET_SHA256 = (
    "97fd80207f3419fc57c3684db824334224546d6bdd62c17ef52cd116eec9ffc8"
)
_CONSTRUCTION_AUTHORITY = (
    "alibaba-bailian-openai-compatible-engineering-json-object-v1"
)
_EMBEDDING_AUTHORITY = "siliconflow-openai-compatible-v1"
_LEGAL_METHODS = frozenset({"M0", "M1", "M2"})


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(child) for child in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(child) for child in value), key=repr)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _encoded(value: Any) -> bytes:
    return (
        json.dumps(
            _jsonable(value),
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        _jsonable(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DevelopmentCampaignError(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise DevelopmentCampaignError(f"{label} is not an object")
    return value


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DevelopmentCampaignError(f"{label} is missing")
    return value


def _require(value: Any, expected: Any, *, label: str) -> None:
    if value != expected:
        raise DevelopmentCampaignError(f"development protocol field drifted: {label}")


def verify_development_source_bindings(
    repository_root: str | Path, source_sha256: Mapping[str, str]
) -> dict[str, str]:
    """Verify a closed relative-path source set without making external calls."""

    if not isinstance(source_sha256, Mapping) or not source_sha256:
        raise DevelopmentCampaignError("development source hash bindings are empty")
    root = Path(repository_root).resolve()
    actual: dict[str, str] = {}
    for name, expected in sorted(source_sha256.items()):
        relative = Path(name) if isinstance(name, str) else Path()
        if (
            not isinstance(name, str)
            or not name
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != name
            or not isinstance(expected, str)
            or _DIGEST.fullmatch(expected) is None
        ):
            raise DevelopmentCampaignError("development source hash binding is invalid")
        target = (root / relative).resolve()
        if root != target.parent and root not in target.parents:
            raise DevelopmentCampaignError("development source hash path escaped its root")
        if not target.is_file():
            raise DevelopmentCampaignError("development source hash target is missing")
        digest = _sha256(target)
        if digest != expected:
            raise DevelopmentCampaignError("development source hash differs from freeze")
        actual[name] = digest
    return actual


def load_development_protocol(
    path: str | Path, *, verify_references: bool = True
) -> dict[str, Any]:
    """Load the frozen temporary-provider campaign and verify all local binds."""

    selected = Path(path).resolve()
    value = _object(selected, label="development protocol")
    for field, expected in {
        "schema_version": "membind.v7.composite-development-protocol.v1",
        "status": "FROZEN_BEFORE_DEVELOPMENT_R1_R3",
        "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
        "provider_identity_kind": "COMPOSITE_ENGINEERING_ONLY",
        "formal_r1_r3_eligible": False,
        "live_treatment_authorized": False,
        "scientific_method_selection_update_allowed": False,
        "provider_swap_requires_new_formal_campaign": True,
        "response_replay_allowed": False,
        "old_read_return_allowed": False,
        "native_demand_skip_allowed": False,
        "repair_apply_allowed": False,
        "raw_request_persistence_allowed": False,
        "raw_response_persistence_allowed": False,
        "raw_embedding_persistence_allowed": False,
        "credential_persistence_allowed": False,
    }.items():
        _require(value.get(field), expected, label=field)

    composite = _mapping(
        value.get("composite_provider_freeze"), label="composite provider freeze"
    )
    _require(
        composite.get("path"),
        "BAILIAN_SILICONFLOW_ENGINEERING_OBSERVER_FREEZE.json",
        label="composite_provider_freeze.path",
    )
    _require(
        composite.get("sha256"),
        _COMPOSITE_FREEZE_SHA256,
        label="composite_provider_freeze.sha256",
    )

    construction = _mapping(value.get("construction"), label="construction identity")
    for field, expected in {
        "authority": _CONSTRUCTION_AUTHORITY,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.5-35b-a3b",
        "api_key_env": "DASHSCOPE_API_KEY",
        "structured_output_mode": "json_object",
        "response_validation": "pydantic-v2",
        "enable_thinking": False,
        "sdk_max_retries": 0,
        "hard_attempt_limit_per_request": 1,
    }.items():
        _require(construction.get(field), expected, label=f"construction.{field}")

    embedding = _mapping(value.get("embedding"), label="embedding identity")
    for field, expected in {
        "authority": _EMBEDDING_AUTHORITY,
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen3-Embedding-0.6B",
        "dimension": 1024,
        "api_key_env": "SILICONFLOW_API_KEY",
        "dimension_policy": "EXACT_NO_TRUNCATION",
        "sdk_max_retries": 0,
        "hard_attempt_limit_per_request": 1,
    }.items():
        _require(embedding.get(field), expected, label=f"embedding.{field}")
    if construction["authority"] == embedding["authority"]:
        raise DevelopmentCampaignError("development provider authorities were mixed")

    source_protocol = _mapping(
        value.get("source_methodology_protocol"), label="source methodology protocol"
    )
    _require(
        source_protocol.get("path"),
        "R1_R3_PROTOCOL_FREEZE_V5.json",
        label="source_methodology_protocol.path",
    )
    _require(
        source_protocol.get("sha256"),
        _SOURCE_PROTOCOL_SHA256,
        label="source_methodology_protocol.sha256",
    )
    scientific = _mapping(
        value.get("scientific_method_selection"), label="scientific method selection"
    )
    _require(scientific.get("path"), "METHOD_SELECTION.json", label="scientific.path")
    _require(
        scientific.get("sha256"),
        _SCIENTIFIC_METHOD_SELECTION_SHA256,
        label="scientific.sha256",
    )
    _require(scientific.get("update_allowed"), False, label="scientific.update_allowed")

    workload = _mapping(value.get("workload"), label="development workload")
    for field, expected in {
        "dataset": "ai-hyz/MemoryAgentBench",
        "dataset_revision": "7ea066982b140a19337e17e60d45d4076e042faf",
        "local_file_sha256": _DATASET_SHA256,
    }.items():
        _require(workload.get(field), expected, label=f"workload.{field}")
    r12 = _mapping(workload.get("r1_r2"), label="R1/R2 workload")
    if dict(r12) != {"context_index": 0, "source_start": 0, "source_count": 2}:
        raise DevelopmentCampaignError("development R1/R2 workload drifted")
    r3 = workload.get("r3_blocks")
    expected_r3 = [
        {
            "block_id": "R3-A",
            "context_index": 1,
            "source_start": 0,
            "source_count": 6,
            "seed": 17,
        },
        {
            "block_id": "R3-B",
            "context_index": 2,
            "source_start": 0,
            "source_count": 6,
            "seed": 23,
        },
    ]
    if r3 != expected_r3:
        raise DevelopmentCampaignError("development R3 workload drifted")
    thresholds = _mapping(value.get("thresholds"), label="development thresholds")
    required_thresholds = {
        "false_stable_max": 0,
        "false_unaffected_max": 0,
        "csp_min": 0.1,
        "sca_work_max": 4.0,
        "affected_fraction_max": 0.5,
        "reconvergence_min": 0.25,
        "required_headroom_floor_ns": 100_000_000,
        "required_headroom_ratio": 0.1,
        "stable_prediction_min": 1,
        "gross_saved_cp_min_ns": 1,
    }
    if dict(thresholds) != required_thresholds:
        raise DevelopmentCampaignError("development thresholds drifted")

    harness = _mapping(value.get("observer_harness"), label="observer harness")
    _require(
        harness.get("schema_version"),
        "membind.v7.development-observer-harness.v1",
        label="observer_harness.schema_version",
    )
    source_sha256 = _mapping(harness.get("source_sha256"), label="source hashes")
    if verify_references:
        v7_root = selected.parent
        verify_development_source_bindings(
            v7_root,
            {
                str(composite["path"]): str(composite["sha256"]),
                str(source_protocol["path"]): str(source_protocol["sha256"]),
                str(scientific["path"]): str(scientific["sha256"]),
            },
        )
        repository_root = selected.parents[2]
        verify_development_source_bindings(repository_root, source_sha256)
    return value


def build_development_selection(provisional: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one pure Gate result into non-live implementation authority."""

    if provisional.get("schema_version") != "membind.v7.method-selection.v2":
        raise DevelopmentCampaignError("provisional Gate result schema is invalid")
    gates = provisional.get("gates")
    if not isinstance(gates, Mapping) or set(gates) != set("ABCDE"):
        raise DevelopmentCampaignError("provisional Gate result is incomplete")
    selected = provisional.get("selected_method")
    gate_authorized = (
        provisional.get("status") == "AUTHORIZED"
        and provisional.get("authorized") is True
        and provisional.get("treatment_authorized") is True
        and selected in _LEGAL_METHODS
        and all(gates.get(name) is True for name in "ABCDE")
    )
    if not gate_authorized:
        selected = "NULL"
    profile = {
        "M0": "exact-native-demand-replay",
        "M1": "core-t1-t6b-native-continuation",
        "M2": "core-t1-t8-staged-persistence",
    }.get(str(selected))
    return {
        "schema_version": "membind.v7.development-method-selection.v1",
        "status": "DEVELOPMENT_SELECTED" if gate_authorized else "DEVELOPMENT_NULL",
        "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
        "implementation_authorized": gate_authorized,
        "live_treatment_authorized": False,
        "formal_r1_r3_eligible": False,
        "provider_swap_requires_new_formal_campaign": True,
        "selected_method": selected,
        "selected_operator": provisional.get("selected_operator") if gate_authorized else None,
        "selected_seam": provisional.get("selected_seam") if gate_authorized else None,
        "theorem_profile": profile,
        "gates": dict(gates),
        "offline_opportunity_margin_ns": provisional.get(
            "offline_opportunity_margin_ns"
        ),
        "reasons": list(provisional.get("reasons") or ()),
        "formal_authorization_artifact_required": "METHOD_SELECTION.json",
    }


def _development_gate_envelope(provisional: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "membind.v7.provisional-gate-result.v1",
        "status": (
            "PROVISIONAL_IMPLEMENTATION_ELIGIBLE"
            if provisional.get("authorized") is True
            else "PROVISIONAL_NULL"
        ),
        "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
        "gate_evaluation": dict(provisional),
        "implementation_eligible": provisional.get("authorized") is True,
        "live_treatment_authorized": False,
        "formal_r1_r3_eligible": False,
        "provider_swap_requires_new_formal_campaign": True,
    }


def _delta_summary(value: Any) -> dict[str, Any]:
    raw = _jsonable(value)
    if not isinstance(raw, Mapping):
        return {"status": "UNAVAILABLE", "change_count": None}
    changes = raw.get("changes")
    safe_changes: list[dict[str, Any]] = []
    if isinstance(changes, list):
        for change in changes:
            if not isinstance(change, Mapping):
                continue
            fields = change.get("changed_fields")
            safe_changes.append(
                {
                    "kind": change.get("kind"),
                    "key_digest": _canonical_sha256(str(change.get("key"))),
                    "operation": change.get("operation"),
                    "changed_fields": sorted(
                        str(field)
                        for field in (fields if isinstance(fields, list) else ())
                    ),
                    "exact_before_image_present": isinstance(change.get("before"), Mapping),
                    "exact_after_image_present": isinstance(change.get("after"), Mapping),
                }
            )
    environment = raw.get("environment_changes")
    return {
        "status": "SANITIZED",
        "source_version": raw.get("source_version"),
        "target_version": raw.get("target_version"),
        "change_count": len(safe_changes),
        "changes": safe_changes,
        "environment_changes": sorted(
            str(item) for item in (environment if isinstance(environment, list) else ())
        ),
        "raw_images_persisted": False,
        "embedding_vectors_persisted": False,
    }


def _build_summary(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    requests = raw.get("requests")
    request_rows = requests if isinstance(requests, list) else []
    reads = raw.get("reads")
    read_rows = reads if isinstance(reads, list) else []
    previous = raw.get("previous_episode")
    previous_digest = (
        previous.get("projection_digest") if isinstance(previous, Mapping) else None
    )
    continuation = raw.get("continuation")
    return {
        "phase": raw.get("phase"),
        "source_sequence": raw.get("source_sequence"),
        "state_version": raw.get("state_version"),
        "duration_ns": raw.get("duration_ns"),
        "publication_calls": raw.get("publication_calls"),
        "read_count": len(read_rows),
        "request_count": len(request_rows),
        "previous_episode_projection_digest": previous_digest,
        "continuation_status": (
            continuation.get("status") if isinstance(continuation, Mapping) else None
        ),
        "raw_request_persisted": False,
        "raw_response_persisted": False,
        "raw_embedding_persisted": False,
    }


def _sanitize_block(value: Mapping[str, Any]) -> dict[str, Any]:
    transitions = value.get("transitions")
    safe_transitions: list[dict[str, Any]] = []
    for row in transitions if isinstance(transitions, list) else ():
        if not isinstance(row, Mapping):
            continue
        safe_transitions.append(
            {
                "source_sequence": row.get("source_sequence"),
                "delta": _delta_summary(row.get("delta")),
            }
        )
    pairs = value.get("pairs")
    safe_pairs: list[dict[str, Any]] = []
    for row in pairs if isinstance(pairs, list) else ():
        if not isinstance(row, Mapping):
            continue
        dag = row.get("semantic_dag")
        safe_dag = _jsonable(dag) if isinstance(dag, Mapping) else None
        safe_pairs.append(
            {
                "source_sequence": row.get("source_sequence"),
                "old_build": _build_summary(row.get("old_build")),
                "fresh_build": _build_summary(row.get("fresh_build")),
                "delta": _delta_summary(row.get("delta")),
                "semantic_dag": safe_dag,
            }
        )
    provider = value.get("provider_identity")
    safe = {
        "schema_version": "membind.v7.sanitized-development-block.v1",
        "status": value.get("status"),
        "block_id": value.get("block_id"),
        "source_count": value.get("source_count"),
        "pair_count": len(safe_pairs),
        "real_graphiti_evidence": value.get("real_graphiti_evidence") is True,
        "epochs": _jsonable(value.get("epochs") or {}),
        "provider_identity": _jsonable(provider) if isinstance(provider, Mapping) else {},
        "shadow_publication_calls": value.get("shadow_publication_calls"),
        "native_publication_calls": value.get("native_publication_calls"),
        "treatment_calls": value.get("treatment_calls"),
        "transitions": safe_transitions,
        "pairs": safe_pairs,
        "raw_block_persisted": False,
        "raw_request_persisted": False,
        "raw_response_persisted": False,
        "raw_embedding_persisted": False,
    }
    safe["sanitized_block_sha256"] = _canonical_sha256(safe)
    return safe


def _sanitize_r2(value: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        str(key): _jsonable(child)
        for key, child in value.items()
        if key != "delta"
    }
    result["delta"] = _delta_summary(value.get("delta"))
    result["raw_request_persisted"] = False
    result["raw_response_persisted"] = False
    result["raw_embedding_persisted"] = False
    return result


def _evidence_manifest(
    artifacts: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    members = [
        {"path": name, "sha256": hashlib.sha256(_encoded(value)).hexdigest()}
        for name, value in sorted(artifacts.items())
    ]
    value = {
        "schema_version": "membind.v7.development-evidence-manifest.v1",
        "status": "SEALED_DEVELOPMENT_INPUTS",
        "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
        "files": members,
        "treatment_calls": 0,
        "response_replay_calls": 0,
        "formal_r1_r3_eligible": False,
        "live_treatment_authorized": False,
    }
    return value, hashlib.sha256(_encoded(value)).hexdigest()


def _validate_campaign_identity(value: Mapping[str, Any]) -> None:
    construction = _mapping(value.get("construction"), label="campaign construction")
    embedding = _mapping(value.get("embedding"), label="campaign embedding")
    harness = _mapping(value.get("observer_harness"), label="campaign observer harness")
    if (
        value.get("schema_version")
        != "membind.v7.development-campaign-identity.v1"
        or value.get("campaign_scope") != "TEMPORARY_PROVIDER_DEVELOPMENT"
        or value.get("provider_identity_kind") != "COMPOSITE_ENGINEERING_ONLY"
        or construction.get("authority") != _CONSTRUCTION_AUTHORITY
        or embedding.get("authority") != _EMBEDDING_AUTHORITY
        or construction.get("authority") == embedding.get("authority")
        or embedding.get("dimension") != 1024
        or harness.get("status") != "PASS"
        or not isinstance(harness.get("source_sha256"), Mapping)
        or not harness.get("source_sha256")
        or value.get("formal_r1_r3_eligible") is not False
        or value.get("live_treatment_authorized") is not False
        or value.get("treatment_calls") != 0
        or value.get("response_replay_calls") != 0
    ):
        raise DevelopmentCampaignError("development campaign identity is invalid")


def materialize_development_artifacts(
    root: str | Path,
    *,
    r1: Mapping[str, Any],
    r2: Mapping[str, Any],
    blocks: Sequence[Mapping[str, Any]],
    characterization: Mapping[str, Any],
    campaign_identity: Mapping[str, Any],
    scientific_method_selection_path: str | Path,
    expected_scientific_method_selection_sha256: str = (
        _SCIENTIFIC_METHOD_SELECTION_SHA256
    ),
) -> dict[str, Any]:
    """Seal development evidence while preserving formal scientific state."""

    if len(blocks) != 2:
        raise DevelopmentCampaignError("development R3 requires exactly two blocks")
    if (
        not isinstance(expected_scientific_method_selection_sha256, str)
        or _DIGEST.fullmatch(expected_scientific_method_selection_sha256) is None
    ):
        raise DevelopmentCampaignError("scientific method-selection digest is invalid")
    method_path = Path(scientific_method_selection_path)
    before = _sha256(method_path)
    if before != expected_scientific_method_selection_sha256:
        raise DevelopmentCampaignError(
            "scientific method selection changed before development materialization"
        )
    _validate_campaign_identity(campaign_identity)
    sanitized_blocks = [_sanitize_block(block) for block in blocks]
    base = {
        "R1_ASSUMPTION_AUDIT.json": _jsonable(dict(r1)),
        "R2_TWO_SOURCE_CAUSAL_TRACE.json": _sanitize_r2(r2),
        "R3_BLOCKS.json": sanitized_blocks,
        "PROPAGATION_MATRIX.json": {
            "schema_version": "membind.v7.propagation-matrix.v1",
            "rows": list(characterization.get("pair_analyses") or ()),
        },
        "CERTIFICATE_CONFUSION.json": {
            "schema_version": "membind.v7.certificate-confusion.v1",
            "matrix": dict(characterization.get("certificate_confusion") or {}),
            "false_unaffected_count": characterization.get("false_unaffected_count"),
        },
        "AFFECTED_SET_ORACLE.json": {
            "schema_version": "membind.v7.affected-set-oracle.v1",
            "pair_analyses": list(characterization.get("pair_analyses") or ()),
        },
        "CSP_SCA.json": {
            "schema_version": "membind.v7.csp-sca.v1",
            "csp": characterization.get("csp"),
            "semantic_change_amplification": dict(
                characterization.get("semantic_change_amplification") or {}
            ),
            "reconvergence": dict(characterization.get("reconvergence") or {}),
        },
        "CRITICAL_OPPORTUNITY.json": {
            "schema_version": "membind.v7.critical-opportunity.v1",
            **dict(characterization.get("critical_opportunity") or {}),
        },
        "WORK_AMPLIFICATION.json": {
            "schema_version": "membind.v7.work-amplification.v1",
            **dict(characterization.get("semantic_change_amplification") or {}),
        },
    }
    evidence_manifest, evidence_digest = _evidence_manifest(base)
    decision = dict(characterization.get("decision_input") or {})
    decision.update(
        {
            "sealed_manifest_sha256": evidence_digest,
            "observer_harness_bound": True,
            "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
            "formal_provider_evidence": False,
            "formal_r1_r3_eligible": False,
        }
    )
    provisional = evaluate_opportunity_gates(decision)
    selection = build_development_selection(provisional)
    artifacts = {
        **base,
        "EVIDENCE_MANIFEST.json": evidence_manifest,
        "R3_DECISION_INPUT.json": decision,
        "PROVISIONAL_GATE_RESULT.json": _development_gate_envelope(provisional),
        "DEVELOPMENT_METHOD_SELECTION.json": selection,
    }
    if "METHOD_SELECTION.json" in artifacts:
        raise DevelopmentCampaignError("development campaign attempted formal selection")
    seal = write_observer_artifacts(
        root,
        artifacts,
        campaign_identity=_jsonable(campaign_identity),
    )
    verification = verify_observer_manifest(root)
    after = _sha256(method_path)
    if after != before:
        raise DevelopmentCampaignError(
            "scientific method selection changed during development materialization"
        )
    return {
        **seal,
        "verification": verification,
        "decision_input": decision,
        "provisional_gate_result": provisional,
        "development_method_selection": selection,
        "scientific_method_selection_sha256_before": before,
        "scientific_method_selection_sha256_after": after,
    }


def build_development_failure(
    *,
    run_id: str,
    error: BaseException,
    protocol_sha256: str,
    scientific_method_selection_sha256: str,
    completed_block_count: int,
) -> dict[str, Any]:
    """Build sanitized invalid-attempt evidence without evaluating any Gate."""

    if (
        not run_id
        or _DIGEST.fullmatch(protocol_sha256) is None
        or _DIGEST.fullmatch(scientific_method_selection_sha256) is None
        or isinstance(completed_block_count, bool)
        or not isinstance(completed_block_count, int)
        or not 0 <= completed_block_count <= 3
    ):
        raise DevelopmentCampaignError("development failure identity is invalid")
    classification = classify_observer_failure(error)
    error_bytes = str(error).encode("utf-8", errors="backslashreplace")
    return {
        "schema_version": "membind.v7.development-campaign-failure.v1",
        "status": "FAILED_CLOSED",
        "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
        "run_id": run_id,
        "protocol_sha256": protocol_sha256,
        "scientific_method_selection_sha256": scientific_method_selection_sha256,
        "completed_block_count": completed_block_count,
        "failure_class": classification["failure_class"],
        "attempt_validity": "INVALID_FOR_DEVELOPMENT_GATES",
        "replacement_eligible": classification["replacement_eligible"],
        "error_type": f"{type(error).__module__}.{type(error).__qualname__}",
        "error_message_sha256": hashlib.sha256(error_bytes).hexdigest(),
        "gate_outcome": "NOT_EVALUATED",
        "development_method_selection_materialized": False,
        "formal_r1_r3_eligible": False,
        "live_treatment_authorized": False,
        "treatment_calls": 0,
        "response_replay_calls": 0,
        "raw_request_persisted": False,
        "raw_response_persisted": False,
        "raw_embedding_persisted": False,
        "credentials_recorded": False,
    }


__all__ = [
    "DevelopmentCampaignError",
    "build_development_failure",
    "build_development_selection",
    "load_development_protocol",
    "materialize_development_artifacts",
    "verify_development_source_bindings",
]
