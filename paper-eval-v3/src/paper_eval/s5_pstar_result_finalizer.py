"""Independent terminal-result verifier for the S5 P*(C=2) smoke.

P* is a deliberately unsafe scientific baseline. A direct invariant violation
or a treatment-induced whole-update failure is therefore a valid terminal
observation, while missing/corrupt telemetry remains non-mergeable. The live
controller cannot declare this result; this module reopens and cross-binds the
complete durable evidence chain before writing one exclusive public artifact.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256, sha256_file
from .s5_a0_result_finalizer import verify_s5_a0_result
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
from .s5_native_method_adapters import (
    P_STAR,
    S5EpisodeRef,
    S5MethodSpec,
    verify_s5_native_method_evidence,
)
from .s5_production_identity_qualification import (
    bind_s5_production_identity_qualification,
    verify_s5_production_identity_qualification,
)
from .s5_production_runner import verify_s5_production_identity
from .s5_pstar_controller import inspect_s5_pstar_controller_attempt
from .s5_pstar_post_observation import verify_s5_pstar_post_observation


SCHEMA = "membind.paper-eval-v3.s5-pstar-scientific-result.v1"
_RESULT_SOURCE = Path(__file__).resolve()
_SHA = re.compile(r"^[0-9a-f]{64}$")
_OUTCOMES = {
    "PASS",
    "DIRECT_INVARIANT_VIOLATION_OBSERVED",
    "TREATMENT_FAILURE_OBSERVED",
}
_AUTHORITY = {
    "scientific_outcome_complete": True,
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
    "scientific_outcome",
    "run_id",
    "execution_identity_sha256",
    "source_manifest_sha256",
    "production_identity_sha256",
    "predecessor",
    "smoke_summary",
    "terminal_accounting",
    "direct_invariant_observation",
    "bindings",
    "source_sha256",
    "authority",
}
_BINDING_PATHS = {
    "production_identity_file_sha256": "production_identity",
    "production_identity_qualification_file_sha256": (
        "production_identity_qualification"
    ),
    "current_stage_pointer_file_sha256": "current_stage_pointer",
    "preflight_file_sha256": "preflight",
    "authority_file_sha256": "authority",
    "predecessor_file_sha256": "predecessor",
    "consumption_file_sha256": "consumption",
    "controller_events_file_sha256": "controller_events",
    "controller_checkpoint_file_sha256": "controller_checkpoint",
    "native_manifest_file_sha256": "native_manifest",
    "native_events_file_sha256": "native_events",
    "native_checkpoint_file_sha256": "native_checkpoint",
    "native_result_file_sha256": "native_result",
    "post_observation_file_sha256": "post_observation",
}


class S5PStarFinalizerError(ValueError):
    """The P* evidence chain is not eligible for a terminal result."""


def _fail(code: str) -> S5PStarFinalizerError:
    return S5PStarFinalizerError(code)


@dataclass(frozen=True)
class S5PStarFinalizerPaths:
    """Immutable evidence inputs and the one exclusive result output."""

    production_identity: Path
    production_identity_qualification: Path
    current_stage_pointer: Path
    preflight: Path
    authority: Path
    predecessor: Path
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


def _exclusive_json(path: Path, value: Mapping[str, object]) -> None:
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    data = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    try:
        descriptor = os.open(
            selected, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664
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


def _pointer(path: Path) -> tuple[dict[str, Any], str]:
    artifact = _load(path, "current_stage_pointer_invalid")
    payload = artifact.get("payload")
    if (
        not isinstance(payload, Mapping)
        or artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or payload.get("schema_version")
        != "membind.paper-eval-v3.current-stage-pointer.v2"
        or payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or payload.get("live_preflight_required") is not True
    ):
        raise _fail("current_stage_pointer_invalid")
    return artifact, _file_sha(path, "current_stage_pointer_invalid")


def _bindings(paths: S5PStarFinalizerPaths) -> dict[str, str]:
    concrete = {
        "production_identity": paths.production_identity,
        "production_identity_qualification": paths.production_identity_qualification,
        "current_stage_pointer": paths.current_stage_pointer,
        "preflight": paths.preflight,
        "authority": paths.authority,
        "predecessor": paths.predecessor,
        "consumption": paths.consumption,
        "controller_events": paths.controller_root / "events.jsonl",
        "controller_checkpoint": paths.controller_root / "checkpoint.json",
        "native_manifest": paths.attempt_root / "manifest.json",
        "native_events": paths.attempt_root / "events.jsonl",
        "native_checkpoint": paths.attempt_root / "checkpoint.json",
        "native_result": paths.attempt_root / "result.json",
        "post_observation": paths.post_observation,
    }
    return {
        output: _file_sha(concrete[source], f"binding_missing:{output}")
        for output, source in _BINDING_PATHS.items()
    }


def _verify_chain(paths: S5PStarFinalizerPaths) -> dict[str, object]:
    if not isinstance(paths, S5PStarFinalizerPaths):
        raise _fail("finalizer_paths_invalid")
    if Path(paths.result).exists():
        raise _fail("result_exists")
    try:
        identity = verify_s5_production_identity(
            _load(paths.production_identity, "production_identity_invalid")
        )
        qualification = verify_s5_production_identity_qualification(
            _load(
                paths.production_identity_qualification,
                "production_identity_qualification_invalid",
            )
        )
        qualification_binding = bind_s5_production_identity_qualification(
            qualification,
            file_sha256=_file_sha(
                paths.production_identity_qualification,
                "production_identity_qualification_invalid",
            ),
        )
    except Exception:
        raise _fail("production_identity_chain_invalid") from None
    identity_file_sha = _file_sha(
        paths.production_identity, "production_identity_invalid"
    )
    if (
        identity.get("method") != P_STAR
        or qualification_binding.get("method") != P_STAR
        or qualification_binding.get("production_identity_sha256")
        != identity.get("identity_sha256")
        or qualification_binding.get("production_identity_file_sha256")
        != identity_file_sha
    ):
        raise _fail("production_identity_binding_mismatch")

    pointer, pointer_file_sha = _pointer(paths.current_stage_pointer)
    if qualification_binding.get("current_stage_pointer") != {
        "file_sha256": pointer_file_sha,
        "payload_sha256": pointer.get("payload_sha256"),
        "run_id": pointer.get("run_id"),
        "current_stage": "S3_CONFIGURATION_FROZEN",
    }:
        raise _fail("qualification_pointer_binding_mismatch")
    try:
        preflight = verify_s5_live_preflight(
            _load(paths.preflight, "preflight_invalid")
        )
        authority = verify_s5_live_authority(
            _load(paths.authority, "authority_invalid")
        )
        consumption = verify_s5_live_authority_consumption(
            _load(paths.consumption, "consumption_invalid")
        )
        predecessor = verify_s5_a0_result(
            _load(paths.predecessor, "predecessor_invalid")
        )
    except Exception:
        raise _fail("authority_chain_invalid") from None
    authority_payload = authority["payload"]
    run = authority_payload.get("run")
    authority_predecessor = authority_payload.get("predecessor")
    if (
        preflight["payload"].get("method") != P_STAR
        or preflight["payload"].get("production_identity_qualification")
        != qualification_binding
        or authority_payload.get("method") != P_STAR
        or authority_payload.get("production_identity_qualification")
        != qualification_binding
        or authority_payload.get("preflight_file_sha256")
        != _file_sha(paths.preflight, "preflight_invalid")
        or authority_payload.get("preflight_payload_sha256")
        != preflight.get("payload_sha256")
        or authority_payload.get("current_stage_pointer_sha256")
        != pointer_file_sha
        or not isinstance(run, Mapping)
        or run.get("method") != P_STAR
        or run.get("configured_concurrency") != 2
        or not isinstance(authority_predecessor, Mapping)
        or authority_predecessor.get("method") != "A0"
        or authority_predecessor.get("verdict") != "PASS"
        or authority_predecessor.get("result_file_sha256")
        != _file_sha(paths.predecessor, "predecessor_invalid")
        or authority_predecessor.get("result_payload_sha256")
        != predecessor.get("payload_sha256")
        or predecessor["payload"].get("verdict") != "PASS"
        or consumption["payload"].get("method") != P_STAR
        or consumption["payload"].get("run") != run
        or consumption["payload"].get("authority_file_sha256")
        != _file_sha(paths.authority, "authority_invalid")
        or consumption["payload"].get("authority_payload_sha256")
        != authority.get("payload_sha256")
        or consumption["payload"].get("production_identity_sha256")
        != identity.get("identity_sha256")
    ):
        raise _fail("authority_chain_binding_mismatch")
    source_binding = authority_payload.get("source_sha256")
    if (
        not isinstance(source_binding, Mapping)
        or source_binding.get("result_verifier")
        != _file_sha(_RESULT_SOURCE, "result_verifier_source_missing")
    ):
        raise _fail("result_verifier_source_binding_mismatch")

    try:
        controller = inspect_s5_pstar_controller_attempt(paths.controller_root)
        attempt = inspect_s5_attempt(paths.attempt_root)
    except Exception:
        raise _fail("execution_evidence_invalid") from None
    controller_events = controller["events"]
    if (
        controller["checkpoint"].get("run_id") != run.get("run_id")
        or controller["checkpoint"].get("status")
        != "controller_complete_evidence_only"
        or [event.get("event_type") for event in controller_events]
        != [
            "authority_consumed",
            "runtime_constructed",
            "runtime_ready",
            "native_runner_started",
            "runtime_closed",
            "raw_runner_evidence_complete",
        ]
    ):
        raise _fail("controller_attempt_incomplete")

    manifest = attempt["manifest"]
    native_result = attempt.get("result")
    native_payload = (
        native_result.get("payload") if isinstance(native_result, Mapping) else None
    )
    source_hashes = manifest.get("source_sha256s")
    if (
        manifest.get("run_id") != run.get("run_id")
        or manifest.get("method") != P_STAR
        or manifest.get("production_core_identity_sha256")
        != identity.get("identity_sha256")
        or not isinstance(source_hashes, list)
        or len(source_hashes) != 49
        or not isinstance(native_result, Mapping)
        or native_result.get("status")
        not in {"complete", "scientific_outcome_complete"}
        or not isinstance(native_payload, Mapping)
        or native_payload.get("status")
        not in {"PASS", "SCIENTIFIC_OUTCOME_COMPLETE"}
        or native_payload.get("production_core_identity_sha256")
        != identity.get("identity_sha256")
    ):
        raise _fail("native_attempt_incomplete")
    source_manifest = payload_sha256(
        [
            {"source_sequence": index, "source_sha256": digest}
            for index, digest in enumerate(source_hashes)
        ]
    )
    if source_manifest != run.get("source_manifest_sha256"):
        raise _fail("native_source_manifest_mismatch")
    spec = S5MethodSpec(
        run_id=str(run["run_id"]),
        method=P_STAR,
        native_path_identity_sha256=str(identity["graphiti_native_source_sha256"]),
    )
    episodes = tuple(
        S5EpisodeRef(index, str(digest), object())
        for index, digest in enumerate(source_hashes)
    )
    try:
        verified_native = verify_s5_native_method_evidence(
            {
                key: value
                for key, value in native_payload.items()
                if key != "production_core_identity_sha256"
            },
            expected_spec=spec,
            expected_episodes=episodes,
        )
        post = verify_s5_pstar_post_observation(
            _load(paths.post_observation, "post_observation_invalid")
        )
    except Exception:
        raise _fail("scientific_evidence_invalid") from None

    expected_sources = [
        {"source_sequence": index, "source_sha256": digest}
        for index, digest in enumerate(source_hashes)
    ]
    terminal_projection = [
        {
            "source_sequence": event["source_sequence"],
            "source_sha256": event["source_sha256"],
            "terminal_classification": event["terminal_classification"],
        }
        for event in verified_native["events"]
        if event.get("event_type") == "source_terminal"
    ]
    terminal_projection.sort(key=lambda row: int(row["source_sequence"]))
    published_sequences = sorted(
        int(event["source_sequence"])
        for event in verified_native["events"]
        if event.get("event_type") == "publication"
    )
    if (
        post.get("run_id_sha256") != payload_sha256(str(run["run_id"]))
        or post.get("source_manifest_sha256") != payload_sha256(expected_sources)
        or post.get("source_classifications") != terminal_projection
        or post.get("published_source_sequences") != published_sequences
    ):
        raise _fail("post_observation_binding_mismatch")

    native_status = verified_native["status"]
    outcome = str(post["status"])
    accounting = deepcopy(dict(post["accounting"]))
    if native_status == "PASS":
        if outcome not in {"PASS", "DIRECT_INVARIANT_VIOLATION_OBSERVED"} or accounting != {
            "expected": 49,
            "published": 49,
            "failed": 0,
            "censored": 0,
        }:
            raise _fail("full_publication_observation_mismatch")
        per_source = {
            int(source): int(count)
            for source, count in post["per_source_violation_counts"].items()
        }
        try:
            smoke = validate_smoke_records(
                P_STAR,
                expected_source_sequences=list(range(49)),
                records=native_method_to_smoke_records(
                    verified_native,
                    direct_invariant_violations=per_source,
                ),
            )
        except Exception:
            raise _fail("native_smoke_contract_invalid") from None
    else:
        if (
            outcome != "TREATMENT_FAILURE_OBSERVED"
            or accounting.get("failed") != 1
            or sum(
                int(accounting[name])
                for name in ("published", "failed", "censored")
            )
            != 49
        ):
            raise _fail("treatment_failure_observation_mismatch")
        smoke = None

    return {
        "run": dict(run),
        "identity": identity,
        "source_manifest_sha256": source_manifest,
        "execution_identity_sha256": payload_sha256(
            {"run_id": run.get("run_id"), "namespace": run.get("namespace")}
        ),
        "predecessor": deepcopy(dict(authority_predecessor)),
        "post": post,
        "smoke_summary": smoke,
        "accounting": accounting,
        "bindings": _bindings(paths),
    }


def finalize_s5_pstar_result(
    *, paths: S5PStarFinalizerPaths, git_commit: str
) -> dict[str, object]:
    """Exclusively persist one independently verified P* scientific outcome."""

    if not isinstance(git_commit, str) or not git_commit:
        raise _fail("git_commit_invalid")
    verified = _verify_chain(paths)
    post = verified["post"]
    run = verified["run"]
    payload = {
        "schema_version": SCHEMA,
        "stage": "S5_PSTAR_METHOD_SMOKE",
        "method": P_STAR,
        "verdict": "SCIENTIFIC_OUTCOME_COMPLETE",
        "scientific_outcome": post["status"],
        "run_id": run["run_id"],
        "execution_identity_sha256": verified["execution_identity_sha256"],
        "source_manifest_sha256": verified["source_manifest_sha256"],
        "production_identity_sha256": verified["identity"]["identity_sha256"],
        "predecessor": verified["predecessor"],
        "smoke_summary": verified["smoke_summary"],
        "terminal_accounting": verified["accounting"],
        "direct_invariant_observation": {
            "status": post["status"],
            "global_violation_total": post["global_violation_total"],
            "violation_counts": post["violation_counts"],
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
    artifact = verify_s5_pstar_result(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=git_commit,
            run_id=f"{run['run_id']}-result",
        )
    )
    _exclusive_json(paths.result, artifact)
    return artifact


def verify_s5_pstar_result(value: Mapping[str, object]) -> dict[str, object]:
    """Verify a sealed P* result without reopening live or private state."""

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
        or payload.get("stage") != "S5_PSTAR_METHOD_SMOKE"
        or payload.get("method") != P_STAR
        or payload.get("verdict") != "SCIENTIFIC_OUTCOME_COMPLETE"
        or payload.get("scientific_outcome") not in _OUTCOMES
        or payload.get("authority") != _AUTHORITY
    ):
        raise _fail("result_invalid")
    for field in (
        "execution_identity_sha256",
        "source_manifest_sha256",
        "production_identity_sha256",
    ):
        if not isinstance(payload.get(field), str) or _SHA.fullmatch(
            str(payload[field])
        ) is None:
            raise _fail("result_identity_invalid")
    if not isinstance(payload.get("run_id"), str):
        raise _fail("result_identity_invalid")
    bindings = payload.get("bindings")
    if (
        not isinstance(bindings, Mapping)
        or set(bindings) != set(_BINDING_PATHS)
        or any(
            not isinstance(digest, str) or _SHA.fullmatch(digest) is None
            for digest in bindings.values()
        )
    ):
        raise _fail("result_bindings_invalid")
    predecessor = payload.get("predecessor")
    if (
        not isinstance(predecessor, Mapping)
        or predecessor.get("method") != "A0"
        or predecessor.get("verdict") != "PASS"
        or set(predecessor)
        != {
            "method",
            "verdict",
            "result_file_sha256",
            "result_payload_sha256",
        }
        or any(
            not isinstance(predecessor.get(field), str)
            or _SHA.fullmatch(str(predecessor[field])) is None
            for field in ("result_file_sha256", "result_payload_sha256")
        )
    ):
        raise _fail("result_predecessor_invalid")
    source = payload.get("source_sha256")
    if (
        not isinstance(source, Mapping)
        or set(source) != {"result_verifier"}
        or not isinstance(source.get("result_verifier"), str)
        or _SHA.fullmatch(str(source["result_verifier"])) is None
    ):
        raise _fail("result_source_invalid")

    accounting = payload.get("terminal_accounting")
    observation = payload.get("direct_invariant_observation")
    if (
        not isinstance(accounting, Mapping)
        or set(accounting) != {"expected", "published", "failed", "censored"}
        or any(
            isinstance(accounting.get(name), bool)
            or not isinstance(accounting.get(name), int)
            or int(accounting[name]) < 0
            for name in accounting
        )
        or accounting.get("expected") != 49
        or sum(int(accounting[name]) for name in ("published", "failed", "censored"))
        != 49
        or not isinstance(observation, Mapping)
        or observation.get("status") != payload.get("scientific_outcome")
        or isinstance(observation.get("global_violation_total"), bool)
        or not isinstance(observation.get("global_violation_total"), int)
        or int(observation["global_violation_total"]) < 0
        or not isinstance(observation.get("violation_counts"), Mapping)
        or not isinstance(observation.get("observation_sha256"), str)
        or _SHA.fullmatch(str(observation["observation_sha256"])) is None
    ):
        raise _fail("result_scientific_summary_invalid")
    outcome = payload["scientific_outcome"]
    smoke = payload.get("smoke_summary")
    if outcome == "TREATMENT_FAILURE_OBSERVED":
        if accounting.get("failed") != 1 or smoke is not None:
            raise _fail("result_scientific_summary_invalid")
    else:
        if (
            accounting
            != {"expected": 49, "published": 49, "failed": 0, "censored": 0}
            or not isinstance(smoke, Mapping)
            or smoke.get("status") != "PASS"
            or smoke.get("method") != P_STAR
            or smoke.get("episode_count") != 49
            or smoke.get("coverage") != 1.0
            or smoke.get("lost_count") != 0
            or smoke.get("duplicate_count") != 0
            or smoke.get("fallback_count") != 0
            or smoke.get("whole_update_overlap_observed") is not True
        ):
            raise _fail("result_scientific_summary_invalid")
        if (outcome == "PASS") != (
            observation.get("global_violation_total") == 0
        ):
            raise _fail("result_scientific_summary_invalid")
    artifact["payload"] = dict(payload)
    return artifact


__all__ = [
    "S5PStarFinalizerError",
    "S5PStarFinalizerPaths",
    "finalize_s5_pstar_result",
    "verify_s5_pstar_result",
]
