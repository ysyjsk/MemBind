#!/usr/bin/env python3
"""Seal the transparent A1 opportunity-exposure protocol amendment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file  # noqa: E402


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"object_required:{path}")
    return value


def build_amendment(
    *,
    audit_path: Path,
    method_plan_path: Path,
    provider_path: Path,
    development_reference_path: Path | None = None,
) -> dict[str, Any]:
    audit = _read(audit_path)
    plan = _read(method_plan_path)
    provider = _read(provider_path)
    development_reference = (
        _read(development_reference_path)
        if development_reference_path is not None
        else None
    )
    audit_payload = audit.get("payload_sha256")
    if not isinstance(audit_payload, str) or audit_payload != payload_sha256(
        {key: value for key, value in audit.items() if key != "payload_sha256"}
    ):
        raise ValueError("audit_payload_hash_invalid")
    history_id = "07741c45"
    full_trace = "ff5f10b62d375dc7e3cf9963bc34c1277e913a58bf1f8fc29b1f7ad7a89f11a8"
    source_inventory = "8bcd9fe468bbf471f0a26847b658fc2466df3e14639f05b575a8f207a45a89ec"
    if audit.get("history_id") != history_id or audit.get("history_arrival_trace_sha256") != full_trace:
        raise ValueError("audit_history_identity_invalid")
    if audit.get("source_manifest_sha256") != source_inventory:
        raise ValueError("audit_source_inventory_identity_invalid")
    canonical_trace = plan.get("arrival_traces", {}).get(history_id, {}).get(
        "history_arrival_trace_sha256"
    )
    if canonical_trace != full_trace or plan.get("source_manifest_sha256") != source_inventory:
        raise ValueError("method_plan_identity_invalid")

    reference_binding: dict[str, Any] | None = None
    if development_reference_path is not None:
        if not isinstance(development_reference, dict):
            raise ValueError("development_reference_invalid")
        ref_digest = development_reference.get("payload_sha256")
        if not isinstance(ref_digest, str) or ref_digest != payload_sha256(
            {
                key: value
                for key, value in development_reference.items()
                if key != "payload_sha256"
            }
        ):
            raise ValueError("development_reference_payload_hash_invalid")
        if (
            development_reference.get("protocol_amendment_id") != "A1"
            or development_reference.get("history_id") != history_id
            or development_reference.get("source_count") != 20
        ):
            raise ValueError("development_reference_identity_invalid")
        reference_binding = {
            "absolute_path": str(development_reference_path.resolve()),
            "file_sha256": sha256_file(development_reference_path),
            "payload_sha256": ref_digest,
        }

    body: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v4.a1-protocol-amendment.v1",
        "status": "AUTHORIZED_DEVELOPMENT_ONLY",
        "formal_main_table_eligible": False,
        "protocol_amendment_id": "A1",
        "amendment_title": "Opportunity exposure from the first sealed NodeResolve window",
        "history_id": history_id,
        "development_source_count": 20,
        # Top-level aliases are part of the verifier contract; keeping them
        # here also makes a detached amendment self-describing.
        "arrival_trace_sha256": full_trace,
        "history_arrival_trace_sha256": full_trace,
        "source_manifest_sha256": source_inventory,
        "source_inventory_sha256": source_inventory,
        "source_prefix": "0..19",
        "source_inventory_scope": "full_v3.1_49_source_inventory_with_first_20_exposed",
        "sealed_reference": {
            "history_arrival_trace_sha256": full_trace,
            "arrival_trace_sha256": full_trace,
            "source_manifest_sha256": source_inventory,
            "shared_execution_envelope_sha256": audit.get(
                "shared_execution_envelope_sha256"
            ),
            "execution_identity_sha256": audit.get("execution_identity_sha256"),
            "provider_execution_envelope_sha256": provider.get("payload_sha256"),
            "method_plan_payload_sha256": plan.get("payload_sha256"),
        },
        "audit_binding": {
            "absolute_path": str(audit_path.resolve()),
            "file_sha256": sha256_file(audit_path),
            "payload_sha256": audit_payload,
            "schema_version": audit.get("schema_version"),
        },
        "development_reference_binding": reference_binding,
        "change": {
            "original_c01_six_source_stop_immutable": True,
            "original_prefix_0_5_opportunity_count": 0,
            "original_prefix_0_11_opportunity_count": 0,
            "first_opportunity_source": 12,
            "new_development_prefix_opportunity_count": 7,
            "full_workload_opportunity_count": 22,
            "rationale": "The sealed untreated v3.1 timing trace exposes no opportunity in 0..5 or 0..11; source 12 is the first opportunity, and 0..19 contains seven opportunities.",
            "selection_basis": "sealed_v3.1_timing_only_before_v4_treatment",
            "opportunity_filtering_applied_to_formal_experiment": False,
            "formal_experiment_trace_rule": "use_the_complete_original_arrival_trace; never_filter_by_opportunity",
        },
        "policy_invariants": {
            "candidate_id": "c01",
            "policy": "IDLE_SLOT_VALIDATED_SPEC",
            "global_llm_admission_k": 2,
            "speculation_distance": 1,
            "compile_workers": 2,
            "lookahead": 2,
            "bind_workers": 1,
            "frontier_first": True,
            "prompt_schema_model_backend_unchanged": True,
            "node_resolve_semantics_unchanged": True,
            "publication_order_unchanged": True,
            "retry_and_transport_admission_unchanged": True,
        },
        "scope_and_authority": {
            "authorized_live_runs": 1,
            "authorized_run": "c01/A1/source_count=20/history=07741c45",
            "other_candidates_authorized": False,
            "formal_four_history_authorized": False,
            "gpu_sweep_authorized": False,
            "k_or_w_tuning_authorized": False,
            "original_workplan_rewritten": False,
            "original_stop_artifact_replaced": False,
            "development_performance_is_formal_comparator": False,
        },
        "reference_alignment": {
            "v3_1_trace_reused_without_rerun": True,
            "performance_reference_for_0_19_available": True,
            "performance_reference_scope": "development_only_from_sealed_v3.1_event_trace",
            "reason": "The 0..19 development reference is derived directly from the sealed v3.1 event timestamps and cross-checks the existing 0..5 and 0..11 PREFIX_REFERENCE values; it is not a formal fair comparator.",
            "mixed_p0_baseline_status": "MIXED_ENVELOPES_NOT_FORMAL_COMPARISON",
        },
        "input_bindings": {
            "method_plan_absolute_path": str(method_plan_path.resolve()),
            "method_plan_file_sha256": sha256_file(method_plan_path),
            "provider_envelope_absolute_path": str(provider_path.resolve()),
            "provider_envelope_file_sha256": sha256_file(provider_path),
            "provider_envelope_payload_sha256": provider.get("payload_sha256"),
        },
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit",
        type=Path,
        default=PROJECT
        / "artifacts/paper_eval/membind_v4/protocol_amendment_a1/V4_OPPORTUNITY_AUDIT_A1.json",
    )
    parser.add_argument(
        "--method-plan",
        type=Path,
        default=PROJECT / "artifacts/paper_eval/membind_v31/V31_METHOD_PLAN.json",
    )
    parser.add_argument(
        "--provider-envelope",
        type=Path,
        default=PROJECT
        / "artifacts/paper_eval/membind_v31/PROVIDER_EXECUTION_ENVELOPE_XGRAMMAR_20260819.json",
    )
    parser.add_argument(
        "--development-reference",
        type=Path,
        default=PROJECT
        / "artifacts/paper_eval/membind_v4/protocol_amendment_a1/V4_A1_DEVELOPMENT_REFERENCE.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT
        / "artifacts/paper_eval/membind_v4/protocol_amendment_a1/V4_PROTOCOL_AMENDMENT_A1_OPPORTUNITY_EXPOSURE.json",
    )
    args = parser.parse_args(argv)
    artifact = build_amendment(
        audit_path=args.audit.resolve(),
        method_plan_path=args.method_plan.resolve(),
        provider_path=args.provider_envelope.resolve(),
        development_reference_path=args.development_reference.resolve(),
    )
    atomic_write_json(args.output.resolve(), artifact)
    print(json.dumps({"output": str(args.output.resolve()), "payload_sha256": artifact["payload_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
