"""TDD for fixed-three S4 qualification scheduling and wiring."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.s4_candidate_projection import PROJECTION_SCHEMA_SHA256
from paper_eval.s4_sidecar_qualification_controller import (
    QualificationPhaseDependencies,
    compose_qualification_phase_specs,
    execute_qualification_phase,
    orchestrate_qualification_block,
    qualification_cache_paths,
    qualification_sidecar_config,
    select_next_qualification_block,
)


@dataclass(frozen=True)
class Episode:
    source_sequence: int
    source_hash: str
    name: str
    body: str


def _block(history_id: str, count: int) -> dict:
    cache_id = f"s4q-d0-{history_id}-001"
    return {
        "block_index": {"b6019101": 0, "6071bd76": 1, "a2f3aa27": 2}[
            history_id
        ],
        "history": {
            "history_id": history_id,
            "episode_count": count,
            "episode_manifest_sha256": "a" * 64,
            "data_role": "DEVELOPMENT_EXPOSED",
        },
        "plan_block": {
            "history_id": history_id,
            "cache_id": cache_id,
            "mode": "NEW_CAPTURE_REPLAY_BLOCK",
            "live_execution": False,
            "runs": {
                "U0_CAPTURE": {
                    "cache_id": cache_id,
                    "method": "U0",
                    "mode": "capture",
                    "namespace": f"pev3-s4-u0-qual-{history_id}-001",
                    "run_id": f"s4q-u0-{history_id}-001",
                },
                "D0_READ_ONLY_REPLAY": {
                    "cache_id": cache_id,
                    "method": "D0",
                    "mode": "replay",
                    "namespace": f"pev3-s4-d0-qual-{history_id}-001",
                    "run_id": f"s4q-d0-{history_id}-001",
                },
            },
        },
        "private_cache": {
            "prompt_relpath": f"runtime/private/{cache_id}/prompt.jsonl",
            "embedding_relpath": f"runtime/private/{cache_id}/embedding.jsonl",
            "candidate_sidecar_relpath": (
                f"runtime/private/{cache_id}/candidate-sidecar.jsonl"
            ),
            "reportable_contents": False,
        },
    }


def _authority() -> dict:
    return {"payload": {"blocks": [_block("b6019101", 49), _block("6071bd76", 46), _block("a2f3aa27", 44)]}}


def _episodes(count: int) -> list[Episode]:
    return [
        Episode(
            index,
            hashlib.sha256(f"source-{index}".encode()).hexdigest(),
            f"episode-{index}",
            f"body-{index}",
        )
        for index in range(count)
    ]


def test_compose_specs_preserves_frozen_run_identity_and_dynamic_count() -> None:
    block = _block("6071bd76", 46)
    capture, replay = compose_qualification_phase_specs(block)

    assert capture == {
        "phase": "U0_CAPTURE",
        "history_id": "6071bd76",
        **block["plan_block"]["runs"]["U0_CAPTURE"],
    }
    assert replay["phase"] == "D0_READ_ONLY_REPLAY"
    assert replay["history_id"] == "6071bd76"


def test_sidecar_config_is_history_parameterized_and_path_confined(
    tmp_path: Path,
) -> None:
    block = _block("a2f3aa27", 44)
    episodes = _episodes(44)
    block["history"]["episode_manifest_sha256"] = __import__(
        "paper_eval.s4_sidecar_qualification_data",
        fromlist=["build_s4_qualification_episode_manifest"],
    ).build_s4_qualification_episode_manifest(episodes)[1]
    spec = compose_qualification_phase_specs(block)[0]

    config = qualification_sidecar_config(
        block=block,
        spec=spec,
        episodes=episodes,
        edge_operations_module=SimpleNamespace(resolve_extracted_edge=lambda: None),
        project_root=tmp_path,
    )

    assert config.identity["attempt_id"] == "s4q-a2f3aa27-001"
    assert config.identity["history_id"] == "a2f3aa27"
    assert config.identity["projection_schema_sha256"] == PROJECTION_SCHEMA_SHA256
    assert config.path == (
        tmp_path
        / "runtime/private/s4q-d0-a2f3aa27-001/candidate-sidecar.jsonl"
    )

    block["private_cache"]["candidate_sidecar_relpath"] = "../escaped.jsonl"
    with pytest.raises(ValueError, match="escaped"):
        qualification_sidecar_config(
            block=block,
            spec=spec,
            episodes=episodes,
            edge_operations_module=SimpleNamespace(resolve_extracted_edge=lambda: None),
            project_root=tmp_path,
        )


def test_scheduler_requires_each_prior_strict_pass() -> None:
    authority = _authority()

    first = select_next_qualification_block(authority, {})
    assert first["history"]["history_id"] == "b6019101"

    with pytest.raises(ValueError, match="strict PASS"):
        select_next_qualification_block(
            authority,
            {"b6019101": {"payload": {"verdict": "FAIL"}}},
        )

    second = select_next_qualification_block(
        authority,
        {
            "b6019101": {
                "payload": {
                    "history_id": "b6019101",
                    "block_index": 0,
                    "verdict": "PASS",
                    "next_block_authorized": True,
                    "s5_authorized": False,
                }
            }
        },
    )
    assert second["history"]["history_id"] == "6071bd76"


def test_orchestrator_forbids_replay_until_capture_pass() -> None:
    block = _block("b6019101", 49)
    calls: list[str] = []

    async def execute(spec: dict) -> dict:
        calls.append(spec["phase"])
        return {"payload": {"status": "INCOMPLETE"}}

    with pytest.raises(RuntimeError, match="capture did not PASS"):
        asyncio.run(
            orchestrate_qualification_block(
                block=block,
                execute_phase=execute,
                evaluate=lambda **_kwargs: {},
            )
        )
    assert calls == ["U0_CAPTURE"]


def test_execute_phase_reuses_runtime_and_runner_with_dynamic_count(
    tmp_path: Path,
) -> None:
    block = _block("6071bd76", 46)
    episodes = _episodes(46)
    manifest = __import__(
        "paper_eval.s4_sidecar_qualification_data",
        fromlist=["build_s4_qualification_episode_manifest"],
    ).build_s4_qualification_episode_manifest(episodes)[1]
    block["history"]["episode_manifest_sha256"] = manifest
    spec = compose_qualification_phase_specs(block)[0]
    calls: dict[str, object] = {}

    class Runtime:
        graph = object()
        episode_kwargs = staticmethod(lambda episode: {"episode": episode})
        namespace_probe = staticmethod(lambda: {})
        graph_exporter = staticmethod(lambda *_args: {})
        runtime_evidence = staticmethod(lambda: {})
        cache_evidence = staticmethod(lambda: {})
        cleanup_namespace = staticmethod(lambda _namespace: None)
        phase_context = staticmethod(lambda: None)
        restore_sidecar_prefix = staticmethod(lambda _prefix: None)
        pre_finalize_sidecar = staticmethod(lambda _evidence: None)

    def build_runtime(**kwargs):
        calls["build_runtime"] = kwargs
        return Runtime()

    async def run_phase(**kwargs):
        calls["run_phase"] = kwargs
        return {"payload": {"status": "PASS"}}

    async def ensure_ready(graph):
        calls["ready_graph"] = graph

    result = asyncio.run(
        execute_qualification_phase(
            block=block,
            spec=spec,
            episodes=episodes,
            project_root=tmp_path,
            artifact_root=tmp_path / "artifacts",
            git_commit="deadbeef",
            dependencies=QualificationPhaseDependencies(
                build_runtime=build_runtime,
                run_phase=run_phase,
                ensure_runtime_ready=ensure_ready,
                edge_operations_module=SimpleNamespace(
                    resolve_extracted_edge=lambda: None
                ),
            ),
        )
    )

    assert result == {"payload": {"status": "PASS"}}
    assert calls["ready_graph"] is Runtime.graph
    assert calls["run_phase"]["expected_episode_count"] == 46
    assert calls["run_phase"]["restore_prefix"] is Runtime.restore_sidecar_prefix
    assert qualification_cache_paths(block, project_root=tmp_path).prompt == (
        tmp_path / "runtime/private/s4q-d0-6071bd76-001/prompt.jsonl"
    )
