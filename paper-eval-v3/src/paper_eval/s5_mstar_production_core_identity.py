"""Non-circular identity for the shared M* production core.

The FX0 production artifact binds this identity.  A later S5 method identity
may then bind the FX0 artifact hash, avoiding an impossible identity/artifact
self-reference.  This module describes code and configuration only; it never
grants qualification or live authority.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy

from .artifacts import payload_sha256


SCHEMA = "membind.paper-eval-v3.s5-mstar-production-core-identity.v1"
GRAPHITI_VERSION = "0.29.3"
GRAPHITI_COMMIT = "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENTRYPOINT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")
_PRIVATE_FIELDS = {
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
    "raw_output",
    "raw_response",
    "request",
    "response",
    "secret",
}
_HASH_FIELDS = (
    "graphiti_semantic_api_sha256",
    "graphiti_semantic_identity_artifact_sha256",
    "runtime_factory_source_sha256",
    "pipeline_source_sha256",
    "pipeline_test_source_sha256",
    "adapter_source_sha256",
    "adapter_test_source_sha256",
    "semantic_runtime_source_sha256",
    "semantic_runtime_test_source_sha256",
    "semantic_binding_source_sha256",
    "semantic_binding_test_source_sha256",
    "durable_store_source_sha256",
    "durable_store_test_source_sha256",
    "runtime_config_sha256",
)
_FIELDS = {
    "schema_version",
    "status",
    "method",
    "graphiti_version",
    "graphiti_commit",
    *_HASH_FIELDS,
    "runtime_factory_entrypoint",
    "method_policy",
    "failure_policy",
    "qualification_status",
    "authority",
    "identity_sha256",
}
_METHOD_POLICY = {
    "configured_prepare_concurrency": 2,
    "scheduler": "PARALLEL_PREPARE_SOURCE_ORDERED_BIND",
    "shared_fx0_and_live_core_required": True,
    "fx0_exact_parity_required": True,
}
_FAILURE_POLICY = {
    "failed_attempt_status": "incomplete_non_mergeable",
    "resume_authorized": False,
    "fresh_attempt_required": True,
    "db_commit_idempotence_claimed": False,
    "commit_journal_recovery_qualification_required": True,
}
_AUTHORITY = {
    "model_call_authorized": False,
    "neo4j_read_authorized": False,
    "neo4j_mutation_authorized": False,
    "s5_live_execution_authorized": False,
    "current_stage_pointer_update_authorized": False,
}


class S5MStarProductionCoreIdentityError(ValueError):
    """M* production-core identity is incomplete, private, or mutated."""


def _fail(code: str) -> S5MStarProductionCoreIdentityError:
    return S5MStarProductionCoreIdentityError(code)


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_identity_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def build_s5_mstar_production_core_identity(
    *,
    graphiti_version: str,
    graphiti_commit: str,
    graphiti_semantic_api_sha256: str,
    graphiti_semantic_identity_artifact_sha256: str,
    runtime_factory_entrypoint: str,
    runtime_factory_source_sha256: str,
    pipeline_source_sha256: str,
    pipeline_test_source_sha256: str,
    adapter_source_sha256: str,
    adapter_test_source_sha256: str,
    semantic_runtime_source_sha256: str,
    semantic_runtime_test_source_sha256: str,
    semantic_binding_source_sha256: str,
    semantic_binding_test_source_sha256: str,
    durable_store_source_sha256: str,
    durable_store_test_source_sha256: str,
    runtime_config_sha256: str,
) -> dict[str, object]:
    """Build the frozen pre-FX0 code/config identity for M*."""

    payload: dict[str, object] = {
        "schema_version": SCHEMA,
        "status": "FROZEN",
        "method": "M*",
        "graphiti_version": graphiti_version,
        "graphiti_commit": graphiti_commit,
        "graphiti_semantic_api_sha256": graphiti_semantic_api_sha256,
        "graphiti_semantic_identity_artifact_sha256": (
            graphiti_semantic_identity_artifact_sha256
        ),
        "runtime_factory_entrypoint": runtime_factory_entrypoint,
        "runtime_factory_source_sha256": runtime_factory_source_sha256,
        "pipeline_source_sha256": pipeline_source_sha256,
        "pipeline_test_source_sha256": pipeline_test_source_sha256,
        "adapter_source_sha256": adapter_source_sha256,
        "adapter_test_source_sha256": adapter_test_source_sha256,
        "semantic_runtime_source_sha256": semantic_runtime_source_sha256,
        "semantic_runtime_test_source_sha256": semantic_runtime_test_source_sha256,
        "semantic_binding_source_sha256": semantic_binding_source_sha256,
        "semantic_binding_test_source_sha256": semantic_binding_test_source_sha256,
        "durable_store_source_sha256": durable_store_source_sha256,
        "durable_store_test_source_sha256": durable_store_test_source_sha256,
        "runtime_config_sha256": runtime_config_sha256,
        "method_policy": deepcopy(_METHOD_POLICY),
        "failure_policy": deepcopy(_FAILURE_POLICY),
        "qualification_status": "CORE_IDENTITY_ONLY_UNQUALIFIED",
        "authority": deepcopy(_AUTHORITY),
    }
    _assert_public(payload)
    payload["identity_sha256"] = payload_sha256(payload)
    return verify_s5_mstar_production_core_identity(payload)


def verify_s5_mstar_production_core_identity(
    value: Mapping[str, object],
) -> dict[str, object]:
    """Recompute every field and reject FX0/self-reference injection."""

    if not isinstance(value, Mapping):
        raise _fail("identity_not_mapping")
    identity = deepcopy(dict(value))
    _assert_public(identity)
    if set(identity) != _FIELDS:
        raise _fail("identity_shape_invalid")
    if (
        identity.get("schema_version") != SCHEMA
        or identity.get("status") != "FROZEN"
        or identity.get("method") != "M*"
        or identity.get("graphiti_version") != GRAPHITI_VERSION
        or identity.get("graphiti_commit") != GRAPHITI_COMMIT
        or identity.get("qualification_status")
        != "CORE_IDENTITY_ONLY_UNQUALIFIED"
        or identity.get("method_policy") != _METHOD_POLICY
        or identity.get("failure_policy") != _FAILURE_POLICY
        or identity.get("authority") != _AUTHORITY
    ):
        raise _fail("identity_binding_invalid")
    entrypoint = identity.get("runtime_factory_entrypoint")
    if not isinstance(entrypoint, str) or _ENTRYPOINT.fullmatch(entrypoint) is None:
        raise _fail("runtime_factory_entrypoint_invalid")
    for field in _HASH_FIELDS:
        _sha(identity.get(field), f"{field}_invalid")
    expected = payload_sha256(
        {key: item for key, item in identity.items() if key != "identity_sha256"}
    )
    if identity.get("identity_sha256") != expected:
        raise _fail("identity_sha256_mismatch")
    return identity


__all__ = [
    "GRAPHITI_COMMIT",
    "GRAPHITI_VERSION",
    "SCHEMA",
    "S5MStarProductionCoreIdentityError",
    "build_s5_mstar_production_core_identity",
    "verify_s5_mstar_production_core_identity",
]
