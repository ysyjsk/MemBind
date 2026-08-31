"""Operator-neutral Frozen-V6 cross-snapshot observation primitives.

The module deliberately stops at the native ``_process_episode_data`` seam.
It reuses an already materialized :class:`BuildStageResult` and executes only
the stateful suffix on a supplied authoritative state.  No speculative branch
can publish or write the database.  The higher-level live treatment is not
implemented here; this file is the capture-only substrate used by Phase 3.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import math
import time
from typing import Any, Mapping, Sequence

from .graphiti_observer import (
    BuildStageBindings,
    BuildStageResult,
    GraphitiObserverError,
    _assert_complete_embeddings,
    _default_bindings,
    _ensure_single_partition_provenance,
    _maybe_await,
    canonical_digest,
)


DVSR_CROSS_SNAPSHOT_SCHEMA = "membind.dvsr.cross-snapshot-observer.v1"
DVSR_CUTS = frozenset({"CUT-N", "CUT-D"})
_DAG_PHASES = {
    "CUT-N": ("node-resolution",),
    "CUT-D": ("node-resolution", "edge-extraction", "edge-resolution", "attributes-summary"),
}


def _request_phase(prompt_name: str) -> str | None:
    if prompt_name == "dedupe_nodes.nodes":
        return "node-resolution"
    if prompt_name == "extract_edges.edge":
        return "edge-extraction"
    if prompt_name.startswith("dedupe_edges.") or prompt_name.startswith("extract_edges.extract_"):
        return "edge-resolution"
    if prompt_name == "extract_nodes.extract_summaries_batch":
        return "attributes-summary"
    return None


def _valid_interval(value: Mapping[str, Any], start: str = "start_ns", end: str = "end_ns") -> tuple[int, int] | None:
    left, right = value.get(start), value.get(end)
    if any(isinstance(item, bool) or not isinstance(item, int) for item in (left, right)) or int(right) < int(left):
        return None
    return int(left), int(right)


def _union_duration(intervals: Sequence[tuple[int, int]]) -> int:
    ordered = sorted((int(left), int(right)) for left, right in intervals if right > left)
    if not ordered:
        return 0
    total = 0
    left, right = ordered[0]
    for next_left, next_right in ordered[1:]:
        if next_left <= right:
            right = max(right, next_right)
            continue
        total += right - left
        left, right = next_left, next_right
    return total + right - left


def build_operator_dag(
    capture: Mapping[str, Any],
    *,
    cut: str,
    reusable_request_keys: Sequence[tuple[str, int]] = (),
    reusable_read_keys: Sequence[tuple[str, int]] = (),
) -> dict[str, Any]:
    """Build a conservative request-interval DAG for one nested operator cut.

    Phase intervals are treated as the authoritative wall-clock chain.  Each
    phase is split at request boundaries; a segment is removable only when it
    is covered exclusively by requests whose prompt/occurrence identity was
    certified reusable.  Overlapping requests therefore cannot be double
    counted, and gaps remain visible shell work.
    """

    if cut not in DVSR_CUTS:
        raise DvsrCrossSnapshotError("operator cut is invalid")
    trace = capture.get("trace")
    if not isinstance(trace, Sequence) or isinstance(trace, (str, bytes, bytearray)):
        return {"schema_version": "membind.dvsr.operator-dag.v1", "status": "UNKNOWN", "reason": "trace_missing"}
    selected: dict[str, tuple[int, int]] = {}
    for phase in _DAG_PHASES[cut]:
        rows = [row for row in trace if isinstance(row, Mapping) and row.get("phase") == phase and row.get("status") == "ok"]
        if len(rows) != 1:
            return {"schema_version": "membind.dvsr.operator-dag.v1", "status": "UNKNOWN", "reason": f"phase_{phase}_ambiguous"}
        interval = _valid_interval(rows[0])
        if interval is None:
            return {"schema_version": "membind.dvsr.operator-dag.v1", "status": "UNKNOWN", "reason": f"phase_{phase}_interval_invalid"}
        selected[phase] = interval
    if any(selected[_DAG_PHASES[cut][index]][1] > selected[_DAG_PHASES[cut][index + 1]][0] for index in range(len(_DAG_PHASES[cut]) - 1)):
        return {"schema_version": "membind.dvsr.operator-dag.v1", "status": "UNKNOWN", "reason": "phase_order_invalid"}
    reads = capture.get("reads", ())
    if not isinstance(reads, Sequence) or isinstance(reads, (str, bytes, bytearray)):
        return {"schema_version": "membind.dvsr.operator-dag.v1", "status": "UNKNOWN", "reason": "reads_missing"}
    requests_by_phase: dict[str, list[tuple[tuple[str, int], tuple[int, int]]]] = {phase: [] for phase in _DAG_PHASES[cut]}
    reads_by_phase: dict[str, list[tuple[tuple[str, int], tuple[int, int]]]] = {phase: [] for phase in _DAG_PHASES[cut]}
    for raw in capture.get("requests", ()):
        if not isinstance(raw, Mapping):
            continue
        prompt = str(raw.get("prompt_name", "unknown"))
        phase = _request_phase(prompt)
        if phase not in requests_by_phase:
            continue
        ordinal = raw.get("cut_occurrence", raw.get("ordinal"))
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            return {"schema_version": "membind.dvsr.operator-dag.v1", "status": "UNKNOWN", "reason": "request_identity_invalid"}
        interval = _valid_interval(raw)
        if interval is None:
            return {"schema_version": "membind.dvsr.operator-dag.v1", "status": "UNKNOWN", "reason": "request_interval_invalid"}
        requests_by_phase[phase].append(((prompt, ordinal), interval))
    for raw in reads:
        if not isinstance(raw, Mapping):
            return {"schema_version": "membind.dvsr.operator-dag.v1", "status": "UNKNOWN", "reason": "read_row_invalid"}
        operator = str(raw.get("operator", "unknown"))
        occurrence = raw.get("occurrence")
        if isinstance(occurrence, bool) or not isinstance(occurrence, int) or occurrence < 0:
            return {"schema_version": "membind.dvsr.operator-dag.v1", "status": "UNKNOWN", "reason": "read_identity_invalid"}
        phase = _request_phase(operator) or "node-resolution"
        if phase not in reads_by_phase:
            continue
        interval = _valid_interval(raw, "native_start_ns", "native_end_ns")
        if interval is None:
            return {"schema_version": "membind.dvsr.operator-dag.v1", "status": "UNKNOWN", "reason": "read_interval_invalid"}
        reads_by_phase[phase].append(((operator, occurrence), interval))

    reusable = set((str(prompt), int(ordinal)) for prompt, ordinal in reusable_request_keys)
    reusable_reads = set((str(operator), int(occurrence)) for operator, occurrence in reusable_read_keys)
    nodes: list[dict[str, Any]] = []
    predecessor: str | None = None
    removable: list[str] = []
    for phase in _DAG_PHASES[cut]:
        start, end = selected[phase]
        points = {start, end}
        clipped: list[tuple[tuple[str, int], int, int]] = []
        for key, (left, right) in requests_by_phase[phase]:
            left, right = max(start, left), min(end, right)
            if left < right:
                points.update((left, right))
                clipped.append((key, left, right))
        clipped_reads: list[tuple[tuple[str, int], int, int]] = []
        for key, (left, right) in reads_by_phase[phase]:
            left, right = max(start, left), min(end, right)
            if left < right:
                points.update((left, right))
                clipped_reads.append((key, left, right))
        ordered = sorted(points)
        for index, (left, right) in enumerate(zip(ordered, ordered[1:])):
            if right <= left:
                continue
            midpoint = (left + right) / 2
            active = [key for key, req_left, req_right in clipped if req_left <= midpoint < req_right]
            active_reads = [key for key, read_left, read_right in clipped_reads if read_left <= midpoint < read_right]
            node_id = f"{phase}-segment-{index:04d}"
            all_reusable = bool(active or active_reads) and all(key in reusable for key in active) and all(key in reusable_reads for key in active_reads)
            row = {
                "node_id": node_id,
                "predecessors": [predecessor] if predecessor else [],
                "cost_ns": right - left,
                "state_dependent": True,
                "phase": phase,
                "request_keys": [[key[0], key[1]] for key in active],
                "read_keys": [[key[0], key[1]] for key in active_reads],
                "reusable": all_reusable,
            }
            nodes.append(row)
            if all_reusable:
                removable.append(node_id)
            predecessor = node_id
    certificate_intervals: list[tuple[int, int]] = []
    c0_requery_intervals: list[tuple[int, int]] = []
    for raw in reads:
        if not isinstance(raw, Mapping):
            continue
        observer = _valid_interval(raw, "observer_start_ns", "observer_end_ns")
        native = _valid_interval(raw, "native_start_ns", "native_end_ns")
        if observer is not None and native is not None:
            certificate_intervals.extend(((observer[0], native[0]), (native[1], observer[1])))
            c0_requery_intervals.append(native)
    observer_overhead = _union_duration(certificate_intervals)
    c0_validation_cost = _union_duration(c0_requery_intervals)
    return {
        "schema_version": "membind.dvsr.operator-dag.v1",
        "status": "COMPLETE",
        "cut": cut,
        "nodes": nodes,
        "removable_node_ids": removable,
        "certificate_level": "C0_FRESH_REQUERY",
        "certificate_cost_ub_ns": c0_validation_cost,
        "c0_validation_cost_ns": c0_validation_cost,
        "observer_only_overhead_ns": observer_overhead,
        "baseline_cp_ns": sum(int(row["cost_ns"]) for row in nodes),
        "reusable_request_keys": [[key[0], key[1]] for key in sorted(reusable)],
        "reusable_read_keys": [[key[0], key[1]] for key in sorted(reusable_reads)],
    }


class DvsrCrossSnapshotError(GraphitiObserverError):
    """A paired observation cannot establish its identity or completeness."""


@dataclasses.dataclass(frozen=True, slots=True)
class PreparedResolutionResult:
    """Stateful resolution output before native publication."""

    cut: str
    prepared_artifact_digest: str
    source_sequence: int
    read_epoch: str
    nodes: tuple[Any, ...]
    entity_edges: tuple[Any, ...]
    node_episode_index_map: Mapping[str, Any]
    continuation_k: Mapping[str, Any]
    provider_calls: int
    database_writes: int
    started_ns: int
    ended_ns: int

    @property
    def duration_ns(self) -> int:
        return max(0, int(self.ended_ns) - int(self.started_ns))

    @property
    def continuation_digest(self) -> str:
        return canonical_digest(self.continuation_k)


async def resolve_prepared_to_seam_async(
    graphiti: Any,
    prepared: BuildStageResult,
    episode_kwargs: Mapping[str, Any],
    *,
    cut: str,
    publication_frontier: int,
    backend_epoch: str,
    read_epoch: str,
    bindings: BuildStageBindings | None = None,
    refresh_previous: bool = False,
) -> PreparedResolutionResult:
    """Resolve one immutable prepared artifact on the current state.

    ``prepared`` is never mutated.  For CUT-N the call ends immediately after
    native node resolution.  CUT-D executes the complete native suffix through
    summary/hydration and returns the same continuation boundary used by V6.
    ``_process_episode_data`` is intentionally never called.
    """

    if cut not in DVSR_CUTS:
        raise DvsrCrossSnapshotError(f"unsupported DVSR cut: {cut}")
    if isinstance(publication_frontier, bool) or publication_frontier < 0:
        raise DvsrCrossSnapshotError("publication frontier is invalid")
    if not isinstance(read_epoch, str) or not read_epoch:
        raise DvsrCrossSnapshotError("read epoch is missing")
    if not isinstance(backend_epoch, str) or not backend_epoch:
        raise DvsrCrossSnapshotError("backend epoch is missing")
    if not isinstance(prepared, BuildStageResult):
        raise DvsrCrossSnapshotError("prepared result has the wrong type")

    if not isinstance(refresh_previous, bool):
        raise DvsrCrossSnapshotError("refresh_previous must be boolean")
    selected = bindings or _default_bindings()
    started = time.monotonic_ns()
    # Deep copies are required because Graphiti's resolver promotes objects in
    # place.  Reusing the same object would make the second snapshot depend on
    # the first branch and invalidate the pair.
    episode = copy.deepcopy(prepared.episode)
    kwargs = dict(episode_kwargs)
    if refresh_previous:
        # A fresh branch must use the authoritative state after publication;
        # carrying the prepared snapshot's window would invalidate the pair.
        previous = tuple(
            copy.deepcopy(
                list(await _maybe_await(selected.retrieve_previous(graphiti, kwargs)))
            )
        )
    else:
        previous = tuple(copy.deepcopy(list(prepared.previous_episodes)))
    extracted = copy.deepcopy(list(prepared.extracted_nodes))
    # The prepared branch intentionally skips Node extraction.  Recreate the
    # runtime's one-partition provenance before the direct Edge call so the
    # fresh suffix has the same context as a native build on short sources.
    _ensure_single_partition_provenance(graphiti, episode, extracted)
    nodes, uuid_map, _duplicates = await _maybe_await(
        selected.resolve_nodes(graphiti, extracted, episode, previous, kwargs)
    )
    nodes = list(nodes)
    if cut == "CUT-N":
        continuation = {
            "schema_version": "membind.dvsr.cut-n-continuation.v1",
            "cut": cut,
            "source_sequence": int(prepared.episode.source_sequence)
            if hasattr(prepared.episode, "source_sequence")
            else int(kwargs.get("source_sequence", prepared.continuation_k.get("publication_frontier", 0))),
            "nodes": nodes,
            "uuid_map": dict(uuid_map),
            "publication_frontier": int(publication_frontier),
            "backend_epoch": backend_epoch,
        }
        ended = time.monotonic_ns()
        return PreparedResolutionResult(
            cut=cut,
            prepared_artifact_digest=canonical_digest(prepared.continuation_k),
            source_sequence=int(kwargs.get("source_sequence", continuation["source_sequence"])),
            read_epoch=read_epoch,
            nodes=tuple(copy.deepcopy(nodes)),
            entity_edges=(),
            node_episode_index_map=copy.deepcopy(dict(prepared.node_episode_index_map)),
            continuation_k=continuation,
            provider_calls=0,
            database_writes=0,
            started_ns=started,
            ended_ns=ended,
        )

    edge_types = kwargs.get("edge_types")
    edge_map = kwargs.get("edge_type_map") or (
        {("Entity", "Entity"): list(edge_types)} if edge_types else {("Entity", "Entity"): []}
    )
    resolved_edges, invalidated_edges, new_edges = await _maybe_await(
        selected.extract_resolve_edges(
            graphiti,
            episode,
            extracted,
            previous,
            nodes,
            dict(uuid_map),
            kwargs,
        )
    )
    entity_edges = list(resolved_edges) + list(invalidated_edges)
    hydrated_nodes = await _maybe_await(
        selected.extract_attributes(
            graphiti,
            nodes,
            episode,
            previous,
            list(new_edges),
            kwargs,
        )
    )
    _assert_complete_embeddings(hydrated_nodes, entity_edges)
    driver = getattr(graphiti, "driver", None)
    provider = getattr(getattr(driver, "provider", None), "value", getattr(driver, "provider", "neo4j"))
    database = getattr(driver, "_database", kwargs.get("group_id", "neo4j"))
    now = prepared.continuation_k.get("now")
    continuation = selected.continuation_k(
        episodes=[episode],
        nodes=hydrated_nodes,
        entity_edges=entity_edges,
        node_episode_index_map=dict(prepared.node_episode_index_map),
        now=now,
        group_id=kwargs["group_id"],
        store_raw_episode_content=bool(getattr(graphiti, "store_raw_episode_content", True)),
        driver_provider=str(provider).lower(),
        driver_database=str(database),
        backend_epoch=backend_epoch,
        publication_frontier=int(publication_frontier),
        saga=None,
        saga_previous_episode_uuid=None,
        update_communities=False,
    )
    ended = time.monotonic_ns()
    return PreparedResolutionResult(
        cut=cut,
        prepared_artifact_digest=canonical_digest(prepared.continuation_k),
        source_sequence=int(kwargs.get("source_sequence", prepared.continuation_k.get("publication_frontier", 0))),
        read_epoch=read_epoch,
        nodes=tuple(copy.deepcopy(hydrated_nodes)),
        entity_edges=tuple(copy.deepcopy(entity_edges)),
        node_episode_index_map=copy.deepcopy(dict(prepared.node_episode_index_map)),
        continuation_k=continuation,
        provider_calls=0,
        database_writes=0,
        started_ns=started,
        ended_ns=ended,
    )


def _request_key(row: Mapping[str, Any]) -> tuple[str, int]:
    prompt = str(row.get("prompt_name", "unknown"))
    ordinal = row.get("cut_occurrence", row.get("ordinal"))
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise DvsrCrossSnapshotError("request ordinal is invalid")
    return prompt, ordinal


def _read_key(row: Mapping[str, Any]) -> tuple[str, int]:
    operator = str(row.get("operator", "unknown"))
    occurrence = row.get("occurrence")
    if isinstance(occurrence, bool) or not isinstance(occurrence, int) or occurrence < 0:
        raise DvsrCrossSnapshotError("read occurrence is invalid")
    return operator, occurrence


def _read_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "operator",
        "group_ids",
        "limit",
        "min_score",
        "actual_result",
        "reference_result",
        "cutoff",
        "boundary_ties",
        "tie_contract",
        "query_digest",
        "filter_fingerprint",
        "completeness_status",
        "query_epoch",
        "index_epoch",
        "config_epoch",
    )
    return {field: row.get(field) for field in fields}


def _continuation_projection(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Remove the expected publication frontier from cross-state identity."""

    return {
        str(key): child
        for key, child in value.items()
        if key not in {"publication_frontier"}
    }


