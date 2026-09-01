#!/usr/bin/env python3
"""Compute the pre-experiment gate from executable evidence artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QA_SRC = ROOT / "mab_quality_v2_final_qa/src"
SRC = ROOT / "saturated_fixed_work_baseline_v1_3/src"
if str(QA_SRC) not in sys.path:
    sys.path.insert(0, str(QA_SRC))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
EVIDENCE = ROOT / "saturated_fixed_work_baseline_v1_3/structured_output_recovery"
DATA_EVIDENCE = ROOT / "mab_quality_v2_final_qa/evidence/OFFICIAL_DATASET_PARITY_REPORT.json"


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"evidence is not an object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _uuid_semantics_probe() -> dict:
    from graphiti_core.errors import NodeNotFoundError
    from graphiti_core.nodes import EpisodicNode
    from saturated_fixed_work_baseline_v1_3.mab_live_runner import _mab_graphiti_kwargs, _mab_publication_idempotency_key, episode_from_input
    from saturated_fixed_work_baseline_v1_3.workload_contract import EpisodeInput

    class Driver:
        graph_operations_interface = None
        provider = None
        async def execute_query(self, *_args, **_kwargs):
            return [], None, None

    observed = None
    try:
        asyncio.run(EpisodicNode.get_by_uuid(Driver(), "00000000-0000-4000-8000-000000000001"))
    except NodeNotFoundError:
        observed = "NodeNotFoundError"
    episode = episode_from_input(EpisodeInput(context_id="uuid-proof", source_sequence=0, episode_id="uuid-proof-0", reference_time="2026-01-01T00:00:00Z", body="proof"))
    kwargs = _mab_graphiti_kwargs(episode, namespace="uuid-proof", include_uuid=False)
    key_a = _mab_publication_idempotency_key(episode, namespace="uuid-proof")
    key_b = _mab_publication_idempotency_key(episode, namespace="uuid-proof")
    status = "PASS" if observed == "NodeNotFoundError" and "uuid" not in kwargs and key_a == key_b else "FAIL"
    return {"status": status, "fresh_uuid_lookup": observed, "fresh_write_uuid_omitted": "uuid" not in kwargs, "stable_key_repeatable": key_a == key_b, "publication_guarantee": "AT_LEAST_ONCE_WITH_STABLE_IDEMPOTENCY_KEY"}


def main() -> int:
    identity = _read(EVIDENCE / "NATIVE_BASELINE_IDENTITY.json")
    native = _read(EVIDENCE / "NATIVE_IMMUTABILITY_REPORT.json")
    structured = _read(EVIDENCE / "STRUCTURED_OUTPUT_QUALIFICATION_RESULT.json")
    parity = _read(DATA_EVIDENCE)
    uuid_probe = _uuid_semantics_probe()
    base_code_commit = _git_head()
    generator_source_sha256 = _sha256_file(Path(__file__))
    evaluated_source_bundle = {
        "native_identity_sha256": _sha256_file(EVIDENCE / "NATIVE_BASELINE_IDENTITY.json"),
        "native_report_sha256": _sha256_file(EVIDENCE / "NATIVE_IMMUTABILITY_REPORT.json"),
        "structured_qualification_sha256": _sha256_file(EVIDENCE / "STRUCTURED_OUTPUT_QUALIFICATION_RESULT.json"),
        "official_dataset_parity_sha256": _sha256_file(DATA_EVIDENCE),
        "generator_source_sha256": generator_source_sha256,
        "base_code_commit": base_code_commit,
    }
    evaluated_source_bundle_sha256 = hashlib.sha256(
        json.dumps(evaluated_source_bundle, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    native_pass = identity.get("status") == "PASS" and native.get("prohibited_difference_count") == 0 and native.get("unknown_comparison_count") == 0
    r1_pass = structured.get("r1_actual_callsite_inventory") == "PASS_ACTUAL_RUNTIME_CALLSITE"
    dataset_pass = parity.get("status") == "PASS" and parity.get("selection") == "OFFICIAL_AS_PUBLISHED_5_RECORDS"
    if not native_pass:
        state = "BLOCKED_NATIVE_IMMUTABILITY"
    elif uuid_probe["status"] != "PASS":
        state = "BLOCKED_V61_UUID_SEMANTICS"
    elif not r1_pass:
        state = "BLOCKED_ACTUAL_CALLSITE_COVERAGE"
    elif not dataset_pass:
        state = "BLOCKED_OFFICIAL_DATASET_PARITY"
    else:
        state = "CODE_READY_FOR_THREE_ARM_ENGINEERING_CANARY"
    body = {
        "schema_version": "membind.preexperiment.current-state.v2",
        "state": state,
        "native_immutability": {"status": "PASS" if native_pass else "FAIL", "identity_status": identity.get("status"), "prohibited_difference_count": native.get("prohibited_difference_count"), "unknown_comparison_count": native.get("unknown_comparison_count")},
        "v61_uuid_semantics": uuid_probe,
        "structured_output": {"status": structured.get("status"), "r1_schema_boundedness": structured.get("r1_schema_boundedness"), "r1_actual_callsite_inventory": structured.get("r1_actual_callsite_inventory"), "r2_classified_recovery": structured.get("r2_classified_recovery"), "r3_publication": structured.get("r3_publication"), "r4_finalizer": structured.get("r4_finalizer")},
        "official_dataset": {"status": parity.get("status"), "selection": parity.get("selection"), "differences": len(parity.get("differences", [])), "anomaly_disclosure": parity.get("anomaly_disclosure", [])},
        "provider_calls": 0,
        "formal_history_executed": False,
        "engineering_canary_executed": False,
        "three_arm_experiment_created": False,
        "evidence_inputs": {"native_identity": str((EVIDENCE / "NATIVE_BASELINE_IDENTITY.json").resolve()), "native_report": str((EVIDENCE / "NATIVE_IMMUTABILITY_REPORT.json").resolve()), "structured_qualification": str((EVIDENCE / "STRUCTURED_OUTPUT_QUALIFICATION_RESULT.json").resolve()), "official_dataset_parity": str(DATA_EVIDENCE.resolve())},
        "evaluated_source_bundle": evaluated_source_bundle,
        "evaluated_source_bundle_sha256": evaluated_source_bundle_sha256,
        "generator_source_sha256": generator_source_sha256,
        "base_code_commit": base_code_commit,
    }
    body["status_reason"] = "State is computed from native, UUID, actual-callsite, and official-dataset evidence; no prior state is reused."
    decision = {"schema_version": "membind.preexperiment.final-decision.v2", "decision": state, "status": state, "canary_authorized": state == "CODE_READY_FOR_THREE_ARM_ENGINEERING_CANARY", "formal_three_arm_authorized": False, "provider_calls": 0, "inputs": body["evidence_inputs"], "reason": body["status_reason"], "evaluated_source_bundle": evaluated_source_bundle, "evaluated_source_bundle_sha256": evaluated_source_bundle_sha256, "generator_source_sha256": generator_source_sha256, "base_code_commit": base_code_commit}
    (EVIDENCE / "CURRENT_STATE.json").write_text(json.dumps(body, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (EVIDENCE / "FINAL_DECISION.json").write_text(json.dumps(decision, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"state": state, "native": native_pass, "uuid": uuid_probe["status"], "r1": r1_pass, "dataset": dataset_pass}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
