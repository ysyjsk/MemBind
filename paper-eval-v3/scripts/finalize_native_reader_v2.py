#!/usr/bin/env python3
"""Seal the Reader-v2 contract, offline qualification, and one-shot authority."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.native_reader_v2 import (
    OfficialConSessionReader,
    common_method_reader_bindings,
)
from paper_eval.native_reader_v2_authority import (
    READER_V2_EVIDENCE_NAMES,
    READER_V2_PREREQUISITE_STATUS,
    build_reader_v2_authorization,
    build_reader_v2_offline_qualification,
)
from paper_eval.native_reader_v2_qualification import (
    CANARY_HISTORY_ID,
    CANARY_NAMESPACE,
    HISTORICAL_DIRECT_RESULT_SHA256,
    build_reader_v2_contract,
)
from paper_eval.s2_adapters import OpenAIChatCompletionsTransport
from paper_eval.s2_completion_production import (
    EXPECTED_BASE_URL,
    EXPECTED_MODEL,
    load_completion_env_file,
)


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "paper-eval-v3"
LEGACY = ROOT / "membind-validation"
NATIVE = PROJECT / "artifacts/paper_eval/native"
RUN_ID = "native-reader-v2-canary-20260814-001"

CONTRACT_PATH = NATIVE / "NATIVE_READER_V2_CONTRACT.json"
QUALIFICATION_PATH = NATIVE / "NATIVE_READER_V2_OFFLINE_QUALIFICATION.json"
AUTHORIZATION_PATH = NATIVE / "NATIVE_READER_V2_AUTHORIZATION.json"
RUN_DIR = NATIVE / "runs" / RUN_ID

HISTORICAL_RESULT = (
    NATIVE
    / "runs/s2-completion-20260814-001/S2_COMPLETION_RESULT.json"
)
C2_MANIFEST = (
    LEGACY
    / "artifacts/native_characterization/runs/c2-17cdaabd562e9673/manifest.json"
)
C2_CANARY_BLOCK = (
    LEGACY
    / "artifacts/native_characterization/runs/c2-17cdaabd562e9673/blocks/001_b6019101/block_summary.json"
)
JUDGE_QUALIFICATION = (
    LEGACY
    / "artifacts/judge_qualification/runs/jq-b00a9689796c1e67/qualification_summary.json"
)
FOCUSED_GREEN = PROJECT / "logs/TDD_FOCUSED_GREEN_NATIVE_READER_V2_ROOT_RECHECK_20260814.xml"
PRODUCTION_GREEN = PROJECT / "logs/TDD_GREEN_NATIVE_READER_V2_PRODUCTION_20260814.xml"
FULL_GREEN = PROJECT / "logs/TDD_FULL_OFFLINE_GREEN_NATIVE_READER_V2_PRESEAL_20260814.xml"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid JSON object: {path.name}")
    return value


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except FileExistsError:
        raise RuntimeError(f"refusing to overwrite sealed artifact: {path.name}") from None
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def _junit_pass(path: Path, *, minimum_tests: int) -> None:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    tests = sum(int(suite.attrib.get("tests", "0")) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", "0")) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", "0")) for suite in suites)
    if tests < minimum_tests or failures or errors:
        raise RuntimeError(f"JUnit evidence is not green: {path.name}")


def _prerequisite(
    path: Path,
    *,
    status: str,
    payload_sha: str | None = None,
) -> dict[str, str]:
    return {
        "file_sha256": sha256_file(path),
        "payload_sha256": payload_sha or sha256_file(path),
        "status": status,
    }


async def _close_transport(transport: OpenAIChatCompletionsTransport) -> None:
    await transport.aclose()


def main() -> None:
    targets = (CONTRACT_PATH, QUALIFICATION_PATH, AUTHORIZATION_PATH)
    existing = [path.name for path in targets if path.exists()]
    if existing:
        raise RuntimeError("refusing to overwrite sealed artifacts: " + ",".join(existing))

    _junit_pass(FOCUSED_GREEN, minimum_tests=47)
    _junit_pass(PRODUCTION_GREEN, minimum_tests=5)
    _junit_pass(FULL_GREEN, minimum_tests=429)
    historical = _load(HISTORICAL_RESULT)
    if (
        sha256_file(HISTORICAL_RESULT) != HISTORICAL_DIRECT_RESULT_SHA256
        or historical.get("status") != "finalized"
        or historical.get("payload", {}).get("status") != "REVIEW_REQUIRED"
    ):
        raise RuntimeError("historical direct result drift")
    roles = _load(PROJECT / "artifacts/paper_eval/DEVELOPMENT_EXPOSED_IDS.json")
    role_payload = roles.get("payload", {})
    if CANARY_HISTORY_ID not in role_payload.get("roles", {}).get(
        "DEVELOPMENT_EXPOSED", []
    ):
        raise RuntimeError("canary is not DEVELOPMENT_EXPOSED")
    c2_manifest = _load(C2_MANIFEST)
    c2_block = _load(C2_CANARY_BLOCK)
    if (
        c2_manifest.get("status") != "completed"
        or c2_block.get("history_id") != CANARY_HISTORY_ID
        or c2_block.get("graph_namespace") != CANARY_NAMESPACE
        or c2_block.get("episode_count") != 49
    ):
        raise RuntimeError("C2 canary manifest drift")
    dataset_parity = _load(NATIVE / "DATASET_PARITY.json")
    judge_summary = _load(JUDGE_QUALIFICATION)
    if (
        dataset_parity.get("payload", {}).get("verdict") != "PASS"
        or judge_summary.get("qualification_status") != "PASS"
        or judge_summary.get("attempt_status") != "complete"
        or judge_summary.get("invalid_output_count") != 0
        or judge_summary.get("service_error_count") != 0
        or judge_summary.get("retry_count_total") != 0
    ):
        raise RuntimeError("reused evaluator prerequisite drift")

    env = load_completion_env_file(LEGACY / ".env")
    transport = OpenAIChatCompletionsTransport(
        model=EXPECTED_MODEL,
        base_url=EXPECTED_BASE_URL,
        api_key=env["CONSTRUCTION_LLM_API_KEY"],
        timeout_seconds=180.0,
    )
    reader = OfficialConSessionReader(model=EXPECTED_MODEL, transport=transport)
    try:
        source_hashes = {
            "workplan": sha256_file(PROJECT / "NATIVE_READER_V2_QUALIFICATION_WORKPLAN_v1.0.md"),
            "parent_workplan": sha256_file(
                ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md"
            ),
            "reader_source": sha256_file(PROJECT / "src/paper_eval/native_reader_v2.py"),
            "reader_test": sha256_file(PROJECT / "tests/test_native_reader_v2.py"),
            "qualification_source": sha256_file(
                PROJECT / "src/paper_eval/native_reader_v2_qualification.py"
            ),
            "qualification_test": sha256_file(
                PROJECT / "tests/test_native_reader_v2_qualification.py"
            ),
            "historical_result": sha256_file(HISTORICAL_RESULT),
        }
        contract = build_reader_v2_contract(
            reader_public_config=reader.public_config,
            reader_config_sha256=reader.config_sha256,
            reader_transport_public_config=transport.public_config,
            reader_transport_config_sha256=transport.config_sha256,
            method_reader_bindings=common_method_reader_bindings(
                reader.config_sha256
            ),
            retrieval_policy_file_sha256=sha256_file(
                NATIVE / "S2_COMPLETION_POLICY_FREEZE.json"
            ),
            judge_identity_sha256=sha256_file(
                NATIVE / "S2_COMPLETION_ADAPTER_IDENTITY.json"
            ),
            historical_direct_result_sha256=sha256_file(HISTORICAL_RESULT),
            canary_history_id=CANARY_HISTORY_ID,
            canary_namespace=CANARY_NAMESPACE,
            canary_selection={
                "data_role": "DEVELOPMENT_EXPOSED",
                "selection_rule": "first_remaining_frozen_calibration_id",
                "excluded_observed_history_id": "07741c45",
                "selected_before_reader_v2_outcome": True,
                "canary_construction_revision_matches_current_u0": False,
                "canary_use": "ADAPTER_COMPATIBILITY_ONLY",
            },
            disclosure={
                "prior_direct_failure_observed": True,
                "reader_v2_selection_not_blinded": True,
                "change_motivated_by_observed_failure": True,
                "recipe_source": "upstream_recommended",
                "direct_path_was_officially_supported": True,
                "retrieval_or_top_k_candidate_search": False,
            },
            source_sha256=source_hashes,
        )
    finally:
        asyncio.run(_close_transport(transport))

    contract_file_hash = hashlib.sha256(
        (json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
    ).hexdigest()
    focused_hash = payload_sha256(
        {
            "core": sha256_file(FOCUSED_GREEN),
            "production": sha256_file(PRODUCTION_GREEN),
        }
    )
    evidence_paths = {
        "workplan": PROJECT / "NATIVE_READER_V2_QUALIFICATION_WORKPLAN_v1.0.md",
        "parent_workplan": ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md",
        "reader_source": PROJECT / "src/paper_eval/native_reader_v2.py",
        "reader_test": PROJECT / "tests/test_native_reader_v2.py",
        "qualification_source": PROJECT / "src/paper_eval/native_reader_v2_qualification.py",
        "qualification_test": PROJECT / "tests/test_native_reader_v2_qualification.py",
        "authority_source": PROJECT / "src/paper_eval/native_reader_v2_authority.py",
        "authority_test": PROJECT / "tests/test_native_reader_v2_authority.py",
        "controller_source": PROJECT / "src/paper_eval/native_reader_v2_controller.py",
        "controller_test": PROJECT / "tests/test_native_reader_v2_controller.py",
        "production_source": PROJECT / "src/paper_eval/native_reader_v2_production.py",
        "production_test": PROJECT / "tests/test_native_reader_v2_production.py",
        "historical_direct_result": HISTORICAL_RESULT,
        "c2_manifest": C2_MANIFEST,
        "dataset_parity": NATIVE / "DATASET_PARITY.json",
        "development_roles": PROJECT / "artifacts/paper_eval/DEVELOPMENT_EXPOSED_IDS.json",
        "judge_qualification": JUDGE_QUALIFICATION,
    }
    evidence_sha = {
        name: sha256_file(path) for name, path in evidence_paths.items()
    }
    evidence_sha["focused_green"] = focused_hash
    evidence_sha["full_offline_green"] = sha256_file(FULL_GREEN)
    if set(evidence_sha) != set(READER_V2_EVIDENCE_NAMES):
        raise RuntimeError("Reader-v2 evidence inventory drift")

    prerequisites = {
        "historical_direct_result": _prerequisite(
            HISTORICAL_RESULT,
            status="VERIFIED_REVIEW_REQUIRED",
            payload_sha=str(historical["payload_sha256"]),
        ),
        "dataset_parity": _prerequisite(
            NATIVE / "DATASET_PARITY.json",
            status="PASS",
            payload_sha=str(dataset_parity["payload_sha256"]),
        ),
        "development_roles": _prerequisite(
            PROJECT / "artifacts/paper_eval/DEVELOPMENT_EXPOSED_IDS.json",
            status="PASS",
            payload_sha=str(roles["payload_sha256"]),
        ),
        "judge_qualification": _prerequisite(
            JUDGE_QUALIFICATION,
            status="PASS",
            payload_sha=str(judge_summary["payload_sha256"]),
        ),
        "c2_canary_manifest": _prerequisite(
            C2_MANIFEST,
            status="VERIFIED_DRIFT_DISCLOSED",
            payload_sha=str(c2_manifest["payload_sha256"]),
        ),
        "reader_contract_tests": {
            "file_sha256": focused_hash,
            "payload_sha256": focused_hash,
            "status": "PASS",
        },
        "full_offline_tests": _prerequisite(
            FULL_GREEN,
            status="PASS",
        ),
    }
    if set(prerequisites) != set(READER_V2_PREREQUISITE_STATUS):
        raise RuntimeError("Reader-v2 prerequisite inventory drift")
    qualification = build_reader_v2_offline_qualification(
        contract=contract,
        contract_file_sha256=contract_file_hash,
        evidence_sha256=evidence_sha,
        prerequisites=prerequisites,
        history_id=CANARY_HISTORY_ID,
        namespace=CANARY_NAMESPACE,
        expected_session_count=49,
        git_commit=_git_commit(),
        run_id="native-reader-v2-offline-20260814-001",
    )
    qualification_file_hash = hashlib.sha256(
        (
            json.dumps(qualification, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
    ).hexdigest()
    authorization = build_reader_v2_authorization(
        qualification=qualification,
        qualification_file_sha256=qualification_file_hash,
        contract_file_sha256=contract_file_hash,
        run_id=RUN_ID,
        history_id=CANARY_HISTORY_ID,
        namespace=CANARY_NAMESPACE,
        consumption_path=RUN_DIR / "NATIVE_READER_V2_AUTHORIZATION_CONSUMPTION.json",
        result_path=RUN_DIR / "NATIVE_READER_V2_RESULT.json",
        failure_path=RUN_DIR / "NATIVE_READER_V2_FAILURE.json",
        git_commit=_git_commit(),
    )

    _write_exclusive(CONTRACT_PATH, contract)
    _write_exclusive(QUALIFICATION_PATH, qualification)
    _write_exclusive(AUTHORIZATION_PATH, authorization)
    print(
        json.dumps(
            {
                "status": "PASS",
                "run_id": RUN_ID,
                "history_id": CANARY_HISTORY_ID,
                "namespace": CANARY_NAMESPACE,
                "contract_sha256": contract["contract_sha256"],
                "live_io_performed": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
