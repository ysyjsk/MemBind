from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file
from paper_eval.s2_completion_authority import (
    build_completion_authorization,
    build_completion_offline_qualification,
    build_completion_policy_freeze,
)
from paper_eval.s2_completion_chain import BoundedCompletionResult
from paper_eval.s2_completion_controller import (
    CompletionControllerDependencies,
    CompletionLiveExecutor,
    run_s2_completion_controller,
)
from paper_eval.s2_session_policy import SessionRetrievalMetrics


RUN_ID = "s2-completion-controller-test-001"
HISTORY_ID = "07741c45"
NAMESPACE = "pev3-s1-20260814-001"


def _hashes(names: tuple[str, ...]) -> dict[str, str]:
    return {
        name: format(index % 16, "x") * 64
        for index, name in enumerate(names, start=1)
    }


def _fixture(tmp_path: Path) -> dict[str, Path]:
    identity_body = {
        "schema_version": "synthetic-test-only",
        "component": "session-chain",
    }
    identity = {
        **identity_body,
        "identity_sha256": payload_sha256(identity_body),
    }
    identity_path = tmp_path / "identity.json"
    atomic_write_json(identity_path, identity)

    contract = {"schema_version": "synthetic-test-only", "contract_sha256": "b" * 64}
    contract_path = tmp_path / "contract.json"
    atomic_write_json(contract_path, contract)

    policy_names = (
        "parent_protocol",
        "retrieval_amendment",
        "execution_workplan",
        "research_basis",
        "completion_contract_source",
        "completion_contract_test",
        "session_policy_source",
        "session_policy_test",
        "session_reader_source",
        "session_reader_test",
        "completion_chain_source",
        "completion_chain_test",
        "completion_identity_source",
        "completion_identity_test",
        "formal_retrieval_source",
        "formal_retrieval_test",
        "completion_authority_source",
        "completion_authority_test",
        "completion_controller_source",
        "completion_controller_test",
        "completion_production_source",
        "completion_production_test",
        "finalize_script",
        "run_script",
        "focused_green",
        "full_offline_green",
    )
    policy = build_completion_policy_freeze(
        contract_file_sha256=sha256_file(contract_path),
        contract_sha256=contract["contract_sha256"],
        adapter_identity_file_sha256=sha256_file(identity_path),
        adapter_identity_sha256=identity["identity_sha256"],
        evidence_sha256=_hashes(policy_names),
        git_commit="deadbeef",
        run_id="policy-test",
    )
    policy_path = tmp_path / "policy.json"
    atomic_write_json(policy_path, policy)

    status = {
        "s1_smoke": "PASS",
        "u0_qualification": "PASS",
        "dataset_parity": "PASS",
        "evaluator_parity": "PASS",
        "development_roles": "PASS",
        "current_state": "VERIFIED",
        "judge_qualification": "PASS",
        "s2r0_chain": "VERIFIED",
    }
    prerequisites = {
        name: {
            "file_sha256": format(index % 16, "x") * 64,
            "payload_sha256": format((index + 8) % 16, "x") * 64,
            "status": value,
        }
        for index, (name, value) in enumerate(status.items(), start=1)
    }
    qualification = build_completion_offline_qualification(
        policy_freeze=policy,
        policy_freeze_file_sha256=sha256_file(policy_path),
        prerequisites=prerequisites,
        history_id=HISTORY_ID,
        namespace=NAMESPACE,
        expected_session_count=49,
        expected_gold_count=2,
        git_commit="deadbeef",
        run_id="qualification-test",
    )
    qualification_path = tmp_path / "qualification.json"
    atomic_write_json(qualification_path, qualification)

    run_dir = tmp_path / RUN_ID
    authorization = build_completion_authorization(
        qualification=qualification,
        qualification_file_sha256=sha256_file(qualification_path),
        policy_freeze_file_sha256=sha256_file(policy_path),
        adapter_identity_file_sha256=sha256_file(identity_path),
        adapter_identity_sha256=identity["identity_sha256"],
        run_id=RUN_ID,
        history_id=HISTORY_ID,
        namespace=NAMESPACE,
        consumption_path=run_dir / "S2_COMPLETION_AUTHORIZATION_CONSUMPTION.json",
        result_path=run_dir / "S2_COMPLETION_RESULT.json",
        failure_path=run_dir / "S2_COMPLETION_FAILURE.json",
        git_commit="deadbeef",
    )
    authorization_path = tmp_path / "authorization.json"
    atomic_write_json(authorization_path, authorization)
    return {
        "identity": identity_path,
        "policy": policy_path,
        "qualification": qualification_path,
        "authorization": authorization_path,
        "run_dir": run_dir,
    }


