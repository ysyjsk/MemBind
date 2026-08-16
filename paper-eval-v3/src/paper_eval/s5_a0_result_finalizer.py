"""Independent on-disk finalizer for one completed S5 A0 smoke.

The live controller deliberately cannot declare scientific success.  This
module reopens every durable input, verifies their cross-bindings, injects the
mandatory post-namespace invariant observation into the common smoke
contract, and only then writes an exclusive A0 result artifact.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256, sha256_file
from .s5_a0_controller import inspect_s5_a0_controller_attempt
from .s5_durable_attempt_store import inspect_s5_attempt
from .s5_live_authority import (
    verify_s5_live_authority,
    verify_s5_live_authority_consumption,
)
from .s5_live_preflight import verify_s5_live_preflight
from .s5_method_smoke_contract import (
    native_method_to_smoke_records,
    validate_smoke_records,
)
from .s5_native_post_observation import verify_s5_native_post_observation
from .s5_production_identity_qualification import (
    bind_s5_production_identity_qualification,
    verify_s5_production_identity_qualification,
)
from .s5_production_runner import verify_s5_production_identity


SCHEMA = "membind.paper-eval-v3.s5-a0-scientific-result.v1"
_RESULT_SOURCE = Path(__file__).resolve()
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_AUTHORITY = {
    "scientific_pass_authorized": True,
    "next_method_authorized": True,
    "current_stage_pointer_update_authorized": False,
    "pilot_execution_authorized": False,
    "formal_execution_authorized": False,
    "resume_authorized": False,
    "namespace_cleanup_authorized": False,
}
_PAYLOAD_FIELDS = {
    "schema_version",
    "stage",
    "method",
    "verdict",
    "run_id",
    "execution_identity_sha256",
    "source_manifest_sha256",
    "production_identity_sha256",
    "smoke_summary",
    "direct_invariant_observation",
    "bindings",
    "source_sha256",
    "authority",
}
_BINDING_FIELDS = {
    "production_identity_file_sha256",
    "production_identity_qualification_file_sha256",
    "current_stage_pointer_file_sha256",
    "preflight_file_sha256",
    "authority_file_sha256",
    "consumption_file_sha256",
    "controller_events_file_sha256",
    "controller_checkpoint_file_sha256",
    "native_manifest_file_sha256",
    "native_events_file_sha256",
    "native_checkpoint_file_sha256",
    "native_result_file_sha256",
    "post_observation_file_sha256",
}


class S5A0FinalizerError(ValueError):
    """The A0 evidence chain cannot be promoted to scientific PASS."""


def _fail(code: str) -> S5A0FinalizerError:
    return S5A0FinalizerError(code)


@dataclass(frozen=True)
class S5A0FinalizerPaths:
    """All immutable inputs and the one exclusive result output."""

    production_identity: Path
    production_identity_qualification: Path
    current_stage_pointer: Path
    preflight: Path
    authority: Path
    consumption: Path
    controller_root: Path
    attempt_root: Path
    post_observation: Path
    result: Path


def _load(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _file_sha(path: Path, code: str) -> str:
    digest = sha256_file(Path(path))
    if digest == "missing":
        raise _fail(code)
    return digest


def _sealed_pointer(path: Path) -> tuple[dict[str, Any], str]:
    artifact = _load(path, "current_stage_pointer_invalid")
    payload = artifact.get("payload")
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
        or artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or not isinstance(payload, Mapping)
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or payload.get("schema_version")
        != "membind.paper-eval-v3.current-stage-pointer.v2"
        or payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or payload.get("live_preflight_required") is not True
    ):
        raise _fail("current_stage_pointer_invalid")
    return artifact, _file_sha(path, "current_stage_pointer_invalid")


def _exclusive_json(path: Path, value: Mapping[str, object]) -> None:
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    data = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    try:
        descriptor = os.open(
            selected,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o664,
        )
    except FileExistsError:
        raise _fail("result_exists") from None
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(selected.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _hash_bindings(paths: S5A0FinalizerPaths) -> dict[str, str]:
    values = {
        "production_identity_file_sha256": paths.production_identity,
        "production_identity_qualification_file_sha256": (
            paths.production_identity_qualification
        ),
        "current_stage_pointer_file_sha256": paths.current_stage_pointer,
        "preflight_file_sha256": paths.preflight,
        "authority_file_sha256": paths.authority,
        "consumption_file_sha256": paths.consumption,
        "controller_events_file_sha256": paths.controller_root / "events.jsonl",
        "controller_checkpoint_file_sha256": (
            paths.controller_root / "checkpoint.json"
        ),
        "native_manifest_file_sha256": paths.attempt_root / "manifest.json",
        "native_events_file_sha256": paths.attempt_root / "events.jsonl",
        "native_checkpoint_file_sha256": paths.attempt_root / "checkpoint.json",
        "native_result_file_sha256": paths.attempt_root / "result.json",
        "post_observation_file_sha256": paths.post_observation,
    }
    return {name: _file_sha(path, f"binding_missing:{name}") for name, path in values.items()}


def _verify_chain(paths: S5A0FinalizerPaths) -> dict[str, object]:
    if not isinstance(paths, S5A0FinalizerPaths):
        raise _fail("finalizer_paths_invalid")
    if Path(paths.result).exists():
        raise _fail("result_exists")

    try:
        identity = verify_s5_production_identity(
            _load(paths.production_identity, "production_identity_invalid")
        )
    except Exception:
        raise _fail("production_identity_invalid") from None
    identity_file_sha = _file_sha(
        paths.production_identity, "production_identity_invalid"
    )
    if identity.get("method") != "A0":
        raise _fail("production_identity_method_mismatch")

    try:
        qualification = verify_s5_production_identity_qualification(
            _load(
                paths.production_identity_qualification,
                "production_identity_qualification_invalid",
            )
        )
        qualification_file_sha = _file_sha(
            paths.production_identity_qualification,
            "production_identity_qualification_invalid",
        )
        qualification_binding = bind_s5_production_identity_qualification(
            qualification,
            file_sha256=qualification_file_sha,
        )
    except Exception:
        raise _fail("production_identity_qualification_invalid") from None
    if (
        qualification_binding.get("method") != "A0"
        or qualification_binding.get("production_identity_sha256")
        != identity.get("identity_sha256")
        or qualification_binding.get("production_identity_file_sha256")
        != identity_file_sha
    ):
        raise _fail("production_identity_qualification_binding_mismatch")

    pointer, pointer_file_sha = _sealed_pointer(paths.current_stage_pointer)
    qualified_pointer = qualification_binding.get("current_stage_pointer")
    if (
        not isinstance(qualified_pointer, Mapping)
        or qualified_pointer.get("file_sha256") != pointer_file_sha
        or qualified_pointer.get("payload_sha256")
        != pointer.get("payload_sha256")
        or qualified_pointer.get("run_id") != pointer.get("run_id")
    ):
        raise _fail("qualification_pointer_binding_mismatch")

    try:
        preflight = verify_s5_live_preflight(
            _load(paths.preflight, "preflight_invalid")
        )
    except Exception:
        raise _fail("preflight_invalid") from None
    preflight_file_sha = _file_sha(paths.preflight, "preflight_invalid")
    preflight_payload = preflight["payload"]
    if (
        preflight_payload.get("method") != "A0"
        or preflight_payload.get("production_identity_qualification")
        != qualification_binding
        or preflight_payload.get("current_stage_pointer", {}).get("file_sha256")
        != pointer_file_sha
        or preflight_payload.get("current_stage_pointer", {}).get(
            "payload_sha256"
        )
        != pointer.get("payload_sha256")
    ):
        raise _fail("preflight_chain_mismatch")

    try:
        authority = verify_s5_live_authority(
            _load(paths.authority, "authority_invalid")
        )
    except Exception:
        raise _fail("authority_invalid") from None
    authority_file_sha = _file_sha(paths.authority, "authority_invalid")
    authority_payload = authority["payload"]
    run = authority_payload.get("run")
    source_binding = authority_payload.get("source_sha256")
    if (
        authority_payload.get("method") != "A0"
        or not isinstance(run, Mapping)
        or authority_payload.get("production_identity_qualification")
        != qualification_binding
        or authority_payload.get("preflight_file_sha256") != preflight_file_sha
        or authority_payload.get("preflight_payload_sha256")
        != preflight.get("payload_sha256")
        or authority_payload.get("current_stage_pointer_sha256")
        != pointer_file_sha
    ):
        raise _fail("authority_chain_mismatch")
    if (
        not isinstance(source_binding, Mapping)
        or source_binding.get("result_verifier")
        != _file_sha(_RESULT_SOURCE, "result_verifier_source_missing")
    ):
        raise _fail("result_verifier_source_binding_mismatch")

    try:
        consumption = verify_s5_live_authority_consumption(
            _load(paths.consumption, "consumption_invalid")
        )
    except Exception:
        raise _fail("consumption_invalid") from None
    consumption_payload = consumption["payload"]
    if (
        consumption_payload.get("method") != "A0"
        or consumption_payload.get("run") != run
        or consumption_payload.get("authority_file_sha256")
        != authority_file_sha
        or consumption_payload.get("authority_payload_sha256")
        != authority.get("payload_sha256")
        or consumption_payload.get("production_identity_sha256")
        != identity.get("identity_sha256")
        or consumption_payload.get(
            "production_identity_qualification_payload_sha256"
        )
        != qualification.get("payload_sha256")
    ):
        raise _fail("consumption_chain_mismatch")

    try:
        controller = inspect_s5_a0_controller_attempt(paths.controller_root)
    except Exception:
        raise _fail("controller_attempt_invalid") from None
    controller_checkpoint = controller["checkpoint"]
    controller_events = controller["events"]
    expected_controller_events = [
        "authority_consumed",
        "runtime_constructed",
        "runtime_ready",
        "native_runner_started",
        "runtime_closed",
        "raw_runner_evidence_complete",
    ]
    if (
        controller_checkpoint.get("run_id") != run.get("run_id")
        or controller_checkpoint.get("status")
        != "controller_complete_evidence_only"
        or [event.get("event_type") for event in controller_events]
        != expected_controller_events
    ):
        raise _fail("controller_attempt_incomplete")

    try:
        attempt = inspect_s5_attempt(paths.attempt_root)
    except Exception:
        raise _fail("native_attempt_invalid") from None
    manifest = attempt["manifest"]
    native_result = attempt.get("result")
    native_payload = (
        native_result.get("payload") if isinstance(native_result, Mapping) else None
    )
    source_sha256s = manifest.get("source_sha256s")
    if (
        manifest.get("run_id") != run.get("run_id")
        or manifest.get("method") != "A0"
        or manifest.get("production_core_identity_sha256")
        != identity.get("identity_sha256")
        or not isinstance(source_sha256s, list)
        or len(source_sha256s) != 49
        or not isinstance(native_result, Mapping)
        or native_result.get("status") != "complete"
        or not isinstance(native_payload, Mapping)
        or native_payload.get("status") != "PASS"
    ):
        raise _fail("native_attempt_incomplete")
    source_manifest = payload_sha256(
        [
            {"source_sequence": index, "source_sha256": digest}
            for index, digest in enumerate(source_sha256s)
        ]
    )
    if source_manifest != run.get("source_manifest_sha256"):
        raise _fail("native_source_manifest_mismatch")

    try:
        post = verify_s5_native_post_observation(
            _load(paths.post_observation, "post_observation_invalid"),
            expected_method="A0",
            expected_run_id=str(run.get("run_id", "")),
            expected_namespace=str(run.get("namespace", "")),
        )
    except Exception:
        raise _fail("post_observation_invalid") from None
    execution_identity_sha = payload_sha256(
        {"run_id": run.get("run_id"), "namespace": run.get("namespace")}
    )
    publications = [
        event
        for event in attempt["events"]
        if event.get("event_type") == "publication"
    ]
    publication_manifest = payload_sha256(
        [
            {
                "source_sequence": event.get("source_sequence"),
                "source_sha256": event.get("source_sha256"),
            }
            for event in publications
        ]
    )
    if (
        post.get("method") != "A0"
        or post.get("execution_identity_sha256") != execution_identity_sha
        or post.get("source_manifest_sha256") != source_manifest
        or post.get("durable_publication_manifest_sha256")
        != publication_manifest
    ):
        raise _fail("post_observation_binding_mismatch")
    if (
        post.get("status") != "PASS"
        or post.get("global_violation_total") != 0
    ):
        raise _fail("direct_invariant_violation_observed")

    try:
        records = native_method_to_smoke_records(
            native_payload,
            direct_invariant_violations=post["per_source_violation_counts"],
        )
        smoke_summary = validate_smoke_records(
            "A0",
            expected_source_sequences=list(range(49)),
            records=records,
        )
    except Exception:
        raise _fail("native_smoke_contract_invalid") from None
    if smoke_summary.get("direct_invariant_violation_count") != 0:
        raise _fail("direct_invariant_violation_observed")

    return {
        "run": dict(run),
        "identity": identity,
        "source_manifest_sha256": source_manifest,
        "execution_identity_sha256": execution_identity_sha,
        "post": post,
        "smoke_summary": smoke_summary,
        "bindings": _hash_bindings(paths),
    }


def finalize_s5_a0_result(
    *, paths: S5A0FinalizerPaths, git_commit: str
) -> dict[str, object]:
    """Verify the complete chain and exclusively persist scientific A0 PASS."""

    if not isinstance(git_commit, str) or not git_commit:
        raise _fail("git_commit_invalid")
    verified = _verify_chain(paths)
    run = verified["run"]
    post = verified["post"]
    payload = {
        "schema_version": SCHEMA,
        "stage": "S5_A0_METHOD_SMOKE",
        "method": "A0",
        "verdict": "PASS",
        "run_id": run["run_id"],
        "execution_identity_sha256": verified["execution_identity_sha256"],
        "source_manifest_sha256": verified["source_manifest_sha256"],
        "production_identity_sha256": verified["identity"]["identity_sha256"],
        "smoke_summary": verified["smoke_summary"],
        "direct_invariant_observation": {
            "status": post["status"],
            "global_violation_total": post["global_violation_total"],
            "counts": post["counts"],
            "observation_sha256": post["observation_sha256"],
        },
        "bindings": verified["bindings"],
        "source_sha256": {
            "result_verifier": _file_sha(
                _RESULT_SOURCE, "result_verifier_source_missing"
            )
        },
        "authority": deepcopy(_AUTHORITY),
    }
    artifact = verify_s5_a0_result(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=git_commit,
            run_id=f"{run['run_id']}-result",
        )
    )
    _exclusive_json(paths.result, artifact)
    return artifact


def verify_s5_a0_result(value: Mapping[str, object]) -> dict[str, object]:
    """Verify the sealed public result without reopening private/live state."""

    if not isinstance(value, Mapping):
        raise _fail("result_invalid")
    artifact = deepcopy(dict(value))
    payload = artifact.get("payload")
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
        or artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or not isinstance(payload, Mapping)
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or set(payload) != _PAYLOAD_FIELDS
        or payload.get("schema_version") != SCHEMA
        or payload.get("stage") != "S5_A0_METHOD_SMOKE"
        or payload.get("method") != "A0"
        or payload.get("verdict") != "PASS"
        or payload.get("authority") != _AUTHORITY
    ):
        raise _fail("result_invalid")
    for field in (
        "execution_identity_sha256",
        "source_manifest_sha256",
        "production_identity_sha256",
    ):
        if not isinstance(payload.get(field), str) or _SHA256.fullmatch(
            str(payload.get(field))
        ) is None:
            raise _fail("result_identity_invalid")
    bindings = payload.get("bindings")
    if (
        not isinstance(bindings, Mapping)
        or set(bindings) != _BINDING_FIELDS
        or any(
            not isinstance(digest, str) or _SHA256.fullmatch(digest) is None
            for digest in bindings.values()
        )
    ):
        raise _fail("result_bindings_invalid")
    source = payload.get("source_sha256")
    if (
        not isinstance(source, Mapping)
        or set(source) != {"result_verifier"}
        or not isinstance(source.get("result_verifier"), str)
        or _SHA256.fullmatch(str(source.get("result_verifier"))) is None
    ):
        raise _fail("result_source_invalid")
    smoke = payload.get("smoke_summary")
    observation = payload.get("direct_invariant_observation")
    if (
        not isinstance(smoke, Mapping)
        or smoke.get("status") != "PASS"
        or smoke.get("method") != "A0"
        or smoke.get("episode_count") != 49
        or smoke.get("coverage") != 1.0
        or smoke.get("lost_count") != 0
        or smoke.get("duplicate_count") != 0
        or smoke.get("fallback_count") != 0
        or smoke.get("direct_invariant_violation_count") != 0
        or not isinstance(observation, Mapping)
        or observation.get("status") != "PASS"
        or observation.get("global_violation_total") != 0
        or not isinstance(observation.get("counts"), Mapping)
        or not isinstance(observation.get("observation_sha256"), str)
        or _SHA256.fullmatch(str(observation.get("observation_sha256"))) is None
    ):
        raise _fail("result_scientific_summary_invalid")
    artifact["payload"] = dict(payload)
    return artifact


__all__ = [
    "S5A0FinalizerError",
    "S5A0FinalizerPaths",
    "finalize_s5_a0_result",
    "verify_s5_a0_result",
]
