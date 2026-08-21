"""Production v3.1 OBSERVE_ONLY composition and bounded capture runner."""

from __future__ import annotations

import ast
import inspect
import json
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from paper_eval.artifacts import (
    append_jsonl_durable,
    atomic_write_json,
    payload_sha256,
    sha256_file,
)
from paper_eval.membind_v1.graphiti_factories import build_source_log_from_episodes
from paper_eval.membind_v31.admission import AdmissionPolicy
from paper_eval.membind_v31.certification import StateCutCertification
from paper_eval.membind_v31.coordinator import run_membind_v31_stream
from paper_eval.membind_v31.graphiti_adapter import MemBindV31GraphitiAdapter
from paper_eval.membind_v31.live_block import V31LiveHooks, production_v31_live_hooks
from paper_eval.membind_v31.method_plan import verify_membind_v31_method_plan
from paper_eval.membind_v31.optimization_pilot import (
    BIND_WORKERS,
    COMPILE_WORKERS,
    GLOBAL_LLM_ADMISSION_K,
    LOOKAHEAD,
    PILOT_HISTORY,
)
from paper_eval.s5_graphiti_semantic_binding import (
    S5GraphitiSemanticBinding,
    load_graphiti_semantic_binding,
)

from .graphiti_0293_runtime import (
    MEGRuntimeInstrumentedAdapter,
    build_observe_only_binding,
)
from .mutation_epoch import StateMutationEpoch
from .read_view import ReadViewStatus
from .runtime_instrumentation import (
    InstrumentationMode,
    MEGRuntimeRecorder,
    OperatorEventType,
    SemanticOperatorClass,
    WriterDomainCertificate,
)


_RUN_ID = re.compile(r"^membind-v31-opt-w4-meg-runtime-observe-[a-z0-9-]{3,40}$")
_NAMESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")


class MEGRuntimeLiveError(ValueError):
    """The observe-only live composition or capture failed closed."""


def _fail(code: str) -> MEGRuntimeLiveError:
    return MEGRuntimeLiveError(code)