def _result() -> BoundedCompletionResult:
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
        qa_accuracy=1.0,
        reference_sanity_status="PASS",
        reader_evidence={
            "status": "SUCCESS",
            "prompt_sha256": "a" * 64,
            "output_sha256": "b" * 64,
        },
        judge_evidence={
            "status": "SUCCESS",
            "label": True,
            "parse_status": "YES",
            "prompt_sha256": "c" * 64,
            "output_sha256": "d" * 64,
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


def test_controller_consumes_before_live_and_persists_each_checkpoint(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    order: list[str] = []

    def build_live() -> CompletionLiveExecutor:
        assert (paths["run_dir"] / "S2_COMPLETION_AUTHORIZATION_CONSUMPTION.json").is_file()
        order.append("build_live")

        async def execute(checkpoint):
            checkpoint("retrieval_complete", {"status": "SUCCESS", "count": 10})
            checkpoint("reader_complete", {"status": "SUCCESS"})
            checkpoint("judge_complete", {"status": "SUCCESS", "parse_status": "YES"})
            return _result()

        async def close() -> None:
            order.append("close")

        return CompletionLiveExecutor(execute=execute, close=close)

    outcome = run_s2_completion_controller(
        authorization_path=paths["authorization"],
        qualification_path=paths["qualification"],
        policy_freeze_path=paths["policy"],
        adapter_identity_path=paths["identity"],
        dependencies=CompletionControllerDependencies(build_live=build_live),
    )

    assert outcome.status == "PASS"
    assert order == ["build_live", "close"]
    result_path = paths["run_dir"] / "S2_COMPLETION_RESULT.json"
    assert result_path.is_file()
    assert not (paths["run_dir"] / "S2_COMPLETION_FAILURE.json").exists()
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
    checkpoint = json.loads((paths["run_dir"] / "checkpoint.json").read_text())
    assert checkpoint["status"] == "completed"
    assert checkpoint["completed_stages"] == [
        "retrieval_complete",
        "reader_complete",
        "judge_complete",
    ]


def test_controller_seals_nonmergeable_failure_after_partial_checkpoint(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)

    def build_live() -> CompletionLiveExecutor:
        async def execute(checkpoint):
            checkpoint("retrieval_complete", {"status": "SUCCESS", "count": 10})
            raise ConnectionError("private endpoint and raw prompt")

        async def close() -> None:
            return None

        return CompletionLiveExecutor(execute=execute, close=close)

    outcome = run_s2_completion_controller(
        authorization_path=paths["authorization"],
        qualification_path=paths["qualification"],
        policy_freeze_path=paths["policy"],
        adapter_identity_path=paths["identity"],
        dependencies=CompletionControllerDependencies(build_live=build_live),
    )

    assert outcome.status == "FAILED_STOPPED"
    failure_path = paths["run_dir"] / "S2_COMPLETION_FAILURE.json"
    failure = json.loads(failure_path.read_text())
    assert failure["payload"]["error_class"] == "ConnectionError"
    assert failure["payload"]["result_mergeable"] is False
    assert failure["payload"]["completed_stages"] == ["retrieval_complete"]
    assert "private endpoint" not in failure_path.read_text()
    assert not (paths["run_dir"] / "S2_COMPLETION_RESULT.json").exists()


def test_controller_cannot_reuse_consumed_authorization(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)

    async def execute(_checkpoint):
        return _result()

    dependencies = CompletionControllerDependencies(
        build_live=lambda: CompletionLiveExecutor(execute=execute, close=lambda: None)
    )
    first = run_s2_completion_controller(
        authorization_path=paths["authorization"],
        qualification_path=paths["qualification"],
        policy_freeze_path=paths["policy"],
        adapter_identity_path=paths["identity"],
        dependencies=dependencies,
    )
    assert first.status == "PASS"

    with pytest.raises(ValueError, match="already consumed|already exists"):
        run_s2_completion_controller(
            authorization_path=paths["authorization"],
            qualification_path=paths["qualification"],
            policy_freeze_path=paths["policy"],
            adapter_identity_path=paths["identity"],
            dependencies=dependencies,
        )


def test_controller_rejects_bound_file_drift_before_consumption(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    identity = json.loads(paths["identity"].read_text())
    identity["component"] = "drifted"
    atomic_write_json(paths["identity"], identity)

    with pytest.raises(ValueError, match="identity.*drift|hash"):
        run_s2_completion_controller(
            authorization_path=paths["authorization"],
            qualification_path=paths["qualification"],
            policy_freeze_path=paths["policy"],
            adapter_identity_path=paths["identity"],
            dependencies=CompletionControllerDependencies(
                build_live=lambda: pytest.fail("must not build live")
            ),
        )
    assert not paths["run_dir"].exists()
