"""Strict external-evidence verifier for an S4 sidecar smoke PASS."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import payload_sha256
from .s4_sidecar_authority import (
    verify_s4_sidecar_authority,
    verify_s4_sidecar_authority_consumption,
)
from .s4_sidecar_result import evaluate_s4_sidecar_smoke


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
_SIDECAR_RUNTIME = {
    "sidecar_exact_hit_count",
    "sidecar_remap_hit_count",
    "sidecar_rejection_count",
    "sidecar_capture_append_count",
    "sidecar_capture_reuse_count",
    "sidecar_replay_binding_count",
    "sidecar_record_count",
    "sidecar_consumed_count",
    "sidecar_remaining_count",
    "sidecar_resumed_consumed_count",
    "sidecar_prepared_count",
}
_REMAP_RUNTIME = {
    "exact_prompt_hit_count",
    "candidate_remap_hit_count",
    "candidate_remap_node_hit_count",
    "candidate_remap_edge_hit_count",
    "candidate_remap_rejection_count",
}
_PHASE_FIELDS = {
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


def _attempt(runs: Mapping[str, Any]) -> str:
    selected = _mapping(runs, label="S4 sidecar runs")
    capture = _mapping(selected.get("U0_CAPTURE"), label="capture run")
    replay = _mapping(selected.get("D0_READ_ONLY_REPLAY"), label="replay run")
    match = re.fullmatch(
        r"s4-d0-capture-20260815-(\d{3})", str(capture.get("run_id"))
    )
    if match is None:
        raise ValueError("S4 sidecar capture attempt identity drift")
    attempt = match.group(1)
    if (
        int(attempt) < 6
        or replay.get("run_id") != f"s4-d0-replay-20260815-{attempt}"
    ):
        raise ValueError("S4 sidecar phase attempt identity drift")
    return attempt


def _phase(
    value: Mapping[str, Any],
    *,
    mode: str,
    expected_run: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = _envelope(value, label=f"S4 sidecar {mode} phase")
    payload = artifact["payload"]
    phase = "U0_CAPTURE" if mode == "capture" else "D0_READ_ONLY_REPLAY"
    if (
        set(payload) != _PHASE_FIELDS
        or artifact.get("run_id") != expected_run.get("run_id")
        or payload.get("schema_version")
        != "membind.paper-eval-v3.s4-phase-result.v1"
        or payload.get("stage") != "S4"
        or payload.get("phase") != phase
        or payload.get("run_id") != expected_run.get("run_id")
        or payload.get("history_id") != "07741c45"
        or payload.get("namespace") != expected_run.get("namespace")
        or payload.get("method") != expected_run.get("method")
        or payload.get("mode") != mode
        or payload.get("cache_id") != expected_run.get("cache_id")
        or payload.get("status") != "PASS"
        or payload.get("mergeable") is not True
        or payload.get("expected_episode_count") != 49
        or payload.get("completed_source_sequences") != list(range(49))
        or payload.get("episode_coverage") != 1.0
        or payload.get("error_class") is not None
    ):
        raise ValueError(f"S4 sidecar {mode} phase identity or coverage drift")
    for field in ("canonical_graph_sha256", "checkpoint_sha256", "events_sha256"):
        _sha(payload.get(field), field=f"{mode} {field}")

    runtime = _mapping(payload.get("runtime_evidence"), label=f"{mode} runtime")
    expected_runtime = _BASE_RUNTIME | _SIDECAR_RUNTIME
    if mode == "replay":
        expected_runtime |= _REMAP_RUNTIME
    if set(runtime) != expected_runtime or any(
        not isinstance(child, int) or isinstance(child, bool) or child < 0
        for child in runtime.values()
    ):
        raise ValueError(f"S4 sidecar {mode} runtime evidence drift")

    caches = _mapping(payload.get("cache_evidence"), label=f"{mode} caches")
    if set(caches) != {
        "prompt_cache_sha256",
        "embedding_cache_sha256",
        "candidate_sidecar_sha256",
    }:
        raise ValueError(f"S4 sidecar {mode} cache evidence drift")
    for name, digest in caches.items():
        _sha(digest, field=f"{mode} {name}")

    cleanup = _mapping(payload.get("cleanup"), label=f"{mode} cleanup")
    if cleanup != {
        "scope": "EXACT_GROUP_ID_ONLY",
        "namespace": expected_run.get("namespace"),
        "global_cleanup_used": False,
        "post_cleanup_node_count": 0,
        "post_cleanup_relationship_count": 0,
    }:
        raise ValueError(f"S4 sidecar {mode} cleanup drift")
    return artifact


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
                raise ValueError("S4 sidecar result contains private data")
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def verify_s4_sidecar_smoke_result(
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
    candidate_sidecar_file_sha256: str,
) -> dict[str, Any]:
    """Recompute every public hard gate and all external-file bindings."""

    selected_authority = verify_s4_sidecar_authority(authority)
    selected_consumption = verify_s4_sidecar_authority_consumption(consumption)
    authority_payload = selected_authority["payload"]
    runs = _mapping(authority_payload.get("runs"), label="authority runs")
    attempt = _attempt(runs)
    capture = _phase(
        capture_result,
        mode="capture",
        expected_run=_mapping(runs["U0_CAPTURE"], label="capture authority run"),
    )
    replay = _phase(
        replay_result,
        mode="replay",
        expected_run=_mapping(
            runs["D0_READ_ONLY_REPLAY"], label="replay authority run"
        ),
    )
    artifact = _envelope(result, label="S4 sidecar smoke result")
    payload = artifact["payload"]
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
        "candidate_sidecar_file_sha256": _sha(
            candidate_sidecar_file_sha256, field="candidate sidecar file"
        ),
    }
    if set(payload) != {
        "schema_version",
        "stage",
        "verdict",
        *external,
        "evaluation",
        "authority",
    } or any(payload.get(field) != digest for field, digest in external.items()):
        raise ValueError("S4 sidecar result shape or external binding drift")

    consumption_payload = selected_consumption["payload"]
    if (
        selected_authority.get("run_id")
        != f"s4-sidecar-smoke-authority-20260815-{attempt}"
        or selected_consumption.get("run_id")
        != f"s4-sidecar-authority-consumption-20260815-{attempt}"
        or consumption_payload.get("authority_file_sha256")
        != external["authority_file_sha256"]
        or consumption_payload.get("authority_payload_sha256")
        != selected_authority["payload_sha256"]
        or consumption_payload.get("runs") != runs
    ):
        raise ValueError("S4 sidecar authority, consumption, or run binding drift")

    capture_cache = capture["payload"]["cache_evidence"]
    replay_cache = replay["payload"]["cache_evidence"]
    if (
        capture_cache.get("candidate_sidecar_sha256")
        != external["candidate_sidecar_file_sha256"]
        or replay_cache.get("candidate_sidecar_sha256")
        != external["candidate_sidecar_file_sha256"]
    ):
        raise ValueError("S4 sidecar file and phase cache evidence drift")

    recomputed = evaluate_s4_sidecar_smoke(
        capture_result=capture,
        replay_result=replay,
    )
    if (
        artifact.get("run_id")
        != f"s4-d0-sidecar-smoke-result-20260815-{attempt}"
        or payload.get("schema_version")
        != "membind.paper-eval-v3.s4-d0-sidecar-smoke-result.v3"
        or payload.get("stage") != "S4"
        or payload.get("verdict") != "PASS"
        or payload.get("evaluation") != recomputed
        or recomputed.get("verdict") != "PASS"
        or recomputed.get("canonical_graph_parity") is not True
        or recomputed.get("cache_and_sidecar_mutation_during_replay") is not False
        or recomputed.get("sidecar_consumption_exact") is not True
        or recomputed.get("edge_sidecar_resolution_accounting") is not True
        or payload.get("authority")
        != {
            "s4_four_history_qualification_authorized": True,
            "s5_authorized": False,
            "pilot_execution_authorized": False,
        }
    ):
        raise ValueError("S4 sidecar result verdict or hard-gate drift")
    _reject_private(payload)
    return artifact
