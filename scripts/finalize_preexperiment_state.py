#!/usr/bin/env python3
"""Compute the pre-experiment gate from executable evidence artifacts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
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


def _commit_exists(commit: str) -> bool:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        return False
    return subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
    ).returncode == 0


_MATERIALIZED_EVIDENCE_PREFIXES = (
    "saturated_fixed_work_baseline_v1_3/structured_output_recovery/",
    "mab_quality_v2_final_qa/evidence/OFFICIAL_DATASET_PARITY_REPORT.json",
    "mab_quality_v2_final_qa/evidence/OFFICIAL_DATASET_PARITY_REPORT.md",
)


def _is_materialized_evidence_path(path: str) -> bool:
    """Return whether a path is produced while materializing provenance.

    Provenance, gate, and method-seal artifacts are expected to change after
    the evaluated source commit is selected.  They are evidence outputs, not
    implementation input.  Runtime/source changes remain included in the
    identity diff and therefore still invalidate the epoch.
    """

    return path.startswith(_MATERIALIZED_EVIDENCE_PREFIXES)


def _tracked_diff_sha256() -> str | None:
    """Hash tracked implementation changes while ignoring evidence outputs."""

    names = subprocess.run(
        ["git", "diff", "HEAD", "--name-only", "--no-ext-diff"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    implementation_paths = [
        path for path in names if path and not _is_materialized_evidence_path(path)
    ]
    if not implementation_paths:
        return None
    value = subprocess.run(
        ["git", "diff", "HEAD", "--no-ext-diff", "--binary", "--", *implementation_paths],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return hashlib.sha256(value).hexdigest() if value else None


def _evaluation_base_commit(override: str | None = None) -> tuple[str, str]:
    """Select the commit whose source was evaluated, not the artifact commit.

    Evidence is commonly materialized and committed after the code it tests.
    Re-reading ``HEAD`` on a later finalizer run would silently rebind the
    evidence to that materialization commit and create a provenance cycle.
    An explicit override is required to start a new evaluation epoch.
    """

    if override:
        if not _commit_exists(override):
            raise ValueError(f"invalid --base-code-commit: {override!r}")
        return override, "cli"
    configured = os.environ.get("MEMBIND_EVALUATION_BASE_COMMIT")
    if configured:
        if not _commit_exists(configured):
            raise ValueError("invalid MEMBIND_EVALUATION_BASE_COMMIT")
        return configured, "environment"
    candidates: list[tuple[str, str]] = []
    try:
        existing = _read(EVIDENCE / "CURRENT_STATE.json").get("base_code_commit")
    except (OSError, json.JSONDecodeError, ValueError):
        existing = None
    if isinstance(existing, str):
        candidates.append((existing, "existing_evidence"))
    candidates.append((_git_head(), "head_bootstrap"))
    for commit, source in candidates:
        if _commit_exists(commit):
            return commit, source
    raise RuntimeError("no valid evaluation base commit; pass --base-code-commit")


def _require_evidence_base_commit(base_code_commit: str, inputs: list[dict]) -> None:
    """Reject a state that combines evidence from different source epochs."""

    missing = sum(
        1 for value in inputs if not isinstance(value.get("base_code_commit"), str)
    )
    declared = {
        value.get("base_code_commit")
        for value in inputs
        if isinstance(value.get("base_code_commit"), str)
    }
    if missing or declared != {base_code_commit}:
        rendered = ", ".join(sorted(str(item) for item in declared)) or "missing"
        if missing:
            rendered = f"{rendered}; missing_fields={missing}"
        raise RuntimeError(
            "evidence base_code_commit mismatch: "
            f"selected={base_code_commit}, declared={rendered}"
        )


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-code-commit",
        help="40-hex commit whose source was evaluated; required for a new epoch",
    )
    args = parser.parse_args(argv)
    identity = _read(EVIDENCE / "NATIVE_BASELINE_IDENTITY.json")
    native = _read(EVIDENCE / "NATIVE_IMMUTABILITY_REPORT.json")
    structured = _read(EVIDENCE / "STRUCTURED_OUTPUT_QUALIFICATION_RESULT.json")
    parity = _read(DATA_EVIDENCE)
    try:
        current_identity = _read(EVIDENCE / "EVALUATED_IMPLEMENTATION_IDENTITY.json")
    except (OSError, json.JSONDecodeError, ValueError):
        current_identity = None
    uuid_probe = _uuid_semantics_probe()
    base_code_commit, base_commit_source = _evaluation_base_commit(args.base_code_commit)
    evidence_epoch_error = None
    try:
        _require_evidence_base_commit(base_code_commit, [identity, native, structured, parity])
    except RuntimeError as exc:
        # A stale/mixed evidence epoch is a reportable gate failure, not a
        # reason to leave the previous state artifact looking authorized.
        evidence_epoch_error = str(exc)
    current_head = _git_head()
    current_diff_sha256 = _tracked_diff_sha256()
    identity_match = bool(
        isinstance(current_identity, dict)
        and current_identity.get("head_commit") == current_head
        and current_identity.get("tracked_diff_sha256") == current_diff_sha256
    )
    evaluated_commit_is_current = base_code_commit == current_head
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
    if not identity_match:
        state = "BLOCKED_CURRENT_HEAD_IDENTITY"
    elif not evaluated_commit_is_current:
        state = "BLOCKED_STALE_OR_MIXED_EVIDENCE"
    elif evidence_epoch_error is not None:
        state = "BLOCKED_STALE_OR_MIXED_EVIDENCE"
    elif not native_pass:
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
        "current_implementation_identity": {
            "status": "PASS" if identity_match else "FAIL",
            "artifact_present": isinstance(current_identity, dict),
            "head_commit": current_head,
            "identity_head_commit": current_identity.get("head_commit") if isinstance(current_identity, dict) else None,
            "tracked_diff_sha256": current_diff_sha256,
            "identity_tracked_diff_sha256": current_identity.get("tracked_diff_sha256") if isinstance(current_identity, dict) else None,
        },
        "evidence_epoch": {"status": "PASS" if evidence_epoch_error is None else "FAIL", "error": evidence_epoch_error},
        "evaluated_commit_is_current": evaluated_commit_is_current,
        "provider_calls": 0,
        "formal_history_executed": False,
        "engineering_canary_executed": False,
        "three_arm_experiment_created": False,
        "evidence_inputs": {"native_identity": str((EVIDENCE / "NATIVE_BASELINE_IDENTITY.json").resolve()), "native_report": str((EVIDENCE / "NATIVE_IMMUTABILITY_REPORT.json").resolve()), "structured_qualification": str((EVIDENCE / "STRUCTURED_OUTPUT_QUALIFICATION_RESULT.json").resolve()), "official_dataset_parity": str(DATA_EVIDENCE.resolve())},
        "evaluated_source_bundle": evaluated_source_bundle,
        "evaluated_source_bundle_sha256": evaluated_source_bundle_sha256,
        "generator_source_sha256": generator_source_sha256,
        "base_code_commit": base_code_commit,
        "base_code_commit_source": base_commit_source,
    }
    body["status_reason"] = "State is computed from current implementation identity, evidence epoch, native, UUID, actual-callsite, and official-dataset evidence; no prior state is reused."
    canary_authorized = state == "CODE_READY_FOR_THREE_ARM_ENGINEERING_CANARY" and identity_match and evaluated_commit_is_current and evidence_epoch_error is None
    decision = {"schema_version": "membind.preexperiment.final-decision.v3", "decision": state, "status": state, "canary_authorized": canary_authorized, "formal_three_arm_authorized": False, "provider_calls": 0, "inputs": body["evidence_inputs"], "reason": body["status_reason"], "evaluated_source_bundle": evaluated_source_bundle, "evaluated_source_bundle_sha256": evaluated_source_bundle_sha256, "generator_source_sha256": generator_source_sha256, "base_code_commit": base_code_commit, "base_code_commit_source": base_commit_source, "current_identity_match": identity_match, "evidence_epoch_status": "PASS" if evidence_epoch_error is None else "FAIL"}
    (EVIDENCE / "CURRENT_STATE.json").write_text(json.dumps(body, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (EVIDENCE / "FINAL_DECISION.json").write_text(json.dumps(decision, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"state": state, "native": native_pass, "uuid": uuid_probe["status"], "r1": r1_pass, "dataset": dataset_pass, "identity": identity_match, "evidence_epoch": evidence_epoch_error is None}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
