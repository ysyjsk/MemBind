"""Outer three-arm campaign planning, fresh-attempt namespaces, and ledger."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .campaign_reducer import METHOD_CLASSES, arm_order_for


class CampaignOrchestrationError(ValueError):
    """A campaign request violates a frozen protocol boundary."""


METHODS = ("B0", "B1", "V6")
PINNED_REVISION = "7ea066982b140a19337e17e60d45d4076e042faf"
PINNED_SOURCE = "longmemeval_s*"


def build_campaign_plan(
    authority: Mapping[str, Any],
    *,
    context_indices: Sequence[int] | None = None,
    scope: str = "FORMAL",
    repeats: int = 1,
    session_limit: int | None = None,
) -> dict[str, Any]:
    if authority.get("revision") != PINNED_REVISION or authority.get("source_filter") != PINNED_SOURCE:
        raise CampaignOrchestrationError("dataset identity is not frozen")
    if authority.get("context_count") != 5 or len(authority.get("context_ids", ())) != 5:
        raise CampaignOrchestrationError("formal plan requires all five contexts")
    if scope not in {"FORMAL", "ENGINEERING_DIAGNOSTIC"}:
        raise CampaignOrchestrationError("scope is invalid")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats <= 0:
        raise CampaignOrchestrationError("repeats is invalid")
    selected = tuple(range(5)) if context_indices is None else tuple(context_indices)
    if len(set(selected)) != len(selected) or any(index not in range(5) for index in selected):
        raise CampaignOrchestrationError("context indices must be within 0..4")
    if scope == "FORMAL" and selected != tuple(range(5)):
        raise CampaignOrchestrationError("formal plan requires context indices 0..4; prefix/subset is forbidden")
    if scope == "FORMAL" and session_limit is not None:
        raise CampaignOrchestrationError("formal plan cannot use a prefix session limit")
    if session_limit is not None and (isinstance(session_limit, bool) or not isinstance(session_limit, int) or session_limit <= 0):
        raise CampaignOrchestrationError("session limit is invalid")
    blocks: list[dict[str, Any]] = []
    for context_index in selected:
        for repeat in range(repeats):
            order = arm_order_for(context_index, repeat)
            for method in order:
                block_id = f"{scope.lower()}-context-{context_index}-repeat-{repeat}-{method}"
                blocks.append({
                    "block_id": block_id,
                    "context_index": context_index,
                    "context_id": str(authority["context_ids"][context_index]),
                    "repeat": repeat,
                    "method": method,
                    "semantic_class": METHOD_CLASSES[method],
                    "scope": scope,
                    "session_limit": session_limit,
                    "dataset_authority_sha256": str(authority.get("authority_sha256", "")),
                    # A UUID is deliberately per attempt.  It is not in the
                    # method-independent workload hash.
                    "namespace": f"mab-v13-{method.lower()}-c{context_index}-r{repeat}-{uuid.uuid4().hex[:12]}",
                    "attempt_id": uuid.uuid4().hex,
                })
    return {
        "schema_version": "membind.v1.3.campaign-plan.v1",
        "scope": scope,
        "dataset_revision": PINNED_REVISION,
        "source_filter": PINNED_SOURCE,
        "context_indices": list(selected),
        "repeats": repeats,
        "session_limit": session_limit,
        "blocks": blocks,
    }


def _append(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


async def run_campaign(
    plan: Mapping[str, Any],
    *,
    output_root: str | Path,
    block_runner: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]] | Mapping[str, Any]],
) -> dict[str, Any]:
    if plan.get("schema_version") != "membind.v1.3.campaign-plan.v1":
        raise CampaignOrchestrationError("campaign plan is invalid")
    blocks = plan.get("blocks")
    if not isinstance(blocks, Sequence) or isinstance(blocks, (str, bytes)) or not blocks:
        raise CampaignOrchestrationError("campaign plan has no blocks")
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    plan_path = root / "campaign_plan.json"
    if not plan_path.exists():
        plan_path.write_text(json.dumps(dict(plan), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    ledger_path = root / "campaign_ledger.jsonl"
    completed = failed = 0
    for raw in blocks:
        if not isinstance(raw, Mapping):
            raise CampaignOrchestrationError("campaign block is invalid")
        block = dict(raw)
        started = asyncio.get_running_loop().time()
        row = {
            "event": "ATTEMPT_START",
            "block_id": block.get("block_id"),
            "attempt_id": block.get("attempt_id"),
            "namespace": block.get("namespace"),
            "method": block.get("method"),
            "context_index": block.get("context_index"),
            "repeat": block.get("repeat"),
            "scope": block.get("scope"),
        }
        _append(ledger_path, row)
        try:
            result = block_runner(block)
            if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
                result = await result
            if not isinstance(result, Mapping):
                raise CampaignOrchestrationError("block runner returned non-object")
        except Exception as exc:
            failed += 1
            _append(ledger_path, {
                **row,
                "event": "ATTEMPT_FAILURE",
                "status": "FAILED",
                "failure_class": type(exc).__name__,
                "error": str(exc)[:500],
                "elapsed_s": asyncio.get_running_loop().time() - started,
            })
        else:
            completed += 1
            _append(ledger_path, {
                **row,
                "event": "ATTEMPT_COMPLETE",
                "status": str(result.get("status", "PASS")),
                "result": dict(result),
                "elapsed_s": asyncio.get_running_loop().time() - started,
            })
    summary = {
        "schema_version": "membind.v1.3.campaign-summary.v1",
        "scope": plan.get("scope"),
        "planned_block_count": len(blocks),
        "completed_block_count": completed,
        "failed_block_count": failed,
        "ledger_path": str(ledger_path),
        "status": "PASS" if failed == 0 else "PARTIAL",
    }
    (root / "campaign_summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return summary


__all__ = ["CampaignOrchestrationError", "build_campaign_plan", "run_campaign"]
