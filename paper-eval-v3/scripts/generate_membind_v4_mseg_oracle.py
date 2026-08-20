#!/usr/bin/env python3
"""Generate the fail-closed MemBind v4 MSEG offline Oracle Gate bundle.

The generator reads only existing local code and sealed traces.  It makes no
network request, starts no service, creates no namespace, and never writes a
runtime database.  Missing fine-grained causal evidence remains explicitly
NOT_OBSERVABLE.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file  # noqa: E402
from paper_eval.membind_v4.mseg.reducer import (  # noqa: E402
    audit_llm_trace_observability,
)


HISTORY_ID = "07741c45"
PILOT_ROOT = (
    PROJECT
    / "artifacts/paper_eval/membind_v31/optimization/pilots"
    / "membind-v31-opt-w4-20260818-001"
)
V4_ROOT = PROJECT / "artifacts/paper_eval/membind_v4"
DEFAULT_OUTPUT_ROOT = V4_ROOT / "mseg"
GRAPHITI_SITE = (
    PROJECT.parent / "membind-validation/.venv/lib/python3.12/site-packages"
)
GRAPHITI_ROOT = GRAPHITI_SITE / "graphiti_core"
GRAPHITI_VERSION = "0.29.3"
SEALED_HASHES = {
    "V4_CONFLICT_OFFLINE_REPLAY.json": (
        "d003baeca9858cbe91ec11b0d0216741aa2cc32529536bb5616ebe0d412c0834"
    ),
    "V4_READY_TASK_OPPORTUNITY_PROFILE.json": (
        "5f33ca8c7e15627684e62b2f4e79b8a32ccb7d07c07a93e24c4da810b5081d65"
    ),
    "V4_PARALLELISM_FUNNEL.json": (
        "9735680b445e2aa93c623fce2314e3ea1ccf75254a2a772f0b9ef5c9ca0045a7"
    ),
    "V4_FINAL_DECISION.md": (
        "7e971c32a278578ceff1ddbc0ca13486048e428c87d79c4a5d7fb4bcdf97b941"
    ),
}
GRAPHITI_FILES = {
    "graphiti.py": GRAPHITI_ROOT / "graphiti.py",
    "node_operations.py": GRAPHITI_ROOT / "utils/maintenance/node_operations.py",
    "edge_operations.py": GRAPHITI_ROOT / "utils/maintenance/edge_operations.py",
    "bulk_utils.py": GRAPHITI_ROOT / "utils/bulk_utils.py",
}
REQUIRED_CODE_MARKERS = {
    "graphiti.py": (
        "async def add_episode(",
        "await extract_nodes(",
        "await resolve_extracted_nodes(",
        "await self._extract_and_resolve_edges(",
        "await extract_attributes_from_nodes(",
        "await self._process_episode_data(",
    ),
    "node_operations.py": (
        "async def resolve_extracted_nodes(",
        "prompt_name='dedupe_nodes.nodes'",
        "prompt_name='extract_nodes.extract_attributes'",
        "prompt_name = 'extract_nodes.extract_summaries_batch'",
    ),
    "edge_operations.py": (
        "async def resolve_extracted_edges(",
        "prompt_name='extract_edges.edge'",
        "prompt_name='dedupe_edges.resolve_edge'",
        "prompt_name='extract_edges.extract_timestamps'",
    ),
    "bulk_utils.py": (
        "async def add_nodes_and_edges_bulk(",
        "await session.execute_write(",
        "async def add_nodes_and_edges_bulk_tx(",
    ),
}
NOT_OBSERVABLE = "NOT_OBSERVABLE"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError(f"json_unreadable:{path}") from None
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _verify_graphiti_installation() -> dict[str, object]:
    metadata = GRAPHITI_SITE / f"graphiti_core-{GRAPHITI_VERSION}.dist-info/METADATA"
    try:
        metadata_text = metadata.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ValueError("graphiti_metadata_unreadable") from None
    if f"Version: {GRAPHITI_VERSION}\n" not in metadata_text:
        raise ValueError("graphiti_version_mismatch")
    source_hashes: dict[str, str] = {}
    for name, path in GRAPHITI_FILES.items():
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise ValueError(f"graphiti_source_unreadable:{name}") from None
        missing = [marker for marker in REQUIRED_CODE_MARKERS[name] if marker not in source]
        if missing:
            raise ValueError(f"graphiti_code_marker_missing:{name}:{missing[0]}")
        source_hashes[name] = sha256_file(path)
    return {
        "version": GRAPHITI_VERSION,
        "metadata_file_sha256": sha256_file(metadata),
        "source_file_sha256s": source_hashes,
    }


def _verify_sealed_artifacts() -> dict[str, object]:
    rows: dict[str, object] = {}
    for name, expected in SEALED_HASHES.items():
        observed = sha256_file(V4_ROOT / name)
        if observed != expected:
            raise ValueError(f"sealed_artifact_hash_mismatch:{name}")
        rows[name] = {
            "expected_sha256": expected,
            "observed_sha256": observed,
            "unchanged": True,
        }
    return rows


def _with_payload(body: dict[str, Any]) -> dict[str, Any]:
    result = dict(body)
    result["payload_sha256"] = payload_sha256(result)
    return result


def _input_binding(
    graphiti: dict[str, object],
    observability: dict[str, object],
    sealed: dict[str, object],
) -> dict[str, object]:
    return {
        "history_id": HISTORY_ID,
        "source_scope": "0..11",
        "pilot_root": str(PILOT_ROOT.relative_to(PROJECT)),
        "pilot_manifest_file_sha256": sha256_file(PILOT_ROOT / "manifest.json"),
        "pilot_result_file_sha256": sha256_file(PILOT_ROOT / "result.json"),
        "pilot_events_file_sha256": sha256_file(PILOT_ROOT / "events.jsonl"),
        "pilot_queue_file_sha256": sha256_file(PILOT_ROOT / "queue.jsonl"),
        "pilot_llm_file_sha256": observability["source_trace_sha256"],
        "graphiti": graphiti,
        "sealed_artifacts": sealed,
    }


def _scope() -> dict[str, object]:
    return {
        "analysis_mode": "OFFLINE_READ_ONLY",
        "network_calls": 0,
        "services_started": 0,
        "namespaces_created": 0,
        "persistent_writes": 0,
        "scheduler_implemented": False,
        "runtime_instrumentation_installed": False,
        "live_candidate_authorized": False,
        "formal_main_table_eligible": False,
        "stop_v4_node_resolve_preserved": True,
        "no_stage_scheduler_choice_preserved": True,
    }


def _not_observable_oracle(name: str, reason: str) -> dict[str, object]:
    return {
        "name": name,
        "status": NOT_OBSERVABLE,
        "makespan_ns": NOT_OBSERVABLE,
        "goodput_episodes_per_second": NOT_OBSERVABLE,
        "p50_freshness_ns": NOT_OBSERVABLE,
        "p95_freshness_ns": NOT_OBSERVABLE,
        "reason": reason,
    }


def _operator_surface() -> list[dict[str, object]]:
    return [
        {
            "operator_role": "PreviousEpisodeLookup",
            "caller": "Graphiti.add_episode",
            "callee": "Graphiti.retrieve_episodes / EpisodicNode.get_by_uuids",
            "inputs": "reference_time, group_id, source or explicit previous UUIDs",
            "outputs": "previous_episodes",
            "execution": "DETERMINISTIC_DB_READ",
            "persistent_read": True,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "EpisodeMaterialize",
            "caller": "Graphiti.add_episode",
            "callee": "EpisodicNode.get_by_uuid or local EpisodicNode constructor",
            "inputs": "episode UUID or arrival payload",
            "outputs": "episode",
            "execution": "CONDITIONAL_DB_READ_OR_LOCAL",
            "persistent_read": "CONDITIONAL",
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "EntityExtract",
            "caller": "Graphiti.add_episode",
            "callee": "extract_nodes -> _call_extraction_llm",
            "inputs": "episode, previous episodes, entity schema",
            "outputs": "extracted EntityNode values and episode attribution",
            "execution": "LLM_REQUEST",
            "prompt_names": [
                "extract_nodes.extract_message",
                "extract_nodes.extract_text",
                "extract_nodes.extract_json",
            ],
            "persistent_read": False,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "NodeCandidateLookup",
            "caller": "resolve_extracted_nodes",
            "callee": "_collect_candidate_nodes -> node_similarity_search",
            "inputs": "extracted node names, embeddings, group namespace",
            "outputs": "ordered existing EntityNode candidates",
            "execution": "EMBEDDING_AND_DETERMINISTIC_DB_READ",
            "persistent_read": True,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "NodeResolveDeterministic",
            "caller": "resolve_extracted_nodes",
            "callee": "_resolve_with_similarity",
            "inputs": "extracted node and candidate indexes",
            "outputs": "resolved UUID map or unresolved index",
            "execution": "DETERMINISTIC",
            "persistent_read": False,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "NodeResolveLLM",
            "caller": "resolve_extracted_nodes",
            "callee": "_resolve_with_llm",
            "inputs": "unresolved extracted nodes, candidates, episode context",
            "outputs": "resolved nodes, UUID map, duplicate pairs",
            "execution": "CONDITIONAL_LLM_REQUEST",
            "prompt_names": ["dedupe_nodes.nodes"],
            "persistent_read": False,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "EdgeExtract",
            "caller": "Graphiti._extract_and_resolve_edges",
            "callee": "extract_edges",
            "inputs": "episode, extracted entities, previous episodes, edge schema",
            "outputs": "extracted EntityEdge values",
            "execution": "LLM_REQUEST",
            "prompt_names": ["extract_edges.edge"],
            "persistent_read": False,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "EdgePointerRemap",
            "caller": "Graphiti._extract_and_resolve_edges",
            "callee": "resolve_edge_pointers",
            "inputs": "extracted edges and resolved node UUID map",
            "outputs": "edges with resolved endpoint UUIDs",
            "execution": "DETERMINISTIC",
            "persistent_read": False,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "EdgeCandidateLookup",
            "caller": "resolve_extracted_edges",
            "callee": "EntityEdge.get_between_nodes and search",
            "inputs": "resolved endpoints, fact embedding, group namespace",
            "outputs": "duplicate and invalidation candidates",
            "execution": "EMBEDDING_AND_DETERMINISTIC_DB_READ",
            "persistent_read": True,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "EdgeResolveLLM",
            "caller": "resolve_extracted_edges",
            "callee": "_resolve_extracted_edge",
            "inputs": "new edge, duplicate candidates, invalidation candidates",
            "outputs": "duplicate choice and contradicted candidate indexes",
            "execution": "CONDITIONAL_LLM_REQUEST",
            "prompt_names": ["dedupe_edges.resolve_edge"],
            "persistent_read": False,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "TemporalExtract",
            "caller": "_resolve_extracted_edge",
            "callee": "_extract_edge_timestamps",
            "inputs": "new edge fact and episode reference time",
            "outputs": "valid_at and invalid_at",
            "execution": "CONDITIONAL_LLM_REQUEST",
            "prompt_names": ["extract_edges.extract_timestamps"],
            "persistent_read": False,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "EdgeAttributeExtract",
            "caller": "_resolve_extracted_edge",
            "callee": "extract_edges.extract_attributes prompt path",
            "inputs": "resolved edge and configured edge schema",
            "outputs": "typed edge attributes",
            "execution": "CONDITIONAL_LLM_REQUEST",
            "prompt_names": ["extract_edges.extract_attributes"],
            "persistent_read": False,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "EdgeInvalidation",
            "caller": "_resolve_extracted_edge",
            "callee": "resolve_edge_contradictions and timestamp comparisons",
            "inputs": "resolved edge and invalidation candidates",
            "outputs": "invalidated existing edges",
            "execution": "DETERMINISTIC",
            "persistent_read": False,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "EntityAttributeExtract",
            "caller": "extract_attributes_from_nodes",
            "callee": "_extract_entity_attributes",
            "inputs": "resolved node, episode context, configured entity schema",
            "outputs": "merged typed entity attributes",
            "execution": "CONDITIONAL_LLM_REQUEST_PER_NODE",
            "prompt_names": ["extract_nodes.extract_attributes"],
            "persistent_read": False,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "EntitySummary",
            "caller": "extract_attributes_from_nodes",
            "callee": "_extract_entity_summaries_batch",
            "inputs": "resolved nodes, new edge facts, episode context",
            "outputs": "updated node summaries",
            "execution": "CONDITIONAL_BATCHED_LLM_OR_DETERMINISTIC_APPEND",
            "prompt_names": [
                "extract_nodes.extract_summaries_batch",
                "extract_nodes.extract_entity_summaries_from_episodes",
            ],
            "persistent_read": False,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "EntityEmbedding",
            "caller": "extract_attributes_from_nodes",
            "callee": "create_entity_node_embeddings",
            "inputs": "hydrated entity nodes",
            "outputs": "name embeddings",
            "execution": "EMBEDDING_REQUEST",
            "persistent_read": False,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "EpisodicEffectBuild",
            "caller": "Graphiti._process_episode_data",
            "callee": "build_episodic_edges and local effect materialization",
            "inputs": "episode, hydrated nodes, resolved and invalidated edges",
            "outputs": "episodic edges and persistent effect batch",
            "execution": "DETERMINISTIC",
            "persistent_read": False,
            "persistent_write": False,
            "can_block_publication": True,
        },
        {
            "operator_role": "PersistentWritePublication",
            "caller": "Graphiti._process_episode_data",
            "callee": "add_nodes_and_edges_bulk -> add_nodes_and_edges_bulk_tx",
            "inputs": "episode, episodic edges, entity nodes, entity edges",
            "outputs": "committed graph state",
            "execution": "DETERMINISTIC_WRITE_TRANSACTION_WITH_EMBEDDING_GUARDS",
            "persistent_read": False,
            "persistent_write": True,
            "can_block_publication": True,
        },
    ]


def _build_documents() -> tuple[dict[str, dict[str, Any]], dict[str, object]]:
    graphiti = _verify_graphiti_installation()
    sealed = _verify_sealed_artifacts()
    observability = audit_llm_trace_observability(
        PILOT_ROOT / "llm.jsonl",
        history_id=HISTORY_ID,
    )
    result = _read_json(PILOT_ROOT / "result.json")
    if result.get("history_id") != HISTORY_ID or result.get("source_count") != 12:
        raise ValueError("pilot_result_scope_mismatch")
    performance = result.get("performance")
    if not isinstance(performance, dict):
        raise ValueError("pilot_performance_missing")
    binding = _input_binding(graphiti, observability, sealed)
    scope = _scope()
    reason = (
        "The sealed W=4 trace has no direct operator role/parent identity, operator-ready "
        "or materialization time, exact memory version, dependency edge, effect scope, "
        "deterministic operator, persistent effect, or publication-instance records."
    )

    graph = _with_payload(
        {
            "schema_version": "membind.paper-eval-v4.mseg-graph.v1",
            "status": "NOT_RECOVERABLE_FROM_SEALED_TRACE",
            "scope": scope,
            "input_binding": binding,
            "mseg_recovered": False,
            "operator_instance_count": 0,
            "persistent_effect_instance_count": NOT_OBSERVABLE,
            "publication_instance_count": NOT_OBSERVABLE,
            "node_count": NOT_OBSERVABLE,
            "edge_count_by_type": {
                "DATA_DEP": NOT_OBSERVABLE,
                "EFFECT_CONFLICT_DEP": NOT_OBSERVABLE,
                "PUBLICATION_DEP": NOT_OBSERVABLE,
                "VERSION_DEP": NOT_OBSERVABLE,
            },
            "operator_surface_role_count": len(_operator_surface()),
            "operator_surface_role_count_semantics": (
                "Code-proven possible roles, not trace-observed instances"
            ),
            "reason": reason,
        }
    )
    dependency = _with_payload(
        {
            "schema_version": "membind.paper-eval-v4.mseg-dependency-summary.v1",
            "status": NOT_OBSERVABLE,
            "scope": scope,
            "input_binding": binding,
            "dependency_states": {
                "CERTIFIED_READY_fraction": NOT_OBSERVABLE,
                "CERTIFIED_BLOCKED_fraction": NOT_OBSERVABLE,
                "UNRESOLVED_fraction": NOT_OBSERVABLE,
            },
            "edge_count_by_type": graph["edge_count_by_type"],
            "earliest_certified_start_recoverable": False,
            "unknown_policy": "FAIL_CLOSED",
            "reason": reason,
        }
    )
    late_bound = _with_payload(
        {
            "schema_version": "membind.paper-eval-v4.mseg-late-bound-analysis.v1",
            "status": NOT_OBSERVABLE,
            "scope": scope,
            "input_binding": binding,
            "unresolved_dependency_fraction": NOT_OBSERVABLE,
            "state_residence_time_ns": NOT_OBSERVABLE,
            "transition_reason_counts": NOT_OBSERVABLE,
            "h2_late_bound_dependency": "NOT_EVALUABLE",
            "interpretation": (
                "Not evaluable because the sealed trace does not record the causal "
                "variables needed to test late-bound dependency; this does not prove "
                "that late-bound dependency is absent in Graphiti."
            ),
            "reason": reason,
        }
    )
    critical_path = _with_payload(
        {
            "schema_version": "membind.paper-eval-v4.mseg-publication-critical-path.v1",
            "status": NOT_OBSERVABLE,
            "scope": scope,
            "input_binding": binding,
            "publication_critical_path_length_ns": NOT_OBSERVABLE,
            "critical_path_service_by_role_ns": NOT_OBSERVABLE,
            "total_certified_hideable_ns": NOT_OBSERVABLE,
            "total_speculative_hideable_ns": NOT_OBSERVABLE,
            "publication_slack_by_operator": NOT_OBSERVABLE,
            "reason": reason,
        }
    )
    conflict = _with_payload(
        {
            "schema_version": "membind.paper-eval-v4.mseg-conflict-oracle.v1",
            "status": NOT_OBSERVABLE,
            "scope": scope,
            "input_binding": binding,
            "cross_source_operator_pairs": NOT_OBSERVABLE,
            "certified_non_conflicting_fraction": NOT_OBSERVABLE,
            "conflicting_fraction": NOT_OBSERVABLE,
            "unknown_fraction": NOT_OBSERVABLE,
            "max_legal_cross_source_width": NOT_OBSERVABLE,
            "projected_makespan_reduction_ns": NOT_OBSERVABLE,
            "stop_mco": True,
            "unknown_policy": "UNKNOWN_IS_NOT_NON_CONFLICTING",
            "reason": reason,
        }
    )
    validated = _with_payload(
        {
            "schema_version": "membind.paper-eval-v4.mseg-validated-execution-oracle.v1",
            "status": NOT_OBSERVABLE,
            "scope": scope,
            "input_binding": binding,
            "validatable_operator_count": NOT_OBSERVABLE,
            "hit": NOT_OBSERVABLE,
            "miss": NOT_OBSERVABLE,
            "hit_rate": NOT_OBSERVABLE,
            "wasted_calls": NOT_OBSERVABLE,
            "wasted_tokens": NOT_OBSERVABLE,
            "wasted_service_ns": NOT_OBSERVABLE,
            "hidden_critical_service_ns": NOT_OBSERVABLE,
            "backend_interference": NOT_OBSERVABLE,
            "optimistic_interference_assumption": "I=0 NOT COMPUTED WITHOUT MSEG",
            "reason": reason,
        }
    )
    o0 = {
        "name": "O0_CURRENT",
        "status": "OBSERVED_DIAGNOSTIC_ONLY_NON_MERGEABLE",
        "makespan_ns": performance.get("makespan_ns"),
        "goodput_episodes_per_second": performance.get(
            "goodput_episodes_per_second"
        ),
        "p50_freshness_ns": performance.get("p50_freshness_ns"),
        "p95_freshness_ns": performance.get("p95_freshness_ns"),
        "published_episode_count": performance.get("published_episode_count"),
        "resource_envelope": {
            "global_llm_admission_k": result.get("global_llm_admission_k"),
            "compile_workers": result.get("compile_workers"),
            "bind_workers": result.get("bind_workers"),
            "lookahead": result.get("lookahead"),
        },
    }
    comparison = _with_payload(
        {
            "schema_version": "membind.paper-eval-v4.mseg-oracle-comparison.v1",
            "status": "ORACLE_GATE_STOP",
            "scope": scope,
            "input_binding": binding,
            "trace_observability": observability,
            "mseg_recovered": False,
            "structural_metrics": {
                "max_legal_ready_width": NOT_OBSERVABLE,
                "p_legal_width_ge_2": NOT_OBSERVABLE,
                "p_legal_width_ge_4": NOT_OBSERVABLE,
                "legal_choice_epochs": NOT_OBSERVABLE,
                "mean_legal_ready_set_size": NOT_OBSERVABLE,
            },
            "oracles": {
                "O0_CURRENT": o0,
                "O1_CERTIFIED_EARLY": _not_observable_oracle(
                    "O1_CERTIFIED_EARLY", reason
                ),
                "O2_CONFLICT_ORDERED": _not_observable_oracle(
                    "O2_CONFLICT_ORDERED", reason
                ),
                "O3_VALIDATED_EXECUTION": _not_observable_oracle(
                    "O3_VALIDATED_EXECUTION", reason
                ),
                "O4_PUBLICATION_CRITICAL": _not_observable_oracle(
                    "O4_PUBLICATION_CRITICAL", reason
                ),
            },
            "gain_attribution": {
                "Gain_StateCut_O1_minus_O0": NOT_OBSERVABLE,
                "Gain_ConflictOrdering_O2_minus_O1": NOT_OBSERVABLE,
                "Gain_ValidatedExecution_O3_minus_O2": NOT_OBSERVABLE,
                "Gain_CriticalityAdmission_O4_minus_O3": NOT_OBSERVABLE,
            },
            "hypothesis_status": {
                "H1_OVER_SERIALIZATION": "NOT_EVALUABLE",
                "H2_LATE_BOUND_DEPENDENCY": "NOT_EVALUABLE",
                "H3_CRITICALITY_HETEROGENEITY": "NOT_EVALUABLE",
                "H4_SEMANTIC_ADMISSION_OPPORTUNITY": "NOT_EVALUABLE",
            },
            "decision": {
                "dominant_gain_source": "NOT_EVALUABLE",
                "instrumentation_only_qualification_authorized": True,
                "live_authorized": False,
                "mseg_recovered": False,
                "new_mechanism_authorized": False,
                "new_scheduler_authorized": False,
                "next_action": "INSTRUMENTATION_ONLY_QUALIFICATION",
                "next_mechanism": "STOP_V4_FINE_GRAINED_ON_EXISTING_TRACE",
                "q0_measurement_authorized": True,
                "root_cause": "FINE_GRAINED_CAUSAL_IDENTITY_NOT_RECORDED",
                "status": "STOP_V4_FINE_GRAINED_ON_EXISTING_TRACE",
            },
        }
    )
    return (
        {
            "MSEG_GRAPH.json": graph,
            "MSEG_DEPENDENCY_SUMMARY.json": dependency,
            "MSEG_LATE_BOUND_ANALYSIS.json": late_bound,
            "MSEG_PUBLICATION_CRITICAL_PATH.json": critical_path,
            "MSEG_CONFLICT_ORACLE.json": conflict,
            "MSEG_VALIDATED_EXECUTION_ORACLE.json": validated,
            "MSEG_ORACLE_COMPARISON.json": comparison,
        },
        {
            "graphiti": graphiti,
            "observability": observability,
            "sealed": sealed,
            "operator_surface": _operator_surface(),
            "comparison": comparison,
        },
    )


def _render_operator_audit(context: dict[str, object]) -> str:
    graphiti = context["graphiti"]
    observability = context["observability"]
    surface = context["operator_surface"]
    assert isinstance(graphiti, dict)
    assert isinstance(observability, dict)
    assert isinstance(surface, list)
    table_rows = []
    for row in surface:
        assert isinstance(row, dict)
        table_rows.append(
            "| {operator_role} | {caller} | {callee} | {execution} | {persistent_read} | "
            "{persistent_write} | {can_block_publication} |".format(**row)
        )
    field_status = observability["field_coverage"]
    assert isinstance(field_status, dict)
    missing_fields = [
        name
        for name, value in field_status.items()
        if isinstance(value, dict) and value.get("status") == NOT_OBSERVABLE
    ]
    return """# MSEG Operator Audit

