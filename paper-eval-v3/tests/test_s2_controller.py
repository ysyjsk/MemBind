from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import (
    atomic_write_json,
    finalize_envelope,
    payload_sha256,
    sha256_file,
)
from paper_eval.s2_controller import (
    DEFAULT_S0_CURRENT_STATE,
    S2ControllerDependencies,
    S2ControllerError,
    execute_s2_controller,
)


def _qualification(
    path: Path,
    *,
    verdict: str = "PASS",
    protocol_version: str = "membind.paper-eval-v3.s2-u0-qualification.v1",
    payload_overrides: dict[str, object] | None = None,
) -> None:
    payload = {
        "stage": "S2",
        "method": "U0",
        "verdict": verdict,
        "authorization": (
            "AUTHORIZE_S2_U0_1_HISTORY" if verdict == "PASS" else "BLOCK_S2_U0"
        ),
        "history_id": "07741c45",
        "namespace": "pev3-s1-20260814-001",
        "s1_run_id": "s1-20260814-001",
        "s1_artifact_sha256": "a" * 64,
        "s1_checkpoint_sha256": "b" * 64,
        "s1_events_sha256": "c" * 64,
        "dataset_parity_sha256": "d" * 64,
        "runtime_identity_sha256": "e" * 64,
        "s0_current_state_sha256": sha256_file(DEFAULT_S0_CURRENT_STATE),
        "direct_add_episode_contract_sha256": "f" * 64,
        "episode_count": 49,
        "qualification_scope": "one_history_u0_only",
        "failure_reasons": [],
        "checks": {
            "coverage_49_of_49": True,
            "s1_hashes_bound": True,
            "s1_preflight_pass": True,
            "dataset_parity_pass": True,
            "evaluator_parity_pass": True,
            "runtime_identity_current": True,
            "direct_add_episode_contract_bound": True,
        },
    }
    payload.update(payload_overrides or {})
    atomic_write_json(
        path,
        finalize_envelope(
            payload=payload,
            protocol_version=protocol_version,
            git_commit="deadbeef",
            run_id="s2-qual-test",
        ),
    )


def _instance() -> dict:
    return {
        "question_id": "07741c45",
        "question_type": "knowledge-update",
        "question": "raw question",
        "question_date": "2024/03/01 (Fri) 12:00",
        "answer": "raw answer",
        "answer_session_ids": ["gold-session"],
    }


@pytest.mark.asyncio
async def test_controller_rejects_qualification_before_any_live_factory(tmp_path: Path) -> None:
    qualification = tmp_path / "qualification.json"
    _qualification(qualification, verdict="FAIL")
    calls: list[str] = []
    deps = S2ControllerDependencies(
        load_history=lambda: _instance(),
        build_episodes=lambda _instance: [],
        build_runtime=lambda: calls.append("runtime"),
        ensure_runtime_ready=lambda _runtime: None,
        build_reader=lambda: calls.append("reader"),
        build_judge=lambda: calls.append("judge"),
        project_adapter_identity=lambda **_kwargs: {},
        run_durable=lambda **_kwargs: None,
    )

    with pytest.raises(S2ControllerError, match="qualification"):
        await execute_s2_controller(
            run_id="s2-live-test",
            qualification_path=qualification,
            artifact_root=tmp_path / "runs",
            final_output=tmp_path / "result.json",
            adapter_identity_output=tmp_path / "adapter.json",
            dependencies=deps,
            git_commit="deadbeef",
        )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("protocol_version", "payload_overrides"),
    [
        ("paper-eval-v3", {}),
        (
            "membind.paper-eval-v3.s2-u0-qualification.v1",
            {"qualification_scope": "four_history_u0"},
        ),
        (
            "membind.paper-eval-v3.s2-u0-qualification.v1",
            {"episode_count": 48},
        ),
    ],
)
async def test_controller_rejects_qualification_contract_drift_before_factories(
    tmp_path: Path,
    protocol_version: str,
    payload_overrides: dict[str, object],
) -> None:
    qualification = tmp_path / "qualification.json"
    _qualification(
        qualification,
        protocol_version=protocol_version,
        payload_overrides=payload_overrides,
    )
    calls: list[str] = []
    deps = S2ControllerDependencies(
        load_history=lambda: calls.append("history") or _instance(),
        build_episodes=lambda _instance: [],
        build_runtime=lambda: calls.append("runtime"),
        ensure_runtime_ready=lambda _runtime: None,
        build_reader=lambda: calls.append("reader"),
        build_judge=lambda: calls.append("judge"),
        project_adapter_identity=lambda **_kwargs: {},
        run_durable=lambda **_kwargs: None,
    )

    with pytest.raises(S2ControllerError, match="qualification"):
        await execute_s2_controller(
            run_id="s2-live-test",
            qualification_path=qualification,
            artifact_root=tmp_path / "runs",
            final_output=tmp_path / "result.json",
            adapter_identity_output=tmp_path / "adapter.json",
            dependencies=deps,
            git_commit="deadbeef",
        )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload_overrides",
    [
        {"s0_current_state_sha256": "0" * 64},
        {"s0_current_state_sha256": None},
    ],
)
async def test_controller_rejects_qualification_not_bound_to_current_s0(
    tmp_path: Path, payload_overrides: dict[str, object]
) -> None:
    qualification = tmp_path / "qualification.json"
    _qualification(qualification, payload_overrides=payload_overrides)
    calls: list[str] = []
    deps = S2ControllerDependencies(
        load_history=lambda: calls.append("history") or _instance(),
        build_episodes=lambda _instance: [],
        build_runtime=lambda: calls.append("runtime"),
        ensure_runtime_ready=lambda _runtime: None,
        build_reader=lambda: calls.append("reader"),
        build_judge=lambda: calls.append("judge"),
        project_adapter_identity=lambda **_kwargs: {},
        run_durable=lambda **_kwargs: None,
    )

    with pytest.raises(S2ControllerError, match="qualification"):
        await execute_s2_controller(
            run_id="s2-live-test",
            qualification_path=qualification,
            artifact_root=tmp_path / "runs",
            final_output=tmp_path / "result.json",
            adapter_identity_output=tmp_path / "adapter.json",
            dependencies=deps,
            git_commit="deadbeef",
        )
    assert calls == []


