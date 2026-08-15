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
from .artifacts import finalize_envelope, payload_sha256
from .s4_qualification_plan import verify_s4_qualification_plan
from .s4_sidecar_smoke_result_verifier import verify_s4_sidecar_smoke_result


SCHEMA = "membind.paper-eval-v3.s4-sidecar-qualification-activation.v2"
RUN_ID = "s4-sidecar-qualification-activation-20260815-002"
HISTORY_IDS = ["07741c45", "b6019101", "6071bd76", "a2f3aa27"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
    """Reverify retry-007 and activate only the remaining three blocks."""

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
    )
    smoke_payload = _mapping(
        verified_smoke.get("payload"), label="verified sidecar smoke payload"
    )
    if (
        verified_smoke.get("run_id")
        != "s4-d0-sidecar-smoke-result-20260815-007"
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
    if set(sources) != {"activation", "test"}:
        raise ValueError("S4 sidecar activation source inventory drift")

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
        }
        or smoke.get("kind") != "S4_D0_BILATERAL_SIDECAR_SMOKE_V3"
        or smoke.get("run_id") != "s4-d0-sidecar-smoke-result-20260815-007"
        or smoke.get("verdict") != "PASS"
        or smoke.get("history_id") != HISTORY_IDS[0]
    ):
        raise ValueError("S4 sidecar activated smoke drift")
    _sha(smoke.get("file_sha256"), field="activated smoke file")
    _sha(smoke.get("payload_sha256"), field="activated smoke payload")

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
    if set(sources) != {"activation", "test"}:
        raise ValueError("S4 sidecar activation source inventory drift")
    for name, digest in sources.items():
        _sha(digest, field=f"activation source {name}")
    _reject_private(payload)
    artifact["payload"] = payload
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
