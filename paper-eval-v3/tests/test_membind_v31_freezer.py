"""RED-first contracts for the offline MemBind v3.1 qualification freezer."""

from __future__ import annotations

import asyncio
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.freezer import (
    FROZEN_FILENAMES,
    MemBindV31FreezerError,
    V31FreezePaths,
    freeze_v31_qualification,
    load_v31_state_cut_certification,
    verify_v31_qualification_artifacts,
)


REPOSITORY = Path(__file__).resolve().parents[2]


def _paths(output_dir: Path) -> V31FreezePaths:
    return V31FreezePaths.from_repository(REPOSITORY, output_dir=output_dir)


def _read_all(root: Path) -> dict[str, dict[str, object]]:
    return {
        name: json.loads((root / name).read_text(encoding="utf-8"))
        for name in FROZEN_FILENAMES
    }


def test_freezer_emits_six_self_hashing_content_safe_artifacts(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "freeze")
    frozen = asyncio.run(freeze_v31_qualification(paths))
    verified = verify_v31_qualification_artifacts(paths)

    assert tuple(frozen) == FROZEN_FILENAMES
    assert frozen == verified == _read_all(paths.output_dir)
    for document in verified.values():
        body = {key: value for key, value in document.items() if key != "payload_sha256"}
        assert document["payload_sha256"] == payload_sha256(body)
        assert document["status"] == "PASS"
        serialized = json.dumps(document, sort_keys=True).casefold()
        for private in ("api_key", "authorization", "password", "raw prompt", "raw response"):
            assert private not in serialized


def test_workload_complexity_freezes_content_free_source_normalizers(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path / "freeze")
    frozen = asyncio.run(freeze_v31_qualification(paths))
    workload = frozen["V31_WORKLOAD_COMPLEXITY.json"]

    assert workload["source_manifest_sha256"] == frozen["V31_REUSE_AUDIT.json"][
        "baseline"
    ]["source_manifest_sha256"]
    assert workload["definitions"] == {
        "source_input_characters": "sum(len(rendered Episode.body))",
        "source_input_tokens": (
            "sum(Qwen tokenizer encode(rendered Episode.body, "
            "add_special_tokens=False))"
        ),
        "source_turn": "one raw message in each frozen LongMemEval session",
    }
    assert workload["renderer_identity"]["path"] == "membind-validation/src/dataset.py"
    assert workload["renderer_identity"]["sha256"] == (
        "0dc97963f4e6143b555853d6061967b6e7606d36e0cba66acc70e27ba0a4d163"
    )
    assert workload["tokenizer_identity"]["repository"] == "Qwen/Qwen3-32B-FP8"
    assert workload["tokenizer_identity"]["revision"] == (
        "aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df"
    )
    assert workload["tokenizer_identity"]["add_special_tokens"] is False
    assert list(workload["histories"]) == [
        "07741c45",
        "b6019101",
        "6071bd76",
        "a2f3aa27",
    ]
    assert workload["histories"] == {
        "07741c45": {
            "episode_count": 49,
            "source_turn_count": 527,
            "source_input_token_count": 104014,
            "source_input_character_count": 475885,
        },
        "b6019101": {
            "episode_count": 49,
            "source_turn_count": 510,
            "source_input_token_count": 106914,
            "source_input_character_count": 502571,
        },
        "6071bd76": {
            "episode_count": 46,
            "source_turn_count": 482,
            "source_input_token_count": 105786,
            "source_input_character_count": 491502,
        },
        "a2f3aa27": {
            "episode_count": 44,
            "source_turn_count": 448,
            "source_input_token_count": 105977,
            "source_input_character_count": 491801,
        },
    }
    assert workload["totals"] == {
        key: sum(values[key] for values in workload["histories"].values())
        for key in (
            "episode_count",
            "source_turn_count",
            "source_input_token_count",
            "source_input_character_count",
        )
    }
    assert workload["totals"] == {
        "episode_count": 188,
        "source_turn_count": 1967,
        "source_input_token_count": 422691,
        "source_input_character_count": 1961759,
    }

    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(child) for child in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(child) for child in value))
        return set()

    assert not {"records", "messages", "body", "token_ids"} & keys(workload)


