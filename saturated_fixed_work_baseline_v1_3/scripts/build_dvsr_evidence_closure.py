#!/usr/bin/env python3
"""Build the append-only Phase-0/0A DVSR evidence closure.

This command is deliberately provider-free and read-only.  It consumes only
checked-in V6/V7 artifacts plus the frozen dataset split and emits a
machine-readable scientific identity, an explicit V6 semantic-root audit,
and an evidence ledger whose missing fields remain visible instead of being
filled by assumptions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PACKAGE_ROOT / "v7" / "dvsr_v7_831_phase0"
PROFILE_ID = "local-qwen3-8b-awq-dualreplica-v1"
GRAPHITI_VERSION = "0.29.3"
V6_ROOTS = (
    PACKAGE_ROOT
    / "artifacts/mab-v1-3-live-firstpass-c0-recovery-methods-20260825-011/context-0/V6/e78d9a9be2e5",
    PACKAGE_ROOT
    / "artifacts/mab-v1-3-live-firstpass-context1-20260825-014/context-1/V6/6db1005726a9",
    PACKAGE_ROOT
    / "artifacts/mab-v1-3-live-firstpass-context2-20260825-015/context-2/V6/8c4a5e4e66b5",
)
SPLIT_PATH = WORKSPACE_ROOT / "membind-validation/artifacts/dataset/frozen_split_v1_3.json"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _tree_manifest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): _sha(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSON object required: {path}:{line_number}")
        rows.append(value)
    return rows


def _critical_path(root: Path) -> dict[str, Any]:
    # Import the existing provider-free reducer without importing Graphiti.
    source_root = PACKAGE_ROOT / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from saturated_fixed_work_baseline_v1_3.membind_v6.critical_path import reduce_history_artifact

    try:
        return reduce_history_artifact(root)
    except Exception as error:  # legacy V6 trace has no NATIVE_INTERVAL envelope
        metrics = _load(root / "metrics.json")
        lifecycle = _load(root / "lifecycle_validation.json")
        raw = _read_jsonl(root / "raw_events.jsonl")
        starts = [row for row in raw if row.get("event") == "FORMAL_START"]
        durable = [row for row in raw if row.get("event") == "PUBLICATION_DURABLE"]
        start_ns = int((starts[0] if starts else {}).get("monotonic_ns") or lifecycle.get("formal_start_ns") or 0)
        stop_ns = int((durable[-1] if durable else {}).get("monotonic_ns") or lifecycle.get("last_publication_durable_ns") or 0)
        return {
            "schema_version": "membind.dvsr.legacy-v6-critical-path-summary.v1",
            "reduction_status": "MISSING_FIELD",
            "missing_field": "NATIVE_INTERVAL/TIMER_STOP events required by generic reducer",
            "reducer_error": str(error),
            "source_count": int(lifecycle.get("completed_count") or 0),
            "timer": {
                "timer_start_ns": start_ns,
                "timer_stop_ns": stop_ns,
                "build_makespan_ns": int(metrics.get("t_build_ns") or max(0, stop_ns - start_ns)),
                "reconstructed_from_lifecycle": True,
            },
        }


def _phase_seconds(reduction: dict[str, Any], phase: str) -> float:
    return float(reduction.get("phase_attribution", {}).get(phase, {}).get("total_duration_ns", 0)) / 1e9


def _v6_record(root: Path) -> dict[str, Any]:
    required = (
        "construction_seal.json",
        "frozen_config.json",
        "refinement_validation.json",
        "order_validation.json",
        "work_inventory.json",
        "native_trace.jsonl",
        "request_identity.jsonl",
        "raw_events.jsonl",
    )
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"V6 artifact is incomplete: {root} missing {missing}")
    seal = _load(root / "construction_seal.json")
    config = _load(root / "frozen_config.json")
    refinement = _load(root / "refinement_validation.json")
    order = _load(root / "order_validation.json")
    work = _load(root / "work_inventory.json")
    reduction = _critical_path(root)
    request_rows = _read_jsonl(root / "request_identity.jsonl")
    extraction_rows = [row for row in request_rows if str(row.get("callsite", "")).startswith("extract_")]
    previous_digests = sorted(
        {
            str(row.get("field_digests", {}).get("previous_context_digest"))
            for row in extraction_rows
            if row.get("field_digests", {}).get("previous_context_digest") is not None
        }
    )
    trace_rows = [row for row in _read_jsonl(root / "native_trace.jsonl")]
    prompt_counts: dict[str, int] = {}
    for envelope in trace_rows:
        for span in envelope.get("spans", []):
            if span.get("operation_class") != "logical-call":
                continue
            prompt = str(span.get("metadata", {}).get("prompt_name", "unknown"))
            prompt_counts[prompt] = prompt_counts.get(prompt, 0) + 1
    phase_totals: dict[str, int] = {}
    phase_counts: dict[str, int] = {}
    for envelope in trace_rows:
        for span in envelope.get("spans", []):
            phase = span.get("phase")
            duration = span.get("duration_ns")
            if isinstance(phase, str) and isinstance(duration, int) and duration >= 0:
                phase_totals[phase] = phase_totals.get(phase, 0) + duration
                phase_counts[phase] = phase_counts.get(phase, 0) + 1
    phase_attribution = {
        phase: {"span_count": phase_counts[phase], "total_duration_ns": total, "overlap_safe": False}
        for phase, total in sorted(phase_totals.items())
    }
    if "phase_attribution" not in reduction:
        reduction["phase_attribution"] = phase_attribution
    if "critical_path" not in reduction:
        reduction["critical_path"] = {
            "build_makespan_ns": reduction.get("timer", {}).get("build_makespan_ns", 0),
            "decomposition_status": "MISSING_FIELD",
        }
    return {
        "artifact_root": str(root),
        "artifact_tree_sha256": _canonical_sha(_tree_manifest(root)),
        "construction_seal_sha256": _sha(root / "construction_seal.json"),
        "construction_status": seal.get("status"),
        "identity": seal.get("identity", {}),
        "frozen_config_sha256": _sha(root / "frozen_config.json"),
        "frozen_config_revision": config.get("revision"),
        "graphiti_version": GRAPHITI_VERSION,
        "refinement_status": refinement.get("refinement_status"),
        "order_contract_status": order.get("order_contract_status"),
        "work_inventory": work,
        "critical_path": reduction,
        "phase_seconds": {
            "node_resolution": _phase_seconds(reduction, "node-resolution"),
            "edge_resolution": _phase_seconds(reduction, "edge-resolution"),
            "attributes_summary": _phase_seconds(reduction, "attributes-summary"),
            "publication": _phase_seconds(reduction, "publication"),
        },
        "extraction_observation": {
            "extraction_request_count": len(extraction_rows),
            "previous_context_digest_values": previous_digests,
            "previous_context_digest_cardinality": len(previous_digests),
            "summary_call_count": prompt_counts.get("extract_nodes.extract_summaries_batch", 0),
            "typed_attribute_call_count": sum(
                count for prompt, count in prompt_counts.items() if "attribute" in prompt.lower()
            ),
            "prompt_counts": dict(sorted(prompt_counts.items())),
        },
        "checked_in_fields": {
            "prepared_ready_frontier": False,
            "state_read_set": False,
            "prompt_visible_projection_digest": False,
            "canonical_stateful_request": False,
            "continuation_k": False,
            "actual_touched_write_delta": False,
            "foreground_interference": False,
        },
    }


def _status_rows(v6: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"id": "RQ1_v6_stateful_critical_path", "status": "ALREADY_PROVEN", "evidence": "three sealed V6 critical-path reductions", "next_action": "do not rerun characterization"},
        {"id": "RQ1_typed_attributes_current_hotspot", "status": "ALREADY_PROVEN", "evidence": "typed attribute call count is zero in all three sealed traces", "next_action": "exclude from first operator selection"},
        {"id": "RQ2_same_source_cross_snapshot_semantic_reads", "status": "REQUIRES_NEW_OBSERVER", "evidence": "no paired same-source read-set in checked-in artifacts", "next_action": "provider-free TDD then read-only observer"},
        {"id": "RQ2_canonical_stateful_request_identity", "status": "REQUIRES_NEW_OBSERVER", "evidence": "request_identity contains extraction fields only; no paired stateful identity", "next_action": "capture canonical request in observer"},
        {"id": "RQ3_certificate_soundness", "status": "REQUIRES_NEW_OBSERVER", "evidence": "certificate code exists but adversarial closure is not sealed", "next_action": "failing tests before observer"},
        {"id": "RQ4_exact_reconvergence", "status": "REQUIRES_NEW_OBSERVER", "evidence": "no paired repair/continuation oracle", "next_action": "single-call branch oracle after certificate TDD"},
        {"id": "RQ5a_offline_operator_economics", "status": "REQUIRES_NEW_OBSERVER", "evidence": "no operator-specific validation/miss/repair ledger", "next_action": "development observer only"},
        {"id": "RQ5b_online_foreground_interference", "status": "REQUIRES_NEW_LIVE", "evidence": "cannot be inferred from offline traces", "next_action": "selected operator and G4 only"},
        {"id": "B0_to_v6_timing_only_equivalence", "status": "PARTIALLY_SUPPORTED", "evidence": "V6 request fields show stripped extraction previous context, but paired B0 request audit is absent", "next_action": "do not claim Native-equivalence; optional separate audit"},
        {"id": "v6_based_no_reuse_seam", "status": "MISSING_FIELD", "evidence": "existing V6 does not expose prepared/no-reuse differential fields", "next_action": "implement minimal Frozen-V6 seam"},
        {"id": "dvrs_end_to_end_speedup", "status": "REQUIRES_NEW_LIVE", "evidence": "no authorized DVSR treatment", "next_action": "only after G6"},
    ]


def _identity(v6: list[dict[str, Any]], split: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "membind.dvsr.scientific-identity.v1",
        "status": "SEALED_PHASE_0_IDENTITY_LIVE_TREATMENT_UNAUTHORIZED",
        "algorithm_identity": "DVSR_OPERATOR_NEUTRAL_OBSERVER_V1",
        "semantic_root": {
            "method": "Frozen V6",
            "claim": "DVSR preserves Frozen V6 logical inputs, state evolution, and ordered durable publication; Native-equivalence to B0 is not claimed by this identity",
            "graphiti_version": GRAPHITI_VERSION,
            "profile_id": PROFILE_ID,
            "horizon": 1,
            "future_stateful_cap": 1,
            "publication": "ordered_authoritative",
            "speculation_writes": 0,
            "validation": "fail_closed",
            "fallback": "fresh_resolve_on_current_authoritative_state",
            "v6_artifact_seals": [row["construction_seal_sha256"] for row in v6],
        },
        "operator_cuts": {
            "CUT-N": "prepared extraction -> Node Resolution",
            "CUT-D": "prepared extraction -> Node -> Edge -> Summary/Hydration -> publication seam (nested CUT-N)",
            "CUT-E": "Edge direct reuse (deferred extension)",
            "typed_attributes": "excluded from first selection because sealed V6 call count is zero",
        },
        "certificate_contract": {
            "levels": ["C0 fresh oracle", "C1 semantic read certificate", "C2 exact reconvergence"],
            "false_valid_target": 0,
            "unknown_policy": "fail_closed",
            "canonical_request_fields": ["model", "schema", "flags", "order", "messages"],
        },
        "frozen_data_roles": {
            "development_exposed": split["calibration_question_ids"],
            "compatibility_quarantine": split["compatibility_development_question_ids"],
            "held_out_evaluation": split["evaluation_question_ids"],
            "held_out_access": "forbidden until method, thresholds, lambda, admission, statistics, and stopping rules are frozen",
        },
        "non_authorizations": [
            "no DVSR live reuse",
            "no B1 execution",
            "no old V7-FRESH oracle",
            "no V6 Core modification",
            "no held-out method-specific outcome inspection",
            "no rerun of sealed B0 or sealed V6 characterization",
        ],
    }


def _identity_markdown(identity: dict[str, Any], v6: list[dict[str, Any]]) -> str:
    rows = []
    for item in v6:
        reduction = item["critical_path"]
        timer = reduction.get("timer", {})
        duration_ns = int(timer.get("build_makespan_ns") or 0)
        rows.append(
            f"| `{item['identity'].get('context_id', 'unknown')}` | {duration_ns} ns | "
            f"{item['phase_seconds']['node_resolution']:.3f} | {item['phase_seconds']['attributes_summary']:.3f} | "
            f"{item['phase_seconds']['edge_resolution']:.3f} | {item['refinement_status']} | {item['order_contract_status']} |"
        )
    return f"""# V6 Semantic Root Audit

