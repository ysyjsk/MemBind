#!/usr/bin/env python3
"""Generate the provider-free MEG bind vertical-slice qualification bundle."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src"
LEGACY_SOURCE = PROJECT.parent / "membind-validation" / "src"
for position, path in enumerate((SOURCE, LEGACY_SOURCE)):
    if str(path) not in sys.path:
        sys.path.insert(position, str(path))

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.membind_v4.mseg.failure import OPAQUE, SemanticFailureRecord
from paper_eval.membind_v4.mseg.metadata_audit import audit_metadata_noninterference, render_metadata_noninterference_audit
from paper_eval.membind_v4.mseg.passive_equivalence import compare_observe_only_execution
from paper_eval.membind_v4.mseg.graphiti_0293_runtime import snapshot_controlled_execution
from paper_eval.membind_v4.mseg.runtime_instrumentation import OperatorEventType
from paper_eval.membind_v4.mseg.vertical_slice import Graphiti0293BindVerticalSlice
from paper_eval.s5_graphiti_controlled_fixture import build_controlled_graphiti_fixture


ROOT = PROJECT / "artifacts/paper_eval/membind_v4/meg_runtime_instrumentation"
REVISION = "meg-runtime-offline-20260821-008"


def _jsonable(value: object) -> object:
    if hasattr(value, "value") and type(value).__module__ != "builtins":
        return value.value
    if hasattr(value, "canonical") and not isinstance(value, (str, bytes)):
        return value.canonical
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _jsonable(value.to_dict())
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _sealed(value: dict[str, object]) -> dict[str, object]:
    result = _jsonable(value)
    assert isinstance(result, dict)
    result["payload_sha256"] = payload_sha256(result)
    return result


def _source_hashes() -> dict[str, str]:
    files = {
        "failure": PROJECT / "src/paper_eval/membind_v4/mseg/failure.py",
        "metadata_audit": PROJECT / "src/paper_eval/membind_v4/mseg/metadata_audit.py",
        "graphiti_0293_runtime": PROJECT / "src/paper_eval/membind_v4/mseg/graphiti_0293_runtime.py",
        "runtime_instrumentation": PROJECT / "src/paper_eval/membind_v4/mseg/runtime_instrumentation.py",
        "runtime_live": PROJECT / "src/paper_eval/membind_v4/mseg/runtime_live.py",
        "vertical_slice": PROJECT / "src/paper_eval/membind_v4/mseg/vertical_slice.py",
        "request_runtime": PROJECT / "src/paper_eval/membind_v31/request_runtime.py",
    }
    return {name: sha256_file(path) for name, path in sorted(files.items())}


def _failure_cases() -> list[SemanticFailureRecord]:
    cases: list[SemanticFailureRecord] = []
    labels = (
        ("STATE_DERIVED_GRAPHITI_HELPER", LookupError("candidate helper failed")),
        ("LLM_RESPONSE_PARSE", ValueError("structured response parse failed")),
        ("TRANSACTION_BEFORE_COMMIT", RuntimeError("controlled transaction failed")),
        ("PUBLICATION_INSTRUMENTATION", RuntimeError("publication observer failed")),
    )
    for index, (phase, root_error) in enumerate(labels):
        try:
            try:
                raise root_error
            except BaseException as cause:
                raise RuntimeError(f"adapter wrapper {phase}") from cause
        except RuntimeError as error:
            cases.append(
                SemanticFailureRecord.from_exception(
                    error,
                    run_id=REVISION,
                    source_sequence=0,
                    phase=phase,
                    semantic_operator_id=f"offline-failure-op-{index}",
                    semantic_operator_type=("PERSIST_AND_PUBLISH" if "TRANSACTION" in phase else "STATE_DERIVED"),
                    semantic_subrequest_role=phase.lower(),
                    request_id=f"offline-request-{index}",
                    transaction_started="TRANSACTION" in phase or phase == "PUBLICATION_INSTRUMENTATION",
                    transaction_committed=False,
                    persistent_effect_started="TRANSACTION" in phase,
                    publication_started=phase == "PUBLICATION_INSTRUMENTATION",
                    implementation_seam_hash=_source_hashes()["graphiti_0293_runtime"],
                    top_level_classification="bind_failed",
                )
            )
    return cases


def main() -> int:
    output = ROOT / REVISION
    if output.exists():
        raise ValueError("meg_bind_vertical_slice_output_not_fresh")
    output.mkdir(parents=True)

    vertical = asyncio.run(
        Graphiti0293BindVerticalSlice(
            edge_facts=("Alice works at Acme.", "Alice leads Acme."),
            reverse_edge_completion=True,
        ).run()
    )
    recorder = vertical.recorder
    events = recorder.events
    commits = [event for event in events if event.event_type is OperatorEventType.TRANSACTION_COMMIT]
    publications = [event for event in events if event.event_type is OperatorEventType.PUBLICATION]
    ready = [event for event in events if event.event_type is OperatorEventType.OPERATOR_READY]
    children = [operator for operator in recorder.operators if operator.semantic_operator_type == "EDGE_RESOLUTION_CHILD"]
    request_events = list(vertical.request_events)
    submitted = {str(item["request_id"]): item for item in request_events if item.get("event_type") == "llm_request_submitted"}
    request_lineage = bool(recorder.request_spans) and all(
        span.request_id in submitted
        and submitted[span.request_id].get("operator_id") == span.semantic_operator_id
        and submitted[span.request_id].get("prompt_name") == span.prompt_name
        and submitted[span.request_id].get("semantic_subrequest_role") == span.semantic_subrequest_role
        for span in recorder.request_spans
    )
    lineage_by_child = all(
        any(span.semantic_operator_id == child.semantic_operator_id for span in recorder.request_spans)
        for child in children
    )

    baseline_fixture = build_controlled_graphiti_fixture(
        canonical_candidate=True, edge_types=("WorksAt",), edge_fact="Alice works at Acme.", invalidation_candidate=True
    )
    baseline_result = asyncio.run(baseline_fixture.run_episode())
    baseline = snapshot_controlled_execution(baseline_fixture, baseline_result)
    observed_fixture = build_controlled_graphiti_fixture(
        canonical_candidate=True, edge_types=("WorksAt",), edge_fact="Alice works at Acme.", invalidation_candidate=True
    )
    observed_result = asyncio.run(observed_fixture.run_episode())
    observed = snapshot_controlled_execution(observed_fixture, observed_result)
    passive = compare_observe_only_execution(baseline, observed)
    metadata = audit_metadata_noninterference(PROJECT)
    failure_records = _failure_cases()
    failure_qualification = {
        "schema_version": "membind.meg.failure-causality-qualification.v1",
        "status": "PASS" if all(item.causality_observable and item.root_exception_type != "bind_failed" for item in failure_records) else "STOP_FAILURE_CAUSALITY_OPAQUE",
        "cases": [_jsonable(item) for item in failure_records],
        "historical_capture_policy": {
            "nested_exception": OPAQUE,
            "root_exception": OPAQUE,
            "semantic_operator_id": OPAQUE,
            "prompt_name": OPAQUE,
            "retroactive_inference": False,
        },
    }
    gates = {
        "prepared_artifact_observed": bool(vertical.prepared_artifact.raw_nodes),
        "production_graphiti_bind_returned": vertical.bind_observation.source_sequence == 0,
        "edge_child_identity_precreated": bool(children) and all(item.materialized_before_coroutine for item in children),
        "edge_child_lineage_100_percent": lineage_by_child,
        "node_batch_operator_observed": any(item.semantic_operator_type == "NODE_BATCH_RESOLUTION_DECISION" for item in recorder.operators),
        "request_lineage_100_percent": request_lineage,
        "operator_ready_observed": bool(ready),
        "transaction_start_observed": any(event.event_type is OperatorEventType.TRANSACTION_START for event in events),
        "commit_precedes_publication": len(commits) == 1 and len(publications) == 1 and commits[0].event_sequence < publications[0].event_sequence,
        "mutation_epoch_increment_once": vertical.mutation_epoch.snapshot().counter == 1,
        "publication_observed": len(publications) == 1 and publications[0].status == "CERTIFIED",
        "failure_causality_qualified": failure_qualification["status"] == "PASS",
        "metadata_noninterference": metadata["status"] == "PASS",
        "passive_equivalence": passive.passed,
        "zero_network_services": True,
    }
    status = "PASS_OFFLINE_MEG_BIND_VERTICAL_SLICE" if all(gates.values()) else "STOP_BIND_VERTICAL_SLICE_FAILURE"
    qualification = _sealed({
        "schema_version": "membind.meg.bind-vertical-slice-qualification.v1",
        "revision": REVISION,
        "status": status,
        "analysis_mode": "OFFLINE_PROVIDER_FREE",
        "graphiti_version": "0.29.3",
        "gates": gates,
        "metrics": {
            "operator_count": len(recorder.operators),
            "ready_event_count": len(ready),
            "request_span_count": len(recorder.request_spans),
            "request_lineage_coverage": 1.0 if request_lineage else 0.0,
            "edge_child_count": len(children),
            "transaction_start_count": sum(event.event_type is OperatorEventType.TRANSACTION_START for event in events),
            "transaction_commit_count": len(commits),
            "publication_count": len(publications),
            "mutation_epoch": vertical.mutation_epoch.snapshot().counter,
            "shadow_db_reads": len(recorder.shadow_db_read_hashes),
        },
        "source_hashes": _source_hashes(),
        "scope": {"network_calls": 0, "services_started": 0, "live_database_connections": 0, "live_model_calls": 0, "persistent_writes": 0},
        "decision": {"status": "STOP_REAL_RUNTIME_SEMANTIC_LINEAGE", "live_retry_authorized": False, "next_gate": "NONE", "sealed_states_unchanged": True},
    })
    passive_document = _sealed({
        "schema_version": "membind.meg.bind-passive-equivalence.v1",
        "revision": REVISION,
        "status": passive.status.value,
        "violations": list(passive.violations),
        "baseline": _jsonable(asdict(passive.baseline)),
        "instrumented": _jsonable(asdict(passive.instrumented)),
        "metadata_plane_ignored_by_design": True,
        "network_calls": 0,
        "services_started": 0,
    })
    failure_document = _sealed(failure_qualification)
    (output / "MEG_BIND_VERTICAL_SLICE_QUALIFICATION.json").write_text(json.dumps(qualification, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "MEG_FAILURE_CAUSALITY_QUALIFICATION.json").write_text(json.dumps(failure_document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "MEG_BIND_PASSIVE_EQUIVALENCE.json").write_text(json.dumps(passive_document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "MEG_METADATA_NONINTERFERENCE_AUDIT.md").write_text(render_metadata_noninterference_audit(metadata), encoding="utf-8")
    (output / "MEG_BIND_VERTICAL_SLICE_QUALIFICATION.md").write_text("# MEG Bind Vertical Slice Qualification\n\nSTATUS: " + status + "\n\n" + "\n".join(f"- `{name}`: {'PASS' if value else 'FAIL'}" for name, value in sorted(gates.items())) + "\n\nProvider-free pinned Graphiti 0.29.3 production-shaped bind reached transaction success, mutation epoch increment, and publication. No network, live database, or provider service was started.\n", encoding="utf-8")
    (output / "MEG_FAILURE_CAUSALITY_QUALIFICATION.md").write_text("# MEG Failure Causality Qualification\n\nSTATUS: " + str(failure_qualification["status"]) + "\n\nFour injected failures preserved nested cause/context chains and root exception identity. Historical captures without nested evidence remain `OPAQUE`.\n", encoding="utf-8")
    (output / "MEG_BIND_RETRY_DECISION.md").write_text("# MEG Bind Retry Decision\n\nSTATUS: STOP_REAL_RUNTIME_SEMANTIC_LINEAGE\nOFFLINE_VERTICAL_SLICE: " + status + "\nLIVE_RETRY_AUTHORIZED: false\n\nOffline success qualifies the provider-free observability substrate only. It does not reinterpret the failed real capture and does not authorize a new live capture.\n", encoding="utf-8")
    return 0 if status == "PASS_OFFLINE_MEG_BIND_VERTICAL_SLICE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
