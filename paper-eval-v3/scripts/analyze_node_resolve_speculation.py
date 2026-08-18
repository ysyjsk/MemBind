#!/usr/bin/env python3
"""Run the isolated, read-only NodeResolve speculation feasibility audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.node_resolve_speculation import (
    audit_graphiti_node_resolve_source,
    audit_trace_fields,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", action="append", required=True, type=Path)
    parser.add_argument("--graphiti-source", required=True, type=Path)
    parser.add_argument("--phase-diagnostic", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def _phase_context(path: Path | None) -> dict[str, object] | None:
    if path is None:
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("phase diagnostic must be an object")
    phase_time = value.get("phase_time_ns")
    phase_fraction = value.get("phase_fraction")
    ready = value.get("ready_work")
    if not isinstance(phase_time, dict) or not isinstance(phase_fraction, dict):
        raise ValueError("phase diagnostic missing phase projections")
    return {
        "source_artifact": str(path),
        "observation_window_ns": value.get("observation_window_ns"),
        "phase_time_ns": phase_time,
        "phase_fraction": phase_fraction,
        "ready_work": ready,
        "interpretation": "Bind phase is an upper bound for NodeResolve; it is not a NodeResolve measurement.",
    }


def _markdown(result: dict[str, object]) -> str:
    audit = result["trace_audit"]
    boundary = result["source_boundary_audit"]
    phase = result.get("phase_context")
    lines = [
        "# NodeResolve Speculation Feasibility Audit",
        "",
        "Status: `DIAGNOSTIC_ONLY`; this artifact cannot authorize or merge a live run.",
        "",
        "## Decision",
        "",
        f"`{result['decision']}`",
        "",
        result["decision_reason"],
        "",
        "The probe validates the semantic-call contract offline. It does not infer a reuse rate from transport spans.",
        "",
        "## Graphiti Source Boundary",
        "",
        f"- verdict: `{boundary['verdict']}`",
        f"- candidate materialization separate: `{str(boundary['candidate_materialization_separate']).lower()}`",
        f"- LLM execution separate: `{str(boundary['llm_execution_separate']).lower()}`",
        f"- LLM stage persistent-effect free: `{str(boundary['llm_stage_persistent_effect_free']).lower()}`",
        f"- source SHA-256: `{boundary['source_sha256']}`",
        "",
        "## Existing Trace Audit",
        "",
        f"- trace files: `{audit['trace_count']}`",
        f"- rows scanned: `{audit['row_count']}`",
        f"- verdict: `{audit['verdict']}`",
        f"- missing fields: `{', '.join(audit['missing_fields']) or 'none'}`",
        "",
        "Required fields are operator identity, predecessor state version, candidate ordering/binding, and semantic-call fingerprint.",
        "Without them, an existing Compile/FRONTIER transport trace cannot establish NodeResolve stability.",
        "",
        "## Phase Context (Non-attributable)",
        "",
    ]
    if phase is None:
        lines.append("No phase diagnostic supplied.")
    else:
        fractions = phase.get("phase_fraction", {})
        lines.append(f"- observed Bind fraction: `{float(fractions.get('BINDING', 0.0)):.6f}`")
        lines.append("- this is an upper bound/context only; NodeResolve is not isolated in the current trace")
    lines.extend(
        [
            "",
            "## Next Evidence Needed",
            "",
            "1. Capture one content-free operator record per NodeResolve call with the required fields.",
            "2. Persist per-prefix state/candidate materialization so stale and exact calls can be paired.",
            "3. Run the offline reducer; only `D2_REUSE_POTENTIAL_SUPPORTED` may justify a separate V4 pilot.",
            "",
            f"Artifact payload SHA-256: `{result['payload_sha256']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _parser().parse_args()
    audit = audit_trace_fields(args.trace)
    boundary = audit_graphiti_node_resolve_source(args.graphiti_source)
    if boundary["verdict"] != "NODE_RESOLVE_BOUNDARY_FEASIBLE":
        decision = "D2_BOUNDARY_NOT_FEASIBLE"
        reason = "The current Graphiti source does not expose a separately reusable, persistent-effect-free NodeResolve LLM stage."
    elif audit["verdict"] == "D2_DATA_SUFFICIENT":
        decision = "D2_REUSE_POTENTIAL_REQUIRES_REPLAY"
        reason = "The trace exposes the required fields; pair stale/exact calls before estimating reuse."
    else:
        decision = "D2_BOUNDARY_FEASIBLE_DATA_INSUFFICIENT"
        reason = "The implementation boundary can support validated speculation, but the current immutable trace lacks operator-level semantic-call/state evidence needed to estimate reuse."
    result: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v3.node-resolve-feasibility-audit.v1",
        "status": "DIAGNOSTIC_ONLY_NON_MERGEABLE",
        "decision": decision,
        "decision_reason": reason,
        "source_boundary_audit": boundary,
        "trace_audit": audit,
        "phase_context": _phase_context(args.phase_diagnostic),
        "live_services_contacted": False,
        "frozen_v31_modified": False,
        "payload_sha256": "",
    }
    result["payload_sha256"] = payload_sha256(
        {key: value for key, value in result.items() if key != "payload_sha256"}
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "NODE_RESOLVE_FEASIBILITY.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.output / "NODE_RESOLVE_FEASIBILITY.md").write_text(
        _markdown(result), encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