## Scope and Binding

This is a read-only audit of the installed Graphiti 0.29.3 production
`Graphiti.add_episode` path and the sealed v3.1 W=4 pilot for
`history=07741c45`, sources `0..11`. It starts no service, sends no model or
embedding request, creates no namespace, and performs no persistent write.

Graphiti source hashes:

```json
%s
```

## Production Path

The code-proven control flow is:

```text
previous-episode read
  -> episode materialization
  -> EntityExtract
  -> NodeCandidateLookup
  -> deterministic NodeResolve, then conditional dedupe_nodes.nodes
  -> EdgeExtract
  -> EdgePointerRemap
  -> EdgeCandidateLookup
  -> deterministic/LLM EdgeResolve
  -> conditional Temporal and EdgeAttribute extraction
  -> deterministic invalidation
  -> conditional EntityAttribute and batched Summary work
  -> embeddings and episodic-effect construction
  -> add_nodes_and_edges_bulk transaction
  -> publication complete
```

`update_communities` defaults to false and is outside the frozen primary path.
The audit follows actual conditional code: a possible role is not asserted to
occur in every episode.

## Code-Proven Operator Surface

| Operator role | Caller | Callee | Execution | Persistent read | \
Persistent write | Blocks publication |
|---|---|---|---|---:|---:|---:|
%s

