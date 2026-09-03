"""Fail-closed block and campaign reducer for the MAB v1.3 experiment."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


class CampaignReductionError(ValueError):
    """Formal artifacts cannot be combined into the requested table."""


METHOD_CLASSES = {
    "B0": "ORDERED_REFERENCE",
    "B1": "RELAXED_ORDER_REFERENCE",
    "V6": "ORDERED_REFINEMENT",
    # Public three-arm identities. The short names above remain accepted for
    # historical offline fixtures, while new artifacts use these names.
    "GRAPHITI_UPSTREAM_SERIAL": "GRAPHITI_UPSTREAM",
    "RELAXED_ORDER_PARALLEL": "RELAXED_ORDER",
    "MEMBIND_V6_1": "MEMBIND_V6_1",
    "GRAPHITI_SERIAL_SHARED_BOUNDED_SO": "GRAPHITI_SERIAL_SHARED_BOUNDED_SO",
    "RELAXED_ORDER_SHARED_BOUNDED_SO": "RELAXED_ORDER_SHARED_BOUNDED_SO",
    "MEMBIND_V6_1_SHARED_BOUNDED_SO": "MEMBIND_V6_1_SHARED_BOUNDED_SO",
    "GRAPHITI_SERIAL_UPSTREAM_CORE_MAB8192": "GRAPHITI_UPSTREAM",
    "GRAPHITI_ASYNC_UPSTREAM_CORE_MAB8192": "RELAXED_ORDER",
    "MEMBIND_V6_1_UPSTREAM_CORE_MAB8192": "MEMBIND_V6_1",
}
ORDERS = (("B0", "B1", "V6"), ("B1", "V6", "B0"), ("V6", "B0", "B1"))


def arm_order_for(context_index: int, repeat_index: int) -> tuple[str, str, str]:
    if isinstance(context_index, bool) or not isinstance(context_index, int) or context_index < 0:
        raise CampaignReductionError("context index is invalid")
    if isinstance(repeat_index, bool) or not isinstance(repeat_index, int) or repeat_index < 0:
        raise CampaignReductionError("repeat index is invalid")
    return ORDERS[(context_index + repeat_index) % 3]


def _status(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CampaignReductionError(f"{field} is invalid")
    return value


def validate_block_status(block: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(block, Mapping):
        raise CampaignReductionError("block is not an object")
    value = dict(block)
    method = _status(value.get("method"), "method")
    if method not in METHOD_CLASSES:
        raise CampaignReductionError("method is not a frozen arm")
    if method in {"GRAPHITI_UPSTREAM_SERIAL", "RELAXED_ORDER_PARALLEL"}:
        raise CampaignReductionError(
            "strict A0 Native substrate is compatibility-only and cannot enter formal reduction"
        )
    if method in {
        "GRAPHITI_SERIAL_SHARED_BOUNDED_SO",
        "RELAXED_ORDER_SHARED_BOUNDED_SO",
        "MEMBIND_V6_1_SHARED_BOUNDED_SO",
    } and value.get("formal_eligible") is not True:
        raise CampaignReductionError("shared formal arm is missing formal_eligible seal")
    if value.get("scope") != "FORMAL":
        raise CampaignReductionError("prefix/diagnostic block cannot enter formal reducer")
    artifact_status = "PASS" if value.get("sealed") is True and value.get("artifact_status") == "PASS" else "FAIL"
    expected = value.get("expected_episode_count")
    submitted = value.get("submitted_count")
    completed = value.get("completed_count")
    fixed_work = isinstance(expected, int) and expected > 0 and submitted == expected and completed == expected
    order = value.get("order_contract_status")
    if method == "B1":
        contract = fixed_work and order in {"NOT_REQUIRED", "PASS"}
    else:
        contract = fixed_work and order == "PASS"
    contract_status = "PASS" if value.get("contract_status") == "PASS" and contract else "FAIL"
    if method == "V6":
        refinement_status = "PASS" if value.get("refinement_status") == "PASS" else "FAIL"
    else:
        refinement_status = "N/A"
    quality_status = _status(value.get("quality_status"), "quality_status")
    if artifact_status != "PASS":
        inclusion = "EXCLUDED_ARTIFACT"
        reason = "ARTIFACT_INVALID"
    elif contract_status != "PASS":
        inclusion = "EXCLUDED_CONTRACT"
        reason = "CONTRACT_INVALID"
    elif refinement_status == "FAIL":
        inclusion = "EXCLUDED_REFINEMENT"
        reason = "REFINEMENT_INVALID"
    else:
        inclusion = "PERFORMANCE_AND_CORRECTNESS"
        reason = None
        if quality_status == "PASS":
            inclusion = "ALL_PRIMARY_TABLES"
    return {
        "block_id": value.get("block_id"),
        "method": method,
        "context_id": value.get("context_id"),
        "context_index": value.get("context_index"),
        "repeat": value.get("repeat"),
        "artifact_status": artifact_status,
        "contract_status": contract_status,
        "refinement_status": refinement_status,
        "quality_status": quality_status,
        "inclusion_status": inclusion,
        "exclusion_reason": reason,
        "t_build_ns": value.get("t_build_ns") if inclusion not in {"EXCLUDED_ARTIFACT", "EXCLUDED_CONTRACT", "EXCLUDED_REFINEMENT"} else None,
    }


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _qa_summary(blocks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_context: dict[str, list[float]] = defaultdict(list)
    for block in blocks:
        qa = block.get("qa")
        if not isinstance(qa, Mapping) or block.get("quality_status") != "PASS":
            continue
        score = qa.get("overall_accuracy")
        if isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(float(score)):
            by_context[str(block.get("context_id"))].append(float(score))
    context_scores = {key: _mean(values) for key, values in sorted(by_context.items())}
    valid = [value for value in context_scores.values() if value is not None]
    return {
        "context_count": len(context_scores),
        "overall_accuracy": _mean([float(v) for values in by_context.values() for v in values]),
        "equal_context_macro_accuracy": _mean([float(v) for v in valid]),
        "by_context": context_scores,
        "invalid_quality_is_null": True,
    }


def reduce_campaign(
    blocks: Sequence[Mapping[str, Any]],
    *,
    formal_context_count: int = 5,
    required_methods: Sequence[str] = ("B0", "B1", "V6"),
) -> dict[str, Any]:
    if isinstance(blocks, (str, bytes)) or not isinstance(blocks, Sequence) or not blocks:
        raise CampaignReductionError("no blocks supplied")
    if formal_context_count <= 0:
        raise CampaignReductionError("formal context count is invalid")
    statuses: list[dict[str, Any]] = []
    values: list[dict[str, Any]] = []
    for block in blocks:
        status = validate_block_status(block)
        statuses.append(status)
        values.append(dict(block))
    observed_contexts = {int(item["context_index"]) for item in statuses if isinstance(item.get("context_index"), int)}
    expected_contexts = set(range(formal_context_count))
    if observed_contexts != expected_contexts:
        raise CampaignReductionError("formal reducer requires all five contexts")
    authority_ids = {str(item.get("dataset_authority_sha256")) for item in values}
    workload_ids = {str(item.get("workload_hash")) for item in values}
    if len(authority_ids) != 1:
        raise CampaignReductionError("DATASET_MISMATCH")
    if len(workload_ids) != 1:
        raise CampaignReductionError("WORKLOAD_MISMATCH")
    exclusions: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []
    for status, block in zip(statuses, values):
        if status["inclusion_status"].startswith("EXCLUDED"):
            exclusions.append({"block_id": status["block_id"], "reason": status["exclusion_reason"]})
        else:
            valid.append(block)
    by_context: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for block in valid:
        by_context[int(block["context_index"])].append(block)
    # Config identity is checked within each paired context, so an invalid arm
    # remains auditable instead of poisoning unrelated blocks.
    for context_index, context_blocks in by_context.items():
        configs = {str(item.get("config_hash")) for item in context_blocks}
        if len(configs) > 1:
            reference = next(iter(configs))
            for block in context_blocks:
                if str(block.get("config_hash")) != reference:
                    exclusions.append({"block_id": block.get("block_id"), "reason": "CONFIG_MISMATCH"})
                    valid.remove(block)
    required = set(required_methods)
    available = {str(item.get("method")) for item in valid}
    if not required.issubset(available):
        status = "PARTIAL"
    else:
        status = "PASS" if not exclusions else "PARTIAL"
    primary: dict[str, dict[str, Any]] = {}
    b0_values = [int(item["t_build_ns"]) for item in valid if item.get("method") == "B0" and isinstance(item.get("t_build_ns"), int)]
    b0_mean = _mean([float(value) for value in b0_values])
    for method in sorted(available):
        durations = [int(item["t_build_ns"]) for item in valid if item.get("method") == method and isinstance(item.get("t_build_ns"), int)]
        mean = _mean([float(value) for value in durations])
        primary[method] = {
            "valid_block_count": len(durations),
            "t_build_ns": mean,
            "speedup_vs_b0": (b0_mean / mean) if b0_mean and mean else None,
            "durable_goodput": (sum(int(item["expected_episode_count"]) for item in valid if item.get("method") == method) / sum(durations)) if durations else None,
        }
    mechanism = {
        method: {
            "inversions": sum(int(item.get("inversion_count", 0)) for item in valid if item.get("method") == method),
            "block_count": sum(item.get("method") == method for item in valid),
        }
        for method in sorted(available)
    }
    return {
        "schema_version": "membind.v1.3.campaign-reduction.v1",
        "status": status,
        "formal_context_count": formal_context_count,
        "valid_block_count": len(valid),
        "block_statuses": statuses,
        "exclusion_ledger": exclusions,
        "primary": primary,
        "quality": _qa_summary(valid),
        "mechanism": mechanism,
        "claim_boundary": "same pinned MAB workload; QA is an end-to-end guard, not concurrency proof",
    }


__all__ = ["CampaignReductionError", "arm_order_for", "reduce_campaign", "validate_block_status"]