def test_reuse_and_execution_freezes_bind_exact_authorities_and_c_w_k(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "freeze")
    frozen = asyncio.run(freeze_v31_qualification(paths))
    reuse = frozen["V31_REUSE_AUDIT.json"]
    envelope = frozen["V31_EXECUTION_ENVELOPE.json"]

    assert reuse["methodology_sha256"] == envelope["methodology_sha256"]
    assert reuse["workplan_sha256"] == envelope["workplan_sha256"]
    assert reuse["baseline"]["run_id"] == "apc-baseline-dev-20260817-001"
    assert reuse["canonical_projection_reuse"]["reuse_scope"] == (
        "SCHEMA_AND_EXPORT_IMPLEMENTATION_ONLY"
    )
    assert reuse["canonical_projection_reuse"]["old_d0_result_authority"] == (
        "NOT_V31_SERIALIZABILITY_EVIDENCE"
    )
    assert envelope["method_knobs"] == {
        "bind_workers": 1,
        "compile_workers_c": 2,
        "global_llm_admission_k": 2,
        "lookahead_w": 2,
    }
    assert envelope["baseline_shared_execution_envelope_sha256"] == (
        reuse["baseline"]["shared_execution_envelope_sha256"]
    )
    assert envelope["backend_contract"]["backend_prefix_match_granularity_tokens"] == 16
    assert envelope["backend_contract"]["granularity_evidence"]["vllm_revision"] == (
        "568afb3a13806beb53bb2e6bd518269357b237c0"
    )
    assert envelope["backend_contract"]["granularity_evidence"]["cache_source_sha256"] == (
        "ee2c0db3e4e6c9e9cab33d8be566c4b8101159d36c0d3787c30d47931ee2a9a4"
    )
    assert envelope["backend_contract"]["decode_context_parallel_size"] == 1
    assert envelope["backend_contract"]["decode_context_parallel_evidence"] == {
        "default_field": "ParallelConfig.decode_context_parallel_size",
        "default_value": 1,
        "source_git_blob": "53688c05d92d9b33dee54e1ecc792f47090e03e9",
        "source_path": "vllm/config/parallel.py",
        "source_sha256": "a6581c267ab265e24905d2f5caa514482c28359f71380c6f894ceab25aa22541",
        "vllm_repository": "https://github.com/vllm-project/vllm",
        "vllm_revision": "568afb3a13806beb53bb2e6bd518269357b237c0",
    }
    assert envelope["request_admission_contract"] == {
        "logical_call_permit_scope": "FORBIDDEN_ACROSS_RETRIES",
        "observed_inflight_counter_unit": "ACTUAL_TRANSPORT_ATTEMPT",
        "permit_unit": "ACTUAL_TRANSPORT_ATTEMPT",
        "retry_policy": "EVERY_ATTEMPT_REACQUIRES_INDEPENDENTLY",
    }
    assert envelope["backend_contract"]["cache_isolation_contract"] == {
        "comparable_methods": [
            "U0-aligned",
            "A0-aligned",
            "P(C=2)-aligned",
            "MemBind-Barrier",
            "MemBind-FIFO",
            "MemBind",
        ],
        "cross_block_prefix_identity_reuse": False,
        "cross_block_warm_inheritance": False,
        "physical_cache_reset_claimed": False,
        "policy_applies_equally_to_all_comparable_methods": True,
        "request_cache_salt": "UNIQUE_FRESH_PER_BLOCK",
        "within_block_prefix_reuse": True,
    }
    assert envelope["backend_contract"]["cache_claim_status"] == "OBSERVATIONAL"
    assert envelope["tokenizer_identity"]["revision"] == (
        "aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df"
    )
    assert envelope["live_service_calls_performed"] is False


def test_offline_freezer_does_not_emit_terminal_baseline_acceptance(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "freeze")
    frozen = asyncio.run(freeze_v31_qualification(paths))

    assert "V31_REUSE_AUDIT.json" in frozen
    assert "V31_BASELINE_ACCEPTANCE.json" not in FROZEN_FILENAMES
    assert "V31_BASELINE_ACCEPTANCE.json" not in frozen
    assert frozen["V31_REUSE_AUDIT.json"]["baseline"]["terminal_acceptance_claimed"] is False


def test_certification_projection_and_serializability_are_cross_bound(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "freeze")
    frozen = asyncio.run(freeze_v31_qualification(paths))
    certification = frozen["STATE_CUT_CERTIFICATION.json"]
    projection = frozen["CANONICAL_PROJECTION_FREEZE.json"]
    serial = frozen["DETERMINISTIC_SERIALIZABILITY_RESULT.json"]

    assert certification["compiled_operator_names"] == [
        "graphiti.extract_edges",
        "graphiti.extract_nodes",
    ]
    assert certification["forbidden_observation_counts"] == {
        "future_evidence_access_count": 0,
        "persistent_state_read_count": 0,
        "persistent_state_write_count": 0,
        "undeclared_external_side_effect_count": 0,
        "undeclared_state_facing_call_count": 0,
    }
    assert projection["state_cut_artifact_sha256"] == certification["payload_sha256"]
    assert projection["canonicalizer"]["reuse_origin"] == "S4_D0"
    assert "uuid" in projection["excluded_non_semantic_keys"]
    assert serial["state_cut_artifact_sha256"] == certification["payload_sha256"]
    assert serial["canonical_projection_artifact_sha256"] == projection["payload_sha256"]
    assert serial["checkpoint_count"] == 2
    assert serial["canonical_state_parity_at_every_checkpoint"] is True
    assert serial["same_prepared_artifact_at_every_checkpoint"] is True
    assert serial["fail_closed_tamper_probe"] == "PASS_REJECTED_DRIFT"
    assert serial["oracle_miss_count"] == serial["hidden_fallback_count"] == 0
    assert all("serial_transition" in row for row in serial["checkpoints"])
    assert all("candidate_transition" in row for row in serial["checkpoints"])
    loaded = load_v31_state_cut_certification(paths)
    assert loaded.certification_sha256 == certification[
        "state_cut_certification_sha256"
    ]


