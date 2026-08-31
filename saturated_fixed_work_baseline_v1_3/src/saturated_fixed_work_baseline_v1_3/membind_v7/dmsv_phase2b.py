"""Provider-free DMSV Phase 2B closure checks.

This module is deliberately a reference/audit surface.  It does not perform
LLM calls, database writes, incremental admission, or online publication.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .graphiti_observer import canonical_digest


DMSV_PHASE2B_SCHEMA = "membind.dmsv.phase2b.closure.v1"
_PATHS = ("BV-NATIVE", "BV-VERSIONED", "BV-PERSISTENT")


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise TypeError("closure evidence must be bool or None")


def classify_base_view_path(
    *,
    path: str,
    ready_before_need: bool | None,
    blocks_authoritative_publication: bool | None,
    snapshot_identity_proven: bool | None,
    lifecycle_proven: bool | None,
    maintenance_cost_proven: bool | None,
) -> dict[str, Any]:
    """Classify one path without treating missing proof as a pass."""

    if path not in _PATHS:
        raise ValueError(f"unsupported base-view path: {path}")
    values = {
        "ready_before_need": _bool_or_none(ready_before_need),
        "blocks_authoritative_publication": _bool_or_none(blocks_authoritative_publication),
        "snapshot_identity_proven": _bool_or_none(snapshot_identity_proven),
        "lifecycle_proven": _bool_or_none(lifecycle_proven),
        "maintenance_cost_proven": _bool_or_none(maintenance_cost_proven),
    }
    missing = sorted(name for name, value in values.items() if value is None)
    if missing:
        status = "UNKNOWN"
        reason = "missing_proof"
    elif values["blocks_authoritative_publication"]:
        status = "FAIL"
        reason = "would_delay_or_block_authoritative_publication"
    elif not values["ready_before_need"]:
        status = "FAIL"
        reason = "base_view_not_ready_before_authoritative_need"
    elif not values["snapshot_identity_proven"]:
        status = "FAIL"
        reason = "snapshot_or_epoch_identity_not_proven"
    elif not values["lifecycle_proven"]:
        status = "FAIL"
        reason = "lifecycle_failure_or_gc_not_proven"
    elif not values["maintenance_cost_proven"]:
        status = "FAIL"
        reason = "materialization_or_maintenance_cost_not_proven"
    else:
        status = "PASS"
        reason = "legal_timed_base_view_path"
    return {
        "path": path,
        "status": status,
        "reason": reason,
        "missing_fields": missing,
        **values,
    }


def summarize_base_view_paths(paths: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Return a fail-closed aggregate verdict for all BV paths."""

    rows = []
    for path in _PATHS:
        evidence = paths.get(path)
        if not isinstance(evidence, Mapping):
            rows.append(
                {
                    "path": path,
                    "status": "UNKNOWN",
                    "reason": "path_evidence_missing",
                    "missing_fields": ["path_evidence"],
                }
            )
            continue
        rows.append(classify_base_view_path(path=path, **dict(evidence)))
    if any(row["status"] == "PASS" for row in rows):
        verdict = "MAIN_TRACK_CANDIDATE"
    elif all(row["status"] == "FAIL" for row in rows):
        verdict = "DMSV_BASE_VIEW_UNAVAILABLE"
    else:
        verdict = "BLOCKED"
    return {
        "schema_version": DMSV_PHASE2B_SCHEMA,
        "verdict": verdict,
        "paths": rows,
        "main_track_candidate": verdict == "MAIN_TRACK_CANDIDATE",
    }