@pytest.mark.asyncio
async def test_controller_rejects_started_run_before_live_factories(tmp_path: Path) -> None:
    qualification = tmp_path / "qualification.json"
    _qualification(qualification)
    marker = tmp_path / "runs" / "s2-live-test" / ".started"
    marker.parent.mkdir(parents=True)
    marker.write_text("one-chain-only\n", encoding="utf-8")
    calls: list[str] = []
    deps = S2ControllerDependencies(
        load_history=lambda: calls.append("history") or _instance(),
        build_episodes=lambda _instance: [],
        build_runtime=lambda: calls.append("runtime"),
        ensure_runtime_ready=lambda _runtime: None,
        build_reader=lambda: calls.append("reader"),
        build_judge=lambda: calls.append("judge"),
        project_adapter_identity=lambda **_kwargs: {},
        run_durable=lambda **_kwargs: None,
    )

    with pytest.raises(S2ControllerError, match="already started"):
        await execute_s2_controller(
            run_id="s2-live-test",
            qualification_path=qualification,
            artifact_root=tmp_path / "runs",
            final_output=tmp_path / "result.json",
            adapter_identity_output=tmp_path / "adapter.json",
            dependencies=deps,
            git_commit="deadbeef",
        )
    assert calls == []


@pytest.mark.asyncio
async def test_controller_consumes_completed_s1_namespace_without_construction(
    tmp_path: Path,
) -> None:
    qualification = tmp_path / "qualification.json"
    _qualification(qualification)
    graph = SimpleNamespace()
    runtime = SimpleNamespace(graphiti=graph)
    transport = SimpleNamespace(aclose=lambda: None)
    reader = SimpleNamespace()
    judge = SimpleNamespace(aclose=lambda: None)
    captured: dict = {}

    async def ready(value):
        assert value is runtime

    async def run_durable(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            payload={"status": "PASS", "evidence_recall_at_10": 1.0, "qa_accuracy": 1.0}
        )

    identity = {
        "schema_version": "membind.paper-eval-v3.s2-adapter-identity.v1",
        "reader": {"config_sha256": "1" * 64},
    }
    identity["identity_sha256"] = payload_sha256(identity)
    deps = S2ControllerDependencies(
        load_history=_instance,
        build_episodes=lambda _instance: [SimpleNamespace(name=f"e{i}") for i in range(49)],
        build_runtime=lambda: runtime,
        ensure_runtime_ready=ready,
        build_reader=lambda: (reader, transport),
        build_judge=lambda: judge,
        project_adapter_identity=lambda **_kwargs: identity,
        run_durable=run_durable,
    )

    result = await execute_s2_controller(
        run_id="s2-live-test",
        qualification_path=qualification,
        artifact_root=tmp_path / "runs",
        final_output=tmp_path / "result.json",
        adapter_identity_output=tmp_path / "adapter.json",
        dependencies=deps,
        git_commit="deadbeef",
    )

    assert result.payload["status"] == "PASS"
    assert captured["graph"] is graph
    assert len(captured["episodes"]) == 49
    assert captured["run"].inputs.namespace == "pev3-s1-20260814-001"
    assert captured["run"].inputs.history_id == "07741c45"
    assert captured["qualification_evidence_sha256"]
    assert captured["adapter_identity_sha256"] == sha256_file(
        tmp_path / "adapter.json"
    )
    persisted = json.loads((tmp_path / "adapter.json").read_text())
    assert persisted["payload"]["identity_sha256"] == identity["identity_sha256"]
    assert persisted["payload"]["qualification_sha256"]
    assert persisted["payload"]["execution_source_sha256"]
    assert persisted["payload"]["execution_policy"]["retrieval_surface"] == (
        "graphiti_basic_edge"
    )
    assert persisted["payload"]["execution_policy"]["retrieval_unit"] == (
        "EntityEdge"
    )
    assert persisted["payload"]["execution_policy"]["top_k_unit"] == "edge"
    assert persisted["payload"]["execution_policy"][
        "official_longmemeval_session_metric"
    ] is False
    assert {
        "graphiti.search.search",
        "graphiti.search.search_config",
        "graphiti.search.search_config_recipes",
        "graphiti.search.search_utils",
    }.issubset(persisted["payload"]["execution_source_sha256"])
    assert all(
        len(value) == 64
        for value in persisted["payload"]["execution_source_sha256"].values()
    )
    assert "raw question" not in (tmp_path / "adapter.json").read_text()
    assert "raw answer" not in (tmp_path / "adapter.json").read_text()


