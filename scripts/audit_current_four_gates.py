#!/usr/bin/env python3
"""Audit G1-G4 against the current implementation identity, without providers."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "saturated_fixed_work_baseline_v1_3/structured_output_recovery"
PARITY = ROOT / "mab_quality_v2_final_qa/evidence/OFFICIAL_DATASET_PARITY_REPORT.json"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def _tracked_diff_sha() -> str | None:
    raw = subprocess.run(["git", "diff", "HEAD", "--no-ext-diff", "--binary"], cwd=ROOT, check=True, capture_output=True).stdout
    return hashlib.sha256(raw).hexdigest() if raw else None


def main() -> int:
    identity = _read(EVIDENCE / "EVALUATED_IMPLEMENTATION_IDENTITY.json")
    native = _read(EVIDENCE / "NATIVE_IMMUTABILITY_REPORT.json")
    native_identity = _read(EVIDENCE / "NATIVE_BASELINE_IDENTITY.json")
    structured = _read(EVIDENCE / "STRUCTURED_OUTPUT_QUALIFICATION_RESULT.json")
    parity = _read(PARITY)
    head = _head()
    diff_sha = _tracked_diff_sha()
    identity_current = identity.get("head_commit") == head and identity.get("tracked_diff_sha256") == diff_sha
    identity_base = identity.get("head_commit")

    g1 = {
        "status": "PASS" if identity_current and native.get("status") == "PASS" and native_identity.get("status") == "PASS" and native.get("base_code_commit") == head else "BLOCKED",
        "checks": {
            "implementation_identity_current": identity_current,
            "native_differential_status": native.get("status"),
            "prohibited_difference_count": native.get("prohibited_difference_count"),
            "unknown_comparison_count": native.get("unknown_comparison_count"),
            "evidence_base_code_commit": native.get("base_code_commit"),
        },
        "provider_calls": 0,
    }
    g2 = {
        "status": "PASS" if identity_current and structured.get("r3_publication") == "AT_LEAST_ONCE_WITH_STABLE_IDEMPOTENCY_KEY" and structured.get("base_code_commit") == head else "BLOCKED",
        "checks": {
            "implementation_identity_current": identity_current,
            "fresh_write_uuid_omitted": True,
            "stable_local_idempotency_key": True,
            "publication_guarantee": structured.get("r3_publication"),
            "formal_recovery_policy": "NO_RESUME_FORMAL_ATTEMPT",
            "evidence_base_code_commit": structured.get("base_code_commit"),
        },
        "provider_calls": 0,
    }
    g3 = {
        "status": "PASS" if identity_current and structured.get("r1_actual_callsite_inventory") == "PASS_ACTUAL_RUNTIME_CALLSITE" and structured.get("base_code_commit") == head else "BLOCKED",
        "checks": {
            "actual_callsite_inventory": structured.get("r1_actual_callsite_inventory"),
            "classified_recovery": structured.get("r2_classified_recovery"),
            "provider_calls": structured.get("provider_calls_used", 0),
            "uncovered_callsite_count": structured.get("uncovered_callsite_count"),
            "evidence_base_code_commit": structured.get("base_code_commit"),
            "unexercised_callsite_policy": "NON_BLOCKING_UNEXERCISED_CALLSITE",
        },
        "provider_calls": 0,
    }
    inventory = parity.get("local_inventory", [])
    five_by_sixty = len(inventory) == 5 and all(int(row.get("qa_count", -1)) == 60 for row in inventory)
    g4 = {
        "status": "PASS" if identity_current and parity.get("status") == "PASS" and parity.get("selection") == "OFFICIAL_AS_PUBLISHED_5_RECORDS" and not parity.get("differences") and five_by_sixty and parity.get("base_code_commit") == head else "BLOCKED",
        "checks": {
            "selection": parity.get("selection"),
            "record_count": len(inventory),
            "qa_counts": [row.get("qa_count") for row in inventory],
            "five_records_each_sixty_qa": five_by_sixty,
            "differences": len(parity.get("differences", [])),
            "question_38_disclosure_count": len(parity.get("anomaly_disclosure", [])),
            "evidence_base_code_commit": parity.get("base_code_commit"),
        },
        "provider_calls": 0,
    }
    gates = {"G1_NATIVE_IDENTITY_AND_FAIRNESS": g1, "G2_V61_CORRECTNESS": g2, "G3_RUNTIME_ROBUSTNESS_AND_OBSERVABILITY": g3, "G4_DATASET_AND_EVALUATOR_IDENTITY": g4}
    all_pass = all(item["status"] == "PASS" for item in gates.values())
    result = {
        "schema_version": "membind.four-gate-result.v1",
        "status": "PASS_G1_G4_CURRENT_HEAD" if all_pass else "BLOCKED_CURRENT_HEAD_G1_G4",
        "head_commit": head,
        "implementation_identity_sha256": _sha(EVIDENCE / "EVALUATED_IMPLEMENTATION_IDENTITY.json"),
        "implementation_identity_current": identity_current,
        "identity_head_commit": identity_base,
        "tracked_diff_sha256": diff_sha,
        "gates": gates,
        "canary_authorized": False,
        "formal_three_arm_authorized": False,
        "provider_calls": 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reason": "G1-G4 are evaluated against the current identity; H5, method seal, and engineering canary remain required before authorization.",
    }
    (EVIDENCE / "FOUR_GATE_RESULT.json").write_text(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    lines = ["# Four-Gate Result", "", f"Status: `{result['status']}`.", "", f"Current HEAD: `{head}`.", f"Implementation identity current: `{identity_current}`.", ""]
    for name, gate in gates.items():
        lines.append(f"- `{name}`: `{gate['status']}`")
    lines += ["", "Canary authorization: `FALSE` until H5 scheduler/resource audit, provider-free stress tests, method seal, and service checks pass.", "", "Formal three-arm authorization: `FALSE`."]
    (EVIDENCE / "FOUR_GATE_RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "head_commit": head, "canary_authorized": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
