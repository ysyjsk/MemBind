#!/usr/bin/env python3
"""Seal the offline cross-layer parallelism funnel diagnosis.

This generator only reads existing local traces.  It does not start services,
contact a model, modify an arrival trace, or create a live result artifact.
"""

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
from paper_eval.membind_v4.parallelism_funnel import (  # noqa: E402
    analyze_aligned_baseline_prefix,
    analyze_parallelism_funnel,
    render_parallelism_funnel_report,
)


ARTIFACTS = PROJECT / "artifacts/paper_eval"
PILOT_ROOT = ARTIFACTS / "membind_v31/optimization/pilots/membind-v31-opt-w4-20260818-001"
BASELINE_ROOT = ARTIFACTS / "apc_aligned_baseline/runs/apc-baseline-dev-20260817-001"
V4_ROOT = ARTIFACTS / "membind_v4"
HISTORY_ID = "07741c45"
PREFIX_COUNT = 12
BASELINE_METHODS = ("U0-aligned", "A0-aligned", "P(C=2)-aligned")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError(f"json_unreadable:{path}") from None
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _verify_payload(document: dict[str, Any], key: str, label: str) -> None:
    digest = document.get(key)
    body = {name: value for name, value in document.items() if name != key}
    if not isinstance(digest, str) or digest != payload_sha256(body):
        raise ValueError(f"payload_hash_mismatch:{label}")


def _sha256_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and len(item) == 64 for item in value
    ):
        raise ValueError(f"source_hash_list_invalid:{label}")
    return list(value)