@pytest.mark.asyncio
async def test_controller_cleanup_failure_cannot_return_pass(tmp_path: Path) -> None:
    qualification = tmp_path / "qualification.json"
    _qualification(qualification)
    graph = SimpleNamespace()
    runtime = SimpleNamespace(graphiti=graph)

    class _BadTransport:
        async def aclose(self):
            raise RuntimeError("private transport detail")

    reader = SimpleNamespace()
    judge = SimpleNamespace(aclose=lambda: None)
    identity = {
        "schema_version": "membind.paper-eval-v3.s2-adapter-identity.v1",
        "reader": {"config_sha256": "1" * 64},
    }
    identity["identity_sha256"] = payload_sha256(identity)
    deps = S2ControllerDependencies(
        load_history=_instance,
        build_episodes=lambda _instance: [SimpleNamespace(source_sequence=i) for i in range(49)],
        build_runtime=lambda: runtime,
        ensure_runtime_ready=lambda _runtime: None,
        build_reader=lambda: (reader, _BadTransport()),
        build_judge=lambda: judge,
        project_adapter_identity=lambda **_kwargs: identity,
        run_durable=lambda **_kwargs: SimpleNamespace(
            payload={"status": "PASS", "evidence_recall_at_10": 1.0, "qa_accuracy": 1.0}
        ),
    )

    with pytest.raises(S2ControllerError, match="cleanup"):
        await execute_s2_controller(
            run_id="s2-cleanup-test",
            qualification_path=qualification,
            artifact_root=tmp_path / "runs",
            final_output=tmp_path / "result.json",
            adapter_identity_output=tmp_path / "adapter.json",
            dependencies=deps,
            git_commit="deadbeef",
        )
    cleanup = json.loads(
        (tmp_path / "runs" / "s2-cleanup-test" / "cleanup_status.json").read_text()
    )
    assert cleanup["payload"]["status"] == "WARNING"
    assert cleanup["payload"]["result_usable"] is False


@pytest.mark.asyncio
async def test_controller_never_overwrites_existing_adapter_sidecar(tmp_path: Path) -> None:
    qualification = tmp_path / "qualification.json"
    _qualification(qualification)
    adapter = tmp_path / "adapter.json"
    adapter.write_text("old sidecar\n", encoding="utf-8")
    calls: list[str] = []
    deps = S2ControllerDependencies(
        load_history=lambda: calls.append("history") or _instance(),
        build_episodes=lambda _instance: [],
        build_runtime=lambda: calls.append("runtime"),
        ensure_runtime_ready=lambda _runtime: None,
        build_reader=lambda: calls.append("reader"),
        build_judge=lambda: calls.append("judge"),
        project_adapter_identity=lambda **_kwargs: {},
        run_durable=lambda **_kwargs: None,
    )

    with pytest.raises(S2ControllerError, match="sidecar"):
        await execute_s2_controller(
            run_id="s2-sidecar-test",
            qualification_path=qualification,
            artifact_root=tmp_path / "runs",
            final_output=tmp_path / "result.json",
            adapter_identity_output=adapter,
            dependencies=deps,
            git_commit="deadbeef",
        )
    assert adapter.read_text(encoding="utf-8") == "old sidecar\n"
    assert calls == []


def test_s2_tmux_launcher_rejects_path_traversal_and_has_no_secret_surface(
    tmp_path: Path,
) -> None:
    script = Path(__file__).parents[1] / "scripts/run_s2_live_tmux.sh"
    result = __import__("subprocess").run(
        ["bash", str(script), "../escape"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    source = script.read_text(encoding="utf-8")
    assert "source .env" not in source
    assert "API_KEY" not in source
    assert "paper_eval.s2_controller" in source
    assert "membind-validation/.venv/bin/python" in source
