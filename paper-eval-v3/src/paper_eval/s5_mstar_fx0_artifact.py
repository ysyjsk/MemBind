"""Independent, fail-closed artifact contract for production-path M* FX0.

The legacy FX0 artifact records only a harness self-test with callback doubles.
This schema is intentionally disjoint.  Verification requires an external
fixture-manifest hash and the complete expected input-binding map so a caller
cannot mutate an artifact and merely recompute its internal hashes.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import atomic_write_json, finalize_envelope, payload_sha256
from .fx0_mechanism_fixture import (
    FIXTURE_COUNT_POLICY,
    FX0_REQUIRED_FAILURE_MODES,
    FX0_REQUIRED_TRANSITIONS,
    PRODUCTION_CONTROLLED_PROVIDER_NAMES,
)
from .s5_mstar_production_core_identity import (
    S5MStarProductionCoreIdentityError,
    verify_s5_mstar_production_core_identity,
)
from .s5_graphiti_mstar_semantics import S5GraphitiMStarSemanticRuntime
from .s5_graphiti_fx0_environment import S5GraphitiFx0ControlledEnvironment
from .s5_mstar_production_adapter import (
    S5MStarFx0ExecutionEvidence,
    S5MStarProductionAdapter,
    S5MStarProductionAdapterError,
)


PRODUCTION_FX0_SCHEMA = (
    "membind.paper-eval-v3.s5-mstar-production-fx0-parity.v1"
)
PRODUCTION_FX0_LANE = "S5_MSTAR_PRODUCTION_PATH_FX0_PARITY"
PRODUCTION_FX0_VERDICT = "PRODUCTION_PATH_EXACT_PARITY_PASS"
PRODUCTION_FX0_SCOPE = "CONTROLLED_OFFLINE_HASH_BOUND_GRAPHITI_PRODUCTION_CORE"
PRODUCTION_FX0_FIXTURE_MANIFEST_SCHEMA = (
    "membind.paper-eval-v3.s5-mstar-production-fx0-fixture-manifest.v1"
)
PINNED_GRAPHITI_SEMANTIC_API_SHA256 = (
    "06909217defc448d7dd380f051b6b282fbb9a8a021c337f998c395fc9bb196fa"
)
PINNED_GRAPHITI_SEMANTIC_IDENTITY_ARTIFACT_SHA256 = (
    "8ecf8c93176205195b1284a5a6b6cb1a24d739634fc1f8ec3ea3823ded600e04"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INPUT_BINDING_FIELDS = {
    "parent_protocol_sha256",
    "amendment_sha256",
    "current_stage_pointer_sha256",
    "production_core_identity_sha256",
    "graphiti_semantic_api_identity_sha256",
    "graphiti_semantic_identity_artifact_sha256",
    "fx0_fixture_manifest_sha256",
    "execution_input_set_sha256",
    "oracle_set_sha256",
    "controlled_provider_set_sha256",
    "adapter_source_sha256",
    "pipeline_source_sha256",
    "semantic_runtime_source_sha256",
    "semantic_binding_source_sha256",
}
_CASE_FIELDS = {
    "case_identity_sha256",
    "transition",
    "execution_input_sha256",
    "controlled_provider_sha256",
    "oracle_outcome_sha256",
    "observed_outcome_sha256",
    "outcome_class_sha256",
    "pipeline_evidence_sha256",
    "execution_shape",
    "execution_shape_sha256",
    "exact_status_error_parity",
    "exact_canonical_state_parity",
    "exact_publication_history_parity",
}
_EXECUTION_SHAPE_FIELDS = {
    "source_count",
    "attempt_count",
    "transaction_attempt_count",
    "prepare_overlap_observed",
    "published_source_count",
    "published_source_order_observed",
    "prepare_to_bind_state_change_observed",
    "single_logical_publication_observed",
    "retry_replay_observed",
    "publication_fault_detection_observed",
}
_PAYLOAD_FIELDS = {
    "schema_version",
    "lane",
    "verdict",
    "evidence_scope",
    "fixture_count_policy",
    "fixture_count",
    "covered_transitions",
    "covered_publication_failure_mode_hashes",
    "controlled_nondeterminism_providers",
    "production_core_identity",
    "input_bindings",
    "case_evidence",
    "case_evidence_sha256",
    "parity",
    "claims",
    "legacy_boundary",
    "authority",
}
_PARITY = {
    "exact_status_error_parity": True,
    "exact_canonical_logical_state_parity": True,
    "exact_publication_history_parity": True,
    "all_required_transition_shapes_observed": True,
}
_CLAIMS = {
    "fx0_fixture_exact_parity_observed": True,
    "semantic_correctness_beyond_fixture_authorized": False,
    "performance_claims_authorized": False,
    "live_readiness_claim_authorized": False,
}
_LEGACY_BOUNDARY = {
    "legacy_self_test_artifact_accepted": False,
    "placeholder_production_identity_accepted": False,
    "test_double_only_evidence_accepted": False,
}
_AUTHORITY = {
    "model_call_authorized": False,
    "neo4j_read_authorized": False,
    "neo4j_mutation_authorized": False,
    "namespace_creation_authorized": False,
    "namespace_cleanup_authorized": False,
    "s5_live_execution_authorized": False,
    "pilot_execution_authorized": False,
    "formal_execution_authorized": False,
    "current_stage_pointer_update_authorized": False,
    "follow_on_execution_authorized": False,
}
_PRIVATE_FIELDS = {
    "answer",
    "api_key",
    "authorization",
    "body",
    "content",
    "credential",
    "episode",
    "group_id",
    "messages",
    "namespace",
    "password",
    "prompt",
    "question",
    "raw_output",
    "raw_response",
    "request",
    "response",
    "secret",
    "source",
}


class S5MStarProductionFx0ArtifactError(ValueError):
    """Production FX0 evidence is missing, mutated, private, or overstated."""


def _fail(code: str) -> S5MStarProductionFx0ArtifactError:
    return S5MStarProductionFx0ArtifactError(code)


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
                raise _fail("private_artifact_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


def _outcome_class(status: str, error_code: str | None) -> str:
    return payload_sha256({"status": status, "error_code": error_code})


_PASS_OUTCOME = _outcome_class("PASS", None)
_CONFLICTING_DUPLICATE_OUTCOME = _outcome_class(
    "FAIL_CLOSED", "CONFLICTING_DUPLICATE_UUID"
)
_PUBLICATION_FAILURE_OUTCOMES = {
    mode: _outcome_class("FAIL_CLOSED", mode)
    for mode in FX0_REQUIRED_FAILURE_MODES
}


def _case_binding_projection(case: object) -> dict[str, str]:
    """Bind one case's input, providers, and oracle to the same identity."""

    case_identity_sha256 = payload_sha256({"case_id": case.case_id})
    common = {
        "case_identity_sha256": case_identity_sha256,
        "transition": case.transition,
    }
    return {
        **common,
        "execution_input_sha256": payload_sha256(
            {
                **common,
                "source_sequence": case.source_sequence,
                "source_sha256": payload_sha256(case.source),
            }
        ),
        "controlled_provider_sha256": payload_sha256(
            {
                **common,
                "production_hash_projection": (
                    case.providers.production_hash_projection()
                ),
            }
        ),
        "oracle_outcome_sha256": payload_sha256(
            {
                **common,
                "status": case.expected_status,
                "error_code": case.expected_error_code,
                "canonical_logical_state": (
                    case.expected_canonical_logical_state
                ),
                "publication_history": case.expected_publication_history,
            }
        ),
    }


