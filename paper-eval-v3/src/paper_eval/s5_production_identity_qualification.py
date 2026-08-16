"""Service-free S5 production-identity qualification.

Raw S5 identities describe code and configuration but intentionally remain
``IDENTITY_ONLY_UNQUALIFIED``.  This module seals the independent offline
evidence needed to authorize one bounded read-only preflight.  It never opens a
network connection, accesses Neo4j, grants live execution, or updates the
current-stage pointer.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256, sha256_file
from .s3_native_v2_freeze import (
    NativeBaselineV2FreezeError,
    verify_native_baseline_v2_freeze,
)
from .s5_method_qualification_plan import (
    S5MethodQualificationError,
    verify_s5_method_qualification_plan,
)
from .s5_mstar_production_core_identity import (
    S5MStarProductionCoreIdentityError,
    verify_s5_mstar_production_core_identity,
)
from .s5_production_runner import (
    S5ProductionIdentityError,
    verify_s5_production_identity,
)


SCHEMA = "membind.paper-eval-v3.s5-production-identity-qualification.v1"
QUALIFICATION_STATUS = "PRODUCTION_IDENTITY_OFFLINE_QUALIFIED"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_AP_IDENTITY_SOURCE_FIELDS = {
    "graphiti_native": "graphiti_native_source_sha256",
    "graphiti_semantic_api": "graphiti_semantic_api_sha256",
    "runtime_factory": "runtime_factory_source_sha256",
    "scheduler": "scheduler_source_sha256",
    "scheduler_test": "scheduler_test_source_sha256",
    "durable_store": "durable_store_source_sha256",
    "durable_store_test": "durable_store_test_source_sha256",
    "runtime_config": "runtime_config_sha256",
}

AP_SOURCE_ROLES = frozenset(
    {
        *_AP_IDENTITY_SOURCE_FIELDS,
        "native_binding",
        "native_binding_test",
        "production_runner",
        "production_runner_test",
        "method_smoke_contract",
        "method_smoke_contract_test",
    }
)

_MSTAR_CORE_SOURCE_FIELDS = {
    "graphiti_semantic_api": "graphiti_semantic_api_sha256",
    "runtime_factory": "runtime_factory_source_sha256",
    "pipeline": "pipeline_source_sha256",
    "pipeline_test": "pipeline_test_source_sha256",
    "adapter": "adapter_source_sha256",
    "adapter_test": "adapter_test_source_sha256",
    "semantic_runtime": "semantic_runtime_source_sha256",
    "semantic_runtime_test": "semantic_runtime_test_source_sha256",
    "semantic_binding": "semantic_binding_source_sha256",
    "semantic_binding_test": "semantic_binding_test_source_sha256",
    "durable_store": "durable_store_source_sha256",
    "durable_store_test": "durable_store_test_source_sha256",
    "runtime_config": "runtime_config_sha256",
}

MSTAR_SOURCE_ROLES = frozenset(
    {
        "graphiti_native",
        *_MSTAR_CORE_SOURCE_FIELDS,
        "mstar_production_runner",
        "mstar_production_runner_test",
        "publication_journal",
        "publication_journal_test",
        "method_smoke_contract",
        "method_smoke_contract_test",
    }
)

_AUTHORITY = {
    "s5_read_only_preflight_authorized": True,
    "preflight_scope": "SINGLE_BOUNDED_READ_ONLY_PREFLIGHT",
    "construction_models_get_authorized": True,
    "construction_version_get_authorized": True,
    "embedding_models_get_authorized": True,
    "neo4j_connectivity_check_authorized": True,
    "neo4j_exact_namespace_count_authorized": True,
    "model_generation_authorized": False,
    "embedding_generation_authorized": False,
    "neo4j_mutation_authorized": False,
    "s5_live_execution_authorized": False,
    "pilot_execution_authorized": False,
    "formal_execution_authorized": False,
    "current_stage_pointer_update_authorized": False,
}

_FX0_AUTHORITY = {
    "model_call_authorized": False,
    "neo4j_read_authorized": False,
    "neo4j_mutation_authorized": False,
    "s5_live_execution_authorized": False,
    "current_stage_pointer_update_authorized": False,
}

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

_PAYLOAD_FIELDS = {
    "schema_version",
    "stage",
    "method",
    "qualification_status",
    "raw_identity_qualification_status",
    "production_identity_sha256",
    "production_identity_file_sha256",
    "native_baseline_freeze",
    "current_stage_pointer",
    "s5_plan",
    "s5_workplan_file_sha256",
    "source_closure_sha256",
    "source_closure_digest",
    "full_regression",
    "mstar_fx0",
    "authority",
}


class S5ProductionIdentityQualificationError(ValueError):
    """The offline identity qualification is incomplete or has drifted."""


def _fail(code: str) -> S5ProductionIdentityQualificationError:
    return S5ProductionIdentityQualificationError(code)


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_qualification_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


def _mapping(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    return deepcopy(dict(value))


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _file_sha(path: Path, code: str) -> str:
    digest = sha256_file(Path(path))
    if digest == "missing":
        raise _fail(code)
    return digest


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    return _mapping(value, code)


def _sealed(value: object, code: str) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = _mapping(value, code)
    payload = _mapping(artifact.get("payload"), code)
    if (
        set(artifact)
        != {
            "protocol_version",
            "git_commit",
            "run_id",
            "status",
            "payload",
            "payload_sha256",
        }
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise _fail(code)
    artifact["payload"] = payload
    return artifact, payload


def _junit_summary(path: Path) -> dict[str, int]:
    try:
        root = ElementTree.parse(Path(path)).getroot()
    except (OSError, ElementTree.ParseError):
        raise _fail("full_regression_junit_invalid") from None
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        suites = list(root.iter("testsuite"))
    if not suites:
        raise _fail("full_regression_junit_invalid")
    summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for field in summary:
            try:
                summary[field] += int(suite.attrib.get(field, "0"))
            except ValueError:
                raise _fail("full_regression_junit_invalid") from None
    return summary


def _source_closure(
    *, method: str, source_paths: Mapping[str, Path]
) -> dict[str, str]:
    selected = _mapping(source_paths, "source_closure_invalid")
    expected = MSTAR_SOURCE_ROLES if method == "M*" else AP_SOURCE_ROLES
    if set(selected) != expected:
        raise _fail("source_closure_inventory_invalid")
    return {
        role: _source_identity_sha256(
            role=role,
            path=Path(selected[role]),
        )
        for role in sorted(expected)
    }


def _source_identity_sha256(*, role: str, path: Path) -> str:
    """Return the digest represented by one source-closure role.

    Most roles are source files and therefore use their byte hash.  Two roles
    are already sealed semantic artifacts in the production path: the pinned
    Graphiti API identity and the runtime configuration.  Their corresponding
    production-identity fields intentionally carry the verified semantic or
    payload digest, not incidental JSON envelope bytes.  Tiny text fixtures
    used by the generic contract tests retain the ordinary file-hash behavior.
    """

    file_digest = _file_sha(path, f"source_closure_missing:{role}")
    if role not in {"graphiti_semantic_api", "runtime_config"}:
        return file_digest
    try:
        artifact = _load_json(path, f"source_closure_invalid:{role}")
    except S5ProductionIdentityQualificationError:
        return file_digest
    if role == "runtime_config":
        payload = artifact.get("payload")
        digest = artifact.get("payload_sha256")
        if isinstance(payload, Mapping) and digest == payload_sha256(payload):
            return _sha(digest, "runtime_config_payload_sha256_invalid")
        return file_digest

    digest = artifact.get("identity_sha256")
    stored_payload_digest = artifact.get("payload_sha256")
    semantic_payload = {
        key: value for key, value in artifact.items() if key != "payload_sha256"
    }
    if (
        _SHA256.fullmatch(str(digest or "")) is not None
        and stored_payload_digest == payload_sha256(semantic_payload)
    ):
        return str(digest)
    return file_digest


def _check_ap_identity_sources(
    identity: Mapping[str, object], closure: Mapping[str, str]
) -> None:
    if any(
        identity.get(field) != closure[role]
        for role, field in _AP_IDENTITY_SOURCE_FIELDS.items()
    ):
        raise _fail("source_binding_identity_mismatch")


def _check_mstar_sources(
    *,
    identity: Mapping[str, object],
    core: Mapping[str, object],
    closure: Mapping[str, str],
) -> None:
    if (
        identity.get("graphiti_native_source_sha256")
        != closure["graphiti_native"]
        or any(
            core.get(field) != closure[role]
            for role, field in _MSTAR_CORE_SOURCE_FIELDS.items()
        )
        or identity.get("graphiti_semantic_api_sha256")
        != core.get("graphiti_semantic_api_sha256")
        or identity.get("runtime_factory_source_sha256")
        != core.get("runtime_factory_source_sha256")
        or identity.get("runtime_factory_entrypoint")
        != core.get("runtime_factory_entrypoint")
        or identity.get("scheduler_source_sha256")
        != core.get("pipeline_source_sha256")
        or identity.get("scheduler_test_source_sha256")
        != core.get("pipeline_test_source_sha256")
        or identity.get("durable_store_source_sha256")
        != core.get("durable_store_source_sha256")
        or identity.get("durable_store_test_source_sha256")
        != core.get("durable_store_test_source_sha256")
        or identity.get("runtime_config_sha256")
        != core.get("runtime_config_sha256")
    ):
        raise _fail("source_binding_mstar_core_mismatch")


def _verify_fx0_qualification(
    *,
    path: Path,
    core: Mapping[str, object],
    identity: Mapping[str, object],
    current_stage_pointer_sha256: str,
) -> dict[str, object]:
    artifact, payload = _sealed(
        _load_json(path, "mstar_fx0_qualification_invalid"),
        "mstar_fx0_qualification_invalid",
    )
    expected_fields = {
        "schema_version",
        "verdict",
        "fixture_count",
        "run_id",
        "runtime_config_sha256",
        "production_core_identity_sha256",
        "fx0_artifact_payload_sha256",
        "fx0_fixture_manifest_sha256",
        "current_stage_pointer_sha256",
        "full_regression_junit_sha256",
        "full_regression_summary",
        "legacy_status_artifact_preserved",
        "authority",
    }
    summary = _mapping(
        payload.get("full_regression_summary"), "mstar_fx0_summary_invalid"
    )
    for field in (
        "runtime_config_sha256",
        "production_core_identity_sha256",
        "fx0_artifact_payload_sha256",
        "fx0_fixture_manifest_sha256",
        "current_stage_pointer_sha256",
        "full_regression_junit_sha256",
    ):
        _sha(payload.get(field), "mstar_fx0_hash_invalid")
    if (
        set(payload) != expected_fields
        or artifact.get("protocol_version") != PROTOCOL_VERSION
        or payload.get("schema_version")
        != "membind.paper-eval-v3.s5-graphiti-fx0-production-qualification.v1"
        or payload.get("verdict") != "PRODUCTION_PATH_EXACT_PARITY_PASS"
        or payload.get("fixture_count") != 11
        or payload.get("legacy_status_artifact_preserved") is not True
        or payload.get("authority") != _FX0_AUTHORITY
        or summary.get("tests", 0) < 1
        or summary.get("failures") != 0
        or summary.get("errors") != 0
        or summary.get("skipped") != 0
        or payload.get("runtime_config_sha256")
        != core.get("runtime_config_sha256")
        or payload.get("production_core_identity_sha256")
        != core.get("identity_sha256")
        or payload.get("fx0_artifact_payload_sha256")
        != identity.get("fx0_parity_artifact_sha256")
        or payload.get("current_stage_pointer_sha256")
        != current_stage_pointer_sha256
    ):
        raise _fail("mstar_fx0_binding_invalid")
    return {
        "qualification_file_sha256": _file_sha(
            path, "mstar_fx0_qualification_missing"
        ),
        "qualification_payload_sha256": artifact["payload_sha256"],
        "production_core_identity_sha256": core["identity_sha256"],
        "fx0_artifact_payload_sha256": payload["fx0_artifact_payload_sha256"],
        "fx0_fixture_manifest_sha256": payload["fx0_fixture_manifest_sha256"],
        "verdict": payload["verdict"],
    }


def build_s5_production_identity_qualification(
    *,
    method: str,
    production_identity_path: Path,
    native_baseline_freeze_path: Path,
    current_stage_pointer_path: Path,
    s5_plan_path: Path,
    s5_workplan_path: Path,
    source_paths: Mapping[str, Path],
    full_regression_junit_path: Path,
    expected_full_test_count: int,
    git_commit: str,
    run_id: str,
    mstar_core_identity_path: Path | None = None,
    mstar_fx0_qualification_path: Path | None = None,
) -> dict[str, object]:
    """Build a hash-sealed offline gate for one raw S5 method identity."""

    if method not in {"A0", "P*", "M*"}:
        raise _fail("method_invalid")
    if not isinstance(git_commit, str) or not git_commit:
        raise _fail("git_commit_invalid")
    if not isinstance(run_id, str) or not run_id:
        raise _fail("run_id_invalid")
    try:
        identity = verify_s5_production_identity(
            _load_json(production_identity_path, "production_identity_invalid")
        )
    except (S5ProductionIdentityError, ValueError):
        raise _fail("production_identity_invalid") from None
    if identity["method"] != method:
        raise _fail("production_identity_method_mismatch")

    freeze_artifact = _load_json(
        native_baseline_freeze_path, "native_baseline_freeze_invalid"
    )
    try:
        freeze = verify_native_baseline_v2_freeze(freeze_artifact)
    except (NativeBaselineV2FreezeError, ValueError):
        raise _fail("native_baseline_freeze_invalid") from None
    freeze_payload = freeze["payload"]
    freeze_file_sha = _file_sha(
        native_baseline_freeze_path, "native_baseline_freeze_missing"
    )

    current, current_payload = _sealed(
        _load_json(current_stage_pointer_path, "current_stage_pointer_invalid"),
        "current_stage_pointer_invalid",
    )
    if (
        current.get("protocol_version") != PROTOCOL_VERSION
        or current_payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or current_payload.get("status") != "PASS_CONFIGURATION_FREEZE_ONLY"
        or current_payload.get("native_baseline_v2_freeze_file_sha256")
        != freeze_file_sha
        or current_payload.get("native_baseline_v2_freeze_payload_sha256")
        != freeze["payload_sha256"]
        or current_payload.get("s4_live_execution_authorized") is not False
        or current_payload.get("pilot_execution_authorized") is not False
    ):
        raise _fail("current_stage_pointer_binding_invalid")
    current_file_sha = _file_sha(
        current_stage_pointer_path, "current_stage_pointer_missing"
    )

    try:
        plan = verify_s5_method_qualification_plan(
            _load_json(s5_plan_path, "s5_plan_invalid")
        )
    except (S5MethodQualificationError, ValueError):
        raise _fail("s5_plan_invalid") from None
    plan_bindings = plan["payload"]["input_bindings"]
    if (
        plan_bindings["native_baseline_freeze"]["file_sha256"]
        != freeze_file_sha
        or plan_bindings["native_baseline_freeze"]["payload_sha256"]
        != freeze["payload_sha256"]
        or plan_bindings["current_stage_pointer"]["file_sha256"]
        != current_file_sha
        or plan_bindings["current_stage_pointer"]["payload_sha256"]
        != current["payload_sha256"]
    ):
        raise _fail("s5_plan_frozen_input_binding_invalid")

    closure = _source_closure(method=method, source_paths=source_paths)
    mstar_fx0: dict[str, object] | None = None
    if method == "M*":
        if mstar_core_identity_path is None or mstar_fx0_qualification_path is None:
            raise _fail("mstar_fx0_qualification_required")
        try:
            core = verify_s5_mstar_production_core_identity(
                _load_json(mstar_core_identity_path, "mstar_core_identity_invalid")
            )
        except (S5MStarProductionCoreIdentityError, ValueError):
            raise _fail("mstar_core_identity_invalid") from None
        _check_mstar_sources(identity=identity, core=core, closure=closure)
        mstar_fx0 = _verify_fx0_qualification(
            path=Path(mstar_fx0_qualification_path),
            core=core,
            identity=identity,
            current_stage_pointer_sha256=current_file_sha,
        )
        mstar_fx0["production_core_identity_file_sha256"] = _file_sha(
            mstar_core_identity_path, "mstar_core_identity_missing"
        )
    else:
        if mstar_core_identity_path is not None or mstar_fx0_qualification_path is not None:
            raise _fail("non_mstar_fx0_forbidden")
        _check_ap_identity_sources(identity, closure)

    summary = _junit_summary(full_regression_junit_path)
    if (
        isinstance(expected_full_test_count, bool)
        or not isinstance(expected_full_test_count, int)
        or summary
        != {
            "tests": expected_full_test_count,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
        }
    ):
        raise _fail("full_regression_not_green")

    payload: dict[str, object] = {
        "schema_version": SCHEMA,
        "stage": "S5_PRODUCTION_IDENTITY_QUALIFICATION",
        "method": method,
        "qualification_status": QUALIFICATION_STATUS,
        "raw_identity_qualification_status": "IDENTITY_ONLY_UNQUALIFIED",
        "production_identity_sha256": identity["identity_sha256"],
        "production_identity_file_sha256": _file_sha(
            production_identity_path, "production_identity_missing"
        ),
        "native_baseline_freeze": {
            "file_sha256": freeze_file_sha,
            "payload_sha256": freeze["payload_sha256"],
            "baseline_id": freeze_payload["baseline_id"],
        },
        "current_stage_pointer": {
            "file_sha256": current_file_sha,
            "payload_sha256": current["payload_sha256"],
            "run_id": current["run_id"],
            "current_stage": current_payload["current_stage"],
        },
        "s5_plan": {
            "file_sha256": _file_sha(s5_plan_path, "s5_plan_missing"),
            "payload_sha256": plan["payload_sha256"],
            "run_id": plan["run_id"],
            "status": plan["payload"]["status"],
        },
        "s5_workplan_file_sha256": _file_sha(
            s5_workplan_path, "s5_workplan_missing"
        ),
        "source_closure_sha256": closure,
        "source_closure_digest": payload_sha256(closure),
        "full_regression": {
            "junit_file_sha256": _file_sha(
                full_regression_junit_path, "full_regression_junit_missing"
            ),
            **summary,
        },
        "mstar_fx0": mstar_fx0,
        "authority": deepcopy(_AUTHORITY),
    }
    _assert_public(payload)
    return verify_s5_production_identity_qualification(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=git_commit,
            run_id=run_id,
        )
    )


def verify_s5_production_identity_qualification(
    value: Mapping[str, object],
) -> dict[str, object]:
    """Verify the sealed preflight-only qualification without filesystem I/O."""

    artifact, payload = _sealed(value, "qualification_envelope_invalid")
    _assert_public(artifact)
    method = payload.get("method")
    closure = _mapping(
        payload.get("source_closure_sha256"), "source_closure_invalid"
    )
    expected_roles = MSTAR_SOURCE_ROLES if method == "M*" else AP_SOURCE_ROLES
    if set(closure) != expected_roles:
        raise _fail("source_closure_inventory_invalid")
    for digest in closure.values():
        _sha(digest, "source_closure_hash_invalid")
    if payload.get("source_closure_digest") != payload_sha256(closure):
        raise _fail("source_closure_digest_invalid")

    freeze = _mapping(
        payload.get("native_baseline_freeze"), "native_baseline_binding_invalid"
    )
    current = _mapping(
        payload.get("current_stage_pointer"), "current_stage_binding_invalid"
    )
    plan = _mapping(payload.get("s5_plan"), "s5_plan_binding_invalid")
    regression = _mapping(
        payload.get("full_regression"), "full_regression_binding_invalid"
    )
    for binding, fields, code in (
        (
            freeze,
            {"file_sha256", "payload_sha256", "baseline_id"},
            "native_baseline_binding_invalid",
        ),
        (
            current,
            {"file_sha256", "payload_sha256", "run_id", "current_stage"},
            "current_stage_binding_invalid",
        ),
        (
            plan,
            {"file_sha256", "payload_sha256", "run_id", "status"},
            "s5_plan_binding_invalid",
        ),
        (
            regression,
            {"junit_file_sha256", "tests", "failures", "errors", "skipped"},
            "full_regression_binding_invalid",
        ),
    ):
        if set(binding) != fields:
            raise _fail(code)
    for binding in (freeze, current, plan):
        _sha(binding.get("file_sha256"), "binding_file_hash_invalid")
        _sha(binding.get("payload_sha256"), "binding_payload_hash_invalid")
    _sha(regression.get("junit_file_sha256"), "full_regression_hash_invalid")
    _sha(payload.get("production_identity_sha256"), "identity_hash_invalid")
    _sha(
        payload.get("production_identity_file_sha256"),
        "identity_file_hash_invalid",
    )
    _sha(payload.get("s5_workplan_file_sha256"), "s5_workplan_hash_invalid")

    if method == "M*":
        fx0 = _mapping(payload.get("mstar_fx0"), "mstar_fx0_binding_invalid")
        if set(fx0) != {
            "qualification_file_sha256",
            "qualification_payload_sha256",
            "production_core_identity_sha256",
            "production_core_identity_file_sha256",
            "fx0_artifact_payload_sha256",
            "fx0_fixture_manifest_sha256",
            "verdict",
        }:
            raise _fail("mstar_fx0_binding_invalid")
        for field in set(fx0) - {"verdict"}:
            _sha(fx0.get(field), "mstar_fx0_hash_invalid")
        if fx0.get("verdict") != "PRODUCTION_PATH_EXACT_PARITY_PASS":
            raise _fail("mstar_fx0_verdict_invalid")
    elif payload.get("mstar_fx0") is not None:
        raise _fail("non_mstar_fx0_forbidden")

    if (
        set(payload) != _PAYLOAD_FIELDS
        or artifact.get("protocol_version") != PROTOCOL_VERSION
        or payload.get("schema_version") != SCHEMA
        or payload.get("stage") != "S5_PRODUCTION_IDENTITY_QUALIFICATION"
        or method not in {"A0", "P*", "M*"}
        or payload.get("qualification_status") != QUALIFICATION_STATUS
        or payload.get("raw_identity_qualification_status")
        != "IDENTITY_ONLY_UNQUALIFIED"
        or freeze.get("baseline_id") != "native-graphiti-u0-reader-v2"
        or current.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or plan.get("status") != "OFFLINE_DESIGN_ONLY"
        or regression.get("tests", 0) < 1
        or regression.get("failures") != 0
        or regression.get("errors") != 0
        or regression.get("skipped") != 0
    ):
        raise _fail("qualification_binding_invalid")
    if payload.get("authority") != _AUTHORITY:
        raise _fail("qualification_authority_invalid")
    return artifact


def verify_s5_production_identity_qualification_binding(
    value: Mapping[str, object],
) -> dict[str, object]:
    """Verify the canonical qualification projection carried downstream."""

    binding = _mapping(value, "qualification_binding_invalid")
    if set(binding) != {
        "method",
        "qualification_file_sha256",
        "qualification_payload_sha256",
        "production_identity_sha256",
        "production_identity_file_sha256",
        "native_baseline_freeze",
        "current_stage_pointer",
        "s5_plan",
        "s5_workplan_file_sha256",
        "source_closure_digest",
        "full_regression",
        "mstar_fx0",
    }:
        raise _fail("qualification_binding_invalid")
    method = binding.get("method")
    if method not in {"A0", "P*", "M*"}:
        raise _fail("qualification_binding_method_invalid")
    for field in (
        "qualification_file_sha256",
        "qualification_payload_sha256",
        "production_identity_sha256",
        "production_identity_file_sha256",
        "s5_workplan_file_sha256",
        "source_closure_digest",
    ):
        _sha(binding.get(field), "qualification_binding_hash_invalid")
    freeze = _mapping(
        binding.get("native_baseline_freeze"), "qualification_binding_invalid"
    )
    current = _mapping(
        binding.get("current_stage_pointer"), "qualification_binding_invalid"
    )
    plan = _mapping(binding.get("s5_plan"), "qualification_binding_invalid")
    regression = _mapping(
        binding.get("full_regression"), "qualification_binding_invalid"
    )
    if (
        set(freeze) != {"file_sha256", "payload_sha256", "baseline_id"}
        or freeze.get("baseline_id") != "native-graphiti-u0-reader-v2"
        or set(current)
        != {"file_sha256", "payload_sha256", "run_id", "current_stage"}
        or current.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or set(plan) != {"file_sha256", "payload_sha256", "run_id", "status"}
        or plan.get("status") != "OFFLINE_DESIGN_ONLY"
        or set(regression)
        != {"junit_file_sha256", "tests", "failures", "errors", "skipped"}
        or regression.get("tests", 0) < 1
        or regression.get("failures") != 0
        or regression.get("errors") != 0
        or regression.get("skipped") != 0
    ):
        raise _fail("qualification_binding_invalid")
    for selected in (freeze, current, plan):
        _sha(selected.get("file_sha256"), "qualification_binding_hash_invalid")
        _sha(selected.get("payload_sha256"), "qualification_binding_hash_invalid")
    _sha(
        regression.get("junit_file_sha256"),
        "qualification_binding_hash_invalid",
    )
    fx0 = binding.get("mstar_fx0")
    if method == "M*":
        selected_fx0 = _mapping(fx0, "qualification_binding_fx0_invalid")
        if set(selected_fx0) != {
            "qualification_file_sha256",
            "qualification_payload_sha256",
            "production_core_identity_sha256",
            "production_core_identity_file_sha256",
            "fx0_artifact_payload_sha256",
            "fx0_fixture_manifest_sha256",
            "verdict",
        }:
            raise _fail("qualification_binding_fx0_invalid")
        for field in set(selected_fx0) - {"verdict"}:
            _sha(selected_fx0.get(field), "qualification_binding_fx0_invalid")
        if selected_fx0.get("verdict") != "PRODUCTION_PATH_EXACT_PARITY_PASS":
            raise _fail("qualification_binding_fx0_invalid")
    elif fx0 is not None:
        raise _fail("qualification_binding_fx0_forbidden")
    _assert_public(binding)
    return binding


def bind_s5_production_identity_qualification(
    value: Mapping[str, object], *, file_sha256: str
) -> dict[str, object]:
    """Project one verified qualification into its downstream hash binding."""

    artifact = verify_s5_production_identity_qualification(value)
    payload = artifact["payload"]
    return verify_s5_production_identity_qualification_binding(
        {
            "method": payload["method"],
            "qualification_file_sha256": _sha(
                file_sha256, "qualification_file_sha256_invalid"
            ),
            "qualification_payload_sha256": artifact["payload_sha256"],
            "production_identity_sha256": payload[
                "production_identity_sha256"
            ],
            "production_identity_file_sha256": payload[
                "production_identity_file_sha256"
            ],
            "native_baseline_freeze": deepcopy(
                payload["native_baseline_freeze"]
            ),
            "current_stage_pointer": deepcopy(payload["current_stage_pointer"]),
            "s5_plan": deepcopy(payload["s5_plan"]),
            "s5_workplan_file_sha256": payload["s5_workplan_file_sha256"],
            "source_closure_digest": payload["source_closure_digest"],
            "full_regression": deepcopy(payload["full_regression"]),
            "mstar_fx0": deepcopy(payload["mstar_fx0"]),
        }
    )


def write_s5_production_identity_qualification_exclusive(
    path: Path, value: Mapping[str, object]
) -> dict[str, object]:
    """Persist a verified qualification once; never overwrite prior evidence."""

    artifact = verify_s5_production_identity_qualification(value)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = (
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except FileExistsError:
        raise _fail("output_exists") from None
    try:
        os.write(descriptor, serialized)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(output.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return artifact


__all__ = [
    "AP_SOURCE_ROLES",
    "MSTAR_SOURCE_ROLES",
    "QUALIFICATION_STATUS",
    "SCHEMA",
    "S5ProductionIdentityQualificationError",
    "bind_s5_production_identity_qualification",
    "build_s5_production_identity_qualification",
    "verify_s5_production_identity_qualification",
    "verify_s5_production_identity_qualification_binding",
    "write_s5_production_identity_qualification_exclusive",
]
