from __future__ import annotations

import json
import importlib.util
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6.final_qa import (
    FORMAL_BASELINE_SEAL_SHA256,
    V6FinalQAError,
    create_fresh_output_root,
    final_qa_verdict,
    gold_blind_retrieval_arguments,
    graph_namespace,
    retrieval_identity_sha256,
    tree_sha256,
    validate_candidates,
    validate_persisted_episode_rows,
)


HISTORY = "6071bd76"


def _episodes(count: int = 46) -> list[dict[str, object]]:
    return [
        {
            "source_sequence": sequence,
            "session_id": f"session-{sequence:02d}",
            "source_hash": f"{sequence:064x}",
        }
        for sequence in range(count)
    ]


def _graph(*, namespace: str, count: int = 46) -> dict[str, object]:
    return {
        "canonical_graph_hash": "a" * 64,
        "entities": [{"name": "entity", "group_id": namespace}],
        "edges": [],
        "episodes": _episodes(count),
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _candidate(
    root: Path,
    *,
    namespace: str,
    policy: str = "v6",
    source_count: int = 46,
    history_id: str = HISTORY,
) -> None:
    _write_json(
        root / "seal.json",
        {
            "status": "V6_PROBE_SEALED",
            "history_id": history_id,
            "policy": policy,
            "method": "V6_REQUEST_STABILITY_PROBE",
            "source_count": source_count,
            "durable_frontier": source_count - 1,
        },
    )
    _write_json(
        root / "manifest.json",
        {
            "status": "PASS",
            "policy": policy,
            "method": "V6_REQUEST_STABILITY_PROBE",
            "native_graphiti_path": "Graphiti.add_episode",
            "baseline_reference": {
                "formal_run_seal_sha256": FORMAL_BASELINE_SEAL_SHA256,
                "qa_contract_status": "INVALID_RETAINED",
            },
        },
    )
    _write_json(
        root / "proof.json",
        {
            "frontier": {
                "status": "PASS",
                "durable_frontier": source_count - 1,
                "publication_count": source_count,
            },
            "provider": {
                "status": "PASS",
                "capacity": 8,
                "max_outstanding": 8,
                "max_future_outstanding": 7,
            },
            "replay": {
                "status": "PASS",
                "logical_captured": 92,
                "logical_consumed": 92,
            },
        },
    )
    _write_json(
        root / f"histories/{history_id}/canonical_graph.json",
        _graph(namespace=namespace, count=source_count),
    )


def _evaluation(status: str) -> dict[str, object]:
    if status in {"PASS", "FAIL"}:
        return {
            "status": status,
            "correct": status == "PASS",
            "semantic_authority": "OFFICIAL_LONGMEMEVAL_JUDGE",
            "judge_status": "SUCCESS",
        }
    return {
        "status": status,
        "correct": None,
        "semantic_authority": "NONE",
        "judge_status": "INVALID_OUTPUT",
    }


def _qa_row(candidate: str, status: str = "PASS") -> dict[str, object]:
    return {
        "candidate_root": candidate,
        "namespace": f"namespace-{candidate}",
        "retrieval": {
            "ranked_session_ids": [f"session-{index}" for index in range(10)],
            "retrieval_identity_sha256": "b" * 64,
        },
        "session_recall_posthoc": {"recall_at_10": 1.0},
        "headline": {
            "reader_answer": "The ratio changed from 6 oz to 5 oz per tablespoon.",
            "answer_evaluation": _evaluation(status),
        },
        "ablation": {
            "reader_answer": "The ratio changed from 6 oz to 5 oz per tablespoon.",
            "answer_evaluation": _evaluation(status),
        },
    }


def _runtime_evidence(*, roots_unchanged: bool = True) -> dict[str, object]:
    return {
        "construction_calls": 0,
        "graph_writes": 0,
        "candidate_roots_unchanged": roots_unchanged,
    }


def test_candidate_pair_requires_sealed_v6_and_exact_formal_episode_mapping(
    tmp_path: Path,
) -> None:
    first = tmp_path / "v6-r06-v6"
    second = tmp_path / "v6-r07-v6"
    _candidate(first, namespace="namespace-r06")
    _candidate(second, namespace="namespace-r07")
    baseline = tmp_path / "formal-canonical.json"
    _write_json(baseline, _graph(namespace="formal-baseline"))

    candidates = validate_candidates(
        candidate_roots=[first, second], baseline_graph_path=baseline
    )

    assert [item["namespace"] for item in candidates] == [
        "namespace-r06",
        "namespace-r07",
    ]
    assert all(item["source_count"] == 46 for item in candidates)
    assert len({item["episode_mapping_sha256"] for item in candidates}) == 1


@pytest.mark.parametrize(
    ("policy", "count", "history"),
    [("matched-control", 46, HISTORY), ("v6", 45, HISTORY), ("v6", 46, "other")],
)
def test_candidate_pair_rejects_mixed_or_incomplete_candidates(
    tmp_path: Path, policy: str, count: int, history: str
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _candidate(first, namespace="namespace-first")
    _candidate(
        second,
        namespace="namespace-second",
        policy=policy,
        source_count=count,
        history_id=history,
    )
    baseline = tmp_path / "baseline.json"
    _write_json(baseline, _graph(namespace="baseline"))

    with pytest.raises(V6FinalQAError):
        validate_candidates(candidate_roots=[first, second], baseline_graph_path=baseline)


def test_graph_namespace_requires_exactly_one_nonempty_group() -> None:
    assert graph_namespace(_graph(namespace="one")) == "one"
    mixed = _graph(namespace="one")
    mixed["edges"] = [{"group_id": "two"}]
    with pytest.raises(V6FinalQAError, match="namespace"):
        graph_namespace(mixed)


def test_gold_blind_retrieval_arguments_cannot_receive_answer_labels() -> None:
    arguments = gold_blind_retrieval_arguments(
        query="What changed?",
        namespace="namespace",
        episode_uuid_to_session_id={"episode": "session"},
    )
    assert set(arguments) == {"query", "namespace", "episode_uuid_to_session_id"}
    assert not any("gold" in key or "answer" in key for key in arguments)


def test_retrieval_identity_is_session_ranked_and_uuid_independent() -> None:
    ranked = ["session-a", "session-b"]
    first = retrieval_identity_sha256(
        ranked_session_ids=ranked,
        query="What changed?",
        search_config_sha256="a" * 64,
    )
    second = retrieval_identity_sha256(
        ranked_session_ids=list(ranked),
        query="What changed?",
        search_config_sha256="a" * 64,
    )
    assert first == second
    assert first != retrieval_identity_sha256(
        ranked_session_ids=list(reversed(ranked)),
        query="What changed?",
        search_config_sha256="a" * 64,
    )


def test_persisted_episode_coverage_requires_all_46_exact_rows() -> None:
    expected = {
        f"{HISTORY}::episode::{sequence:04d}": f"session-{sequence:02d}"
        for sequence in range(46)
    }
    records = [
        {
            "uuid": f"episode-{sequence}",
            "name": name,
            "group_id": "namespace",
            "content": f"[USER] question {sequence}\n[ASSISTANT] answer {sequence}",
        }
        for sequence, name in enumerate(expected)
    ]

    mapping, rows = validate_persisted_episode_rows(
        records=records, namespace="namespace", expected_name_to_session_id=expected
    )

    assert len(mapping) == len(rows) == 46
    assert mapping["episode-45"] == "session-45"
    with pytest.raises(V6FinalQAError, match="46"):
        validate_persisted_episode_rows(
            records=records[:-1],
            namespace="namespace",
            expected_name_to_session_id=expected,
        )


def test_persisted_episode_coverage_rejects_foreign_or_duplicate_rows() -> None:
    expected = {f"{HISTORY}::episode::0000": "session-00"}
    record = {
        "uuid": "episode-0",
        "name": f"{HISTORY}::episode::0000",
        "group_id": "namespace",
        "content": "[USER] question\n[ASSISTANT] answer",
    }
    with pytest.raises(V6FinalQAError):
        validate_persisted_episode_rows(
            records=[record, record],
            namespace="namespace",
            expected_name_to_session_id=expected,
        )
    with pytest.raises(V6FinalQAError):
        validate_persisted_episode_rows(
            records=[{**record, "group_id": "foreign"}],
            namespace="namespace",
            expected_name_to_session_id=expected,
        )


def test_official_judge_is_required_and_parse_failure_is_indeterminate() -> None:
    rows = [_qa_row("first"), _qa_row("second")]
    rows[1]["headline"]["answer_evaluation"] = _evaluation(
        "UNSCORED_JUDGE_INVALID_OUTPUT"
    )

    result = final_qa_verdict(rows=rows, runtime_evidence=_runtime_evidence())

    assert result["verdict"] == "QA_INDETERMINATE"
    assert result["quality_claim"] is False


def test_one_pass_and_one_fail_is_unstable_and_fails_closed() -> None:
    result = final_qa_verdict(
        rows=[_qa_row("first", "PASS"), _qa_row("second", "FAIL")],
        runtime_evidence=_runtime_evidence(),
    )

    assert result["verdict"] == "VALID_QA_FAIL"
    assert result["repetition_stable"] is False
    assert result["quality_claim"] is False


def test_two_official_passes_with_complete_recall_are_valid() -> None:
    result = final_qa_verdict(
        rows=[_qa_row("first"), _qa_row("second")],
        runtime_evidence=_runtime_evidence(),
    )

    assert result["verdict"] == "VALID_QA_PASS"
    assert result["repetition_stable"] is True
    assert result["quality_claim"] is True
    assert result["headline_pass_count"] == 2
    assert result["complete_posthoc_recall"] is True


def test_missing_posthoc_recall_is_not_a_valid_quality_pass() -> None:
    rows = [_qa_row("first"), _qa_row("second")]
    rows[1].pop("session_recall_posthoc")
    result = final_qa_verdict(rows=rows, runtime_evidence=_runtime_evidence())
    assert result["verdict"] == "QA_INDETERMINATE"
    assert result["complete_posthoc_recall"] is False


@pytest.mark.parametrize(
    "runtime_evidence",
    [
        {"construction_calls": 1, "graph_writes": 0, "candidate_roots_unchanged": True},
        {"construction_calls": 0, "graph_writes": 1, "candidate_roots_unchanged": True},
        {"construction_calls": 0, "graph_writes": 0, "candidate_roots_unchanged": False},
    ],
)
def test_read_only_or_immutability_violation_is_indeterminate(
    runtime_evidence: dict[str, object],
) -> None:
    result = final_qa_verdict(
        rows=[_qa_row("first"), _qa_row("second")],
        runtime_evidence=runtime_evidence,
    )
    assert result["verdict"] == "QA_INDETERMINATE"


def test_output_root_must_be_fresh_and_candidate_tree_hash_detects_mutation(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate"
    _write_json(candidate / "seal.json", {"status": "sealed"})
    before = tree_sha256(candidate)
    output = tmp_path / "qa-sidecar"
    create_fresh_output_root(output)
    with pytest.raises(V6FinalQAError, match="fresh"):
        create_fresh_output_root(output)
    _write_json(candidate / "unexpected.json", {"mutation": True})
    assert tree_sha256(candidate) != before


def test_live_cli_schema_accepts_two_candidates_and_explicit_sidecar(
    tmp_path: Path,
) -> None:
    script = Path(__file__).parents[1] / "scripts/run_v6_final_qa.py"
    spec = importlib.util.spec_from_file_location("run_v6_final_qa", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    args = module.build_parser().parse_args(
        [
            "--candidate-root",
            str(tmp_path / "first"),
            "--candidate-root",
            str(tmp_path / "second"),
            "--output-root",
            str(tmp_path / "qa"),
        ]
    )

    assert args.candidate_root == [tmp_path / "first", tmp_path / "second"]
    assert args.output_root == tmp_path / "qa"


def test_live_cli_defaults_to_frozen_v6_8000_8001_runtime() -> None:
    script = Path(__file__).parents[1] / "scripts/run_v6_final_qa.py"
    spec = importlib.util.spec_from_file_location("run_v6_final_qa_defaults", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = module.build_parser().parse_args(
        ["--candidate-root", "/tmp/first", "--candidate-root", "/tmp/second"]
    )
    assert args.chat_base_url == "http://10.87.5.247:8000/v1"
    assert args.embedding_base_url == "http://10.87.5.247:8001/v1"


def test_live_cli_runtime_env_overrides_historical_qa_embedding_endpoint() -> None:
    script = Path(__file__).parents[1] / "scripts/run_v6_final_qa.py"
    spec = importlib.util.spec_from_file_location("run_v6_final_qa_runtime_env", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module._qa_runtime_env(
        {"EMBEDDING_BASE_URL": "http://10.87.5.247:8003/v1", "EMBEDDING_DIM": "1024"},
        embedding_base_url="http://10.87.5.247:8001/v1",
        embedding_model="qwen3-embedding-0.6b",
    )
    assert result["EMBEDDING_BASE_URL"] == "http://10.87.5.247:8001/v1"
    assert result["EMBEDDING_MODEL"] == "qwen3-embedding-0.6b"
    assert result["EMBEDDING_DIM"] == "1024"
