"""Offline TDD for the single-use S5 A0 controller lifecycle."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import finalize_envelope, payload_sha256, sha256_file
from paper_eval.s5_a0_controller import (
    S5A0ControllerError,
    S5A0ControllerPaths,
    execute_s5_a0_controller,
    inspect_s5_a0_controller_attempt,
)
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
from tests.test_s5_live_preflight import _identity, _pointer, _qualification


PROJECT = Path(__file__).resolve().parents[1]
CONTROLLER_SOURCE = PROJECT / "src/paper_eval/s5_a0_controller.py"
AUTHORITY_SOURCE = PROJECT / "src/paper_eval/s5_live_authority.py"
RESULT_VERIFIER_SOURCE = PROJECT / "src/paper_eval/s5_a0_result_finalizer.py"
RUN_ID = "s5-a0-20260816-101"
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


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )


def _episodes() -> tuple[S5EpisodeRef, ...]:
    return tuple(
        S5EpisodeRef(
            source_sequence=index,
            source_sha256=digest,
            native_episode=_NativeEpisode(NAMESPACE, index),
        )
        for index, digest in enumerate(SOURCE_SHA256S)
    )


def _sealed_qualification(
    *, identity: dict[str, object], identity_file_sha256: str, pointer: dict
) -> dict[str, object]:
    template = _qualification("A0")
    payload = copy.deepcopy(template["payload"])
    payload["production_identity_sha256"] = identity["identity_sha256"]
    payload["production_identity_file_sha256"] = identity_file_sha256
    payload["current_stage_pointer"] = {
        "file_sha256": "0" * 64,
        "payload_sha256": pointer["payload_sha256"],
        "run_id": pointer["run_id"],
        "current_stage": "S3_CONFIGURATION_FROZEN",
    }
    return finalize_envelope(
        payload=payload,
        protocol_version="paper-eval-v3",
        git_commit="deadbeef",
        run_id="s5-a0-production-identity-qualification-test",
    )


def _chain(tmp_path: Path) -> tuple[S5A0ControllerPaths, tuple[S5EpisodeRef, ...]]:
    identity_path = tmp_path / "S5_A0_PRODUCTION_IDENTITY.json"
    qualification_path = tmp_path / "S5_A0_PRODUCTION_IDENTITY_QUALIFICATION.json"
    pointer_path = tmp_path / "CURRENT_STAGE_STATUS.json"
    preflight_path = tmp_path / "S5_A0_LIVE_PREFLIGHT.json"
    authority_path = tmp_path / "S5_A0_LIVE_AUTHORITY.json"
    consumption_path = tmp_path / "runs" / RUN_ID / "authority_consumption.json"
    controller_root = tmp_path / "runs" / RUN_ID / "controller"
    attempt_root = tmp_path / "runs" / RUN_ID / "attempt"

    identity = _identity("A0")
    _write_json(identity_path, identity)
    pointer = _pointer()
    _write_json(pointer_path, pointer)
    pointer_file_sha = sha256_file(pointer_path)

    qualification = _sealed_qualification(
        identity=identity,
        identity_file_sha256=sha256_file(identity_path),
        pointer=pointer,
    )
    qualification["payload"]["current_stage_pointer"]["file_sha256"] = (
        pointer_file_sha
    )
    qualification = verify_s5_production_identity_qualification(
        finalize_envelope(
            payload=qualification["payload"],
            protocol_version="paper-eval-v3",
            git_commit="deadbeef",
            run_id="s5-a0-production-identity-qualification-test",
        )
    )
    _write_json(qualification_path, qualification)
    qualification_file_sha = sha256_file(qualification_path)

    evaluation = evaluate_s5_live_preflight(
        method="A0",
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
        method="A0",
        run={
            "method": "A0",
            "run_id": RUN_ID,
            "namespace": NAMESPACE,
            "history_id": "07741c45",
            "episode_count": 49,
            "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
            "configured_concurrency": 1,
        },
        production_identity_qualification=qualification,
        production_identity_qualification_file_sha256=qualification_file_sha,
        preflight=preflight,
        preflight_file_sha256=sha256_file(preflight_path),
        current_stage_pointer_sha256=pointer_file_sha,
        predecessor=None,
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
        S5A0ControllerPaths(
            production_identity=identity_path,
            production_identity_qualification=qualification_path,
            current_stage_pointer=pointer_path,
            preflight=preflight_path,
            authority=authority_path,
            consumption=consumption_path,
            controller_root=controller_root,
            attempt_root=attempt_root,
        ),
        _episodes(),
    )


class _Runner:
    def __init__(self, trace: list[str], outcome: object) -> None:
        self.trace = trace
        self.outcome = outcome

    async def run(self):
        self.trace.append("native")
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _dependencies(
    trace: list[str],
    *,
    runtime_error: BaseException | None = None,
    readiness_error: BaseException | None = None,
    runner_outcome: object | None = None,
):
    runtime = SimpleNamespace(graphiti=object())

    def env_loader():
        trace.append("env")
        return {"opaque": "not persisted"}

    def runtime_factory(_env):
        trace.append("runtime")
        if runtime_error is not None:
            raise runtime_error
        return runtime

    async def readiness(_runtime):
        trace.append("readiness")
        if readiness_error is not None:
            raise readiness_error

    def binding_loader():
        trace.append("binding")
        return object()

    def runner_factory(**_kwargs):
        trace.append("runner")
        return _Runner(
            trace,
            runner_outcome
            or {
                "status": "complete",
                "resume_authorized": False,
                "payload": {"status": "PASS"},
            },
        )

    async def close_runtime(_runtime):
        trace.append("close")

    return {
        "env_loader": env_loader,
        "runtime_factory": runtime_factory,
        "readiness": readiness,
        "binding_loader": binding_loader,
        "runner_factory": runner_factory,
        "close_runtime": close_runtime,
    }


def test_complete_sealed_chain_is_validated_before_authority_consumption(
    tmp_path: Path,
) -> None:
    paths, episodes = _chain(tmp_path)
    identity = json.loads(paths.production_identity.read_text(encoding="utf-8"))
    identity["identity_sha256"] = "0" * 64
    _write_json(paths.production_identity, identity)
    trace: list[str] = []

    with pytest.raises(S5A0ControllerError, match="production_identity"):
        asyncio.run(
            execute_s5_a0_controller(
                paths=paths,
                episodes=episodes,
                git_commit="deadbeef",
                **_dependencies(trace),
            )
        )

    assert trace == []
    assert not paths.consumption.exists()
    assert not paths.controller_root.exists()


def test_consumption_is_exclusive_and_precedes_env_runtime_graphiti_and_readiness(
    tmp_path: Path,
) -> None:
    paths, episodes = _chain(tmp_path)
    trace: list[str] = []

    result = asyncio.run(
        execute_s5_a0_controller(
            paths=paths,
            episodes=episodes,
            git_commit="deadbeef",
            **_dependencies(trace),
        )
    )

    assert paths.consumption.is_file()
    assert trace == ["env", "runtime", "readiness", "binding", "runner", "native", "close"]
    inspected = inspect_s5_a0_controller_attempt(paths.controller_root)
    assert inspected["events"][0]["event_type"] == "authority_consumed"
    assert inspected["checkpoint"]["status"] == "controller_complete_evidence_only"
    assert result["scientific_pass_authorized"] is False
    assert result["next_method_authorized"] is False
    assert result["current_stage_pointer_update_authorized"] is False

    trace.clear()
    with pytest.raises(S5A0ControllerError, match="consumption"):
        asyncio.run(
            execute_s5_a0_controller(
                paths=paths,
                episodes=episodes,
                git_commit="deadbeef",
                **_dependencies(trace),
            )
        )
    assert trace == []


@pytest.mark.parametrize(
    ("kind", "expected_stage", "expected_trace"),
    [
        ("runtime", "runtime_construction", ["env", "runtime"]),
        (
            "readiness",
            "runtime_readiness",
            ["env", "runtime", "readiness", "close"],
        ),
        (
            "native",
            "native_execution",
            ["env", "runtime", "readiness", "binding", "runner", "native", "close"],
        ),
    ],
)
def test_post_consumption_failures_are_sanitized_incomplete_and_non_resumable(
    tmp_path: Path,
    kind: str,
    expected_stage: str,
    expected_trace: list[str],
) -> None:
    paths, episodes = _chain(tmp_path)
    trace: list[str] = []
    kwargs = {
        "runtime_error": RuntimeError("secret runtime detail") if kind == "runtime" else None,
        "readiness_error": RuntimeError("secret readiness detail") if kind == "readiness" else None,
        "runner_outcome": RuntimeError("secret native detail") if kind == "native" else None,
    }

    result = asyncio.run(
        execute_s5_a0_controller(
            paths=paths,
            episodes=episodes,
            git_commit="deadbeef",
            **_dependencies(trace, **kwargs),
        )
    )

    assert trace == expected_trace
    assert result == {
        "status": "incomplete_non_mergeable",
        "failure_stage": expected_stage,
        "error_class": "builtins.RuntimeError",
        "resume_authorized": False,
        "namespace_cleanup_authorized": False,
        "scientific_pass_authorized": False,
        "next_method_authorized": False,
        "current_stage_pointer_update_authorized": False,
    }
    inspected = inspect_s5_a0_controller_attempt(paths.controller_root)
    checkpoint = inspected["checkpoint"]
    assert checkpoint["status"] == "incomplete_non_mergeable"
    assert checkpoint["failure_stage"] == expected_stage
    assert checkpoint["error_class"] == "builtins.RuntimeError"
    assert "secret" not in paths.controller_root.joinpath("events.jsonl").read_text()
    assert checkpoint["resume_authorized"] is False
    assert checkpoint["namespace_cleanup_authorized"] is False
    assert checkpoint["next_method_authorized"] is False
    assert checkpoint["current_stage_pointer_update_authorized"] is False


def test_native_incomplete_result_is_not_promoted_to_controller_or_scientific_pass(
    tmp_path: Path,
) -> None:
    paths, episodes = _chain(tmp_path)
    trace: list[str] = []
    native_result = {
        "status": "incomplete_non_mergeable",
        "resume_authorized": False,
        "payload": {
            "status": "FAIL_CLOSED",
            "events": [
                {
                    "event_type": "treatment_failure",
                    "error_class": "httpx.ConnectError",
                }
            ],
        },
    }

    result = asyncio.run(
        execute_s5_a0_controller(
            paths=paths,
            episodes=episodes,
            git_commit="deadbeef",
            **_dependencies(trace, runner_outcome=native_result),
        )
    )

    assert result["status"] == "incomplete_non_mergeable"
    assert result["failure_stage"] == "native_execution"
    assert result["error_class"] == "httpx.ConnectError"
    assert result["scientific_pass_authorized"] is False


def test_raw_unqualified_identity_cannot_substitute_for_qualification(
    tmp_path: Path,
) -> None:
    paths, episodes = _chain(tmp_path)
    paths = S5A0ControllerPaths(
        **{
            **paths.__dict__,
            "production_identity_qualification": paths.production_identity,
        }
    )
    trace: list[str] = []

    with pytest.raises(S5A0ControllerError, match="qualification"):
        asyncio.run(
            execute_s5_a0_controller(
                paths=paths,
                episodes=episodes,
                git_commit="deadbeef",
                **_dependencies(trace),
            )
        )

    assert trace == []
    assert not paths.consumption.exists()
