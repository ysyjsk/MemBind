"""Activate the sealed fixed-four plan after a strict sidecar smoke PASS."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256, sha256_file
from .s4_qualification_plan import verify_s4_qualification_plan
from .s4_sidecar_smoke_result_verifier import verify_s4_sidecar_smoke_result


SCHEMA = "membind.paper-eval-v3.s4-sidecar-qualification-activation.v3"
RUN_ID = "s4-sidecar-qualification-activation-20260815-003"
HISTORY_IDS = ["07741c45", "b6019101", "6071bd76", "a2f3aa27"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_NAMES = {
    "activation",
    "finalizer",
    "smoke_result_verifier",
    "test",
}


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is not a SHA256")
    return value


def _reject_private(value: object) -> None:
    forbidden = {
        "answer",
        "api_key",
        "content",
        "fact",
        "messages",
        "password",
        "prompt",
        "prompt_parts",
        "question",
        "raw_output",
        "raw_response",
        "secret",
        "uuid",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in forbidden:
                raise ValueError("S4 sidecar activation contains private data")
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def _artifact_evidence(
    value: Mapping[str, Any],
    *,
    file_sha256: str,
    expected_run_id: str,
    label: str,
) -> dict[str, str]:
    artifact = _mapping(value, label=label)
    if artifact.get("run_id") != expected_run_id:
        raise ValueError(f"{label} run identity drift")
    return {
        "file_sha256": _sha(file_sha256, field=f"{label} file"),
        "payload_sha256": _sha(
            artifact.get("payload_sha256"), field=f"{label} payload"
        ),
        "run_id": expected_run_id,
    }


def _phase_evidence(
    value: Mapping[str, Any],
    *,
    file_sha256: str,
    expected_run_id: str,
    label: str,
) -> dict[str, str]:
    evidence = _artifact_evidence(
        value,
        file_sha256=file_sha256,
        expected_run_id=expected_run_id,
        label=label,
    )
    artifact = _mapping(value, label=label)
    payload = _mapping(artifact.get("payload"), label=f"{label} payload")
    caches = _mapping(
        payload.get("cache_evidence"), label=f"{label} cache evidence"
    )
    if set(caches) != {
        "prompt_cache_sha256",
        "embedding_cache_sha256",
        "candidate_sidecar_sha256",
    }:
        raise ValueError(f"{label} cache evidence shape drift")
    evidence.update(
        {
            "prompt_cache_sha256": _sha(
                caches.get("prompt_cache_sha256"),
                field=f"{label} prompt cache",
            ),
            "embedding_cache_sha256": _sha(
                caches.get("embedding_cache_sha256"),
                field=f"{label} embedding cache",
            ),
            "candidate_sidecar_sha256": _sha(
                caches.get("candidate_sidecar_sha256"),
                field=f"{label} candidate sidecar",
            ),
            "checkpoint_sha256": _sha(
                payload.get("checkpoint_sha256"),
                field=f"{label} checkpoint",
            ),
            "events_sha256": _sha(
                payload.get("events_sha256"), field=f"{label} events"
            ),
        }
    )
    return evidence


def _verify_artifact_evidence(
    value: object,
    *,
    expected_run_id: str,
    label: str,
) -> dict[str, str]:
    evidence = _mapping(value, label=label)
    if (
        set(evidence) != {"file_sha256", "payload_sha256", "run_id"}
        or evidence.get("run_id") != expected_run_id
    ):
        raise ValueError(f"{label} identity drift")
    _sha(evidence.get("file_sha256"), field=f"{label} file")
    _sha(evidence.get("payload_sha256"), field=f"{label} payload")
    return evidence


def _verify_phase_evidence(
    value: object,
    *,
    expected_run_id: str,
    candidate_sidecar_sha256: str,
    label: str,
) -> dict[str, str]:
    evidence = _mapping(value, label=label)
    if (
        set(evidence)
        != {
            "file_sha256",
            "payload_sha256",
            "run_id",
            "prompt_cache_sha256",
            "embedding_cache_sha256",
            "candidate_sidecar_sha256",
            "checkpoint_sha256",
            "events_sha256",
        }
        or evidence.get("run_id") != expected_run_id
        or evidence.get("candidate_sidecar_sha256")
        != candidate_sidecar_sha256
    ):
        raise ValueError(f"{label} identity or sidecar drift")
    for name, digest in evidence.items():
        if name != "run_id":
            _sha(digest, field=f"{label} {name}")
    return evidence


def build_s4_sidecar_qualification_activation(
    *,
    qualification_plan: Mapping[str, Any],
    qualification_plan_file_sha256: str,
    smoke_result: Mapping[str, Any],
    smoke_result_file_sha256: str,
    authority: Mapping[str, Any],
    authority_file_sha256: str,
    consumption: Mapping[str, Any],
    consumption_file_sha256: str,
    capture_result: Mapping[str, Any],
    capture_result_file_sha256: str,
    replay_result: Mapping[str, Any],
    replay_result_file_sha256: str,
    candidate_sidecar_file_sha256: str,
    source_sha256: Mapping[str, str],
    git_commit: str,
) -> dict[str, Any]:
    """Reverify retry-008 and activate only the remaining three blocks."""

    plan = verify_s4_qualification_plan(qualification_plan)
    verified_smoke = verify_s4_sidecar_smoke_result(
        result=smoke_result,
        authority=authority,
        authority_file_sha256=authority_file_sha256,
        consumption=consumption,
        consumption_file_sha256=consumption_file_sha256,
        capture_result=capture_result,
        capture_result_file_sha256=capture_result_file_sha256,
        replay_result=replay_result,
        replay_result_file_sha256=replay_result_file_sha256,
        candidate_sidecar_file_sha256=candidate_sidecar_file_sha256,
        expected_attempt="008",
    )
    smoke_payload = _mapping(
        verified_smoke.get("payload"), label="verified sidecar smoke payload"
    )
    if (
        verified_smoke.get("run_id")
        != "s4-d0-sidecar-smoke-result-20260815-008"
        or smoke_payload.get("schema_version")
        != "membind.paper-eval-v3.s4-d0-sidecar-smoke-result.v3"
        or smoke_payload.get("verdict") != "PASS"
        or smoke_payload.get("authority")
        != {
            "s4_four_history_qualification_authorized": True,
            "s5_authorized": False,
            "pilot_execution_authorized": False,
        }
    ):
        raise ValueError("verified S4 sidecar smoke identity or authority drift")
    if (
        plan.get("history_ids") != HISTORY_IDS
        or plan["blocks"][0].get("history_id") != HISTORY_IDS[0]
        or [block.get("history_id") for block in plan["blocks"][1:]]
        != HISTORY_IDS[1:]
        or plan.get("authority")
        != {
            "qualification_live_authorized": False,
            "s5_authorized": False,
            "pilot_execution_authorized": False,
        }
    ):
        raise ValueError("sealed S4 qualification plan projection drift")
    sources = _mapping(source_sha256, label="sidecar activation sources")
    if set(sources) != _SOURCE_NAMES:
        raise ValueError("S4 sidecar activation source inventory drift")

    authority_evidence = _artifact_evidence(
        authority,
        file_sha256=authority_file_sha256,
        expected_run_id="s4-sidecar-smoke-authority-20260815-008",
        label="S4 sidecar authority",
    )
    consumption_evidence = _artifact_evidence(
        consumption,
        file_sha256=consumption_file_sha256,
        expected_run_id="s4-sidecar-authority-consumption-20260815-008",
        label="S4 sidecar authority consumption",
    )
    sidecar_sha = _sha(
        candidate_sidecar_file_sha256, field="candidate sidecar file"
    )
    capture_evidence = _phase_evidence(
        capture_result,
        file_sha256=capture_result_file_sha256,
        expected_run_id="s4-d0-capture-20260815-008",
        label="S4 sidecar capture phase",
    )
    replay_evidence = _phase_evidence(
        replay_result,
        file_sha256=replay_result_file_sha256,
        expected_run_id="s4-d0-replay-20260815-008",
        label="S4 sidecar replay phase",
    )
    if (
        capture_evidence["candidate_sidecar_sha256"] != sidecar_sha
        or replay_evidence["candidate_sidecar_sha256"] != sidecar_sha
    ):
        raise ValueError("S4 sidecar activation sidecar evidence drift")

    payload = {
        "schema_version": SCHEMA,
        "stage": "S4_QUALIFICATION_ACTIVATION",
        "status": "ACTIVATED_BY_VERIFIED_SIDECAR_SMOKE_PASS",
        "qualification_plan": {
            "file_sha256": _sha(
                qualification_plan_file_sha256, field="qualification plan file"
            ),
            "plan_sha256": _sha(plan.get("plan_sha256"), field="plan payload"),
            "common_method_policy_sha256": _sha(
                plan.get("common_method_policy_sha256"), field="common policy"
            ),
        },
        "verified_smoke": {
            "kind": "S4_D0_BILATERAL_SIDECAR_SMOKE_V3",
            "file_sha256": _sha(smoke_result_file_sha256, field="smoke file"),
            "payload_sha256": _sha(
                verified_smoke.get("payload_sha256"), field="smoke payload"
            ),
            "run_id": verified_smoke["run_id"],
            "verdict": "PASS",
            "history_id": HISTORY_IDS[0],
            "evidence": {
                "authority": authority_evidence,
                "consumption": consumption_evidence,
                "candidate_sidecar_file_sha256": sidecar_sha,
                "phases": {
                    "U0_CAPTURE": capture_evidence,
                    "D0_READ_ONLY_REPLAY": replay_evidence,
                },
            },
        },
        "activated_projection": {
            "reused_smoke_history_id": HISTORY_IDS[0],
            "live_history_ids": HISTORY_IDS[1:],
            "live_blocks_sha256": payload_sha256(plan["blocks"][1:]),
            "sequential_blocks": True,
            "next_block_requires_prior_pass": True,
        },
        "authority": {
            "qualification_live_authorized": True,
            "s5_authorized": False,
            "pilot_execution_authorized": False,
        },
        "source_sha256": {
            name: _sha(digest, field=f"source {name}")
            for name, digest in sorted(sources.items())
        },
    }
    return verify_s4_sidecar_qualification_activation(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id=RUN_ID,
        )
    )


def verify_s4_sidecar_qualification_activation(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = _mapping(value, label="S4 sidecar qualification activation")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise ValueError("S4 sidecar activation envelope shape drift")
    payload = _mapping(artifact.get("payload"), label="sidecar activation payload")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("run_id") != RUN_ID
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or set(payload)
        != {
            "schema_version",
            "stage",
            "status",
            "qualification_plan",
            "verified_smoke",
            "activated_projection",
            "authority",
            "source_sha256",
        }
        or payload.get("schema_version") != SCHEMA
        or payload.get("stage") != "S4_QUALIFICATION_ACTIVATION"
        or payload.get("status")
        != "ACTIVATED_BY_VERIFIED_SIDECAR_SMOKE_PASS"
    ):
        raise ValueError("S4 sidecar activation envelope or payload drift")

    plan = _mapping(payload.get("qualification_plan"), label="activated plan")
    if set(plan) != {
        "file_sha256",
        "plan_sha256",
        "common_method_policy_sha256",
    }:
        raise ValueError("S4 sidecar activated plan shape drift")
    for name, digest in plan.items():
        _sha(digest, field=f"activated plan {name}")

    smoke = _mapping(payload.get("verified_smoke"), label="activated smoke")
    if (
        set(smoke)
        != {
            "kind",
            "file_sha256",
            "payload_sha256",
            "run_id",
            "verdict",
            "history_id",
            "evidence",
        }
        or smoke.get("kind") != "S4_D0_BILATERAL_SIDECAR_SMOKE_V3"
        or smoke.get("run_id") != "s4-d0-sidecar-smoke-result-20260815-008"
        or smoke.get("verdict") != "PASS"
        or smoke.get("history_id") != HISTORY_IDS[0]
    ):
        raise ValueError("S4 sidecar activated smoke drift")
    _sha(smoke.get("file_sha256"), field="activated smoke file")
    _sha(smoke.get("payload_sha256"), field="activated smoke payload")
    evidence = _mapping(smoke.get("evidence"), label="activated smoke evidence")
    if set(evidence) != {
        "authority",
        "consumption",
        "candidate_sidecar_file_sha256",
        "phases",
    }:
        raise ValueError("S4 sidecar activated evidence shape drift")
    _verify_artifact_evidence(
        evidence.get("authority"),
        expected_run_id="s4-sidecar-smoke-authority-20260815-008",
        label="activated authority",
    )
    _verify_artifact_evidence(
        evidence.get("consumption"),
        expected_run_id="s4-sidecar-authority-consumption-20260815-008",
        label="activated consumption",
    )
    sidecar_sha = _sha(
        evidence.get("candidate_sidecar_file_sha256"),
        field="activated candidate sidecar file",
    )
    phases = _mapping(evidence.get("phases"), label="activated smoke phases")
    if set(phases) != {"U0_CAPTURE", "D0_READ_ONLY_REPLAY"}:
        raise ValueError("S4 sidecar activated phase inventory drift")
    capture_evidence = _verify_phase_evidence(
        phases.get("U0_CAPTURE"),
        expected_run_id="s4-d0-capture-20260815-008",
        candidate_sidecar_sha256=sidecar_sha,
        label="activated capture phase",
    )
    replay_evidence = _verify_phase_evidence(
        phases.get("D0_READ_ONLY_REPLAY"),
        expected_run_id="s4-d0-replay-20260815-008",
        candidate_sidecar_sha256=sidecar_sha,
        label="activated replay phase",
    )
    for cache_name in ("prompt_cache_sha256", "embedding_cache_sha256"):
        if capture_evidence[cache_name] != replay_evidence[cache_name]:
            raise ValueError("S4 sidecar activated cache parity drift")

    projection = _mapping(
        payload.get("activated_projection"), label="activated projection"
    )
    if (
        projection
        != {
            "reused_smoke_history_id": HISTORY_IDS[0],
            "live_history_ids": HISTORY_IDS[1:],
            "live_blocks_sha256": projection.get("live_blocks_sha256"),
            "sequential_blocks": True,
            "next_block_requires_prior_pass": True,
        }
    ):
        raise ValueError("S4 sidecar activated projection drift")
    _sha(projection.get("live_blocks_sha256"), field="activated live blocks")
    if payload.get("authority") != {
        "qualification_live_authorized": True,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }:
        raise ValueError("S4 sidecar activation authority widening")
    sources = _mapping(payload.get("source_sha256"), label="activation sources")
    if set(sources) != _SOURCE_NAMES:
        raise ValueError("S4 sidecar activation source inventory drift")
    for name, digest in sources.items():
        _sha(digest, field=f"activation source {name}")
    _reject_private(payload)
    artifact["payload"] = payload
    return artifact


def _load_json_mapping(path: Path, *, label: str) -> dict[str, Any]:
    target = Path(path)
    try:
        decoded = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot reopen {label}") from exc
    return _mapping(decoded, label=label)


def verify_s4_sidecar_qualification_activation_external(
    *,
    value: Mapping[str, Any],
    qualification_plan_path: Path,
    smoke_result_path: Path,
    authority_path: Path,
    consumption_path: Path,
    capture_result_path: Path,
    replay_result_path: Path,
    candidate_sidecar_path: Path,
    prompt_cache_path: Path,
    embedding_cache_path: Path,
    capture_checkpoint_path: Path,
    capture_events_path: Path,
    replay_checkpoint_path: Path,
    replay_events_path: Path,
    source_paths: Mapping[str, Path],
) -> dict[str, Any]:
    """Reopen and recompute every external binding in an activation."""

    artifact = verify_s4_sidecar_qualification_activation(value)
    selected_sources = _mapping(source_paths, label="activation source paths")
    if set(selected_sources) != _SOURCE_NAMES:
        raise ValueError("S4 sidecar activation source path inventory drift")
    sources = {
        name: sha256_file(Path(selected_sources[name]))
        for name in sorted(selected_sources)
    }
    plan_path = Path(qualification_plan_path)
    smoke_path = Path(smoke_result_path)
    selected_authority_path = Path(authority_path)
    selected_consumption_path = Path(consumption_path)
    capture_path = Path(capture_result_path)
    replay_path = Path(replay_result_path)
    sidecar_path = Path(candidate_sidecar_path)
    prompt_path = Path(prompt_cache_path)
    embedding_path = Path(embedding_cache_path)
    selected_capture_checkpoint_path = Path(capture_checkpoint_path)
    selected_capture_events_path = Path(capture_events_path)
    selected_replay_checkpoint_path = Path(replay_checkpoint_path)
    selected_replay_events_path = Path(replay_events_path)
    sidecar_sha = sha256_file(sidecar_path)
    prompt_sha = sha256_file(prompt_path)
    embedding_sha = sha256_file(embedding_path)
    smoke_evidence = artifact["payload"]["verified_smoke"]["evidence"]
    phase_evidence = smoke_evidence["phases"]
    capture_evidence = phase_evidence["U0_CAPTURE"]
    replay_evidence = phase_evidence["D0_READ_ONLY_REPLAY"]
    if (
        smoke_evidence["candidate_sidecar_file_sha256"] != sidecar_sha
        or capture_evidence["candidate_sidecar_sha256"] != sidecar_sha
        or replay_evidence["candidate_sidecar_sha256"] != sidecar_sha
        or capture_evidence["prompt_cache_sha256"] != prompt_sha
        or replay_evidence["prompt_cache_sha256"] != prompt_sha
        or capture_evidence["embedding_cache_sha256"] != embedding_sha
        or replay_evidence["embedding_cache_sha256"] != embedding_sha
        or capture_evidence["checkpoint_sha256"]
        != sha256_file(selected_capture_checkpoint_path)
        or capture_evidence["events_sha256"]
        != sha256_file(selected_capture_events_path)
        or replay_evidence["checkpoint_sha256"]
        != sha256_file(selected_replay_checkpoint_path)
        or replay_evidence["events_sha256"]
        != sha256_file(selected_replay_events_path)
    ):
        raise ValueError("S4 sidecar activation external evidence drift")
    expected = build_s4_sidecar_qualification_activation(
        qualification_plan=_load_json_mapping(
            plan_path, label="sealed S4 qualification plan"
        ),
        qualification_plan_file_sha256=sha256_file(plan_path),
        smoke_result=_load_json_mapping(
            smoke_path, label="sealed retry-008 smoke result"
        ),
        smoke_result_file_sha256=sha256_file(smoke_path),
        authority=_load_json_mapping(
            selected_authority_path, label="sealed retry-008 authority"
        ),
        authority_file_sha256=sha256_file(selected_authority_path),
        consumption=_load_json_mapping(
            selected_consumption_path,
            label="sealed retry-008 authority consumption",
        ),
        consumption_file_sha256=sha256_file(selected_consumption_path),
        capture_result=_load_json_mapping(
            capture_path, label="sealed retry-008 capture phase"
        ),
        capture_result_file_sha256=sha256_file(capture_path),
        replay_result=_load_json_mapping(
            replay_path, label="sealed retry-008 replay phase"
        ),
        replay_result_file_sha256=sha256_file(replay_path),
        candidate_sidecar_file_sha256=sidecar_sha,
        source_sha256=sources,
        git_commit=str(artifact.get("git_commit")),
    )
    if artifact != expected:
        raise ValueError("S4 sidecar activation external evidence drift")
    return artifact


def finalize_s4_sidecar_qualification_activation(
    *, path: Path, artifact: Mapping[str, Any]
) -> dict[str, Any]:
    verified = verify_s4_sidecar_qualification_activation(artifact)
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
    return verified