def compare_cross_snapshot(
    old_capture: Mapping[str, Any],
    fresh_capture: Mapping[str, Any],
    *,
    operator_cut: str,
    prepared_artifact_digest: str,
) -> dict[str, Any]:
    """Compare a paired old/fresh capture with fail-closed semantics."""

    if operator_cut not in DVSR_CUTS:
        raise DvsrCrossSnapshotError("operator cut is invalid")
    reasons: list[str] = []
    if old_capture.get("phase") != "OLD" or fresh_capture.get("phase") != "FRESH_NATIVE":
        reasons.append("phase_pair_mismatch")
    if old_capture.get("source_sequence") != fresh_capture.get("source_sequence"):
        reasons.append("source_sequence_mismatch")
    old_reads = old_capture.get("reads")
    fresh_reads = fresh_capture.get("reads")
    old_requests = old_capture.get("requests")
    fresh_requests = fresh_capture.get("requests")
    if not all(isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) for value in (old_reads, fresh_reads, old_requests, fresh_requests)):
        reasons.append("capture_fields_missing")
        old_reads = fresh_reads = old_requests = fresh_requests = ()

    def keyed(rows: Sequence[Mapping[str, Any]], *, kind: str) -> dict[tuple[str, int], Mapping[str, Any]]:
        result: dict[tuple[str, int], Mapping[str, Any]] = {}
        for raw in rows:
            if not isinstance(raw, Mapping):
                reasons.append(f"{kind}_row_invalid")
                continue
            try:
                key = _read_key(raw) if kind == "read" else _request_key(raw)
            except DvsrCrossSnapshotError:
                reasons.append(f"{kind}_identity_invalid")
                continue
            if key in result:
                reasons.append(f"{kind}_duplicate_identity")
            result[key] = raw
        return result

    old_read_map = keyed(old_reads, kind="read")
    fresh_read_map = keyed(fresh_reads, kind="read")
    old_req_map = keyed(old_requests, kind="request")
    fresh_req_map = keyed(fresh_requests, kind="request")
    if set(old_read_map) != set(fresh_read_map):
        reasons.append("read_identity_changed")
    read_matches = 0
    for key in sorted(set(old_read_map) & set(fresh_read_map)):
        if canonical_digest(_read_projection(old_read_map[key])) == canonical_digest(_read_projection(fresh_read_map[key])):
            read_matches += 1
        else:
            reasons.append(f"read_changed:{key[0]}:{key[1]}")
    if set(old_req_map) != set(fresh_req_map):
        reasons.append("request_identity_changed")
    request_matches = 0
    for key in sorted(set(old_req_map) & set(fresh_req_map)):
        left = old_req_map[key]
        right = fresh_req_map[key]
        if left.get("request_identity") == right.get("request_identity") and left.get("field_digests") == right.get("field_digests"):
            request_matches += 1
        else:
            reasons.append(f"request_changed:{key[0]}:{key[1]}")

    reusable_read_keys = [
        [key[0], key[1]]
        for key in sorted(set(old_read_map) & set(fresh_read_map))
        if canonical_digest(_read_projection(old_read_map[key]))
        == canonical_digest(_read_projection(fresh_read_map[key]))
    ]
    reusable_request_keys = [
        [key[0], key[1]]
        for key in sorted(set(old_req_map) & set(fresh_req_map))
        if old_req_map[key].get("request_identity") == fresh_req_map[key].get("request_identity")
        and old_req_map[key].get("field_digests") == fresh_req_map[key].get("field_digests")
    ]

    old_cont = old_capture.get("continuation_k")
    fresh_cont = fresh_capture.get("continuation_k")
    continuation_exact = (
        isinstance(old_cont, Mapping)
        and isinstance(fresh_cont, Mapping)
        and canonical_digest(_continuation_projection(old_cont))
        == canonical_digest(_continuation_projection(fresh_cont))
    )
    if not continuation_exact:
        reasons.append("continuation_changed")
    old_writes = old_capture.get("publication_calls", 0)
    fresh_writes = fresh_capture.get("publication_calls", 0)
    if old_writes != 0 or fresh_writes != 0:
        reasons.append("pre_seam_publication_detected")

    status = "VALID" if not reasons else "UNKNOWN"
    return {
        "schema_version": DVSR_CROSS_SNAPSHOT_SCHEMA,
        "status": status,
        "operator_cut": operator_cut,
        "prepared_artifact_digest": prepared_artifact_digest,
        "source_sequence": old_capture.get("source_sequence"),
        "read_match_count": read_matches,
        "old_read_count": len(old_read_map),
        "fresh_read_count": len(fresh_read_map),
        "request_match_count": request_matches,
        "old_request_count": len(old_req_map),
        "fresh_request_count": len(fresh_req_map),
        "continuation_exact": continuation_exact,
        "no_write": old_writes == 0 and fresh_writes == 0,
        "unknown_reasons": sorted(set(reasons)),
        "reusable_read_keys": reusable_read_keys,
        "reusable_request_keys": reusable_request_keys,
    }