This table proves possible production operators and their control/dataflow
position. It does not prove per-request instances, timing, memory versions, or
effect scopes in the sealed pilot.

`Blocks publication` means the current production control flow awaits that
invoked operation before the bulk write can return. It does not mean the
operation is on a measured publication critical path. Critical-path membership
and slack require instance-level dependency and timing evidence, which is
`NOT_OBSERVABLE` here.

Prompt roles proven in Graphiti include `extract_nodes.extract_message`,
`dedupe_nodes.nodes`, `extract_edges.edge`, `dedupe_edges.resolve_edge`,
`extract_edges.extract_timestamps`, `extract_edges.extract_attributes`,
`extract_nodes.extract_attributes`, and
`extract_nodes.extract_summaries_batch`. These strings exist at the live
`generate_response(..., prompt_name=...)` call sites.

## Trace-Observed Instances

The sealed request trace directly observes %s requests: %s COMPILE and %s
FRONTIER. All %s have complete client submit/start/terminal lifecycles.

It does **not** persist `prompt_name`, `operator_role`, `operator_id`, parent
operator/Bind identity, operator ready/materialization timestamps, exact memory
version, publication frontier, dependency edges, read/effect scope,
deterministic operator instances, persistent effects, or publication instances.

Fields with zero direct coverage:

```text
%s
```

Therefore trace-observed fine-grained operator instances are
`NOT_OBSERVABLE`. The role count and same-role width are also
`NOT_OBSERVABLE`.

## Classification Boundary

No role is inferred from request order, prompt/token length, prefix/cache hash,
or prompt similarity. `client running` is neither vLLM batch membership nor GPU
execution. The older `ROLE_PROFILE.json` is an initialization reference from a
different logical-call trace and cannot attribute these W=4 requests.

The current wrapper receives Graphiti keyword arguments at
`AdmittedLLMClientV31._execute`, but the frozen event projection intentionally
omits `prompt_name`. Modifying the frozen v3.1 runtime or running a new
instrumented candidate is outside this Oracle Gate.

## Audit Decision

The production operator *surface* is recovered from code. The target pilot's
operator *instances and causal graph* are not recoverable. A scientific MSEG,
late-bound analysis, conflict oracle, publication critical path, and O1-O4
comparison cannot be constructed without fabricating evidence.
""" % (
        json.dumps(graphiti, indent=2, sort_keys=True),
        "\n".join(table_rows),
        observability["request_count"],
        observability["request_kind_counts"]["COMPILE"],
        observability["request_kind_counts"]["FRONTIER"],
        observability["complete_client_lifecycle_count"],
        "\n".join(sorted(missing_fields)),
    )


def _render_novelty_audit() -> str:
    return """# MSEG Novelty Audit