def canonical_dedupe_request_identity(
    context: Mapping[str, Any],
    *,
    model_epoch: str,
    config_epoch: str,
    schema_epoch: str,
    index_epoch: str,
) -> dict[str, str]:
    """Digest Graphiti's actual dedupe prompt and its full logical closure."""

    if not isinstance(context, Mapping):
        raise TypeError("dedupe context must be a mapping")
    from graphiti_core.prompts import prompt_library

    prompt = prompt_library.dedupe_nodes.nodes(deepcopy(dict(context)))
    messages = [
        {"role": str(getattr(message, "role", "")), "content": str(getattr(message, "content", ""))}
        for message in prompt
    ]
    closure = {
        "messages": messages,
        "model_epoch": str(model_epoch),
        "config_epoch": str(config_epoch),
        "schema_epoch": str(schema_epoch),
        "index_epoch": str(index_epoch),
    }
    return {
        "request_digest": canonical_digest(closure),
        "prompt_digest": canonical_digest(messages),
        "closure_digest": canonical_digest(closure),
    }


def build_dominant_request_delta_matrix(
    base_context: Mapping[str, Any],
    *,
    model_epoch: str = "qwen3-8b-awq@frozen",
    config_epoch: str = "graphiti-0.29.3",
    schema_epoch: str = "entity-schema-v1",
    index_epoch: str = "neo4j-index-v1",
) -> list[dict[str, Any]]:
    """Construct the minimum adjacent-state request counterexample matrix."""

    base = deepcopy(dict(base_context))
    variants: dict[str, tuple[dict[str, Any], dict[str, str]]] = {}

    changed = deepcopy(base)
    changed["existing_nodes"][0]["summary"] = "changed candidate summary"
    variants["candidate_payload"] = (changed, {})

    changed = deepcopy(base)
    changed["existing_nodes"] = list(reversed(changed["existing_nodes"]))
    variants["topk_order"] = (changed, {})

    changed = deepcopy(base)
    changed["existing_nodes"].append(
        {"candidate_id": 99, "name": "New", "entity_types": ["Person"], "summary": "new"}
    )
    variants["topk_membership"] = (changed, {})

    changed = deepcopy(base)
    changed["previous_episodes"][0]["content"] = "changed prior episode"
    variants["previous_episodes"] = (changed, {})

    changed = deepcopy(base)
    changed["extracted_nodes"].append(
        {"id": 1, "name": "Bob", "entity_type": "Person", "entity_type_description": "person", "allowed_candidate_ids": []}
    )
    variants["unresolved_membership_and_batch_shape"] = (changed, {})

    changed = deepcopy(base)
    changed["episode_content"] = "changed current episode"
    variants["current_episode_content"] = (changed, {})

    variants["model_epoch"] = (deepcopy(base), {"model_epoch": f"{model_epoch}:changed"})
    variants["config_epoch"] = (deepcopy(base), {"config_epoch": f"{config_epoch}:changed"})
    variants["schema_epoch"] = (deepcopy(base), {"schema_epoch": f"{schema_epoch}:changed"})
    variants["index_epoch"] = (deepcopy(base), {"index_epoch": f"{index_epoch}:changed"})

    base_identity = canonical_dedupe_request_identity(
        base,
        model_epoch=model_epoch,
        config_epoch=config_epoch,
        schema_epoch=schema_epoch,
        index_epoch=index_epoch,
    )
    rows = []
    for name, (variant, identity_overrides) in variants.items():
        identity_kwargs = {
            "model_epoch": model_epoch,
            "config_epoch": config_epoch,
            "schema_epoch": schema_epoch,
            "index_epoch": index_epoch,
            **identity_overrides,
        }
        identity = canonical_dedupe_request_identity(
            variant,
            **identity_kwargs,
        )
        rows.append(
            {
                "change": name,
                "base_request_digest": base_identity["request_digest"],
                "variant_request_digest": identity["request_digest"],
                "request_equal": identity["request_digest"] == base_identity["request_digest"],
            }
        )
    return rows


__all__ = [
    "DMSV_PHASE2B_SCHEMA",
    "build_dominant_request_delta_matrix",
    "canonical_dedupe_request_identity",
    "classify_base_view_path",
    "summarize_base_view_paths",
]