def build_offline_benefit(
    *,
    reuse_hidden_cp_ns: int | float,
    reconvergence_saved_descendant_cp_ns: int | float,
    validation_cost_ns: int | float,
    visible_repair_cp_ns: int | float,
    failed_speculation_work_ns: int | float,
    seam_tax_ns: int | float,
    failed_work_lambda: float,
) -> dict[str, Any]:
    """Compute the preregistered offline economic ledger without interference."""

    values = {
        "reuse_hidden_cp_ns": reuse_hidden_cp_ns,
        "reconvergence_saved_descendant_cp_ns": reconvergence_saved_descendant_cp_ns,
        "validation_cost_ns": validation_cost_ns,
        "visible_repair_cp_ns": visible_repair_cp_ns,
        "failed_speculation_work_ns": failed_speculation_work_ns,
        "seam_tax_ns": seam_tax_ns,
    }
    for name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
            raise DvsrCrossSnapshotError(f"offline benefit field is invalid: {name}")
    if isinstance(failed_work_lambda, bool) or not isinstance(failed_work_lambda, (int, float)) or not math.isfinite(float(failed_work_lambda)) or float(failed_work_lambda) < 0:
        raise DvsrCrossSnapshotError("failed-work lambda is invalid")
    benefit = (
        float(reuse_hidden_cp_ns)
        + float(reconvergence_saved_descendant_cp_ns)
        - float(validation_cost_ns)
        - float(visible_repair_cp_ns)
        - float(failed_work_lambda) * float(failed_speculation_work_ns)
        - float(seam_tax_ns)
    )
    return {
        "schema_version": "membind.dvsr.offline-benefit.v1",
        "reuse_hidden_cp_ns": int(reuse_hidden_cp_ns),
        "reconvergence_saved_descendant_cp_ns": int(reconvergence_saved_descendant_cp_ns),
        "validation_cost_ns": int(validation_cost_ns),
        "visible_repair_cp_ns": int(visible_repair_cp_ns),
        "failed_speculation_work_ns": int(failed_speculation_work_ns),
        "failed_work_lambda": float(failed_work_lambda),
        "seam_tax_ns": int(seam_tax_ns),
        "offline_benefit_ns": benefit,
    }