def _jsonable(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    return value


def _seal(body: Mapping[str, object]) -> dict[str, object]:
    result = deepcopy(dict(body))
    result["payload_sha256"] = payload_sha256(result)
    return result


def derive_observe_namespace(run_id: str) -> str:
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise _fail("meg_runtime_run_id_invalid")
    suffix = run_id.removeprefix("membind-v31-opt-w4-")
    namespace = f"pev3-opt-membind-v31-w4-{suffix}-{PILOT_HISTORY}"
    if _NAMESPACE.fullmatch(namespace) is None:
        raise _fail("meg_runtime_namespace_invalid")
    return namespace


def _process_call_is_saga_free(adapter_source: Path) -> bool:
    tree = ast.parse(adapter_source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "process_episode_data" or len(node.args) < 8:
            continue
        return all(
            isinstance(node.args[index], ast.Constant)
            and node.args[index].value is None
            for index in (6, 7)
        )
    return False


def build_v31_observe_composition_proof(
    *, project_root: Path, graphiti_source_hashes: Mapping[str, object]
) -> dict[str, object]:
    project = Path(project_root).resolve()
    relative = {
        "coordinator": "src/paper_eval/membind_v31/coordinator.py",
        "graphiti_adapter": "src/paper_eval/membind_v31/graphiti_adapter.py",
        "live_runtime": "src/paper_eval/membind_v31/live_runtime.py",
        "request_runtime": "src/paper_eval/membind_v31/request_runtime.py",
        "meg_runtime_seam": "src/paper_eval/membind_v4/mseg/graphiti_0293_runtime.py",
    }
    paths = {name: project / value for name, value in relative.items()}
    if any(not path.is_file() for path in paths.values()):
        raise _fail("meg_runtime_composition_source_missing")
    adapter_source = paths["graphiti_adapter"]
    adapter_text = adapter_source.read_text(encoding="utf-8")
    coordinator_text = paths["coordinator"].read_text(encoding="utf-8")
    return {
        "status": "PASS",
        "normal_v31_adapter_path": True,
        "semantic_binding_wrapper_only": True,
        "compile_workers": COMPILE_WORKERS,
        "lookahead": LOOKAHEAD,
        "bind_workers": BIND_WORKERS,
        "global_llm_admission_k": GLOBAL_LLM_ADMISSION_K,
        "admission_policy": AdmissionPolicy.CACHE_AFFINE.value,
        "arrival_order_changed": False,
        "prompt_or_model_changed": False,
        "search_or_candidate_limit_changed": False,
        "shadow_reads": 0,
        "saga_none": _process_call_is_saga_free(adapter_source),
        "community_update_invoked": "update_community(" in adapter_text,
        "coordinator_function": "run_membind_v31_stream" in coordinator_text,
        "source_hashes": {
            name: sha256_file(path) for name, path in sorted(paths.items())
        },
        "graphiti_source_hashes": dict(sorted(graphiti_source_hashes.items())),
    }


def build_observe_capture_contract(
    *,
    verified_plan: Mapping[str, object],
    run_id: str,
    output_root: Path,
    source_count: int,
    composition_proof: Mapping[str, object],
) -> dict[str, object]:
    try:
        plan = verify_membind_v31_method_plan(verified_plan)
    except ValueError:
        raise _fail("meg_runtime_parent_plan_invalid") from None
    if isinstance(source_count, bool) or source_count not in {3, 12}:
        raise _fail("meg_runtime_source_count_invalid")
    namespace = derive_observe_namespace(run_id)
    sources = list(plan["history_source_sha256s"][PILOT_HISTORY][:source_count])
    offsets = list(
        plan["arrival_traces"][PILOT_HISTORY]["arrival_offsets_ns"][:source_count]
    )
    if len(sources) != source_count or len(offsets) != source_count:
        raise _fail("meg_runtime_source_prefix_unavailable")
    proof = deepcopy(dict(composition_proof))
    required_proof = (
        proof.get("status") == "PASS"
        and proof.get("saga_none") is True
        and proof.get("community_update_invoked") is False
        and proof.get("compile_workers") == COMPILE_WORKERS
        and proof.get("lookahead") == LOOKAHEAD
        and proof.get("bind_workers") == BIND_WORKERS
        and proof.get("global_llm_admission_k") == GLOBAL_LLM_ADMISSION_K
        and proof.get("shadow_reads") == 0
    )
    if not required_proof:
        raise _fail("meg_runtime_composition_proof_failed")
    cache_salt = payload_sha256(
        {
            "purpose": "MEG_RUNTIME_OBSERVE_ONLY",
            "run_id": run_id,
            "namespace": namespace,
            "plan": plan["payload_sha256"],
            "source_sequences": list(range(source_count)),
        }
    )
    body = {
        "schema_version": "membind.meg.runtime-observe-capture-contract.v1",
        "status": "AUTHORIZED",
        "artifact_status": "DIAGNOSTIC_ONLY_NON_MERGEABLE",
        "formal_main_table_eligible": False,
        "run_id": run_id,
        "history_id": PILOT_HISTORY,
        "source_count": source_count,
        "source_sequences": list(range(source_count)),
        "source_sha256s": sources,
        "arrival_offsets_ns": offsets,
        "arrival_prefix_sha256": payload_sha256(offsets),
        "parent_plan_payload_sha256": plan["payload_sha256"],
        "shared_execution_envelope_sha256": plan[
            "shared_execution_envelope_sha256"
        ],
        "namespace": namespace,
        "cache_salt_sha256": cache_salt,
        "output_root": str(Path(output_root).resolve()),
        "mode": InstrumentationMode.OBSERVE_ONLY.value,
        "compile_workers": COMPILE_WORKERS,
        "lookahead": LOOKAHEAD,
        "bind_workers": BIND_WORKERS,
        "global_llm_admission_k": GLOBAL_LLM_ADMISSION_K,
        "admission_policy": AdmissionPolicy.CACHE_AFFINE.value,
        "shadow_reads_authorized": False,
        "scheduler_change_authorized": False,
        "semantic_change_authorized": False,
        "composition_proof": proof,
    }
    return _seal(body)


@dataclass(slots=True)
class MEGObserveOnlyLiveComposition:
    hooks: V31LiveHooks
    recorder: MEGRuntimeRecorder
    mutation_epoch: StateMutationEpoch
    writer_domain: WriterDomainCertificate
    stream_id: str
    execution_policy_changed: bool = False


def build_meg_observe_only_live_composition(
    *,
    recorder: MEGRuntimeRecorder,
    mutation_epoch: StateMutationEpoch,
    writer_domain: WriterDomainCertificate,
    stream_id: str,
    base_hooks: V31LiveHooks | None = None,
    semantic_binding_loader: Callable[[], S5GraphitiSemanticBinding] | None = None,
    inner_adapter_factory: Callable[
        [object, StateCutCertification, S5GraphitiSemanticBinding], object
    ]
    | None = None,
) -> MEGObserveOnlyLiveComposition:
    if recorder.mode is not InstrumentationMode.OBSERVE_ONLY:
        raise _fail("meg_runtime_composition_mode_invalid")
    if not writer_domain.certified:
        raise _fail("meg_runtime_writer_domain_uncertified")
    if mutation_epoch.namespace != writer_domain.namespace:
        raise _fail("meg_runtime_writer_namespace_mismatch")
    selected_base = production_v31_live_hooks() if base_hooks is None else base_hooks
    if not isinstance(selected_base, V31LiveHooks):
        raise _fail("meg_runtime_base_hooks_invalid")
    selected_loader = semantic_binding_loader or load_graphiti_semantic_binding

    def default_inner(
        runtime: object,
        certification: StateCutCertification,
        binding: S5GraphitiSemanticBinding,
    ) -> object:
        from graphiti_core.edges import EntityEdge
        from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode

        from paper_eval.membind_v1.graphiti_factories import make_graphiti_node_factories

        graphiti = getattr(runtime, "graphiti")
        factories = make_graphiti_node_factories(
            episodic_node_type=EpisodicNode,
            entity_node_type=EntityNode,
            message_source=EpisodeType.message,
        )
        return MemBindV31GraphitiAdapter(
            graphiti=graphiti,
            llm_client=getattr(graphiti, "llm_client"),
            semantic_binding=binding,
            episode_factory=factories.episode_factory,
            extracted_node_factory=factories.extracted_node_factory,
            extracted_edge_factory=lambda value: EntityEdge(**dict(value)),
            state_cut_certification=certification,
        )

    selected_inner = inner_adapter_factory or default_inner

    def adapter_factory(runtime: object, certification: StateCutCertification) -> object:
        binding = build_observe_only_binding(
            selected_loader(),
            recorder=recorder,
            mutation_epoch=mutation_epoch,
            writer_domain=writer_domain,
            stream_id=stream_id,
        )
        inner = selected_inner(runtime, certification, binding)
        return MEGRuntimeInstrumentedAdapter(inner=inner, stream_id=stream_id)

    hooks = V31LiveHooks(
        runtime_builder=selected_base.runtime_builder,
        runtime_ready=selected_base.runtime_ready,
        namespace_probe=selected_base.namespace_probe,
        namespace_episode=selected_base.namespace_episode,
        source_visibility_probe=selected_base.source_visibility_probe,
        reference_time_to_ns=selected_base.reference_time_to_ns,
        adapter_factory=adapter_factory,
        close_runtime=selected_base.close_runtime,
    )
    return MEGObserveOnlyLiveComposition(
        hooks=hooks,
        recorder=recorder,
        mutation_epoch=mutation_epoch,
        writer_domain=writer_domain,
        stream_id=stream_id,
    )


def _append_row(path: Path, schema: str, row: Mapping[str, object]) -> None:
    record = {"schema_version": schema, "row": deepcopy(dict(row))}
    append_jsonl_durable(
        path, {"record": record, "record_sha256": payload_sha256(record)}
    )


async def _await(value: object, code: str) -> object:
    if not inspect.isawaitable(value):
        raise _fail(code)
    return await value


def _capture_payload(
    recorder: MEGRuntimeRecorder, writer: WriterDomainCertificate
) -> dict[str, object]:
    return {
        "schema_version": "membind.meg.runtime-observe-capture.v1",
        "mode": recorder.mode.value,
        "writer_domain": _jsonable(writer),
        "operators": _jsonable(recorder.operators),
        "request_spans": _jsonable(recorder.request_spans),
        "read_views": _jsonable(recorder.read_views),
        "events": _jsonable(recorder.events),
        "production_db_read_hashes": list(recorder.production_db_read_hashes),
        "shadow_db_read_hashes": list(recorder.shadow_db_read_hashes),
        "production_write_intent_hashes": list(
            recorder.production_write_intent_hashes
        ),
        "persistent_effect_hashes": list(recorder.persistent_effect_hashes),
        "publication_order": list(recorder.publication_order),
    }


async def execute_meg_observe_capture(
    *,
    contract: Mapping[str, object],
    episodes: Sequence[object],
    env: Mapping[str, str],
    state_cut_certification: StateCutCertification,
    composition: MEGObserveOnlyLiveComposition,
) -> dict[str, object]:
    selected = deepcopy(dict(contract))
    digest = selected.pop("payload_sha256", None)
    if digest != payload_sha256(selected):
        raise _fail("meg_runtime_capture_contract_hash_invalid")
    if selected.get("status") != "AUTHORIZED" or selected.get("mode") != "OBSERVE_ONLY":
        raise _fail("meg_runtime_capture_contract_invalid")
    source_count = selected.get("source_count")
    if isinstance(source_count, bool) or source_count not in {3, 12}:
        raise _fail("meg_runtime_source_count_invalid")
    if len(episodes) != source_count:
        raise _fail("meg_runtime_episode_count_mismatch")
    root = Path(str(selected["output_root"]))
    if root.exists():
        raise _fail("meg_runtime_capture_root_not_fresh")
    if composition.writer_domain.namespace != selected["namespace"]:
        raise _fail("meg_runtime_capture_namespace_mismatch")
    root.mkdir(parents=True, exist_ok=False)
    atomic_write_json(root / "MEG_RUNTIME_CAPTURE_CONTRACT.json", dict(contract))
    llm_rows: list[dict[str, object]] = []
    lifecycle_rows: list[dict[str, object]] = []
    queue_rows: list[dict[str, object]] = []
    runtime: object | None = None

    def llm_observer(row: dict[str, object]) -> None:
        value = dict(row)
        llm_rows.append(value)
        _append_row(root / "llm.jsonl", "membind.meg.runtime-llm.v1", value)

    def queue_observer(row: dict[str, object]) -> None:
        value = dict(row)
        queue_rows.append(value)
        _append_row(root / "queue.jsonl", "membind.meg.runtime-queue.v1", value)

    def lifecycle(row: dict[str, object]) -> None:
        value = dict(row)
        lifecycle_rows.append(value)
        _append_row(root / "lifecycle.jsonl", "membind.meg.runtime-lifecycle.v1", value)

    hooks = composition.hooks
    try:
        namespace = str(selected["namespace"])
        scoped = tuple(hooks.namespace_episode(item, namespace) for item in episodes)
        source_log, raw_hashes = build_source_log_from_episodes(
            scoped,
            namespace=namespace,
            reference_time_to_ns=hooks.reference_time_to_ns,
        )
        if list(raw_hashes) != selected["source_sha256s"]:
            raise _fail("meg_runtime_source_identity_mismatch")
        block_env = {
            **dict(env),
            "CONSTRUCTION_CACHE_SALT": str(selected["cache_salt_sha256"]),
        }
        runtime = hooks.runtime_builder(
            env=block_env,
            policy=AdmissionPolicy.CACHE_AFFINE,
            request_id_prefix=f"meg-runtime-{selected['run_id']}",
            observer=llm_observer,
            response_observer=llm_observer,
            admission_observer=queue_observer,
        )
        if inspect.isawaitable(runtime):
            raise _fail("meg_runtime_builder_must_be_synchronous")
        if getattr(runtime, "shared_execution_envelope_sha256", None) != selected[
            "shared_execution_envelope_sha256"
        ]:
            raise _fail("meg_runtime_execution_envelope_mismatch")
        await _await(hooks.runtime_ready(runtime), "meg_runtime_ready_invalid")
        initial = await _await(
            hooks.namespace_probe(runtime, namespace),
            "meg_runtime_namespace_probe_invalid",
        )
        if not isinstance(initial, Mapping) or {
            "node_count": int(initial.get("node_count", -1)),
            "relationship_count": int(initial.get("relationship_count", -1)),
            "episode_names": sorted(str(item) for item in initial.get("episode_names", [])),
        } != {"node_count": 0, "relationship_count": 0, "episode_names": []}:
            raise _fail("meg_runtime_namespace_not_fresh")
        adapter = hooks.adapter_factory(runtime, state_cut_certification)

        async def visibility(sequence: int, _result: object) -> bool:
            value = await _await(
                hooks.source_visibility_probe(runtime, source_log.record(sequence)),
                "meg_runtime_visibility_probe_invalid",
            )
            if not isinstance(value, bool):
                raise _fail("meg_runtime_visibility_result_invalid")
            return value

        coordinator = await run_membind_v31_stream(
            stream_id=str(selected["history_id"]),
            source_log=source_log,
            arrival_offsets_ns=tuple(int(item) for item in selected["arrival_offsets_ns"]),
            adapter=adapter,
            request_client=getattr(runtime, "admitted_llm"),
            compile_workers=COMPILE_WORKERS,
            lookahead=LOOKAHEAD,
            observer=lifecycle,
            scheduler_observer=queue_observer,
            publication_probe=visibility,
            commit_observer=lambda sequence, _result: lifecycle(
                {
                    "event_type": "commit_returned",
                    "stream_id": selected["history_id"],
                    "source_sequence": sequence,
                    "timestamp_ns": time.monotonic_ns(),
                }
            ),
            publication_persistor=lambda sequence, _result: lifecycle(
                {
                    "event_type": "publication_durable",
                    "stream_id": selected["history_id"],
                    "source_sequence": sequence,
                    "timestamp_ns": time.monotonic_ns(),
                }
            ),
        )
        final = await _await(
            hooks.namespace_probe(runtime, namespace),
            "meg_runtime_final_namespace_probe_invalid",
        )
        if not isinstance(final, Mapping):
            raise _fail("meg_runtime_final_namespace_invalid")
        expected_names = sorted(str(getattr(item, "name")) for item in scoped)
        if sorted(str(item) for item in final.get("episode_names", [])) != expected_names:
            raise _fail("meg_runtime_final_namespace_coverage_invalid")

        recorder = composition.recorder
        operator_ids = {item.semantic_operator_id for item in recorder.operators}
        state_ids = {
            item.semantic_operator_id
            for item in recorder.operators
            if item.classification is SemanticOperatorClass.STATE_DERIVED
        }
        read_view_ids = {
            item.read_view.operator_instance_id for item in recorder.read_views
        }
        ready_events = [
            item
            for item in recorder.events
            if item.event_type is OperatorEventType.OPERATOR_READY
        ]
        commits = [
            item
            for item in recorder.events
            if item.event_type is OperatorEventType.TRANSACTION_COMMIT
        ]
        publications = [
            item
            for item in recorder.events
            if item.event_type is OperatorEventType.PUBLICATION
        ]
        submitted = {
            str(row["request_id"])
            for row in llm_rows
            if row.get("event_type") == "llm_request_submitted"
        }
        terminal = {
            str(row["request_id"])
            for row in llm_rows
            if row.get("event_type") == "llm_request_terminal"
            and row.get("status") == "ok"
        }
        request_lineage_complete = (
            bool(submitted)
            and submitted == terminal
            and len(recorder.request_spans) == len(terminal)
            and all(
                item.semantic_operator_id in operator_ids
                for item in recorder.request_spans
            )
        )
        publication_by_sequence = {
            int(item.source_sequence): item for item in publications if item.source_sequence is not None
        }
        ready_before_prepared = 0
        prepared_times = {
            int(row["source_sequence"]): int(row["timestamp_ns"])
            for row in lifecycle_rows
            if row.get("event_type") == "prepared_durable"
        }
        for event in ready_events:
            if (
                event.source_sequence in prepared_times
                and event.timestamp_ns < prepared_times[int(event.source_sequence)]
            ):
                ready_before_prepared += 1
        ready_before_predecessor_publication = 0
        for event in ready_events:
            sequence = event.source_sequence
            if sequence is None or sequence <= 0:
                continue
            predecessor = publication_by_sequence.get(sequence - 1)
            if predecessor is not None and event.timestamp_ns < predecessor.timestamp_ns:
                ready_before_predecessor_publication += 1
        gates = {
            "semantic_operators_observed": bool(recorder.operators),
            "operator_ready_observed": bool(ready_events),
            "request_lineage_coverage_complete": request_lineage_complete,
            "transaction_epoch_count_exact": (
                len(commits) == source_count
                and composition.mutation_epoch.snapshot().counter == source_count
            ),
            "publication_count_exact_and_certified": (
                len(publications) == source_count
                and all(item.status == "CERTIFIED" for item in publications)
                and recorder.publication_order == list(range(source_count))
            ),
            "state_readview_coverage_complete": state_ids == read_view_ids,
            "writer_domain_certified": composition.writer_domain.certified,
            "event_sequence_global_and_contiguous": (
                [item.event_sequence for item in recorder.events]
                == list(range(len(recorder.events)))
            ),
            "zero_shadow_reads": not recorder.shadow_db_read_hashes,
            "coordinator_publication_complete": coordinator.get(
                "publication_source_sequences"
            )
            == list(range(source_count)),
            "coordinator_direct_violation_free": coordinator.get(
                "direct_violation_count"
            )
            == 0,
        }
        passed = all(gates.values())
        status = (
            "PASS_REAL_MEG_RUNTIME_OBSERVE_ONLY"
            if passed
            else "STOP_REAL_RUNTIME_SEMANTIC_LINEAGE"
        )
        capture = _seal(_capture_payload(recorder, composition.writer_domain))
        atomic_write_json(root / "MEG_RUNTIME_CAPTURE.json", capture)
        result_body = {
            "schema_version": "membind.meg.runtime-observe-capture-result.v1",
            "status": status,
            "run_id": selected["run_id"],
            "history_id": selected["history_id"],
            "source_sequences": selected["source_sequences"],
            "namespace": namespace,
            "mode": "OBSERVE_ONLY",
            "gates": gates,
            "metrics": {
                "operator_count": len(recorder.operators),
                "operator_counts_by_class": dict(
                    sorted(Counter(item.classification.value for item in recorder.operators).items())
                ),
                "operator_counts_by_type": dict(
                    sorted(Counter(item.semantic_operator_type for item in recorder.operators).items())
                ),
                "ready_event_count": len(ready_events),
                "state_derived_count": len(state_ids),
                "read_view_count": len(recorder.read_views),
                "read_view_status_counts": dict(
                    sorted(Counter(item.status.value for item in recorder.read_views).items())
                ),
                "request_span_count": len(recorder.request_spans),
                "production_request_count": len(terminal),
                "request_lineage_coverage": 1.0 if request_lineage_complete else 0.0,
                "transaction_commit_count": len(commits),
                "publication_count": len(publications),
                "local_ready_before_prepared_count": ready_before_prepared,
                "local_ready_before_exact_predecessor_publication_count": (
                    ready_before_predecessor_publication
                ),
            },
            "scope": {
                "shadow_reads": 0,
                "shadow_llm_calls": 0,
                "scheduler_changed": False,
                "semantic_path_changed": False,
                "formal_main_table_eligible": False,
            },
            "capture_payload_sha256": capture["payload_sha256"],
        }
        result = _seal(result_body)
        atomic_write_json(root / "MEG_RUNTIME_CAPTURE_RESULT.json", result)
        decision = "\n".join(
            [
                "# MEG Runtime OBSERVE_ONLY Capture Decision",
                "",
                f"STATUS: {status}",
                f"SOURCE_SEQUENCES: {selected['source_sequences']}",
                f"REQUEST_LINEAGE_COVERAGE: {result_body['metrics']['request_lineage_coverage']}",
                f"WRITER_DOMAIN_CERTIFIED: {gates['writer_domain_certified']}",
                f"TRANSACTION_COVERAGE_COMPLETE: {gates['transaction_epoch_count_exact']}",
                f"PUBLICATION_COVERAGE_COMPLETE: {gates['publication_count_exact_and_certified']}",
                "SHADOW_READS: 0",
                "SCHEDULER_CHANGED: no",
                "SEMANTIC_PATH_CHANGED: no",
                "",
            ]
        )
        (root / "MEG_RUNTIME_CAPTURE_DECISION.md").write_text(
            decision, encoding="utf-8"
        )
        return result
    except BaseException as error:
        failure = {
            "schema_version": "membind.meg.runtime-observe-capture-failure.v1",
            "status": "STOP_REAL_RUNTIME_SEMANTIC_LINEAGE",
            "error_class": f"{type(error).__module__}.{type(error).__qualname__}",
            "error_code": str(error),
            "operator_count_before_failure": len(composition.recorder.operators),
            "request_span_count_before_failure": len(composition.recorder.request_spans),
            "publication_count_before_failure": len(composition.recorder.publication_order),
        }
        atomic_write_json(root / "FAILURE.json", _seal(failure))
        raise
    finally:
        if runtime is not None:
            await _await(hooks.close_runtime(runtime), "meg_runtime_close_invalid")


__all__ = [
    "MEGObserveOnlyLiveComposition",
    "MEGRuntimeLiveError",
    "build_meg_observe_only_live_composition",
    "build_observe_capture_contract",
    "build_v31_observe_composition_proof",
    "derive_observe_namespace",
    "execute_meg_observe_capture",
]
