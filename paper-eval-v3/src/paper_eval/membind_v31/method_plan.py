"""Pure, read-only projection from an accepted APC plan to six v3.1 blocks."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from paper_eval.apc_aligned_baseline import (
    APC_BASELINE_HISTORIES,
    APC_BASELINE_METHODS,
    build_apc_aligned_baseline_plan,
    verify_apc_aligned_baseline_plan,
)
from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.baseline_acceptance import (
    ACCEPTANCE_SCHEMA,
    EXPECTED_BASELINE_RUN_ID,
)


SCHEMA = "membind.paper-eval-v3.membind-v31-method-plan.v2"
LIVE_AUTHORIZATION_SCOPE = "LIVE_EXECUTION_AUTHORIZED_BASELINE_MERGE_PENDING"
MEMBIND_V31_METHODS = ("MemBind-Barrier", "MemBind-FIFO", "MemBind")
REPRESENTATIVE_HISTORY = "07741c45"
COMPILE_WORKERS = 2
LOOKAHEAD = 2
BIND_WORKERS = 1
PREFIX_MATCH_UNIT = 16
DECODE_CONTEXT_PARALLEL_SIZE = 1
TRANSPORT_ADMISSION_BOUNDARY = "openai_chat_completions_create_attempt"
CACHE_AFFINITY_ORDER = (
    "longest_completed_provider_lcp_g_desc",
    "response_completion_recency_surrogate_desc",
    "current_ready_cohort_lcp_g_sum_desc",
    "source_sequence_fifo_asc",
)
_RUN_ID = re.compile(r"^membind-v31-[a-z0-9][a-z0-9-]{2,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_METHOD_SLUG = {
    "MemBind-Barrier": "barrier",
    "MemBind-FIFO": "fifo",
    "MemBind": "membind",
}
_BLOCK_IDENTITIES = (
    ("MemBind", "07741c45"),
    ("MemBind", "b6019101"),
    ("MemBind", "6071bd76"),
    ("MemBind", "a2f3aa27"),
    ("MemBind-Barrier", "07741c45"),
    ("MemBind-FIFO", "07741c45"),
)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(code)
    return value


def _verify_acceptance(
    value: Mapping[str, object], *, baseline_plan: Mapping[str, object]
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("baseline acceptance invalid")
    accepted = deepcopy(dict(value))
    stored = _sha(accepted.get("payload_sha256"), "baseline acceptance hash invalid")
    body = {key: child for key, child in accepted.items() if key != "payload_sha256"}
    if stored != payload_sha256(body):
        raise ValueError("baseline acceptance hash mismatch")
    expected_keys = {
        "schema_version",
        "status",
        "artifact_status",
        "semantic_verdicts",
        "run_id",
        "completed_block_count",
        "terminal_episode_count_per_method",
        "plan_payload_sha256",
        "source_manifest_sha256",
        "arrival_trace_sha256",
        "shared_execution_envelope_sha256",
        "global_llm_admission_k",
        "execution_identity_sha256",
        "block_result_payload_sha256s",
        "quality_run_id",
        "quality_report_payload_sha256",
        "quality_identity_sha256",
        "quality_runtime_identity_sha256",
        "payload_sha256",
    }
    if set(accepted) != expected_keys:
        raise ValueError("baseline acceptance inventory invalid")
    expected = {
        "schema_version": ACCEPTANCE_SCHEMA,
        "status": "PASS",
        "artifact_status": "SEALED_VALID",
        "run_id": EXPECTED_BASELINE_RUN_ID,
        "completed_block_count": 12,
        "terminal_episode_count_per_method": 188,
        "plan_payload_sha256": baseline_plan["payload_sha256"],
        "source_manifest_sha256": baseline_plan["source_manifest_sha256"],
        "arrival_trace_sha256": baseline_plan["arrival_trace_sha256"],
        "shared_execution_envelope_sha256": baseline_plan[
            "shared_execution_envelope_sha256"
        ],
        "global_llm_admission_k": baseline_plan["global_llm_admission_k"],
    }
    if any(accepted.get(key) != wanted for key, wanted in expected.items()):
        raise ValueError("baseline acceptance binding invalid")
    verdicts = accepted.get("semantic_verdicts")
    if not isinstance(verdicts, Mapping) or set(verdicts) != set(
        APC_BASELINE_METHODS
    ):
        raise ValueError("baseline semantic verdict inventory invalid")
    for method in APC_BASELINE_METHODS:
        verdict = verdicts.get(method)
        if (
            not isinstance(verdict, Mapping)
            or isinstance(verdict.get("direct_violations"), bool)
            or not isinstance(verdict.get("direct_violations"), int)
            or int(verdict["direct_violations"]) < 0
            or verdict.get("semantic_status")
            != (
                "SAFE"
                if int(verdict["direct_violations"]) == 0
                else "VIOLATION_OBSERVED"
            )
        ):
            raise ValueError("baseline semantic verdict invalid")
    _sha(accepted.get("quality_report_payload_sha256"), "baseline quality binding invalid")
    _sha(accepted.get("quality_identity_sha256"), "baseline quality binding invalid")
    _sha(accepted.get("quality_runtime_identity_sha256"), "baseline quality binding invalid")
    _sha(accepted.get("execution_identity_sha256"), "baseline runtime binding invalid")
    results = accepted.get("block_result_payload_sha256s")
    if (
        not isinstance(results, list)
        or len(results) != 12
        or len(set(results)) != 12
        or any(not isinstance(value, str) or _SHA256.fullmatch(value) is None for value in results)
        or not isinstance(accepted.get("quality_run_id"), str)
        or not accepted["quality_run_id"]
    ):
        raise ValueError("baseline acceptance terminal inventory invalid")
    return accepted


def _build(
    *,
    run_id: str,
    baseline: Mapping[str, Any],
    methodology_sha256: str,
    workplan_sha256: str,
) -> dict[str, Any]:
    blocks: list[dict[str, object]] = []
    for block_index, (method, history_id) in enumerate(_BLOCK_IDENTITIES):
        trace = baseline["arrival_traces"][history_id]
        namespace = f"pev3-{run_id}-{_METHOD_SLUG[method]}-{history_id}"
        cache_salt = payload_sha256(
            {"run_id": run_id, "block_index": block_index, "namespace": namespace}
        )
        blocks.append(
            {
                "block_index": block_index,
                "run_id": run_id,
                "method": method,
                "history_id": history_id,
                "source_count": len(baseline["history_source_sha256s"][history_id]),
                "namespace": namespace,
                "source_manifest_sha256": baseline["source_manifest_sha256"],
                "arrival_trace_sha256": baseline["arrival_trace_sha256"],
                "history_arrival_trace_sha256": trace[
                    "history_arrival_trace_sha256"
                ],
                "shared_execution_envelope_sha256": baseline[
                    "shared_execution_envelope_sha256"
                ],
                "global_llm_admission_k": baseline["global_llm_admission_k"],
                "compile_workers": COMPILE_WORKERS,
                "lookahead": LOOKAHEAD,
                "bind_workers": BIND_WORKERS,
                "prefix_match_unit": PREFIX_MATCH_UNIT,
                "decode_context_parallel_size": DECODE_CONTEXT_PARALLEL_SIZE,
                "transport_admission_boundary": TRANSPORT_ADMISSION_BOUNDARY,
                "cache_affinity_order": list(CACHE_AFFINITY_ORDER),
                "methodology_sha256": methodology_sha256,
                "workplan_sha256": workplan_sha256,
                "baseline_plan_payload_sha256": baseline["payload_sha256"],
                "cache_salt_sha256": cache_salt,
                "policy": {
                    "MemBind-Barrier": "FRONTIER_BARRIER",
                    "MemBind-FIFO": "FRONTIER_FIRST_FIFO",
                    "MemBind": "FRONTIER_FIRST_CACHE_AFFINITY",
                }[method],
                "headline_mem_bind_row": method == "MemBind",
                "representative_mechanism_ablation": history_id == REPRESENTATIVE_HISTORY,
            }
        )
    plan: dict[str, Any] = {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "data_role": "DEVELOPMENT_EXPOSED",
        "heldout_data_accessed": False,
        "baseline_run_id": baseline["run_id"],
        "baseline_plan_payload_sha256": baseline["payload_sha256"],
        "authorization_scope": LIVE_AUTHORIZATION_SCOPE,
        "methodology_sha256": methodology_sha256,
        "workplan_sha256": workplan_sha256,
        "methods": list(MEMBIND_V31_METHODS),
        "representative_history_id": REPRESENTATIVE_HISTORY,
        "histories": list(APC_BASELINE_HISTORIES),
        "history_source_sha256s": deepcopy(baseline["history_source_sha256s"]),
        "source_manifest_sha256": baseline["source_manifest_sha256"],
        "interarrival_ns": baseline["interarrival_ns"],
        "service_reference_ns": baseline["service_reference_ns"],
        "normalized_offered_load": baseline["normalized_offered_load"],
        "arrival_traces": deepcopy(baseline["arrival_traces"]),
        "arrival_trace_sha256": baseline["arrival_trace_sha256"],
        "shared_execution_envelope_sha256": baseline[
            "shared_execution_envelope_sha256"
        ],
        "global_llm_admission_k": baseline["global_llm_admission_k"],
        "compile_workers": COMPILE_WORKERS,
        "lookahead": LOOKAHEAD,
        "bind_workers": BIND_WORKERS,
        "prefix_match_unit": PREFIX_MATCH_UNIT,
        "decode_context_parallel_size": DECODE_CONTEXT_PARALLEL_SIZE,
        "transport_admission_boundary": TRANSPORT_ADMISSION_BOUNDARY,
        "cache_affinity_order": list(CACHE_AFFINITY_ORDER),
        "blocks": blocks,
    }
    plan["payload_sha256"] = payload_sha256(plan)
    return plan


def build_membind_v31_live_plan(
    *,
    run_id: str,
    verified_baseline_plan: Mapping[str, object],
    methodology_sha256: str,
    workplan_sha256: str,
) -> dict[str, Any]:
    """Derive the source-bound live plan without claiming baseline mergeability."""

    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("run id invalid")
    methodology = _sha(methodology_sha256, "methodology hash invalid")
    workplan = _sha(workplan_sha256, "workplan hash invalid")
    baseline_input = deepcopy(dict(verified_baseline_plan))
    sources = baseline_input.get("history_source_sha256s")
    if not isinstance(sources, Mapping):
        raise ValueError("baseline source inventory invalid")
    baseline_input["history_source_sha256s"] = {
        history: sources.get(history) for history in APC_BASELINE_HISTORIES
    }
    baseline = verify_apc_aligned_baseline_plan(baseline_input)
    if baseline.get("run_id") != EXPECTED_BASELINE_RUN_ID:
        raise ValueError("baseline run id invalid")
    return _build(
        run_id=run_id,
        baseline=baseline,
        methodology_sha256=methodology,
        workplan_sha256=workplan,
    )


def build_membind_v31_method_plan(
    *,
    run_id: str,
    verified_baseline_plan: Mapping[str, object],
    verified_baseline_acceptance: Mapping[str, object],
    methodology_sha256: str,
    workplan_sha256: str,
) -> dict[str, Any]:
    """Derive exactly six new blocks without reading or mutating live state."""

    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise ValueError("run id invalid")
    baseline_input = deepcopy(dict(verified_baseline_plan))
    sources = baseline_input.get("history_source_sha256s")
    if not isinstance(sources, Mapping):
        raise ValueError("baseline source inventory invalid")
    baseline_input["history_source_sha256s"] = {
        history: sources.get(history) for history in APC_BASELINE_HISTORIES
    }
    baseline = verify_apc_aligned_baseline_plan(baseline_input)
    if baseline.get("run_id") != EXPECTED_BASELINE_RUN_ID:
        raise ValueError("baseline run id invalid")
    acceptance = _verify_acceptance(
        verified_baseline_acceptance, baseline_plan=baseline
    )
    del acceptance
    return build_membind_v31_live_plan(
        run_id=run_id,
        verified_baseline_plan=baseline,
        methodology_sha256=methodology_sha256,
        workplan_sha256=workplan_sha256,
    )


def verify_membind_v31_method_plan(value: Mapping[str, object]) -> dict[str, Any]:
    """Recompute the baseline projection and the complete six-block identity."""

    if not isinstance(value, Mapping) or value.get("schema_version") != SCHEMA:
        raise ValueError("plan invalid")
    candidate = deepcopy(dict(value))
    sources = candidate.get("history_source_sha256s")
    if not isinstance(sources, Mapping):
        raise ValueError("source inventory invalid")
    ordered_sources = {
        history: sources.get(history) for history in APC_BASELINE_HISTORIES
    }
    baseline = build_apc_aligned_baseline_plan(
        run_id=candidate.get("baseline_run_id"),
        history_source_sha256s=ordered_sources,
        interarrival_ns=candidate.get("interarrival_ns"),
        execution_envelope_sha256=candidate.get(
            "shared_execution_envelope_sha256"
        ),
        service_reference_ns=candidate.get("service_reference_ns"),
        normalized_offered_load=candidate.get("normalized_offered_load"),
    )
    if baseline.get("payload_sha256") != candidate.get("baseline_plan_payload_sha256"):
        raise ValueError("baseline plan binding invalid")
    expected = _build(
        run_id=candidate.get("run_id"),
        baseline=baseline,
        methodology_sha256=_sha(
            candidate.get("methodology_sha256"), "methodology hash invalid"
        ),
        workplan_sha256=_sha(candidate.get("workplan_sha256"), "workplan hash invalid"),
    )
    if candidate != expected:
        raise ValueError("plan identity drift")
    namespaces = [row["namespace"] for row in candidate["blocks"]]
    baseline_namespaces = {row["namespace"] for row in baseline["blocks"]}
    if len(namespaces) != len(set(namespaces)) or set(namespaces) & baseline_namespaces:
        raise ValueError("fresh namespace identity invalid")
    return candidate


__all__ = [
    "BIND_WORKERS",
    "CACHE_AFFINITY_ORDER",
    "COMPILE_WORKERS",
    "DECODE_CONTEXT_PARALLEL_SIZE",
    "LOOKAHEAD",
    "LIVE_AUTHORIZATION_SCOPE",
    "MEMBIND_V31_METHODS",
    "PREFIX_MATCH_UNIT",
    "REPRESENTATIVE_HISTORY",
    "TRANSPORT_ADMISSION_BOUNDARY",
    "SCHEMA",
    "build_membind_v31_method_plan",
    "build_membind_v31_live_plan",
    "verify_membind_v31_method_plan",
]