def derive_offline_benefit_components(
    *,
    comparison_status: str,
    old_dag: Mapping[str, Any],
    fresh_dag: Mapping[str, Any],
    validation_cost_ns: int | float,
    seam_tax_ns: int | float,
    failed_work_lambda: float,
    extra_visible_repair_cp_ns: int | float = 0,
) -> dict[str, Any]:
    """Derive marginal offline costs against the no-reuse baseline.

    The fresh branch is the counterfactual work that Frozen V6 must perform
    on the authoritative state.  It is therefore *not* a treatment-only
    repair cost when a pair is invalid.  ``visible_repair_cp_ns`` is reserved
    for explicitly measured work beyond that no-reuse branch (for example a
    second repair call introduced by a selected operator).  This prevents
    the common accounting error of charging the entire fresh baseline twice.
    """

    if not isinstance(comparison_status, str) or not comparison_status:
        raise DvsrCrossSnapshotError("comparison status is invalid")
    if not isinstance(old_dag, Mapping) or not isinstance(fresh_dag, Mapping):
        raise DvsrCrossSnapshotError("operator DAG is invalid")
    if old_dag.get("status") != "COMPLETE" or fresh_dag.get("status") != "COMPLETE":
        raise DvsrCrossSnapshotError("operator DAG is incomplete")

    def _nonnegative_number(value: int | float, name: str) -> int | float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0
        ):
            raise DvsrCrossSnapshotError(f"offline benefit field is invalid: {name}")
        return value

    extra_repair = _nonnegative_number(extra_visible_repair_cp_ns, "extra_visible_repair_cp_ns")
    old_baseline = int(_nonnegative_number(old_dag.get("baseline_cp_ns", 0), "old_baseline_cp_ns"))
    fresh_baseline = int(_nonnegative_number(fresh_dag.get("baseline_cp_ns", 0), "fresh_baseline_cp_ns"))
    if comparison_status == "VALID":
        # Exact cut identity removes the complete fresh operator CP.  C0 read
        # requery remains visible and is charged separately as validation.
        reusable_cp = fresh_baseline
        failed_speculation = 0
    else:
        # Partial exact request reuse is legal even when another component of
        # the nested cut changes.  Reads are marked reusable here only after a
        # C1 proof; a C0-stable read is validation evidence, not avoided work.
        reusable_cp = sum(
            int(node["cost_ns"])
            for node in fresh_dag.get("nodes", ())
            if isinstance(node, Mapping) and node.get("reusable") is True
        )
        retained_old_cp = sum(
            int(node["cost_ns"])
            for node in old_dag.get("nodes", ())
            if isinstance(node, Mapping) and node.get("reusable") is True
        )
        failed_speculation = max(0, old_baseline - retained_old_cp)

    benefit = build_offline_benefit(
        reuse_hidden_cp_ns=reusable_cp,
        reconvergence_saved_descendant_cp_ns=0,
        validation_cost_ns=_nonnegative_number(validation_cost_ns, "validation_cost_ns"),
        visible_repair_cp_ns=extra_repair,
        failed_speculation_work_ns=failed_speculation,
        seam_tax_ns=_nonnegative_number(seam_tax_ns, "seam_tax_ns"),
        failed_work_lambda=failed_work_lambda,
    )
    benefit["comparison_status"] = comparison_status
    benefit["baseline_fresh_cp_ns"] = fresh_baseline
    benefit["visible_repair_definition"] = "treatment_only_extra_over_no_reuse_baseline"
    return benefit