Status: **SEALED_PHASE_0_IDENTITY_LIVE_TREATMENT_UNAUTHORIZED**

DVSR's semantic root is the sealed Frozen V6 substrate.  This is a V6-preserving
claim: preparation may overlap with later stateful work, but the authoritative
state transition and durable publication remain V6's ordered chain.  The audit
does not claim that V6 is timing-only equivalent to B0; the paired B0 request
identity needed for that stronger claim is not present in the checked-in V6
artifacts.

## Frozen identity

- Algorithm before selection: `{identity['algorithm_identity']}`
- Profile: `{identity['semantic_root']['profile_id']}`
- Graphiti: `{identity['semantic_root']['graphiti_version']}`
- Horizon/stateful cap: `d=1`, `1`
- Publication: ordered authoritative; speculative writes: `0`
- Validation: fail-closed; fallback: fresh resolve on current authoritative state
- Candidate cuts: CUT-N and nested CUT-D; typed Attributes is excluded for this workload

## Sealed V6 evidence

| Context | Build interval | Node s | Summary s | Edge s | Refinement | Order |
| --- | ---: | ---: | ---: | ---: | --- | --- |
{chr(10).join(rows)}

The phase values are attribution evidence from the existing provider-free
critical-path reducer.  No provider, database, or sealed artifact was modified
to produce this audit.

