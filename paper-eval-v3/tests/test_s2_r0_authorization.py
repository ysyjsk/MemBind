from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s2_r0_authorization import (
    REQUIRED_BINDINGS,
    consume_s2r0_authorization,
    finalize_s2r0_authorization,
    finalize_s2r0_offline_qualification,
)


def _junit(path: Path, *, failures: int = 0, skipped: int = 0) -> None:
    path.write_text(
        f'<testsuites><testsuite tests="3" errors="0" failures="{failures}" '
        f'skipped="{skipped}"/></testsuites>\n',
        encoding="utf-8",
    )


def _bindings(tmp_path: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for name in REQUIRED_BINDINGS:
        path = tmp_path / f"{name}.txt"
        if name in {"focused_green", "full_green"}:
            _junit(path)
        elif name == "parent_protocol":
            path.write_bytes(b"parent protocol\n")
        else:
            path.write_text(f"{name}\n", encoding="utf-8")
        result[name] = path
    return result


def _config() -> dict[str, object]:
    return {
        "edge_config": None,
        "node_config": None,
        "episode_config": {
            "search_methods": ["bm25"],
            "reranker": "reciprocal_rank_fusion",
            "sim_min_score": 0.6,
            "mmr_lambda": 0.5,
            "bfs_max_depth": 3,
        },
        "community_config": None,
        "limit": 10,
        "reranker_min_score": 0,
        "candidate_limit": 20,
        "search_filter": "EMPTY",
        "center_node_uuid": None,
        "bfs_origin_node_uuids": None,
        "query_vector": None,
    }


def _qualify(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    bindings = _bindings(tmp_path)
    output = tmp_path / "S2_R0_OFFLINE_QUALIFICATION.json"
    artifact = finalize_s2r0_offline_qualification(
        output,
        binding_paths=bindings,
        expected_parent_protocol_sha256=sha256_file(bindings["parent_protocol"]),
        retrieval_config_identity=_config(),
        dataset_sha256=sha256_file(bindings["dataset"]),
        frozen_split_sha256=sha256_file(bindings["frozen_split"]),
        frozen_corpus_identity_sha256="c" * 64,
        ordered_session_ids_sha256="d" * 64,
        gold_session_ids_sha256="e" * 64,
        episode_names_sha256="f" * 64,
        episode_content_hash_sequence_sha256="1" * 64,
        gold_session_count=2,
        git_commit="deadbeef",
        run_id="s2r0-offline-test",
    )
    assert artifact["payload"]["verdict"] == "PASS"
    assert artifact["payload"]["live_authorized"] is False
    assert artifact["payload_sha256"] == payload_sha256(artifact["payload"])
    return output, bindings


def test_offline_qualification_and_one_shot_authorization_are_hash_bound(
    tmp_path: Path,
) -> None:
    qualification, bindings = _qualify(tmp_path)
    authorization = tmp_path / "S2_R0_AUTHORIZATION.json"
    output = tmp_path / "S2_R0_EPISODE_PROBE.json"
    consumption = tmp_path / "S2_R0_AUTHORIZATION_CONSUMPTION.json"
    sealed = finalize_s2r0_authorization(
        authorization,
        qualification_path=qualification,
        binding_paths=bindings,
        expected_output_path=output,
        consumption_path=consumption,
        git_commit="deadbeef",
        run_id="s2r0-20260814-001",
    )

    payload = sealed["payload"]
    assert payload["authorization"] == "RUN_S2_R0_EPISODE_BM25_ONCE"
    assert payload["history_id"] == "07741c45"
    assert payload["namespace"] == "pev3-s1-20260814-001"
    assert payload["limits"] == {
        "graphiti_search_calls": 1,
        "construction_llm_requests": 0,
        "embedding_requests": 0,
        "cross_encoder_requests": 0,
        "reader_requests": 0,
        "judge_requests": 0,
        "database_mutation_attempts": 0,
        "namespace_cleanup_calls": 0,
        "retry_count": 0,
    }
    assert payload["s3_authorized"] is False
    assert payload["neo4j_auto_schema_initialization"] is False

    consumed, authorization_sha256, verified_payload = consume_s2r0_authorization(
        authorization,
        consumption,
        binding_paths=bindings,
        expected_run_id="s2r0-20260814-001",
        git_commit="deadbeef",
    )
    assert consumed["payload"]["status"] == "CONSUMED_BEFORE_LIVE_IO"
    assert consumed["payload"]["authorization_sha256"] == authorization_sha256
    assert verified_payload == sealed["payload"]
    assert consumption.is_file()

    with pytest.raises(ValueError, match="already consumed"):
        consume_s2r0_authorization(
            authorization,
            consumption,
            binding_paths=bindings,
            expected_run_id="s2r0-20260814-001",
            git_commit="deadbeef",
        )


def test_offline_qualification_rejects_failed_junit(tmp_path: Path) -> None:
    bindings = _bindings(tmp_path)
    _junit(bindings["full_green"], failures=1)
    output = tmp_path / "qualification.json"
    with pytest.raises(ValueError, match="offline regression"):
        finalize_s2r0_offline_qualification(
            output,
            binding_paths=bindings,
            expected_parent_protocol_sha256=sha256_file(bindings["parent_protocol"]),
            retrieval_config_identity=_config(),
            dataset_sha256=sha256_file(bindings["dataset"]),
            frozen_split_sha256=sha256_file(bindings["frozen_split"]),
            frozen_corpus_identity_sha256="c" * 64,
            ordered_session_ids_sha256="d" * 64,
            gold_session_ids_sha256="e" * 64,
            episode_names_sha256="f" * 64,
            episode_content_hash_sequence_sha256="1" * 64,
            gold_session_count=2,
            git_commit="deadbeef",
            run_id="s2r0-offline-test",
        )
    assert not output.exists()


def test_offline_qualification_rejects_skipped_junit(tmp_path: Path) -> None:
    bindings = _bindings(tmp_path)
    _junit(bindings["full_green"], skipped=1)
    output = tmp_path / "qualification.json"
    with pytest.raises(ValueError, match="offline regression"):
        finalize_s2r0_offline_qualification(
            output,
            binding_paths=bindings,
            expected_parent_protocol_sha256=sha256_file(bindings["parent_protocol"]),
            retrieval_config_identity=_config(),
            dataset_sha256=sha256_file(bindings["dataset"]),
            frozen_split_sha256=sha256_file(bindings["frozen_split"]),
            frozen_corpus_identity_sha256="c" * 64,
            ordered_session_ids_sha256="d" * 64,
            gold_session_ids_sha256="e" * 64,
            episode_names_sha256="f" * 64,
            episode_content_hash_sequence_sha256="1" * 64,
            gold_session_count=2,
            git_commit="deadbeef",
            run_id="s2r0-offline-test",
        )
    assert not output.exists()


def test_offline_qualification_rejects_graphiti_default_field_drift(
    tmp_path: Path,
) -> None:
    bindings = _bindings(tmp_path)
    config = _config()
    config["episode_config"] = {
        **config["episode_config"],
        "sim_min_score": 0.5,
    }
    output = tmp_path / "qualification.json"
    with pytest.raises(ValueError, match="retrieval config drift"):
        finalize_s2r0_offline_qualification(
            output,
            binding_paths=bindings,
            expected_parent_protocol_sha256=sha256_file(bindings["parent_protocol"]),
            retrieval_config_identity=config,
            dataset_sha256=sha256_file(bindings["dataset"]),
            frozen_split_sha256=sha256_file(bindings["frozen_split"]),
            frozen_corpus_identity_sha256="c" * 64,
            ordered_session_ids_sha256="d" * 64,
            gold_session_ids_sha256="e" * 64,
            episode_names_sha256="f" * 64,
            episode_content_hash_sequence_sha256="1" * 64,
            gold_session_count=2,
            git_commit="deadbeef",
            run_id="s2r0-offline-test",
        )
    assert not output.exists()


def test_offline_qualification_rejects_dataset_binding_drift(tmp_path: Path) -> None:
    bindings = _bindings(tmp_path)
    with pytest.raises(ValueError, match="dataset binding drift"):
        finalize_s2r0_offline_qualification(
            tmp_path / "qualification.json",
            binding_paths=bindings,
            expected_parent_protocol_sha256=sha256_file(bindings["parent_protocol"]),
            retrieval_config_identity=_config(),
            dataset_sha256="a" * 64,
            frozen_split_sha256=sha256_file(bindings["frozen_split"]),
            frozen_corpus_identity_sha256="c" * 64,
            ordered_session_ids_sha256="d" * 64,
            gold_session_ids_sha256="e" * 64,
            episode_names_sha256="f" * 64,
            episode_content_hash_sequence_sha256="1" * 64,
            gold_session_count=2,
            git_commit="deadbeef",
            run_id="s2r0-offline-test",
        )


def test_authorization_and_consumption_fail_closed_on_binding_drift(
    tmp_path: Path,
) -> None:
    qualification, bindings = _qualify(tmp_path)
    authorization = tmp_path / "authorization.json"
    finalize_s2r0_authorization(
        authorization,
        qualification_path=qualification,
        binding_paths=bindings,
        expected_output_path=tmp_path / "result.json",
        consumption_path=tmp_path / "consumption.json",
        git_commit="deadbeef",
        run_id="s2r0-20260814-001",
    )
    bindings["probe_source"].write_text("drift\n", encoding="utf-8")
    with pytest.raises(ValueError, match="binding drift"):
        consume_s2r0_authorization(
            authorization,
            tmp_path / "consumption.json",
            binding_paths=bindings,
            expected_run_id="s2r0-20260814-001",
            git_commit="deadbeef",
        )


def test_authorization_never_overwrites_existing_outputs(tmp_path: Path) -> None:
    qualification, bindings = _qualify(tmp_path)
    authorization = tmp_path / "authorization.json"
    authorization.write_text("historical\n", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        finalize_s2r0_authorization(
            authorization,
            qualification_path=qualification,
            binding_paths=bindings,
            expected_output_path=tmp_path / "result.json",
            consumption_path=tmp_path / "consumption.json",
            git_commit="deadbeef",
            run_id="s2r0-20260814-001",
        )
    assert authorization.read_text(encoding="utf-8") == "historical\n"


def test_consumption_artifact_contains_no_bound_file_content(tmp_path: Path) -> None:
    qualification, bindings = _qualify(tmp_path)
    authorization = tmp_path / "authorization.json"
    consumption = tmp_path / "consumption.json"
    finalize_s2r0_authorization(
        authorization,
        qualification_path=qualification,
        binding_paths=bindings,
        expected_output_path=tmp_path / "result.json",
        consumption_path=consumption,
        git_commit="deadbeef",
        run_id="s2r0-20260814-001",
    )
    consume_s2r0_authorization(
        authorization,
        consumption,
        binding_paths=bindings,
        expected_run_id="s2r0-20260814-001",
        git_commit="deadbeef",
    )
    serialized = json.dumps(json.loads(consumption.read_text()), sort_keys=True)
    assert "probe_source\\n" not in serialized
    assert "parent protocol" not in serialized


def test_two_consumers_cannot_claim_the_same_authorization(tmp_path: Path) -> None:
    qualification, bindings = _qualify(tmp_path)
    authorization = tmp_path / "authorization.json"
    consumption = tmp_path / "consumption.json"
    finalize_s2r0_authorization(
        authorization,
        qualification_path=qualification,
        binding_paths=bindings,
        expected_output_path=tmp_path / "result.json",
        consumption_path=consumption,
        git_commit="deadbeef",
        run_id="s2r0-20260814-001",
    )

    def claim() -> str:
        try:
            consume_s2r0_authorization(
                authorization,
                consumption,
                binding_paths=bindings,
                expected_run_id="s2r0-20260814-001",
                git_commit="deadbeef",
            )
        except ValueError:
            return "rejected"
        return "consumed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: claim(), range(2)))
    assert sorted(outcomes) == ["consumed", "rejected"]


def test_replacement_qualification_and_authorization_preserve_retry_lineage(
    tmp_path: Path,
) -> None:
    bindings = _bindings(tmp_path)
    lineage = {
        "prior_run_id": "s2r0-20260814-001",
        "replacement_run_id": "s2r0-20260814-002",
        "prior_authorization_sha256": sha256_file(
            bindings["prior_s2r0_authorization"]
        ),
        "prior_consumption_sha256": sha256_file(
            bindings["prior_s2r0_consumption"]
        ),
        "prior_failure_sha256": sha256_file(bindings["prior_s2r0_failure"]),
        "failure_classification": "HARNESS_QUERY_PARAMETER_NAME_COLLISION",
        "automatic_retry": False,
    }
    qualification = tmp_path / "retry-qualification.json"
    sealed = finalize_s2r0_offline_qualification(
        qualification,
        binding_paths=bindings,
        expected_parent_protocol_sha256=sha256_file(bindings["parent_protocol"]),
        retrieval_config_identity=_config(),
        dataset_sha256=sha256_file(bindings["dataset"]),
        frozen_split_sha256=sha256_file(bindings["frozen_split"]),
        frozen_corpus_identity_sha256="c" * 64,
        ordered_session_ids_sha256="d" * 64,
        gold_session_ids_sha256="e" * 64,
        episode_names_sha256="f" * 64,
        episode_content_hash_sequence_sha256="1" * 64,
        gold_session_count=2,
        git_commit="deadbeef",
        run_id="s2r0-20260814-002-offline",
        retry_lineage=lineage,
    )
    assert sealed["payload"]["retry_lineage"] == lineage

    authorization = tmp_path / "retry-authorization.json"
    authorized = finalize_s2r0_authorization(
        authorization,
        qualification_path=qualification,
        binding_paths=bindings,
        expected_output_path=tmp_path / "result.json",
        consumption_path=tmp_path / "consumption.json",
        git_commit="deadbeef",
        run_id="s2r0-20260814-002",
    )
    assert authorized["payload"]["retry_lineage"] == lineage
    assert authorized["payload"]["limits"]["retry_count"] == 0
