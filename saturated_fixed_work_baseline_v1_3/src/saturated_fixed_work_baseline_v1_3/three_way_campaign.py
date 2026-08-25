"""Provider-free triad harness and the live-runner integration boundary.

The harness is intentionally small: it exercises scheduling and evidence
contracts with fake callbacks, while the production adapters can plug the
same ``prepare``/``publish`` surface into Graphiti without changing the
validator or measurement boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from .campaign_reducer import METHOD_CLASSES
from .evaluation_contract import (
    TraceValidationError,
    validate_block_trace,
    validate_order_contract,
    validate_v6_bindings,
)


class CampaignRunnerError(ValueError):
    """The runner cannot execute a fixed-work block."""


def _canonical(value: Any) -> bytes:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _workload_hash(workload: Sequence[Mapping[str, Any]]) -> str:
    rows = []
    for expected, raw in enumerate(workload):
        if not isinstance(raw, Mapping):
            raise CampaignRunnerError("workload row is invalid")
        row = dict(raw)
        if row.get("source_sequence") != expected:
            raise CampaignRunnerError("workload source sequence is not contiguous")
        if row.get("arrival_offset_s", 0.0) != 0.0:
            raise CampaignRunnerError("v1.3 saturated workload requires zero arrival offsets")
        for field in ("context_id", "episode_id", "reference_time", "body"):
            if not isinstance(row.get(field), str) or not row[field]:
                raise CampaignRunnerError(f"workload field is missing: {field}")
        rows.append({
            "context_id": row["context_id"],
            "source_sequence": expected,
            "episode_id": row["episode_id"],
            "reference_time": row["reference_time"],
            "body": row["body"],
            "arrival_offset_s": 0.0,
        })
    if not rows:
        raise CampaignRunnerError("workload is empty")
    return hashlib.sha256(_canonical(rows)).hexdigest()


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def run_provider_free_block(
    workload: Sequence[Mapping[str, Any]],
    *,
    method: str,
    prepare: Callable[[int, str], Awaitable[Any] | Any],
    publish: Callable[[int, Any, str], Awaitable[Any] | Any],
) -> dict[str, Any]:
    """Run one fresh provider-free block for B0/B1/V6.

    All methods submit the same zero-offset inputs and end at the same durable
    publication event.  B1 publishes completion order, while V6 prepares in
    parallel and publishes in source order with explicit exact bindings.
    """

    if method not in METHOD_CLASSES:
        raise CampaignRunnerError("method is not frozen")
    rows = [dict(row) for row in workload]
    workload_hash = _workload_hash(rows)
    context_ids = {str(row["context_id"]) for row in rows}
    if len(context_ids) != 1:
        raise CampaignRunnerError("one block must contain one context")
    events: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    clock = max(1, time.monotonic_ns())

    def event(name: str, sequence: int | None = None) -> None:
        nonlocal clock
        clock += 1
        row = {"event": name, "event_index": len(events), "monotonic_ns": clock}
        if sequence is not None:
            row["source_sequence"] = sequence
        events.append(row)

    event("FORMAL_START")
    for row in rows:
        event("SUBMIT", int(row["source_sequence"]))

    async def invoke_prepare(sequence: int) -> Any:
        event("NATIVE_ENTER", sequence) if method != "V6" else None
        try:
            return await prepare(sequence, method)
        except Exception as exc:
            raise CampaignRunnerError(f"prepare failed for source {sequence}: {type(exc).__name__}") from exc

    async def invoke_publish(sequence: int, prepared: Any) -> None:
        try:
            await publish(sequence, prepared, method)
        except Exception as exc:
            raise CampaignRunnerError(f"publish failed for source {sequence}: {type(exc).__name__}") from exc
        event("PUBLICATION_DURABLE", sequence)

    if method == "B0":
        for row in rows:
            sequence = int(row["source_sequence"])
            prepared = await invoke_prepare(sequence)
            await invoke_publish(sequence, prepared)
    elif method == "B1":
        queue: asyncio.Queue[tuple[int, Any]] = asyncio.Queue()

        async def worker(sequence: int) -> None:
            prepared = await invoke_prepare(sequence)
            await queue.put((sequence, prepared))

        tasks = [asyncio.create_task(worker(int(row["source_sequence"]))) for row in rows]
        for _ in rows:
            sequence, prepared = await queue.get()
            await invoke_publish(sequence, prepared)
        await asyncio.gather(*tasks)
    else:  # V6: parallel capture, source-ordered replay/publication.
        tasks = [asyncio.create_task(prepare(int(row["source_sequence"]), method)) for row in rows]
        prepared_values = await asyncio.gather(*tasks)
        for row, prepared in zip(rows, prepared_values):
            sequence = int(row["source_sequence"])
            event("NATIVE_ENTER", sequence)
            request_hash = hashlib.sha256(_canonical({"sequence": sequence, "body": row["body"]})).hexdigest()
            response_hash = hashlib.sha256(_canonical(prepared)).hexdigest()
            bindings.append({
                "source_sequence": sequence,
                "callsite": "provider_free_prepare",
                "ordinal_within_episode": 0,
                "request_identity_hash": request_hash,
                "prepared_response_hash": response_hash,
                "native_request_hash": request_hash,
                "capture_count": 1,
                "consume_count": 1,
                "match_status": "EXACT_MATCH",
                "external_transport_attempted_during_replay": False,
            })
            await invoke_publish(sequence, prepared)
    event("CONSTRUCTION_SEAL")
    try:
        lifecycle = validate_block_trace(
            events,
            expected_source_count=len(rows),
            method=method,
            context_id=next(iter(context_ids)),
        )
    except TraceValidationError as exc:
        raise CampaignRunnerError(str(exc)) from exc
    refinement = {"refinement_status": "N/A"}
    if method == "V6":
        try:
            refinement = validate_v6_bindings(bindings)
        except TraceValidationError as exc:
            raise CampaignRunnerError(str(exc)) from exc
    order = validate_order_contract(events, expected_source_count=len(rows), method=method)
    return {
        "schema_version": "membind.v1.3.provider-free-block.v1",
        "method": method,
        "semantic_class": METHOD_CLASSES[method],
        "context_id": next(iter(context_ids)),
        "workload_hash": workload_hash,
        "expected_episode_count": len(rows),
        "submitted_count": len(rows),
        "completed_count": len(rows),
        "t_build_ns": lifecycle["t_build_ns"],
        "events": events,
        "lifecycle_validation": lifecycle,
        "order_validation": order,
        "refinement_validation": refinement,
        "bindings": bindings,
        "construction_seal": {"status": "CONSTRUCTION_SEALED", "workload_hash": workload_hash},
    }


__all__ = ["CampaignRunnerError", "run_provider_free_block"]
