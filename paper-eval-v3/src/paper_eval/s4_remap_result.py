"""Strict offline verifier for the S4 candidate-remap smoke result."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import payload_sha256
from .s4_d0_runner import evaluate_s4_smoke
from .s4_remap_authority import (
    verify_s4_remap_authority,
    verify_s4_remap_authority_consumption,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BASE_RUNTIME = {
    "live_llm_calls",
    "live_embedding_calls",
    "resolved_prompt_count",
    "resolved_embedding_count",
    "unexpected_prompt_count",
    "unexpected_embedding_count",
    "live_fallback_count",
    "cross_encoder_call_count",
}
_REMAP_RUNTIME = {
    "exact_prompt_hit_count",
    "candidate_remap_hit_count",
    "candidate_remap_node_hit_count",
    "candidate_remap_edge_hit_count",
    "candidate_remap_rejection_count",
}


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is not a SHA256")
    return value


def _envelope(value: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    artifact = _mapping(value, label=label)
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise ValueError(f"{label} envelope shape drift")
    payload = _mapping(artifact.get("payload"), label=f"{label} payload")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise ValueError(f"{label} envelope hash drift")
    artifact["payload"] = payload
    return artifact


def _phase(value: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
    artifact = _envelope(value, label=f"S4 remap {mode} phase")
    payload = artifact["payload"]
    expected_fields = {
        "schema_version",
        "stage",
        "phase",
        "run_id",
        "history_id",
        "namespace",
        "method",
        "mode",
        "cache_id",
        "status",
        "mergeable",
        "expected_episode_count",
        "completed_source_sequences",
        "episode_coverage",
        "canonical_graph_sha256",
        "runtime_evidence",
        "cache_evidence",
        "cleanup",
        "error_class",
        "checkpoint_sha256",
        "events_sha256",
    }
    expected_identity = {
        "capture": {
            "phase": "U0_CAPTURE",
            "method": "U0",
            "run_id": "s4-d0-capture-20260815-005",
            "namespace": "pev3-s4-u0-capture-20260815-005",
        },
        "replay": {
            "phase": "D0_READ_ONLY_REPLAY",
            "method": "D0",
            "run_id": "s4-d0-replay-20260815-005",
            "namespace": "pev3-s4-d0-replay-20260815-005",
        },
    }[mode]
    if (
        set(payload) != expected_fields
        or artifact.get("run_id") != expected_identity["run_id"]
        or payload.get("schema_version")
        != "membind.paper-eval-v3.s4-phase-result.v1"
        or payload.get("stage") != "S4"
        or payload.get("mode") != mode
        or any(payload.get(field) != value for field, value in expected_identity.items())
        or payload.get("history_id") != "07741c45"
        or payload.get("cache_id")
        != "s4-d0-remap-07741c45-20260815-005"
        or payload.get("status") != "PASS"
        or payload.get("mergeable") is not True
        or payload.get("expected_episode_count") != 49
        or payload.get("completed_source_sequences") != list(range(49))
        or payload.get("episode_coverage") != 1.0
        or payload.get("error_class") is not None
    ):
        raise ValueError(f"S4 remap {mode} phase identity or coverage drift")
    for field in ("canonical_graph_sha256", "checkpoint_sha256", "events_sha256"):
        _sha(payload.get(field), field=f"{mode} {field}")

    runtime = _mapping(payload.get("runtime_evidence"), label=f"{mode} runtime")
    expected_runtime = _BASE_RUNTIME if mode == "capture" else _BASE_RUNTIME | _REMAP_RUNTIME
    if set(runtime) != expected_runtime or any(
        not isinstance(child, int) or isinstance(child, bool) or child < 0
        for child in runtime.values()
    ):
        raise ValueError(f"S4 remap {mode} runtime evidence drift")
    if mode == "capture":
        if runtime["live_llm_calls"] <= 0 or runtime["live_embedding_calls"] <= 0:
            raise ValueError("S4 remap capture lacks live model work")
    else:
        if any(
            runtime[field] != 0
            for field in (
                "live_llm_calls",
                "live_embedding_calls",
                "unexpected_prompt_count",
                "unexpected_embedding_count",
                "live_fallback_count",
                "cross_encoder_call_count",
                "candidate_remap_rejection_count",
            )
        ):
            raise ValueError("S4 remap replay used a forbidden live or miss path")
        if runtime["candidate_remap_hit_count"] != (
            runtime["candidate_remap_node_hit_count"]
            + runtime["candidate_remap_edge_hit_count"]
        ):
            raise ValueError("S4 remap replay counter breakdown drift")
        if runtime["exact_prompt_hit_count"] + runtime[
            "candidate_remap_hit_count"
        ] != runtime["resolved_prompt_count"]:
            raise ValueError("S4 remap replay prompt accounting drift")

    caches = _mapping(payload.get("cache_evidence"), label=f"{mode} caches")
    if set(caches) != {"prompt_cache_sha256", "embedding_cache_sha256"}:
        raise ValueError(f"S4 remap {mode} cache evidence drift")
    for name, cache_sha in caches.items():
        _sha(cache_sha, field=f"{mode} {name}")
    cleanup = _mapping(payload.get("cleanup"), label=f"{mode} cleanup")
    if cleanup != {
        "scope": "EXACT_GROUP_ID_ONLY",
        "namespace": expected_identity["namespace"],
        "global_cleanup_used": False,
        "post_cleanup_node_count": 0,
        "post_cleanup_relationship_count": 0,
    }:
        raise ValueError(f"S4 remap {mode} cleanup drift")
    return artifact


def _reject_private(value: object) -> None:
    forbidden = {
        "answer",
        "api_key",
        "content",
        "messages",
        "password",
        "prompt",
        "prompt_parts",
        "question",
        "raw_output",
        "raw_response",
        "secret",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in forbidden:
                raise ValueError("S4 remap result contains private data")
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def verify_s4_remap_smoke_result(
    *,
    result: Mapping[str, Any],
    authority: Mapping[str, Any],
    authority_file_sha256: str,
    consumption: Mapping[str, Any],
    consumption_file_sha256: str,
    capture_result: Mapping[str, Any],
    capture_result_file_sha256: str,
    replay_result: Mapping[str, Any],
    replay_result_file_sha256: str,
) -> dict[str, Any]:
    selected_authority = verify_s4_remap_authority(authority)
    selected_consumption = verify_s4_remap_authority_consumption(consumption)
    capture = _phase(capture_result, mode="capture")
    replay = _phase(replay_result, mode="replay")
    artifact = _envelope(result, label="S4 remap smoke result")
    payload = artifact["payload"]
    expected_fields = {
        "schema_version",
        "stage",
        "verdict",
        "authority_file_sha256",
        "authority_consumption_file_sha256",
        "capture_result_file_sha256",
        "replay_result_file_sha256",
        "evaluation",
        "authority",
    }
    external = {
        "authority_file_sha256": _sha(
            authority_file_sha256, field="authority file"
        ),
        "authority_consumption_file_sha256": _sha(
            consumption_file_sha256, field="consumption file"
        ),
        "capture_result_file_sha256": _sha(
            capture_result_file_sha256, field="capture result file"
        ),
        "replay_result_file_sha256": _sha(
            replay_result_file_sha256, field="replay result file"
        ),
    }
    if set(payload) != expected_fields or any(
        payload.get(field) != value for field, value in external.items()
    ):
        raise ValueError("S4 remap result shape or external binding drift")

    authority_payload = selected_authority["payload"]
    consumption_payload = selected_consumption["payload"]
    if (
        selected_authority.get("run_id")
        != "s4-remap-smoke-authority-20260815-005"
        or selected_consumption.get("run_id")
        != "s4-remap-authority-consumption-20260815-005"
        or consumption_payload["authority_file_sha256"] != external[
            "authority_file_sha256"
        ]
        or consumption_payload["authority_payload_sha256"]
        != selected_authority["payload_sha256"]
        or consumption_payload["runs"] != authority_payload["runs"]
        or consumption_payload["private_cache"] != authority_payload["private_cache"]
        or consumption_payload["candidate_oracle_sha256"]
        != payload_sha256(authority_payload["candidate_oracle"])
        or capture["payload"]["run_id"]
        != authority_payload["runs"]["U0_CAPTURE"]["run_id"]
        or replay["payload"]["run_id"]
        != authority_payload["runs"]["D0_READ_ONLY_REPLAY"]["run_id"]
    ):
        raise ValueError("S4 remap authority, consumption, or run binding drift")

    recomputed = evaluate_s4_smoke(
        capture_result=capture,
        replay_result=replay,
    )
    if (
        artifact.get("run_id")
        != "s4-d0-remap-smoke-result-20260815-005"
        or payload.get("schema_version")
        != "membind.paper-eval-v3.s4-d0-remap-smoke-result.v2"
        or payload.get("stage") != "S4"
        or payload.get("verdict") != "PASS"
        or payload.get("evaluation") != recomputed
        or recomputed.get("verdict") != "PASS"
        or recomputed.get("candidate_oracle_resolution_accounting") is not True
        or payload.get("authority")
        != {
            "s4_four_history_qualification_authorized": True,
            "s5_authorized": False,
            "pilot_execution_authorized": False,
        }
    ):
        raise ValueError("S4 remap result verdict or hard-gate drift")
    _reject_private(payload)
    return artifact
