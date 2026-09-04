from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from saturated_fixed_work_baseline_v1_3.membind_v6_1.resource_credit import (
    ResourceCreditPolicy,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.upstream_campaign import (
    run_upstream_membind_construction_async,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.upstream_runtime import (
    FORMAL_ARM_C,
    GRAPHITI_COMMIT,
    GRAPHITI_VERSION,
    P1_DEPLOYMENT_POLICY,
)


MAB8192_ADAPTER_VERSION = "MAB_ROLE_AWARE_LOSSLESS_8192_V1"


class _Scope:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def restore(self) -> None:
        return None


class _Recorder:
    def __init__(self) -> None:
        self.records = []

    def episode_scope(self, *_args):
        return _Scope()

    def episode_envelope(self, run_id, episode_id, source_sequence):
        return {
            "run_id": run_id,
            "episode_id": episode_id,
            "source_sequence": source_sequence,
            "spans": [],
        }


def _episodes() -> tuple[SimpleNamespace, ...]:
    return tuple(
        SimpleNamespace(
            context_id="ctx-0",
            source_sequence=sequence,
            original_source_sequence=sequence,
            episode_id=f"chunk-{sequence}",
            session_id=f"session-{sequence}",
            reference_time=f"2026-01-0{sequence + 1}T00:00:00Z",
            body=f"[USER]\nmessage {sequence}",
            dataset_revision="dataset@r1",
            chunk_ordinal=0,
            chunk_count=1,
            chunk_id=f"chunk-{sequence}",
            previous_chunk_id=None,
            adapter_version=MAB8192_ADAPTER_VERSION,
        )
        for sequence in range(2)
    )


def test_upstream_campaign_module_excludes_old_method_paths() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "saturated_fixed_work_baseline_v1_3"
        / "membind_v6_1"
        / "upstream_campaign.py"
    ).read_text(encoding="utf-8")
    prohibited = (
        "structured_output_recovery",
        "bounded_edge_tasks",
        "finite_edge_task",
        "from .mab import",
    )
    assert all(value not in source for value in prohibited)


def test_upstream_campaign_replays_exact_extraction_and_seals(tmp_path: Path) -> None:
    episodes = _episodes()

    class Delegate:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def generate_response(self, messages, *, prompt_name=None, **_kwargs):
            content = str(messages[0]["content"])
            self.calls.append((str(prompt_name), content))
            return {"prompt_name": prompt_name, "content": content}

    Delegate.__module__ = "graphiti_core.llm_client.openai_generic_client"
    Delegate.__qualname__ = "OpenAIGenericClient"

    delegate = Delegate()

    class Graph:
        max_coroutines = 3

        def __init__(self) -> None:
            self.llm_client = delegate
            self.clients = SimpleNamespace(llm_client=delegate)
            self.published: list[int] = []

        async def add_episode(self, **kwargs):
            sequence = int(kwargs["name"].split("::")[-1])
            body = kwargs["episode_body"]
            await self.clients.llm_client.generate_response(
                [{"role": "user", "content": f"nodes:{body}"}],
                response_model={"type": "nodes"},
                max_tokens=16384,
                group_id=kwargs["group_id"],
                prompt_name="extract_nodes.extract_message",
            )
            await self.clients.llm_client.generate_response(
                [{"role": "user", "content": f"edges:{body}"}],
                response_model={"type": "edges"},
                max_tokens=16384,
                group_id=kwargs["group_id"],
                prompt_name="extract_edges.edge",
            )
            await self.clients.llm_client.generate_response(
                [{"role": "user", "content": f"dedupe:{body}"}],
                response_model={"type": "dedupe"},
                max_tokens=16384,
                group_id=kwargs["group_id"],
                prompt_name="dedupe_nodes.resolve_nodes",
            )
            self.published.append(sequence)
            return {"sequence": sequence}

        async def close(self) -> None:
            return None

    Graph.__module__ = "graphiti_core.graphiti"
    Graph.__qualname__ = "Graphiti"
    Graph.add_episode.__module__ = "graphiti_core.graphiti"
    Graph.add_episode.__qualname__ = "Graphiti.add_episode"

    graph = Graph()
    runtime = SimpleNamespace(
        graphiti=graph,
        llm_client=delegate,
        config=SimpleNamespace(
            max_coroutines=3,
            construction_model=P1_DEPLOYMENT_POLICY.served_model,
            construction_model_revision=P1_DEPLOYMENT_POLICY.revision,
            requested_max_tokens=16384,
            structured_output_mode="json_schema",
        ),
        _membind_transport_telemetry=[],
        _membind_formal_arm=FORMAL_ARM_C,
        _membind_graphiti_version=GRAPHITI_VERSION,
        _membind_graphiti_commit=GRAPHITI_COMMIT,
        _membind_deployment_policy=P1_DEPLOYMENT_POLICY,
        _membind_patch_inventory={
            "strict_upstream_core": True,
            "graphiti_algorithm_mutated": False,
            "shared_compatibility_substrate": False,
            "algorithm_patches": [],
            "prohibited_algorithm_patches": [],
            "deployment_policy_id": P1_DEPLOYMENT_POLICY.policy_id,
        },
    )

    async def extract_nodes(clients, episode, _previous, *_args):
        await clients.llm_client.generate_response(
            [{"role": "user", "content": f"nodes:{episode.content}"}],
            response_model={"type": "nodes"},
            max_tokens=16384,
            group_id=episode.group_id,
            prompt_name="extract_nodes.extract_message",
        )
        return ["node"], {"node": [0]}

    async def extract_edges(clients, episode, _nodes, _previous, *_args):
        await clients.llm_client.generate_response(
            [{"role": "user", "content": f"edges:{episode.content}"}],
            response_model={"type": "edges"},
            max_tokens=16384,
            group_id=episode.group_id,
            prompt_name="extract_edges.edge",
        )
        return ["edge"]

    manifest = SimpleNamespace(
        manifest_sha256="e" * 64,
        jsonl=lambda: "".join(
            json.dumps(vars(item), sort_keys=True) + "\n" for item in episodes
        ),
    )
    result = asyncio.run(
        run_upstream_membind_construction_async(
            run_id="upstream-c-test",
            context_id="ctx-0",
            namespace="upstream-c-test",
            episodes=episodes,
            policy=ResourceCreditPolicy(),
            runtime_builder=lambda: runtime,
            instrumentation_installer=lambda *_args: _Scope(),
            recorder_factory=_Recorder,
            graph_exporter=lambda *_args: {
                "status": "PASS",
                "canonical_graph_hash": "d" * 64,
            },
            output_root=tmp_path / "c",
            authority={"authority_sha256": "a" * 64},
            workload_manifest=manifest,
            frozen_config={"config_sha256": "c" * 64},
            extract_nodes_fn=extract_nodes,
            extract_edges_fn=extract_edges,
        )
    )
    assert result["status"] == "PASS"
    assert result["method"] == "MEMBIND_V6_1_UPSTREAM_CORE_MAB8192"
    assert result["adapter_coverage"]["status"] == "PASS"
    assert result["publication_guarantee"] == "ORDERED_DURABLE_FRONTIER_NO_ATTEMPT_RESUME"
    assert graph.published == [0, 1]
    assert len(delegate.calls) == 6
    assert result["transcript_summary"] == {
        "logical_captured": 4,
        "logical_consumed": 4,
        "logical_discarded": 0,
        "unconsumed": 0,
        "duplicates": 0,
        "fresh_fallback": 0,
        "mismatch_fallback": 0,
        "missing_fallback": 0,
    }
    assert result["refinement_validation"]["refinement_status"] == "PASS"
    assert len(result["bindings"]) == 4
    replay_rows = [row for row in result["provider_calls"] if row["replay"]]
    assert len(replay_rows) == 4
    assert all(row["physical_attempt_count"] == 0 for row in replay_rows)
    assert json.loads((tmp_path / "c" / "adapter_coverage.json").read_text())["status"] == "PASS"
    bindings = [
        json.loads(line)
        for line in (tmp_path / "c" / "replay_binding.jsonl").read_text().splitlines()
    ]
    assert len(bindings) == 4
    assert all(row["match_status"] == "EXACT_MATCH" for row in bindings)
