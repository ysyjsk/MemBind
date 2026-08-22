#!/usr/bin/env python3
"""Gold-only MemOps state-dependent hazard audit and cohort freeze.

The audit intentionally never reads a B0/B1 result.  It scans the official
Update and TrajectoryOps evidence, computes structural dependency metadata,
and freezes a deterministic replication cohort for the existing v1.3 runner.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping


QUALIFYING_QA_TYPES = {"StateTransition", "CandidateDisambiguation", "StateTrajectory"}
UPDATE_QUOTA = 18
TRAJECTORY_QUOTA = 6


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"ARTIFACT_ALREADY_EXISTS:{path}")
    path.parent.mkdir(parents=True, exist_ok=False if path.parent == path.parent.parent else True)
    body = dict(value)
    body["payload_sha256"] = sha256(body)
    path.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def sample_id(path: Path) -> str:
    for suffix in ("_trajectory_ops.json", "_update.json"):
        if path.name.endswith(suffix):
            return path.name[: -len(suffix)]
    raise ValueError(f"unsupported evidence filename: {path.name}")


def adapter_transition_pairs(operations: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Mirror the existing adapter's confirmed update-pair predicate."""

    pairs: list[dict[str, Any]] = []
    for index, old in enumerate(operations):
        if (
            old.get("type") != "update"
            or old.get("validity") != "confirmed"
            or old.get("old_value") is None
            or old.get("new_value") is None
            or old.get("old_value") == old.get("new_value")
        ):
            continue
        old_target = old.get("target") if isinstance(old.get("target"), Mapping) else {}
        old_segment = (old.get("trigger_span") or {}).get("segment_index")
        for new_index, new in enumerate(operations[index + 1 :], index + 1):
            if (
                new.get("type") != "update"
                or new.get("validity") != "confirmed"
                or new.get("old_value") is None
                or new.get("new_value") is None
                or new.get("old_value") == new.get("new_value")
            ):
                continue
            new_target = new.get("target") if isinstance(new.get("target"), Mapping) else {}
            new_segment = (new.get("trigger_span") or {}).get("segment_index")
            if (
                old_target.get("target_id") == new_target.get("target_id")
                and old.get("new_value") == new.get("old_value")
                and str(old.get("old_value")) != str(new.get("new_value"))
                and old_segment != new_segment
            ):
                pairs.append(
                    {
                        "old_operation_id": str(old.get("operation_id") or index),
                        "new_operation_id": str(new.get("operation_id") or new_index),
                        "target_id": str(old_target.get("target_id") or ""),
                        "target_name": str(old_target.get("target_name") or old_target.get("target_id") or ""),
                        "old_value": str(old.get("old_value")),
                        "new_value": str(new.get("new_value")),
                        "old_segment_index": old_segment,
                        "new_segment_index": new_segment,
                    }
                )
    return pairs


