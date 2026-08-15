"""Offline production-adapter tests for the bounded S4 D2 diagnosis."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.s4_edge_identity_diagnosis_production import (
    DiagnosisProductionError,
    build_episode_manifest,
    candidate_call_diagnoses,
    install_edge_resolution_hook,
    namespace_snapshot,
    persisted_evidence_diagnosis,
    validate_d2_runtime,
)
from paper_eval.s4_edge_identity_dry_run import (
    D2DiagnosticStop,
    EdgeCandidateBarrier,
)


NAMESPACE = "pev3-s4-d0-replay-20260815-005"


@dataclass(frozen=True)
class Episode:
    source_sequence: int
    source_hash: str
    body: str
    group_id: str = "source-group"

    @property
    def name(self) -> str:
        return f"07741c45::episode::{self.source_sequence:04d}"


def _episodes() -> list[Episode]:
    return [
        Episode(index, f"{index + 1:064x}", f"body-{index}")
        for index in range(49)
    ]


def test_episode_manifest_binds_all_49_sources_without_body_disclosure() -> None:
    manifest, manifest_sha256 = build_episode_manifest(_episodes())

    assert len(manifest) == 49
    assert len(manifest_sha256) == 64
    assert manifest[_episodes()[7].name] == {
        "body_sha256": "44911ed2b0354262cce8e59bb7016bb4cf51dd7a2e1bae4d46bbce6d0902ac8e",
        "source_hash": f"{8:064x}",
        "source_sequence": 7,
    }
    assert "body-7" not in repr(manifest)


class SnapshotDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute_query(self, query: str, **kwargs):
        self.calls.append((query, kwargs))
        if "D2_NODE_SNAPSHOT" in query:
            return (
                [
                    {
                        "uuid": "node-2",
                        "labels": ["Entity"],
                        "properties": {"name": "B", "group_id": NAMESPACE},
                    },
                    {
                        "uuid": "episode-1",
                        "labels": ["Episodic"],
                        "properties": {
                            "name": "07741c45::episode::0000",
                            "group_id": NAMESPACE,
                        },
                    },
                ],
                None,
                None,
            )
        if "D2_RELATIONSHIP_SNAPSHOT" in query:
            return (
                [
                    {
                        "source_uuid": "episode-1",
                        "target_uuid": "node-2",
                        "type": "MENTIONS",
                        "properties": {"uuid": "rel-1"},
                    }
                ],
                None,
                None,
            )
        raise AssertionError("unexpected snapshot query")


@pytest.mark.asyncio
async def test_namespace_snapshot_is_hash_only_and_explicitly_read_routed() -> None:
    driver = SnapshotDriver()

    snapshot = await namespace_snapshot(driver, NAMESPACE)

    assert snapshot.keys() == {
        "canonical_snapshot_sha256",
        "episode_count",
        "episode_names_sha256",
        "node_count",
        "relationship_count",
    }
    assert snapshot["node_count"] == 2
    assert snapshot["relationship_count"] == 1
    assert snapshot["episode_count"] == 1
    assert "node-2" not in repr(snapshot)
    assert "07741c45" not in repr(snapshot)
    assert len(driver.calls) == 2
    assert all(kwargs.get("routing_") == "r" for _, kwargs in driver.calls)
    assert all(kwargs.get("namespace") == NAMESPACE for _, kwargs in driver.calls)


def _candidate(index: int) -> SimpleNamespace:
    return SimpleNamespace(
        uuid=f"edge-{index}",
        group_id=NAMESPACE,
        source_node_uuid="node-source",
        target_node_uuid=f"node-target-{index}",
        created_at="volatile",
        name="RELATION",
        fact=f"fact-{index}",
        episodes=["episode-1"],
        valid_at="2025-01-01T00:00:00Z",
        invalid_at=None,
        reference_time="2025-01-01T00:00:00Z",
        expired_at=None,
        attributes={},
        fact_embedding=None,
    )


def _records() -> list[dict]:
    return [
        {
            "correlation": f"internal-{index}",
            "extracted_edge": SimpleNamespace(
                source_node_uuid=f"new-source-{index}",
                target_node_uuid=f"new-target-{index}",
                fact=f"new-fact-{index}",
                name="RELATION",
            ),
            "related_edges": (),
            "invalidation_edges": (_candidate(index),),
        }
        for index in range(10)
    ]


@pytest.mark.asyncio
async def test_candidate_resolution_requires_exact_endpoint_and_provenance_joins() -> None:
    records = _records()
    episodes = _episodes()

    async def entities(_driver, uuids, *, group_id):
        assert group_id == NAMESPACE
        return [
            SimpleNamespace(
                uuid=uuid,
                group_id=NAMESPACE,
                name=uuid,
                labels=["Entity"],
                summary=f"summary-{uuid}",
                attributes={},
            )
            for uuid in reversed(uuids)
        ]

    async def provenance(_driver, uuids):
        assert uuids == ["episode-1"]
        return [
            SimpleNamespace(
                uuid="episode-1",
                group_id=NAMESPACE,
                name=episodes[0].name,
                content=episodes[0].body,
            )
        ]

    calls = await candidate_call_diagnoses(
        records=records,
        driver=object(),
        namespace=NAMESPACE,
        episodes=episodes,
        entity_loader=entities,
        episode_loader=provenance,
    )

    assert len(calls) == 10
    assert len({call["call_correlation_sha256"] for call in calls}) == 10
    assert all(
        call["partitions"]["related"]["classification"] == "EMPTY"
        and call["partitions"]["invalidation"]["classification"] == "UNIQUE"
        for call in calls
    )
    assert "node-source" not in repr(calls)
    assert "episode-1" not in repr(calls)
    assert "new-fact" not in repr(calls)


@pytest.mark.asyncio
async def test_candidate_resolution_rejects_foreign_or_missing_join() -> None:
    records = _records()
    episodes = _episodes()

    async def incomplete_entities(_driver, uuids, *, group_id):
        return [
            SimpleNamespace(
                uuid=uuids[0],
                group_id="foreign",
                name="foreign",
                labels=["Entity"],
                summary="foreign",
                attributes={},
            )
        ]

    async def no_episodes(_driver, uuids):
        return []

    with pytest.raises(DiagnosisProductionError):
        await candidate_call_diagnoses(
            records=records,
            driver=object(),
            namespace=NAMESPACE,
            episodes=episodes,
            entity_loader=incomplete_entities,
            episode_loader=no_episodes,
        )


def test_runtime_validation_rejects_init_task_or_driver_escape() -> None:
    driver = SimpleNamespace(_init_task=None)
    graph = SimpleNamespace(
        driver=driver,
        clients=SimpleNamespace(driver=driver),
    )
    runtime = SimpleNamespace(graph=graph)

    assert validate_d2_runtime(runtime) is driver

    driver._init_task = object()
    with pytest.raises(DiagnosisProductionError):
        validate_d2_runtime(runtime)

    driver._init_task = None
    graph.clients.driver = object()
    with pytest.raises(DiagnosisProductionError):
        validate_d2_runtime(runtime)


@pytest.mark.asyncio
async def test_edge_hook_uses_pre_prompt_boundary_and_restores() -> None:
    calls = 0

    async def original(*args, **kwargs):
        nonlocal calls
        calls += 1

    module = SimpleNamespace(resolve_extracted_edge=original)
    barrier = EdgeCandidateBarrier(expected_call_count=1, timeout_seconds=0.1)

    with install_edge_resolution_hook(module, barrier):
        with pytest.raises(D2DiagnosticStop):
            await module.resolve_extracted_edge(
                object(),
                SimpleNamespace(
                    source_node_uuid="source",
                    target_node_uuid="target",
                    fact="fact",
                ),
                [],
                [],
                object(),
                {},
            )

    assert calls == 0
    assert module.resolve_extracted_edge is original


def test_persisted_diagnosis_recomputes_duplicate_fact_without_disclosure(
    tmp_path: Path,
) -> None:
    episodes = _episodes()
    shared = "private duplicated candidate fact"
    extracted = [
        {
            "fact": f"private new fact {index}",
            "relation_type": "RELATION",
            "source_entity_name": "source",
            "target_entity_name": "target",
            "valid_at": None,
            "invalid_at": None,
        }
        for index in range(10)
    ]
    records = [
        {
            "prompt_parts": {
                "decoding_config": {"prompt_name": "extract_edges.edge"},
                "user_prompt": f"prefix {episodes[7].body} suffix",
            },
            "parsed_response": {"edges": extracted},
        }
    ]
    for index, edge in enumerate(extracted):
        candidates = [
            {"idx": candidate_index, "fact": f"private candidate {candidate_index}"}
            for candidate_index in range(10)
        ]
        if index < 9:
            candidates[-2]["fact"] = shared
            candidates[-1]["fact"] = shared
        records.append(
            {
                "prompt_parts": {
                    "decoding_config": {"prompt_name": "dedupe_edges.resolve_edge"},
                    "user_prompt": (
                        "<FACT INVALIDATION CANDIDATES>\n"
                        f"{candidates!r}\n"
                        "</FACT INVALIDATION CANDIDATES>\n"
                        f"<NEW FACT>\n{edge['fact']}\n</NEW FACT>"
                    ),
                },
                "parsed_response": {
                    "duplicate_facts": [],
                    "contradicted_facts": [],
                },
            }
        )
    prompt_cache = tmp_path / "prompt.jsonl"
    prompt_cache.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    graph = tmp_path / "canonical_graph.json"
    graph.write_text(
        json.dumps(
            {
                "edges": [
                    {
                        "fact": shared,
                        "source_entity_key": "private source one",
                        "target_entity_key": "private target one",
                    },
                    {
                        "fact": shared,
                        "source_entity_key": "private source two",
                        "target_entity_key": "private target two",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    diagnosis = persisted_evidence_diagnosis(
        episodes=episodes,
        prompt_cache_path=prompt_cache,
        canonical_graph_path=graph,
        source_sequence=7,
    )

    assert diagnosis["edge_resolution_prompt_count"] == 10
    assert diagnosis["ambiguous_prompt_count"] == 9
    assert diagnosis["duplicate_fact_multiplicity"] == 2
    assert diagnosis["matching_capture_graph_edge_count"] == 2
    assert diagnosis["matching_edges_directed_endpoints_distinct"] is True
    assert "private" not in repr(diagnosis)
