"""Durable controller contracts for one Reader-v2 canary."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import atomic_write_json, sha256_file
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
from paper_eval.native_reader_v2_controller import (
    ReaderV2ControllerDependencies,
    ReaderV2LiveExecutor,
    run_reader_v2_controller,
)
from paper_eval.native_reader_v2_qualification import build_reader_v2_contract
from paper_eval.s2_completion_chain import BoundedCompletionResult
from paper_eval.s2_session_policy import SessionRetrievalMetrics


RUN_ID = "native-reader-v2-canary-controller-test-001"
HISTORY_ID = "b6019101"
NAMESPACE = "nc-e1e2-1deef863d4241064"


class _Transport:
    public_config = {
        "implementation": "openai_compatible_chat_completions",
        "served_model_name": "qwen3-32b-fp8",
        "endpoint_identity_sha256": "1" * 64,
        "max_attempts": 1,
        "sdk_hidden_retries": 0,
    }
    config_sha256 = "2" * 64


def _hashes(names) -> dict[str, str]:
    return {
        name: format(index % 16, "x") * 64
        for index, name in enumerate(sorted(names), start=1)
    }


def _fixture(tmp_path: Path) -> dict[str, Path]:
    reader = OfficialConSessionReader(
        model="qwen3-32b-fp8", transport=_Transport()
    )
    contract = build_reader_v2_contract(
        reader_public_config=reader.public_config,
        reader_config_sha256=reader.config_sha256,
        reader_transport_public_config=_Transport.public_config,
        reader_transport_config_sha256=_Transport.config_sha256,
        method_reader_bindings=common_method_reader_bindings(
            reader.config_sha256
        ),
        retrieval_policy_file_sha256="3" * 64,
        judge_identity_sha256="4" * 64,
        historical_direct_result_sha256=(
            "d9fc42a6479e3071fce56b8670a583aaa9ad76ce24687f4b6de957173064733d"
        ),
        canary_history_id=HISTORY_ID,
        canary_namespace=NAMESPACE,
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
        source_sha256=_hashes(
            {
                "workplan",
                "parent_workplan",
                "reader_source",
                "reader_test",
                "qualification_source",
                "qualification_test",
                "historical_result",
            }
        ),
    )
    contract_path = tmp_path / "NATIVE_READER_V2_CONTRACT.json"
    atomic_write_json(contract_path, contract)
    prerequisites = {
        name: {
            "file_sha256": format(index % 16, "x") * 64,
            "payload_sha256": format((index + 8) % 16, "x") * 64,
            "status": status,
        }
        for index, (name, status) in enumerate(
            sorted(READER_V2_PREREQUISITE_STATUS.items()), start=1
        )
    }
    qualification = build_reader_v2_offline_qualification(
        contract=contract,
        contract_file_sha256=sha256_file(contract_path),
        evidence_sha256=_hashes(READER_V2_EVIDENCE_NAMES),
        prerequisites=prerequisites,
        history_id=HISTORY_ID,
        namespace=NAMESPACE,
        expected_session_count=49,
        git_commit="deadbeef",
        run_id="native-reader-v2-offline-controller-test",
    )
    qualification_path = tmp_path / "NATIVE_READER_V2_OFFLINE_QUALIFICATION.json"
    atomic_write_json(qualification_path, qualification)
    run_dir = tmp_path / "runs" / RUN_ID
    authorization = build_reader_v2_authorization(
        qualification=qualification,
        qualification_file_sha256=sha256_file(qualification_path),
        contract_file_sha256=sha256_file(contract_path),
        run_id=RUN_ID,
        history_id=HISTORY_ID,
        namespace=NAMESPACE,
        consumption_path=run_dir / "NATIVE_READER_V2_AUTHORIZATION_CONSUMPTION.json",
        result_path=run_dir / "NATIVE_READER_V2_RESULT.json",
        failure_path=run_dir / "NATIVE_READER_V2_FAILURE.json",
        git_commit="deadbeef",
    )
    authorization_path = tmp_path / "NATIVE_READER_V2_AUTHORIZATION.json"
    atomic_write_json(authorization_path, authorization)
    return {
        "contract": contract_path,
        "qualification": qualification_path,
        "authorization": authorization_path,
        "run_dir": run_dir,
    }


def _result(*, qa_accuracy: float) -> BoundedCompletionResult:
    label = qa_accuracy == 1.0
    return BoundedCompletionResult(
        metrics=SessionRetrievalMetrics(
            retrieved_session_count=10,
            gold_session_count=2,
            covered_gold_session_count=2,
            session_recall_any_at_10=1.0,
            session_recall_all_at_10=1.0,
            session_gold_coverage_fraction_at_10=1.0,
            evidence_recall_at_10=1.0,
            gold_ranks=(1, 2),
        ),
        qa_accuracy=qa_accuracy,
        reference_sanity_status="PASS" if label else "REVIEW_REQUIRED",
        reader_evidence={
            "status": "SUCCESS",
            "model": "qwen3-32b-fp8",
            "config_sha256": "a" * 64,
            "prompt_sha256": "b" * 64,
            "prompt_character_count": 100,
            "prompt_byte_count": 100,
            "output_sha256": "c" * 64,
            "output_character_count": 20,
            "output_byte_count": 20,
            "prompt_tokens": 50,
            "completion_tokens": 5,
            "truncation_count": 0,
        },
        judge_evidence={
            "status": "SUCCESS",
            "label": label,
            "model": "qwen3-32b-fp8",
            "prompt_sha256": "d" * 64,
            "config_sha256": "e" * 64,
            "output_sha256": "f" * 64,
            "output_character_count": 3,
            "output_byte_count": 3,
            "parse_status": "YES" if label else "NO",
            "retry_count": 0,
            "error_class": None,
        },
        counters={
            "graphiti_search_calls": 1,
            "neo4j_read_requests": 2,
            "reader_requests": 1,
            "judge_requests": 1,
            "construction_llm_requests": 0,
            "embedding_requests": 0,
            "cross_encoder_requests": 0,
            "database_mutation_attempts": 0,
            "database_mutations": 0,
            "cleanup_calls": 0,
            "retry_count": 0,
        },
        retrieved_session_ids=tuple(f"s{index}" for index in range(10)),
        gold_session_ids=("s0", "s1"),
        history_id=HISTORY_ID,
        namespace=NAMESPACE,
    )


@pytest.mark.parametrize("qa_accuracy", [0.0, 1.0])
def test_controller_compatibility_pass_does_not_use_qa_as_gate(
    tmp_path: Path, qa_accuracy: float
) -> None:
    paths = _fixture(tmp_path)
    order: list[str] = []

    def build_live() -> ReaderV2LiveExecutor:
        consumption = (
            paths["run_dir"]
            / "NATIVE_READER_V2_AUTHORIZATION_CONSUMPTION.json"
        )
        assert consumption.is_file()
        order.append("build_live")

        async def execute(checkpoint):
            checkpoint("retrieval_complete", {"status": "SUCCESS", "count": 10})
            checkpoint("reader_complete", {"status": "SUCCESS"})
            checkpoint(
                "judge_complete",
                {
                    "status": "SUCCESS",
                    "parse_status": "YES" if qa_accuracy else "NO",
                },
            )
            return _result(qa_accuracy=qa_accuracy)

        async def close() -> None:
            order.append("close")

        return ReaderV2LiveExecutor(execute=execute, close=close)

    outcome = run_reader_v2_controller(
        authorization_path=paths["authorization"],
        qualification_path=paths["qualification"],
        contract_path=paths["contract"],
        dependencies=ReaderV2ControllerDependencies(build_live=build_live),
    )

    assert outcome.status == "PASS"
    assert order == ["build_live", "close"]
    result_path = paths["run_dir"] / "NATIVE_READER_V2_RESULT.json"
    artifact = json.loads(result_path.read_text())
    assert artifact["payload"]["compatibility_status"] == "PASS"
    assert artifact["payload"]["quality_gate_used"] is False
    assert artifact["payload"]["classification"]["qa_accuracy_diagnostic"] == (
        qa_accuracy
    )
    assert artifact["payload"]["native_quality_mergeable"] is False
    assert artifact["payload"]["s3_authorized"] is False
    assert not (paths["run_dir"] / "NATIVE_READER_V2_FAILURE.json").exists()
    events = [
        json.loads(line)
        for line in (paths["run_dir"] / "events.jsonl").read_text().splitlines()
    ]
    assert [event["event_type"] for event in events] == [
        "authorization_consumed",
        "retrieval_complete",
        "reader_complete",
        "judge_complete",
        "terminal_success",
    ]


def test_controller_seals_sanitized_nonmergeable_failure(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)

    def build_live() -> ReaderV2LiveExecutor:
        async def execute(checkpoint):
            checkpoint("retrieval_complete", {"status": "SUCCESS", "count": 10})
            raise ConnectionError("private endpoint and raw prompt")

        return ReaderV2LiveExecutor(execute=execute, close=lambda: None)

    outcome = run_reader_v2_controller(
        authorization_path=paths["authorization"],
        qualification_path=paths["qualification"],
        contract_path=paths["contract"],
        dependencies=ReaderV2ControllerDependencies(build_live=build_live),
    )

    assert outcome.status == "FAILED_STOPPED"
    failure_path = paths["run_dir"] / "NATIVE_READER_V2_FAILURE.json"
    failure = json.loads(failure_path.read_text())
    assert failure["payload"]["error_class"] == "ConnectionError"
    assert failure["payload"]["completed_stages"] == ["retrieval_complete"]
    assert failure["payload"]["result_mergeable"] is False
    assert "private endpoint" not in failure_path.read_text()
    assert not (paths["run_dir"] / "NATIVE_READER_V2_RESULT.json").exists()


def test_controller_cannot_reuse_consumed_authority(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    dependencies = ReaderV2ControllerDependencies(
        build_live=lambda: ReaderV2LiveExecutor(
            execute=lambda _checkpoint: _result(qa_accuracy=0.0),
            close=lambda: None,
        )
    )
    first = run_reader_v2_controller(
        authorization_path=paths["authorization"],
        qualification_path=paths["qualification"],
        contract_path=paths["contract"],
        dependencies=dependencies,
    )
    assert first.status == "PASS"

    with pytest.raises(ValueError, match="already consumed|already exists"):
        run_reader_v2_controller(
            authorization_path=paths["authorization"],
            qualification_path=paths["qualification"],
            contract_path=paths["contract"],
            dependencies=dependencies,
        )


def test_controller_rejects_contract_drift_before_consumption(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    contract = json.loads(paths["contract"].read_text())
    contract["reader"]["max_tokens"] = 500
    atomic_write_json(paths["contract"], contract)

    with pytest.raises(ValueError, match="contract|hash"):
        run_reader_v2_controller(
            authorization_path=paths["authorization"],
            qualification_path=paths["qualification"],
            contract_path=paths["contract"],
            dependencies=ReaderV2ControllerDependencies(
                build_live=lambda: pytest.fail("must not build live")
            ),
        )
    assert not paths["run_dir"].exists()


def test_controller_result_contains_no_raw_content(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    run_reader_v2_controller(
        authorization_path=paths["authorization"],
        qualification_path=paths["qualification"],
        contract_path=paths["contract"],
        dependencies=ReaderV2ControllerDependencies(
            build_live=lambda: ReaderV2LiveExecutor(
                execute=lambda _checkpoint: _result(qa_accuracy=0.0),
                close=lambda: None,
            )
        ),
    )
    encoded = (
        paths["run_dir"] / "NATIVE_READER_V2_RESULT.json"
    ).read_text().lower()
    for forbidden in (
        "private question",
        "private answer",
        "raw_prompt",
        "raw_output",
        "has_answer",
        "api_key",
    ):
        assert forbidden not in encoded
