from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s2_r0_result_verifier import (
    S2R0AttemptPaths,
    S2R0VerificationError,
    verify_s2r0_attempt,
)


ROOT = Path(__file__).parents[1]
NATIVE = ROOT / "artifacts/paper_eval/native"


def _production_paths(run_number: int) -> S2R0AttemptPaths:
    run_id = f"s2r0-20260814-{run_number:03d}"
    run_dir = NATIVE / "runs" / run_id
    if run_number == 1:
        qualification = NATIVE / "S2_R0_OFFLINE_QUALIFICATION.json"
        authorization = NATIVE / "S2_R0_AUTHORIZATION.json"
    elif run_number == 2:
        qualification = NATIVE / "S2_R0_RETRY_002_OFFLINE_QUALIFICATION.json"
        authorization = NATIVE / "S2_R0_RETRY_002_AUTHORIZATION.json"
    else:
        raise ValueError("unsupported production fixture")
    return S2R0AttemptPaths(
        qualification=qualification,
        authorization=authorization,
        consumption=run_dir / "S2_R0_AUTHORIZATION_CONSUMPTION.json",
        result=run_dir / "S2_R0_EPISODE_PROBE.json",
        failure=run_dir / "S2_R0_FAILURE.json",
    )


def _copy_attempt(tmp_path: Path, run_number: int) -> S2R0AttemptPaths:
    source = _production_paths(run_number)
    target = tmp_path / f"attempt-{run_number:03d}"
    target.mkdir()
    paths = S2R0AttemptPaths(
        qualification=target / "qualification.json",
        authorization=target / "authorization.json",
        consumption=target / "S2_R0_AUTHORIZATION_CONSUMPTION.json",
        result=target / "S2_R0_EPISODE_PROBE.json",
        failure=target / "S2_R0_FAILURE.json",
    )
    for old, new in (
        (source.qualification, paths.qualification),
        (source.authorization, paths.authorization),
        (source.consumption, paths.consumption),
        (source.result, paths.result),
        (source.failure, paths.failure),
    ):
        if old.exists():
            shutil.copyfile(old, new)
    return paths


def _mutate(
    path: Path,
    change: Callable[[dict], None],
    *,
    reseal_payload: bool,
) -> None:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    change(envelope)
    if reseal_payload:
        envelope["payload_sha256"] = payload_sha256(envelope["payload"])
    path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_verifies_terminal_attempt_001_failure_chain() -> None:
    paths = _production_paths(1)
    outcome = verify_s2r0_attempt(paths)

    assert outcome.run_id == "s2r0-20260814-001"
    assert outcome.terminal_kind == "FAILURE"
    assert outcome.terminal_status == "FAILED_STOPPED"
    assert outcome.interpretation == "NOT_PRODUCED"
    assert outcome.authorization_sha256 == sha256_file(paths.authorization)
    assert outcome.consumption_sha256 == sha256_file(paths.consumption)
    assert outcome.terminal_sha256 == sha256_file(paths.failure)
    assert outcome.graphiti_search_calls == 1
    assert outcome.neo4j_read_requests == 1
    assert outcome.forbidden_call_counts == {
        "construction_llm_requests": 0,
        "embedding_requests": 0,
        "cross_encoder_requests": 0,
        "reader_requests": 0,
        "judge_requests": 0,
        "database_mutation_attempts": 0,
        "database_mutations": 0,
        "namespace_cleanup_calls": 0,
        "retry_count": 0,
    }
    assert outcome.s3_authorized is False


def test_verifies_terminal_attempt_002_success_chain() -> None:
    paths = _production_paths(2)
    outcome = verify_s2r0_attempt(paths)

    assert outcome.run_id == "s2r0-20260814-002"
    assert outcome.terminal_kind == "SUCCESS"
    assert outcome.terminal_status == "READ_ONLY_RETRIEVAL_SURFACE_DIAGNOSTIC"
    assert outcome.interpretation == "EDGE_SURFACE_COVERAGE_GAP_CONFIRMED"
    assert outcome.authorization_sha256 == sha256_file(paths.authorization)
    assert outcome.consumption_sha256 == sha256_file(paths.consumption)
    assert outcome.terminal_sha256 == sha256_file(paths.result)
    assert outcome.graphiti_search_calls == 1
    assert outcome.neo4j_read_requests == 2
    assert all(value == 0 for value in outcome.forbidden_call_counts.values())
    assert outcome.s3_authorized is False


