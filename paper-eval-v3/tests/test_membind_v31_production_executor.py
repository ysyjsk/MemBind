"""TDD for the production executor and real source-bound three-episode smoke."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31 import (
    CertificationRecord,
    DependencyClass,
    EffectClass,
    OperatorContract,
    StateCutCertification,
)
from paper_eval.membind_v31.live_block import V31LiveHooks
from paper_eval.membind_v31.orchestration import SmokeSpec
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v31.production_executor import (
    ProductionExecutorDependencies,
    ProductionExecutorError,
    ProductionExecutorPaths,
    _default_episode_builder,
    _default_control_plan,
    build_production_executor_hooks,
    build_source_bound_smoke_plan,
    execute_v31_three_episode_smoke,
    load_development_episodes,
)


HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")


def test_default_episode_builder_registers_dynamic_module_before_dataclass_exec(
    tmp_path: Path,
) -> None:
    """Python 3.12 dataclasses require the dynamically loaded module identity."""

    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "dataset.py").write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "from dataclasses import dataclass\n"
        "SEEN_REGISTERED = sys.modules.get(__name__) is not None\n"
        "@dataclass(frozen=True)\n"
        "class Episode:\n"
        "    value: str\n"
        "def build_episodes(record):\n"
        "    if not SEEN_REGISTERED:\n"
        "        raise RuntimeError('dynamic module was not registered')\n"
        "    return (Episode(record['value']),)\n",
        encoding="utf-8",
    )

    builder = _default_episode_builder(tmp_path)

    episodes = builder({"value": "ok"})
    assert len(episodes) == 1
    assert episodes[0].value == "ok"
    assert "membind_v31_production_dataset" not in sys.modules


def test_default_episode_builder_restores_existing_module_after_import_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "src"
    source_root.mkdir()
    (source_root / "dataset.py").write_text(
        "raise RuntimeError('fixture import failure')\n",
        encoding="utf-8",
    )
    alias = "membind_v31_production_dataset"
    sentinel = SimpleNamespace(identity="preexisting")
    monkeypatch.setitem(sys.modules, alias, sentinel)

    with pytest.raises(ProductionExecutorError, match="episode renderer unavailable"):
        _default_episode_builder(tmp_path)

    assert sys.modules[alias] is sentinel


def test_default_control_plan_accepts_source_bound_plan_before_baseline_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Path(__file__).resolve().parents[1] / "artifacts/paper_eval/membind_v31/V31_METHOD_PLAN.json"
    (tmp_path / "V31_METHOD_PLAN.json").write_text(source.read_text(), encoding="utf-8")
    monkeypatch.setattr(
        "paper_eval.membind_v31.production_executor.inspect_materialized_control",
        lambda _path: (_ for _ in ()).throw(ValueError("baseline acceptance invalid")),
    )

    plan = _default_control_plan(tmp_path)

    assert plan["authorization_scope"] == "LIVE_EXECUTION_AUTHORIZED_BASELINE_MERGE_PENDING"


@dataclass(frozen=True)
class _Episode:
    source_sequence: int
    source_hash: str
    reference_time: str = "2026-01-01T00:00:00+00:00"
    body: str = "private source body"
    group_id: str = "original"

    @property
    def name(self) -> str:
        return f"history::episode::{self.source_sequence:04d}"


def _seal(value: dict[str, object]) -> dict[str, object]:
    result = dict(value)
    result["payload_sha256"] = payload_sha256(result)
    return result


def _plan() -> dict[str, object]:
    sources = {
        history: [f"{100 * position + index + 1:064x}" for index in range(4)]
        for position, history in enumerate(HISTORIES)
    }
    blocks = [
        {
            "block_index": index,
            "method": method,
            "history_id": history,
            "policy": policy,
            "source_count": len(sources[history]),
            "namespace": f"formal-{index}-{history}",
            "cache_salt_sha256": f"{1000 + index:064x}",
            "shared_execution_envelope_sha256": "a" * 64,
            "global_llm_admission_k": 2,
            "compile_workers": 2,
            "lookahead": 2,
        }
        for index, (method, history, policy) in enumerate(
            (
                ("MemBind", HISTORIES[0], "FRONTIER_FIRST_CACHE_AFFINITY"),
                ("MemBind", HISTORIES[1], "FRONTIER_FIRST_CACHE_AFFINITY"),
                ("MemBind", HISTORIES[2], "FRONTIER_FIRST_CACHE_AFFINITY"),
                ("MemBind", HISTORIES[3], "FRONTIER_FIRST_CACHE_AFFINITY"),
                ("MemBind-Barrier", HISTORIES[0], "FRONTIER_BARRIER"),
                ("MemBind-FIFO", HISTORIES[0], "FRONTIER_FIRST_FIFO"),
            )
        )
    ]
    return _seal(
        {
            "schema_version": "membind.paper-eval-v3.membind-v31-method-plan.v1",
            "run_id": "membind-v31-dev-test",
            "data_role": "DEVELOPMENT_EXPOSED",
            "histories": list(HISTORIES),
            "history_source_sha256s": sources,
            "arrival_traces": {
                history: {"arrival_offsets_ns": [0, 10, 20, 30]}
                for history in HISTORIES
            },
            "shared_execution_envelope_sha256": "a" * 64,
            "global_llm_admission_k": 2,
            "compile_workers": 2,
            "lookahead": 2,
            "blocks": blocks,
        }
    )


def _development_input(plan: dict[str, object]) -> dict[str, object]:
    records = [
        {"question_id": history, "fixture": history}
        for history in HISTORIES
    ]
    return _seal(
        {
            "schema_version": "development-input-test.v1",
            "data_role": "DEVELOPMENT_EXPOSED",
            "selection_policy": "EXACT_FROZEN_DEVELOPMENT_HISTORIES_ONLY",
            "history_order": list(HISTORIES),
            "episode_counts": {history: 4 for history in HISTORIES},
            "records": records,
        }
    )


def _certification() -> StateCutCertification:
    records = []
    for index, name in enumerate(("graphiti.extract_nodes", "graphiti.extract_edges"), start=1):
        records.append(
            CertificationRecord.create(
                operator_contract=OperatorContract.create(
                    operator_name=name,
                    dependency_class=DependencyClass.EVIDENCE_BOUND,
                    effect_class=EffectClass.PURE,
                ),
                memory_backend_identity_sha256="a" * 64,
                adapter_identity_sha256="b" * 64,
                operator_identity_sha256=f"{index:064x}",
                code_revision_sha256="c" * 64,
                prompt_identity_sha256=f"{index + 10:064x}",
                schema_identity_sha256="d" * 64,
                config_identity_sha256="e" * 64,
                allowed_evidence_inputs=("current_source", "evidence_snapshot"),
                allowed_upstream_outputs=(),
                allowed_apis=("llm.generate_response",),
                forbidden_apis=("memory.search", "memory.write"),
                qualification_trace_sha256=f"{index + 20:064x}",
                persistent_state_read_count=0,
                persistent_state_write_count=0,
                undeclared_external_side_effect_count=0,
                future_evidence_access_count=0,
                undeclared_state_facing_call_count=0,
            )
        )
    return StateCutCertification.create(records)


def _spec(plan: dict[str, object]) -> SmokeSpec:
    return SmokeSpec(
        attempt_id="v31-attempt-dev-test",
        plan_payload_sha256=str(plan["payload_sha256"]),
        control_commit_payload_sha256="f" * 64,
        block_index=0,
        method="MemBind",
        history_id=HISTORIES[0],
        namespace="formal-0-07741c45-smoke-v31-attempt-dev-test",
        source_sequences=(0, 1, 2),
        global_llm_admission_k=2,
    )


def test_development_loader_reads_only_exact_sealed_four_history_inventory(tmp_path: Path) -> None:
    plan = _plan()
    artifact = _development_input(plan)
    path = tmp_path / "LONGMEMEVAL_S_DEVELOPMENT_EXPOSED_4.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")

    def episode_builder(record):
        history = record["question_id"]
        return tuple(
            _Episode(index, plan["history_source_sha256s"][history][index])
            for index in range(4)
        )

    loaded = load_development_episodes(
        development_input=path,
        verified_plan=plan,
        episode_builder=episode_builder,
    )

    assert tuple(loaded) == HISTORIES
    assert all(len(loaded[history]) == 4 for history in HISTORIES)
    artifact["records"].append({"question_id": "heldout", "fixture": "forbidden"})
    artifact["payload_sha256"] = payload_sha256(
        {key: value for key, value in artifact.items() if key != "payload_sha256"}
    )
    path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ProductionExecutorError, match="development history inventory invalid"):
        load_development_episodes(
            development_input=path,
            verified_plan=plan,
            episode_builder=episode_builder,
        )


def test_source_bound_smoke_plan_binds_exact_namespace_and_three_source_prefix() -> None:
    plan = _plan()
    episodes = tuple(
        _Episode(index, plan["history_source_sha256s"][HISTORIES[0]][index])
        for index in range(3)
    )

    smoke = build_source_bound_smoke_plan(
        smoke_spec=_spec(plan), verified_plan=plan, episodes=episodes
    )

    assert smoke["source_count"] == 3
    assert smoke["namespace"] == _spec(plan).namespace
    assert smoke["source_sha256s"] == plan["history_source_sha256s"][HISTORIES[0]][:3]
    assert smoke["arrival_offsets_ns"] == [0, 10, 20]
    assert smoke["formal_namespace_reused"] is False
    assert smoke["parent_plan_payload_sha256"] == plan["payload_sha256"]


def test_source_bound_smoke_accepts_the_frozen_orchestrator_block_zero() -> None:
    plan = _plan()
    block_zero_spec = _spec(plan)
    episodes = tuple(
        _Episode(index, plan["history_source_sha256s"][HISTORIES[0]][index])
        for index in range(3)
    )

    smoke = build_source_bound_smoke_plan(
        smoke_spec=block_zero_spec, verified_plan=plan, episodes=episodes
    )

    assert smoke["parent_block_index"] == 0


class _RequestClient:
    def observation(self):
        return {"configured_limit": 2, "observed_max_inflight": 2}

    @asynccontextmanager
    async def frontier_bind_region(self, _stream, _sequence):
        yield


def test_real_smoke_adapter_uses_source_bound_plan_and_persists_three_artifacts(
    tmp_path: Path,
) -> None:
    plan = _plan()
    certification = _certification()
    episodes = tuple(
        _Episode(index, plan["history_source_sha256s"][HISTORIES[0]][index])
        for index in range(3)
    )
    state: dict[str, object] = {"visible": [], "closed": False}
    runtime = SimpleNamespace(
        graphiti=SimpleNamespace(),
        admitted_llm=_RequestClient(),
        shared_execution_envelope_sha256="a" * 64,
        method_execution_identity_sha256="9" * 64,
    )

    async def namespace_probe(_runtime, _namespace):
        return {
            "node_count": len(state["visible"]),
            "relationship_count": 0,
            "episode_names": list(state["visible"]),
        }

    hooks = V31LiveHooks(
        runtime_builder=lambda **_kwargs: runtime,
        runtime_ready=lambda _runtime: asyncio.sleep(0),
        namespace_probe=namespace_probe,
        namespace_episode=lambda episode, namespace: replace(episode, group_id=namespace),
        source_visibility_probe=lambda _runtime, source: asyncio.sleep(
            0, result=source.episode_projection["name"] in state["visible"]
        ),
        reference_time_to_ns=lambda _value: 1,
        adapter_factory=lambda _runtime, _certification: object(),
        close_runtime=lambda _runtime: asyncio.sleep(0, result=state.update(closed=True)),
    )

    async def coordinator(**kwargs):
        source_log = kwargs["source_log"]
        for sequence in range(3):
            source = source_log.record(sequence)
            kwargs["observer"](
                {"event_type": "arrival", "source_sequence": sequence, "timestamp_ns": sequence * 100}
            )
            kwargs["observer"](
                {"event_type": "compile_start", "source_sequence": sequence, "timestamp_ns": sequence * 100 + 1}
            )
            artifact = PreparedArtifact.create(
                source_sequence=sequence,
                source_sha256=source.source_sha256,
                evidence_sha256=f"{500 + sequence:064x}",
                certification_sha256=certification.certification_sha256,
                raw_nodes=[],
                raw_edges=[],
                pure_intermediates={"node_episode_index_map": {}},
            )
            kwargs["prepared_persistor"](artifact)
            kwargs["observer"](
                {"event_type": "prepared_durable", "source_sequence": sequence, "timestamp_ns": sequence * 100 + 2}
            )
            kwargs["observer"](
                {"event_type": "bind_start", "source_sequence": sequence, "timestamp_ns": sequence * 100 + 3}
            )
            state["visible"].append(source.episode_projection["name"])
            kwargs["commit_observer"](sequence, {})
            assert await kwargs["publication_probe"](sequence, {}) is True
            kwargs["publication_persistor"](sequence, {})
        return {
            "status": "PASS",
            "publication_source_sequences": [0, 1, 2],
            "direct_violation_count": 0,
        }

    root = tmp_path / "smoke"
    root.mkdir()
    result = asyncio.run(
        execute_v31_three_episode_smoke(
            smoke_spec=_spec(plan),
            verified_plan=plan,
            episodes=episodes,
            env={"SAFE": "value"},
            smoke_root=root,
            state_cut_certification=certification,
            hooks=hooks,
            coordinator=coordinator,
        )
    )

    assert result["status"] == "PASS"
    assert result["source_count"] == 3
    assert result["verified_prepared_artifact_count"] == 3
    assert result["publication_source_sequences"] == [0, 1, 2]
    assert result["namespace"] == _spec(plan).namespace
    assert state["closed"] is True
    assert len(list((root / "private/prepared").glob("*.json"))) == 3
    assert "private source body" not in (root / "events.jsonl").read_text()


def test_production_hooks_load_env_certification_and_delegate_formal_block(
    tmp_path: Path,
) -> None:
    plan = _plan()
    episodes = {
        history: tuple(
            _Episode(index, plan["history_source_sha256s"][history][index])
            for index in range(4)
        )
        for history in HISTORIES
    }
    certification = _certification()
    calls: dict[str, object] = {}
    paths = ProductionExecutorPaths.from_repository(tmp_path)

    async def smoke_executor(**kwargs):
        calls["smoke"] = kwargs
        return {"status": "PASS"}

    async def block_executor(**kwargs):
        calls["block"] = kwargs
        return {"status": "PASS", "block_index": kwargs["block_index"]}

    def load_env(path):
        calls["env_path"] = path
        return {"ENV": "value"}

    def load_certification(freeze_paths):
        calls["freeze_paths"] = freeze_paths
        return certification

    dependencies = ProductionExecutorDependencies(
        load_control_plan=lambda _path: plan,
        load_env=load_env,
        load_certification=load_certification,
        load_episodes=lambda _path, _plan: episodes,
        execute_smoke=smoke_executor,
        execute_block=block_executor,
    )
    hooks = build_production_executor_hooks(paths=paths, dependencies=dependencies)

    hooks.run_smoke(_spec(plan), tmp_path / "smoke")
    hooks.run_block(plan, 4, tmp_path / "block-04")

    assert calls["env_path"] == tmp_path / "membind-validation/.env"
    assert calls["block"]["episodes"] is episodes[HISTORIES[0]]
    assert calls["block"]["verified_plan"] == plan
    assert calls["block"]["state_cut_certification"] is certification
    assert calls["block"]["compile_workers"] == 2
    assert calls["block"]["lookahead"] == 2
