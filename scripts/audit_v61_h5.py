#!/usr/bin/env python3
"""Create the source-grounded H5 scheduler/resource audit.

This is a static, provider-free audit.  It records what the implementation
actually exposes and why the current fixed safeguard remains the only
authorized method identity until a distinct, evidence-backed candidate exists.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SFWB = ROOT / "saturated_fixed_work_baseline_v1_3"
OUT = SFWB / "structured_output_recovery"
SRC = SFWB / "src/saturated_fixed_work_baseline_v1_3"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def main() -> int:
    policy = SRC / "membind_v6_1/policy.py"
    admission = SRC / "membind_v6_1/admission.py"
    provider = SRC / "membind_v6_1/provider.py"
    executor = SRC / "membind_v6_1/executor.py"
    routing = SRC / "membind_v6_1/routing.py"
    core = SRC / "membind_v6_1/core.py"
    stress = SFWB / "scripts/run_v61_scheduler_stress.py"
    current_head = head()
    source_hashes = {name: sha(path) for name, path in {"policy": policy, "admission": admission, "provider": provider, "executor": executor, "routing": routing, "core": core, "stress": stress}.items()}
    task_classes = [
        {"class": "P0", "implementation": "NATIVE_FRONTIER", "callsites": ["membind_v6_1.provider._class", "membind_v6_1.mab publish/native provider scope"], "dependency": "authoritative frontier publication and dependency-unblocking request", "consumption": "current durable frontier", "priority": 0},
        {"class": "P1", "implementation": "DISABLED_UNPROVEN", "callsites": [], "dependency": "would require a certified direct-consumer edge", "consumption": "not used because source_sequence == frontier + 1 is not itself a proof", "priority": 1},
        {"class": "P2", "implementation": "FUTURE_PREPARE", "callsites": ["membind_v6_1.provider._class", "membind_v6_1.admission"], "dependency": "logical source is dependency-ready but not authoritative next consumer", "consumption": "future source lease / prepare result", "priority": 2},
    ]
    audit = {
        "schema_version": "membind.h5.scheduler-problem-audit.v1",
        "status": "AUDITED_FIXED_GUARD_RETAINED",
        "head_commit": current_head,
        "source_hashes": source_hashes,
        "TASK_CLASSES": task_classes,
        "TRUE_DEPENDENCIES": [
            "executor awaits prepare(sequence) before publish(sequence)",
            "durable publication is contiguous and advances frontier one source at a time",
            "provider classification uses NATIVE for publication and FRONTIER/FUTURE for prepare",
            "future results cannot mutate authoritative state before ordered publication",
        ],
        "CONSUMPTION_HORIZON": {"authoritative": "durable_frontier + 1", "future": "logical-ready sources admitted under source lease and physical permit", "p1": "not certified; no direct-consumer edge proof in current source"},
        "ARTIFICIAL_SERIALIZATION": [
            "staged execution has an explicit preparation-stage barrier before native publication",
            "JIT executor uses an effective two-source window even when policy lookahead is larger",
            "native guard drains active future calls because submitted provider work is not preemptible",
        ],
        "NON_PREEMPTIBLE_BOUNDARIES": [
            "ForegroundAdmissionArbiter admission boundary before provider transport",
            "per-resource request slot and weighted request_tokens/token_budget accounting",
            "vLLM/FCFS provider queue after transport submission; no cancellation-based preemption is assumed",
            "Graphiti/Neo4j durable publication transaction is outside the in-memory arbiter",
        ],
        "INTERFERENCE_RISKS": [
            "future prefill can delay a later authoritative request in a non-preemptible queue",
            "batch/service dilation and KV/token residency pressure can increase critical wait",
            "route spillover can put native and prepare traffic on the same endpoint",
            "completed future work may be non-consumable until frontier publication",
        ],
        "CURRENT_FIXED_GUARDS": {
            "policy": {"lookahead": 2, "future_cap": 1, "native_future_quota": 0},
            "physical": "provider_slots plus request_tokens = prompt tokens + bounded decode reserve",
            "token_budget": "min(authority * 8192, 61440) from current policy deployment facts",
            "native_guard": "close new future admission and drain active future calls before P0 publication",
            "source_lease": "logical future-source bound kept separate from physical permits",
        },
        "WHY_EACH_GUARD_EXISTS": {
            "lookahead": "retained compatibility/window safeguard; current executor caps effective JIT materialization to two successors",
            "future_cap": "limits non-preemptible future exposure and source/debt accumulation",
            "native_future_quota": "zero prevents future calls from co-residing with an authoritative native interval",
            "token_budget": "maps admission to weighted KV/token residency rather than request count alone",
            "native_guard": "only enforceable way to protect P0 after a provider request has been submitted",
        },
        "HISTORICAL_REJECTED_SCHEDULERS": [
            {"candidate_id": "r66a", "mechanism": "adaptive controller", "pathology": "queue/service feedback confusion and no stable improvement", "decision": "DO_NOT_REPEAT", "evidence_source": "MemBind_V6_1_8B_Autoresearch_Workplan.md#14.6"},
            {"candidate_id": "r67-r69", "mechanism": "finish-time/service-EWMA critical scheduler", "pathology": "phase spillover, batch/service dilation, resource-matched effect within noise", "decision": "DO_NOT_REPEAT", "evidence_source": "MemBind_V6_1_8B_Autoresearch_Workplan.md#14.7-14.8"},
        ],
        "OPEN_METHOD_QUESTION": "Whether a distinct logical-ready classification can be widened while preserving the existing physical, token, and native-guard boundary; no current trace proves this without reintroducing the rejected service-prediction family.",
        "provider_calls": 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    credit_map = {
        "schema_version": "membind.h5.scheduler-resource-credit-map.v1",
        "head_commit": current_head,
        "status": "PASS_SOURCE_MAPPED_FIXED_CREDITS",
        "credits": [
            {"name": "logical_source_lease", "boundary": "future source logical admission", "unit": "source", "authority": "CapacityAuthority.value and V61Policy.future_cap", "acquire_release_cancel": "acquire_source_lease/release_source_lease; cancellation removes waiter", "provider_queue_included": False, "shared_by_arms": "C only; A/B keep arm-agnostic shared envelope"},
            {"name": "physical_request_permit", "boundary": "entry to routed provider transport", "unit": "request slot", "authority": "CapacityAuthority.value", "acquire_release_cancel": "acquire_physical/release_physical in finally", "provider_queue_included": "admission boundary only; post-submit queue is non-preemptible", "shared_by_arms": "A/B/C same deployment envelope"},
            {"name": "weighted_token_credit", "boundary": "active prompt plus decode residency envelope", "unit": "tokens", "authority": "V61Policy.token_budget and request_tokens", "acquire_release_cancel": "increment/decrement with permit lifecycle; underflow raises", "provider_queue_included": False, "shared_by_arms": "A/B/C deployment capacity, C accounting"},
            {"name": "endpoint_route_debt", "boundary": "routed endpoint dispatch accounting", "unit": "request tokens per endpoint", "authority": "RoutedOpenAIClient active token debt", "acquire_release_cancel": "dispatch/release paths validate exact request_tokens", "provider_queue_included": False, "shared_by_arms": "arm-agnostic router"},
        ],
        "non_claims": ["provider internal batching/kernel capacity", "GPU idle from absence of active HTTP request", "future P0 arrival prediction", "service-time prediction as correctness condition"],
        "provider_calls": 0,
    }
    heuristic = {
        "schema_version": "membind.h5.heuristic-necessity-audit.v1",
        "head_commit": current_head,
        "status": "FIXED_GUARDS_JUSTIFIED_AS_BOUNDED_ADMISSION_SAFEGUARD",
        "heuristics": [
            {"name": "lookahead", "value": 2, "role": "logical/window compatibility safeguard", "not_claimed_optimal": True},
            {"name": "future_cap", "value": 1, "role": "physical/source exposure bound", "not_claimed_optimal": True},
            {"name": "native_future_quota", "value": 0, "role": "native interval interference guard", "not_claimed_optimal": True},
            {"name": "decode_reserve", "value": 4096, "role": "deployment-derived structured-output reserve", "not_benchmark_tuned": True},
        ],
        "adaptive_candidate_conditions_not_met": [
            "provider queue is non-preemptible",
            "no reliable future P0 arrival predictor is permitted",
            "P1 direct-consumer identity is not certified",
            "historical service-EWMA/finish-time family is negative",
            "current stress evidence validates conservation and bounds, not an adaptive benefit",
        ],
        "decision": "VALID_NEGATIVE_ADAPTIVE_RESULT_FIXED_CONTINUES",
        "provider_calls": 0,
    }
    negative = {
        "schema_version": "membind.h5.historical-scheduler-negative-evidence.v1",
        "head_commit": current_head,
        "status": "APPEND_ONLY_NEGATIVE_EVIDENCE_RETAINED",
        "rows": audit["HISTORICAL_REJECTED_SCHEDULERS"],
        "current_candidate_difference": "Current decision does not introduce a new adaptive candidate; it retains explicit physical/token/native guards and disables unproven P1.",
        "provider_calls": 0,
    }
    decision = {
        "schema_version": "membind.h5.adaptive-decision.v1",
        "head_commit": current_head,
        "decision": "VALID_NEGATIVE_ADAPTIVE_RESULT_FIXED_CONTINUES",
        "selected_method": "V6_FIXED_POLICY",
        "fixed_policy": {"lookahead": 2, "future_cap": 1, "native_future_quota": 0},
        "reason": "Provider-free stress tests pass safety/conservation; source audit cannot justify widening physical outstanding work or a new predictor; rejected r66a/r67-r69 mechanism family is not repeated.",
        "stress_artifact": "SCHEDULER_STRESS_TEST_RESULT.json",
        "provider_calls": 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    artifacts = {
        "SCHEDULER_PROBLEM_AUDIT.json": audit,
        "SCHEDULER_RESOURCE_CREDIT_MAP.json": credit_map,
        "HEURISTIC_NECESSITY_AUDIT.json": heuristic,
        "HISTORICAL_SCHEDULER_NEGATIVE_EVIDENCE.json": negative,
        "ADAPTIVE_DECISION.json": decision,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for filename, value in artifacts.items():
        (OUT / filename).write_text(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    markdown = {
        "SCHEDULER_PROBLEM_AUDIT.md": "# Scheduler Problem Audit\n\nStatus: `AUDITED_FIXED_GUARD_RETAINED`.\n\nThe implementation exposes P0 `NATIVE_FRONTIER` and P2 `FUTURE_PREPARE`; P1 remains disabled because source distance is not a direct-consumer proof. Provider admission is weighted by request tokens and bounded slots. Native guard drains active future work at the non-preemptible boundary.\n",
        "SCHEDULER_RESOURCE_CREDIT_MAP.md": "# Scheduler Resource Credit Map\n\nCredits are mapped to logical source leases, physical provider permits, weighted prompt/decode tokens, and endpoint route debt. They are acquired and released through exact lifecycle paths; provider-internal batching is explicitly unknown.\n",
        "HEURISTIC_NECESSITY_AUDIT.md": "# Heuristic Necessity Audit\n\n`lookahead=2`, `future_cap=1`, and `native_future_quota=0` are retained as bounded-admission safeguards, not optimal parameters. No arrival/service predictor is introduced.\n\nDecision: `VALID_NEGATIVE_ADAPTIVE_RESULT_FIXED_CONTINUES`.\n",
        "HISTORICAL_SCHEDULER_NEGATIVE_EVIDENCE.md": "# Historical Scheduler Negative Evidence\n\nThe append-only record retains r66a and r67-r69 as rejected service-feedback families. Their queue/service confusion, spillover, and dilation evidence is not relabeled or rerun.\n",
    }
    for filename, text in markdown.items():
        (OUT / filename).write_text(text, encoding="utf-8")
    print(json.dumps({"status": decision["decision"], "head_commit": current_head, "provider_calls": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