def _binding_set_sha256(
    rows: Sequence[Mapping[str, str]], field: str
) -> str:
    projection = [
        {
            "case_identity_sha256": row["case_identity_sha256"],
            "transition": row["transition"],
            field: row[field],
        }
        for row in rows
    ]
    return payload_sha256(projection)


def _fixture_manifest_from_case_bindings(
    *,
    case_bindings: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    rows = sorted(
        (deepcopy(dict(row)) for row in case_bindings),
        key=lambda row: row["case_identity_sha256"],
    )
    execution_input_set_sha256 = _binding_set_sha256(
        rows, "execution_input_sha256"
    )
    controlled_provider_set_sha256 = _binding_set_sha256(
        rows, "controlled_provider_sha256"
    )
    oracle_set_sha256 = _binding_set_sha256(rows, "oracle_outcome_sha256")
    body = {
        "schema_version": PRODUCTION_FX0_FIXTURE_MANIFEST_SCHEMA,
        "fixture_count_policy": FIXTURE_COUNT_POLICY,
        "fixture_count": len(rows),
        "controlled_nondeterminism_providers": list(
            PRODUCTION_CONTROLLED_PROVIDER_NAMES
        ),
        "case_bindings": rows,
        "execution_input_set_sha256": execution_input_set_sha256,
        "controlled_provider_set_sha256": controlled_provider_set_sha256,
        "oracle_set_sha256": oracle_set_sha256,
    }
    return {
        **body,
        "fx0_fixture_manifest_sha256": payload_sha256(body),
    }


def derive_s5_mstar_fx0_fixture_manifest(spec: object) -> dict[str, Any]:
    """Derive the deterministic production binding manifest from an FX0 spec."""

    from .fx0_mechanism_fixture import Fx0FixtureCase, Fx0FixtureSpec

    if not isinstance(spec, Fx0FixtureSpec):
        raise _fail("fx0_fixture_spec_invalid")
    case_bindings = []
    for case in spec.cases:
        if not isinstance(case, Fx0FixtureCase):
            raise _fail("fx0_fixture_case_invalid")
        case_bindings.append(_case_binding_projection(case))
    return _fixture_manifest_from_case_bindings(
        case_bindings=case_bindings,
    )


def _validate_fixture_bindings(
    spec: object, bindings: Mapping[str, str]
) -> dict[str, Any]:
    manifest = derive_s5_mstar_fx0_fixture_manifest(spec)
    derived = {
        "parent_protocol_sha256": spec.parent_protocol_sha256,
        "amendment_sha256": spec.amendment_sha256,
        "current_stage_pointer_sha256": spec.current_stage_pointer_sha256,
        "fx0_fixture_manifest_sha256": manifest[
            "fx0_fixture_manifest_sha256"
        ],
        "execution_input_set_sha256": manifest[
            "execution_input_set_sha256"
        ],
        "controlled_provider_set_sha256": manifest[
            "controlled_provider_set_sha256"
        ],
        "oracle_set_sha256": manifest["oracle_set_sha256"],
    }
    if any(bindings.get(field) != digest for field, digest in derived.items()):
        raise _fail("fixture_binding_mismatch")
    return manifest


def _legacy_schema(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    payload = value.get("payload")
    return isinstance(payload, Mapping) and payload.get("schema_version") == (
        "membind.paper-eval-v3.fx0-mechanism-fixture.v1"
    )


def verify_s5_mstar_fx0_artifact(
    value: Mapping[str, Any],
    *,
    expected_input_bindings: Mapping[str, str] | None = None,
    expected_fixture_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Verify exact parity with caller-supplied frozen binding context."""

    if _legacy_schema(value):
        raise _fail("legacy_schema_forbidden")
    if expected_input_bindings is None or expected_fixture_manifest_sha256 is None:
        raise _fail("external_verification_context_required")
    expected_bindings = _mapping(
        expected_input_bindings, "expected_input_bindings_invalid"
    )
    if set(expected_bindings) != _INPUT_BINDING_FIELDS:
        raise _fail("expected_input_binding_shape_invalid")
    for field, digest in expected_bindings.items():
        _sha(digest, f"expected_{field}_invalid")
    _sha(expected_fixture_manifest_sha256, "expected_fixture_manifest_invalid")
    if (
        expected_bindings["fx0_fixture_manifest_sha256"]
        != expected_fixture_manifest_sha256
    ):
        raise _fail("expected_fixture_manifest_binding_invalid")

    artifact = _mapping(value, "artifact_invalid")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise _fail("artifact_envelope_shape_invalid")
    payload = _mapping(artifact.get("payload"), "artifact_payload_invalid")
    _assert_public(payload)
    if set(payload) != _PAYLOAD_FIELDS:
        raise _fail("artifact_payload_shape_invalid")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or payload.get("schema_version") != PRODUCTION_FX0_SCHEMA
        or payload.get("lane") != PRODUCTION_FX0_LANE
        or payload.get("verdict") != PRODUCTION_FX0_VERDICT
        or payload.get("evidence_scope") != PRODUCTION_FX0_SCOPE
        or payload.get("fixture_count_policy") != FIXTURE_COUNT_POLICY
    ):
        raise _fail("artifact_identity_or_schema_invalid")

    bindings = _mapping(payload.get("input_bindings"), "input_bindings_invalid")
    if bindings != expected_bindings:
        raise _fail("external_input_binding_mismatch")
    core = _mapping(
        payload.get("production_core_identity"), "production_core_identity_invalid"
    )
    try:
        verified_core = verify_s5_mstar_production_core_identity(core)
    except S5MStarProductionCoreIdentityError:
        raise _fail("production_core_identity_invalid") from None
    if (
        verified_core["identity_sha256"]
        != bindings["production_core_identity_sha256"]
        or verified_core["graphiti_semantic_api_sha256"]
        != bindings["graphiti_semantic_api_identity_sha256"]
        or verified_core["graphiti_semantic_identity_artifact_sha256"]
        != bindings["graphiti_semantic_identity_artifact_sha256"]
        or verified_core["adapter_source_sha256"]
        != bindings["adapter_source_sha256"]
        or verified_core["pipeline_source_sha256"]
        != bindings["pipeline_source_sha256"]
        or verified_core["semantic_runtime_source_sha256"]
        != bindings["semantic_runtime_source_sha256"]
        or verified_core["semantic_binding_source_sha256"]
        != bindings["semantic_binding_source_sha256"]
    ):
        raise _fail("production_core_binding_mismatch")

    raw_rows = payload.get("case_evidence")
    if isinstance(raw_rows, (str, bytes)) or not isinstance(raw_rows, Sequence):
        raise _fail("case_evidence_invalid")
    rows = [_mapping(row, "case_evidence_row_invalid") for row in raw_rows]
    if (
        not rows
        or payload.get("fixture_count") != len(rows)
        or payload.get("case_evidence_sha256") != payload_sha256(rows)
        or len(rows) <= len(FX0_REQUIRED_TRANSITIONS)
    ):
        raise _fail("case_evidence_coverage_invalid")
    case_ids: list[str] = []
    transitions: set[str] = set()
    publication_failure_hashes: set[str] = set()
    for row in rows:
        if set(row) != _CASE_FIELDS:
            raise _fail("case_evidence_shape_invalid")
        for field in (
            "case_identity_sha256",
            "execution_input_sha256",
            "controlled_provider_sha256",
            "oracle_outcome_sha256",
            "observed_outcome_sha256",
            "outcome_class_sha256",
            "pipeline_evidence_sha256",
            "execution_shape_sha256",
        ):
            _sha(row.get(field), f"case_{field}_invalid")
        case_ids.append(str(row["case_identity_sha256"]))
        transition = row.get("transition")
        if transition not in FX0_REQUIRED_TRANSITIONS:
            raise _fail("case_transition_invalid")
        transitions.add(str(transition))
        if (
            row.get("oracle_outcome_sha256")
            != row.get("observed_outcome_sha256")
            or row.get("exact_status_error_parity") is not True
            or row.get("exact_canonical_state_parity") is not True
            or row.get("exact_publication_history_parity") is not True
        ):
            raise _fail("case_exact_parity_invalid")
        shape = _mapping(row.get("execution_shape"), "execution_shape_invalid")
        if (
            set(shape) != _EXECUTION_SHAPE_FIELDS
            or row.get("execution_shape_sha256") != payload_sha256(shape)
        ):
            raise _fail("execution_shape_hash_or_fields_invalid")
        for field in (
            "source_count",
            "attempt_count",
            "published_source_count",
        ):
            number = shape.get(field)
            if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                raise _fail("execution_shape_count_invalid")
        if "transaction_attempt_count" in shape:
            number = shape["transaction_attempt_count"]
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                raise _fail("execution_shape_count_invalid")
        for field in _EXECUTION_SHAPE_FIELDS - {
            "source_count",
            "attempt_count",
            "transaction_attempt_count",
            "published_source_count",
        }:
            if not isinstance(shape.get(field), bool):
                raise _fail("execution_shape_flag_invalid")
        outcome_class = str(row["outcome_class_sha256"])
        if transition == "CONFLICTING_DUPLICATE_UUID_FAIL_CLOSED":
            if outcome_class != _CONFLICTING_DUPLICATE_OUTCOME:
                raise _fail("conflicting_duplicate_outcome_invalid")
        elif transition == "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION":
            matching = {
                mode
                for mode, digest in _PUBLICATION_FAILURE_OUTCOMES.items()
                if digest == outcome_class
            }
            if (
                len(matching) != 1
                or shape["publication_fault_detection_observed"] is not True
            ):
                raise _fail("publication_failure_detection_invalid")
            publication_failure_hashes.add(outcome_class)
        elif outcome_class != _PASS_OUTCOME:
            raise _fail("passing_transition_outcome_invalid")
        if transition == "SOURCE_ORDERED_PUBLICATION" and not (
            shape["source_count"] >= 2
            and shape["prepare_overlap_observed"] is True
            and shape["published_source_count"] == shape["source_count"]
            and shape["published_source_order_observed"] is True
        ):
            raise _fail("source_order_execution_shape_invalid")
        if transition == "PREPARE_TO_BIND_STATE_CHANGE" and not (
            shape["source_count"] >= 2
            and shape["prepare_to_bind_state_change_observed"] is True
        ):
            raise _fail("prepare_bind_state_change_shape_invalid")
        if transition == "RETRY_IDEMPOTENCE" and not (
            shape["attempt_count"] >= 2
            and shape["retry_replay_observed"] is True
            and shape["single_logical_publication_observed"] is True
        ):
            raise _fail("retry_idempotence_execution_shape_invalid")

    if len(case_ids) != len(set(case_ids)):
        raise _fail("case_identity_duplicate")
    evidence_manifest = _fixture_manifest_from_case_bindings(
        case_bindings=[
            {
                "case_identity_sha256": str(row["case_identity_sha256"]),
                "transition": str(row["transition"]),
                "execution_input_sha256": str(row["execution_input_sha256"]),
                "controlled_provider_sha256": str(
                    row["controlled_provider_sha256"]
                ),
                "oracle_outcome_sha256": str(row["oracle_outcome_sha256"]),
            }
            for row in rows
        ],
    )
    for field in (
        "execution_input_set_sha256",
        "controlled_provider_set_sha256",
        "oracle_set_sha256",
        "fx0_fixture_manifest_sha256",
    ):
        if bindings[field] != evidence_manifest[field]:
            raise _fail("artifact_fixture_binding_mismatch")
    expected_failure_hashes = set(_PUBLICATION_FAILURE_OUTCOMES.values())
    if (
        transitions != set(FX0_REQUIRED_TRANSITIONS)
        or publication_failure_hashes != expected_failure_hashes
        or set(payload.get("covered_transitions", ()))
        != set(FX0_REQUIRED_TRANSITIONS)
        or set(payload.get("covered_publication_failure_mode_hashes", ()))
        != expected_failure_hashes
        or payload.get("controlled_nondeterminism_providers")
        != list(PRODUCTION_CONTROLLED_PROVIDER_NAMES)
        or payload.get("parity") != _PARITY
        or payload.get("claims") != _CLAIMS
        or payload.get("legacy_boundary") != _LEGACY_BOUNDARY
        or payload.get("authority") != _AUTHORITY
    ):
        raise _fail("artifact_coverage_claim_or_authority_invalid")
    artifact["payload"] = payload
    return artifact


def _validate_production_binding(
    mechanism: object,
    *,
    production_core_identity: Mapping[str, object],
) -> S5MStarProductionAdapter:
    if not isinstance(mechanism, S5MStarProductionAdapter):
        raise _fail("production_adapter_type_invalid")
    try:
        core = verify_s5_mstar_production_core_identity(production_core_identity)
    except S5MStarProductionCoreIdentityError:
        raise _fail("production_core_identity_invalid") from None
    if (
        mechanism.production_core_identity != core
        or mechanism.production_core_identity_sha256 != core["identity_sha256"]
        or not mechanism.source_decoder_supplied
        or mechanism.reset_case is None
        or mechanism.witness_snapshot is None
        or not mechanism.persist_event_supplied
        or not mechanism.controlled_provider_factory_supplied
        or not mechanism.publication_fault_detector_supplied
    ):
        raise _fail("production_adapter_boundary_incomplete")
    if (
        core["graphiti_semantic_api_sha256"] != PINNED_GRAPHITI_SEMANTIC_API_SHA256
        or core["graphiti_semantic_identity_artifact_sha256"]
        != PINNED_GRAPHITI_SEMANTIC_IDENTITY_ARTIFACT_SHA256
    ):
        raise _fail("pinned_graphiti_semantic_artifact_mismatch")
    prepare_owner = getattr(mechanism.semantic_prepare, "__self__", None)
    bind_owner = getattr(mechanism.latest_state_bind, "__self__", None)
    if (
        not isinstance(prepare_owner, S5GraphitiMStarSemanticRuntime)
        or bind_owner is not prepare_owner
        or prepare_owner.controlled_provider_scope is None
        or not callable(prepare_owner.controlled_provider_scope)
    ):
        raise _fail("pinned_graphiti_runtime_binding_invalid")
    if prepare_owner.binding.loader_verified is not True:
        raise _fail("pinned_graphiti_binding_not_loader_verified")
    environment_owner = getattr(mechanism.source_decoder, "__self__", None)
    environment_hooks = (
        mechanism.reset_case,
        mechanism.snapshot,
        mechanism.persist_event,
        mechanism.witness_snapshot,
        mechanism.controlled_provider_factory,
        mechanism.publication_fault_detector,
        mechanism.clock_ns,
    )
    if (
        not isinstance(environment_owner, S5GraphitiFx0ControlledEnvironment)
        or environment_owner.runtime is not prepare_owner
        or any(
            getattr(callback, "__self__", None) is not environment_owner
            for callback in environment_hooks
        )
        or getattr(prepare_owner.controlled_provider_scope, "__self__", None)
        is not environment_owner
        or getattr(prepare_owner.latest_state_retriever, "__self__", None)
        is not environment_owner
        or (
            mechanism.recover_publication is not None
            and getattr(mechanism.recover_publication, "__self__", None)
            is not environment_owner
        )
    ):
        raise _fail("controlled_environment_binding_invalid")
    try:
        semantic_identity = prepare_owner.binding.identity_sha256()
    except Exception:
        raise _fail("pinned_graphiti_semantic_identity_unavailable") from None
    if semantic_identity != core["graphiti_semantic_api_sha256"]:
        raise _fail("pinned_graphiti_semantic_identity_mismatch")
    return mechanism


def _row_from_execution(
    case: object,
    execution: S5MStarFx0ExecutionEvidence,
    *,
    expected_status: str,
    expected_error_code: str | None,
    expected_state: Mapping[str, Any],
    expected_history: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    """Project one comparator result to hash-only public evidence."""

    observed = execution.outcome
    if (
        observed.status != expected_status
        or observed.error_code != expected_error_code
        or payload_sha256(observed.canonical_logical_state)
        != payload_sha256(expected_state)
        or payload_sha256(observed.publication_history)
        != payload_sha256(expected_history)
    ):
        raise _fail("production_fx0_exact_parity_failed")
    shape = _mapping(execution.execution_shape, "execution_shape_invalid")
    case_binding = _case_binding_projection(case)
    common = {
        "case_identity_sha256": case_binding["case_identity_sha256"],
        "transition": case.transition,
    }
    return {
        "case_identity_sha256": case_binding["case_identity_sha256"],
        "transition": case.transition,
        "execution_input_sha256": case_binding["execution_input_sha256"],
        "controlled_provider_sha256": case_binding[
            "controlled_provider_sha256"
        ],
        "oracle_outcome_sha256": case_binding["oracle_outcome_sha256"],
        "observed_outcome_sha256": payload_sha256(
            {
                **common,
                "status": observed.status,
                "error_code": observed.error_code,
                "canonical_logical_state": observed.canonical_logical_state,
                "publication_history": observed.publication_history,
            }
        ),
        "outcome_class_sha256": payload_sha256(
            {"status": observed.status, "error_code": observed.error_code}
        ),
        "pipeline_evidence_sha256": payload_sha256(execution.pipeline_evidence),
        "execution_shape": shape,
        "execution_shape_sha256": payload_sha256(shape),
        "exact_status_error_parity": True,
        "exact_canonical_state_parity": True,
        "exact_publication_history_parity": True,
    }


async def build_s5_mstar_fx0_artifact_async(
    *,
    spec: object,
    mechanism: object,
    production_core_identity: Mapping[str, object],
    expected_input_bindings: Mapping[str, str],
    git_commit: str,
) -> dict[str, Any]:
    """Run the pinned semantic runtime and seal hash-only production evidence."""

    from .fx0_mechanism_fixture import Fx0FixtureCase, Fx0FixtureSpec

    if not isinstance(spec, Fx0FixtureSpec):
        raise _fail("fx0_fixture_spec_invalid")
    bindings = _mapping(expected_input_bindings, "input_bindings_invalid")
    if set(bindings) != _INPUT_BINDING_FIELDS:
        raise _fail("input_binding_shape_invalid")
    for field, digest in bindings.items():
        _sha(digest, f"input_{field}_invalid")
    if bindings["production_core_identity_sha256"] != production_core_identity.get(
        "identity_sha256"
    ):
        raise _fail("fixture_binding_mismatch")
    expected_production_path_identity = {
        "status": "FROZEN",
        "method": "M_STAR",
        "identity_sha256": bindings["production_core_identity_sha256"],
    }
    if spec.production_path_identity != expected_production_path_identity:
        raise _fail("fixture_binding_mismatch")
    _validate_fixture_bindings(spec, bindings)
    adapter = _validate_production_binding(
        mechanism, production_core_identity=production_core_identity
    )
    if spec.production_path_identity != adapter.production_path_identity:
        raise _fail("legacy_production_path_identity_mismatch")
    rows: list[dict[str, object]] = []
    for case in spec.cases:
        if not isinstance(case, Fx0FixtureCase):
            raise _fail("fx0_fixture_case_invalid")
        observed = adapter.execute_fixture_case_with_evidence(
            case.execution_input(), case.providers
        )
        if not inspect.isawaitable(observed):
            raise _fail("production_adapter_must_be_async")
        try:
            execution = await observed
        except S5MStarProductionAdapterError as error:
            raise _fail(f"production_adapter_failed:{error.error_code}") from None
        if not isinstance(execution, S5MStarFx0ExecutionEvidence):
            raise _fail("production_execution_evidence_invalid")
        rows.append(
            _row_from_execution(
                case,
                execution,
                expected_status=case.expected_status,
                expected_error_code=case.expected_error_code,
                expected_state=case.expected_canonical_logical_state,
                expected_history=case.expected_publication_history,
            )
        )
    payload: dict[str, object] = {
        "schema_version": PRODUCTION_FX0_SCHEMA,
        "lane": PRODUCTION_FX0_LANE,
        "verdict": PRODUCTION_FX0_VERDICT,
        "evidence_scope": PRODUCTION_FX0_SCOPE,
        "fixture_count_policy": FIXTURE_COUNT_POLICY,
        "fixture_count": len(rows),
        "covered_transitions": sorted({row["transition"] for row in rows}),
        "covered_publication_failure_mode_hashes": sorted(
            {
                row["outcome_class_sha256"]
                for row in rows
                if row["transition"] == "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION"
            }
        ),
        "controlled_nondeterminism_providers": list(
            PRODUCTION_CONTROLLED_PROVIDER_NAMES
        ),
        "production_core_identity": deepcopy(dict(production_core_identity)),
        "input_bindings": bindings,
        "case_evidence": rows,
        "case_evidence_sha256": payload_sha256(rows),
        "parity": deepcopy(_PARITY),
        "claims": deepcopy(_CLAIMS),
        "legacy_boundary": deepcopy(_LEGACY_BOUNDARY),
        "authority": deepcopy(_AUTHORITY),
    }
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=spec.run_id,
    )
    return verify_s5_mstar_fx0_artifact(
        artifact,
        expected_input_bindings=bindings,
        expected_fixture_manifest_sha256=bindings["fx0_fixture_manifest_sha256"],
    )


def build_s5_mstar_fx0_artifact(**kwargs: object) -> dict[str, Any]:
    """Synchronous wrapper for the production-path FX0 qualification run."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(build_s5_mstar_fx0_artifact_async(**kwargs))
    raise S5MStarProductionFx0ArtifactError(
        "build_s5_mstar_fx0_artifact_inside_event_loop_use_async"
    )


def write_s5_mstar_fx0_artifact_exclusive(path: Path, artifact: Mapping[str, Any]) -> None:
    """Write one sealed artifact without overwriting a prior qualification."""

    target = Path(path)
    if target.exists():
        raise S5MStarProductionFx0ArtifactError("artifact_exists")
    verified = verify_s5_mstar_fx0_artifact(
        artifact,
        expected_input_bindings=artifact["payload"]["input_bindings"],
        expected_fixture_manifest_sha256=artifact["payload"]["input_bindings"][
            "fx0_fixture_manifest_sha256"
        ],
    )
    atomic_write_json(target, verified)


__all__ = [
    "PRODUCTION_FX0_LANE",
    "PRODUCTION_FX0_SCHEMA",
    "PRODUCTION_FX0_SCOPE",
    "PRODUCTION_FX0_VERDICT",
    "PRODUCTION_FX0_FIXTURE_MANIFEST_SCHEMA",
    "PINNED_GRAPHITI_SEMANTIC_API_SHA256",
    "PINNED_GRAPHITI_SEMANTIC_IDENTITY_ARTIFACT_SHA256",
    "S5MStarProductionFx0ArtifactError",
    "build_s5_mstar_fx0_artifact",
    "build_s5_mstar_fx0_artifact_async",
    "derive_s5_mstar_fx0_fixture_manifest",
    "verify_s5_mstar_fx0_artifact",
    "write_s5_mstar_fx0_artifact_exclusive",
]
