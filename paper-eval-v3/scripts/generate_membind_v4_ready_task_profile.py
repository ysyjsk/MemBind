#!/usr/bin/env python3
"""Generate a sealed, diagnostic-only ready-task opportunity profile."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file  # noqa: E402
from paper_eval.membind_v4.ready_task_profile import (  # noqa: E402
    analyze_ready_task_opportunities,
    render_ready_task_profile_report,
)


ARTIFACTS = PROJECT / "artifacts/paper_eval"
PILOT_ROOT = (
    ARTIFACTS
    / "membind_v31/optimization/pilots/membind-v31-opt-w4-20260818-001"
)
V4_ROOT = ARTIFACTS / "membind_v4"


def _sealed_manifest(path: Path) -> dict[str, object]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("pilot_manifest_unreadable") from None
    if not isinstance(manifest, dict):
        raise ValueError("pilot_manifest_invalid")
    stored = manifest.get("manifest_sha256")
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if stored != payload_sha256(body):
        raise ValueError("pilot_manifest_hash_mismatch")
    return manifest


def _build_artifact(*, output: Path) -> dict[str, object]:
    manifest_path = PILOT_ROOT / "manifest.json"
    manifest = _sealed_manifest(manifest_path)
    queue_path = PILOT_ROOT / "queue.jsonl"
    events_path = PILOT_ROOT / "events.jsonl"
    llm_path = PILOT_ROOT / "llm.jsonl"
    profile = analyze_ready_task_opportunities(queue_path, events_path, llm_path)
    body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v4.ready-task-opportunity-artifact.v1",
        "status": "DIAGNOSTIC_ONLY",
        "scope": {
            "history_id": manifest.get("history_id"),
            "source_count": manifest.get("source_count"),
            "source_prefix": f"0..{int(manifest['source_count']) - 1}",
            "arrival_trace_unchanged": True,
            "scheduler_implemented": False,
            "network_calls": 0,
            "persistent_writes": 0,
        },
        "input_binding": {
            "pilot_root": "artifacts/paper_eval/membind_v31/optimization/pilots/membind-v31-opt-w4-20260818-001",
            "manifest_file_sha256": sha256_file(manifest_path),
            "manifest_payload_sha256": manifest["manifest_sha256"],
            "queue_file_sha256": profile["source_file_sha256s"]["queue"],
            "events_file_sha256": profile["source_file_sha256s"]["events"],
            "llm_file_sha256": profile["source_file_sha256s"]["llm"],
            "lookahead": manifest.get("lookahead"),
            "compile_workers": manifest.get("compile_workers"),
            "global_llm_admission_k": manifest.get("global_llm_admission_k"),
        },
        "profile": profile,
        "decision": {
            "ready_scheduler_direction": (
                "NO_SCHEDULING_OPPORTUNITY_OBSERVED"
                if not profile["decision"]["scheduler_choice_observed"]
                and not profile["decision"]["workflow_choice_observed"]
                else "OPPORTUNITY_OBSERVED_REQUIRES_CONTROLLED_SCHEDULER_STUDY"
            ),
            "fine_grained_operator_direction": "STOP_FINE_OPERATOR_CLAIMS_UNOBSERVABLE",
            "backend_speedup_proven": False,
            "formal_main_table_eligible": False,
            "reason": (
                "The sealed trace exposes no ready width >= 2; fine-grained operator "
                "membership and backend batching are not observable."
            ),
        },
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=V4_ROOT / "V4_READY_TASK_OPPORTUNITY_PROFILE.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=V4_ROOT / "V4_READY_TASK_OPPORTUNITY_PROFILE.md",
    )
    parser.add_argument(
        "--decision",
        type=Path,
        default=V4_ROOT / "V4_READY_TASK_OFFLINE_DECISION.md",
    )
    args = parser.parse_args(argv)
    artifact = _build_artifact(output=args.output.resolve())
    atomic_write_json(args.output.resolve(), artifact)
    profile = artifact["profile"]
    report = render_ready_task_profile_report(profile)
    report += "\n## Terminal Interpretation\n\n"
    report += (
        "The fixed sealed pilot exposes no legal ready width of two or more. "
        "This is an offline opportunity result, not a backend speedup claim.\n"
    )
    args.report.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.report.resolve().write_text(report, encoding="utf-8")
    decision = """# MemBind v4 Ready-Task Offline Decision

## Scope

This diagnostic replays the existing sealed v3.1 W=4 pilot for
`history=07741c45`, sources `0..11`, without changing the arrival trace,
workload, scheduler, model, backend, or database. It performs no network calls
and no persistent writes. The result is not formal main-table evidence.

## Result

The scheduler aggregate and dependency-reconstructed workflow view both show
`peak ready width = 1` and zero time at ready width `>= 2`. The observable
request-kind groups are only coarse `COMPILE` and `BIND/FRONTIER`; the sealed
trace has no member IDs or fine Graphiti operator labels, so
EntityExtract/EdgeExtract/NodeResolve same-type width is `UNAVAILABLE`, not
zero.

Ready wait is measured from dependency readiness to actual dispatch. Overlap
between a frontier-critical ready interval and noncritical LLM service is
reported only as an overlap proxy; it is not treated as causal blocking.

## Decision

```text
NO_SCHEDULING_OPPORTUNITY_OBSERVED
```

Do not implement or live-test a new scheduler on this trace. A future
controlled scheduler study would require a trace that actually exposes at
least two legal ready tasks, or a separately authorized workload amendment;
neither is authorized by this offline study. Do not infer vLLM batching or
throughput gains from aggregate ready width alone.

The existing conflict-aware NodeResolve terminal remains independent:

```text
STOP_V4_NODE_RESOLVE
```
"""
    args.decision.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.decision.resolve().write_text(decision, encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "report": str(args.report.resolve()),
                "decision": str(args.decision.resolve()),
                "payload_sha256": artifact["payload_sha256"],
                "ready_scheduler_direction": artifact["decision"][
                    "ready_scheduler_direction"
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
