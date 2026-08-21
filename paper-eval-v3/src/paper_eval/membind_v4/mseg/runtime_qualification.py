"""Offline qualification documents for the pinned Graphiti 0.29.3 MEG seam.

The qualification executes only the controlled captured-response fixture. It
does not create a network client, connect to a graph backend, start a service,
or authorize any scheduler or semantic execution change.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s5_graphiti_controlled_fixture import (
    build_controlled_graphiti_fixture,
)

from .graphiti_0293_audit import audit_graphiti_0293
from .graphiti_0293_runtime import (
    build_observe_only_binding,
    snapshot_controlled_execution,
)
from .mutation_epoch import StateMutationEpoch
from .passive_equivalence import compare_observe_only_execution
from .read_view import ReadViewStatus
from .runtime_instrumentation import (
    InstrumentationMode,
    MEGRuntimeRecorder,
    OperatorEventType,
    SemanticOperatorClass,
    WriterDomainCertificate,
)


HISTORY_ID = "07741c45"
INITIAL_SOURCE_SEQUENCES = (0, 1, 2)
PUBLICATION_CONTRACT = "GRAPHITI_0293_ADD_EPISODE_SAGA_FREE_V0"


def _finalized(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["payload_sha256"] = payload_sha256(result)
    return result


def _counts(values: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _writer_domain(audit: dict[str, Any]) -> WriterDomainCertificate:
    covered = tuple(
        str(row["path_id"])
        for row in audit["write_path_inventory"]
        if row["relevance"] == "RELEVANT_COVERED"
    )
    return WriterDomainCertificate.create(
        namespace="controlled-db",
        graph_backend="neo4j",
        authorized_writer_identity="controlled-membind-construction",
        write_path_coverage=covered,
        expected_write_paths=covered,
        external_writer_policy="DENY",
        commit_observer_coverage="ALL_MANAGED_COMMITS",
        fresh_namespace=True,
        no_background_mutation=True,
    )


def _audit_markdown(audit: dict[str, Any]) -> str:
    def cell(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = [
        "# Graphiti 0.29.3 Semantic Boundary Audit",
        "",
        "This audit is pinned to the installed Graphiti 0.29.3 source used by the MemBind experiment environment.",
        "",
        "## Source Identity",
        "",
        f"- version: `{audit['graphiti_version']}`",
        f"- source root: `{audit['graphiti_source_root']}`",
        f"- publication contract: `{audit['publication_contract']['contract']}`",
        f"- write-path coverage: `{audit['covered_write_paths']}/{audit['relevant_write_paths']}` (`{audit['coverage_ratio']}`)",
        f"- audit status: `{audit['status']}`",
        "",
        "| Source | SHA-256 |",
        "| --- | --- |",
    ]
    lines.extend(
        f"| `{name}` | `{digest}` |"
        for name, digest in sorted(audit["source_hashes"].items())
    )
    lines.extend(
        [
            "",
            "## Semantic Boundaries",
            "",
            "| Operator | Semantic inputs | Mutable state read | LLM | Private result | Persistent mutation | Completion semantics | Publication impact | Classification | ReadView | Source evidence |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in audit["semantic_boundaries"]:
        lines.append(
            "| "
            + " | ".join(
                cell(row[key])
                for key in (
                    "operator",
                    "semantic_inputs",
                    "mutable_state_read",
                    "sends_llm",
                    "produces_private_result",
                    "persistent_mutation",
                    "completion_semantics",
                    "affects_publication",
                    "classification",
                    "read_view_required",
                    "source_evidence",
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Attribute, Timestamp, And Summary Classification",
            "",
            "| Operator | Mutable state read | Evidence | Classification | ReadView required | Covered by parent ReadView |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in audit["attribute_timestamp_summary"]:
        lines.append(
            "| "
            + " | ".join(
                cell(row[key])
                for key in (
                    "operator",
                    "mutable_state_read",
                    "evidence",
                    "classification",
                    "read_view_required",
                    "covered_by_parent_read_view",
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Transaction And Publication Boundary",
            "",
            "`add_nodes_and_edges_bulk()` calls `session.execute_write()`, so the commit observer is placed on successful managed-transaction return. Callback retries do not advance the mutation epoch; only the successful outer return does.",
            "",
            "The certified MEG-v0 path requires `saga=None` and no community update. Saga and community paths remain `CONFIG_GUARDED_OUT_OF_SCOPE`; they are not silently treated as covered production paths.",
            "",
            "No live service was contacted while generating this audit.",
            "",
        ]
    )
    return "\n".join(lines)


def _decision_markdown(qualification: dict[str, Any]) -> str:
    decision = qualification["decision"]
    gates = qualification["gates"]
    gate_lines = [
        f"| {name} | {'PASS' if passed else 'FAIL'} |"
        for name, passed in sorted(gates.items())
    ]
    return "\n".join(
        [
            "# MEG Runtime Instrumentation Decision",
            "",
            f"STATUS: {decision['status']}",
            f"QUALIFICATION: {decision['qualification']}",
            f"NEXT_GATE: {decision['next_gate']}",
            "",
            "## Offline Gates",
            "",
            "| Gate | Result |",
            "| --- | --- |",
            *gate_lines,
            "",
            "## Authorized Boundary",
            "",
            f"- mode: `{decision['authorized_mode']}`",
            f"- history: `{decision['authorized_history_id']}`",
            f"- initial sources: `{decision['authorized_source_sequences']}`",
            f"- bounded real capture authorized: `{decision['bounded_real_capture_authorized']}`",
            f"- bounded real capture started by this qualification: `{decision['bounded_real_capture_started']}`",
            f"- shadow read authorized: `{decision['shadow_read_authorized']}`",
            f"- scheduler authorized: `{decision['scheduler_authorized']}`",
            "",
            "This qualification establishes an OBSERVE_ONLY runtime substrate. It does not establish a finer-grained readiness window, ReadView validation HIT, or performance gain, and it does not change the frozen historical conclusions.",
            "",
        ]
    )


def build_runtime_instrumentation_documents(
    *, project_root: Path, graphiti_root: Path
) -> dict[str, dict[str, Any] | str]:
    """Run the no-network fixture and build the complete offline gate bundle."""

    project = Path(project_root).resolve()
    graphiti = Path(graphiti_root).resolve()
    if not project.is_dir():
        raise ValueError("meg_runtime_project_root_missing")
    audit = audit_graphiti_0293(graphiti)
    implementation_paths = {
        "graphiti_0293_audit": project
        / "src/paper_eval/membind_v4/mseg/graphiti_0293_audit.py",
        "graphiti_0293_runtime": project
        / "src/paper_eval/membind_v4/mseg/graphiti_0293_runtime.py",
        "passive_equivalence": project
        / "src/paper_eval/membind_v4/mseg/passive_equivalence.py",
        "read_view": project / "src/paper_eval/membind_v4/mseg/read_view.py",
        "runtime_instrumentation": project
        / "src/paper_eval/membind_v4/mseg/runtime_instrumentation.py",
        "runtime_live": project
        / "src/paper_eval/membind_v4/mseg/runtime_live.py",
        "runtime_qualification": project
        / "src/paper_eval/membind_v4/mseg/runtime_qualification.py",
        "controlled_fixture": project
        / "src/paper_eval/s5_graphiti_controlled_fixture.py",
    }
    if any(not path.is_file() for path in implementation_paths.values()):
        raise ValueError("meg_runtime_qualification_source_missing")
    implementation_source_hashes = {
        name: sha256_file(path) for name, path in sorted(implementation_paths.items())
    }
    writer = _writer_domain(audit)
    fixture_options = {
        "canonical_candidate": True,
        "edge_types": ("WorksAt",),
        "edge_fact": "Alice works at Acme.",
        "invalidation_candidate": True,
    }

    baseline_fixture = build_controlled_graphiti_fixture(**fixture_options)
    baseline_result = asyncio.run(baseline_fixture.run_episode())
    baseline = snapshot_controlled_execution(baseline_fixture, baseline_result)

    observed_fixture = build_controlled_graphiti_fixture(**fixture_options)
    recorder = MEGRuntimeRecorder(
        mode=InstrumentationMode.OBSERVE_ONLY,
        writer_domain=writer,
    )
    epoch = StateMutationEpoch(
        namespace="controlled-db",
        backend_id="neo4j",
        epoch="controlled-db-epoch",
    )
    observed_fixture.runtime.binding = build_observe_only_binding(
        observed_fixture.binding,
        recorder=recorder,
        mutation_epoch=epoch,
        writer_domain=writer,
        stream_id="controlled-stream",
    )
    observed_result = asyncio.run(observed_fixture.run_episode())
    observed = snapshot_controlled_execution(
        observed_fixture, observed_result, recorder=recorder
    )
    equivalence = compare_observe_only_execution(baseline, observed)

    operator_ids = {operator.semantic_operator_id for operator in recorder.operators}
    state_operator_ids = {
        operator.semantic_operator_id
        for operator in recorder.operators
        if operator.classification is SemanticOperatorClass.STATE_DERIVED
    }
    read_view_operator_ids = {
        view.read_view.operator_instance_id for view in recorder.read_views
    }
    events = recorder.events
    commit_positions = [
        event.event_sequence
        for event in events
        if event.event_type is OperatorEventType.TRANSACTION_COMMIT
    ]
    publication_positions = [
        event.event_sequence
        for event in events
        if event.event_type is OperatorEventType.PUBLICATION
    ]
    publication_events = [
        event
        for event in events
        if event.event_type is OperatorEventType.PUBLICATION
    ]
    zero_shadow_behavior = (
        not observed.shadow_db_read_hashes
        and observed.shadow_llm_call_count == 0
        and observed.shadow_embedding_call_count == 0
        and observed.shadow_persistent_write_count == 0
        and observed.publication_modification_count == 0
    )
    event_sequence_global = [event.event_sequence for event in events] == list(
        range(len(events))
    )
    request_lineage_complete = (
        len(recorder.request_spans) == observed.production_llm_call_count
        and all(span.semantic_operator_id in operator_ids for span in recorder.request_spans)
        and all(span.prompt_name for span in recorder.request_spans)
    )
    commit_precedes_publication = (
        len(commit_positions) == 1
        and len(publication_positions) == 1
        and commit_positions[0] < publication_positions[0]
        and publication_events[0].status == "CERTIFIED"
    )
    read_views_stable = bool(recorder.read_views) and all(
        view.status is ReadViewStatus.STABLE_READVIEW for view in recorder.read_views
    )

    gates = {
        "edge_child_identity_before_coroutine": all(
            operator.materialized_before_coroutine
            for operator in recorder.operators
            if operator.semantic_operator_type == "EDGE_RESOLUTION_CHILD"
        ),
        "event_sequence_global_and_contiguous": event_sequence_global,
        "managed_commit_precedes_certified_publication": commit_precedes_publication,
        "mutation_epoch_advanced_once": epoch.snapshot().counter == 1,
        "observe_only_passive_equivalence": equivalence.passed,
        "pinned_graphiti_0293_source": audit["graphiti_version"] == "0.29.3",
        "provider_free_replay_has_no_unexpected_consumption": (
            not baseline_fixture.unexpected_provider_consumption
            and not observed_fixture.unexpected_provider_consumption
        ),
        "request_lineage_complete": request_lineage_complete,
        "state_derived_readview_coverage_complete": (
            bool(state_operator_ids) and read_view_operator_ids == state_operator_ids
        ),
        "state_readviews_stable_under_certified_writer_domain": read_views_stable,
        "static_write_path_coverage_complete": (
            audit["status"] == "PASS"
            and audit["coverage_ratio"] == 1.0
            and audit["covered_write_paths"] == audit["relevant_write_paths"]
        ),
        "writer_domain_certified": writer.certified,
        "zero_shadow_behavior": zero_shadow_behavior,
    }
    qualified = all(gates.values())
    if not gates["static_write_path_coverage_complete"]:
        status = "STOP_TRANSACTION_OBSERVABILITY_INCOMPLETE"
    elif not qualified:
        status = "STOP_INSTRUMENTATION_FAILURE"
    else:
        status = "PASS_OFFLINE_MEG_RUNTIME_INSTRUMENTATION"

    audit_document = _finalized(dict(audit))
    coverage_document = _finalized(
        {
            "schema_version": "membind.meg.graphiti-0293-write-path-coverage.v1",
            "graphiti_version": audit["graphiti_version"],
            "publication_contract": PUBLICATION_CONTRACT,
            "relevant_write_paths": audit["relevant_write_paths"],
            "covered_write_paths": audit["covered_write_paths"],
            "coverage_ratio": audit["coverage_ratio"],
            "status": audit["status"],
            "write_path_inventory": audit["write_path_inventory"],
            "source_hashes": audit["source_hashes"],
        }
    )
    passive_document = _finalized(
        {
            "schema_version": "membind.meg.runtime-passive-equivalence.v1",
            "mode": InstrumentationMode.OBSERVE_ONLY.value,
            "fixture": "PINNED_GRAPHITI_0293_CAPTURED_RESPONSE_REPLAY",
            "instrumentation_source_hashes": implementation_source_hashes,
            "certificate_type": equivalence.certificate_type,
            "status": equivalence.status.value,
            "violations": list(equivalence.violations),
            "baseline": asdict(baseline),
            "instrumented": asdict(observed),
            "zero_shadow_behavior": zero_shadow_behavior,
            "network_calls": 0,
            "services_started": 0,
        }
    )
    metrics = {
        "event_count": len(events),
        "event_counts_by_type": _counts(
            [event.event_type.value for event in events]
        ),
        "event_sequence_global_and_contiguous": event_sequence_global,
        "final_mutation_epoch": epoch.snapshot().counter,
        "operator_count": len(recorder.operators),
        "operator_counts_by_class": _counts(
            [operator.classification.value for operator in recorder.operators]
        ),
        "operator_counts_by_type": _counts(
            [operator.semantic_operator_type for operator in recorder.operators]
        ),
        "publication_count": len(publication_positions),
        "read_view_count": len(recorder.read_views),
        "read_view_counts_by_kind": _counts(
            [view.read_view.read_kind.value for view in recorder.read_views]
        ),
        "read_view_counts_by_status": _counts(
            [view.status.value for view in recorder.read_views]
        ),
        "request_lineage_count": len(recorder.request_spans),
        "request_lineage_coverage": (
            1.0
            if observed.production_llm_call_count == len(recorder.request_spans)
            else 0.0
        ),
        "transaction_commit_count": len(commit_positions),
    }
    decision = {
        "status": status,
        "qualification": (
            "QUALIFIED_REAL_MEG_RUNTIME_INSTRUMENTATION"
            if qualified
            else "NOT_QUALIFIED"
        ),
        "next_gate": (
            "REAL_OBSERVE_ONLY_CAPTURE_0_2" if qualified else "NONE"
        ),
        "authorized_mode": InstrumentationMode.OBSERVE_ONLY.value,
        "authorized_history_id": HISTORY_ID,
        "authorized_source_sequences": list(INITIAL_SOURCE_SEQUENCES),
        "bounded_real_capture_authorized": qualified,
        "bounded_real_capture_started": False,
        "shadow_read_authorized": False,
        "scheduler_authorized": False,
        "semantic_change_authorized": False,
    }
    qualification_document = _finalized(
        {
            "schema_version": "membind.meg.runtime-instrumentation-qualification.v1",
            "analysis_mode": "OFFLINE_PROVIDER_FREE",
            "graphiti_version": audit["graphiti_version"],
            "publication_contract": PUBLICATION_CONTRACT,
            "instrumentation_source_hashes": implementation_source_hashes,
            "scope": {
                "network_calls": 0,
                "services_started": 0,
                "live_database_connections": 0,
                "live_model_calls": 0,
                "persistent_writes": 0,
                "sealed_artifacts_modified": False,
            },
            "writer_domain": {
                **asdict(writer),
                "status": writer.status.value,
            },
            "gates": gates,
            "metrics": metrics,
            "decision": decision,
        }
    )
    return {
        "GRAPHITI_0293_SEMANTIC_BOUNDARY_AUDIT.json": audit_document,
        "GRAPHITI_0293_SEMANTIC_BOUNDARY_AUDIT.md": _audit_markdown(audit),
        "GRAPHITI_WRITE_PATH_COVERAGE.json": coverage_document,
        "MEG_RUNTIME_PASSIVE_EQUIVALENCE.json": passive_document,
        "MEG_RUNTIME_INSTRUMENTATION_QUALIFICATION.json": qualification_document,
        "MEG_RUNTIME_INSTRUMENTATION_DECISION.md": _decision_markdown(
            qualification_document
        ),
    }


__all__ = ["build_runtime_instrumentation_documents"]
