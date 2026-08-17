"""Small, deterministic reducers for the Quality Evaluation v1 overlay.

This module deliberately contains no network or construction code.  It makes
the U0 development stop decision and applies the same retrieval/Reader/Judge
result schema to U0, A0, and P(C=2).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .artifacts import atomic_write_json, payload_sha256
from .baseline_suite import DEVELOPMENT_HISTORIES


METHODS = ("U0", "A0", "P(C=2)")
BUNDLE_SCHEMA = "membind.paper-eval-v3.quality-v1-bundle.v1"


def _verified_artifact(value: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"Quality v1 {label} artifact is invalid")
    result = dict(value)
    if result.get("payload_sha256") != payload_sha256(
        {key: child for key, child in result.items() if key != "payload_sha256"}
    ):
        raise ValueError(f"Quality v1 {label} artifact hash mismatch")
    return result


def _read(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError(f"Quality v1 {label} is unreadable") from None
    if not isinstance(value, dict):
        raise ValueError(f"Quality v1 {label} is invalid")
    return value


def _bundle(
    *, public_artifact: Mapping[str, Any], private_artifact: Mapping[str, Any]
) -> dict[str, Any]:
    public = _verified_artifact(public_artifact, label="public")
    private = _verified_artifact(private_artifact, label="private")
    if public.get("private_payload_sha256") != private["payload_sha256"]:
        raise ValueError("Quality v1 public/private binding mismatch")
    result: dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA,
        "public_artifact": public,
        "private_artifact": private,
    }
    result["bundle_sha256"] = payload_sha256(result)
    return result


def _verified_bundle(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Quality v1 sealed bundle is invalid")
    result = dict(value)
    if (
        result.get("schema_version") != BUNDLE_SCHEMA
        or result.get("bundle_sha256")
        != payload_sha256(
            {key: child for key, child in result.items() if key != "bundle_sha256"}
        )
    ):
        raise ValueError("Quality v1 sealed bundle hash mismatch")
    expected = _bundle(
        public_artifact=result.get("public_artifact", {}),
        private_artifact=result.get("private_artifact", {}),
    )
    if result != expected:
        raise ValueError("Quality v1 sealed bundle content mismatch")
    return result


def persist_quality_v1_bundle(
    attempt_root: Path,
    *,
    public_artifact: Mapping[str, Any],
    private_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal private+public together before publishing the public projection."""

    root = Path(attempt_root)
    candidate = _bundle(
        public_artifact=public_artifact,
        private_artifact=private_artifact,
    )
    bundle_path = root / "private_bundle.json"
    public_path = root / "public.json"
    if bundle_path.exists():
        if _verified_bundle(_read(bundle_path, label="sealed bundle")) != candidate:
            raise ValueError("Quality v1 existing sealed bundle drift")
    elif public_path.exists():
        raise ValueError("Quality v1 public projection lacks sealed bundle")
    else:
        atomic_write_json(bundle_path, candidate)
    public = dict(candidate["public_artifact"])
    if public_path.exists():
        if _read(public_path, label="public projection") != public:
            raise ValueError("Quality v1 existing public projection drift")
    else:
        atomic_write_json(public_path, public)
    return public


def load_or_restore_quality_v1_bundle(attempt_root: Path) -> dict[str, Any]:
    """Restore only the public projection; never issue another model request."""

    root = Path(attempt_root)
    bundle = _verified_bundle(
        _read(root / "private_bundle.json", label="sealed bundle")
    )
    public = dict(bundle["public_artifact"])
    public_path = root / "public.json"
    if public_path.exists():
        if _read(public_path, label="public projection") != public:
            raise ValueError("Quality v1 existing public projection drift")
    else:
        atomic_write_json(public_path, public)
    return public


def _rows(
    rows: Sequence[Mapping[str, Any]], *, methods: Sequence[str]
) -> list[dict[str, Any]]:
    if isinstance(rows, (str, bytes)) or isinstance(methods, (str, bytes)):
        raise ValueError("Quality v1 result inventory is invalid")
    selected_methods = tuple(methods)
    if not selected_methods or len(set(selected_methods)) != len(selected_methods):
        raise ValueError("Quality v1 method inventory is invalid")
    if any(method not in METHODS for method in selected_methods):
        raise ValueError("Quality v1 method inventory is invalid")
    values = [dict(value) for value in rows if isinstance(value, Mapping)]
    if len(values) != len(rows):
        raise ValueError("Quality v1 result inventory is invalid")
    expected = [
        (method, history_id)
        for method in selected_methods
        for history_id in DEVELOPMENT_HISTORIES
    ]
    observed = [(value.get("method"), value.get("history_id")) for value in values]
    if observed != expected:
        raise ValueError("Quality v1 result inventory is incomplete or reordered")
    return values


