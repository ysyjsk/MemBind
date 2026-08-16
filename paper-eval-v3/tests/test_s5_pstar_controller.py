"""Offline TDD for the single-use P*(C=2) production controller."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from paper_eval.artifacts import finalize_envelope, payload_sha256, sha256_file
from paper_eval.s5_live_authority import (
    build_s5_live_authority,
    finalize_s5_live_authority,
)
from paper_eval.s5_live_preflight import (
    evaluate_s5_live_preflight,
    finalize_s5_live_preflight,
)
from paper_eval.s5_native_method_adapters import S5EpisodeRef
from paper_eval.s5_production_identity_qualification import (
    verify_s5_production_identity_qualification,
)
from paper_eval.s5_a0_result_finalizer import finalize_s5_a0_result
from tests.test_s5_a0_result_finalizer import _completed_chain
from paper_eval.s5_pstar_controller import (
    S5PStarControllerPaths,
    execute_s5_pstar_controller,
    inspect_s5_pstar_controller_attempt,
)
from tests.test_s5_live_preflight import _identity, _pointer, _qualification


PROJECT = Path(__file__).resolve().parents[1]
CONTROLLER_SOURCE = PROJECT / "src/paper_eval/s5_pstar_controller.py"
AUTHORITY_SOURCE = PROJECT / "src/paper_eval/s5_live_authority.py"
RESULT_VERIFIER_SOURCE = PROJECT / "src/paper_eval/s5_pstar_result_finalizer.py"
RUN_ID = "s5-p-star-20260816-101"
NAMESPACE = f"pev3-{RUN_ID}"
SOURCE_SHA256S = tuple(f"{index + 1:064x}" for index in range(49))
SOURCE_MANIFEST_SHA256 = payload_sha256(
    [
        {"source_sequence": index, "source_sha256": digest}
        for index, digest in enumerate(SOURCE_SHA256S)
    ]
)


@dataclass(frozen=True)
class _NativeEpisode:
    group_id: str
    source_sequence: int


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="ascii")


def _episodes() -> tuple[S5EpisodeRef, ...]:
    return tuple(
        S5EpisodeRef(index, digest, _NativeEpisode(NAMESPACE, index))
        for index, digest in enumerate(SOURCE_SHA256S)
    )


def _chain(tmp_path: Path) -> tuple[S5PStarControllerPaths, tuple[S5EpisodeRef, ...]]:
    identity_path = tmp_path / "identity.json"
    qualification_path = tmp_path / "qualification.json"
    pointer_path = tmp_path / "pointer.json"
    preflight_path = tmp_path / "preflight.json"
    authority_path = tmp_path / "authority.json"
    predecessor_path = tmp_path / "S5_A0_RESULT.json"
    run_root = tmp_path / "runs" / RUN_ID

    identity = _identity("P*")
    _write(identity_path, identity)
    pointer = _pointer()
    _write(pointer_path, pointer)
    pointer_file_sha = sha256_file(pointer_path)

    template = _qualification("P*")
    payload = copy.deepcopy(template["payload"])
    payload["production_identity_sha256"] = identity["identity_sha256"]
    payload["production_identity_file_sha256"] = sha256_file(identity_path)
    payload["current_stage_pointer"] = {
        "file_sha256": pointer_file_sha,
        "payload_sha256": pointer["payload_sha256"],
        "run_id": pointer["run_id"],
        "current_stage": "S3_CONFIGURATION_FROZEN",
    }
    qualification = verify_s5_production_identity_qualification(
        finalize_envelope(
            payload=payload,
            protocol_version="paper-eval-v3",
            git_commit="deadbeef",
            run_id="s5-p-star-production-identity-qualification-test",
        )
    )
    _write(qualification_path, qualification)
    qualification_file_sha = sha256_file(qualification_path)

    a0_paths = _completed_chain(tmp_path / "a0-predecessor")
    predecessor = finalize_s5_a0_result(paths=a0_paths, git_commit="deadbeef")
    predecessor_path = a0_paths.result
    predecessor_binding = {
        "method": "A0",
        "verdict": "PASS",
        "artifact_sha256": sha256_file(predecessor_path),
    }
    evaluation = evaluate_s5_live_preflight(
        method="P*",
        run_id=RUN_ID,
        namespace=NAMESPACE,
        episode_source_sha256s=SOURCE_SHA256S,
        observations={
            "construction": {
                "served_model_id": "qwen3-32b-fp8",
                "vllm_version": "0.26.0",
                "max_model_len": 65536,
            },
            "embedding": {"served_model_id": "qwen3-embedding-0.6b"},
            "neo4j_connectivity": True,
            "namespace": NAMESPACE,
            "namespace_state": {"node_count": 0, "relationship_count": 0},
        },
        production_identity_qualification=qualification,
        production_identity_qualification_file_sha256=qualification_file_sha,
        current_stage_pointer=pointer,
        current_stage_pointer_file_sha256=pointer_file_sha,
        predecessor=predecessor_binding,
    )
    preflight = finalize_s5_live_preflight(
        output_path=preflight_path,
        evaluation=evaluation,
        source_sha256={
            "contract": "1" * 64,
            "production": "2" * 64,
            "contract_test": "3" * 64,
            "production_test": "4" * 64,
        },
        git_commit="deadbeef",
    )
    authority = build_s5_live_authority(
        method="P*",
        run={
            "method": "P*",
            "run_id": RUN_ID,
            "namespace": NAMESPACE,
            "history_id": "07741c45",
            "episode_count": 49,
            "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
            "configured_concurrency": 2,
        },
        production_identity_qualification=qualification,
        production_identity_qualification_file_sha256=qualification_file_sha,
        preflight=preflight,
        preflight_file_sha256=sha256_file(preflight_path),
        current_stage_pointer_sha256=pointer_file_sha,
        predecessor={
            "method": "A0",
            "verdict": "PASS",
            "result_file_sha256": sha256_file(predecessor_path),
            "result_payload_sha256": predecessor["payload_sha256"],
        },
        fx0_qualification=None,
        source_sha256={
            "authority": sha256_file(AUTHORITY_SOURCE),
            "controller": sha256_file(CONTROLLER_SOURCE),
            "result_verifier": sha256_file(RESULT_VERIFIER_SOURCE),
            "test": sha256_file(Path(__file__)),
        },
    )
    finalize_s5_live_authority(
        output_path=authority_path,
        authority=authority["payload"],
        git_commit="deadbeef",
        run_id=f"{RUN_ID}-authority",
    )
    return (
        S5PStarControllerPaths(
            production_identity=identity_path,
            production_identity_qualification=qualification_path,
            current_stage_pointer=pointer_path,
            preflight=preflight_path,
            authority=authority_path,
            predecessor=predecessor_path,
            consumption=run_root / "authority_consumption.json",
            controller_root=run_root / "controller",
            attempt_root=run_root / "attempt",
        ),
        _episodes(),
    )


class _Runner:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome

    async def run(self):
        return self.outcome


def _execute(paths, episodes, outcome):
    runtime = SimpleNamespace(graphiti=object())
    return asyncio.run(
        execute_s5_pstar_controller(
            paths=paths,
            episodes=episodes,
            git_commit="deadbeef",
            env_loader=lambda: {"opaque": "private"},
            runtime_factory=lambda _env: runtime,
            readiness=lambda _runtime: None,
            binding_loader=lambda: object(),
            runner_factory=lambda **_kwargs: _Runner(outcome),
            close_runtime=lambda _runtime: None,
        )
    )


def test_pass_and_complete_scientific_outcome_are_both_controller_complete(tmp_path: Path):
    for status, payload_status in (
        ("complete", "PASS"),
        ("scientific_outcome_complete", "SCIENTIFIC_OUTCOME_COMPLETE"),
    ):
        root = tmp_path / status
        paths, episodes = _chain(root)
        result = _execute(
            paths,
            episodes,
            {
                "status": status,
                "resume_authorized": False,
                "payload": {"status": payload_status},
            },
        )
        assert result["status"] == "controller_complete_evidence_only"
        assert result["native_attempt_status"] == status
        assert result["scientific_outcome_candidate"] is True
        assert result["next_method_authorized"] is False
        inspected = inspect_s5_pstar_controller_attempt(paths.controller_root)
        assert inspected["checkpoint"]["status"] == "controller_complete_evidence_only"


def test_infrastructure_or_telemetry_failure_remains_incomplete_non_mergeable(tmp_path: Path):
    paths, episodes = _chain(tmp_path)
    result = _execute(
        paths,
        episodes,
        {
            "status": "incomplete_non_mergeable",
            "resume_authorized": False,
            "payload": {"status": "FAIL_CLOSED"},
        },
    )
    assert result["status"] == "incomplete_non_mergeable"
    assert result["scientific_outcome_candidate"] is False
    assert result["resume_authorized"] is False
    assert result["namespace_cleanup_authorized"] is False


def test_controller_binds_a0_predecessor_file_and_payload(tmp_path: Path):
    paths, episodes = _chain(tmp_path)
    predecessor = json.loads(paths.predecessor.read_text())
    predecessor["payload"]["verdict"] = "FAIL"
    _write(paths.predecessor, predecessor)
    try:
        _execute(paths, episodes, {"status": "complete", "payload": {"status": "PASS"}})
    except Exception as error:
        assert "predecessor" in str(error)
    else:
        raise AssertionError("tampered predecessor must fail before consumption")
    assert not paths.consumption.exists()


def test_self_sealed_minimal_a0_pass_is_not_a_valid_predecessor(tmp_path: Path):
    paths, episodes = _chain(tmp_path)
    fake = finalize_envelope(
        payload={"method": "A0", "verdict": "PASS"},
        protocol_version="paper-eval-v3", git_commit="deadbeef",
        run_id="s5-a0-fake-result",
    )
    _write(paths.predecessor, fake)
    authority = json.loads(paths.authority.read_text())
    authority["payload"]["predecessor"]["result_file_sha256"] = sha256_file(paths.predecessor)
    authority["payload"]["predecessor"]["result_payload_sha256"] = fake["payload_sha256"]
    _write(paths.authority, finalize_envelope(
        payload=authority["payload"], protocol_version="paper-eval-v3",
        git_commit="deadbeef", run_id=authority["run_id"],
    ))
    try:
        _execute(paths, episodes, {"status": "complete", "payload": {"status": "PASS"}})
    except Exception as error:
        assert "predecessor" in str(error)
    else:
        raise AssertionError("minimal self-sealed A0 label must fail closed")
    assert not paths.consumption.exists()
