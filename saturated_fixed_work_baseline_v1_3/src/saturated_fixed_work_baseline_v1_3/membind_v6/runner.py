"""V6 runner contracts and the shared frontier composition.

The CLI is intentionally separate from the V5 P9 entrypoint.  The live
Graphiti adapter is added only after Probe A/B selects a treatment; the
provider-free frontier function is complete now so its ordering contract can
be tested without touching services.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import uuid
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from ..membind_v5.campaign import FORMAL_HISTORIES
from ..membind_v5.runtime.core.admission import CapacityAuthority
from ..membind_v5.p9_runner import (
    _DurableJsonl,
    _native_previous_window,
    _transport_attempt_rows,
    _transport_evidence_summary,
    run_frontier_history_async,
)
from ..membind_v5.live_runner import _episode_node, _graphiti_kwargs, _maybe_await, _write_jsonl, _write_new
from ..membind_v5.runtime.core.binder import NativeBindingScope
from ..membind_v5.runtime.core.provider_admission import current_provider_scope, provider_scope
from ..membind_v5.runtime.core.transcript import TranscriptStore
from .provider import V6ProviderClient
from .proof import (
    validate_frontier_events,
    validate_provider_events,
    validate_replay_accounting,
    validate_request_comparisons,
)
from .request_observation import compare_request_observations, write_private_request_capture


class V6RunnerError(ValueError):
    pass


V6_CONSTRUCTION_BASE_URL = "http://10.87.5.247:8000/v1/"
V6_EMBEDDING_BASE_URL = "http://10.87.5.247:8001/v1"
V6_POLICIES = ("matched-control", "v6")
V6_PROBE_METHOD = "V6_REQUEST_STABILITY_PROBE"
V6_CONTROL_METHOD = "V6_MATCHED_CONTROL"


def v6_live_authorization_checker(action: Any, **_kwargs: Any) -> dict[str, Any]:
    """Return the explicit operator authorization for the V6 live path.

    V6 live is user-authorized directly for this campaign.  It must not reuse
    the mutable historical ``CURRENT_STATE.json`` FORMAL gate (that gate is
    retained by V5 and other frozen protocols).  Configuration and service
    identity checks still happen in ``build_u0_graphiti_from_env``.
    """

    action_name = getattr(action, "value", str(action))
    return {
        "allowed": True,
        "reason": "explicit_v6_live_authorization",
        "action": action_name,
    }


def matched_control_uses_preparation() -> bool:
    """Matched control keeps V5 source-derived preparation in the timer."""

    return True


@dataclass(frozen=True, slots=True)
class V6Config:
    repo_root: Path
    baseline_root: Path
    state_path: Path
    output_root: Path
    run_id: str
    history_id: str
    policy: str
    full_history: bool
    source_limit: int | None = None
    construction_base_url: str = V6_CONSTRUCTION_BASE_URL
    embedding_base_url: str = V6_EMBEDDING_BASE_URL

    def __post_init__(self) -> None:
        if not re.fullmatch(r"v6-[a-z0-9][a-z0-9-]{2,79}", self.run_id):
            raise V6RunnerError("V6 run_id is invalid")
        if self.history_id not in FORMAL_HISTORIES:
            raise V6RunnerError("history_id is not frozen")
        if self.policy not in V6_POLICIES:
            raise V6RunnerError("policy must be matched-control or v6")
        if self.full_history and self.source_limit is not None:
            raise V6RunnerError("full_history requires source_limit=None")
        if not self.full_history and (self.source_limit is None or not 1 <= self.source_limit <= 12):
            raise V6RunnerError("prefix source_limit must be between 1 and 12")
        if self.construction_base_url != V6_CONSTRUCTION_BASE_URL or self.embedding_base_url != V6_EMBEDDING_BASE_URL:
            raise V6RunnerError("V6 live must use frozen 8000/8001 endpoints")


async def run_v6_frontier_provider_free(
    source_count: int,
    prepare: Callable[[int], Awaitable[Any]],
    publish: Callable[[int, Any], Awaitable[Any]],
) -> dict[str, Any]:
    """Exercise the shared V5 frontier executor under a V6-owned contract."""

    if source_count <= 0:
        raise V6RunnerError("source_count must be positive")
    result = await run_frontier_history_async(
        source_count,
        prepare,
        publish,
        authority=CapacityAuthority(8, source="provider-free-test-authority"),
        history_id="v6-provider-free",
        admit_native=False,
    )
    publication_order = [
        int(event["source_sequence"])
        for event in result.execution.events
        if event.get("event") == "PUBLICATION_DURABLE"
    ]
    return {
        "schema_version": "membind.v6.frontier-result.v1",
        "durable_frontier": result.durable_frontier,
        "publication_order": publication_order,
        "preparation_count": result.overlap_evidence["preparation_count"],
        "overlap_evidence": result.overlap_evidence,
        "build_makespan_ns": result.execution.build_makespan_ns,
    }


class _V6MultiplexClient:
    def __init__(self, capture: V6ProviderClient, replay: V6ProviderClient | None) -> None:
        self.capture = capture
        self.replay = replay

    async def generate_response(self, messages: list[Any], **kwargs: Any) -> Any:
        region, _source = current_provider_scope()
        if region == "PREPARE" or self.replay is None:
            return await self.capture.generate_response(messages, **kwargs)
        return await self.replay.generate_response(messages, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.replay or self.capture, name)


def _persist_v6_partial_evidence(history_root: Path, recorder: Any) -> dict[str, Any]:
    """Persist per-attempt transport evidence before a failed run tears down.

    Graphiti can raise a logical JSON parsing exception after the underlying
    OpenAI transport has already returned a response.  The recorder then still
    contains the authoritative usage and ``finish_reason`` for that attempt.
    Keep those fields on disk so failure diagnosis does not rely on global
    vLLM counters, while explicitly marking missing recorder data incomplete.
    """

    history_root = Path(history_root)
    history_root.mkdir(parents=True, exist_ok=True)
    rows = _transport_attempt_rows(recorder)
    summary = _transport_evidence_summary(rows)
    attempts_path = history_root / "transport_attempts.jsonl"
    summary_path = history_root / "transport_evidence.json"
    if rows and not attempts_path.exists():
        _write_jsonl(attempts_path, rows)
    if not summary_path.exists():
        _write_new(summary_path, summary)
    return summary


async def run_v6_live_async(
    config: V6Config,
    *,
    runtime_builder: Callable[[], Any],
    episode_loader: Callable[[Path, str, str], Sequence[Any]],
    instrumentation_installer: Callable[[Any, Any], Any],
    recorder_factory: Callable[[], Any],
    graph_exporter: Callable[[Any, list[Any], str], Any],
    authorization_checker: Callable[..., Any],
) -> dict[str, Any]:
    """Run one real Graphiti V6 control or request-stability qualification arm.

    The ``v6`` arm is deliberately labelled qualification-only.  It materializes
    source-closed extraction shadow work and binds the existing certified
    extraction seam, while the request comparison decides whether a later
    state-dependent native phase is safe to promote to a final V6 treatment.
    """

    from saturated_fixed_work_baseline_v1_2.dataset import EXPECTED_EPISODE_COUNTS
    from saturated_fixed_work_baseline_v1_3.membind_v5.campaign import verify_baseline_reference
    baseline = verify_baseline_reference(config.baseline_root, allow_invalid_qa=True)
    root = Path(config.output_root).resolve()
    if root.exists():
        raise V6RunnerError("V6 output root must be fresh")
    root.mkdir(parents=True)
    history_root = root / "histories" / config.history_id
    history_root.mkdir(parents=True)
    namespace = f"membind-v6-{config.run_id}-{config.history_id}-{uuid.uuid4().hex[:10]}"
    episodes = tuple(episode_loader(config.repo_root, config.history_id, namespace))
    if config.source_limit is not None:
        episodes = episodes[: config.source_limit]
    if config.full_history and len(episodes) != EXPECTED_EPISODE_COUNTS[config.history_id]:
        raise V6RunnerError(f"{config.history_id}: full history source count is incomplete")
    if not episodes or [int(item.source_sequence) for item in episodes] != list(range(len(episodes))):
        raise V6RunnerError("V6 source identity mapping is invalid")

    journals = {
        key: _DurableJsonl(history_root / filename)
        for key, filename in (("frontier", "frontier.jsonl"), ("admission", "admission.jsonl"), ("raw", "raw_events.jsonl"))
    }
    frontier_ref = {"value": -1}
    runtime: Any = None
    graphiti: Any = None
    instrumentation: Any = None
    recorder: Any = None
    closed = False

    def journal_frontier(row: dict[str, Any]) -> None:
        journals["frontier"].append(row)
        journals["raw"].append({"event": "FRONTIER", **row})

    def journal_admission(row: dict[str, Any]) -> None:
        journals["admission"].append(row)
        journals["raw"].append({"event": "ADMISSION", **row})

    def advance_frontier(sequence: int) -> None:
        observed = int(sequence)
        if observed != frontier_ref["value"] + 1:
            raise V6RunnerError("V6 durable frontier jump")
        frontier_ref["value"] = observed

    async def close_runtime() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        if instrumentation is not None:
            instrumentation.restore()
        close = getattr(graphiti, "close", None) if graphiti is not None else None
        if callable(close):
            await _maybe_await(close())

    try:
        from graphiti_core.utils.maintenance.edge_operations import extract_edges
        from graphiti_core.utils.maintenance.node_operations import extract_nodes

        runtime = await _maybe_await(runtime_builder())
        graphiti = runtime.graphiti
        recorder = recorder_factory()
        instrumentation = instrumentation_installer(graphiti, recorder)
        original_llm = runtime.llm_client
        capacity = CapacityAuthority.from_protocol_runtime(runtime)
        from ..membind_v5.runtime.core.admission import AdmissionArbiter

        arbiter = AdmissionArbiter(capacity, name="v6-provider", event_sink=journal_admission)
        store = TranscriptStore()
        client_identity = {
            "class": f"{type(original_llm).__module__}.{type(original_llm).__qualname__}",
            "source_hash": hashlib.sha256(inspect.getsource(type(original_llm)).encode()).hexdigest()
            if inspect.isclass(type(original_llm))
            else "unknown",
        }
        capture = V6ProviderClient(
            original_llm,
            store=store,
            arbiter=arbiter,
            mode="capture",
            durable_frontier=lambda: frontier_ref["value"],
            client_identity=client_identity,
            event_sink=journal_admission,
        )
        replay = None if config.policy == "matched-control" else V6ProviderClient(
            original_llm,
            store=store,
            arbiter=arbiter,
            mode="replay",
            durable_frontier=lambda: frontier_ref["value"],
            client_identity=client_identity,
            event_sink=journal_admission,
        )
        graphiti_client = _V6MultiplexClient(capture, replay)
        graphiti.llm_client = graphiti_client
        graphiti.clients.llm_client = graphiti_client

        async def prepare(sequence: int) -> dict[str, Any]:
            episode = episodes[sequence]
            node_episode = _episode_node(episode, namespace=namespace)
            previous = [
                _episode_node(item, namespace=namespace, uuid_value=f"prep-{item.source_sequence}")
                for item in _native_previous_window(episodes, sequence)
            ]
            with recorder.episode_scope(config.run_id, episode.name, sequence):
                with provider_scope(region="PREPARE", source_sequence=sequence):
                    nodes, index_map = await extract_nodes(graphiti.clients, node_episode, previous, None, None, None)
                    edges = await extract_edges(
                        graphiti.clients,
                        node_episode,
                        nodes,
                        previous,
                        {("Entity", "Entity"): []},
                        namespace,
                        None,
                        None,
                    )
            return {
                "source_sequence": sequence,
                "shadow": config.policy == "v6",
                "node_count": len(nodes),
                "edge_count": len(edges),
                "node_index_count": len(index_map),
            }

        async def publish(sequence: int, _prepared: Any) -> Any:
            episode = episodes[sequence]
            with recorder.episode_scope(config.run_id, episode.name, sequence):
                with provider_scope(region="NATIVE", source_sequence=sequence):
                    if config.policy == "v6":
                        with NativeBindingScope(store, source_sequence=sequence):
                            return await graphiti.add_episode(**_graphiti_kwargs(episode, namespace=namespace))
                    return await graphiti.add_episode(**_graphiti_kwargs(episode, namespace=namespace))

        frontier_result = await run_frontier_history_async(
            len(episodes),
            prepare,
            publish,
            authority=capacity,
            history_id=config.history_id,
            event_sink=journal_frontier,
            interval_sink=journals["raw"].append,
            lifecycle_sink=journals["raw"].append,
            admission=arbiter,
            durable_frontier_sink=advance_frontier,
            admit_native=False,
        )
        if frontier_result.durable_frontier != len(episodes) - 1:
            raise V6RunnerError("V6 final durable frontier incomplete")
        logical = store.summary()
        if config.policy == "v6" and (logical["duplicates"] or logical["unconsumed"]):
            raise V6RunnerError("V6 transcript binding incomplete")
        canonical = await _maybe_await(graph_exporter(graphiti, list(episodes), namespace))
        all_observations = capture.observations + ([] if replay is None else replay.observations)
        private_path = write_private_request_capture(
            root / "private" / "request_capture.jsonl",
            [row["observation"] for row in all_observations],
        )
        public_rows = [
            dict(row["public_summary"], region=row["region"], source_sequence=row["source_sequence"], mode=row["mode"])
            for row in all_observations
        ]
        comparisons: list[dict[str, Any]] = []
        if config.policy == "v6" and replay is not None:
            shadow = {
                (int(row["source_sequence"]), row["public_summary"]["callsite"], int(row["public_summary"]["ordinal"])): row["observation"]
                for row in capture.observations
                if row["region"] == "PREPARE"
            }
            native = {
                (int(row["source_sequence"]), row["public_summary"]["callsite"], int(row["public_summary"]["ordinal"])): row["observation"]
                for row in replay.observations
                if row["region"] == "NATIVE"
            }
            for key in sorted(set(shadow) | set(native)):
                if key not in shadow or key not in native:
                    comparisons.append({"key": key, "match": False, "reason": "missing_side"})
                else:
                    comparisons.append({"key": key, **compare_request_observations(shadow[key], native[key])})
        envelopes = [
            recorder.episode_envelope(config.run_id, episode.name, episode.source_sequence)
            for episode in episodes
        ]
        # Persist the same attempt-level evidence on success and failure.  A
        # successful history may still contain recoverable structured-output
        # truncations, so the aggregate summary alone is insufficient for
        # exact source/attempt attribution.
        transport = _persist_v6_partial_evidence(history_root, recorder)
        frontier_proof = validate_frontier_events(frontier_result.execution.events, source_count=len(episodes))
        provider_proof = validate_provider_events(arbiter.evidence()["events"], capacity=capacity.value)
        request_proof = validate_request_comparisons(comparisons)
        replay_proof = (
            validate_replay_accounting(logical)
            if config.policy == "v6"
            else {"schema_version": "membind.v6.replay-proof.v1", "status": "NOT_APPLICABLE"}
        )
        final_publication = max(
            int(event["monotonic_ns"])
            for event in frontier_result.execution.events
            if event.get("event") == "PUBLICATION_DURABLE"
        )
        lifecycle = {
            "status": "DURABLE",
            "timer_start_ns": frontier_result.execution.timer_start_ns,
            "timer_stop_ns": frontier_result.execution.timer_stop_ns,
            "final_publication_ns": final_publication,
            "build_makespan_ns": frontier_result.execution.build_makespan_ns,
        }
        method = V6_PROBE_METHOD if config.policy == "v6" else V6_CONTROL_METHOD
        summary = {
            "schema_version": "membind.v6.history-result.v1",
            "status": "PASS",
            "method": method,
            "claim_status": "QUALIFICATION_ONLY",
            "policy": config.policy,
            "history_id": config.history_id,
            "namespace": namespace,
            "source_count": len(episodes),
            "durable_frontier": frontier_result.durable_frontier,
            "overlap_evidence": frontier_result.overlap_evidence,
            "logical_work_summary": logical,
            "provider_call_count": len(capture.provider_calls) + (len(replay.provider_calls) if replay else 0),
            "request_observation_count": len(public_rows),
            "request_comparisons": comparisons,
            "transport_evidence": transport,
            "runtime_identity": runtime.config.to_artifact() if callable(getattr(runtime.config, "to_artifact", None)) else {},
            "lifecycle": lifecycle,
            "trace_envelope_count": len(envelopes),
            "proof": {
                "frontier": frontier_proof,
                "provider": provider_proof,
                "request": request_proof,
                "replay": replay_proof,
            },
        }
        await close_runtime()
        for journal in journals.values():
            journal.close()
        _write_new(
            root / "manifest.json",
            {
                "schema_version": "membind.v6.manifest.v1",
                "status": "PASS",
                "method": method,
                "claim_status": "QUALIFICATION_ONLY",
                "policy": config.policy,
                "baseline_reference": baseline,
                "native_graphiti_path": "Graphiti.add_episode",
                "endpoint_identity": {"construction": V6_CONSTRUCTION_BASE_URL, "embedding": V6_EMBEDDING_BASE_URL},
            },
        )
        _write_new(history_root / "history_result.json", summary)
        _write_new(history_root / "canonical_graph.json", dict(canonical))
        _write_new(history_root / "logical_work_summary.json", logical)
        _write_new(history_root / "runtime_identity.json", summary["runtime_identity"])
        _write_new(history_root / "lifecycle.json", lifecycle)
        _write_new(
            history_root / "block_metrics.json",
            {
                "history_id": config.history_id,
                "source_count": len(episodes),
                "durable_frontier": frontier_result.durable_frontier,
                "build_makespan_ns": frontier_result.execution.build_makespan_ns,
                "overlap_evidence": frontier_result.overlap_evidence,
            },
        )
        _write_jsonl(root / "request_observation.jsonl", public_rows)
        _write_new(root / "request_comparison.json", {"schema_version": "membind.v6.request-comparison.v1", "comparisons": comparisons})
        _write_new(root / "proof.json", summary["proof"])
        _write_new(
            root / "attempt_status.json",
            {
                "schema_version": "membind.v6.attempt-status.v1",
                "status": "SUCCESS",
                "claim_status": "QUALIFICATION_ONLY",
                "method": method,
                "policy": config.policy,
                "history_id": config.history_id,
                "durable_frontier": frontier_result.durable_frontier,
            },
        )
        seal = {
            "schema_version": "membind.v6.seal.v1",
            "status": "V6_PROBE_SEALED",
            "claim_status": "QUALIFICATION_ONLY",
            "method": method,
            "policy": config.policy,
            "history_id": config.history_id,
            "source_count": len(episodes),
            "durable_frontier": frontier_result.durable_frontier,
        }
        _write_new(root / "seal.json", seal)
        return {"root": str(root), "seal": seal, "history": summary, "private_capture": str(private_path)}
    except BaseException as exc:
        transport_evidence = _transport_evidence_summary([])
        try:
            transport_evidence = _persist_v6_partial_evidence(history_root, recorder)
        except BaseException:
            # Evidence persistence is best effort and must not mask the
            # original Graphiti/provider exception.
            pass
        try:
            _write_new(
                root / "attempt_status.json",
                {
                    "schema_version": "membind.v6.attempt-status.v1",
                    "status": "FAILURE",
                    "policy": config.policy,
                    "history_id": config.history_id,
                    "durable_frontier": frontier_ref["value"],
                    "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "transport_evidence": transport_evidence,
                },
            )
        except BaseException:
            pass
        try:
            _write_new(
                root / "failure.json",
                {
                    "schema_version": "membind.v6.failure.v1",
                    "status": "V6_LIVE_FAILED",
                    "policy": config.policy,
                    "history_id": config.history_id,
                    "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "durable_frontier": frontier_ref["value"],
                    "transport_evidence": transport_evidence,
                },
            )
        except BaseException:
            pass
        try:
            await close_runtime()
        except BaseException:
            pass
        for journal in journals.values():
            try:
                journal.close()
            except BaseException:
                pass
        raise


def build_v6_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the V6 Graphiti-first autoresearch executable")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--state", dest="state_path", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--history-id", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full-history", action="store_true")
    mode.add_argument("--source-limit", type=int)
    parser.add_argument("--policy", choices=V6_POLICIES, required=True)
    parser.add_argument("--execute-live", action="store_true")
    return parser


def config_from_args(args: argparse.Namespace) -> V6Config:
    full_history = bool(args.full_history)
    return V6Config(
        repo_root=args.repo_root.resolve(),
        baseline_root=args.baseline_root.resolve(),
        state_path=args.state_path.resolve(),
        output_root=args.output_root.resolve(),
        run_id=args.run_id,
        history_id=args.history_id,
        policy=args.policy,
        full_history=full_history,
        source_limit=None if full_history else args.source_limit,
    )


def build_v6_live_command(config: V6Config, *, python: str = "membind-validation/.venv/bin/python") -> str:
    script = config.repo_root / "saturated_fixed_work_baseline_v1_3/scripts/run_v6.py"
    v13_src = config.repo_root / "saturated_fixed_work_baseline_v1_3/src"
    v12_src = config.repo_root / "saturated_fixed_work_baseline_v1_2/src"
    validation_src = config.repo_root / "membind-validation/src"
    tokens = [
        "PYTHONPATH=" + ":".join((str(v13_src), str(v12_src), str(validation_src))),
        python,
        str(script),
        "--repo-root", str(config.repo_root),
        "--baseline-root", str(config.baseline_root),
        "--state", str(config.state_path),
        "--output-root", str(config.output_root),
        "--run-id", config.run_id,
        "--history-id", config.history_id,
        "--policy", config.policy,
        "--full-history" if config.full_history else "--source-limit",
    ]
    if not config.full_history:
        tokens.append(str(config.source_limit))
    tokens.append("--execute-live")
    return " ".join(tokens)


__all__ = [
    "V6Config",
    "V6RunnerError",
    "build_v6_live_command",
    "build_v6_parser",
    "config_from_args",
    "_persist_v6_partial_evidence",
    "run_v6_frontier_provider_free",
    "v6_live_authorization_checker",
]
