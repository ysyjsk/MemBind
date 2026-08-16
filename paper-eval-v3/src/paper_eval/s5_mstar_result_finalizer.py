"""Exclusive, standalone-verifiable result format for the S5 M* smoke.

The live-chain verifier is intentionally a separate caller: this module accepts
only its fixed, hash-bound projection and cannot query services or consume live
authority.  A valid direct-invariant counterexample is retained as a complete
scientific outcome, but only a zero-violation block receives PASS authority.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256, sha256_file


SCHEMA = "membind.paper-eval-v3.s5-mstar-scientific-result.v1"
_RESULT_SOURCE = Path(__file__).resolve()
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OUTCOMES = {"PASS", "DIRECT_INVARIANT_VIOLATION_OBSERVED"}
_BINDINGS = {
    "production_identity_file_sha256",
    "production_core_identity_file_sha256",
    "fx0_qualification_file_sha256",
    "production_identity_qualification_file_sha256",
    "current_stage_pointer_file_sha256",
    "preflight_file_sha256",
    "authority_file_sha256",
    "predecessor_file_sha256",
    "consumption_file_sha256",
    "controller_events_file_sha256",
    "controller_checkpoint_file_sha256",
    "attempt_manifest_file_sha256",
    "attempt_events_file_sha256",
    "attempt_checkpoint_file_sha256",
    "attempt_result_file_sha256",
    "publication_journal_file_sha256",
    "post_observation_file_sha256",
}
_PROJECTION_FIELDS = {
    "run_id",
    "execution_identity_sha256",
    "source_manifest_sha256",
    "production_identity_sha256",
    "production_core_identity_sha256",
    "predecessor",
    "smoke_summary",
    "post_observation",
    "publication_journal",
    "bindings",
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
    "production_core_identity_sha256",
    "predecessor",
    "smoke_summary",
    "direct_invariant_observation",
    "publication_journal",
    "bindings",
    "source_sha256",
    "authority",
}


class S5MStarFinalizerError(ValueError):
    """The M* result projection is incomplete, contradictory, or already used."""


def _fail(code: str) -> S5MStarFinalizerError:
    return S5MStarFinalizerError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _mapping(value: object, code: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    return deepcopy(dict(value))


def _authority(outcome: str) -> dict[str, bool]:
    return {
        "scientific_outcome_complete": True,
        "scientific_pass_authorized": outcome == "PASS",
        "next_method_authorized": False,
        "current_stage_pointer_update_authorized": False,
        "pilot_execution_authorized": False,
        "formal_execution_authorized": False,
        "resume_authorized": False,
        "namespace_cleanup_authorized": False,
    }


def _verified_projection(value: Mapping[str, object]) -> dict[str, object]:
    projection = _mapping(value, "projection_invalid")
    if set(projection) != _PROJECTION_FIELDS:
        raise _fail("projection_invalid")
    run_id = projection.get("run_id")
    if (
        not isinstance(run_id, str)
        or re.fullmatch(r"s5-mstar-[0-9]{8}-[0-9]{3}", run_id) is None
    ):
        raise _fail("projection_run_invalid")
    for field in (
        "execution_identity_sha256",
        "source_manifest_sha256",
        "production_identity_sha256",
        "production_core_identity_sha256",
    ):
        _sha(projection.get(field), "projection_identity_invalid")

    predecessor = _mapping(projection.get("predecessor"), "predecessor_invalid")
    if (
        set(predecessor)
        != {
            "method",
            "verdict",
            "result_file_sha256",
            "result_payload_sha256",
        }
        or predecessor.get("method") != "P*"
        or predecessor.get("verdict") != "SCIENTIFIC_OUTCOME_COMPLETE"
    ):
        raise _fail("predecessor_invalid")
    _sha(predecessor.get("result_file_sha256"), "predecessor_invalid")
    _sha(predecessor.get("result_payload_sha256"), "predecessor_invalid")

    post = _mapping(projection.get("post_observation"), "post_observation_invalid")
    if set(post) != {
        "status",
        "global_violation_total",
        "native_observation_sha256",
        "post_observation_sha256",
    } or post.get("status") not in _OUTCOMES:
        raise _fail("post_observation_invalid")
    violations = post.get("global_violation_total")
    if isinstance(violations, bool) or not isinstance(violations, int) or violations < 0:
        raise _fail("post_observation_invalid")
    _sha(post.get("native_observation_sha256"), "post_observation_invalid")
    _sha(post.get("post_observation_sha256"), "post_observation_invalid")
    if (post["status"] == "PASS") != (violations == 0):
        raise _fail("post_observation_status_invalid")

    journal = _mapping(projection.get("publication_journal"), "journal_invalid")
    if set(journal) != {
        "intent_count",
        "commit_count",
        "publication_count",
        "recovered_publication_count",
        "events_sha256",
    }:
        raise _fail("journal_invalid")
    for field in (
        "intent_count",
        "commit_count",
        "publication_count",
        "recovered_publication_count",
    ):
        count = journal.get(field)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise _fail("journal_invalid")
    if (
        journal.get("intent_count") != 49
        or journal.get("commit_count") != 49
        or journal.get("publication_count") != 49
        or int(journal["recovered_publication_count"]) > 49
    ):
        raise _fail("journal_incomplete")
    _sha(journal.get("events_sha256"), "journal_invalid")

    smoke = _mapping(projection.get("smoke_summary"), "smoke_summary_invalid")
    if (
        smoke.get("status") != "PASS"
        or smoke.get("method") != "M*"
        or smoke.get("episode_count") != 49
        or smoke.get("coverage") != 1.0
        or smoke.get("lost_count") != 0
        or smoke.get("duplicate_count") != 0
        or smoke.get("publication_order") != list(range(49))
        or smoke.get("fallback_count") != 0
        or smoke.get("direct_invariant_violation_count") != violations
        or smoke.get("whole_update_overlap_observed") is not None
        or smoke.get("scientific_outcome_not_adapter_failure") is not False
        or not isinstance(smoke.get("post_return_stale_window_ns"), list)
        or len(smoke["post_return_stale_window_ns"]) != 49
    ):
        raise _fail("smoke_summary_invalid")
    bindings = _mapping(projection.get("bindings"), "bindings_invalid")
    if set(bindings) != _BINDINGS:
        raise _fail("bindings_invalid")
    for digest in bindings.values():
        _sha(digest, "bindings_invalid")
    projection.update(
        predecessor=predecessor,
        post_observation=post,
        publication_journal=journal,
        smoke_summary=smoke,
        bindings=bindings,
    )
    return projection


def build_s5_mstar_result(
    *, projection: Mapping[str, object], git_commit: str
) -> dict[str, object]:
    """Build and verify a public result from a previously verified live chain."""

    if not isinstance(git_commit, str) or not git_commit:
        raise _fail("git_commit_invalid")
    selected = _verified_projection(projection)
    post = selected["post_observation"]
    outcome = str(post["status"])
    payload = {
        "schema_version": SCHEMA,
        "stage": "S5_MSTAR_METHOD_SMOKE",
        "method": "M*",
        "verdict": "PASS" if outcome == "PASS" else "SCIENTIFIC_OUTCOME_COMPLETE",
        "scientific_outcome": outcome,
        "run_id": selected["run_id"],
        "execution_identity_sha256": selected["execution_identity_sha256"],
        "source_manifest_sha256": selected["source_manifest_sha256"],
        "production_identity_sha256": selected["production_identity_sha256"],
        "production_core_identity_sha256": selected[
            "production_core_identity_sha256"
        ],
        "predecessor": selected["predecessor"],
        "smoke_summary": selected["smoke_summary"],
        "direct_invariant_observation": post,
        "publication_journal": selected["publication_journal"],
        "bindings": selected["bindings"],
        "source_sha256": {"result_verifier": sha256_file(_RESULT_SOURCE)},
        "authority": _authority(outcome),
    }
    return verify_s5_mstar_result(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=git_commit,
            run_id=f"{selected['run_id']}-result",
        )
    )


def _write_exclusive(path: Path, value: Mapping[str, object]) -> None:
    selected = Path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(
        "ascii"
    )
    try:
        descriptor = os.open(selected, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except FileExistsError:
        raise _fail("result_exists") from None
    try:
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(selected.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def finalize_s5_mstar_result(
    *, output_path: Path, projection: Mapping[str, object], git_commit: str
) -> dict[str, object]:
    """Exclusively persist one verified M* terminal result."""

    if Path(output_path).exists():
        raise _fail("result_exists")
    artifact = build_s5_mstar_result(projection=projection, git_commit=git_commit)
    _write_exclusive(output_path, artifact)
    return artifact


def verify_s5_mstar_result(value: Mapping[str, object]) -> dict[str, object]:
    """Verify a sealed M* result without reopening live or private state."""

    artifact = _mapping(value, "result_invalid")
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
        or payload.get("stage") != "S5_MSTAR_METHOD_SMOKE"
        or payload.get("method") != "M*"
        or payload.get("scientific_outcome") not in _OUTCOMES
    ):
        raise _fail("result_invalid")
    projection = {
        "run_id": payload["run_id"],
        "execution_identity_sha256": payload["execution_identity_sha256"],
        "source_manifest_sha256": payload["source_manifest_sha256"],
        "production_identity_sha256": payload["production_identity_sha256"],
        "production_core_identity_sha256": payload[
            "production_core_identity_sha256"
        ],
        "predecessor": payload["predecessor"],
        "smoke_summary": payload["smoke_summary"],
        "post_observation": payload["direct_invariant_observation"],
        "publication_journal": payload["publication_journal"],
        "bindings": payload["bindings"],
    }
    checked = _verified_projection(projection)
    outcome = str(checked["post_observation"]["status"])
    expected_verdict = "PASS" if outcome == "PASS" else "SCIENTIFIC_OUTCOME_COMPLETE"
    if (
        payload.get("verdict") != expected_verdict
        or payload.get("authority") != _authority(outcome)
        or not isinstance(payload.get("source_sha256"), Mapping)
        or set(payload["source_sha256"]) != {"result_verifier"}
    ):
        raise _fail("result_summary_invalid")
    _sha(payload["source_sha256"].get("result_verifier"), "result_source_invalid")
    artifact["payload"] = dict(payload)
    return artifact


__all__ = [
    "S5MStarFinalizerError",
    "build_s5_mstar_result",
    "finalize_s5_mstar_result",
    "verify_s5_mstar_result",
]

