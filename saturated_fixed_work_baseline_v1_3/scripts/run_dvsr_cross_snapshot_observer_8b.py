#!/usr/bin/env python3
"""Run the Phase-3 DVSR operator-neutral observer on local 8B.

The command is intentionally capture-only.  It publishes the current source
through Frozen V6 so the next source can be paired across adjacent states, but
never publishes a prepared speculative result and never enables reuse.  The
default workload is a two-source development shakeout; full development runs
must be explicitly requested and are still excluded from held-out histories.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import contextvars
import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
for source in (
    ROOT / "mab_quality_v2_final_qa/src",
    ROOT / "saturated_fixed_work_baseline_v1_3/src",
    ROOT / "paper-eval-v3/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from saturated_fixed_work_baseline_v1_3.mab_live_runner import (  # noqa: E402
    _mab_graphiti_kwargs,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.core import (  # noqa: E402
    build_membind_core_runtime_8b,
    core_identity,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime_8b import (  # noqa: E402
    close_8b_u0_runtime,
    load_8b_routing_contract,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.provider_admission import (  # noqa: E402
    provider_scope,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_cross_snapshot import (  # noqa: E402
    build_operator_dag,
    derive_window_bounded_offline_benefit,
    compare_cross_snapshot,
    resolve_prepared_to_seam_async,
    sanitize_observer_capture,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_workload import (  # noqa: E402
    DEV_HISTORIES,
    load_development_history_episodes,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_read_accounting import (  # noqa: E402
    evaluate_c0_c1_read_accounting,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_reconvergence import (  # noqa: E402
    attribute_descendant_reconvergence,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_no_write import (  # noqa: E402
    build_no_write_proof,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_accounting import (  # noqa: E402
    FAILED_WORK_LAMBDA,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_window import (  # noqa: E402
    compute_pair_window_from_observer_evidence,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.graphiti_observer import (  # noqa: E402
    GraphitiCaptureInstallation,
    build_projection_delta,
    build_to_seam_async,
    canonical_digest,
    load_backend_projection_async,
    _ensure_single_partition_provenance,
)
from native_characterization_instrumentation import (  # noqa: E402
    install_native_characterization_instrumentation,
)
from native_characterization_tracing import TraceRecorder  # noqa: E402


HELD_OUT_HISTORIES = (
    "b01defab",
    "0f05491a",
    "6aeb4375",
    "06db6396",
    "89941a94",
    "c4ea545c",
    "ce6d2d27",
    "08e075c7",
)
_MUTATION_RE = re.compile(r"\b(?:CREATE|MERGE|DELETE|SET|REMOVE|DROP|DETACH)\b", re.I)
_WRITE_GUARD: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "dvsr_observer_write_guard", default=False
)


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False, default=str) + "\n").encode("ascii")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="ascii") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=True, sort_keys=True, allow_nan=False, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _install_write_guard(driver: Any, sink: list[dict[str, Any]]):
    original = getattr(driver, "execute_query", None)
    if not callable(original):
        raise RuntimeError("Neo4j execute_query seam is unavailable")

    async def guarded(*args: Any, **kwargs: Any) -> Any:
        # Neo4j accepts both ``query_`` and keyword/positional forms.  Keep
        # the wrapper transparent so keyword calls do not collide with a
        # positional parameter named ``query``.
        query = kwargs.get("query_") or kwargs.get("query") or (args[0] if args else "")
        if _WRITE_GUARD.get() and _MUTATION_RE.search(str(query)):
            row = {"query_digest": _sha256(str(query)), "monotonic_ns": time.monotonic_ns()}
            sink.append(row)
            raise RuntimeError("DVSR observer detected a speculative database mutation")
        return await original(*args, **kwargs)

    setattr(driver, "execute_query", guarded)

    def restore() -> None:
        setattr(driver, "execute_query", original)

    return restore


@contextlib.contextmanager
def _guarded_observation():
    token = _WRITE_GUARD.set(True)
    try:
        yield
    finally:
        _WRITE_GUARD.reset(token)


def _filtered_cut_capture(capture: Mapping[str, Any], *, cut: str, continuation: Mapping[str, Any]) -> dict[str, Any]:
    raw_trace = list(capture.get("trace", ()))
    raw_reads = {
        (str(row.get("operator")), int(row.get("occurrence", -1))): row
        for row in capture.get("reads", ())
        if isinstance(row, Mapping)
        and isinstance(row.get("occurrence"), int)
        and not isinstance(row.get("occurrence"), bool)
    }
    capture = sanitize_observer_capture(capture, continuation=continuation)
    # Extraction is the dependency-free prepared prefix and is intentionally
    # excluded from both stateful cuts.  Requests are re-keyed by occurrence
    # within each prompt because the fresh suffix has no preceding extraction
    # calls and therefore uses a different global ordinal.
    extraction_prompts = {
        "extract_nodes.extract_message",
        "extract_nodes.extract_text",
        "extract_nodes.extract_json",
    }
    requests = [
        row for row in capture.get("requests", ())
        if isinstance(row, Mapping) and row.get("prompt_name") not in extraction_prompts
    ]
    counts: dict[str, int] = {}
    normalized_requests: list[dict[str, Any]] = []
    for row in requests:
        prompt = str(row.get("prompt_name", "unknown"))
        occurrence = counts.get(prompt, 0)
        counts[prompt] = occurrence + 1
        normalized_requests.append({**dict(row), "cut_occurrence": occurrence})
    reads = [row for row in capture.get("reads", ()) if isinstance(row, Mapping) and row.get("operator") == "node_cosine"]
    # C1 needs the query vector and exact domain only while reducing this pair.
    # The second sanitizer in _pair_record removes both before persistence.
    reads = [
        {
            **dict(row),
            **{
                field: raw_reads[(str(row.get("operator")), int(row.get("occurrence", -1)))][field]
                for field in ("query", "complete_domain")
                if (str(row.get("operator")), int(row.get("occurrence", -1))) in raw_reads
                and field in raw_reads[(str(row.get("operator")), int(row.get("occurrence", -1)))]
            },
        }
        for row in reads
    ]
    if cut == "CUT-N":
        reads = [row for row in reads]
        normalized_requests = [
            row for row in normalized_requests
            if row.get("prompt_name") == "dedupe_nodes.nodes"
        ]
    return {
        **dict(capture),
        "reads": reads,
        "requests": normalized_requests,
        "trace": raw_trace,
    }


def _prepared_digest(stage: Any, *, source_sequence: int, v6_identity: str) -> str:
    return canonical_digest(
        {
            "source_sequence": int(source_sequence),
            "v6_identity": v6_identity,
            "episode": stage.episode,
            "previous_episodes": stage.previous_episodes,
            "extracted_nodes": stage.extracted_nodes,
            "node_episode_index_map": stage.node_episode_index_map,
            "policy": "frozen-v6-prepared-object-v1",
        }
    )


def _pair_record(
    *,
    history_id: str,
    source_sequence: int,
    prepared_digest: str,
    old_capture: Mapping[str, Any],
    fresh_capture: Mapping[str, Any],
    delta: Mapping[str, Any],
    state_delta: Any,
    old_nodes: Mapping[str, Mapping[str, Any]],
    fresh_nodes: Mapping[str, Mapping[str, Any]],
    node_repair_evidence: Mapping[str, Any] | None,
    no_write_proof: Mapping[str, Any],
    formal_start_ns: int,
    previous_durable_ns: int | None,
    predecessor_publication_start_ns: int | None,
    comparison: Mapping[str, Any],
    old_stage: Any,
    fresh_stage: Any,
    comparison_cost_ns: int = 0,
) -> dict[str, Any]:
    old_duration = max(0, int(old_capture.get("duration_ns", 0)))
    fresh_duration = max(0, int(fresh_capture.get("duration_ns", 0)))
    old_requests = {
        (str(row.get("prompt_name")), int(row.get("cut_occurrence", row.get("ordinal", -1)))): row
        for row in old_capture.get("requests", ()) if isinstance(row, Mapping)
    }
    fresh_requests = {
        (str(row.get("prompt_name")), int(row.get("cut_occurrence", row.get("ordinal", -1)))): row
        for row in fresh_capture.get("requests", ()) if isinstance(row, Mapping)
    }
    reusable = sorted(
        key for key in set(old_requests) & set(fresh_requests)
        if old_requests[key].get("request_identity") == fresh_requests[key].get("request_identity")
        and old_requests[key].get("field_digests") == fresh_requests[key].get("field_digests")
    )
    read_accounting = evaluate_c0_c1_read_accounting(
        old_capture=old_capture,
        fresh_capture=fresh_capture,
        delta=state_delta,
        old_nodes=old_nodes,
        fresh_nodes=fresh_nodes,
    )
    reusable_reads = [
        (str(key[0]), int(key[1]))
        for key in read_accounting["reusable_read_keys"]
    ]
    # The comparison is the authority for exact cross-snapshot identity.  A
    # request/read can only be marked reusable after the pair checker has
    # compared both captures; the local maps above are retained as a
    # defensive fallback for older comparison artifacts.
    comparison_reusable = [
        (str(key[0]), int(key[1]))
        for key in comparison.get("reusable_request_keys", reusable)
        if isinstance(key, Sequence) and not isinstance(key, (str, bytes, bytearray)) and len(key) == 2
    ]
    old_dag = build_operator_dag(
        old_capture,
        cut=str(comparison["operator_cut"]),
        reusable_request_keys=comparison_reusable,
        reusable_read_keys=reusable_reads,
    )
    fresh_dag = build_operator_dag(
        fresh_capture,
        cut=str(comparison["operator_cut"]),
        reusable_request_keys=comparison_reusable,
        reusable_read_keys=reusable_reads,
    )
    dag_complete = old_dag.get("status") == "COMPLETE" and fresh_dag.get("status") == "COMPLETE"
    window_accounting = compute_pair_window_from_observer_evidence(
        source_sequence=source_sequence,
        old_capture=old_capture,
        fresh_capture=fresh_capture,
        formal_start_ns=formal_start_ns,
        previous_durable_ns=previous_durable_ns,
        predecessor_publication_start_ns=predecessor_publication_start_ns,
        removable_operator_cp_ns=(
            int(fresh_dag.get("baseline_cp_ns", 0))
            if fresh_dag.get("status") == "COMPLETE"
            else 0
        ),
    )
    if str(comparison["operator_cut"]) == "CUT-D" and isinstance(node_repair_evidence, Mapping):
        reconvergence = attribute_descendant_reconvergence(
            parent_operator="node-resolution",
            repair_attempted=bool(node_repair_evidence.get("repair_attempted")),
            old_parent_output_digest=node_repair_evidence.get("old_parent_output_digest"),
            repaired_parent_output_digest=node_repair_evidence.get("repaired_parent_output_digest"),
            operator_dag=fresh_dag,
            descendant_certificate_valid_node_ids=tuple(fresh_dag.get("removable_node_ids", ())),
        )
    else:
        reconvergence = {
            "schema_version": "membind.dvsr.descendant-reconvergence.v1",
            "status": "NOT_APPLICABLE",
            "repair_result": "NOT_REPAIRED",
            "saved_descendant_operator_ids": [],
            "reconvergence_saved_descendant_cp_ns": 0,
            "parent_repair_cp_credited_ns": 0,
            "operator_states": {},
        }
    benefit = (
        derive_window_bounded_offline_benefit(
            window_accounting=window_accounting,
            comparison_status=str(comparison.get("status", "UNKNOWN")),
            old_dag=old_dag,
            fresh_dag=fresh_dag,
            validation_cost_ns=int(read_accounting["selected_validation_cost_ns"]),
            seam_tax_ns=max(0, int(comparison_cost_ns)),
            failed_work_lambda=FAILED_WORK_LAMBDA,
            reconvergence_saved_descendant_node_ids=tuple(
                reconvergence.get("saved_descendant_operator_ids", ())
            ),
        )
        if dag_complete else {
            "schema_version": "membind.dvsr.offline-benefit.v1",
            "status": "UNKNOWN",
            "reason": "operator_dag_incomplete",
        }
    )
    old_safe = sanitize_observer_capture(old_capture)
    fresh_safe = sanitize_observer_capture(fresh_capture)
    # TraceRecorder rows contain only phase IDs, timestamps, statuses and
    # scalar metadata (the recorder rejects prompt/content fields).  Retain
    # them outside the capture sanitizer so the operator DAG can be rebuilt
    # from the sealed pair without persisting semantic payloads.
    old_safe["trace"] = list(old_capture.get("trace", ()))
    fresh_safe["trace"] = list(fresh_capture.get("trace", ()))
    return {
        "schema_version": "membind.dvsr.cross-snapshot-pair.v1",
        "history_id": history_id,
        "source_sequence": int(source_sequence),
        "prepared_artifact_digest": prepared_digest,
        "state_before": {"version": int(old_capture.get("state_version", 0))},
        "state_after": {"version": int(fresh_capture.get("state_version", 0))},
        "delta": dict(delta),
        "old_capture": old_safe,
        "fresh_capture": fresh_safe,
        "comparison": dict(comparison),
        "old_duration_ns": old_duration,
        "fresh_duration_ns": fresh_duration,
        "prepared_output_digest": canonical_digest(old_stage.continuation_k),
        "fresh_output_digest": canonical_digest(fresh_stage.continuation_k),
        "operator_dag": {"old": old_dag, "fresh": fresh_dag},
        "read_accounting": read_accounting,
        "reconvergence": reconvergence,
        "window_accounting": window_accounting,
        "offline_benefit": benefit,
        "no_write_proof": dict(no_write_proof),
        "no_speculative_write": all(
            isinstance(branch, Mapping) and branch.get("status") == "PASS"
            for branch in no_write_proof.values()
        ),
    }


async def _run_history(*, history_id: str, episodes: Sequence[Any], run_id: str, output: Path) -> dict[str, Any]:
    routes = load_8b_routing_contract(os.environ["MEMBIND_V61_ROUTING_CONFIG"])
    events: list[dict[str, Any]] = []
    runtime = build_membind_core_runtime_8b(routing_contract=routes, route_event_sink=events.append)
    graphiti = runtime.graphiti
    init_task = getattr(graphiti.driver, "_init_task", None)
    if init_task is not None:
        await init_task
    suffix = uuid.uuid4().hex[:12]
    namespace = f"{os.environ['MEMBIND_PROFILE_ID']}-dvsr-p3-{run_id}-h{history_id}-{suffix}"
    if not namespace.startswith(os.environ["MEMBIND_PROFILE_ID"] + "-"):
        raise RuntimeError("DVSR namespace identity is invalid")
    capture = GraphitiCaptureInstallation(
        graphiti,
        model_epoch="qwen3-8b-awq@4da05a8edb55c6046cce958586c33b61da07bb79",
        query_epoch="neo4j-node-query-v1",
        index_epoch="neo4j-index-v1",
        config_epoch="local-qwen3-8b-awq-dualreplica-v1",
        backend_epoch="neo4j-local-v1",
        single_call_branch_oracle=True,
    )
    mutation_rows: list[dict[str, Any]] = []
    restore_guard = _install_write_guard(graphiti.driver, mutation_rows)
    capture.install()
    recorder = TraceRecorder()
    native_instrumentation = install_native_characterization_instrumentation(graphiti, recorder)
    rows: list[dict[str, Any]] = []
    published = 0
    formal_start_ns = time.monotonic_ns()
    publication_starts: dict[int, int] = {}
    publication_durables: dict[int, int] = {}

    async def publish_native(episode: Any, kwargs: Mapping[str, Any]) -> None:
        """Run the authoritative Frozen-V6 publication with instrumentation."""

        sequence = int(kwargs["source_sequence"])
        trace_run_id = f"{run_id}:NATIVE:{sequence}"
        publication_starts[sequence] = time.monotonic_ns()
        with capture.scope(
            phase="FRESH_NATIVE",
            source_sequence=sequence,
            state_version=published,
            episode_kwargs=kwargs,
        ):
            with recorder.episode_scope(trace_run_id, str(kwargs["name"]), sequence):
                with provider_scope(region="NATIVE", source_sequence=sequence):
                    _ensure_single_partition_provenance(
                        graphiti,
                        type("_EpisodeContent", (), {"content": str(getattr(episode, "body", ""))})(),
                        (),
                    )
                    await graphiti.add_episode(**{k: v for k, v in kwargs.items() if k != "source_sequence"})
        publication_durables[sequence] = time.monotonic_ns()

    try:
        for sequence, episode in enumerate(episodes):
            kwargs = _mab_graphiti_kwargs(episode, namespace=namespace)
            kwargs["source_sequence"] = int(episode.source_sequence)
            if sequence + 1 < len(episodes):
                target = episodes[sequence + 1]
                target_kwargs = _mab_graphiti_kwargs(target, namespace=namespace)
                target_kwargs["source_sequence"] = int(target.source_sequence)
                before = await load_backend_projection_async(
                    graphiti.driver,
                    namespace=namespace,
                    version=published,
                    backend_epoch="neo4j-local-v1",
                )
                target_sequence = int(target.source_sequence)
                previous_durable_ns = (
                    formal_start_ns
                    if target_sequence == 1
                    else publication_durables.get(target_sequence - 2)
                )
                # The predecessor publication start is not available until
                # the predecessor has actually entered publication.  Keep it
                # unset here and read the measured clock after publish_native
                # returns; never substitute observer wall time.
                predecessor_publication_start_ns = None
                trace_run_id = f"{run_id}:OLD:{target_sequence}"
                old_mutation_start = len(mutation_rows)
                with capture.scope(phase="OLD", source_sequence=target_sequence, state_version=published, episode_kwargs=target_kwargs) as observed:
                    with recorder.episode_scope(trace_run_id, str(target_kwargs["name"]), target_sequence):
                        with provider_scope(region="PREPARE", source_sequence=target_sequence):
                            with _guarded_observation():
                                with recorder.span("build-to-seam", operation_class="semantic-root"):
                                    stage = await build_to_seam_async(
                                        graphiti,
                                        target_kwargs,
                                        publication_frontier=published,
                                        backend_epoch="neo4j-local-v1",
                                    )
                capture.attach_shadow_result(observed, stage)
                old_capture = observed.to_record()
                old_capture["trace"] = [
                    dict(record.to_dict())
                    for record in recorder.records
                    if record.run_id == trace_run_id
                ]
                after_old_speculation = await load_backend_projection_async(
                    graphiti.driver,
                    namespace=namespace,
                    version=published,
                    backend_epoch="neo4j-local-v1",
                )
                old_no_write = build_no_write_proof(
                    api_write_count=len(mutation_rows) - old_mutation_start,
                    shadow_publication_count=int(old_capture.get("publication_calls", 0)),
                    graph_projection_before_digest=before.digest,
                    graph_projection_after_digest=after_old_speculation.digest,
                )
                prepared_digest = _prepared_digest(stage, source_sequence=int(target.source_sequence), v6_identity=core_identity()["version"])

                with provider_scope(region="NATIVE", source_sequence=int(episode.source_sequence)):
                    await publish_native(episode, kwargs)
                published += 1
                predecessor_publication_start_ns = publication_starts.get(target_sequence - 1)
                after = await load_backend_projection_async(
                    graphiti.driver,
                    namespace=namespace,
                    version=published,
                    backend_epoch="neo4j-local-v1",
                )
                delta = build_projection_delta(before, after)
                delta_record = {
                    # StateDelta's public contract names the two snapshots
                    # source/target; keep the on-disk schema explicit while
                    # avoiding an accidental dependency on BackendProjection.
                    "before_version": delta.source_version,
                    "after_version": delta.target_version,
                    "changes": [
                        {"kind": c.kind, "key": c.key, "changed_fields": sorted(c.changed_fields), "operation": c.operation}
                        for c in delta.changes
                    ],
                    "environment_changes": sorted(delta.environment_changes),
                }
                fresh_trace_run_id = f"{run_id}:FRESH:{target_sequence}"
                fresh_mutation_start = len(mutation_rows)
                with capture.scope(phase="FRESH_NATIVE", source_sequence=target_sequence, state_version=published, episode_kwargs=target_kwargs) as fresh_observed:
                    with recorder.episode_scope(fresh_trace_run_id, str(target_kwargs["name"]), target_sequence):
                        with provider_scope(region="NATIVE", source_sequence=target_sequence):
                            with _guarded_observation():
                                with recorder.span("dvsr-resolve-to-seam", operation_class="semantic-root"):
                                    fresh = await resolve_prepared_to_seam_async(
                                        graphiti,
                                        stage,
                                        target_kwargs,
                                        cut="CUT-D",
                                        publication_frontier=published,
                                        backend_epoch="neo4j-local-v1",
                                        read_epoch=f"state-{published}",
                                        refresh_previous=True,
                                    )
                fresh_capture = fresh_observed.to_record()
                fresh_capture["trace"] = [
                    dict(record.to_dict())
                    for record in recorder.records
                    if record.run_id == fresh_trace_run_id
                ]
                after_fresh_speculation = await load_backend_projection_async(
                    graphiti.driver,
                    namespace=namespace,
                    version=published,
                    backend_epoch="neo4j-local-v1",
                )
                fresh_no_write = build_no_write_proof(
                    api_write_count=len(mutation_rows) - fresh_mutation_start,
                    shadow_publication_count=int(fresh_capture.get("publication_calls", 0)),
                    graph_projection_before_digest=after.digest,
                    graph_projection_after_digest=after_fresh_speculation.digest,
                )
                node_repair_evidence: dict[str, Any] | None = None
                for cut in ("CUT-N", "CUT-D"):
                    old_cut = _filtered_cut_capture(old_capture, cut=cut, continuation=(
                        {"cut": "CUT-N", "nodes": getattr(stage, "resolved_nodes", ()) or stage.nodes}
                        if cut == "CUT-N"
                        else stage.continuation_k
                    ))
                    fresh_cut = _filtered_cut_capture(fresh_capture, cut=cut, continuation=(
                        {"cut": "CUT-N", "nodes": fresh.nodes}
                        if cut == "CUT-N"
                        else fresh.continuation_k
                    ))
                    comparison_started = time.monotonic_ns()
                    comparison = compare_cross_snapshot(
                        old_cut,
                        fresh_cut,
                        operator_cut=cut,
                        prepared_artifact_digest=prepared_digest,
                    )
                    comparison_cost_ns = time.monotonic_ns() - comparison_started
                    if cut == "CUT-N":
                        complete = comparison.get("status") != "UNKNOWN_INCOMPLETE_EVIDENCE"
                        node_repair_evidence = {
                            "repair_attempted": comparison.get("status") == "INVALID_CHANGED",
                            "old_parent_output_digest": (
                                old_cut.get("continuation_k", {}).get("payload_digest") if complete else None
                            ),
                            "repaired_parent_output_digest": (
                                fresh_cut.get("continuation_k", {}).get("payload_digest") if complete else None
                            ),
                        }
                    rows.append(
                        _pair_record(
                            history_id=history_id,
                            source_sequence=int(target.source_sequence),
                            prepared_digest=prepared_digest,
                            old_capture=old_cut,
                            fresh_capture=fresh_cut,
                            delta=delta_record,
                            state_delta=delta,
                            old_nodes=before.nodes,
                            fresh_nodes=after.nodes,
                            node_repair_evidence=node_repair_evidence,
                            no_write_proof={"old_speculative": old_no_write, "fresh_oracle": fresh_no_write},
                            formal_start_ns=formal_start_ns,
                            previous_durable_ns=previous_durable_ns,
                            predecessor_publication_start_ns=predecessor_publication_start_ns,
                            comparison=comparison,
                            old_stage=stage,
                            fresh_stage=fresh,
                            comparison_cost_ns=comparison_cost_ns,
                        )
                    )
            else:
                with provider_scope(region="NATIVE", source_sequence=int(episode.source_sequence)):
                    await publish_native(episode, kwargs)
                published += 1
    finally:
        capture.restore()
        native_instrumentation.restore()
        restore_guard()
        await close_8b_u0_runtime(runtime)

    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "manifest.json", {
        "schema_version": "membind.dvsr.phase3-observer-manifest.v1",
        "status": "OBSERVER_ONLY",
        "run_id": run_id,
        "history_id": history_id,
        "namespace": namespace,
        "profile_id": os.environ.get("MEMBIND_PROFILE_ID"),
        "v6_identity": core_identity(),
        "source_count": len(episodes),
        "held_out_accessed": history_id in HELD_OUT_HISTORIES,
        "speculative_db_writes": len(mutation_rows),
        "publication_frontier": published - 1,
        "route_event_count": len(events),
    })
    pair_path = output / "DVSR_CROSS_SNAPSHOT_OBSERVER.jsonl"
    with pair_path.open("x", encoding="ascii") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True, allow_nan=False, default=str) + "\n")
    _write_json(output / "write_guard.json", {"schema_version": "membind.dvsr.no-write-proof.v1", "status": "PASS" if not mutation_rows else "FAIL", "mutation_count": len(mutation_rows), "mutation_query_digests": [row["query_digest"] for row in mutation_rows]})
    return {"history_id": history_id, "source_count": len(episodes), "pair_count": len(rows), "mutation_count": len(mutation_rows), "output": str(output)}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", choices=DEV_HISTORIES, default="07741c45")
    parser.add_argument("--source-count", type=int, default=2)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.source_count < 2:
        parser.error("--source-count must be at least 2 for a cross-snapshot pair")
    if args.history in HELD_OUT_HISTORIES:
        parser.error("held-out histories are forbidden in Phase 3")
    return args


async def _main(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("MEMBIND_PROFILE_ID") != "local-qwen3-8b-awq-dualreplica-v1":
        raise RuntimeError("source scripts/local_runtime_8b_dual/activate.sh first")
    selected = load_development_history_episodes(
        repository_root=ROOT,
        history_id=args.history,
        source_count=args.source_count,
    )
    run_id = args.run_id or f"phase3-{args.history}-{args.source_count}s-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:6]}"
    output = (args.output or (Path(os.environ["MEMBIND_EXPERIMENT_ROOT"]) / "v7_dvsr_phase3" / run_id)).resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("output namespace already exists; use a fresh run-id")
    return await _run_history(history_id=args.history, episodes=selected, run_id=run_id, output=output)


if __name__ == "__main__":
    try:
        print(json.dumps(asyncio.run(_main(_parse_args())), ensure_ascii=True, sort_keys=True))
    except BaseException as exc:
        print(json.dumps({"status": "INVALID_ATTEMPT", "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}", "error_digest": _sha256(str(exc))}, ensure_ascii=True, sort_keys=True))
        raise
