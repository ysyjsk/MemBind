"""Frozen contracts for the isolated U0/P(C=2)/P(C=4) APC experiment.

This module intentionally does not import Graphiti or open a network connection.
It freezes the 12-block matrix, shared arrivals, model identities, and the
per-block cold-start/warm-cache accounting required by the live runner.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from paper_eval.artifacts import payload_sha256

SCHEMA = "membind.paper-eval-v3.c246-baseline-plan.v1"
C8_EXTENSION_SCHEMA = "membind.paper-eval-v3.c246-c8-extension-plan.v1"
C246_METHODS = ("U0-aligned", "P(C=2)-aligned", "P(C=4)-aligned")
C246_HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
_ORDERS = (
    C246_METHODS,
    (C246_METHODS[1], C246_METHODS[2], C246_METHODS[0]),
    (C246_METHODS[2], C246_METHODS[0], C246_METHODS[1]),
    C246_METHODS,
)
_SLUG = {"U0-aligned": "u0", "P(C=2)-aligned": "pc2", "P(C=4)-aligned": "pc4"}
_RUN = re.compile(r"^c246-baseline-[a-z0-9][a-z0-9-]{2,63}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(code)
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise ValueError(code)
    return value


def cache_salt_for_block(run_id: str, block_index: int) -> str:
    if not isinstance(run_id, str) or _RUN.fullmatch(run_id) is None:
        raise ValueError("run id invalid")
    return f"c246-{payload_sha256({'run_id': run_id, 'block_index': _int(block_index, 'block index invalid')})[:32]}"


def cache_salt_for_extension_block(run_id: str, block_index: int) -> str:
    index = _int(block_index, "block index invalid")
    if index >= len(C246_HISTORIES):
        raise ValueError("block index invalid")
    return cache_salt_for_block(run_id, index + 12)


def _sources(value: Mapping[str, Sequence[str]]) -> dict[str, list[str]]:
    if not isinstance(value, Mapping) or tuple(value) != C246_HISTORIES:
        raise ValueError("source inventory invalid")
    result: dict[str, list[str]] = {}
    for history in C246_HISTORIES:
        raw = value.get(history)
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or not raw:
            raise ValueError("source inventory invalid")
        selected = [_sha(item, "source identity invalid") for item in raw]
        if len(set(selected)) != len(selected):
            raise ValueError("source identity duplicate")
        result[history] = selected
    return result


def build_c246_plan(
    *,
    run_id: str,
    history_source_sha256s: Mapping[str, Sequence[str]],
    interarrival_ns: int,
    service_reference_ns: int,
    execution_envelope_sha256: str,
    construction_model_identity_sha256: str,
    embedding_model_identity_sha256: str,
    normalized_offered_load: float = 1.2,
    methods: Sequence[str] = C246_METHODS,
) -> dict[str, Any]:
    if not isinstance(run_id, str) or _RUN.fullmatch(run_id) is None:
        raise ValueError("run id invalid")
    if tuple(methods) != C246_METHODS:
        raise ValueError("methods invalid")
    interval = _int(interarrival_ns, "interarrival invalid")
    service = _int(service_reference_ns, "service reference invalid")
    load = float(normalized_offered_load)
    if not math.isfinite(load) or load <= 0 or round(service / load) != interval:
        raise ValueError("interarrival derivation invalid")
    sources = _sources(history_source_sha256s)
    envelope = _sha(execution_envelope_sha256, "execution envelope invalid")
    construction = _sha(construction_model_identity_sha256, "construction identity invalid")
    embedding = _sha(embedding_model_identity_sha256, "embedding identity invalid")
    traces: dict[str, dict[str, object]] = {}
    for history in C246_HISTORIES:
        body = {
            "history_id": history,
            "interarrival_ns": interval,
            "arrival_offsets_ns": [i * interval for i in range(len(sources[history]))],
        }
        traces[history] = {**body, "history_arrival_trace_sha256": payload_sha256(body)}
    source_manifest = payload_sha256(sources)
    arrival_manifest = payload_sha256(traces)
    blocks: list[dict[str, object]] = []
    for history_index, history in enumerate(C246_HISTORIES):
        for position, method in enumerate(_ORDERS[history_index]):
            index = len(blocks)
            salt = cache_salt_for_block(run_id, index)
            blocks.append(
                {
                    "block_index": index,
                    "run_id": run_id,
                    "aligned_run_id": run_id,
                    "method": method,
                    "method_position": position,
                    "history_id": history,
                    "source_count": len(sources[history]),
                    "namespace": f"c246-{run_id}-{_SLUG[method]}-{history}",
                    "source_manifest_sha256": source_manifest,
                    "arrival_trace_sha256": arrival_manifest,
                    "history_arrival_trace_sha256": traces[history]["history_arrival_trace_sha256"],
                    "execution_envelope_sha256": envelope,
                    "shared_execution_envelope_sha256": envelope,
                    "construction_model_identity_sha256": construction,
                    "embedding_model_identity_sha256": embedding,
                    "global_llm_admission_k": 2,
                    "cache_salt_sha256": payload_sha256({"cache_salt": salt}),
                    "cold_start": {
                        "policy": "HOT_SHARED_ENGINE_UNIQUE_BLOCK_CACHE_SALT",
                        "first_request_started_at": None,
                        "first_request_completed_at": None,
                        "first_request_latency_ns": None,
                        "first_request_prefix_cache_queries_delta": None,
                        "first_request_prefix_cache_hits_delta": None,
                        "excluded_from_warm_metrics": True,
                    },
                    "warm_block_metrics": {
                        "prefix_cache_hit_rate": None,
                        "prompt_throughput": None,
                        "generation_throughput": None,
                        "running_requests": None,
                        "waiting_requests": None,
                        "gpu_kv_cache_usage": None,
                    },
                }
            )
    plan: dict[str, Any] = {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "aligned_run_id": run_id,
        "data_role": "DEVELOPMENT_EXPOSED",
        "heldout_data_accessed": False,
        "methods": list(C246_METHODS),
        "histories": list(C246_HISTORIES),
        "history_source_sha256s": sources,
        "source_manifest_sha256": source_manifest,
        "interarrival_ns": interval,
        "service_reference_ns": service,
        "normalized_offered_load": load,
        "arrival_traces": traces,
        "arrival_trace_sha256": arrival_manifest,
        "execution_envelope_sha256": envelope,
        "shared_execution_envelope_sha256": envelope,
        "construction_model_identity_sha256": construction,
        "embedding_model_identity_sha256": embedding,
        "global_llm_admission_k": 2,
        "apc_cache_policy": "HOT_ENGINE_COLD_CROSS_BLOCK_UNIQUE_SALT_WARM_WITHIN_BLOCK",
        "blocks": blocks,
    }
    plan["payload_sha256"] = payload_sha256(plan)
    return plan


def verify_c246_plan(value: Mapping[str, object]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != SCHEMA:
        raise ValueError("plan invalid")
    candidate = deepcopy(dict(value))
    expected = build_c246_plan(
        run_id=candidate.get("run_id"),
        history_source_sha256s=candidate.get("history_source_sha256s"),
        interarrival_ns=candidate.get("interarrival_ns"),
        service_reference_ns=candidate.get("service_reference_ns"),
        execution_envelope_sha256=candidate.get("execution_envelope_sha256"),
        construction_model_identity_sha256=candidate.get("construction_model_identity_sha256"),
        embedding_model_identity_sha256=candidate.get("embedding_model_identity_sha256"),
        normalized_offered_load=candidate.get("normalized_offered_load"),
        methods=candidate.get("methods"),
    )
    if candidate != expected:
        raise ValueError("plan identity drift")
    return candidate


def build_c8_extension_plan(
    *, base_plan: Mapping[str, object], full_phase_result: Mapping[str, object]
) -> dict[str, Any]:
    """Build the optional C8 row only after the 12-block base lane passes."""

    base = verify_c246_plan(base_plan)
    if not isinstance(full_phase_result, Mapping):
        raise ValueError("full base result required")
    full = deepcopy(dict(full_phase_result))
    full_body = {key: value for key, value in full.items() if key != "payload_sha256"}
    if (
        full.get("payload_sha256") != payload_sha256(full_body)
        or full.get("status") != "PASS"
        or full.get("phase") != "full"
        or full.get("run_id") != base["run_id"]
        or full.get("completed_block_indices") != list(range(12))
    ):
        raise ValueError("full base result required")
    blocks: list[dict[str, object]] = []
    for index, history in enumerate(C246_HISTORIES):
        salt = cache_salt_for_extension_block(base["run_id"], index)
        blocks.append(
            {
                "block_index": index,
                "run_id": base["run_id"],
                "aligned_run_id": f"{base['run_id']}-c8",
                "method": "P(C=8)-aligned",
                "method_position": 0,
                "history_id": history,
                "source_count": len(base["history_source_sha256s"][history]),
                "namespace": f"c246-{base['run_id']}-pc8-{history}",
                "source_manifest_sha256": base["source_manifest_sha256"],
                "arrival_trace_sha256": base["arrival_trace_sha256"],
                "history_arrival_trace_sha256": base["arrival_traces"][history]["history_arrival_trace_sha256"],
                "execution_envelope_sha256": base["execution_envelope_sha256"],
                "shared_execution_envelope_sha256": base["shared_execution_envelope_sha256"],
                "construction_model_identity_sha256": base["construction_model_identity_sha256"],
                "embedding_model_identity_sha256": base["embedding_model_identity_sha256"],
                "global_llm_admission_k": base["global_llm_admission_k"],
                "cache_salt_sha256": payload_sha256({"cache_salt": salt}),
                "cold_start": {
                    "policy": "HOT_SHARED_ENGINE_UNIQUE_BLOCK_CACHE_SALT",
                    "first_request_started_at": None,
                    "first_request_completed_at": None,
                    "first_request_latency_ns": None,
                    "first_request_prefix_cache_queries_delta": None,
                    "first_request_prefix_cache_hits_delta": None,
                    "excluded_from_warm_metrics": True,
                },
                "warm_block_metrics": {
                    "prefix_cache_hit_rate": None,
                    "prompt_throughput": None,
                    "generation_throughput": None,
                    "running_requests": None,
                    "waiting_requests": None,
                    "gpu_kv_cache_usage": None,
                },
            }
        )
    result: dict[str, Any] = {
        "schema_version": C8_EXTENSION_SCHEMA,
        "run_id": base["run_id"],
        "aligned_run_id": f"{base['run_id']}-c8",
        "data_role": base["data_role"],
        "heldout_data_accessed": False,
        "methods": ["P(C=8)-aligned"],
        "histories": list(C246_HISTORIES),
        "history_source_sha256s": deepcopy(base["history_source_sha256s"]),
        "source_manifest_sha256": base["source_manifest_sha256"],
        "interarrival_ns": base["interarrival_ns"],
        "service_reference_ns": base["service_reference_ns"],
        "normalized_offered_load": base["normalized_offered_load"],
        "arrival_traces": deepcopy(base["arrival_traces"]),
        "arrival_trace_sha256": base["arrival_trace_sha256"],
        "execution_envelope_sha256": base["execution_envelope_sha256"],
        "shared_execution_envelope_sha256": base["shared_execution_envelope_sha256"],
        "construction_model_identity_sha256": base["construction_model_identity_sha256"],
        "embedding_model_identity_sha256": base["embedding_model_identity_sha256"],
        "global_llm_admission_k": base["global_llm_admission_k"],
        "apc_cache_policy": base["apc_cache_policy"],
        "base_plan_payload_sha256": base["payload_sha256"],
        "base_full_result_payload_sha256": full["payload_sha256"],
        "base_plan": base,
        "base_full_phase_result": full,
        "blocks": blocks,
    }
    result["payload_sha256"] = payload_sha256(result)
    return result


def verify_c8_extension_plan(value: Mapping[str, object]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != C8_EXTENSION_SCHEMA:
        raise ValueError("C8 extension plan invalid")
    candidate = deepcopy(dict(value))
    expected = build_c8_extension_plan(
        base_plan=candidate.get("base_plan"),
        full_phase_result=candidate.get("base_full_phase_result"),
    )
    if candidate != expected:
        raise ValueError("C8 extension plan identity drift")
    return candidate


__all__ = [
    "C246_HISTORIES", "C246_METHODS", "C8_EXTENSION_SCHEMA", "SCHEMA",
    "build_c246_plan", "build_c8_extension_plan", "cache_salt_for_block",
    "cache_salt_for_extension_block", "verify_c246_plan", "verify_c8_extension_plan",
]