## Gate Status

No novelty claim is authorized from this sealed trace because fine-grained
operator identity, progressive dependency state, effect scope, and publication
criticality are not observable. The distinctions below define the proposed
research boundary; they are not empirical claims from the stopped lane.

## Against Parrot

The proposed MSEG is not merely a Semantic Variable layer for Graphiti. Its
distinct target would be persistent memory versions, dynamically materialized
read/effect scopes, late-bound conflicts, and ordered publication semantics.
Those instance-level properties were not recovered here.

## Against Agentix

The proposed method is not an Agentix-style priority scheduler. Agentix can use
expressed program dependencies; MSEG would first have to discover persistent
memory dependencies progressively, then prioritize publication-critical legal
work. This trace lacks the evidence required to show that opportunity.

## Against ROCOCO

The proposed method is not simply ROCOCO applied to memory. Graphiti entity
identity, candidate sets, merge targets, invalidated edges, attributes, and
summary targets can materialize during semantic execution instead of being a
complete predeclared read/write set. The code path supports this motivation,
but the sealed trace cannot quantify it.

## Against Sarathi-Serve

MSEG would not be another LLM-serving or GPU scheduler. Its boundary would be
which stateful semantic work is legal and useful to expose to the backend;
vLLM would retain batching, KV, prefill/decode, and GPU execution. No backend
batch or GPU claim is made here.

