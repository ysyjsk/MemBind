#!/usr/bin/env python3
"""Seal the bounded S2 completion contract, policy, qualification, and authority."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s2_adapters import (
    OpenAIChatCompletionsTransport,
    build_qualified_qwen_judge,
)
from paper_eval.s2_completion_authority import (
    build_completion_authorization,
    build_completion_offline_qualification,
    build_completion_policy_freeze,
)
from paper_eval.s2_completion_contract import build_s2_completion_contract
from paper_eval.s2_completion_identity import build_s2_completion_adapter_identity
from paper_eval.s2_completion_production import (
    EXPECTED_BASE_URL,
    EXPECTED_MODEL,
    load_completion_env_file,
)
from paper_eval.s2_r0_result_verifier import (
    S2R0AttemptPaths,
    verify_s2r0_attempt,
)
from paper_eval.s2_retrieval_probe import (
    build_episode_bm25_search_config,
    search_config_identity,
)
from paper_eval.s2_session_reader import OfficialSessionReader


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "paper-eval-v3"
LEGACY = ROOT / "membind-validation"
NATIVE = PROJECT / "artifacts/paper_eval/native"
RUN_ID = "s2-completion-20260814-001"
HISTORY_ID = "07741c45"
NAMESPACE = "pev3-s1-20260814-001"

CONTRACT_PATH = NATIVE / "S2_COMPLETION_CONTRACT.json"
IDENTITY_PATH = NATIVE / "S2_COMPLETION_ADAPTER_IDENTITY.json"
POLICY_PATH = NATIVE / "S2_COMPLETION_POLICY_FREEZE.json"
QUALIFICATION_PATH = NATIVE / "S2_COMPLETION_OFFLINE_QUALIFICATION.json"
AUTHORIZATION_PATH = NATIVE / "S2_COMPLETION_AUTHORIZATION.json"
RUN_DIR = NATIVE / "runs" / RUN_ID

JUDGE_QUALIFICATION = (
    LEGACY
    / "artifacts/judge_qualification/runs/jq-b00a9689796c1e67/qualification_summary.json"
)


def _serialized(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _predicted_file_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_serialized(value)).hexdigest()


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except FileExistsError:
        raise RuntimeError(f"refusing to overwrite sealed artifact: {path.name}") from None
    try:
        os.write(descriptor, _serialized(value))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid JSON object: {path.name}")
    return value


def _envelope(path: Path, *, verdict: str | None = None) -> dict[str, Any]:
    value = _load_json(path)
    payload = value.get("payload")
    if (
        value.get("status") != "finalized"
        or not isinstance(payload, dict)
        or value.get("payload_sha256") != payload_sha256(payload)
    ):
        raise RuntimeError(f"unsealed prerequisite: {path.name}")
    if verdict is not None and payload.get("verdict") != verdict:
        raise RuntimeError(f"prerequisite is not {verdict}: {path.name}")
    return value


def _aggregate_source_hash(**paths: Path) -> str:
    return payload_sha256({name: sha256_file(path) for name, path in sorted(paths.items())})


def _git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def _role_binding() -> tuple[dict[str, Any], dict[str, Any]]:
    path = PROJECT / "artifacts/paper_eval/DEVELOPMENT_EXPOSED_IDS.json"
    envelope = _envelope(path)
    registry = envelope["payload"].get("roles")
    if not isinstance(registry, dict) or set(registry) != {
        "DEVELOPMENT_EXPOSED",
        "PILOT",
        "FINAL_PAPER_TEST",
    }:
        raise RuntimeError("development role registry is invalid")
    role_sets = {name: set(values) for name, values in registry.items()}
    if HISTORY_ID not in role_sets["DEVELOPMENT_EXPOSED"] or any(
        role_sets[left] & role_sets[right]
        for left, right in (
            ("DEVELOPMENT_EXPOSED", "PILOT"),
            ("DEVELOPMENT_EXPOSED", "FINAL_PAPER_TEST"),
            ("PILOT", "FINAL_PAPER_TEST"),
        )
    ):
        raise RuntimeError("development role registry drift")
    return envelope, {
        "role_artifact_sha256": sha256_file(path),
        "role_payload_sha256": envelope["payload_sha256"],
        "evaluation_role": "DEVELOPMENT_EXPOSED",
        "selected_history_ids": [HISTORY_ID],
        "registry": registry,
    }


def _judge_summary() -> dict[str, Any]:
    value = _load_json(JUDGE_QUALIFICATION)
    body = dict(value)
    stored = body.pop("payload_sha256", None)
    if (
        stored != payload_sha256(body)
        or value.get("qualification_status") != "PASS"
        or value.get("attempt_status") != "complete"
        or value.get("invalid_output_count") != 0
        or value.get("service_error_count") != 0
        or value.get("retry_count_total") != 0
    ):
        raise RuntimeError("Judge qualification is not reusable")
    return value


async def _close(*components: Any) -> None:
    for component in components:
        method = getattr(component, "aclose", None)
        if callable(method):
            result = method()
            if result is not None and hasattr(result, "__await__"):
                await result


def main() -> None:
    targets = (
        CONTRACT_PATH,
        IDENTITY_PATH,
        POLICY_PATH,
        QUALIFICATION_PATH,
        AUTHORIZATION_PATH,
    )
    existing = [path.name for path in targets if path.exists()]
    if existing:
        raise RuntimeError("refusing to overwrite sealed artifacts: " + ",".join(existing))

    env = load_completion_env_file(LEGACY / ".env")
    transport = OpenAIChatCompletionsTransport(
        model=EXPECTED_MODEL,
        base_url=EXPECTED_BASE_URL,
        api_key=env["CONSTRUCTION_LLM_API_KEY"],
        timeout_seconds=180.0,
    )
    reader = OfficialSessionReader(model=EXPECTED_MODEL, transport=transport)
    judge = build_qualified_qwen_judge(
        base_url=EXPECTED_BASE_URL,
        api_key=env["CONSTRUCTION_LLM_API_KEY"],
    )
    try:
        state = _envelope(PROJECT / "artifacts/paper_eval/S0_CURRENT_STATE.json")
        runtime = state["payload"].get("runtime_identities")
        if not isinstance(runtime, dict) or not isinstance(runtime.get("construction"), dict):
            raise RuntimeError("current runtime identity is incomplete")
        role_envelope, role_binding = _role_binding()
        judge_summary = _judge_summary()
        search_identity = search_config_identity(build_episode_bm25_search_config())

        retrieval_source = _aggregate_source_hash(
            formal=PROJECT / "src/paper_eval/s2_formal_retrieval.py",
            guard=PROJECT / "src/paper_eval/s2_retrieval_probe.py",
            mapping=PROJECT / "src/paper_eval/s2_session_policy.py",
        )
        judge_source = _aggregate_source_hash(
            wrapper=PROJECT / "src/paper_eval/s2_adapters.py",
            adapter=LEGACY / "src/evaluation/benchmarks/longmemeval.py",
            rubric=LEGACY / "src/evaluation/vendor/longmemeval_evaluate_qa.py",
        )
        reader_source = sha256_file(PROJECT / "src/paper_eval/s2_session_reader.py")
        model_identity_sha = payload_sha256(runtime["construction"])
        backend_public = judge.public_config["backend_public_config"]

        contract = build_s2_completion_contract(
            retrieval_policy={
                "policy_name": "graphiti-0.29.3-episode-bm25-session-v1",
                "policy_version": "v1",
                "retrieval_surface": "graphiti_episode_bm25",
                "retrieval_method": "Graphiti.search_",
                "search_recipe": "EPISODE_BM25_RRF",
                "native_result_type": "EpisodicNode",
                "evaluation_result_unit": "LongMemEvalSession",
                "top_k": 10,
                "candidate_limit": 20,
                "top_k_unit": "unique_session",
                "gold_unit": "LongMemEvalSession",
                "metric_name": "per_question_session_recall_all_at_10",
                "aggregate_metric": "mean_per_question_session_recall_all_at_10",
                "reader_input_representation": "longmemeval_flat_session_item",
                "official_longmemeval_session_metric": True,
                "episode_to_session_mapping": "frozen_one_to_one_fail_closed",
                "edge_search_enabled": False,
                "node_search_enabled": False,
                "community_search_enabled": False,
                "query_embedding_used": False,
                "cross_encoder_used": False,
                "search_filters": "empty",
                "group_scope": "exactly_one_history_namespace",
                "question_date_used_for_retrieval": False,
                "retrieval_temporal_filter": "none",
                "custom_fusion_sort_or_dedup": False,
                "implementation_source_sha256": retrieval_source,
                "configuration_sha256": payload_sha256(search_identity),
            },
            selection_basis={
                "kind": "architecture_and_benchmark_semantics_not_blinded",
                "frozen_before_numeric_execution": True,
                "r0_outcome_previously_observed": True,
                "selection_not_blinded": True,
                "r0_numeric_score_used_for_policy_choice": False,
                "candidate_score_search_performed": False,
                "reasons": [
                    "BENCHMARK_RESULT_UNIT_ALIGNMENT",
                    "UPSTREAM_NATIVE_API",
                    "NO_CUSTOM_CROSS_SURFACE_FUSION",
                    "SAME_POLICY_FOR_ALL_METHODS",
                ],
                "evidence_sha256": sha256_file(
                    PROJECT / "S2_COMPLETION_POLICY_RESEARCH_BASIS_20260814.md"
                ),
            },
            reader_identity={
                "implementation": reader.public_config["implementation"],
                "input_representation": reader.public_config["input_representation"],
                "official_flat_session_item_semantics": True,
                "upstream_repository": reader.public_config["upstream_repository"],
                "upstream_commit": reader.public_config["upstream_commit"],
                "upstream_source_path": reader.public_config["upstream_source_path"],
                "upstream_file_sha256": reader.public_config["upstream_file_sha256"],
                "prompt_template_sha256": reader.public_config["prompt_template_sha256"],
                "implementation_source_sha256": reader_source,
                "model_identity_sha256": model_identity_sha,
                "transport_identity_sha256": transport.config_sha256,
                "retriever_type": "flat-session",
                "topk_context": 10,
                "history_format": "json",
                "useronly": False,
                "cot": False,
                "con": False,
                "merge_key_expansion_into_value": "none",
                "session_value_source": "frozen_dataset_haystack_sessions",
                "has_answer_label_removed": True,
                "episode_content_hash_verified": True,
                "presentation_order": "chronological_after_top_k_rank_stable_ties",
                "truncation_policy": "FAIL_CLOSED_IF_CONTEXT_EXCEEDED",
                "qualification_truncation_count": 0,
                "messages": ["user"],
                "system_prompt": None,
                "temperature": 0,
                "max_tokens": 500,
                "thinking_control": "client_request",
                "effective_enable_thinking": False,
                "thinking_parameter_sent": True,
                "max_attempts": 1,
                "sdk_hidden_retries": 0,
                "retry_delays_seconds": [],
            },
            judge_identity={
                "implementation": judge.public_config["implementation"],
                "rubric_sha256": sha256_file(
                    LEGACY / "src/evaluation/vendor/longmemeval_evaluate_qa.py"
                ),
                "parser_sha256": sha256_file(
                    LEGACY / "src/evaluation/benchmarks/longmemeval.py"
                ),
                "qualification_artifact_sha256": sha256_file(JUDGE_QUALIFICATION),
                "qualification_status": judge_summary["qualification_status"],
                "implementation_source_sha256": judge_source,
                "model_identity_sha256": model_identity_sha,
                "transport_identity_sha256": payload_sha256(backend_public),
                "question_type": "knowledge-update",
                "abstention": False,
                "rubric": "official_get_anscheck_prompt",
                "headline_parser": "substring_yes_case_insensitive",
                "audit_parser": "strict_yes_no_invalid",
                "messages": ["user"],
                "system_prompt": None,
                "temperature": 0,
                "max_tokens": 10,
                "n": 1,
                "thinking_control": "client_request",
                "effective_enable_thinking": False,
                "thinking_parameter_sent": True,
                "max_attempts": 1,
                "sdk_hidden_retries": 0,
                "retry_delays_seconds": [0.0],
                "invalid_output_policy": "SEAL_FAILURE_AND_STOP",
                "protocol_alignment": "PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED",
                "judge_backend_difference_disclosed": True,
            },
            role_binding=role_binding,
            metric_surfaces={
                "retrieval": {
                    "name": "evidence_recall_at_10",
                    "definition": "per_question_session_recall_all_at_10",
                    "aggregation": "mean",
                    "unit": "question",
                    "top_k": 10,
                    "value_source": "ranked_retrieval",
                    "substitutable_by_other_metric": False,
                },
                "qa": {
                    "name": "qa_accuracy",
                    "unit": "question",
                    "value_source": "qualified_judge",
                    "substitutable_by_other_metric": False,
                },
                "graph_correctness": {
                    "name": "graph_sensitive_construction_correctness",
                    "unit": "history",
                    "value_source": "separate_graph_oracle",
                    "substitutable_by_other_metric": False,
                },
            },
            failure_policy={
                "max_live_attempts": 1,
                "automatic_retry": False,
                "on_transport_failure": "SEAL_FAILURE_AND_STOP",
                "on_invalid_reader_output": "SEAL_FAILURE_AND_STOP",
                "on_invalid_judge_output": "SEAL_FAILURE_AND_STOP",
                "partial_results_mergeable": False,
            },
            source_hashes={
                "completion_contract": sha256_file(
                    PROJECT / "src/paper_eval/s2_completion_contract.py"
                ),
                "retrieval": retrieval_source,
                "reader": reader_source,
                "judge": judge_source,
                "roles": sha256_file(PROJECT / "src/paper_eval/roles.py"),
            },
        )
        contract_file_hash = _predicted_file_sha256(contract)

        identity = build_s2_completion_adapter_identity(
            retrieval_policy_contract_sha256=contract["contract_sha256"],
            reader_transport=transport,
            reader=reader,
            judge=judge,
            judge_qualification_artifact_sha256=sha256_file(JUDGE_QUALIFICATION),
            source_sha256={
                "retrieval": retrieval_source,
                "reader": reader_source,
                "judge": judge_source,
                "chain": sha256_file(PROJECT / "src/paper_eval/s2_completion_chain.py"),
            },
        )
        identity_file_hash = _predicted_file_sha256(identity)

        evidence_paths = {
            "parent_protocol": ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md",
            "retrieval_amendment": PROJECT / "PAPER_EVALUATION_PROTOCOL_AMENDMENT_v1.1.md",
            "execution_workplan": PROJECT / "S2_COMPLETION_EXECUTION_WORKPLAN_v1.0.md",
            "research_basis": PROJECT / "S2_COMPLETION_POLICY_RESEARCH_BASIS_20260814.md",
            "completion_contract_source": PROJECT / "src/paper_eval/s2_completion_contract.py",
            "completion_contract_test": PROJECT / "tests/test_s2_completion_contract.py",
            "session_policy_source": PROJECT / "src/paper_eval/s2_session_policy.py",
            "session_policy_test": PROJECT / "tests/test_s2_session_policy.py",
            "session_reader_source": PROJECT / "src/paper_eval/s2_session_reader.py",
            "session_reader_test": PROJECT / "tests/test_s2_session_reader.py",
            "completion_chain_source": PROJECT / "src/paper_eval/s2_completion_chain.py",
            "completion_chain_test": PROJECT / "tests/test_s2_completion_chain.py",
            "completion_identity_source": PROJECT / "src/paper_eval/s2_completion_identity.py",
            "completion_identity_test": PROJECT / "tests/test_s2_completion_identity.py",
            "formal_retrieval_source": PROJECT / "src/paper_eval/s2_formal_retrieval.py",
            "formal_retrieval_test": PROJECT / "tests/test_s2_formal_retrieval.py",
            "completion_authority_source": PROJECT / "src/paper_eval/s2_completion_authority.py",
            "completion_authority_test": PROJECT / "tests/test_s2_completion_authority.py",
            "completion_controller_source": PROJECT / "src/paper_eval/s2_completion_controller.py",
            "completion_controller_test": PROJECT / "tests/test_s2_completion_controller.py",
            "completion_production_source": PROJECT / "src/paper_eval/s2_completion_production.py",
            "completion_production_test": PROJECT / "tests/test_s2_completion_production.py",
            "finalize_script": PROJECT / "scripts/finalize_s2_completion.py",
            "run_script": PROJECT / "scripts/run_s2_completion.py",
            "focused_green": PROJECT / "logs/TDD_FOCUSED_GREEN_S2_COMPLETION_PRELIVE_REPAIR_FINAL_20260814.xml",
            "full_offline_green": PROJECT / "logs/TDD_FULL_OFFLINE_GREEN_S2_COMPLETION_PRELIVE_REPAIR_FINAL_20260814.xml",
        }
        evidence_hashes = {name: sha256_file(path) for name, path in evidence_paths.items()}
        if "missing" in evidence_hashes.values():
            raise RuntimeError("policy evidence file is missing")
        git_commit = _git_commit()
        policy = build_completion_policy_freeze(
            contract_file_sha256=contract_file_hash,
            contract_sha256=contract["contract_sha256"],
            adapter_identity_file_sha256=identity_file_hash,
            adapter_identity_sha256=identity["identity_sha256"],
            evidence_sha256=evidence_hashes,
            git_commit=git_commit,
            run_id="s2-completion-policy-20260814-001",
        )
        policy_file_hash = _predicted_file_sha256(policy)

        s1 = _envelope(NATIVE / "U0_SMOKE.json", verdict="PASS")
        u0 = _envelope(NATIVE / "U0_QUALIFICATION.json", verdict="PASS")
        dataset = _envelope(NATIVE / "DATASET_PARITY.json", verdict="PASS")
        evaluator = _envelope(NATIVE / "EVALUATOR_PARITY.json", verdict="PASS")
        r0_paths = S2R0AttemptPaths(
            qualification=NATIVE / "S2_R0_RETRY_002_OFFLINE_QUALIFICATION.json",
            authorization=NATIVE / "S2_R0_RETRY_002_AUTHORIZATION.json",
            consumption=NATIVE / "runs/s2r0-20260814-002/S2_R0_AUTHORIZATION_CONSUMPTION.json",
            result=NATIVE / "runs/s2r0-20260814-002/S2_R0_EPISODE_PROBE.json",
            failure=NATIVE / "runs/s2r0-20260814-002/S2_R0_FAILURE.json",
        )
        r0 = verify_s2r0_attempt(r0_paths)
        if r0.terminal_kind != "SUCCESS" or r0.s3_authorized is not False:
            raise RuntimeError("S2-R0 chain is not verified success")
        current_path = PROJECT / "artifacts/paper_eval/S0_CURRENT_STATE.json"
        role_path = PROJECT / "artifacts/paper_eval/DEVELOPMENT_EXPOSED_IDS.json"
        r0_result = r0_paths.result
        prerequisites = {
            "s1_smoke": {
                "file_sha256": sha256_file(NATIVE / "U0_SMOKE.json"),
                "payload_sha256": s1["payload_sha256"],
                "status": "PASS",
            },
            "u0_qualification": {
                "file_sha256": sha256_file(NATIVE / "U0_QUALIFICATION.json"),
                "payload_sha256": u0["payload_sha256"],
                "status": "PASS",
            },
            "dataset_parity": {
                "file_sha256": sha256_file(NATIVE / "DATASET_PARITY.json"),
                "payload_sha256": dataset["payload_sha256"],
                "status": "PASS",
            },
            "evaluator_parity": {
                "file_sha256": sha256_file(NATIVE / "EVALUATOR_PARITY.json"),
                "payload_sha256": evaluator["payload_sha256"],
                "status": "PASS",
            },
            "development_roles": {
                "file_sha256": sha256_file(role_path),
                "payload_sha256": role_envelope["payload_sha256"],
                "status": "PASS",
            },
            "current_state": {
                "file_sha256": sha256_file(current_path),
                "payload_sha256": state["payload_sha256"],
                "status": "VERIFIED",
            },
            "judge_qualification": {
                "file_sha256": sha256_file(JUDGE_QUALIFICATION),
                "payload_sha256": judge_summary["payload_sha256"],
                "status": "PASS",
            },
            "s2r0_chain": {
                "file_sha256": sha256_file(r0_result),
                "payload_sha256": _load_json(r0_result)["payload_sha256"],
                "status": "VERIFIED",
            },
        }
        qualification = build_completion_offline_qualification(
            policy_freeze=policy,
            policy_freeze_file_sha256=policy_file_hash,
            prerequisites=prerequisites,
            history_id=HISTORY_ID,
            namespace=NAMESPACE,
            expected_session_count=49,
            expected_gold_count=2,
            git_commit=git_commit,
            run_id="s2-completion-qualification-20260814-001",
        )
        qualification_file_hash = _predicted_file_sha256(qualification)
        authorization = build_completion_authorization(
            qualification=qualification,
            qualification_file_sha256=qualification_file_hash,
            policy_freeze_file_sha256=policy_file_hash,
            adapter_identity_file_sha256=identity_file_hash,
            adapter_identity_sha256=identity["identity_sha256"],
            run_id=RUN_ID,
            history_id=HISTORY_ID,
            namespace=NAMESPACE,
            consumption_path=RUN_DIR / "S2_COMPLETION_AUTHORIZATION_CONSUMPTION.json",
            result_path=RUN_DIR / "S2_COMPLETION_RESULT.json",
            failure_path=RUN_DIR / "S2_COMPLETION_FAILURE.json",
            git_commit=git_commit,
        )

        for path, value in (
            (CONTRACT_PATH, contract),
            (IDENTITY_PATH, identity),
            (POLICY_PATH, policy),
            (QUALIFICATION_PATH, qualification),
            (AUTHORIZATION_PATH, authorization),
        ):
            _write_exclusive(path, value)
        print(
            json.dumps(
                {
                    "status": "S2_COMPLETION_AUTHORIZED",
                    "run_id": RUN_ID,
                    "contract_sha256": contract["contract_sha256"],
                    "adapter_identity_sha256": identity["identity_sha256"],
                    "policy_file_sha256": sha256_file(POLICY_PATH),
                    "qualification_file_sha256": sha256_file(QUALIFICATION_PATH),
                    "authorization_file_sha256": sha256_file(AUTHORIZATION_PATH),
                    "live_requests_performed": 0,
                    "s3_authorized": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        asyncio.run(_close(transport, judge))


if __name__ == "__main__":
    main()