def _identity_list(values: Any) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return []
    result: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            candidate = value.get("uuid") or value.get("id") or value.get("name")
        else:
            candidate = (
                getattr(value, "uuid", None)
                or getattr(value, "id", None)
                or getattr(value, "name", None)
            )
        if candidate is not None:
            result.append(str(candidate))
    return result


_NODE_SEMANTIC_FIELDS = (
    "name",
    "group_id",
    "labels",
    "created_at",
    "summary",
    "attributes",
    "name_embedding",
)
_EDGE_SEMANTIC_FIELDS = (
    "group_id",
    "source_node_uuid",
    "target_node_uuid",
    "created_at",
    "name",
    "fact",
    "fact_embedding",
    "episodes",
    "expired_at",
    "valid_at",
    "invalid_at",
    "reference_time",
    "attributes",
)
_EPISODE_SEMANTIC_FIELDS = (
    "name",
    "group_id",
    "labels",
    "created_at",
    "source",
    "source_description",
    "content",
    "valid_at",
    "entity_edges",
    "episode_metadata",
)
_CONTINUATION_CONTROL_FIELDS = (
    "node_episode_index_map",
    "uuid_map",
    "now",
    "store_raw_episode_content",
    "driver_provider",
    "driver_database",
    "saga",
    "saga_previous_episode_uuid",
    "update_communities",
)


