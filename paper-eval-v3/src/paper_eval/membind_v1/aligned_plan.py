"""Pure planning contract for the first fresh aligned development table.

Historical U0/P(C=2) construction runs used different arrival semantics.  This
module freezes a new common source manifest and open-loop arrival trace before
any live namespace exists, so every row of the later U0/P/MemBind comparison
has the same admission and arrival identities.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from paper_eval.artifacts import payload_sha256


SCHEMA = "membind.paper-eval-v3.membind-v1-aligned-development-plan.v1"
ALIGNED_METHODS = ("U0-aligned", "P(C=2)-aligned", "MemBind-v1 node-only")
ALIGNED_DEVELOPMENT_HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
_RUN_ID = re.compile(r"^aligned-[a-z0-9][a-z0-9-]{2,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HISTORY_ID = re.compile(r"^[0-9a-f]{8}$")
_METHOD_SLUGS = {
    "U0-aligned": "u0",
    "P(C=2)-aligned": "pc2",
    "MemBind-v1 node-only": "mv1",
}

# Four blocks cannot place all three methods in every position equally.  This
# fixed sequence gives each method exactly one position with one extra block,
# while retaining a complete cyclic rotation before the final balancing block.
_METHOD_ORDERS = (
    ("U0-aligned", "P(C=2)-aligned", "MemBind-v1 node-only"),
    ("P(C=2)-aligned", "MemBind-v1 node-only", "U0-aligned"),
    ("MemBind-v1 node-only", "U0-aligned", "P(C=2)-aligned"),
    ("MemBind-v1 node-only", "P(C=2)-aligned", "U0-aligned"),
)


class AlignedPlanError(ValueError):
    """An aligned workload, trace, or fairness identity drifted."""


def _fail(code: str) -> AlignedPlanError:
    return AlignedPlanError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _history_sources(value: Mapping[str, Sequence[str]]) -> dict[str, list[str]]:
    if not isinstance(value, Mapping) or tuple(value) != ALIGNED_DEVELOPMENT_HISTORIES:
        raise _fail("source manifest history inventory invalid")
    result: dict[str, list[str]] = {}
    for history_id in ALIGNED_DEVELOPMENT_HISTORIES:
        if _HISTORY_ID.fullmatch(history_id) is None:
            raise _fail("source manifest history identity invalid")
        raw = value.get(history_id)
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence) or not raw:
            raise _fail("source manifest invalid")
        hashes = [_sha(item, "source manifest invalid") for item in raw]
        if len(set(hashes)) != len(hashes):
            raise _fail("source manifest invalid")
        result[history_id] = hashes
    return result


def _arrival_traces(
    sources: Mapping[str, Sequence[str]], *, interarrival_ns: int
) -> dict[str, dict[str, object]]:
    interval = _nonnegative_int(interarrival_ns, "interarrival invalid")
    traces: dict[str, dict[str, object]] = {}
    for history_id in ALIGNED_DEVELOPMENT_HISTORIES:
        offsets = [index * interval for index in range(len(sources[history_id]))]
        body: dict[str, object] = {
            "history_id": history_id,
            "arrival_offsets_ns": offsets,
            "interarrival_ns": interval,
        }
        traces[history_id] = {**body, "trace_sha256": payload_sha256(body)}
    return traces


def _blocks(
    *,
    aligned_run_id: str,
    sources: Mapping[str, Sequence[str]],
    source_manifest_sha256: str,
    arrival_trace_sha256: str,
    traces: Mapping[str, Mapping[str, object]],
    shared_execution_envelope_sha256: str,
) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    for history_index, history_id in enumerate(ALIGNED_DEVELOPMENT_HISTORIES):
        for position, method in enumerate(_METHOD_ORDERS[history_index]):
            blocks.append(
                {
                    "block_index": len(blocks),
                    "aligned_run_id": aligned_run_id,
                    "history_id": history_id,
                    "method": method,
                    "method_position": position,
                    "namespace": (
                        f"pev3-{aligned_run_id}-{_METHOD_SLUGS[method]}-"
                        f"{history_id}-a001"
                    ),
                    "source_count": len(sources[history_id]),
                    "source_manifest_sha256": source_manifest_sha256,
                    "arrival_trace_sha256": arrival_trace_sha256,
                    "history_arrival_trace_sha256": traces[history_id]["trace_sha256"],
                    "shared_execution_envelope_sha256": shared_execution_envelope_sha256,
                    "global_llm_admission_k": 2,
                }
            )
    return blocks


def build_aligned_development_plan(
    *,
    aligned_run_id: str,
    history_source_sha256s: Mapping[str, Sequence[str]],
    interarrival_ns: int,
    shared_execution_envelope_sha256: str,
) -> dict[str, Any]:
    """Freeze a fresh U0/P(C=2)/MemBind development plan before live work."""

    if not isinstance(aligned_run_id, str) or _RUN_ID.fullmatch(aligned_run_id) is None:
        raise _fail("aligned run id invalid")
    sources = _history_sources(history_source_sha256s)
    envelope = _sha(shared_execution_envelope_sha256, "shared execution envelope invalid")
    traces = _arrival_traces(sources, interarrival_ns=interarrival_ns)
    source_manifest_sha = payload_sha256(sources)
    arrival_trace_sha = payload_sha256(traces)
    plan: dict[str, Any] = {
        "schema_version": SCHEMA,
        "aligned_run_id": aligned_run_id,
        "data_role": "DEVELOPMENT_EXPOSED",
        "heldout_data_accessed": False,
        "methods": list(ALIGNED_METHODS),
        "histories": list(ALIGNED_DEVELOPMENT_HISTORIES),
        "history_source_sha256s": sources,
        "source_manifest_sha256": source_manifest_sha,
        "interarrival_ns": interarrival_ns,
        "arrival_traces": traces,
        "arrival_trace_sha256": arrival_trace_sha,
        "shared_execution_envelope_sha256": envelope,
        "global_llm_admission_k": 2,
        "method_orders": [list(order) for order in _METHOD_ORDERS],
        "blocks": _blocks(
            aligned_run_id=aligned_run_id,
            sources=sources,
            source_manifest_sha256=source_manifest_sha,
            arrival_trace_sha256=arrival_trace_sha,
            traces=traces,
            shared_execution_envelope_sha256=envelope,
        ),
    }
    plan["payload_sha256"] = payload_sha256(plan)
    return plan


def verify_aligned_development_plan(value: Mapping[str, object]) -> dict[str, Any]:
    """Recompute all identities rather than trusting a stored plan projection."""

    if not isinstance(value, Mapping):
        raise _fail("plan invalid")
    candidate = deepcopy(dict(value))
    if candidate.get("schema_version") != SCHEMA:
        raise _fail("plan schema invalid")
    if candidate.get("global_llm_admission_k") != 2:
        raise _fail("global LLM admission invalid")
    traces = candidate.get("arrival_traces")
    if not isinstance(traces, Mapping) or tuple(traces) != ALIGNED_DEVELOPMENT_HISTORIES:
        raise _fail("arrival trace inventory invalid")
    for history_id, trace in traces.items():
        if not isinstance(trace, Mapping):
            raise _fail("arrival trace invalid")
        offsets = trace.get("arrival_offsets_ns")
        if isinstance(offsets, (str, bytes)) or not isinstance(offsets, list):
            raise _fail("arrival trace invalid")
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in offsets
        ) or any(right < left for left, right in zip(offsets, offsets[1:])):
            raise _fail("arrival trace invalid")
    sources = candidate.get("history_source_sha256s")
    if not isinstance(sources, Mapping):
        raise _fail("source manifest invalid")
    normalized_sources = _history_sources(sources)
    expected = build_aligned_development_plan(
        aligned_run_id=candidate.get("aligned_run_id"),
        history_source_sha256s=normalized_sources,
        interarrival_ns=candidate.get("interarrival_ns"),
        shared_execution_envelope_sha256=candidate.get("shared_execution_envelope_sha256"),
    )
    # Arrival timestamps are a pre-frozen shared identity, not merely a
    # monotonic sequence.  Validate their complete deterministic projection
    # before the generic whole-payload comparison so live admission drift is
    # diagnosed precisely.
    if (
        candidate["arrival_traces"] != expected["arrival_traces"]
        or candidate.get("arrival_trace_sha256") != expected["arrival_trace_sha256"]
    ):
        raise _fail("arrival trace invalid")

    blocks = candidate.get("blocks")
    if not isinstance(blocks, list) or len(blocks) != len(expected["blocks"]):
        raise _fail("block inventory invalid")
    # K constrains actual request-level LLM admission and must be identical
    # for every row, rather than only appearing correct in the plan header.
    for block in blocks:
        if not isinstance(block, Mapping) or block.get("global_llm_admission_k") != 2:
            raise _fail("global LLM admission invalid")
    if candidate != expected:
        raise _fail("plan inventory or payload drift")
    return candidate


__all__ = [
    "ALIGNED_DEVELOPMENT_HISTORIES",
    "ALIGNED_METHODS",
    "AlignedPlanError",
    "build_aligned_development_plan",
    "verify_aligned_development_plan",
]