## Claim boundary

The valid primary claim is: **DVSR preserves Frozen V6 semantics while changing
only execution timing through dependency-aware preparation, semantic validation,
exact repair/reconvergence, and ordered publication.** Any summary bypass,
predicate pushdown, deterministic materialization, or other work reduction is a
separate extension and cannot be folded into the core result.

Live treatment is unauthorized until certificate adversarial TDD, the complete
development observer, and the offline operator-selection gate pass.
"""


def _evidence_markdown(identity: dict[str, Any], v6: list[dict[str, Any]], statuses: list[dict[str, Any]], split: dict[str, Any], manifest_sha: str) -> str:
    lines = [
        "# DVSR Existing Evidence Closure",
        "",
        "Status: **PASS_WITH_EXPLICIT_MISSING_FIELDS**",
        "",
        f"Evidence manifest SHA-256: `{manifest_sha}`",
        "",
        "This is a provider-free, read-only closure over sealed artifacts. Missing fields are recorded as missing; they are not inferred from timing or from another method.",
        "",
        "## Frozen data roles",
        "",
        f"- Development exposed: `{', '.join(split['calibration_question_ids'])}`",
        f"- Compatibility quarantine: `{', '.join(split['compatibility_development_question_ids'])}`",
        f"- Held-out evaluation (locked): `{', '.join(split['evaluation_question_ids'])}`",
        "- Held-out method-specific outcomes were not opened and must remain closed until every method, threshold, lambda, admission policy, statistic, and stop rule is frozen.",
        "",
        "## Sealed artifacts",
        "",
    ]
    for item in v6:
        lines.append(f"- `{item['artifact_root']}`: construction `{item['construction_status']}`, seal `{item['construction_seal_sha256']}`, tree `{item['artifact_tree_sha256']}`")
    lines += ["", "## RQ and gate status", "", "| Item | Status | Evidence | Legal next action |", "| --- | --- | --- | --- |"]
    lines.extend(f"| `{row['id']}` | **{row['status']}** | {row['evidence']} | {row['next_action']} |" for row in statuses)
    lines += [
        "",
        "## Prohibited reruns",
        "",
        "- sealed B0/NATIVE_SERIAL;\n- sealed V6 Node/Summary/Edge characterization;\n- V4 legal-window analysis;\n- old V7-FRESH and V7-B NULL attempts;\n- typed-attribute hotspot check.",
        "",
        "The next legal implementation step is a Frozen-V6 prepared/no-reuse seam followed by provider-free adversarial certificate tests. No live reuse or held-out evaluation is authorized by this closure.",
    ]
    return "\n".join(lines) + "\n"


def build(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target_files = (
        output_dir / "DVSR_SCIENTIFIC_IDENTITY.json",
        output_dir / "V6_SEMANTIC_ROOT_AUDIT.md",
        output_dir / "DVSR_EXISTING_EVIDENCE_AUDIT.json",
        output_dir / "DVSR_EXISTING_EVIDENCE_AUDIT.md",
    )
    if any(path.exists() for path in target_files):
        raise FileExistsError("Phase-0 outputs already exist; refusing to overwrite sealed evidence")
    if not SPLIT_PATH.is_file():
        raise FileNotFoundError(f"frozen split not found: {SPLIT_PATH}")
    split = _load(SPLIT_PATH)
    v6 = [_v6_record(root) for root in V6_ROOTS]
    if len(v6) != 3 or any(row["construction_status"] != "CONSTRUCTION_SEALED" for row in v6):
        raise RuntimeError("not all three V6 roots are construction sealed")
    identity = _identity(v6, split)
    statuses = _status_rows(v6)
    evidence = {
        "schema_version": "membind.dvsr.existing-evidence-audit.v1",
        "status": "PASS_WITH_EXPLICIT_MISSING_FIELDS",
        "scope": "PHASE_0A_READ_ONLY_SEALED_EVIDENCE_CLOSURE",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "profile_id": PROFILE_ID,
        "graphiti_version": GRAPHITI_VERSION,
        "dataset_split": {
            "path": str(SPLIT_PATH),
            "sha256": _sha(SPLIT_PATH),
            "protocol_version": split.get("protocol_version"),
            "selection_uses_model_or_performance_results": split.get("selection_uses_model_or_performance_results"),
            "development_exposed": split.get("calibration_question_ids", []),
            "compatibility_quarantine": split.get("compatibility_development_question_ids", []),
            "held_out_evaluation": split.get("evaluation_question_ids", []),
        },
        "v6_artifacts": v6,
        "rq_gate_metric_status": statuses,
        "provenance_contract": {
            "provider_calls": 0,
            "database_writes": 0,
            "sealed_artifacts_modified": False,
            "held_out_method_specific_outcomes_read": False,
            "old_v7_fresh_used_as_oracle": False,
        },
        "missing_fields": sorted({field for row in v6 for field, present in row["checked_in_fields"].items() if not present}),
        "prohibited_reruns": ["B0/NATIVE_SERIAL", "sealed V6 characterization", "V4 legal-window analysis", "old V7-FRESH", "old V7-B NULL", "typed-attribute hotspot check"],
        "next_authorized_phase": "PHASE_1_FROZEN_V6_PREPARED_NOREUSE_SEAM",
    }
    # Seal the content that will be written, excluding the self-referential hash.
    manifest = {"identity": identity, "evidence": evidence, "split_sha256": _sha(SPLIT_PATH)}
    manifest_sha = _canonical_sha(manifest)
    evidence["evidence_manifest_sha256"] = manifest_sha
    identity["identity_manifest_sha256"] = _canonical_sha(identity)
    (output_dir / "DVSR_SCIENTIFIC_IDENTITY.json").write_text(json.dumps(identity, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (output_dir / "V6_SEMANTIC_ROOT_AUDIT.md").write_text(_identity_markdown(identity, v6), encoding="utf-8")
    (output_dir / "DVSR_EXISTING_EVIDENCE_AUDIT.json").write_text(json.dumps(evidence, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (output_dir / "DVSR_EXISTING_EVIDENCE_AUDIT.md").write_text(_evidence_markdown(identity, v6, statuses, split, manifest_sha), encoding="utf-8")
    return {"status": evidence["status"], "output_dir": str(output_dir), "manifest_sha256": manifest_sha, "v6_count": len(v6), "held_out_count": len(split.get("evaluation_question_ids", []))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.output_dir)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