## Result

The conceptual differentiation is coherent, but the Oracle Gate has no causal
trace on which to validate H1-H4 or measure O1-O4. No mechanism or scheduler is
authorized. The sole next action is the bounded, instrumentation-only
`V4-MSEG-Q0` measurement; the existing trace remains stopped at
`STOP_V4_FINE_GRAINED_ON_EXISTING_TRACE`.
"""


def _render_final_decision(context: dict[str, object]) -> str:
    comparison = context["comparison"]
    assert isinstance(comparison, dict)
    o0 = comparison["oracles"]["O0_CURRENT"]
    return """# MSEG Final Decision

## Required Result

```text
STATUS: STOP_V4_FINE_GRAINED_ON_EXISTING_TRACE

MSEG_RECOVERED: no

ROOT_CAUSE: FINE_GRAINED_CAUSAL_IDENTITY_NOT_RECORDED

H1_OVER_SERIALIZATION: NOT_EVALUABLE

H2_LATE_BOUND_DEPENDENCY: NOT_EVALUABLE

H3_CRITICALITY_HETEROGENEITY: NOT_EVALUABLE

H4_SEMANTIC_ADMISSION_OPPORTUNITY: NOT_EVALUABLE

MAX_LEGAL_READY_WIDTH: NOT_OBSERVABLE

P_LEGAL_WIDTH_GE_2: NOT_OBSERVABLE

UNRESOLVED_DEPENDENCY_FRACTION: NOT_OBSERVABLE

CONFLICT_FREE_CROSS_SOURCE_FRACTION: NOT_OBSERVABLE

TOTAL_CERTIFIED_HIDEABLE_MS: NOT_OBSERVABLE

TOTAL_VALIDATABLE_HIDEABLE_MS: NOT_OBSERVABLE

O0_CURRENT: %s ns

O1_CERTIFIED_EARLY: NOT_OBSERVABLE

O2_CONFLICT_ORDERED: NOT_OBSERVABLE

O3_VALIDATED_EXECUTION: NOT_OBSERVABLE

O4_PUBLICATION_CRITICAL: NOT_OBSERVABLE

DOMINANT_GAIN_SOURCE: NOT_EVALUABLE

NEXT_ACTION: INSTRUMENTATION_ONLY_QUALIFICATION

NEW_MECHANISM_AUTHORIZED: no

NEW_SCHEDULER_AUTHORIZED: no

Q0_MEASUREMENT_AUTHORIZED: yes

SEALED_ARTIFACTS_UNCHANGED: yes
```