def _semantic_object_projection(value: Any, *, fields: Sequence[str]) -> dict[str, Any]:
    """Digest prompt/publication-visible object fields without retaining payloads."""

    identity = (
        value.get("uuid") if isinstance(value, Mapping) else getattr(value, "uuid", None)
    )
    projection: dict[str, Any] = {"identity": None if identity is None else str(identity)}
    present: list[str] = []
    for field in fields:
        if isinstance(value, Mapping):
            if field not in value:
                continue
            child = value[field]
        else:
            if not hasattr(value, field):
                continue
            child = getattr(value, field)
        present.append(field)
        projection[f"{field}_digest"] = canonical_digest(child)
    projection["semantic_fields_present"] = present
    return projection


def _sanitize_previous_episode(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"status": "MISSING"}
    order = value.get("order")
    window = value.get("window")
    return {
        "selector_digest": canonical_digest(value.get("selector", {})),
        "order": [str(item) for item in order]
        if isinstance(order, Sequence) and not isinstance(order, (str, bytes, bytearray))
        else [],
        "projection_digest": value.get("projection_digest"),
        "window_count": len(window)
        if isinstance(window, Sequence) and not isinstance(window, (str, bytes, bytearray))
        else 0,
        "start_ns": value.get("start_ns"),
        "end_ns": value.get("end_ns"),
        "duration_ns": value.get("duration_ns"),
    }


