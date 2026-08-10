"""Bounded V2 model-oracle self-replay integration.

This is deliberately smaller than the V3 full M0 -> M2 smoke. It validates
that one M0 capture can be consumed by a fresh M0 read-only replay without
live model fallback, while the graph and retrieval work remain real.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

from current_state_gate import LiveAction, require_live_action
from embedding_identity import validate_embedding_model_manifest
from experiment_runner import ExperimentRunFailed, run_experiment
from graphiti_native import M0_NATIVE_SERIAL


V2_ORACLE_CACHE_ID = "v2_oracle_integration_001"


def build_v2_oracle_specs(attempt: str, question_id: str) -> list[dict[str, Any]]:
    attempt = str(attempt)
    question_id = str(question_id)
    cache_id = V2_ORACLE_CACHE_ID
    return [
        {
            "run_id": f"{attempt}_M0_capture",
            "lane": "correctness",
            "mode": "capture",
            "method": M0_NATIVE_SERIAL,
            "question_id": question_id,
            "repeat": 0,
            "cache_id": cache_id,
        },
        {
            "run_id": f"{attempt}_M0_replay",
            "lane": "correctness",
            "mode": "replay",
            "method": M0_NATIVE_SERIAL,
            "question_id": question_id,
            "repeat": 0,
            "cache_id": cache_id,
        },
    ]


def v2_integration_instance() -> dict[str, Any]:
    return {
        "question_id": "v2_integration_smoke",
        "question": "Where does Alice work?",
        "answer_session_ids": ["integration-s0"],
        "haystack_sessions": [
            [
                {
                    "role": "user",
                    "content": "Alice works at Adidas.",
                },
                {
                    "role": "assistant",
                    "content": "I will remember that Alice works at Adidas.",
                },
            ]
        ],
        "haystack_dates": ["2026-08-06T00:00:00Z"],
        "haystack_session_ids": ["integration-s0"],
    }


def validate_v2_oracle_statuses(
    capture: dict[str, Any], replay: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    if capture.get("status") != "success":
        errors.append("capture_status")
    if replay.get("status") != "success":
        errors.append("replay_status")
    capture_llm = (capture.get("llm_metrics") or {}).get("llm_call_count", 0)
    capture_embedding = (capture.get("embedding_metrics") or {}).get(
        "embedding_call_count", 0
    )
    if int(capture_llm) <= 0:
        errors.append("capture_llm_calls")
    if int(capture_embedding) <= 0:
        errors.append("capture_embedding_calls")
    if int(capture.get("rank_call_count", 0)) != 0:
        errors.append("capture_cross_encoder_calls")
    if int(replay.get("rank_call_count", 0)) != 0:
        errors.append("replay_cross_encoder_calls")
    if int(capture.get("post_run_node_count", -1)) != 0:
        errors.append("capture_cleanup")
    if int(replay.get("post_run_node_count", -1)) != 0:
        errors.append("replay_cleanup")
    replay_llm = (replay.get("llm_metrics") or {}).get("llm_call_count", -1)
    replay_embedding = (replay.get("embedding_metrics") or {}).get(
        "embedding_call_count", -1
    )
    if int(replay_llm) != 0:
        errors.append("replay_llm_calls")
    if int(replay_embedding) != 0:
        errors.append("replay_embedding_calls")
    if int(replay.get("unexpected_prompt_count", 0)) != 0:
        errors.append("replay_prompt_oracle_miss")
    if int(replay.get("unexpected_embedding_count", 0)) != 0:
        errors.append("replay_embedding_oracle_miss")
    if replay.get("live_fallback"):
        errors.append("replay_live_fallback")
    return errors


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def run_v2_oracle_integration(
    *,
    artifacts: str | Path,
    attempt: str = V2_ORACLE_CACHE_ID,
    instance: dict[str, Any] | None = None,
    arrival_interval_ms: int = 0,
    run_experiment_fn: Callable[..., Awaitable[dict[str, Any]]] = run_experiment,
    graphiti_factory: Callable[..., Any] | None = None,
    service_checker: Callable[[], Awaitable[Any]] | None = None,
    authorization_checker: Callable[..., Any] = require_live_action,
) -> dict[str, Any]:
    authorization_checker(LiveAction.V2_R)
    artifacts = Path(artifacts)
    instance = instance or v2_integration_instance()
    specs = build_v2_oracle_specs(attempt, str(instance["question_id"]))
    summary_path = artifacts / "diagnostics" / f"{attempt}_summary.json"
    summary: dict[str, Any] = {
        "schema_version": "membind.v2_oracle_integration.v1",
        "attempt": str(attempt),
        "cache_id": V2_ORACLE_CACHE_ID,
        "status": "preflight",
        "run_ids": [spec["run_id"] for spec in specs],
    }
    manifest_path = artifacts / "environment" / "embedding_model_fingerprint.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_embedding_model_manifest(manifest)
    except Exception as exc:
        unresolved = "unresolved runtime config" in str(exc)
        summary.update(
            {
                "status": "blocked" if unresolved else "failed",
                "gate_errors": [
                    "embedding_runtime_config" if unresolved else "embedding_manifest"
                ],
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with summary_path.open("x", encoding="utf-8") as handle:
            handle.write(
                json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True)
                + "\n"
            )
        return summary

    audit_path = artifacts / "diagnostics" / "model_oracle_audit.json"
    statuses: list[dict[str, Any]] = []
    cache_paths = [
        artifacts / "prompt_cache" / f"{V2_ORACLE_CACHE_ID}.jsonl",
        artifacts / "embedding_cache" / f"{V2_ORACLE_CACHE_ID}.jsonl",
    ]
    cache_sha256_before_replay: dict[str, str] = {}
    pre_replay_errors: list[str] = []
    summary["status"] = "running"
    try:
        for index, spec in enumerate(specs):
            kwargs: dict[str, Any] = {
                "spec": spec,
                "instance": instance,
                "arrival_interval_ms": int(arrival_interval_ms),
                "artifacts": artifacts,
                "service_checker": service_checker,
            }
            if graphiti_factory is not None:
                kwargs["graphiti_factory"] = graphiti_factory
            if run_experiment_fn is run_experiment:
                kwargs["authorization_checker"] = lambda *_args, **_kwargs: None
            if index == 0:
                kwargs["model_oracle_audit_path"] = audit_path
            try:
                status = await run_experiment_fn(**kwargs)
            except ExperimentRunFailed as exc:
                status = dict(exc.status)
            statuses.append(status)
            if status.get("status") != "success":
                break
            if index == 0:
                cache_sha256_before_replay = {
                    str(path): _sha256(path)
                    for path in cache_paths
                    if path.is_file()
                }
                if not audit_path.is_file():
                    pre_replay_errors.append("model_oracle_audit_missing")
                    break
                try:
                    audit = json.loads(audit_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pre_replay_errors.append("model_oracle_audit_invalid")
                    break
                summary["model_oracle_audit"] = {
                    "path": str(audit_path),
                    "sha256": _sha256(audit_path),
                    "rank_call_count": audit.get("rank_call_count"),
                    "cross_encoder_status": audit.get("cross_encoder_status"),
                    "blocks_v2": audit.get("blocks_v2"),
                }
                if (
                    audit.get("schema_version") != "membind.model_oracle_audit.v1"
                    or audit.get("run_id") != spec["run_id"]
                ):
                    pre_replay_errors.append("model_oracle_audit_invalid")
                    break
                if (
                    int(audit.get("rank_call_count", -1)) != 0
                    or audit.get("cross_encoder_status") != "not_invoked"
                    or audit.get("blocks_v2") is not False
                ):
                    pre_replay_errors.append("capture_cross_encoder_calls")
                    break
        summary["run_statuses"] = statuses
        if len(statuses) == 2:
            after = {
                str(path): _sha256(path)
                for path in cache_paths
                if path.is_file()
            }
            summary["cache_sha256_before_replay"] = cache_sha256_before_replay
            summary["cache_sha256_after_replay"] = after
            summary["cache_unchanged"] = (
                len(cache_sha256_before_replay) == len(cache_paths)
                and cache_sha256_before_replay == after
            )
            summary["graph_hash_equal"] = (
                statuses[0].get("canonical_graph_hash")
                == statuses[1].get("canonical_graph_hash")
            )
            summary["retrieval_metrics_equal"] = (
                statuses[0].get("retrieval_metrics")
                == statuses[1].get("retrieval_metrics")
            )
            summary["gate_errors"] = validate_v2_oracle_statuses(
                statuses[0], statuses[1]
            )
            summary["gate_errors"].extend(
                error
                for error, ok in (
                    ("cache_modified", summary["cache_unchanged"]),
                    ("graph_hash_mismatch", summary["graph_hash_equal"]),
                    ("retrieval_metrics_mismatch", summary["retrieval_metrics_equal"]),
                )
                if not ok
            )
        else:
            summary["gate_errors"] = pre_replay_errors or [
                "capture_did_not_complete"
            ]
        summary["status"] = "success" if not summary["gate_errors"] else "failed"
    except Exception as exc:
        summary["status"] = "failed"
        summary["gate_errors"] = [type(exc).__name__]
        summary["error"] = repr(exc)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
    return summary
