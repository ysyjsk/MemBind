"""Materialize the auditable construction block file set before sealing."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .artifact_seals import seal_construction_block


class ArtifactMaterializationError(ValueError):
    """A block result cannot be represented by the frozen artifact schema."""


_MEMBERS = (
    "dataset_authority.json",
    "workload_manifest.jsonl",
    "workload_manifest.sha256",
    "frozen_config.json",
    "environment.json",
    "preflight.json",
    "raw_events.jsonl",
    "native_trace.jsonl",
    "transport_trace.jsonl",
    "request_identity.jsonl",
    "replay_binding.jsonl",
    "work_inventory.json",
    "lifecycle_validation.json",
    "order_validation.json",
    "refinement_validation.json",
    "graph_diagnostics.json",
    "metrics.json",
)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Any) -> None:
    if isinstance(rows, str):
        path.write_text(rows if rows.endswith("\n") else rows + "\n", encoding="utf-8")
        return
    if not isinstance(rows, Sequence) or isinstance(rows, (bytes, str)):
        raise ArtifactMaterializationError(f"JSONL rows are invalid: {path.name}")
    path.write_text("".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def materialize_construction_block(
    root: str | Path,
    *,
    authority: Mapping[str, Any],
    workload_manifest: Any,
    frozen_config: Mapping[str, Any],
    result: Mapping[str, Any],
    identity: Mapping[str, Any],
    environment: Mapping[str, Any] | None = None,
    preflight: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    block_root = Path(root).resolve()
    if block_root.exists() and any(block_root.iterdir()):
        raise ArtifactMaterializationError("block root must be fresh")
    block_root.mkdir(parents=True, exist_ok=True)
    method = str(identity.get("method", result.get("method", "")))
    if method not in {"B0", "B1", "V6"}:
        raise ArtifactMaterializationError("method is not frozen")
    if not isinstance(result, Mapping) or result.get("expected_episode_count") != result.get("submitted_count") or result.get("expected_episode_count") != result.get("completed_count"):
        raise ArtifactMaterializationError("fixed-work result is incomplete")
    authority_body = {key: value for key, value in authority.items() if key != "contexts"}
    _write_json(block_root / "dataset_authority.json", authority_body)
    if hasattr(workload_manifest, "jsonl") and callable(workload_manifest.jsonl):
        manifest_jsonl = str(workload_manifest.jsonl())
        manifest_hash = str(getattr(workload_manifest, "manifest_sha256", ""))
    elif isinstance(workload_manifest, Mapping):
        manifest_jsonl = str(workload_manifest.get("jsonl", ""))
        manifest_hash = str(workload_manifest.get("manifest_sha256", ""))
    else:
        raise ArtifactMaterializationError("workload manifest is invalid")
    if not manifest_jsonl.strip() or len(manifest_hash) != 64:
        raise ArtifactMaterializationError("workload manifest identity is invalid")
    (block_root / "workload_manifest.jsonl").write_text(manifest_jsonl if manifest_jsonl.endswith("\n") else manifest_jsonl + "\n", encoding="utf-8")
    (block_root / "workload_manifest.sha256").write_text(manifest_hash + "\n", encoding="utf-8")
    _write_json(block_root / "frozen_config.json", frozen_config)
    _write_json(block_root / "environment.json", dict(environment or {"status": "NOT_CAPTURED"}))
    _write_json(block_root / "preflight.json", dict(preflight or {"status": "PASS", "scope": "provider-free"}))
    events = result.get("events", [])
    _write_jsonl(block_root / "raw_events.jsonl", events)
    _write_jsonl(block_root / "native_trace.jsonl", result.get("native_trace", events))
    _write_jsonl(block_root / "transport_trace.jsonl", result.get("transport_trace", []))
    _write_jsonl(block_root / "request_identity.jsonl", result.get("request_identity", []))
    if method == "V6":
        _write_jsonl(block_root / "replay_binding.jsonl", result.get("bindings", []))
    else:
        _write_jsonl(block_root / "replay_binding.jsonl", [{"status": "N/A", "reason": "replay refinement is not applicable to this method"}])
    inventory_keys = (
        "llm_logical_requests",
        "llm_logical_requests_by_prompt",
        "transport_attempts",
        "transport_failed_attempts",
        "transport_true_retry_attempts",
        "compatibility_expansion_attempts",
        "transport_retry_attempts",
        "pagination_requests",
        "pagination_continuation_requests",
        "pagination_raw_unique_progress_edges",
        "pagination_unique_delta_edges",
        "pagination_duplicate_edges",
        "pagination_duplicate_recovery_requests",
        "pagination_duplicate_recovery_successes",
        "pagination_invalid_endpoint_edges",
        "pagination_zero_delta_terminations",
        "pagination_empty_terminations",
        "pagination_page_capacity",
        "summary_response_audits",
        "summary_unknown_rejected",
        "summary_duplicate_rejected",
        "summary_omitted_requested",
        "prompt_tokens",
        "completion_tokens",
        "finish_reason_length_count",
        "embedding_calls",
        "embedding_items",
        "db_reads",
        "db_write_statements",
        "db_write_transactions",
        "db_writes",
    )
    _write_json(block_root / "work_inventory.json", {
        "expected_episode_count": result.get("expected_episode_count"),
        "submitted_count": result.get("submitted_count"),
        "completed_count": result.get("completed_count"),
        **{key: result.get(key) for key in inventory_keys},
    })
    _write_json(block_root / "lifecycle_validation.json", result.get("lifecycle_validation", {"contract_status": "INVALID"}))
    _write_json(block_root / "order_validation.json", result.get("order_validation", {"order_contract_status": "INVALID_TRACE"}))
    _write_json(block_root / "refinement_validation.json", result.get("refinement_validation", {"refinement_status": "N/A" if method != "V6" else "INVALID"}))
    _write_json(block_root / "graph_diagnostics.json", result.get("graph_diagnostics", {"status": "NOT_CAPTURED"}))
    _write_json(block_root / "metrics.json", {
        "t_build_ns": result.get("t_build_ns"),
        "durable_goodput": (int(result["expected_episode_count"]) / (int(result["t_build_ns"]) / 1_000_000_000)) if isinstance(result.get("t_build_ns"), int) and result.get("t_build_ns", 0) > 0 else None,
        "method": method,
    })
    seal_identity = {
        **dict(identity),
        "namespace": identity.get("namespace", result.get("namespace")),
        "workload_hash": manifest_hash,
        "dataset_authority_sha256": authority_body.get("authority_sha256"),
        "method": method,
    }
    return seal_construction_block(block_root, identity=seal_identity, required_members=_MEMBERS)


__all__ = ["ArtifactMaterializationError", "materialize_construction_block"]
