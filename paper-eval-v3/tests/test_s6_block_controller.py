"""Offline RED/GREEN tests for one authority-consumed S6 block lifecycle."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import paper_eval.s6_block_postprocess as block_postprocess_module
import paper_eval.s6_calibration_contract as calibration_module
import paper_eval.s6_live_authority as authority_module
import paper_eval.s6_mstar_grid as mstar_module
import paper_eval.s6_pstar_grid as pstar_module
from paper_eval.artifacts import sha256_file
from paper_eval.s5_mstar_pipeline import MStarSource
from paper_eval.s5_native_method_adapters import S5EpisodeRef
from paper_eval.s6_block_controller import (
    S6BlockControllerError,
    S6BlockControllerPaths,
    S6BlockRuntime,
    execute_s6_block_controller,
    inspect_s6_block_controller,
)
from paper_eval.s6_calibration_contract import (
    DEVELOPMENT_HISTORIES_PAYLOAD_SHA256,
    build_s6_matrix,
    finalize_s6_matrix_freeze,
)
from paper_eval.s6_live_authority import (
    build_s6_live_authority,
    evaluate_s6_live_preflight,
    finalize_s6_live_authority,
    finalize_s6_live_preflight,
)
from paper_eval.s6_pstar_grid import S6TreatmentFailure


GIT = "a" * 40


class StepClock:
    def __init__(self) -> None:
        self.value = 100

    def __call__(self) -> int:
        self.value += 1
        return self.value


def _matrix() -> dict[str, object]:
    return build_s6_matrix(
        input_bindings={
            "s6_development_histories_payload_sha256": (
                DEVELOPMENT_HISTORIES_PAYLOAD_SHA256
            ),
            "parent_protocol_sha256": "1" * 64,
            "s5_pstar_result_file_sha256": "2" * 64,
            "s5_pstar_result_payload_sha256": "3" * 64,
            "s5_mstar_result_file_sha256": "4" * 64,
            "s5_mstar_result_payload_sha256": "5" * 64,
        }
    )


def _p_sources(count: int = 3) -> tuple[S5EpisodeRef, ...]:
    return tuple(
        S5EpisodeRef(index, f"{index + 1:064x}", {"source": index})
        for index in range(count)
    )


def _m_sources(count: int = 3) -> tuple[MStarSource, ...]:
    return tuple(
        MStarSource(index, f"{index + 1:064x}", {"source": index}, 1_000 + index)
        for index in range(count)
    )


def _source_hashes(sources) -> tuple[str, ...]:
    return tuple(str(item.source_sha256) for item in sources)


def _source_closure(method: str) -> dict[str, str]:
    controller_source = Path(__import__("paper_eval.s6_block_controller", fromlist=["x"]).__file__)
    method_source = Path(pstar_module.__file__ if method == "P*" else mstar_module.__file__)
    return {
        "authority": sha256_file(Path(authority_module.__file__)),
        "calibration_contract": sha256_file(Path(calibration_module.__file__)),
        "block_controller": sha256_file(controller_source),
        "method_runner": sha256_file(method_source),
        "block_postprocess": sha256_file(Path(block_postprocess_module.__file__)),
        "production_runtime": sha256_file(
            Path(__file__).parents[1] / "src/paper_eval/s6_production.py"
        ),
        "authority_test": sha256_file(
            Path(__file__).with_name("test_s6_live_authority.py")
        ),
    }


def _chain(
    tmp_path: Path, *, cell_index: int
) -> tuple[S6BlockControllerPaths, tuple[object, ...], dict[str, object]]:
    matrix = _matrix()
    cell = matrix["cells"][cell_index]
    method = str(cell["method"])
    sources: tuple[object, ...] = (
        _p_sources() if method == "P*" else _m_sources()
    )
    matrix_path = tmp_path / "S6_MATRIX_FREEZE.json"
    matrix_freeze = finalize_s6_matrix_freeze(
        output_path=matrix_path,
        matrix=matrix,
        git_commit=GIT,
    )["artifact"]
    observations = {
        "construction": {
            "status": "PASS",
            "served_model_id": "qwen3-32b-fp8",
            "vllm_version": "0.26.0",
            "max_model_len": 65536,
        },
        "embedding": {
            "status": "PASS",
            "served_model_id": "qwen3-embedding-0.6b",
        },
        "neo4j_connectivity": True,
        "namespace": cell["namespace"],
        "namespace_state": {"node_count": 0, "relationship_count": 0},
    }
    evaluation = evaluate_s6_live_preflight(
        matrix_freeze=matrix_freeze,
        matrix_file_sha256=sha256_file(matrix_path),
        cell_index=cell_index,
        episode_source_sha256s=_source_hashes(sources),
        execution_identity_sha256="b" * 64,
        observations=observations,
    )
    preflight_path = tmp_path / "preflights" / f"{cell['run_id']}.json"
    preflight = finalize_s6_live_preflight(
        output_path=preflight_path,
        evaluation=evaluation,
        git_commit=GIT,
    )
    draft = build_s6_live_authority(
        matrix_freeze=matrix_freeze,
        matrix_file_sha256=sha256_file(matrix_path),
        cell_index=cell_index,
        episode_source_sha256s=_source_hashes(sources),
        preflight=preflight,
        preflight_file_sha256=sha256_file(preflight_path),
        execution_identity_sha256="b" * 64,
        source_sha256=_source_closure(method),
    )
    authority_path = tmp_path / "authorities" / f"{cell['run_id']}.json"
    authority = finalize_s6_live_authority(
        output_path=authority_path,
        authority=draft["payload"],
        git_commit=GIT,
    )
    run_root = tmp_path / "runs" / str(cell["run_id"])
    paths = S6BlockControllerPaths(
        matrix_freeze=matrix_path,
        preflight=preflight_path,
        authority=authority_path,
        consumption=run_root / "authority_consumption.json",
        controller_root=run_root / "controller",
        attempt_root=run_root / "attempt",
    )
    return paths, sources, authority


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _zero_work_volume() -> dict[str, int | None]:
    return {
        "llm_call_count": 0,
        "llm_prompt_tokens": 0,
        "llm_completion_tokens": 0,
        "embedding_call_count": 0,
        "embedding_input_count": 0,
        "db_query_count": None,
        "db_transaction_count": None,
        "db_write_count": None,
    }


@pytest.mark.asyncio
async def test_authority_is_consumed_before_runtime_and_success_is_checkpointed(
    tmp_path: Path,
) -> None:
    paths, sources, _authority = _chain(tmp_path, cell_index=0)
    calls: list[str] = []

    async def native_add_episode(_episode: object) -> None:
        calls.append("native")

    async def close() -> None:
        calls.append("close")

    def work_volume_snapshot() -> dict[str, int | None]:
        calls.append("snapshot")
        return {
            "llm_call_count": 3,
            "llm_prompt_tokens": 300,
            "llm_completion_tokens": 30,
            "embedding_call_count": 2,
            "embedding_input_count": 5,
            "db_query_count": 7,
            "db_transaction_count": None,
            "db_write_count": 4,
        }

    def runtime_factory(_cell: dict[str, object]) -> S6BlockRuntime:
        assert paths.consumption.is_file()
        calls.append("runtime")
        return S6BlockRuntime(
            native_add_episode=native_add_episode,
            work_volume_snapshot=work_volume_snapshot,
            close=close,
        )

    result = await execute_s6_block_controller(
        paths=paths,
        sources=sources,
        runtime_factory=runtime_factory,
        git_commit=GIT,
        clock_ns=StepClock(),
    )

    assert result["status"] == "controller_complete_evidence_only"
    assert calls == [
        "runtime",
        "native",
        "native",
        "native",
        "snapshot",
        "close",
    ]
    inspected = inspect_s6_block_controller(paths.controller_root)
    assert inspected["checkpoint"]["status"] == "controller_complete_evidence_only"
    assert (paths.attempt_root / "manifest.json").is_file()
    assert (paths.attempt_root / "events.jsonl").is_file()
    attempt_result = _read(paths.attempt_root / "result.json")
    assert attempt_result["payload"]["status"] == "PASS"
    work_volume = _read(paths.attempt_root / "work_volume.json")
    assert work_volume["payload"]["llm_call_count"] == 3


@pytest.mark.asyncio
async def test_source_manifest_drift_fails_before_consumption_or_runtime(
    tmp_path: Path,
) -> None:
    paths, sources, _authority = _chain(tmp_path, cell_index=0)
    drifted = list(sources)
    drifted[-1] = S5EpisodeRef(2, "f" * 64, {"source": 2})
    called = False

    def runtime_factory(_cell: dict[str, object]) -> S6BlockRuntime:
        nonlocal called
        called = True
        raise AssertionError("not reached")

    with pytest.raises(S6BlockControllerError, match="source_manifest_binding_invalid"):
        await execute_s6_block_controller(
            paths=paths,
            sources=tuple(drifted),
            runtime_factory=runtime_factory,
            git_commit=GIT,
            clock_ns=StepClock(),
        )

    assert called is False
    assert not paths.consumption.exists()
    assert not paths.controller_root.exists()
    assert not paths.attempt_root.exists()


@pytest.mark.asyncio
async def test_provider_disconnect_marks_only_active_block_incomplete(
    tmp_path: Path,
) -> None:
    paths, sources, _authority = _chain(tmp_path, cell_index=0)

    async def disconnected(_episode: object) -> None:
        raise ConnectionError("private vllm endpoint")

    runtime = S6BlockRuntime(native_add_episode=disconnected)
    with pytest.raises(S6BlockControllerError, match="block_incomplete_non_mergeable"):
        await execute_s6_block_controller(
            paths=paths,
            sources=sources,
            runtime_factory=lambda _cell: runtime,
            git_commit=GIT,
            clock_ns=StepClock(),
        )

    assert paths.consumption.is_file()
    checkpoint = _read(paths.controller_root / "checkpoint.json")
    assert checkpoint["status"] == "incomplete_non_mergeable"
    assert checkpoint["failure_stage"] == "method_execution"
    assert checkpoint["error_class"] == "builtins.ConnectionError"
    assert not (paths.attempt_root / "result.json").exists()
    assert "private vllm" not in repr(checkpoint)


@pytest.mark.asyncio
async def test_explicit_pstar_treatment_failure_is_complete_scientific_evidence(
    tmp_path: Path,
) -> None:
    paths, sources, _authority = _chain(tmp_path, cell_index=0)

    async def treatment(episode: object) -> None:
        if int(episode["source"]) == 0:
            raise S6TreatmentFailure()

    result = await execute_s6_block_controller(
        paths=paths,
        sources=sources,
        runtime_factory=lambda _cell: S6BlockRuntime(
            native_add_episode=treatment,
            work_volume_snapshot=_zero_work_volume,
        ),
        git_commit=GIT,
        clock_ns=StepClock(),
    )

    assert result["status"] == "controller_complete_evidence_only"
    attempt = _read(paths.attempt_root / "result.json")
    assert attempt["payload"]["status"] == "SCIENTIFIC_OUTCOME_COMPLETE"
    assert attempt["payload"]["mergeable"] is True


@pytest.mark.asyncio
async def test_mstar_c1_uses_same_consumed_controller_lifecycle(tmp_path: Path) -> None:
    paths, sources, _authority = _chain(tmp_path, cell_index=1)

    async def prepare(source: object, _logical_time_ns: int) -> object:
        return source

    async def bind(*_args: object) -> None:
        return None

    runtime = S6BlockRuntime(
        semantic_prepare=prepare,
        latest_state_bind=bind,
        production_core_identity_sha256="c" * 64,
        work_volume_snapshot=_zero_work_volume,
    )
    result = await execute_s6_block_controller(
        paths=paths,
        sources=sources,
        runtime_factory=lambda _cell: runtime,
        git_commit=GIT,
        clock_ns=StepClock(),
    )

    assert result["method"] == "M*"
    assert result["runner_status"] == "PASS"
    assert paths.consumption.is_file()
