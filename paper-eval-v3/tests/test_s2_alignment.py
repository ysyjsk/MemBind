from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from paper_eval.s2_alignment import (
    dataset_parity,
    decide_c2_u0_reuse,
    evaluator_parity,
    dataset_projection_parity,
    write_s2_artifact,
)


def _record(question_id: str = "q1") -> dict[str, object]:
    return {
        "question_id": question_id,
        "question_type": "single-session-user",
        "question": "question",
        "answer": "answer",
        "haystack_session_ids": ["s1", "s2"],
        "haystack_dates": ["2023/01/01", "2023/01/02"],
        "haystack_sessions": [[], []],
        "answer_session_ids": ["s2"],
    }


@dataclass(frozen=True)
class _Episode:
    session_id: str
    reference_time: str
    source_hash: str
    body: str


def _episode_builder(record: dict[str, object]) -> list[_Episode]:
    """Test double for the pinned builder; hash input includes rendered body."""
    question_id = str(record["question_id"])
    episodes = []
    for index, (session, date) in enumerate(
        zip(record["haystack_sessions"], record["haystack_dates"], strict=True)
    ):
        body = "\n".join(f"[USER] {message}" for message in session)
        session_id = str(record["haystack_session_ids"][index])
        source_hash = hashlib.sha256(
            json.dumps(
                {
                    "body": body,
                    "question_id": question_id,
                    "reference_time": str(date),
                    "session_id": session_id,
                    "source_sequence": index,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        episodes.append(_Episode(session_id, str(date), source_hash, body))
    return episodes


def _projection(record: dict[str, object]) -> dict[str, object]:
    episodes = _episode_builder(record)
    return {
        "question_id": record["question_id"],
        "question_type": record["question_type"],
        "session_ids": [episode.session_id for episode in episodes],
        "timestamps": [episode.reference_time for episode in episodes],
        "answer_session_ids": list(record["answer_session_ids"]),
        "question_sha256": hashlib.sha256(str(record["question"]).encode()).hexdigest(),
        "answer_sha256": hashlib.sha256(str(record["answer"]).encode()).hexdigest(),
        "episode_body_hashes": [
            hashlib.sha256(episode.body.encode()).hexdigest() for episode in episodes
        ],
        "episode_source_hashes": [episode.source_hash for episode in episodes],
    }


def test_dataset_parity_checks_all_protocol_fields_and_hashes() -> None:
    report = dataset_parity([_record()], [copy.deepcopy(_record())], ["q1"])
    assert report["verdict"] == "PASS"
    changed = _record()
    changed["answer"] = "changed"
    report = dataset_parity([_record()], [changed], ["q1"])
    assert report["verdict"] == "FAIL"
    assert report["mismatches"][0]["reason"] == "field_or_hash_mismatch"


def test_dataset_parity_rejects_duplicate_ids_missing_fields_and_changed_body() -> None:
    record = _record()
    duplicate = dataset_parity(
        [record, copy.deepcopy(record)], [copy.deepcopy(record)], ["q1"]
    )
    assert duplicate["verdict"] == "FAIL"
    assert any(item["reason"] == "duplicate_left_question_id" for item in duplicate["mismatches"])

    missing = copy.deepcopy(record)
    del missing["question"]
    report = dataset_parity([missing], [copy.deepcopy(record)], ["q1"])
    assert report["verdict"] == "FAIL"
    assert any(item["reason"] == "missing_required_fields" for item in report["mismatches"])

    changed = copy.deepcopy(record)
    changed["haystack_sessions"][0] = ["changed body"]
    report = dataset_parity([record], [changed], ["q1"])
    assert report["verdict"] == "FAIL"
    assert any(item["reason"] == "field_or_hash_mismatch" for item in report["mismatches"])


def test_dataset_projection_parity_requires_episode_projection() -> None:
    record = _record()
    projection = _projection(record)
    report = dataset_projection_parity(
        [record], [projection], ["q1"], episode_builder=_episode_builder
    )
    assert report["verdict"] == "PASS"


def test_dataset_projection_parity_rejects_malformed_episode_hashes() -> None:
    record = _record()
    projection = {
        "question_id": "q1",
        "question_type": "single-session-user",
        "session_ids": ["s1", "s2"],
        "timestamps": ["2023/01/01", "2023/01/02"],
        "answer_session_ids": ["s2"],
        "question_sha256": __import__("hashlib").sha256(b"question").hexdigest(),
        "answer_sha256": __import__("hashlib").sha256(b"answer").hexdigest(),
        "episode_source_hashes": ["a" * 64, "not-a-sha256"],
    }
    report = dataset_projection_parity([record], [projection], ["q1"])
    assert report["verdict"] == "FAIL"
    assert report["mismatches"][0]["reason"] == "episode_source_hash_invalid"


def test_dataset_projection_rejects_duplicate_selected_ids() -> None:
    record = _record()
    report = dataset_projection_parity(
        [record, copy.deepcopy(record)],
        [_projection(record)],
        ["q1"],
        episode_builder=_episode_builder,
    )
    assert report["verdict"] == "FAIL"
    assert any(item["reason"] == "duplicate_source_question_id" for item in report["mismatches"])

    report = dataset_projection_parity(
        [record],
        [_projection(record), _projection(record)],
        ["q1"],
        episode_builder=_episode_builder,
    )
    assert report["verdict"] == "FAIL"
    assert any(item["reason"] == "duplicate_projection_question_id" for item in report["mismatches"])

    report = dataset_projection_parity(
        [record], [_projection(record)], ["q1", "q1"], episode_builder=_episode_builder
    )
    assert report["verdict"] == "FAIL"
    assert any(item["reason"] == "duplicate_selected_question_id" for item in report["mismatches"])


def test_dataset_projection_rejects_missing_required_fields() -> None:
    record = _record()
    del record["haystack_session_ids"]
    report = dataset_projection_parity(
        [record], [], ["q1"], episode_builder=_episode_builder
    )
    assert report["verdict"] == "FAIL"
    assert any(item["reason"] == "missing_required_fields" for item in report["mismatches"])


def test_dataset_projection_requires_lowercase_sha256_episode_hashes() -> None:
    record = _record()
    projection = _projection(record)
    projection["episode_source_hashes"][0] = "A" * 64
    report = dataset_projection_parity(
        [record], [projection], ["q1"], episode_builder=_episode_builder
    )
    assert report["verdict"] == "FAIL"
    assert any(item["reason"] == "episode_source_hash_invalid" for item in report["mismatches"])


def test_dataset_projection_recomputes_body_sensitive_hashes() -> None:
    record = _record()
    projection = _projection(record)
    changed = copy.deepcopy(record)
    changed["haystack_sessions"][0] = ["changed body"]
    report = dataset_projection_parity(
        [changed], [projection], ["q1"], episode_builder=_episode_builder
    )
    assert report["verdict"] == "FAIL"
    assert any(item["reason"] == "episode_source_hash_mismatch" for item in report["mismatches"])


def test_evaluator_parity_requires_prompt_and_label_agreement() -> None:
    route = {
        "question_type": "single-session-user",
        "abstention": False,
        "official_prompt": "official prompt",
        "adapter_prompt": "official prompt",
        "official_label": True,
        "adapter_label": True,
    }
    assert evaluator_parity({"fixture": route})["verdict"] == "PASS"
    route["adapter_label"] = False
    assert evaluator_parity({"fixture": route})["verdict"] == "FAIL"


def test_evaluator_parity_rejects_matching_hashes_for_different_prompts() -> None:
    route = {
        "question_type": "single-session-user",
        "abstention": False,
        "official_prompt": "one",
        "adapter_prompt": "two",
        "official_label": True,
        "adapter_label": True,
    }
    report = evaluator_parity({"fixture": route})
    assert report["verdict"] == "FAIL"
    assert report["mismatches"][0]["reason"] == "prompt_hash_mismatch"


def test_evaluator_parity_checks_aggregate_label_semantics() -> None:
    routes = {
        "yes": {
            "question_type": "single-session-user",
            "abstention": False,
            "official_prompt": "same-yes",
            "adapter_prompt": "same-yes",
            "official_label": True,
            "adapter_label": True,
        },
        "no": {
            "question_type": "single-session-user",
            "abstention": False,
            "official_prompt": "same-no",
            "adapter_prompt": "same-no",
            "official_label": False,
            "adapter_label": False,
        },
    }
    report = evaluator_parity(routes)
    assert report["verdict"] == "PASS"
    assert report["aggregate_label_semantics"]["official_positive_count"] == 1
    assert report["aggregate_label_semantics"]["adapter_positive_count"] == 1
    routes["no"]["adapter_label"] = True
    report = evaluator_parity(routes)
    assert report["verdict"] == "FAIL"
    assert report["mismatches"][-1]["reason"] == "aggregate_label_semantics_mismatch"


def test_c2_revision_drift_forces_case_b() -> None:
    manifest = {
        "run_id": "c2-test",
        "status": "completed",
        "episode_count": 188,
        "checkpoint_sha256": "c" * 64,
        "e1_breakdown_sha256": "e" * 64,
        "top_level_e1_breakdown_sha256": "e" * 64,
        "artifact_inventory": {"checkpoint.json": {}},
        "artifact_sha256": {"checkpoint.json": "c" * 64},
        "telemetry_completeness": {"status": "complete"},
        "provenance": {
            "sanitized_runtime_identity": {
                "construction": {"model_revision": "old"},
                "graphiti": {"version": "0.29.3"},
                "embedding": {"model": "embed"},
            }
        },
    }
    current = {
        "construction": {"repository_revision": "new"},
        "graphiti": {"version": "0.29.3"},
        "embedding": {"model": "embed"},
    }
    decision = decide_c2_u0_reuse(
        c2_manifest=manifest,
        current_runtime=current,
        u0_contract={"graphiti": current["graphiti"], "embedding": current["embedding"]},
    )
    assert decision["case"] == "CASE_B_1_HISTORY_U0_QUALIFICATION"
    assert "construction_model_revision_drift" in decision["reasons"]


def test_c2_matching_comparable_identity_is_case_a() -> None:
    runtime = {
        "construction": {
            "model_revision": "same",
            "served_model_id": "model",
            "vllm_version": "0.26.0",
            "max_model_len": 65536,
        },
        "graphiti": {"version": "0.29.3", "commit": "g"},
        "embedding": {
            "served_model_id": "embed",
            "deployment_fingerprint": "fingerprint",
        },
    }
    manifest = {
        "run_id": "c2-test",
        "status": "completed",
        "episode_count": 188,
        "checkpoint_sha256": "c" * 64,
        "e1_breakdown_sha256": "e" * 64,
        "top_level_e1_breakdown_sha256": "e" * 64,
        "artifact_inventory": {"checkpoint.json": {}},
        "artifact_sha256": {"checkpoint.json": "c" * 64},
        "telemetry_completeness": {"status": "complete"},
        "provenance": {
            "sanitized_runtime_identity": runtime,
            "u0_runtime_source_sha256": "u0-hash",
        },
    }
    current = {
        "construction": {
            "repository_revision": "same",
            "served_model_id": "model",
            "vllm_version": "0.26.0",
            "max_model_len": 65536,
        },
        "graphiti": {"version": "0.29.3", "repository_commit": "g"},
        "embedding": {
            "served_model_id": "embed",
            "deployment_fingerprint": "fingerprint",
        },
    }
    decision = decide_c2_u0_reuse(
        c2_manifest=manifest,
        current_runtime=current,
        u0_contract={"source_hashes": {"u0_runtime_source_sha256": "u0-hash"}},
        c2_verification={
            "status": "verified",
            "run_id": "c2-test",
            "checkpoint_sha256": "c" * 64,
            "e1_breakdown_sha256": "e" * 64,
            "top_level_e1_breakdown_sha256": "e" * 64,
            "indexed_file_count": 1,
            "jsonl_line_count": 188,
            "manifest_sha256": "a" * 64,
        },
    )
    assert decision["case"] == "CASE_A_REUSE_C2"
    assert decision["reasons"] == []


def test_c2_missing_identity_fails_closed_instead_of_none_matching() -> None:
    manifest = {
        "run_id": "c2-test",
        "status": "completed",
        "episode_count": 188,
        "provenance": {"sanitized_runtime_identity": {}},
    }
    decision = decide_c2_u0_reuse(
        c2_manifest=manifest,
        current_runtime={},
        u0_contract={},
    )
    assert decision["case"] == "CASE_B_1_HISTORY_U0_QUALIFICATION"
    assert any(reason.startswith("runtime_identity_missing:") for reason in decision["reasons"])


def test_c2_missing_integrity_evidence_cannot_be_reused() -> None:
    runtime = {
        "construction": {
            "model_revision": "same",
            "served_model_id": "model",
            "vllm_version": "0.26.0",
            "max_model_len": 65536,
        },
        "graphiti": {"version": "0.29.3", "commit": "g"},
        "embedding": {"served_model_id": "embed", "deployment_fingerprint": "fingerprint"},
    }
    manifest = {
        "run_id": "c2-test",
        "status": "completed",
        "episode_count": 188,
        "provenance": {"sanitized_runtime_identity": runtime},
    }
    current = {
        "construction": {
            "repository_revision": "same",
            "served_model_id": "model",
            "vllm_version": "0.26.0",
            "max_model_len": 65536,
        },
        "graphiti": {"version": "0.29.3", "repository_commit": "g"},
        "embedding": {"served_model_id": "embed", "deployment_fingerprint": "fingerprint"},
    }
    decision = decide_c2_u0_reuse(
        c2_manifest=manifest,
        current_runtime=current,
        u0_contract={},
    )
    assert decision["case"] == "CASE_B_1_HISTORY_U0_QUALIFICATION"
    assert "c2_integrity_evidence_missing" in decision["reasons"]


def test_evaluator_parity_reports_official_vs_strict_malformed_output_semantics() -> None:
    routes = {
        "valid-yes": {
            "question_type": "single-session-user",
            "abstention": False,
            "official_prompt": "p1",
            "adapter_prompt": "p1",
            "official_raw_output": "yes",
            "adapter_raw_output": "yes",
            "official_label": True,
            "adapter_label": True,
            "adapter_status": "SUCCESS",
        },
        "valid-no": {
            "question_type": "single-session-user",
            "abstention": False,
            "official_prompt": "p0",
            "adapter_prompt": "p0",
            "official_raw_output": "no",
            "adapter_raw_output": "no",
            "official_label": False,
            "adapter_label": False,
            "adapter_status": "SUCCESS",
        },
        "invalid-yesterday": {
            "question_type": "single-session-user",
            "abstention": False,
            "official_prompt": "p2",
            "adapter_prompt": "p2",
            "official_raw_output": "yesterday",
            "adapter_raw_output": "yesterday",
            "official_label": True,
            "adapter_label": True,
            "adapter_status": "INVALID_OUTPUT",
        },
        "invalid-maybe": {
            "question_type": "single-session-user",
            "abstention": False,
            "official_prompt": "p3",
            "adapter_prompt": "p3",
            "official_raw_output": "maybe",
            "adapter_raw_output": "maybe",
            "official_label": False,
            "adapter_label": False,
            "adapter_status": "INVALID_OUTPUT",
        },
        "invalid-yes-and-no": {
            "question_type": "single-session-user",
            "abstention": False,
            "official_prompt": "p4",
            "adapter_prompt": "p4",
            "official_raw_output": "yes and no",
            "adapter_raw_output": "yes and no",
            "official_label": True,
            "adapter_label": True,
            "adapter_status": "INVALID_OUTPUT",
        },
    }
    report = evaluator_parity(routes)
    assert report["verdict"] == "PASS"
    assert report["aggregate_label_semantics"] == {
        "official_headline_positive_count": 3,
        "adapter_headline_positive_count": 3,
        "adapter_success_positive_count": 1,
        "adapter_invalid_count": 3,
        "total_count": 5,
    }


def test_evaluator_parity_enforces_frozen_prompt_hash_when_supplied() -> None:
    route = {
        "question_type": "single-session-user",
        "abstention": False,
        "official_prompt": "frozen prompt",
        "adapter_prompt": "frozen prompt",
        "official_label": True,
        "adapter_label": True,
    }
    report = evaluator_parity(
        {"fixture": route},
        expected_prompt_hashes={"fixture": "0" * 64},
    )
    assert report["verdict"] == "FAIL"
    assert report["mismatches"][0]["reason"] == "frozen_prompt_hash_mismatch"


def test_s2_artifact_is_sealed_and_safe(tmp_path: Path) -> None:
    output = tmp_path / "alignment.json"
    artifact = write_s2_artifact(
        output,
        {"stage": "S2", "verdict": "PASS", "question": "must not persist"},
        git_commit="deadbeef",
        run_id="s2-test",
    )
    persisted = json.loads(output.read_text())
    assert persisted == artifact
    assert persisted["payload_sha256"]
    assert "api_key" not in output.read_text().lower()
