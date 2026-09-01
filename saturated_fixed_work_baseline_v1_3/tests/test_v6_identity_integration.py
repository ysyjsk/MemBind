from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.binder import (
    NativeBindingScope,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.provider_admission import (
    provider_scope,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.request_identity import (
    build_request_identity,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.transcript import (
    BindingMismatch,
    TranscriptStore,
)
from saturated_fixed_work_baseline_v1_3.membind_v6.proof import (
    V6ProofError,
    validate_replay_accounting,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.admission import (
    ForegroundAdmissionArbiter,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.policy import V61Policy
from saturated_fixed_work_baseline_v1_3.membind_v6_1.provider import V61ProviderClient
import saturated_fixed_work_baseline_v1_3.membind_v6_1.core as core
import saturated_fixed_work_baseline_v1_3.membind_v6_1.mab as v61_mab
from saturated_fixed_work_baseline_v1_3.workload_contract import EpisodeInput, WorkloadManifest
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission import (
    CapacityAuthority,
)


def _identity(content: str):
    return build_request_identity(
        source_sequence=0,
        callsite="extract_nodes.extract_message",
        ordinal=0,
        messages=[{"role": "user", "content": content}],
        response_model={"type": "object"},
        max_tokens=32,
        model_size="medium",
        group_id="g",
        prompt_name="extract_nodes.extract_message",
        flags={"attribute_extraction": False},
        client_identity={"class": "test", "source_hash": "test"},
        transport_identity={"seed": 1},
        cache_salt="",
        previous_context_digest="",
    )


class _Delegate:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_response(self, messages, **_kwargs):
        self.calls += 1
        return {"call": self.calls, "messages": messages}


def _client(delegate: _Delegate, store: TranscriptStore, *, mode: str):
    arbiter = ForegroundAdmissionArbiter(
        CapacityAuthority(2),
        policy=V61Policy(lookahead=1, future_cap=1, native_future_quota=0),
    )
    return V61ProviderClient(
        delegate,
        store=store,
        arbiter=arbiter,
        mode=mode,
        durable_frontier=lambda: -1,
        client_identity={"class": "test.Delegate", "source_hash": "test"},
    ), arbiter


def _kwargs():
    return {
        "response_model": {"type": "object"},
        "max_tokens": 32,
        "model_size": "medium",
        "group_id": "g",
        "prompt_name": "extract_nodes.extract_message",
    }


def test_mismatch_fallback_is_fresh_and_admitted() -> None:
    async def scenario() -> None:
        delegate = _Delegate()
        store = TranscriptStore()
        capture, arbiter = _client(delegate, store, mode="capture")
        with provider_scope(region="PREPARE", source_sequence=0):
            await capture.generate_response(
                [{"role": "user", "content": "prepared"}], **_kwargs()
            )

        replay = V61ProviderClient(
            delegate,
            store=store,
            arbiter=arbiter,
            mode="replay",
            durable_frontier=lambda: -1,
            client_identity={"class": "test.Delegate", "source_hash": "test"},
        )
        with provider_scope(region="NATIVE", source_sequence=0):
            with NativeBindingScope(store, source_sequence=0, strict=False):
                result = await replay.generate_response(
                    [{"role": "user", "content": "native changed"}], **_kwargs()
                )
        assert result["call"] == 2
        assert delegate.calls == 2
        assert store.summary() == {
            "logical_captured": 1,
            "logical_consumed": 0,
            "logical_discarded": 1,
            "unconsumed": 0,
            "duplicates": 0,
            "fresh_fallback": 1,
            "mismatch_fallback": 1,
            "missing_fallback": 0,
        }
        row = replay.provider_calls[-1]
        assert row["replay"] is False
        assert row["fallback_type"] == "mismatch"
        assert row["transport_attempt_count"] == 0
        assert arbiter.outstanding == 0

    asyncio.run(scenario())


def test_missing_fallback_is_fresh_and_accounted() -> None:
    async def scenario() -> None:
        delegate = _Delegate()
        store = TranscriptStore()
        replay, arbiter = _client(delegate, store, mode="replay")
        with provider_scope(region="NATIVE", source_sequence=0):
            with NativeBindingScope(store, source_sequence=0, strict=False):
                result = await replay.generate_response(
                    [{"role": "user", "content": "missing"}], **_kwargs()
                )
        assert result["call"] == 1
        assert store.summary()["fresh_fallback"] == 1
        assert store.summary()["missing_fallback"] == 1
        assert store.summary()["logical_discarded"] == 0
        row = replay.provider_calls[-1]
        assert row["replay"] is False
        assert row["fallback_type"] == "missing"
        assert arbiter.outstanding == 0

    asyncio.run(scenario())


def test_duplicate_consume_never_silently_falls_back() -> None:
    async def scenario() -> None:
        store = TranscriptStore()
        identity = _identity("same")
        store.capture(identity, {"answer": "prepared"})
        assert store.consume(identity) == {"answer": "prepared"}
        with pytest.raises(BindingMismatch, match="duplicate transcript consume"):
            with NativeBindingScope(store, source_sequence=0, strict=False) as scope:
                await scope.invoke(identity, lambda: _fresh(), certified=True)

    async def _fresh():
        return {"answer": "fresh"}

    asyncio.run(scenario())


def test_replay_accounting_accepts_discarded_fallback_but_not_remaining_work() -> None:
    summary = {
        "logical_captured": 3,
        "logical_consumed": 2,
        "logical_discarded": 1,
        "unconsumed": 0,
        "duplicates": 0,
        "fresh_fallback": 1,
        "mismatch_fallback": 1,
        "missing_fallback": 0,
    }
    assert validate_replay_accounting(summary)["status"] == "PASS"
    with pytest.raises(V6ProofError, match="replay accounting"):
        validate_replay_accounting({**summary, "unconsumed": 1})
    with pytest.raises(V6ProofError, match="replay accounting"):
        validate_replay_accounting({**summary, "fresh_fallback": 2})


class _ConstructionDelegate:
    def __init__(self, *, fail_native: bool = False) -> None:
        self.calls = 0
        self.fail_native = fail_native
        self._membind_extraction_diagnostics: list[dict[str, object]] = []

    async def generate_response(self, messages, **kwargs):
        self.calls += 1
        if self.fail_native and kwargs.get("prompt_name") == "extract_edges.edge":
            raise RuntimeError("fresh provider failed")
        return {"messages": messages, "call": self.calls}


class _ConstructionGraph:
    def __init__(self, delegate: _ConstructionDelegate, *, native_suffix: str = "") -> None:
        self.llm_client = delegate
        self.clients = SimpleNamespace(llm_client=delegate)
        self.driver = SimpleNamespace(execute_query=self._execute_query)
        self.max_coroutines = 2
        self.native_suffix = native_suffix

    async def _execute_query(self, *_args, **_kwargs):
        return SimpleNamespace(records=[])

    async def add_episode(self, **kwargs):
        # Core's Native path uses a distinct message when native_suffix is set,
        # intentionally exercising the binding mismatch fallback.
        content = f"{kwargs['name']}{self.native_suffix}"
        messages = [{"role": "user", "content": content}]
        await self.llm_client.generate_response(
            messages,
            **_kwargs_for("extract_nodes.extract_message", kwargs["group_id"]),
        )
        await self.llm_client.generate_response(
            messages,
            **_kwargs_for("extract_edges.edge", kwargs["group_id"]),
        )
        return {"name": kwargs["name"]}

    async def close(self):
        return None


class _ConstructionRecorder:
    def __init__(self) -> None:
        self.records = []

    class Scope:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def restore(self):
            return None

    def episode_scope(self, *_args):
        return self.Scope()

    def episode_envelope(self, run_id, episode_id, source_sequence):
        return {
            "schema_version": "test.trace",
            "run_id": run_id,
            "episode_id": episode_id,
            "source_sequence": source_sequence,
            "spans": [],
        }


def _kwargs_for(prompt_name: str, group_id: str):
    return {
        "response_model": {"type": "object"},
        "max_tokens": 32,
        "model_size": "medium",
        "group_id": group_id,
        "prompt_name": prompt_name,
    }


def _construction_modules(monkeypatch, *, missing_edge_source: int | None = None):
    async def fake_extract_nodes(clients, episode, *_args):
        messages = [{"role": "user", "content": episode.name}]
        await clients.llm_client.generate_response(
            messages,
            **_kwargs_for("extract_nodes.extract_message", episode.group_id),
        )
        return [SimpleNamespace(name="node")], {0: 0}

    async def fake_extract_edges(clients, episode, *_args):
        if episode.source_sequence != missing_edge_source:
            messages = [{"role": "user", "content": episode.name}]
            await clients.llm_client.generate_response(
                messages,
                **_kwargs_for("extract_edges.edge", episode.group_id),
            )
        return [SimpleNamespace(name="edge")]

    modules = {
        name: ModuleType(name)
        for name in (
            "graphiti_core",
            "graphiti_core.utils",
            "graphiti_core.utils.maintenance",
            "graphiti_core.utils.maintenance.edge_operations",
            "graphiti_core.utils.maintenance.node_operations",
        )
    }
    modules["graphiti_core.utils.maintenance.node_operations"].extract_nodes = fake_extract_nodes
    modules["graphiti_core.utils.maintenance.edge_operations"].extract_edges = fake_extract_edges
    modules["graphiti_core.utils.maintenance.edge_operations"].resolve_extracted_edge = (
        lambda *_args, **_kwargs: None
    )
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(
        v61_mab,
        "_episode_node",
        lambda episode, *, namespace, uuid_value=None: SimpleNamespace(
            name=episode.name,
            group_id=namespace,
            uuid=uuid_value,
            source_sequence=getattr(episode, "source_sequence", 0),
        ),
    )
    monkeypatch.setattr(v61_mab, "_native_previous_window", lambda *_args: [])


def _construction_episodes() -> tuple[EpisodeInput, ...]:
    return tuple(
        EpisodeInput(
            context_id="ctx",
            source_sequence=index,
            episode_id=f"episode-{index}",
            reference_time=f"2026-01-0{index + 1}T00:00:00Z",
            body=f"message {index}",
        )
        for index in range(2)
    )


def _construction_manifest(episodes: tuple[EpisodeInput, ...]) -> WorkloadManifest:
    return WorkloadManifest.from_episodes(
        context_id="ctx",
        episodes=episodes,
        dataset_revision="revision",
        dataset_file_sha256="a" * 64,
        expected_episode_count=len(episodes),
    )


def _run_core_construction(
    tmp_path,
    delegate: _ConstructionDelegate,
    graph: _ConstructionGraph,
    episodes: tuple[EpisodeInput, ...],
):
    async def exporter(_graph, selected, namespace):
        return {
            "namespace": namespace,
            "episodes": [item.source_sequence for item in selected],
            "canonical_graph_hash": "b" * 64,
        }

    return asyncio.run(
        core.run_membind_core_construction_async(
            policy=core.core_policy(),
            run_id="test-core",
            context_id="ctx",
            namespace="local-qwen3-8b-awq-dualreplica-v1-test-core",
            episodes=episodes,
            runtime_builder=lambda: SimpleNamespace(
                graphiti=graph,
                llm_client=delegate,
                config=SimpleNamespace(max_coroutines=2),
            ),
            instrumentation_installer=lambda *_args: _ConstructionRecorder.Scope(),
            recorder_factory=_ConstructionRecorder,
            graph_exporter=exporter,
            output_root=tmp_path / "block",
            authority={"authority_sha256": "c" * 64},
            workload_manifest=_construction_manifest(episodes),
            frozen_config={"profile_id": "local-qwen3-8b-awq-dualreplica-v1"},
            environment={"profile_id": "local-qwen3-8b-awq-dualreplica-v1"},
            preflight={"status": "PASS"},
        )
    )


def test_core_construction_mismatch_fallback_seals_with_ordered_publication(
    tmp_path, monkeypatch
) -> None:
    _construction_modules(monkeypatch)
    delegate = _ConstructionDelegate()
    graph = _ConstructionGraph(delegate, native_suffix="-native")
    result = _run_core_construction(tmp_path, delegate, graph, _construction_episodes())
    assert result["status"] == "PASS"
    assert result["method"] == "MEMBIND_CORE"
    assert delegate.calls == 8  # four capture calls plus four fresh fallbacks
    logical = result["refinement_validation"]["proof"]["replay"]
    assert logical["logical_captured"] == 4
    assert logical["logical_consumed"] == 0
    assert logical["logical_discarded"] == 4
    assert logical["fresh_fallback"] == 4
    assert logical["mismatch_fallback"] == 4
    assert all(row["match_status"] == "MISMATCH_FRESH_FALLBACK" for row in result["bindings"])
    assert all(row["replay"] is False for row in result["provider_calls"] if row["region"] == "NATIVE")
    assert result["order_validation"]["order_contract_status"] == "PASS"
    assert result["construction_seal"]["status"] == "CONSTRUCTION_SEALED"


def test_core_construction_mixed_missing_and_mismatch_fallbacks_are_accounted(
    tmp_path, monkeypatch
) -> None:
    _construction_modules(monkeypatch, missing_edge_source=1)
    delegate = _ConstructionDelegate()
    graph = _ConstructionGraph(delegate, native_suffix="-native")
    result = _run_core_construction(tmp_path, delegate, graph, _construction_episodes())
    logical = result["refinement_validation"]["proof"]["replay"]
    assert logical["logical_captured"] == 3
    assert logical["logical_consumed"] == 0
    assert logical["logical_discarded"] == 3
    assert logical["mismatch_fallback"] == 3
    assert logical["missing_fallback"] == 1
    statuses = {row["match_status"] for row in result["bindings"]}
    assert statuses == {"MISMATCH_FRESH_FALLBACK", "MISSING_FRESH_FALLBACK"}
    assert result["construction_seal"]["status"] == "CONSTRUCTION_SEALED"


def test_core_construction_fresh_failure_does_not_publish_or_seal(tmp_path, monkeypatch) -> None:
    _construction_modules(monkeypatch)
    delegate = _ConstructionDelegate(fail_native=True)
    graph = _ConstructionGraph(delegate, native_suffix="-native")
    with pytest.raises(RuntimeError, match="fresh provider failed"):
        _run_core_construction(tmp_path, delegate, graph, _construction_episodes())
    assert not (tmp_path / "block").exists()


def test_core_construction_exact_binding_is_replayed_without_fresh_calls(tmp_path, monkeypatch) -> None:
    _construction_modules(monkeypatch)
    delegate = _ConstructionDelegate()
    graph = _ConstructionGraph(delegate)
    result = _run_core_construction(tmp_path, delegate, graph, _construction_episodes())
    assert result["status"] == "PASS"
    assert delegate.calls == 4
    logical = result["refinement_validation"]["proof"]["replay"]
    assert logical["logical_captured"] == 4
    assert logical["logical_consumed"] == 4
    assert logical["logical_discarded"] == 0
    assert logical["fresh_fallback"] == 0
    assert all(row["match_status"] == "EXACT_MATCH" for row in result["bindings"])
    assert sum(row["replay"] is True for row in result["provider_calls"]) == 4
    assert result["construction_seal"]["status"] == "CONSTRUCTION_SEALED"