@pytest.mark.parametrize(
    ("compile_workers", "lookahead", "global_k"),
    ((1, 2, 2), (2, 1, 2), (2, 2, 1), (3, 2, 2), (2, 3, 2), (2, 2, 3)),
)
def test_freezer_fails_closed_unless_c_w_k_are_exactly_two(
    tmp_path: Path,
    compile_workers: int,
    lookahead: int,
    global_k: int,
) -> None:
    with pytest.raises(MemBindV31FreezerError, match="method_knobs_not_frozen_to_two"):
        asyncio.run(
            freeze_v31_qualification(
                _paths(tmp_path / "freeze"),
                compile_workers=compile_workers,
                lookahead=lookahead,
                global_llm_admission_k=global_k,
            )
        )
    assert not (tmp_path / "freeze").exists()


def test_verifier_rejects_artifact_or_protocol_tamper(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "freeze")
    asyncio.run(freeze_v31_qualification(paths))
    target = paths.output_dir / "V31_EXECUTION_ENVELOPE.json"
    original = target.read_text(encoding="utf-8")
    tampered = json.loads(original)
    tampered["method_knobs"]["lookahead_w"] = 3
    target.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(MemBindV31FreezerError, match="artifact_hash_mismatch"):
        verify_v31_qualification_artifacts(paths)
    target.write_text(original, encoding="utf-8")

    other = tmp_path / "protocol-drift"
    other.mkdir()
    methodology = other / "methodology.md"
    methodology.write_text(paths.methodology.read_text(encoding="utf-8") + "\ndrift\n")
    drifted = deepcopy(paths)
    object.__setattr__(drifted, "methodology", methodology)
    with pytest.raises(MemBindV31FreezerError, match="methodology_hash_mismatch"):
        verify_v31_qualification_artifacts(drifted)


def test_verifier_rejects_resealed_workload_complexity_drift(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "freeze")
    asyncio.run(freeze_v31_qualification(paths))
    target = paths.output_dir / "V31_WORKLOAD_COMPLEXITY.json"
    tampered = json.loads(target.read_text(encoding="utf-8"))
    tampered["histories"]["07741c45"]["source_turn_count"] += 1
    body = {key: value for key, value in tampered.items() if key != "payload_sha256"}
    tampered["payload_sha256"] = payload_sha256(body)
    target.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(MemBindV31FreezerError, match="workload_complexity_binding_invalid"):
        verify_v31_qualification_artifacts(paths)


def test_existing_conflicting_freeze_is_never_overwritten(tmp_path: Path) -> None:
    paths = _paths(tmp_path / "freeze")
    asyncio.run(freeze_v31_qualification(paths))
    target = paths.output_dir / "V31_REUSE_AUDIT.json"
    target.write_text('{"foreign":"artifact"}\n', encoding="utf-8")

    with pytest.raises(MemBindV31FreezerError, match="existing_artifact_conflict"):
        asyncio.run(freeze_v31_qualification(paths))
    assert target.read_text(encoding="utf-8") == '{"foreign":"artifact"}\n'


def test_cli_freeze_then_verify_without_live_dependencies(tmp_path: Path) -> None:
    output = tmp_path / "cli-freeze"
    command = [
        str(REPOSITORY / "paper-eval-v3/.venv/bin/python"),
        str(REPOSITORY / "paper-eval-v3/scripts/freeze_membind_v31.py"),
    ]
    frozen = subprocess.run(
        [
            *command,
            "freeze",
            "--repository-root",
            str(REPOSITORY),
            "--output-dir",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    verified = subprocess.run(
        [
            *command,
            "verify",
            "--repository-root",
            str(REPOSITORY),
            "--output-dir",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(frozen.stdout)["status"] == "PASS"
    assert json.loads(verified.stdout)["status"] == "PASS"
