"""Authority-bound production composition and controller for S5 M*.

The inner controller remains dependency-injected for service-free tests.  The
production wrapper closes the single-use authority chain before constructing a
runtime, then binds the exact frozen workload to Graphiti's semantic adapter.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import re
import sys
import time
from collections.abc import (
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from contextlib import AbstractContextManager, nullcontext
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .artifacts import (
    append_jsonl_durable,
    atomic_write_json,
    payload_sha256,
    sha256_file,
)
from .s5_graphiti_mstar_semantics import GraphitiBindObservation
from .s5_graphiti_native_binding import load_graphiti_native_binding
from .s5_graphiti_semantic_binding import (
    S5GraphitiSemanticBinding,
    load_graphiti_semantic_binding,
)
from .s5_live_authority import (
    consume_s5_live_authority,
    verify_s5_live_authority,
    verify_s5_live_authority_consumption,
)
from .s5_live_preflight import verify_s5_live_preflight
from .s5_mstar_live_semantic_adapter import (
    S5MStarLiveSemanticAdapter,
    materialize_s5_mstar_sources,
)
from .s5_mstar_pipeline import MStarSource, MStarSpec
from .s5_mstar_production_runner import (
    S5MStarProductionRunner,
    S5MStarProductionRunnerError,
    verify_s5_mstar_production_bindings,
)
from .s5_mstar_production_core_identity import (
    verify_s5_mstar_production_core_identity,
)
from .s5_native_method_adapters import S5EpisodeRef
from .s5_production_identity_qualification import (
    bind_s5_production_identity_qualification,
    verify_s5_production_identity_qualification,
)
from .s5_production_runner import verify_s5_production_identity
from .s5_pstar_result_finalizer import verify_s5_pstar_result


EVENT_SCHEMA = "membind.paper-eval-v3.s5-mstar-controller-event.v2"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.s5-mstar-controller-checkpoint.v2"
EXPECTED_SOURCE_COUNT = 49
_RUN_ID = re.compile(r"^s5-mstar-[0-9]{8}-[0-9]{3}$")
_PROJECT = Path(__file__).resolve().parents[2]
_ROOT = _PROJECT.parent
_LEGACY = _ROOT / "membind-validation"
_LEGACY_SRC = _LEGACY / "src"
_DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
_RESULT_VERIFIER = Path(__file__).with_name("s5_mstar_result_finalizer.py")
_PRIVATE_FIELDS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "credential",
    "episode",
    "group_id",
    "messages",
    "namespace",
    "password",
    "prompt",
    "raw_output",
    "raw_response",
    "request",
    "response",
    "secret",
}


class S5MStarControllerError(ValueError):
    """Stable M* workload, composition, or controller lifecycle failure."""


def _fail(code: str) -> S5MStarControllerError:
    return S5MStarControllerError(code)


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_controller_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


def _qualified_error_class(error: BaseException) -> str:
    kind = type(error)
    return f"{kind.__module__}.{kind.__qualname__}"


def _validate_workload(
    episodes: Sequence[S5EpisodeRef], namespace: str
) -> tuple[S5EpisodeRef, ...]:
    if not isinstance(namespace, str) or not namespace:
        raise _fail("namespace_invalid")
    if isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence):
        raise _fail("workload_invalid")
    selected = tuple(episodes)
    if (
        len(selected) != EXPECTED_SOURCE_COUNT
        or any(not isinstance(item, S5EpisodeRef) for item in selected)
        or [item.source_sequence for item in selected]
        != list(range(EXPECTED_SOURCE_COUNT))
    ):
        raise _fail("workload_invalid")
    for index, item in enumerate(selected):
        native = item.native_episode
        if getattr(native, "group_id", None) != namespace:
            raise _fail("namespace_binding_invalid")
        if getattr(native, "source_sequence", None) != index:
            raise _fail("workload_source_sequence_invalid")
        if getattr(native, "source_hash", None) != item.source_sha256:
            raise _fail("workload_source_identity_invalid")
    return selected


def _source_manifest(episodes: Sequence[S5EpisodeRef]) -> str:
    return payload_sha256(
        [
            {
                "source_sequence": item.source_sequence,
                "source_sha256": item.source_sha256,
            }
            for item in episodes
        ]
    )


@dataclass(frozen=True)
class S5MStarControllerPaths:
    """Fresh run-scoped outputs owned by the inner M* controller."""

    controller_root: Path
    attempt_root: Path


@dataclass(frozen=True)
class S5MStarLivePaths:
    """Immutable inputs and single-use outputs for one live M* authority."""

    production_identity: Path
    production_identity_qualification: Path
    production_core_identity: Path
    fx0_qualification: Path
    current_stage_pointer: Path
    preflight: Path
    authority: Path
    predecessor: Path
    consumption: Path
    controller_root: Path
    attempt_root: Path


@dataclass(frozen=True)
class S5MStarProductionPaths:
    """Production files needed to materialize the frozen 49-episode smoke."""

    live: S5MStarLivePaths
    env_file: Path
    dataset: Path = _DATASET
    frozen_split: Path = _LEGACY / "artifacts/dataset/frozen_split_v1_3.json"
    dataset_builder: Path = _LEGACY_SRC / "dataset.py"
    legacy_src: Path = _LEGACY_SRC


SemanticPrepare = Callable[[object, int], Awaitable[object]]
LatestStateBind = Callable[[object, int, int, tuple[int, ...]], Awaitable[object]]
CommitEvidence = Callable[
    [object, int, int, tuple[int, ...]], Awaitable[str] | str
]
ClockNs = Callable[[], int]
FailureTelemetrySnapshot = Callable[[], Sequence[Mapping[str, object]]]
TelemetryScope = Callable[[str, int], AbstractContextManager[object]]


@dataclass(frozen=True)
class S5MStarRuntimeComposition:
    """The exact callbacks and sources consumed by the durable M* runner."""

    sources: tuple[MStarSource, ...]
    semantic_prepare: SemanticPrepare
    latest_state_bind: LatestStateBind
    commit_evidence: CommitEvidence
    telemetry_clock_ns: ClockNs
    failure_telemetry_snapshot: FailureTelemetrySnapshot

    def __post_init__(self) -> None:
        if (
            len(self.sources) != EXPECTED_SOURCE_COUNT
            or any(not isinstance(item, MStarSource) for item in self.sources)
            or [item.source_sequence for item in self.sources]
            != list(range(EXPECTED_SOURCE_COUNT))
        ):
            raise _fail("composition_sources_invalid")
        if any(item.logical_time_ns is None for item in self.sources):
            raise _fail("composition_logical_time_missing")
        for callback, code in (
            (self.semantic_prepare, "semantic_prepare_invalid"),
            (self.latest_state_bind, "latest_state_bind_invalid"),
            (self.commit_evidence, "commit_evidence_invalid"),
            (self.telemetry_clock_ns, "telemetry_clock_invalid"),
            (
                self.failure_telemetry_snapshot,
                "failure_telemetry_snapshot_invalid",
            ),
        ):
            if not callable(callback):
                raise _fail(code)


def build_s5_mstar_runtime_composition(
    *,
    runtime: object,
    episodes: Sequence[S5EpisodeRef],
    namespace: str,
    semantic_binding: S5GraphitiSemanticBinding,
    graphiti_episode_kwargs: Callable[[object], Mapping[str, object]],
    episodic_node_type: Callable[..., object],
    epoch_clock_ns: ClockNs,
    commit_evidence: CommitEvidence,
    telemetry_clock_ns: ClockNs = time.monotonic_ns,
    telemetry_scope: TelemetryScope | None = None,
) -> S5MStarRuntimeComposition:
    """Bind the frozen workload to the pinned Graphiti M* semantic adapter."""

    selected = _validate_workload(episodes, namespace)
    if not callable(commit_evidence):
        raise _fail("commit_evidence_invalid")
    if not callable(epoch_clock_ns) or not callable(telemetry_clock_ns):
        raise _fail("clock_invalid")
    if telemetry_scope is not None and not callable(telemetry_scope):
        raise _fail("telemetry_scope_invalid")
    graphiti = getattr(runtime, "graphiti", None)
    if graphiti is None:
        raise _fail("runtime_graphiti_missing")
    try:
        sources = materialize_s5_mstar_sources(
            tuple(item.native_episode for item in selected),
            namespace=namespace,
            epoch_clock_ns=epoch_clock_ns,
        )
        adapter = S5MStarLiveSemanticAdapter(
            graphiti=graphiti,
            semantic_binding=semantic_binding,
            graphiti_episode_kwargs=graphiti_episode_kwargs,
            episodic_node_type=episodic_node_type,
        )
    except S5MStarControllerError:
        raise
    except Exception:
        raise _fail("runtime_composition_invalid") from None
    if [item.source_sha256 for item in sources] != [
        item.source_sha256 for item in selected
    ]:
        raise _fail("runtime_source_identity_drift")

    selected_scope: TelemetryScope = telemetry_scope or (
        lambda _run_id, _source_sequence: nullcontext()
    )

    async def scoped_prepare(source: object, logical_time_ns: int) -> object:
        source_sequence = getattr(source, "source_sequence", None)
        if (
            isinstance(source_sequence, bool)
            or not isinstance(source_sequence, int)
            or source_sequence < 0
        ):
            raise _fail("telemetry_source_sequence_invalid")
        with selected_scope(namespace, source_sequence):
            return await adapter.prepare(source, logical_time_ns)

    async def scoped_bind(
        prepared: object,
        logical_time_ns: int,
        source_sequence: int,
        visible_publication_prefix: tuple[int, ...],
    ) -> object:
        with selected_scope(namespace, source_sequence):
            return await adapter.bind(
                prepared,
                logical_time_ns,
                source_sequence,
                visible_publication_prefix,
            )

    llm_client = getattr(runtime, "llm_client", None)
    if llm_client is None:
        llm_client = getattr(graphiti, "llm_client", None)

    def failure_telemetry_snapshot() -> tuple[Mapping[str, object], ...]:
        raw = getattr(llm_client, "call_events", ())
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            return ()
        projected: list[dict[str, object]] = []
        for ordinal, item in enumerate(raw):
            if not isinstance(item, Mapping):
                return ()
            episode_key = item.get("episode_key")
            source_sequence = None
            if (
                isinstance(episode_key, (tuple, list))
                and len(episode_key) == 2
                and isinstance(episode_key[1], int)
                and not isinstance(episode_key[1], bool)
                and episode_key[1] >= 0
            ):
                source_sequence = int(episode_key[1])
            usage = item.get("token_usage")
            if not isinstance(usage, Mapping):
                usage = {}

            def token(name: str) -> int | None:
                value = usage.get(name)
                return (
                    int(value)
                    if isinstance(value, int)
                    and not isinstance(value, bool)
                    and value >= 0
                    else None
                )

            max_tokens = item.get("max_tokens")
            max_tokens = (
                int(max_tokens)
                if isinstance(max_tokens, int)
                and not isinstance(max_tokens, bool)
                and max_tokens > 0
                else 0
            )
            finish_reason = item.get("finish_reason")
            projected.append(
                {
                    "request_ordinal": ordinal,
                    "source_sequence": source_sequence,
                    "response_format_type": None,
                    "json_schema_name": None,
                    "json_schema_sha256": None,
                    "requested_max_tokens": max_tokens,
                    "prompt_tokens": token("prompt_tokens"),
                    "completion_tokens": token("completion_tokens"),
                    "total_tokens": token("total_tokens"),
                    "finish_reason": (
                        finish_reason if isinstance(finish_reason, str) else None
                    ),
                    "transport_outcome": "response_received",
                    "http_status": None,
                    "error_class": None,
                }
            )
        return tuple(projected)

    return S5MStarRuntimeComposition(
        sources=sources,
        semantic_prepare=scoped_prepare,
        latest_state_bind=scoped_bind,
        commit_evidence=commit_evidence,
        telemetry_clock_ns=telemetry_clock_ns,
        failure_telemetry_snapshot=failure_telemetry_snapshot,
    )


class _ControllerEvidence:
    def __init__(self, root: Path, *, run_id: str) -> None:
        self.root = Path(root)
        if self.root.exists():
            raise _fail("single_use_output_exists")
        self.root.mkdir(parents=True)
        self.events_path = self.root / "events.jsonl"
        self.checkpoint_path = self.root / "checkpoint.json"
        self.run_id = run_id
        self.events: list[dict[str, object]] = []

    def append(self, event_type: str, **fields: object) -> None:
        event = {
            "schema_version": EVENT_SCHEMA,
            "event_sequence": len(self.events),
            "event_type": event_type,
            "run_id": self.run_id,
            **fields,
        }
        _assert_public(event)
        append_jsonl_durable(
            self.events_path,
            {"event": event, "event_sha256": payload_sha256(event)},
        )
        self.events.append(event)

    def checkpoint(self, **fields: object) -> None:
        checkpoint = {
            "schema_version": CHECKPOINT_SCHEMA,
            "run_id": self.run_id,
            "event_count": len(self.events),
            **fields,
        }
        _assert_public(checkpoint)
        checkpoint["checkpoint_sha256"] = payload_sha256(checkpoint)
        atomic_write_json(self.checkpoint_path, checkpoint)


def _failure(
    stage: str,
    error_class: str,
    *,
    failure_binding: Mapping[str, str] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "incomplete_non_mergeable",
        "failure_stage": stage,
        "error_class": error_class,
        "scientific_outcome_candidate": False,
        "resume_authorized": False,
        "namespace_cleanup_authorized": False,
        "scientific_pass_authorized": False,
        "next_method_authorized": False,
        "current_stage_pointer_update_authorized": False,
    }
    if failure_binding is not None:
        result.update(dict(failure_binding))
    return result


def _runner_failure_binding(value: Mapping[str, object]) -> dict[str, str]:
    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        raise _fail("runner_failure_payload_invalid")
    result: dict[str, str] = {}
    for field in (
        "failure_envelope_file_sha256",
        "failure_envelope_payload_sha256",
    ):
        selected = value.get(field)
        if (
            not isinstance(selected, str)
            or re.fullmatch(r"[0-9a-f]{64}", selected) is None
            or payload.get(field) != selected
        ):
            raise _fail("runner_failure_envelope_binding_invalid")
        result[field] = selected
    classification = value.get("failure_classification")
    if (
        classification not in {
            "CAP_EXHAUSTED",
            "STRUCTURED_INVALID",
            "UNCLASSIFIED",
        }
        or payload.get("failure_classification") != classification
    ):
        raise _fail("runner_failure_classification_invalid")
    result["failure_classification"] = str(classification)
    return result


def _runner_failure_class(value: Mapping[str, object]) -> str:
    payload = value.get("payload")
    if isinstance(payload, Mapping):
        if isinstance(payload.get("error_class"), str):
            return str(payload["error_class"])
        events = payload.get("events")
        if isinstance(events, Sequence) and not isinstance(events, (str, bytes)):
            for event in reversed(events):
                if isinstance(event, Mapping) and isinstance(
                    event.get("error_class"), str
                ):
                    return str(event["error_class"])
    return "paper_eval.s5_mstar_controller.MStarAttemptIncomplete"


async def _await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def execute_s5_mstar_controller(
    *,
    paths: S5MStarControllerPaths,
    run_id: str,
    namespace: str,
    episodes: Sequence[S5EpisodeRef],
    identity: Mapping[str, object],
    production_core_identity: Mapping[str, object],
    fx0_qualification: Mapping[str, object],
    runtime_factory: Callable[[], object],
    readiness: Callable[[object], Awaitable[object] | object],
    composition_factory: Callable[
        [object, tuple[S5EpisodeRef, ...], str], S5MStarRuntimeComposition
    ],
    close_runtime: Callable[[object], Awaitable[object] | object],
    runner_factory: Callable[..., object] = S5MStarProductionRunner,
) -> dict[str, object]:
    """Execute one fresh M* attempt after side-effect-free input validation."""

    if not isinstance(paths, S5MStarControllerPaths):
        raise _fail("controller_paths_invalid")
    if (
        _RUN_ID.fullmatch(run_id or "") is None
        or namespace != f"pev3-{run_id}"
    ):
        raise _fail("run_identity_invalid")
    if paths.controller_root.exists() or paths.attempt_root.exists():
        raise _fail("single_use_output_exists")
    selected = _validate_workload(episodes, namespace)
    for callback in (
        runtime_factory,
        readiness,
        composition_factory,
        close_runtime,
        runner_factory,
    ):
        if not callable(callback):
            raise _fail("controller_callback_invalid")
    spec = MStarSpec(
        run_id=run_id,
        production_core_identity_sha256=str(
            production_core_identity.get("identity_sha256", "")
        ),
        prepare_concurrency=2,
    )
    try:
        bindings = verify_s5_mstar_production_bindings(
            spec=spec,
            identity=identity,
            production_core_identity=production_core_identity,
            fx0_qualification=fx0_qualification,
        )
    except S5MStarProductionRunnerError:
        raise _fail("production_binding_invalid") from None
    checked_identity = bindings["identity"]
    checked_core = bindings["production_core_identity"]

    evidence = _ControllerEvidence(paths.controller_root, run_id=run_id)
    evidence.append(
        "controller_started",
        method="M*",
        source_manifest_sha256=_source_manifest(selected),
        production_identity_sha256=checked_identity["identity_sha256"],
        production_core_identity_sha256=checked_core["identity_sha256"],
    )
    runtime: object | None = None
    stage = "runtime_construction"
    try:
        runtime = runtime_factory()
        evidence.append("runtime_constructed", method="M*")
        stage = "runtime_readiness"
        await _await(readiness(runtime))
        evidence.append("runtime_ready", method="M*")
        stage = "runtime_composition"
        composition = await _await(composition_factory(runtime, selected, namespace))
        if not isinstance(composition, S5MStarRuntimeComposition):
            raise _fail("runtime_composition_shape_invalid")
        if [source.source_sha256 for source in composition.sources] != [
            item.source_sha256 for item in selected
        ]:
            raise _fail("runtime_composition_source_drift")
        evidence.append(
            "runtime_composed",
            method="M*",
            source_count=len(composition.sources),
        )
        stage = "runner_construction"
        runner = runner_factory(
            attempt_root=paths.attempt_root,
            spec=spec,
            identity=checked_identity,
            production_core_identity=checked_core,
            fx0_qualification=fx0_qualification,
            sources=composition.sources,
            semantic_prepare=composition.semantic_prepare,
            latest_state_bind=composition.latest_state_bind,
            commit_evidence=composition.commit_evidence,
            clock_ns=composition.telemetry_clock_ns,
            failure_telemetry_snapshot=composition.failure_telemetry_snapshot,
        )
        evidence.append("mstar_runner_started", method="M*")
        stage = "mstar_execution"
        result = await _await(runner.run())
        stage = "runtime_close"
        selected_runtime, runtime = runtime, None
        await _await(close_runtime(selected_runtime))
        evidence.append("runtime_closed", method="M*")

        valid = (
            isinstance(result, Mapping)
            and result.get("status") == "complete"
            and isinstance(result.get("payload"), Mapping)
            and result["payload"].get("status") == "PASS"
            and result.get("production_identity_sha256")
            == checked_identity["identity_sha256"]
            and result.get("production_core_identity_sha256")
            == checked_core["identity_sha256"]
            and result.get("resume_authorized") is False
        )
        if not valid:
            error_class = (
                _runner_failure_class(result)
                if isinstance(result, Mapping)
                else "paper_eval.s5_mstar_controller.MStarAttemptInvalid"
            )
            failure_binding: dict[str, str] | None = None
            if isinstance(result, Mapping):
                try:
                    failure_binding = _runner_failure_binding(result)
                except S5MStarControllerError:
                    error_class = (
                        "paper_eval.s5_mstar_controller."
                        "MStarAttemptInvalid"
                    )
            failure = _failure(
                "mstar_execution",
                error_class,
                failure_binding=failure_binding,
            )
            evidence.append(
                "mstar_attempt_incomplete",
                method="M*",
                error_class=error_class,
                **(failure_binding or {}),
            )
            evidence.checkpoint(**failure)
            return failure
        evidence.append(
            "runner_evidence_complete",
            method="M*",
            production_identity_sha256=checked_identity["identity_sha256"],
            production_core_identity_sha256=checked_core["identity_sha256"],
        )
        completed = {
            "status": "controller_complete_evidence_only",
            "attempt_status": "complete",
            "production_core_identity_sha256": checked_core["identity_sha256"],
            "scientific_outcome_candidate": True,
            "resume_authorized": False,
            "namespace_cleanup_authorized": False,
            "scientific_pass_authorized": False,
            "next_method_authorized": False,
            "current_stage_pointer_update_authorized": False,
        }
        evidence.checkpoint(**completed)
        return completed
    except Exception as error:
        failure = _failure(stage, _qualified_error_class(error))
        evidence.append(
            "controller_failure",
            method="M*",
            failure_stage=stage,
            error_class=failure["error_class"],
        )
        evidence.checkpoint(**failure)
        return failure
    finally:
        if runtime is not None:
            try:
                await _await(close_runtime(runtime))
            except Exception:
                pass


def _import_exact(module_name: str, path: Path) -> object:
    source_root = str(Path(path).resolve().parent)
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    module = importlib.import_module(module_name)
    if Path(str(getattr(module, "__file__", ""))).resolve() != Path(path).resolve():
        raise _fail(f"{module_name}_source_drift")
    return module


def _production_episodes(
    paths: S5MStarProductionPaths, namespace: str
) -> tuple[S5EpisodeRef, ...]:
    from .s1_live import load_fixed_history

    history = load_fixed_history(paths.dataset, paths.frozen_split)
    builder_module = _import_exact("dataset", paths.dataset_builder)
    builder = getattr(builder_module, "build_episodes", None)
    if not callable(builder):
        raise _fail("dataset_builder_missing")
    native = tuple(builder(dict(history)))
    refs: list[S5EpisodeRef] = []
    for index, episode in enumerate(native):
        try:
            rebound = replace(episode, group_id=namespace)
        except (TypeError, ValueError):
            raise _fail("frozen_episode_rebind_failed") from None
        refs.append(
            S5EpisodeRef(
                index,
                str(getattr(rebound, "source_hash", "")),
                rebound,
            )
        )
    _validate_workload(refs, namespace)
    return tuple(refs)


def _preconsume_production(
    *,
    paths: S5MStarLivePaths,
    episodes: Sequence[S5EpisodeRef],
    git_commit: str,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, Any],
    str,
]:
    """Verify the complete immutable chain before the first live side effect."""

    if not isinstance(paths, S5MStarLivePaths):
        raise _fail("live_paths_invalid")
    if any(
        selected.exists()
        for selected in (
            paths.consumption,
            paths.controller_root,
            paths.attempt_root,
        )
    ):
        raise _fail("single_use_output_exists")
    try:
        identity = verify_s5_production_identity(
            _read_json(paths.production_identity, "production_identity_invalid")
        )
        qualification = verify_s5_production_identity_qualification(
            _read_json(
                paths.production_identity_qualification,
                "production_identity_qualification_invalid",
            )
        )
        qualification_binding = bind_s5_production_identity_qualification(
            qualification,
            file_sha256=sha256_file(paths.production_identity_qualification),
        )
        core = verify_s5_mstar_production_core_identity(
            _read_json(paths.production_core_identity, "production_core_identity_invalid")
        )
        fx0 = _read_json(paths.fx0_qualification, "fx0_qualification_invalid")
        preflight = verify_s5_live_preflight(
            _read_json(paths.preflight, "preflight_invalid")
        )
        authority = verify_s5_live_authority(
            _read_json(paths.authority, "authority_invalid")
        )
        predecessor = verify_s5_pstar_result(
            _read_json(paths.predecessor, "predecessor_invalid")
        )
    except Exception:
        raise _fail("qualified_chain_invalid") from None

    pointer = _read_json(paths.current_stage_pointer, "pointer_invalid")
    pointer_payload = pointer.get("payload")
    run = authority["payload"].get("run")
    if not isinstance(run, Mapping):
        raise _fail("authority_run_invalid")
    run_id = str(run.get("run_id", ""))
    namespace = str(run.get("namespace", ""))
    spec = MStarSpec(
        run_id=run_id,
        production_core_identity_sha256=str(core.get("identity_sha256", "")),
        prepare_concurrency=2,
    )
    try:
        verify_s5_mstar_production_bindings(
            spec=spec,
            identity=identity,
            production_core_identity=core,
            fx0_qualification=fx0,
        )
    except Exception:
        raise _fail("production_binding_invalid") from None

    predecessor_payload = predecessor.get("payload")
    authority_predecessor = authority["payload"].get("predecessor")
    authority_fx0 = authority["payload"].get("fx0_qualification")
    qualified_fx0 = qualification_binding.get("mstar_fx0")
    source_binding = authority["payload"].get("source_sha256")
    if (
        identity.get("method") != "M*"
        or qualification_binding.get("method") != "M*"
        or qualification_binding.get("production_identity_sha256")
        != identity.get("identity_sha256")
        or qualification_binding.get("production_identity_file_sha256")
        != sha256_file(paths.production_identity)
        or not isinstance(pointer_payload, Mapping)
        or pointer.get("payload_sha256") != payload_sha256(pointer_payload)
        or pointer_payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or qualification_binding.get("current_stage_pointer", {}).get("file_sha256")
        != sha256_file(paths.current_stage_pointer)
        or preflight["payload"].get("method") != "M*"
        or preflight["payload"].get("production_identity_qualification")
        != qualification_binding
        or authority.get("git_commit") != git_commit
        or authority["payload"].get("method") != "M*"
        or authority["payload"].get("production_identity_qualification")
        != qualification_binding
        or authority["payload"].get("preflight_file_sha256")
        != sha256_file(paths.preflight)
        or authority["payload"].get("preflight_payload_sha256")
        != preflight.get("payload_sha256")
        or run.get("method") != "M*"
        or run.get("configured_concurrency") != 2
        or _RUN_ID.fullmatch(run_id) is None
        or namespace != f"pev3-{run_id}"
        or run.get("source_manifest_sha256") != _source_manifest(episodes)
        or not isinstance(predecessor_payload, Mapping)
        or predecessor_payload.get("method") != "P*"
        or predecessor_payload.get("verdict") != "SCIENTIFIC_OUTCOME_COMPLETE"
        or not isinstance(authority_predecessor, Mapping)
        or authority_predecessor.get("method") != "P*"
        or authority_predecessor.get("verdict") != "SCIENTIFIC_OUTCOME_COMPLETE"
        or authority_predecessor.get("result_file_sha256")
        != sha256_file(paths.predecessor)
        or authority_predecessor.get("result_payload_sha256")
        != predecessor.get("payload_sha256")
        or not isinstance(qualified_fx0, Mapping)
        or not isinstance(authority_fx0, Mapping)
        or authority_fx0.get("qualification_file_sha256")
        != sha256_file(paths.fx0_qualification)
        or authority_fx0.get("qualification_payload_sha256")
        != fx0.get("payload_sha256")
        or authority_fx0.get("production_parity_payload_sha256")
        != qualified_fx0.get("fx0_artifact_payload_sha256")
        or not isinstance(source_binding, Mapping)
        or source_binding.get("controller") != sha256_file(Path(__file__))
        or source_binding.get("result_verifier") != sha256_file(_RESULT_VERIFIER)
    ):
        raise _fail("qualified_chain_binding_invalid")
    _validate_workload(episodes, namespace)
    return identity, core, fx0, authority, sha256_file(paths.authority)


def _consumed_checker(
    paths: S5MStarLivePaths, authority: Mapping[str, Any]
) -> Callable[[object], object]:
    authority_file_sha = sha256_file(paths.authority)

    def check(action: object) -> object:
        if getattr(action, "value", action) != "native_characterization_c0":
            raise _fail("runtime_live_action_invalid")
        try:
            consumption = verify_s5_live_authority_consumption(
                _read_json(paths.consumption, "authority_consumption_invalid")
            )
        except Exception:
            raise _fail("authority_consumption_invalid") from None
        payload = consumption["payload"]
        if (
            payload.get("method") != "M*"
            or payload.get("run") != authority["payload"].get("run")
            or payload.get("authority_file_sha256") != authority_file_sha
            or payload.get("authority_payload_sha256")
            != authority.get("payload_sha256")
        ):
            raise _fail("authority_consumption_binding_invalid")
        return {"status": "S5_AUTHORITY_CONSUMED"}

    return check


def _commit_evidence(
    result: object,
    logical_time_ns: int,
    source_sequence: int,
    visible_prefix: tuple[int, ...],
) -> str:
    if (
        not isinstance(result, GraphitiBindObservation)
        or result.source_sequence != source_sequence
        or result.logical_time_ns != logical_time_ns
        or visible_prefix != tuple(range(source_sequence))
    ):
        raise _fail("commit_observation_binding_invalid")
    return payload_sha256(
        {
            "observation": asdict(result),
            "source_sequence": source_sequence,
            "logical_time_ns": logical_time_ns,
            "visible_publication_prefix": list(visible_prefix),
        }
    )


async def execute_s5_mstar_production(
    *, paths: S5MStarProductionPaths, git_commit: str
) -> dict[str, object]:
    """Consume one M*(C=2) authority, then execute the exact production path."""

    if not isinstance(paths, S5MStarProductionPaths):
        raise _fail("production_paths_invalid")
    raw_authority = verify_s5_live_authority(
        _read_json(paths.live.authority, "authority_invalid")
    )
    run = raw_authority["payload"].get("run")
    if not isinstance(run, Mapping):
        raise _fail("authority_run_invalid")
    episodes = _production_episodes(paths, str(run.get("namespace", "")))
    identity, core, fx0, authority, authority_file_sha = _preconsume_production(
        paths=paths.live,
        episodes=episodes,
        git_commit=git_commit,
    )
    run_id = str(run["run_id"])
    try:
        consume_s5_live_authority(
            authority=authority,
            authority_file_sha256=authority_file_sha,
            output_path=paths.live.consumption,
            git_commit=git_commit,
            run_id=f"{run_id}-authority-consumption",
        )
    except Exception:
        raise _fail("authority_consumption_failed") from None

    def runtime_factory() -> object:
        graphiti_native = _import_exact(
            "graphiti_native", paths.legacy_src / "graphiti_native.py"
        )
        loader = getattr(graphiti_native, "load_env_file", None)
        if not callable(loader) or not isinstance(loader(paths.env_file), Mapping):
            raise _fail("environment_invalid")
        runtime_module = _import_exact(
            "native_characterization_runtime",
            paths.legacy_src / "native_characterization_runtime.py",
        )
        builder = getattr(runtime_module, "build_u0_graphiti_from_env", None)
        if not callable(builder):
            raise _fail("runtime_builder_missing")
        return builder(
            authorization_checker=_consumed_checker(paths.live, authority),
            live_action="native_characterization_c0",
            env_loader=lambda: None,
            structured_output_mode="json_schema",
        )

    async def readiness(runtime: object) -> None:
        from .s5_a0_controller import ensure_s5_a0_runtime_ready

        await ensure_s5_a0_runtime_ready(runtime)

    def composition_factory(
        runtime: object,
        selected: tuple[S5EpisodeRef, ...],
        namespace: str,
    ) -> S5MStarRuntimeComposition:
        native_binding = load_graphiti_native_binding()
        semantic_binding = load_graphiti_semantic_binding()
        graphiti_native = _import_exact(
            "graphiti_native", paths.legacy_src / "graphiti_native.py"
        )
        telemetry_scope = getattr(graphiti_native, "episode_scope", None)
        if not callable(telemetry_scope):
            raise _fail("telemetry_scope_missing")
        try:
            from graphiti_core.nodes import EpisodicNode
        except Exception:
            raise _fail("episodic_node_type_missing") from None
        return build_s5_mstar_runtime_composition(
            runtime=runtime,
            episodes=selected,
            namespace=namespace,
            semantic_binding=semantic_binding,
            graphiti_episode_kwargs=native_binding.graphiti_episode_kwargs,
            episodic_node_type=EpisodicNode,
            epoch_clock_ns=time.time_ns,
            commit_evidence=_commit_evidence,
            telemetry_clock_ns=time.monotonic_ns,
            telemetry_scope=telemetry_scope,
        )

    async def close_runtime(runtime: object) -> None:
        from .s5_a0_controller import close_s5_a0_runtime

        await close_s5_a0_runtime(runtime)

    return await execute_s5_mstar_controller(
        paths=S5MStarControllerPaths(
            controller_root=paths.live.controller_root,
            attempt_root=paths.live.attempt_root,
        ),
        run_id=run_id,
        namespace=str(run["namespace"]),
        episodes=episodes,
        identity=identity,
        production_core_identity=core,
        fx0_qualification=fx0,
        runtime_factory=runtime_factory,
        readiness=readiness,
        composition_factory=composition_factory,
        close_runtime=close_runtime,
    )


def _read_json(path: Path, code: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def inspect_s5_mstar_controller_attempt(root: Path) -> dict[str, object]:
    """Verify the controller event stream and terminal checkpoint seals."""

    root = Path(root)
    try:
        records = [
            json.loads(line)
            for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail("controller_events_invalid") from None
    events: list[dict[str, object]] = []
    for record in records:
        if (
            not isinstance(record, Mapping)
            or set(record) != {"event", "event_sha256"}
            or not isinstance(record.get("event"), Mapping)
            or record.get("event_sha256") != payload_sha256(record["event"])
        ):
            raise _fail("controller_event_invalid")
        event = deepcopy(dict(record["event"]))
        _assert_public(event)
        events.append(event)
    checkpoint = _read_json(
        root / "checkpoint.json", "controller_checkpoint_invalid"
    )
    seal = checkpoint.pop("checkpoint_sha256", None)
    if (
        not events
        or [event.get("event_sequence") for event in events]
        != list(range(len(events)))
        or events[0].get("event_type") != "controller_started"
        or checkpoint.get("event_count") != len(events)
        or seal != payload_sha256(checkpoint)
        or checkpoint.get("resume_authorized") is not False
        or checkpoint.get("namespace_cleanup_authorized") is not False
        or checkpoint.get("scientific_pass_authorized") is not False
        or checkpoint.get("next_method_authorized") is not False
        or checkpoint.get("current_stage_pointer_update_authorized") is not False
    ):
        raise _fail("controller_evidence_invalid")
    checkpoint["checkpoint_sha256"] = seal
    return {"events": events, "checkpoint": checkpoint}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute one qualified S5 M*(C=2) smoke"
    )
    for name in (
        "production-identity",
        "production-identity-qualification",
        "production-core-identity",
        "fx0-qualification",
        "preflight",
        "authority",
        "predecessor",
        "run-root",
    ):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument(
        "--current-stage-pointer",
        type=Path,
        default=_PROJECT / "runtime/CURRENT_STAGE_STATUS.json",
    )
    parser.add_argument("--env-file", type=Path, default=_LEGACY / ".env")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.run_root)
    live = S5MStarLivePaths(
        production_identity=args.production_identity,
        production_identity_qualification=args.production_identity_qualification,
        production_core_identity=args.production_core_identity,
        fx0_qualification=args.fx0_qualification,
        current_stage_pointer=args.current_stage_pointer,
        preflight=args.preflight,
        authority=args.authority,
        predecessor=args.predecessor,
        consumption=root / "authority_consumption.json",
        controller_root=root / "controller",
        attempt_root=root / "attempt",
    )
    try:
        result = asyncio.run(
            execute_s5_mstar_production(
                paths=S5MStarProductionPaths(live=live, env_file=args.env_file),
                git_commit=str(args.git_commit),
            )
        )
    except Exception as error:
        print(
            json.dumps(
                {"status": "error", "error_class": type(error).__name__},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "controller_complete_evidence_only" else 2


__all__ = [
    "EXPECTED_SOURCE_COUNT",
    "S5MStarControllerError",
    "S5MStarControllerPaths",
    "S5MStarLivePaths",
    "S5MStarProductionPaths",
    "S5MStarRuntimeComposition",
    "build_s5_mstar_runtime_composition",
    "execute_s5_mstar_controller",
    "execute_s5_mstar_production",
    "inspect_s5_mstar_controller_attempt",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
