#!/usr/bin/env python3
"""Qualify the Graphiti 0.29.3 native-driver-shaped MEG offline gate.

This qualification is intentionally provider-free.  It exercises the same
fallback calls and native capability surface as the pinned Neo4j driver, but
it never creates a network client, starts a service, or writes a live graph.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent
SOURCE = PROJECT / "src"
LEGACY = REPOSITORY / "membind-validation"
for position, path in enumerate((SOURCE, LEGACY / "src")):
    if str(path) not in sys.path:
        sys.path.insert(position, str(path))

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file  # noqa: E402
from paper_eval.membind_v4.mseg.graphiti_0293_audit import audit_graphiti_0293  # noqa: E402
from paper_eval.membind_v4.mseg.graphiti_0293_runtime import (  # noqa: E402
    _DriverProxy,
    build_observe_only_binding,
    snapshot_controlled_execution,
)
from paper_eval.membind_v4.mseg.mutation_epoch import StateMutationEpoch  # noqa: E402
from paper_eval.membind_v4.mseg.passive_equivalence import compare_observe_only_execution  # noqa: E402
from paper_eval.membind_v4.mseg.runtime_instrumentation import (  # noqa: E402
    InstrumentationMode,
    MEGRuntimeRecorder,
    OperatorEventType,
    WriterDomainCertificate,
)
from paper_eval.membind_v4.mseg.vertical_slice import Graphiti0293BindVerticalSlice  # noqa: E402
from paper_eval.s5_graphiti_controlled_fixture import build_controlled_graphiti_fixture  # noqa: E402


REVISION = "meg-runtime-offline-20260821-011"
ROOT = PROJECT / "artifacts/paper_eval/membind_v4/meg_runtime_instrumentation"


class _RecorderProbe:
    def record_db_read(self, _value: dict[str, object]) -> None:
        return None

    def record_write_intent(self, _value: dict[str, object]) -> None:
        return None


def _writer() -> WriterDomainCertificate:
    return WriterDomainCertificate.create(
        # Graphiti group identity is the isolation namespace; the Neo4j
        # database remains fixed and is carried separately as backend_id.
        namespace="fresh-graphiti-group",
        graph_backend="neo4j",
        authorized_writer_identity="native-driver-shaped-offline",
        write_path_coverage=("bulk_utils.add_nodes_and_edges_bulk.execute_write",),
        expected_write_paths=("bulk_utils.add_nodes_and_edges_bulk.execute_write",),
        external_writer_policy="DENY",
        commit_observer_coverage="ALL_MANAGED_COMMITS",
        fresh_namespace=True,
        no_background_mutation=True,
    )


def _fallback_probe() -> dict[str, Any]:
    from graphiti_core.driver.driver import GraphProvider
    from graphiti_core.search.search_filters import SearchFilters
    from graphiti_core.search.search_utils import (
        edge_fulltext_search,
        edge_similarity_search,
        node_fulltext_search,
        node_similarity_search,
    )

    class NativeSearchDriver:
        provider = GraphProvider.NEO4J
        fulltext_syntax = ""
        search_interface = None
        graph_operations_interface = None

        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def execute_query(self, cypher_query_: str, **kwargs: object):
            self.calls.append({"cypher": cypher_query_, "kwargs": dict(kwargs)})
            return [], None, None

    inner = NativeSearchDriver()
    proxy = _DriverProxy(inner, _RecorderProbe(), None)  # type: ignore[arg-type]
    filters = SearchFilters()
    groups = ["fresh-graphiti-group"]
    asyncio.run(node_similarity_search(proxy, [0.1, 0.2], filters, groups, 7, 0.42))
    asyncio.run(node_fulltext_search(proxy, "Alice", filters, groups, 7))
    asyncio.run(edge_similarity_search(proxy, [0.1, 0.2], "source", "target", filters, groups, 7, 0.42))
    asyncio.run(edge_fulltext_search(proxy, "works", filters, groups, 7))
    calls = inner.calls
    return {
        "call_count": len(calls),
        "all_native_execute_query": len(calls) == 4,
        "similarity_calls_preserved": ["search_vector" in call["kwargs"] for call in calls] == [True, False, True, False],
        "fulltext_calls_preserved": ["query" in call["kwargs"] for call in calls] == [False, True, False, True],
        "candidate_limit_preserved": all(call["kwargs"].get("limit") == 7 for call in calls),
        "group_ids_preserved": all(call["kwargs"].get("group_ids") == groups for call in calls),
        "routing_preserved": all(call["kwargs"].get("routing_") == "r" for call in calls),
        "query_parameter_preserved": bool(calls[1]["kwargs"].get("query")) and bool(calls[3]["kwargs"].get("query")),
        "min_score_preserved": calls[0]["kwargs"].get("min_score") == 0.42 and calls[2]["kwargs"].get("min_score") == 0.42,
    }


def _seal(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["payload_sha256"] = payload_sha256(result)
    return result


def build_documents() -> dict[str, dict[str, Any] | str]:
    graphiti_root = LEGACY / "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core"
    if not graphiti_root.is_dir():
        graphiti_root = REPOSITORY / "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core"
    audit = audit_graphiti_0293(graphiti_root)
    from graphiti_core.driver.driver import GraphDriver, GraphProvider
    from graphiti_core.driver.neo4j_driver import Neo4jDriver

    native_fixture = build_controlled_graphiti_fixture(
        native_driver_shape=True,
        configured_database="neo4j",
        group_id="fresh-graphiti-group",
        canonical_candidate=True,
        edge_types=("WorksAt",),
        edge_fact="Alice works at Acme.",
        invalidation_candidate=True,
    )
    native_result = asyncio.run(native_fixture.run_episode())
    native_events = native_fixture.events
    fallback = _fallback_probe()

    baseline_fixture = build_controlled_graphiti_fixture(
        native_driver_shape=True,
        configured_database="neo4j",
        group_id="fresh-graphiti-group",
        canonical_candidate=True,
        edge_types=("WorksAt",),
        edge_fact="Alice works at Acme.",
        invalidation_candidate=True,
    )
    baseline_result = asyncio.run(baseline_fixture.run_episode())
    baseline = snapshot_controlled_execution(baseline_fixture, baseline_result)
    observed_fixture = build_controlled_graphiti_fixture(
        native_driver_shape=True,
        configured_database="neo4j",
        group_id="fresh-graphiti-group",
        canonical_candidate=True,
        edge_types=("WorksAt",),
        edge_fact="Alice works at Acme.",
        invalidation_candidate=True,
    )
    recorder = MEGRuntimeRecorder(mode=InstrumentationMode.OBSERVE_ONLY, writer_domain=_writer())
    epoch = StateMutationEpoch(namespace="fresh-graphiti-group", backend_id="neo4j", epoch=f"{REVISION}-epoch")
    observed_fixture.runtime.binding = build_observe_only_binding(
        observed_fixture.binding,
        recorder=recorder,
        mutation_epoch=epoch,
        writer_domain=_writer(),
        stream_id="07741c45",
    )
    observed_result = asyncio.run(observed_fixture.run_episode())
    observed = snapshot_controlled_execution(observed_fixture, observed_result, recorder=recorder)
    passive = compare_observe_only_execution(baseline, observed)

    vertical = asyncio.run(
        Graphiti0293BindVerticalSlice(
            edge_facts=("Alice works at Acme.", "Alice leads Acme."),
            reverse_edge_completion=True,
        ).run()
    )
    commits = [event for event in vertical.recorder.events if event.event_type is OperatorEventType.TRANSACTION_COMMIT]
    publications = [event for event in vertical.recorder.events if event.event_type is OperatorEventType.PUBLICATION]
    native_clone = native_fixture.driver.clone(database="fresh-graphiti-group")
    route_gate = (
        Neo4jDriver.clone is GraphDriver.clone
        and native_clone is native_fixture.driver
        and native_fixture.driver._database == "neo4j"
        and native_result.routed_database == "neo4j"
        and native_fixture.driver.provider is GraphProvider.NEO4J
        and native_fixture.driver.clone_calls == ["fresh-graphiti-group", "fresh-graphiti-group", "fresh-graphiti-group"]
    )
    tx_fallback = (
        native_result.commit_completed
        and sum(event.get("event") == "tx_run" for event in native_events) == 4
        and not any(event.get("event") in {"node_save_bulk", "edge_save_bulk"} for event in native_events)
    )
    gates = {
        "native_optional_capability_shape": native_fixture.driver.search_interface is None and native_fixture.driver.graph_operations_interface is None,
        "native_provider_unchanged": native_fixture.driver.provider is GraphProvider.NEO4J,
        "native_clone_fixed_database_fresh_group_contract": route_gate,
        "search_fallback_branch_unchanged": all(fallback.values()),
        "transaction_fallback_unchanged": tx_fallback,
        "native_shaped_vertical_slice_commit_publication": bool(vertical.prepared_artifact.raw_nodes) and len(commits) == len(publications) == 1 and commits[0].event_sequence < publications[0].event_sequence,
        "native_shaped_request_lineage_100_percent": vertical.request_lineage_complete,
        "native_shaped_operator_ready": any(event.event_type is OperatorEventType.OPERATOR_READY for event in vertical.recorder.events),
        "native_shaped_passive_equivalence": passive.passed,
        "zero_shadow_and_external_services": not observed.shadow_db_read_hashes and observed.shadow_llm_call_count == 0 and observed.shadow_embedding_call_count == 0 and observed.shadow_persistent_write_count == 0,
    }
    status = "PASS_NATIVE_GRAPHITI_0293_BACKEND_CONTRACT_PARITY" if all(gates.values()) else "STOP_NATIVE_GRAPHITI_0293_BACKEND_CONTRACT_PARITY"
    source_hashes = {
        name: sha256_file(PROJECT / path)
        for name, path in {
            "graphiti_runtime": "src/paper_eval/membind_v4/mseg/graphiti_0293_runtime.py",
            "controlled_fixture": "src/paper_eval/s5_graphiti_controlled_fixture.py",
            "vertical_slice": "src/paper_eval/membind_v4/mseg/vertical_slice.py",
            "graphiti_adapter": "src/paper_eval/membind_v31/graphiti_adapter.py",
        }.items()
    }
    qualification = _seal({
        "schema_version": "membind.meg.native-driver-parity.v1",
        "revision": REVISION,
        "status": status,
        "analysis_mode": "OFFLINE_PROVIDER_FREE_NATIVE_DRIVER_SHAPED",
        "graphiti_version": audit["graphiti_version"],
        "historical_fixture_status": "OFFLINE_FIXTURE_PARITY_GAP",
        "backend_contract": {
            "isolation": "fixed Neo4j database + fresh Graphiti group_id",
            "database": "neo4j",
            "group_id": "fresh-graphiti-group",
            "per_run_database": False,
            "clone_semantics": "GraphDriver.clone() returns self for Neo4j 0.29.3",
            "with_database_in_experiment_path": False,
        },
        "pinned_native_shape": {
            "search_interface": None,
            "graph_operations_interface": None,
            "provider": GraphProvider.NEO4J.value,
            "clone_is_base_implementation": Neo4jDriver.clone is GraphDriver.clone,
            "execute_query_signature": str(inspect.signature(Neo4jDriver.execute_query)),
        },
        "fallback_probe": fallback,
        "gates": gates,
        "metrics": {
            "native_vertical_operator_count": len(vertical.recorder.operators),
            "native_vertical_request_span_count": len(vertical.recorder.request_spans),
            "native_vertical_transaction_commit_count": len(commits),
            "native_vertical_publication_count": len(publications),
            "native_fixture_execute_query_count": sum(event.get("event") == "execute_query" for event in native_events),
            "native_fixture_transaction_run_count": sum(event.get("event") == "tx_run" for event in native_events),
            "observed_mutation_epoch": epoch.snapshot().counter,
        },
        "source_hashes": source_hashes,
        "scope": {"network_calls": 0, "services_started": 0, "live_database_connections": 0, "live_model_calls": 0, "persistent_writes": 0, "sealed_artifacts_modified": False},
        "decision": {
            "status": "GO_RETRY_REAL_MEG_OBSERVE_0_2" if status.startswith("PASS") else "STOP_REAL_RUNTIME_SEMANTIC_LINEAGE",
            "retry_authorized": status.startswith("PASS"),
            "history_id": "07741c45",
            "source_sequences": [0, 1, 2],
            "mode": "OBSERVE_ONLY",
            "shadow_read": False,
            "scheduler_change": False,
            "admission_reorder": False,
            "extra_llm_embedding_db_io": False,
        },
    })
    parity = _seal({
        "schema_version": "membind.meg.native-driver-parity-report.v1",
        "revision": REVISION,
        "status": status,
        "historical_fixture_status": "OFFLINE_FIXTURE_PARITY_GAP",
        "native_driver_capability_shape": qualification["pinned_native_shape"],
        "backend_contract": qualification["backend_contract"],
        "fallback_probe": fallback,
        "native_fixture_events": {"execute_query": qualification["metrics"]["native_fixture_execute_query_count"], "tx_run": qualification["metrics"]["native_fixture_transaction_run_count"]},
        "passive_equivalence": {"passed": passive.passed, "violations": list(passive.violations)},
        "scope": qualification["scope"],
    })
    markdown = "\n".join([
        "# Native Graphiti 0.29.3 Backend Contract Parity",
        "",
        f"STATUS: `{status}`",
        f"REVISION: `{REVISION}`",
        "",
        "Historical provider-free vertical-slice artifacts remain unchanged and are labeled `OFFLINE_FIXTURE_PARITY_GAP`; this bundle is the first native-driver-shaped qualification.",
        "",
        "## Contract",
        "",
        "The only accepted isolation contract is fixed Neo4j database `neo4j` plus fresh Graphiti `group_id` `fresh-graphiti-group`. Neo4j 0.29.3 inherits `GraphDriver.clone()`, which returns the same driver; `with_database()` is not used by the experiment.",
        "",
        "## Gates",
        "",
        *[f"- `{name}`: {'PASS' if passed else 'FAIL'}" for name, passed in sorted(gates.items())],
        "",
        f"Decision: `{qualification['decision']['status']}`",
        "",
        "No live service, model, embedding provider, or database was contacted while producing this artifact.",
        "",
    ])
    return {
        "MEG_NATIVE_DRIVER_PARITY_QUALIFICATION.json": qualification,
        "MEG_NATIVE_DRIVER_PARITY_REPORT.json": parity,
        "MEG_NATIVE_DRIVER_PARITY.md": markdown,
    }


def main() -> int:
    output = ROOT / REVISION
    if output.exists():
        raise ValueError("native_driver_parity_output_not_fresh")
    documents = build_documents()
    output.mkdir(parents=True, exist_ok=False)
    for name, value in documents.items():
        path = output / name
        if isinstance(value, dict):
            atomic_write_json(path, value)
        else:
            path.write_text(value, encoding="utf-8")
    qualification = documents["MEG_NATIVE_DRIVER_PARITY_QUALIFICATION.json"]
    assert isinstance(qualification, dict)
    print(json.dumps(qualification["decision"], sort_keys=True))
    return 0 if qualification["status"].startswith("PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
