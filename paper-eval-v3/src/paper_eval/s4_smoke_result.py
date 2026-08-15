"""Strict offline verifier for the sealed one-history S4 smoke result."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import payload_sha256
from .s4_d0_runner import evaluate_s4_smoke


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
    artifact = _envelope(value, label=f"S4 {mode} result")
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
        "capture": ("U0_CAPTURE", "U0"),
        "replay": ("D0_READ_ONLY_REPLAY", "D0"),
    }
    if (
        set(payload) != expected_fields
        or payload.get("schema_version")
        != "membind.paper-eval-v3.s4-phase-result.v1"
        or payload.get("stage") != "S4"
        or payload.get("mode") != mode
        or (payload.get("phase"), payload.get("method"))
        != expected_identity[mode]
        or payload.get("history_id") != "07741c45"
        or payload.get("status") != "PASS"
        or payload.get("mergeable") is not True
        or payload.get("expected_episode_count") != 49
        or payload.get("completed_source_sequences") != list(range(49))
        or payload.get("episode_coverage") != 1.0
        or payload.get("error_class") is not None
    ):
        raise ValueError(f"S4 {mode} phase identity or coverage drift")
    _sha(payload.get("canonical_graph_sha256"), field=f"{mode} graph")
    _sha(payload.get("checkpoint_sha256"), field=f"{mode} checkpoint")
    _sha(payload.get("events_sha256"), field=f"{mode} events")
    runtime = _mapping(payload.get("runtime_evidence"), label=f"{mode} runtime")
    expected_runtime = {
        "live_llm_calls",
        "live_embedding_calls",
        "resolved_prompt_count",
        "resolved_embedding_count",
        "unexpected_prompt_count",
        "unexpected_embedding_count",
        "live_fallback_count",
        "cross_encoder_call_count",
    }
    if set(runtime) != expected_runtime or any(
        not isinstance(value, int) or value < 0 for value in runtime.values()
    ):
        raise ValueError(f"S4 {mode} runtime evidence drift")
    caches = _mapping(payload.get("cache_evidence"), label=f"{mode} caches")
    if set(caches) != {"prompt_cache_sha256", "embedding_cache_sha256"}:
        raise ValueError(f"S4 {mode} cache evidence drift")
    for name, cache_sha in caches.items():
        _sha(cache_sha, field=f"{mode} {name}")
    cleanup = _mapping(payload.get("cleanup"), label=f"{mode} cleanup")
    if (
        cleanup.get("scope") != "EXACT_GROUP_ID_ONLY"
        or cleanup.get("namespace") != payload.get("namespace")
        or cleanup.get("global_cleanup_used") is not False
        or cleanup.get("post_cleanup_node_count") != 0
        or cleanup.get("post_cleanup_relationship_count") != 0
    ):
        raise ValueError(f"S4 {mode} cleanup drift")
    return artifact


def _reject_private(value: object) -> None:
    forbidden = {
        "answer",
        "api_key",
        "content",
        "messages",
        "password",
        "prompt",
        "question",
        "raw_output",
        "raw_response",
        "secret",
    }
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in forbidden:
                raise ValueError("S4 result contains private data")
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def verify_s4_smoke_result(
    *,
    result: Mapping[str, Any],
    authority_file_sha256: str,
    consumption_file_sha256: str,
    capture_result: Mapping[str, Any],
    capture_result_file_sha256: str,
    replay_result: Mapping[str, Any],
    replay_result_file_sha256: str,
) -> dict[str, Any]:
    """Recompute all smoke gates and verify every external file binding."""

    capture = _phase(capture_result, mode="capture")
    replay = _phase(replay_result, mode="replay")
    artifact = _envelope(result, label="S4 smoke result")
    payload = artifact["payload"]
    if set(payload) != {
        "schema_version",
        "stage",
        "verdict",
        "authority_file_sha256",
        "authority_consumption_file_sha256",
        "capture_result_file_sha256",
        "replay_result_file_sha256",
        "evaluation",
        "authority",
    }:
        raise ValueError("S4 smoke result payload shape drift")
    expected_hashes = {
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
    if any(payload.get(name) != value for name, value in expected_hashes.items()):
        raise ValueError("S4 smoke result external file binding drift")
    recomputed = evaluate_s4_smoke(
        capture_result=capture,
        replay_result=replay,
    )
    if (
        payload.get("schema_version")
        != "membind.paper-eval-v3.s4-d0-smoke-result.v1"
        or payload.get("stage") != "S4"
        or payload.get("verdict") != "PASS"
        or payload.get("evaluation") != recomputed
        or recomputed.get("verdict") != "PASS"
        or payload.get("authority")
        != {
            "s4_four_history_qualification_authorized": True,
            "s5_authorized": False,
            "pilot_execution_authorized": False,
        }
    ):
        raise ValueError("S4 smoke verdict, evaluation, or authority drift")
    _reject_private(payload)
    return artifact