## Evidence Interpretation

The four hypotheses are `NOT_EVALUABLE`: the target trace did not record the
causal variables required to test them. This is not evidence that the
underlying Graphiti workflow lacks late-bound dependencies or fine-grained
opportunity. Absence of observability is not absence of opportunity.

The O0 value is the existing 12-source diagnostic pilot, not a new run and not
formal main-table evidence. O1-O4 are not reported as equal to O0 or as zero
gain; they are `NOT_OBSERVABLE`. Substituting role-profile averages, prompt
length, request order, or unlimited resources would create a false oracle.

## Gate Consequence

The mechanism prerequisites fail at fine-grained identity and MSEG recovery,
so legal width, hideable critical time, incremental mechanism gain, and a
complete correctness contract cannot be established. No scheduler, M-CO
runtime, speculation runtime, or admission candidate is authorized. One fresh
namespace may be used solely for `V4-MSEG-Q0`, with identical v3.1 execution
policy and an observability overlay. `STOP_V4_NODE_RESOLVE` and
`NO_STAGE_SCHEDULER_CHOICE` remain sealed.
""" % o0["makespan_ns"]


def _write_trace_barrier(path: Path, observability: dict[str, object]) -> None:
    record = {
        "schema_version": "membind.paper-eval-v4.mseg-fine-grained-trace.v1",
        "record_type": "OBSERVABILITY_BARRIER",
        "history_id": HISTORY_ID,
        "source_scope": "0..11",
        "source_llm_trace_sha256": observability["source_trace_sha256"],
        "source_request_count": observability["request_count"],
        "operator_instance_count": 0,
        "operator_rows_fabricated": False,
        "status": "NOT_RECOVERABLE_FROM_SEALED_TRACE",
        "blocking_reasons": observability["blocking_reasons"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
    )
    args = parser.parse_args(argv)
    output_root = args.output_root.resolve()
    documents, context = _build_documents()
    output_root.mkdir(parents=True, exist_ok=True)
    for name, document in documents.items():
        atomic_write_json(output_root / name, document)
    observability = context["observability"]
    assert isinstance(observability, dict)
    _write_trace_barrier(output_root / "MSEG_FINE_GRAINED_TRACE.jsonl", observability)
    (output_root / "MSEG_OPERATOR_AUDIT.md").write_text(
        _render_operator_audit(context),
        encoding="utf-8",
    )
    (output_root / "MSEG_NOVELTY_AUDIT.md").write_text(
        _render_novelty_audit(),
        encoding="utf-8",
    )
    (output_root / "MSEG_FINAL_DECISION.md").write_text(
        _render_final_decision(context),
        encoding="utf-8",
    )
    comparison = documents["MSEG_ORACLE_COMPARISON.json"]
    print(
        json.dumps(
            {
                "output_root": str(output_root),
                "payload_sha256": comparison["payload_sha256"],
                "mseg_recovered": False,
                "next_action": "INSTRUMENTATION_ONLY_QUALIFICATION",
                "next_mechanism": "STOP_V4_FINE_GRAINED_ON_EXISTING_TRACE",
                "live_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