def _build_artifact() -> dict[str, Any]:
    pilot_manifest_path = PILOT_ROOT / "manifest.json"
    pilot_contract_path = PILOT_ROOT / "PILOT_CONTRACT.json"
    pilot_manifest = _read_json(pilot_manifest_path)
    pilot_contract = _read_json(pilot_contract_path)
    _verify_payload(pilot_manifest, "manifest_sha256", "pilot_manifest")
    _verify_payload(pilot_contract, "payload_sha256", "pilot_contract")
    if pilot_manifest.get("history_id") != HISTORY_ID:
        raise ValueError("pilot_history_mismatch")
    if pilot_contract.get("history_id") != HISTORY_ID:
        raise ValueError("pilot_contract_history_mismatch")
    pilot_hashes = _sha256_list(pilot_manifest.get("source_sha256s"), "pilot")
    contract_hashes = _sha256_list(
        pilot_contract.get("source_sha256s"), "pilot_contract"
    )
    if pilot_hashes != contract_hashes:
        raise ValueError("pilot_source_hash_binding_mismatch")
    if len(pilot_hashes) != PREFIX_COUNT:
        raise ValueError("pilot_source_count_mismatch")

    funnel = analyze_parallelism_funnel(
        PILOT_ROOT / "queue.jsonl",
        PILOT_ROOT / "events.jsonl",
        PILOT_ROOT / "llm.jsonl",
    )

    baseline_plan_path = BASELINE_ROOT / "PLAN.json"
    baseline_preflight_path = BASELINE_ROOT / "PREFLIGHT.json"
    baseline_plan = _read_json(baseline_plan_path)
    baseline_preflight = _read_json(baseline_preflight_path)
    _verify_payload(baseline_plan, "payload_sha256", "baseline_plan")
    _verify_payload(baseline_preflight, "payload_sha256", "baseline_preflight")
    baseline_arrival = baseline_plan.get("arrival_traces", {}).get(HISTORY_ID)
    if not isinstance(baseline_arrival, dict):
        raise ValueError("baseline_arrival_trace_missing")
    baseline_offsets = baseline_arrival.get("arrival_offsets_ns")
    if not isinstance(baseline_offsets, list):
        raise ValueError("baseline_arrival_offsets_missing")
    baseline_source_hashes = _sha256_list(
        baseline_plan.get("history_source_sha256s", {}).get(HISTORY_ID),
        "baseline_plan",
    )
    baseline_block_rows = [
        row
        for row in baseline_plan.get("blocks", [])
        if isinstance(row, dict)
        and row.get("history_id") == HISTORY_ID
        and row.get("method") in BASELINE_METHODS
    ]
    if len(baseline_block_rows) != len(BASELINE_METHODS):
        raise ValueError("baseline_method_scope_missing")

    baseline_prefixes: list[dict[str, Any]] = []
    for block in sorted(baseline_block_rows, key=lambda row: int(row["block_index"])):
        method = str(block["method"])
        block_index = int(block["block_index"])
        block_root = BASELINE_ROOT / "blocks" / f"block-{block_index:02d}"
        block_manifest = _read_json(block_root / "manifest.json")
        _verify_payload(
            block_manifest,
            "manifest_sha256",
            f"baseline_block_manifest:{block_index}",
        )
        registered_path = block_root / "APC_ALIGNED_BLOCK_RESULT.json"
        prefix = analyze_aligned_baseline_prefix(
            block_root / "events.jsonl",
            prefix_count=PREFIX_COUNT,
            registered_result_path=registered_path,
        )
        baseline_prefixes.append(
            {
                "method": method,
                "block_index": block_index,
                "scope": {
                    "registered_source_count": int(block.get("source_count", 49)),
                    "recomputed_prefix_source_count": PREFIX_COUNT,
                    "prefix_censored": True,
                    "history_id": HISTORY_ID,
                },
                "input_binding": {
                    "manifest_file_sha256": sha256_file(block_root / "manifest.json"),
                    "manifest_payload_sha256": block_manifest["manifest_sha256"],
                    "events_file_sha256": sha256_file(block_root / "events.jsonl"),
                    "registered_result_file_sha256": sha256_file(registered_path),
                    "registered_result_payload_sha256": prefix["registered_full_run"][
                        "result_payload_sha256"
                    ],
                },
                "registered_full_run": prefix["registered_full_run"],
                "recomputed_prefix": prefix,
            }
        )

    same_source_hashes = baseline_source_hashes[:PREFIX_COUNT] == pilot_hashes
    same_offsets = baseline_offsets[:PREFIX_COUNT] == pilot_contract.get(
        "arrival_offsets_ns"
    )
    same_interarrival = baseline_arrival.get("interarrival_ns") == pilot_contract.get(
        "interarrival_ns"
    )
    same_envelope = all(
        row.get("shared_execution_envelope_sha256")
        == pilot_contract.get("shared_execution_envelope_sha256")
        for row in baseline_block_rows
    )
    baseline_history_trace = baseline_arrival.get("history_arrival_trace_sha256")
    pilot_history_trace = pilot_manifest.get("history_arrival_trace_sha256")
    baseline_source_manifest = baseline_plan.get("source_manifest_sha256")
    pilot_source_manifest = pilot_manifest.get("source_manifest_sha256")
    baseline_execution_identity = baseline_preflight.get("execution_identity_sha256")
    pilot_execution_identity = pilot_manifest.get("execution_identity_sha256")

    identity_audit: dict[str, Any] = {
        "history_id": HISTORY_ID,
        "source_scope": {
            "pilot_source_count": PREFIX_COUNT,
            "baseline_registered_source_count": 49,
            "same_first_12_source_hashes": same_source_hashes,
            "same_first_12_source_hashes_definition": (
                "pilot manifest source_sha256s vs baseline plan history_source_sha256s"
            ),
            "baseline_full_run_is_scope_censored_for_prefix_comparison": True,
        },
        "arrival": {
            "same_first_12_offsets": same_offsets,
            "pilot_interarrival_ns": pilot_contract.get("interarrival_ns"),
            "baseline_interarrival_ns": baseline_arrival.get("interarrival_ns"),
            "same_interarrival": same_interarrival,
            "pilot_arrival_trace_sha256": pilot_manifest.get("arrival_trace_sha256"),
            "baseline_run_arrival_trace_sha256": baseline_plan.get(
                "arrival_trace_sha256"
            ),
            "pilot_history_arrival_trace_sha256": pilot_history_trace,
            "baseline_history_arrival_trace_sha256": baseline_history_trace,
            "history_arrival_trace_sha256_equal": pilot_history_trace
            == baseline_history_trace,
        },
        "execution_envelope": {
            "pilot_shared_execution_envelope_sha256": pilot_contract.get(
                "shared_execution_envelope_sha256"
            ),
            "baseline_shared_execution_envelope_sha256": baseline_plan.get(
                "shared_execution_envelope_sha256"
            ),
            "same_shared_execution_envelope": same_envelope,
        },
        "execution_identity": {
            "pilot_execution_identity_sha256": pilot_execution_identity,
            "baseline_preflight_execution_identity_sha256": baseline_execution_identity,
            "same_execution_identity": pilot_execution_identity
            == baseline_execution_identity,
            "comparison_limit": (
                "pilot and baseline are different execution identities/method runs; "
                "this is contextual evidence, not a causal method comparison"
            ),
        },
        "method_identity": {
            "pilot_method": pilot_manifest.get("method"),
            "baseline_methods": list(BASELINE_METHODS),
            "pilot_policy": pilot_manifest.get("policy"),
            "baseline_policy": baseline_plan.get("apc_cache_policy"),
        },
        "source_manifest": {
            "pilot_source_manifest_sha256": pilot_source_manifest,
            "baseline_source_manifest_sha256": baseline_source_manifest,
            "equal": pilot_source_manifest == baseline_source_manifest,
            "reason": "pilot is a 12-source prefix; baseline registration is full-run scope",
        },
    }

    body: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v4.parallelism-funnel-artifact.v1",
        "status": "DIAGNOSTIC_ONLY_NON_MERGEABLE",
        "scope": {
            "history_id": HISTORY_ID,
            "pilot_source_prefix": "0..11",
            "pilot_source_count": PREFIX_COUNT,
            "baseline_registered_source_count": 49,
            "arrival_trace_unchanged": True,
            "network_calls": 0,
            "persistent_writes": 0,
            "scheduler_implemented": False,
            "live_candidate_authorized": False,
            "formal_main_table_eligible": False,
        },
        "input_binding": {
            "pilot_root": str(PILOT_ROOT.relative_to(PROJECT)),
            "pilot_manifest_file_sha256": sha256_file(pilot_manifest_path),
            "pilot_manifest_payload_sha256": pilot_manifest["manifest_sha256"],
            "pilot_contract_file_sha256": sha256_file(pilot_contract_path),
            "pilot_contract_payload_sha256": pilot_contract["payload_sha256"],
            "pilot_queue_file_sha256": funnel["source_file_sha256s"]["queue"],
            "pilot_events_file_sha256": funnel["source_file_sha256s"]["events"],
            "pilot_llm_file_sha256": funnel["source_file_sha256s"]["llm"],
            "baseline_root": str(BASELINE_ROOT.relative_to(PROJECT)),
            "baseline_plan_file_sha256": sha256_file(baseline_plan_path),
            "baseline_plan_payload_sha256": baseline_plan["payload_sha256"],
            "baseline_preflight_file_sha256": sha256_file(baseline_preflight_path),
            "baseline_preflight_payload_sha256": baseline_preflight["payload_sha256"],
        },
        "funnel": funnel,
        "baseline_prefix_audit": baseline_prefixes,
        "identity_scope_audit": identity_audit,
        "decision": {
            "root_cause_classification": funnel["decision"][
                "root_cause_classification"
            ],
            "terminal": funnel["decision"]["terminal"],
            "source_backlog_observed": funnel["decision"]["source_backlog_observed"],
            "coarse_stage_scheduler_authorized": funnel["decision"][
                "coarse_stage_scheduler_authorized"
            ],
            "end_to_end_parallelism_collapse_proven": funnel["decision"][
                "end_to_end_parallelism_collapse_proven"
            ],
            "backend_bottleneck_proven": funnel["decision"][
                "backend_bottleneck_proven"
            ],
            "workload_too_sparse_proven": funnel["decision"][
                "workload_too_sparse_proven"
            ],
            "stop_v4_node_resolve": True,
            "reason": (
                "The observable coarse ready pool has no scheduling choice, while "
                "source overlap and internal client admission pressure are both present."
            ),
        },
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def _render_decision(artifact: dict[str, Any]) -> str:
    funnel = artifact["funnel"]
    decision = artifact["decision"]
    source = funnel["source_outstanding"]
    ready = funnel["workflow_ready_waiting"]
    active = funnel["workflow_active"]
    pending = funnel["llm_request_pending"]
    waiting = funnel["llm_admission_waiting"]
    running = funnel["llm_client_running"]
    return """# MemBind v4 Parallelism Root-Cause Decision

## Scope

This is a sealed offline diagnosis of the existing v3.1 W=4 pilot for
`history=07741c45`, sources `0..11`. It does not change the arrival trace,
workload, scheduler, model, backend, or database. It makes zero network calls
and zero persistent runtime writes. The APC baseline prefix values below are
read-only recomputations and are not new baseline results.

## Funnel Result

| Observable boundary | Peak width | Meaning |
|---|---:|---|
| Source outstanding | %s | `ARRIVAL` to `PUBLICATION_DURABLE` |
| Coarse workflow ready-waiting | %s | legal dependency-ready work not dispatched |
| Workflow active | %s | dispatched stage spans |
| Client LLM request pending | %s | submitted to terminal |
| Client LLM admission waiting | %s | submitted to start |
| Client-observed request running | %s | not GPU execution |

The trace therefore does not show a completely arrival-serial workload, and it
does not show end-to-end dependency serialization. It shows a coarse ready pool
with no scheduling choice plus substantial internal request fan-out/admission
pressure. vLLM batch membership, GPU execution width, and fine-grained operator
identity are not observable in this evidence.

## Terminal Decision

```text
%s
%s
```

Do not implement a coarse stage scheduler from this trace. Do not claim a GPU
bottleneck or a speedup from client request overlap. Do not use the 49-source
registered baseline backlog as a 12-source pilot metric; the prefix comparison
is explicitly scope-censored and method/execution identity differs.

The existing v4 NodeResolve lane remains stopped:

```text
STOP_V4_NODE_RESOLVE
```

No c02/c03 or live scheduler candidate is authorized by this diagnosis.
""" % (
        source["peak_width"],
        ready["peak_width"],
        active["peak_width"],
        pending["peak_width"],
        waiting["peak_width"],
        running["peak_width"],
        decision["root_cause_classification"],
        decision["terminal"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=V4_ROOT / "V4_PARALLELISM_FUNNEL.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=V4_ROOT / "V4_PARALLELISM_FUNNEL.md",
    )
    parser.add_argument(
        "--decision",
        type=Path,
        default=V4_ROOT / "V4_PARALLELISM_ROOT_CAUSE_DECISION.md",
    )
    args = parser.parse_args(argv)
    artifact = _build_artifact()
    atomic_write_json(args.output.resolve(), artifact)
    report = render_parallelism_funnel_report(artifact["funnel"])
    report += "\n## Identity and Scope Boundary\n\n"
    report += (
        "The first 12 source hashes, arrival offsets, interarrival, and shared "
        "execution envelope are checked against the APC-aligned baseline. The "
        "pilot and baseline remain different method/execution identities; the "
        "baseline full-run result is not replaced by a prefix result.\n"
    )
    args.report.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.report.resolve().write_text(report, encoding="utf-8")
    args.decision.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.decision.resolve().write_text(_render_decision(artifact), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "report": str(args.report.resolve()),
                "decision": str(args.decision.resolve()),
                "payload_sha256": artifact["payload_sha256"],
                "terminal": artifact["decision"]["terminal"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