def _sanitize_read(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"status": "INVALID"}
    fields = (
        "operator", "occurrence", "group_ids", "limit", "min_score",
        "actual_result", "reference_result", "cutoff", "boundary_ties",
        "tie_contract", "query_digest", "filter_fingerprint",
        "completeness_status", "query_epoch", "index_epoch", "config_epoch",
        "start_ns", "end_ns", "duration_ns", "observer_start_ns",
        "native_start_ns", "native_end_ns", "observer_end_ns",
    )
    return {field: value.get(field) for field in fields if field in value}


def _sanitize_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"status": "INVALID"}
    fields = (
        "schema_version", "phase", "source_sequence", "state_version",
        "ordinal", "prompt_name", "request_identity", "field_digests",
        "cut_occurrence", "response_digest",
        "prompt_ordinal", "transport_response_digest", "response_binding",
        "model_epoch", "start_ns", "end_ns", "duration_ns", "status",
    )
    return {field: value.get(field) for field in fields if field in value}


def _sanitize_continuation(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DvsrCrossSnapshotError("continuation is missing")
    episodes = value.get("episodes", ())
    nodes = value.get("nodes", ())
    edges = value.get("entity_edges", value.get("edges", ()))
    sanitized = {
        "schema_version": value.get("schema_version"),
        "seam": value.get("seam"),
        "cut": value.get("cut"),
        "source_sequence": value.get("source_sequence"),
        "group_id_digest": canonical_digest(value.get("group_id")),
        "backend_epoch": value.get("backend_epoch"),
        "publication_frontier": value.get("publication_frontier"),
        "node_ids": _identity_list(nodes),
        "edge_ids": _identity_list(edges),
        "episode_ids": _identity_list(episodes),
        "node_semantic_projection": [
            _semantic_object_projection(node, fields=_NODE_SEMANTIC_FIELDS)
            for node in nodes
        ]
        if isinstance(nodes, Sequence) and not isinstance(nodes, (str, bytes, bytearray))
        else [],
        "edge_semantic_projection": [
            _semantic_object_projection(edge, fields=_EDGE_SEMANTIC_FIELDS)
            for edge in edges
        ]
        if isinstance(edges, Sequence) and not isinstance(edges, (str, bytes, bytearray))
        else [],
        "episode_semantic_projection": [
            _semantic_object_projection(episode, fields=_EPISODE_SEMANTIC_FIELDS)
            for episode in episodes
        ]
        if isinstance(episodes, Sequence) and not isinstance(episodes, (str, bytes, bytearray))
        else [],
        "control_field_digests": {
            field: canonical_digest(value.get(field))
            for field in _CONTINUATION_CONTROL_FIELDS
            if field in value
        },
        "node_count": len(nodes)
        if isinstance(nodes, Sequence) and not isinstance(nodes, (str, bytes, bytearray))
        else 0,
        "edge_count": len(edges)
        if isinstance(edges, Sequence) and not isinstance(edges, (str, bytes, bytearray))
        else 0,
    }
    # The observer intentionally drops raw Graphiti payloads and unknown
    # runtime fields.  Exactness is nevertheless bound to every ordered
    # prompt/publication-visible Node, Edge, Episode and continuation-control
    # field through the digest-only projections above.
    sanitized["payload_digest"] = canonical_digest(sanitized)
    return sanitized


def sanitize_observer_capture(
    capture: Mapping[str, Any],
    *,
    continuation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep identity/timing evidence while dropping prompt-visible payloads."""

    if not isinstance(capture, Mapping):
        raise DvsrCrossSnapshotError("capture is invalid")
    source_continuation = (
        continuation if continuation is not None else capture.get("continuation_k")
    )
    sanitized_continuation = _sanitize_continuation(source_continuation)
    reads = capture.get("reads", ())
    requests = capture.get("requests", ())
    return {
        "schema_version": capture.get("schema_version"),
        "status": capture.get("status", "OBSERVER_ONLY"),
        "phase": capture.get("phase"),
        "source_sequence": capture.get("source_sequence"),
        "state_version": capture.get("state_version"),
        "previous_episode": _sanitize_previous_episode(capture.get("previous_episode")),
        "reads": [_sanitize_read(value) for value in reads]
        if isinstance(reads, Sequence) and not isinstance(reads, (str, bytes, bytearray))
        else [],
        "requests": [_sanitize_request(value) for value in requests]
        if isinstance(requests, Sequence) and not isinstance(requests, (str, bytes, bytearray))
        else [],
        "dependency_edges": capture.get("dependency_edges", []),
        "continuation_k": sanitized_continuation,
        "continuation": {
            "status": capture.get("continuation", {}).get("status")
            if isinstance(capture.get("continuation"), Mapping)
            else "UNKNOWN"
        },
        "start_ns": capture.get("start_ns"),
        "end_ns": capture.get("end_ns"),
        "duration_ns": capture.get("duration_ns"),
        "publication_calls": capture.get("publication_calls", 0),
        "treatment_calls": capture.get("treatment_calls", 0),
        "capture_payload_digest": canonical_digest(
            {
                "phase": capture.get("phase"),
                "source_sequence": capture.get("source_sequence"),
                "state_version": capture.get("state_version"),
                "reads": reads,
                "requests": requests,
                "continuation": source_continuation,
            }
        ),
    }


__all__ = [
    "DVSR_CROSS_SNAPSHOT_SCHEMA",
    "DVSR_CUTS",
    "DvsrCrossSnapshotError",
    "build_operator_dag",
    "PreparedResolutionResult",
    "build_offline_benefit",
    "derive_offline_benefit_components",
    "compare_cross_snapshot",
    "resolve_prepared_to_seam_async",
    "sanitize_observer_capture",
]