@pytest.mark.parametrize(
    ("run_number", "component"),
    [
        (1, "qualification"),
        (1, "authorization"),
        (1, "consumption"),
        (1, "failure"),
        (2, "qualification"),
        (2, "authorization"),
        (2, "consumption"),
        (2, "result"),
    ],
)
def test_rejects_unsealed_payload_tamper(
    tmp_path: Path, run_number: int, component: str
) -> None:
    paths = _copy_attempt(tmp_path, run_number)
    path = getattr(paths, component)
    _mutate(
        path,
        lambda envelope: envelope["payload"].update({"history_id": "tampered"}),
        reseal_payload=False,
    )

    with pytest.raises(S2R0VerificationError, match="envelope"):
        verify_s2r0_attempt(paths)


@pytest.mark.parametrize(
    ("run_number", "component", "field", "value"),
    [
        (1, "qualification", "history_id", "other-history"),
        (1, "authorization", "namespace", "other-namespace"),
        (1, "consumption", "run_id", "s2r0-other"),
        (1, "failure", "authorization_sha256", "0" * 64),
        (2, "authorization", "qualification_sha256", "0" * 64),
        (2, "consumption", "authorization_sha256", "0" * 64),
        (2, "result", "consumption_sha256", "0" * 64),
    ],
)
def test_rejects_resealed_hash_or_identity_drift(
    tmp_path: Path,
    run_number: int,
    component: str,
    field: str,
    value: object,
) -> None:
    paths = _copy_attempt(tmp_path, run_number)
    _mutate(
        getattr(paths, component),
        lambda envelope: envelope["payload"].update({field: value}),
        reseal_payload=True,
    )

    with pytest.raises(S2R0VerificationError):
        verify_s2r0_attempt(paths)


@pytest.mark.parametrize("run_number", [1, 2])
def test_rejects_resealed_binding_or_source_drift(
    tmp_path: Path, run_number: int
) -> None:
    paths = _copy_attempt(tmp_path, run_number)
    if run_number == 1:
        path = paths.consumption
        field = "binding_sha256"
    else:
        path = paths.result
        field = "source_sha256"

    def drift(envelope: dict) -> None:
        bindings = dict(envelope["payload"][field])
        bindings["parent_protocol"] = "0" * 64
        envelope["payload"][field] = bindings

    _mutate(path, drift, reseal_payload=True)

    with pytest.raises(S2R0VerificationError, match="binding|source"):
        verify_s2r0_attempt(paths)


@pytest.mark.parametrize(
    ("run_number", "component", "counter", "value"),
    [
        (1, "failure", "retry_count", 1),
        (1, "failure", "graphiti_search_calls", 2),
        (2, "result", "reader_requests", 1),
        (2, "result", "neo4j_read_requests", 0),
    ],
)
def test_rejects_resealed_counter_violation(
    tmp_path: Path,
    run_number: int,
    component: str,
    counter: str,
    value: int,
) -> None:
    paths = _copy_attempt(tmp_path, run_number)
    _mutate(
        getattr(paths, component),
        lambda envelope: envelope["payload"].update({counter: value}),
        reseal_payload=True,
    )

    with pytest.raises(S2R0VerificationError, match="counter"):
        verify_s2r0_attempt(paths)


@pytest.mark.parametrize(
    ("run_number", "component"),
    [
        (1, "qualification"),
        (1, "authorization"),
        (1, "consumption"),
        (1, "failure"),
        (2, "qualification"),
        (2, "authorization"),
        (2, "consumption"),
        (2, "result"),
    ],
)
def test_rejects_any_resealed_s3_authorization(
    tmp_path: Path, run_number: int, component: str
) -> None:
    paths = _copy_attempt(tmp_path, run_number)
    _mutate(
        getattr(paths, component),
        lambda envelope: envelope["payload"].update({"s3_authorized": True}),
        reseal_payload=True,
    )

    with pytest.raises(S2R0VerificationError, match="S3"):
        verify_s2r0_attempt(paths)


def test_rejects_mutually_nonexclusive_terminal_artifacts(tmp_path: Path) -> None:
    paths = _copy_attempt(tmp_path, 2)
    shutil.copyfile(paths.result, paths.failure)

    with pytest.raises(S2R0VerificationError, match="exactly one terminal"):
        verify_s2r0_attempt(paths)


def test_rejects_missing_terminal_artifact(tmp_path: Path) -> None:
    paths = _copy_attempt(tmp_path, 1)
    paths.failure.unlink()

    with pytest.raises(S2R0VerificationError, match="exactly one terminal"):
        verify_s2r0_attempt(paths)