def qualifying_qa(raw: Mapping[str, Any], transition_segments: set[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    answers = raw.get("answer")
    if not isinstance(answers, list):
        return rows
    for answer in answers:
        if not isinstance(answer, Mapping) or answer.get("evaluation_type") not in QUALIFYING_QA_TYPES:
            continue
        provenance = answer.get("gold_provenance")
        provenance_segments = {
            row.get("segment_index")
            for row in provenance
            if isinstance(row, Mapping) and isinstance(row.get("segment_index"), int)
        } if isinstance(provenance, list) else set()
        if not provenance_segments & transition_segments:
            continue
        rows.append(
            {
                "evaluation_type": answer.get("evaluation_type"),
                "question_pair_id": answer.get("question_pair_id"),
                "evaluation_setting": answer.get("evaluation_setting"),
                "gold_provenance_segments": sorted(provenance_segments),
            }
        )
    return rows


def load_adapter_parseability(repo_root: Path, paths: list[Path]) -> dict[str, Any]:
    """Use the current public parser only to record live-compatibility status."""

    v13 = repo_root / "saturated_fixed_work_baseline_v1_3" / "src"
    v12 = repo_root / "saturated_fixed_work_baseline_v1_2" / "src"
    sys.path[:0] = [str(v13), str(v12)]
    try:
        from saturated_fixed_work_baseline_v1_3.memops_adapter import parse_memops_sample
    except Exception as exc:  # pragma: no cover - environment diagnostic
        return {path.name: {"parseable": False, "error": f"IMPORT:{exc}"} for path in paths}
    result: dict[str, Any] = {}
    for path in paths:
        try:
            parsed = parse_memops_sample(path)
            result[path.name] = {
                "parseable": True,
                "qa_count": len(parsed.questions),
                "qa_types": sorted({question.evaluation_type for question in parsed.questions}),
                "transition_count": len(parsed.transitions),
            }
        except Exception as exc:
            result[path.name] = {"parseable": False, "error": str(exc)}
    return result


def audit_one(path: Path, adapter_status: Mapping[str, Any]) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    operations = [row for row in raw.get("operations", []) if isinstance(row, Mapping)]
    conversations = raw.get("conversations") if isinstance(raw.get("conversations"), list) else []
    targets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    confirmed_mutations: list[Mapping[str, Any]] = []
    chain_lengths: Counter[str] = Counter()
    for operation in operations:
        target = operation.get("target") if isinstance(operation.get("target"), Mapping) else {}
        target_id = str(target.get("target_id") or "")
        if target_id:
            targets[target_id].append(operation)
        if operation.get("validity") == "confirmed" and operation.get("chain_id"):
            chain_lengths[str(operation["chain_id"])] += 1
        if (
            operation.get("type") == "update"
            and operation.get("validity") == "confirmed"
            and operation.get("old_value") is not None
            and operation.get("new_value") is not None
            and operation.get("old_value") != operation.get("new_value")
        ):
            confirmed_mutations.append(operation)

    mutation_counts = Counter(
        str((operation.get("target") or {}).get("target_id") or "")
        for operation in confirmed_mutations
    )
    pairs = adapter_transition_pairs(operations)
    transition_segments = {
        int(value)
        for pair in pairs
        for value in (pair.get("old_segment_index"), pair.get("new_segment_index"))
        if isinstance(value, int)
    }
    dependency_sources = sorted(
        {
            int(pair["new_segment_index"])
            for pair in pairs
            if isinstance(pair.get("new_segment_index"), int)
        }
    )
    mutation_sources = sorted(
        {
            int((operation.get("trigger_span") or {}).get("segment_index"))
            for operation in confirmed_mutations
            if isinstance((operation.get("trigger_span") or {}).get("segment_index"), int)
        }
    )
    tentative_count = sum(operation.get("validity") == "tentative" for operation in operations)
    retracted_count = sum(operation.get("validity") == "retracted" for operation in operations)
    forget_count = sum(operation.get("type") == "forget" for operation in operations)
    reflect_count = sum(operation.get("type") == "reflect" for operation in operations)
    knobs = raw.get("difficulty_knobs") if isinstance(raw.get("difficulty_knobs"), Mapping) else {}
    update_knob = knobs.get("update_chain") if isinstance(knobs.get("update_chain"), Mapping) else {}
    recency_knob = knobs.get("recency_trap") if isinstance(knobs.get("recency_trap"), Mapping) else {}
    qa_rows = qualifying_qa(raw, transition_segments)
    adapter = dict(adapter_status)
    source_count = len(conversations)
    max_mutations = max(mutation_counts.values() or [0])
    max_chain = max(chain_lengths.values() or [0])
    eligibility: list[str] = []
    structural = True
    for check, passed in (
        ("SAME_TARGET_CONFIRMED_MUTATION_COUNT_GE2", max_mutations >= 2),
        ("CONFIRMED_CHAIN_LENGTH_GE3", max_chain >= 3),
        ("CROSS_SOURCE_STATE_DEPENDENCY_PRESENT", bool(pairs)),
        ("SOURCE_COUNT_GE2", source_count >= 2),
        ("CURRENT_ADAPTER_QUALIFYING_QA_PRESENT", bool(qa_rows)),
        ("CURRENT_ADAPTER_PARSEABLE", bool(adapter.get("parseable"))),
    ):
        eligibility.append(f"{check}:{'PASS' if passed else 'FAIL'}")
        structural = structural and passed
    score = (
        max_mutations * 100
        + max_chain * 20
        + len(pairs) * 15
        + len(dependency_sources) * 5
        + len(raw.get("state_checkpoints") or []) * 3
        + int(bool(recency_knob.get("enabled"))) * 2
        + min(source_count, 5)
    )
    return {
        "sample_id": sample_id(path),
        "operation_type": raw.get("operation_type"),
        "source_file": str(path),
        "source_sha256": file_sha256(path),
        "source_count": source_count,
        "segment_indices": [row.get("segment_index") for row in conversations if isinstance(row, Mapping)],
        "operations_total": len(operations),
        "target_ids": sorted(targets),
        "target_count": len(targets),
        "confirmed_update_operations": len(confirmed_mutations),
        "confirmed_mutation_counts_by_target": dict(sorted(mutation_counts.items())),
        "max_same_target_confirmed_mutations": max_mutations,
        "confirmed_chain_lengths": dict(sorted(chain_lengths.items())),
        "max_confirmed_chain_length": max_chain,
        "confirmed_transition_pairs": pairs,
        "state_dependency_pair_count": len(pairs),
        "state_dependency_sources": dependency_sources,
        "mutation_source_indices": mutation_sources,
        "theoretical_overlap_width": source_count,
        "dependent_source_overlap_width": len(dependency_sources),
        "tentative_count": tentative_count,
        "retracted_count": retracted_count,
        "forget_count": forget_count,
        "reflect_count": reflect_count,
        "recency_trap": {
            "enabled": bool(recency_knob.get("enabled")),
            "trap_type": recency_knob.get("trap_type"),
        },
        "update_chain_knob": dict(update_knob),
        "state_checkpoint_count": len(raw.get("state_checkpoints") or []) if isinstance(raw.get("state_checkpoints"), list) else 0,
        "qualifying_qa_count": len(qa_rows),
        "qualifying_qa_types": sorted({row["evaluation_type"] for row in qa_rows}),
        "adapter_compatibility": adapter,
        "hazard_score": score,
        "eligibility": eligibility,
        "structurally_eligible": structural,
        "selection_basis": "gold-only structure; no B0/B1 result fields read",
        "raw_difficulty_knobs": knobs,
    }


def manifest_row(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = json.loads(Path(str(row["source_file"])).read_text(encoding="utf-8"))
    target_id = next(iter(row["target_ids"]), "")
    target_name = ""
    for operation in raw.get("operations", []):
        target = operation.get("target") if isinstance(operation, Mapping) else {}
        if isinstance(target, Mapping) and target.get("target_id") == target_id:
            target_name = str(target.get("target_name") or target_id)
            break
    cohort_sample_id = f"{row['sample_id']}__{row['operation_type']}"
    return {
        "sample_id": cohort_sample_id,
        "cohort_sample_id": cohort_sample_id,
        "official_sample_id": row["sample_id"],
        "operation_type": row["operation_type"],
        "history_id": f"memops-{str(row['sample_id']).lower()}-{str(row['operation_type']).lower()}",
        "target_id": target_id,
        "target_name": target_name,
        "source_file": row["source_file"],
        "source_sha256": row["source_sha256"],
        "hazard_score": row["hazard_score"],
        "state_dependency_pair_count": row["state_dependency_pair_count"],
        "max_same_target_confirmed_mutations": row["max_same_target_confirmed_mutations"],
        "max_confirmed_chain_length": row["max_confirmed_chain_length"],
        "qualifying_qa_types": row["qualifying_qa_types"],
    }


def freeze(output_root: Path, memops_root: Path, repo_root: Path) -> dict[str, Any]:
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError("HAZARD_AUDIT_ROOT_MUST_BE_NEW")
    output_root.mkdir(parents=True, exist_ok=False)
    evidence_root = memops_root / "generated_result" / "2-evidence_conversation"
    paths = sorted(evidence_root.glob("*_update.json")) + sorted(evidence_root.glob("*_trajectory_ops.json"))
    if not paths:
        raise RuntimeError("MEMOPS_EVIDENCE_EMPTY")
    adapter_status = load_adapter_parseability(repo_root, paths)
    rows = [audit_one(path, adapter_status.get(path.name, {"parseable": False, "error": "MISSING"})) for path in paths]
    eligible = [row for row in rows if row["structurally_eligible"]]
    by_type = {
        "Update": sorted((row for row in eligible if row["operation_type"] == "Update"), key=lambda row: (-row["hazard_score"], row["sample_id"], row["source_file"])),
        "TrajectoryOps": sorted((row for row in eligible if row["operation_type"] == "TrajectoryOps"), key=lambda row: (-row["hazard_score"], row["sample_id"], row["source_file"])),
    }
    if len(by_type["Update"]) < UPDATE_QUOTA or len(by_type["TrajectoryOps"]) < TRAJECTORY_QUOTA:
        raise RuntimeError("HAZARD_COHORT_QUOTA_UNAVAILABLE")
    selected = by_type["Update"][:UPDATE_QUOTA] + by_type["TrajectoryOps"][:TRAJECTORY_QUOTA]
    selected_ids = [f"{row['sample_id']}__{row['operation_type']}" for row in selected]
    selected_set = {(row["sample_id"], row["operation_type"]) for row in selected}
    evaluator_path = memops_root / "5.5-evaluate_operation_metrics.py"
    evaluator_hash = file_sha256(evaluator_path)
    selected_manifest_rows = [manifest_row(row) for row in selected]
    for row in selected:
        raw_path = Path(row["source_file"])
        envelope = {
            "schema_version": "sfwb.v1.3.memops-hazard-frozen-sample.v1",
            "sample_id": row["sample_id"],
            "source_file": row["source_file"],
            "source_sha256": row["source_sha256"],
            "sample": json.loads(raw_path.read_text(encoding="utf-8")),
        }
        write_json(output_root / "replication_cohort" / "frozen_samples" / f"{row['sample_id']}__{row['operation_type']}.json", envelope)
    selection_manifest = {
        "schema_version": "sfwb.v1.3.memops-hazard-replication-selection.v1",
        "status": "OFFLINE_HAZARD_COHORT_FROZEN",
        "benchmark": "MemOps",
        "pilot_policy_set": ["B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC"],
        "sample_order": selected_ids,
        "sample_count": len(selected),
        "samples": selected_manifest_rows,
        "official_qa_evaluator": {
            "path": str(evaluator_path),
            "sha256": evaluator_hash,
            "prompt_builder": "build_evaluation_prompt",
            "result_parser": "parse_judge_metrics",
        },
        "selection_basis": "gold-only hazard score; fixed type quotas Update=18 and TrajectoryOps=6; no B0/B1 artifacts read",
        "replication_contract": {
            "replications_per_method": 3,
            "fresh_namespace_per_replication": True,
            "fresh_run_root_per_replication": True,
            "same_order_across_methods": True,
            "b0_failure_does_not_gate_b1": True,
            "v5_started": False,
        },
    }
    write_json(output_root / "replication_cohort" / "selection_manifest.json", selection_manifest)
    audit = {
        "schema_version": "sfwb.v1.3.memops-hazard-audit.v1",
        "status": "OFFLINE_HAZARD_AUDIT_PASS",
        "benchmark": "MemOps",
        "evidence_root": str(evidence_root),
        "source_of_truth": "official gold operation/conversation/answer/state_checkpoints JSON only",
        "result_blindness": {
            "b0_b1_artifacts_read": False,
            "selection_uses_b0_b1_outcomes": False,
            "selection_uses_final_graph": False,
        },
        "audit_counts": {
            "evidence_files": len(rows),
            "operation_type_counts": dict(Counter(row["operation_type"] for row in rows)),
            "adapter_parseable_counts": dict(Counter(row["operation_type"] for row in rows if row["adapter_compatibility"].get("parseable"))),
            "structurally_eligible_count": len(eligible),
            "structurally_eligible_by_type": dict(Counter(row["operation_type"] for row in eligible)),
            "selected_count": len(selected),
            "selected_by_type": dict(Counter(row["operation_type"] for row in selected)),
            "max_mutation_distribution": dict(Counter(row["max_same_target_confirmed_mutations"] for row in rows)),
            "chain_length_distribution": dict(Counter(row["max_confirmed_chain_length"] for row in rows)),
        },
        "eligibility_predicate": {
            "same_target_confirmed_mutations_at_least": 2,
            "max_confirmed_chain_length_at_least": 3,
            "cross_source_state_dependency_pair_at_least": 1,
            "source_count_at_least": 2,
            "current_adapter_qualifying_qa_required": True,
            "adapter_parseable_required_for_live_replication": True,
        },
        "selection_rule": {
            "score": "100*max_same_target_mutations + 20*max_chain_length + 15*dependency_pairs + 5*dependent_sources + 3*trajectory_checkpoints + 2*recency_trap + min(source_count,5)",
            "tie_break": "sample_id then source_file, ascending",
            "quotas": {"Update": UPDATE_QUOTA, "TrajectoryOps": TRAJECTORY_QUOTA},
            "reason": "retain both Update and TrajectoryOps state-checkpoint surfaces without using semantic outcomes",
        },
        "samples": rows,
        "selected_samples": selected,
        "selected_sample_manifest_rows": selected_manifest_rows,
        "replication_cohort_root": str((output_root / "replication_cohort").resolve()),
        "next_gate": "OFFLINE_HAZARD_ALIGNMENT_CONFIRMED_LIVE_REPLICATION_AUTHORIZED",
    }
    write_json(output_root / "hazard_audit.json", audit)
    report_lines = [
        "# MemOps Offline Hazard Audit",
        "",
        f"Status: `{audit['status']}`. Evidence files: `{len(rows)}`; structurally eligible: `{len(eligible)}`; frozen replication cohort: `{len(selected)}`.",
        "",
        "Selection is gold-only and result-blind. The cohort has 18 `Update` and 6 `TrajectoryOps` samples, all with at least two same-target confirmed mutations, a cross-source confirmed transition dependency, a confirmed chain of length at least three, current-adapter qualifying QA, and current-adapter parseability.",
        "",
        "The structural alignment is sufficient to authorize the next phase: three fresh B0 and three fresh B1 replications per frozen sample. This is an authorization of the replication protocol only; it is not evidence that a race has occurred.",
        "",
        "Mechanism evidence before live: `NOT_ESTABLISHED`. Gold structure proves a legal predecessor dependency and theoretical overlap, but cannot prove actual admission order, durable frontier, graph-read visibility, candidate set, request fingerprint, or semantic consequence.",
        "",
        "## Frozen Cohort",
        "",
        "| # | Sample | Type | Hazard score | Mutations | Chain | Dependency pairs | Checkpoints | QA types |",
        "|---:|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for index, row in enumerate(selected, 1):
        report_lines.append(
            f"| {index} | {row['sample_id']} | {row['operation_type']} | {row['hazard_score']} | {row['max_same_target_confirmed_mutations']} | {row['max_confirmed_chain_length']} | {row['state_dependency_pair_count']} | {row['state_checkpoint_count']} | {', '.join(row['qualifying_qa_types'])} |"
        )
    report_lines += [
        "",
        "## Next-Phase Evidence Contract",
        "",
        "The replication must establish or explicitly mark each link: unordered admission; predecessor publication durable status; first state-dependent graph-read frontier; candidate/fingerprint observation; resolution request fingerprint; additional/divergent work; and semantic consequence. Missing any link is `NOT_ESTABLISHED`, not an inferred race.",
        "",
        "No live service was started by this audit.",
        "",
    ]
    report_path = output_root / "MEMOPS_HAZARD_AUDIT.md"
    if report_path.exists():
        raise RuntimeError("ARTIFACT_ALREADY_EXISTS:report")
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    return {
        "status": audit["status"],
        "output_root": str(output_root.resolve()),
        "selected_count": len(selected),
        "selected_ids": selected_ids,
        "structurally_eligible_count": len(eligible),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--memops-root", type=Path, default=Path("/data/predator/ly/third_party/MemOps"))
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args.output_root.resolve(), args.memops_root.resolve(), args.repo_root.resolve()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
