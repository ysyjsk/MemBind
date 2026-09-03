#!/usr/bin/env python3
"""Materialize a provenance audit for the implementation currently checked out.

This script is intentionally provider-free.  It records the working tree and
hashes the code/data/config surfaces used by the three-arm runner so evidence
from a previous source epoch cannot silently authorize a new canary.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "saturated_fixed_work_baseline_v1_3" / "structured_output_recovery"
OLD_EVIDENCE_BASE = "c62b548d18bbf0da161069be7be86750e977581c"
_MATERIALIZED_EVIDENCE_PREFIXES = (
    "saturated_fixed_work_baseline_v1_3/structured_output_recovery/",
    "mab_quality_v2_final_qa/evidence/OFFICIAL_DATASET_PARITY_REPORT.json",
    "mab_quality_v2_final_qa/evidence/OFFICIAL_DATASET_PARITY_REPORT.md",
)


def _run(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return _sha256_bytes(path.read_bytes())


def _is_materialized_evidence_path(path: str) -> bool:
    return path.startswith(_MATERIALIZED_EVIDENCE_PREFIXES)


def _implementation_diff() -> tuple[str | None, list[str]]:
    names = _run("diff", "HEAD", "--name-only", "--no-ext-diff").splitlines()
    paths = [path for path in names if path and not _is_materialized_evidence_path(path)]
    if not paths:
        return None, []
    raw = subprocess.run(
        ["git", "diff", "HEAD", "--no-ext-diff", "--binary", "--", *paths],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return (_sha256_bytes(raw) if raw else None), paths


def _aggregate(paths: list[Path]) -> tuple[str, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(set(paths), key=lambda item: item.as_posix()):
        digest = _sha256_file(path)
        if digest is None:
            continue
        try:
            display_path = path.relative_to(ROOT).as_posix()
        except ValueError:
            # Keep provenance useful without embedding environment-specific
            # absolute paths in the artifact.
            display_path = f"external:{path.name}"
        rows.append({"path": display_path, "sha256": digest})
    canonical = json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return _sha256_bytes(canonical.encode("utf-8")), rows


def _source_sets() -> dict[str, list[Path]]:
    sfwb = ROOT / "saturated_fixed_work_baseline_v1_3"
    v61 = sfwb / "src/saturated_fixed_work_baseline_v1_3/membind_v6_1"
    native = sfwb / "src/saturated_fixed_work_baseline_v1_3/membind_v5/runtime"
    evaluator = ROOT / "mab_quality_v2_final_qa"
    graphiti_candidates = [
        Path("/data/predator/ly/Mem/envs/membind-local/lib/python3.12/site-packages/graphiti_core/graphiti.py"),
        Path("/data/predator/ly/Mem/envs/membind-local/lib/python3.12/site-packages/graphiti_core/nodes.py"),
    ]
    return {
        "runner": [
            sfwb / "scripts/run_mab_v61_8b.py",
            sfwb / "scripts/formal_three_arm_harness.py",
            sfwb / "scripts/run_formal_three_arm.py",
            sfwb / "src/saturated_fixed_work_baseline_v1_3/mab_live_runner.py",
            ROOT / "scripts/finalize_preexperiment_state.py",
            ROOT / "scripts/audit_current_four_gates.py",
            ROOT / "scripts/audit_v61_h5.py",
            ROOT / "scripts/freeze_v61_fixed_method.py",
            ROOT / "scripts/current_platform_identity.py",
            ROOT / "scripts/validate_and_freeze_canary.py",
            ROOT / "scripts/materialize_resource_credit_identities.py",
            sfwb / "scripts/run_v61_scheduler_stress.py",
        ],
        "native_boundary": [
            native / "adapters/graphiti_0293.py",
            sfwb / "src/saturated_fixed_work_baseline_v1_3/native_serial_certification.py",
            *graphiti_candidates,
        ],
        "v61_source": list(v61.glob("*.py")),
        "method_spec": [
            sfwb / "structured_output_recovery/THREE_ARM_METHOD_BOUNDARIES.json",
            sfwb / "structured_output_recovery/STRUCTURED_OUTPUT_RECOVERY_POLICY.json",
            ROOT / "STRUCTURED_OUTPUT_DESIGN_DECISION.md",
            ROOT / "STRUCTURED_OUTPUT_DESIGN_DECISION.json",
        ],
        "dataset": [
            ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json",
            ROOT / "mab_quality_v2_final_qa/evidence/OFFICIAL_DATASET_PARITY_REPORT.json",
        ],
        "evaluator": list((evaluator / "src/mab_quality_v2_final_qa").glob("*.py"))
        + [evaluator / "run_mab_quality_v2.py"],
        "config": [
            sfwb / "FROZEN_BACKEND_CONFIG.json",
            sfwb / "FROZEN_CLIENT_CONFIG.json",
            sfwb / "artifacts/mab-v1-3-authority-20260824-001/frozen_config.json",
        ],
    }


def _current_tree() -> dict[str, Any]:
    status = _run("status", "--porcelain=v1")
    implementation_diff_sha256, implementation_paths = _implementation_diff()
    status_lines = status.splitlines()
    implementation_status = [
        line for line in status_lines
        if len(line) >= 4 and not _is_materialized_evidence_path(line[3:])
    ]
    return {
        "head_commit": _run("rev-parse", "HEAD").strip(),
        "branch": _run("branch", "--show-current").strip(),
        "origin_main": _run("rev-parse", "origin/main").strip(),
        "working_tree_clean": not bool(implementation_status),
        "status_porcelain_sha256": _sha256_bytes("\n".join(implementation_status).encode("utf-8")),
        "tracked_diff_sha256": implementation_diff_sha256,
        "tracked_status": implementation_status,
        "materialized_evidence_status": [
            line for line in status_lines
            if len(line) >= 4 and _is_materialized_evidence_path(line[3:])
        ],
    }


def main() -> int:
    tree = _current_tree()
    sets = _source_sets()
    aggregates: dict[str, str] = {}
    members: dict[str, list[dict[str, Any]]] = {}
    for name, paths in sets.items():
        digest, rows = _aggregate(paths)
        aggregates[name] = digest
        members[name] = rows
    bundle_payload = {
        "head_commit": tree["head_commit"],
        "tracked_diff_sha256": tree["tracked_diff_sha256"],
        "aggregates": aggregates,
        "members": members,
    }
    source_bundle_sha256 = _sha256_bytes(
        json.dumps(bundle_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    identity = {
        "schema_version": "membind.evaluated-implementation-identity.v1",
        **tree,
        "old_evidence_base_commit": OLD_EVIDENCE_BASE,
        "source_bundle_sha256": source_bundle_sha256,
        "runner_sha256": aggregates["runner"],
        "native_boundary_sha256": aggregates["native_boundary"],
        "v61_source_sha256": aggregates["v61_source"],
        "method_spec_sha256": aggregates["method_spec"],
        "dataset_sha256": aggregates["dataset"],
        "evaluator_sha256": aggregates["evaluator"],
        "config_sha256": aggregates["config"],
        "source_bundle_members": members,
        "generation_metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider_calls": 0,
            "generator": Path(__file__).relative_to(ROOT).as_posix(),
            "purpose": "current-head-provenance-only; no canary authorization",
        },
    }
    old_state = json.loads((OUT / "CURRENT_STATE.json").read_text(encoding="utf-8"))
    old_bundle = old_state.get("evaluated_source_bundle", {})
    changed_files = _run("diff", "--name-status", f"{OLD_EVIDENCE_BASE}..{tree['head_commit']}").splitlines()
    reusable = {
        "official_dataset_parity": {
            "reusable": old_bundle.get("official_dataset_parity_sha256")
            == _sha256_file(ROOT / "mab_quality_v2_final_qa/evidence/OFFICIAL_DATASET_PARITY_REPORT.json"),
            "reason": "dataset/evaluator identity is independent of the two changed runtime modules only if hashes match",
        },
        "native_and_structured_output": {
            "reusable": False,
            "reason": "old evidence is bound to c62b548 and must not authorize 58af232",
        },
    }
    audit = {
        "schema_version": "membind.current-head-gap-audit.v1",
        "status": "CURRENT_HEAD_PROVENANCE_AUDITED_G1_G4_RECOMPUTED_H5_PENDING_CANARY",
        "current": tree,
        "old_evidence": {
            "base_code_commit": OLD_EVIDENCE_BASE,
            "current_state": old_state.get("state"),
            "evaluated_source_bundle_sha256": old_state.get("evaluated_source_bundle_sha256"),
            "canary_authorized_in_old_epoch": old_state.get("state") == "CODE_READY_FOR_THREE_ARM_ENGINEERING_CANARY",
        },
        "file_level_differences_since_old_evidence": changed_files,
        "known_runtime_changes": [
            "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/mab_live_runner.py",
            "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v6_1/mab.py",
        ],
        "reusable_evidence": reusable,
        "recomputed_current_head": [
            "G1 native identity/fairness against current runner",
            "G2 V6.1 UUID/publication/recovery against current mab.py",
            "G3 structured-output actual callsite and runtime observability against current source bundle",
            "G4 dataset/evaluator identity linkage to current implementation identity",
            "H5 scheduler/resource-credit audit and provider-free stress suite",
        ],
        "authorization_rule": "old CODE_READY is historical only; current HEAD canary/formal authorization requires exact current EVALUATED_IMPLEMENTATION_IDENTITY plus matching G1-G4 evidence",
        "generated_at": identity["generation_metadata"]["generated_at"],
        "provider_calls": 0,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "EVALUATED_IMPLEMENTATION_IDENTITY.json").write_text(
        json.dumps(identity, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "CURRENT_HEAD_GAP_AUDIT.json").write_text(
        json.dumps(audit, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    markdown = [
        "# Current HEAD Gap Audit",
        "",
        f"Status: `{audit['status']}`.",
        "",
        f"Current HEAD: `{tree['head_commit']}`; branch: `{tree['branch']}`; origin/main: `{tree['origin_main']}`.",
        f"Working tree clean: `{tree['working_tree_clean']}`; tracked diff SHA-256: `{tree['tracked_diff_sha256']}`.",
        "",
        f"Historical evidence base: `{OLD_EVIDENCE_BASE}`. Its `CODE_READY` state is retained as historical evidence only and cannot authorize the current HEAD.",
        "",
        "Known runtime changes since that evidence epoch:",
        *[f"- `{item}`" for item in audit["known_runtime_changes"]],
        "",
        "Current implementation identity was generated provider-free. G1-G4 and H5 must be recomputed or source-hash matched before canary authorization.",
        "",
        f"Complete source bundle SHA-256: `{source_bundle_sha256}`.",
    ]
    (OUT / "CURRENT_HEAD_GAP_AUDIT.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    print(json.dumps({"status": audit["status"], "head_commit": tree["head_commit"], "source_bundle_sha256": source_bundle_sha256}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