def _number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Quality v1 {field} is invalid")
    return float(value)


def decide_u0_freeze(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Freeze at >=2/4 only when all four Reader/Judge outputs are valid."""

    values = _rows(rows, methods=("U0",))
    valid = [value for value in values if value.get("judge_valid_denominator") == 1]
    correct = sum(
        _number(value.get("qa_accuracy"), field="QA accuracy") for value in valid
    )
    if len(valid) != len(DEVELOPMENT_HISTORIES):
        decision = "BLOCK_PIPELINE_INVALID"
    elif correct >= 2:
        decision = "FREEZE_QUALITY_EVALUATION_V1"
    else:
        decision = "STOP_LOW_QA_SIGNAL"
    return {
        "schema_version": "membind.paper-eval-v3.quality-v1-u0-decision.v1",
        "decision": decision,
        "question_count": len(values),
        "valid_denominator": len(valid),
        "correct_count": int(correct),
        "qa_accuracy": correct / len(valid) if valid else None,
        "threshold_policy": "FREEZE_IF_ALL_4_VALID_AND_CORRECT_COUNT_GTE_2",
        "development_only": True,
    }


def summarize_quality_v1(
    rows: Sequence[Mapping[str, Any]], *, methods: Sequence[str] = METHODS
) -> dict[str, Any]:
    """Macro-reduce the non-saturating metrics under one common inventory."""

    selected_methods = tuple(methods)
    values = _rows(rows, methods=selected_methods)
    by_method: dict[str, dict[str, Any]] = {}
    for method in selected_methods:
        selected = [value for value in values if value["method"] == method]
        valid = [
            value for value in selected if value.get("judge_valid_denominator") == 1
        ]

        def session_metric(name: str) -> float:
            return sum(
                _number(
                    value.get("session_metrics", {}).get(name),
                    field=f"session {name}",
                )
                for value in selected
            ) / len(selected)

        temporal_names = (
            "stale_fact_count",
            "active_fact_count",
            "future_fact_count",
            "conflicting_relation_group_count",
            "stale_ranked_before_latest_valid_count",
        )
        temporal = {
            f"{name}_macro": sum(
                _number(
                    value.get("temporal_diagnostics", {}).get(name),
                    field=f"temporal {name}",
                )
                for value in selected
            )
            / len(selected)
            for name in temporal_names
        }
        by_method[method] = {
            "question_count": len(selected),
            "valid_judge_count": len(valid),
            "invalid_judge_count": len(selected) - len(valid),
            "qa_accuracy": (
                sum(
                    _number(value.get("qa_accuracy"), field="QA accuracy")
                    for value in valid
                )
                / len(valid)
                if valid
                else None
            ),
            **{
                f"recall_at_{cutoff}_macro": session_metric(f"recall_at_{cutoff}")
                for cutoff in (1, 3, 5, 10)
            },
            "mrr_macro": session_metric("mrr"),
            "ndcg_at_10_macro": session_metric("ndcg_at_10"),
            **temporal,
        }
    return {
        "schema_version": "membind.paper-eval-v3.quality-v1-summary.v1",
        "methods": list(selected_methods),
        "question_count": len(values),
        "by_method": by_method,
        "fact_gold_labels_available": False,
        "edge_metrics_scope": "PROVENANCE_PROXY_NOT_GOLD_FACT_RECALL",
        "quality_latency_included_in_construction_metrics": False,
        "claim_scope": "DEVELOPMENT_DIAGNOSTIC_NOT_PAPER_SIGNIFICANCE",
    }


__all__ = [
    "BUNDLE_SCHEMA",
    "METHODS",
    "decide_u0_freeze",
    "load_or_restore_quality_v1_bundle",
    "persist_quality_v1_bundle",
    "summarize_quality_v1",
]
