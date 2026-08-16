"""Minimal offline-qualified runtime composition and controller for S5 M*.

The module contains no environment loader, live authority consumer, or CLI.
Those remain separate gates.  A future live wrapper may call this controller
only after it has verified and consumed method-specific authority.
"""

from __future__ import annotations

import inspect
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import append_jsonl_durable, atomic_write_json, payload_sha256
from .s5_graphiti_semantic_binding import S5GraphitiSemanticBinding
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
from .s5_native_method_adapters import S5EpisodeRef


EVENT_SCHEMA = "membind.paper-eval-v3.s5-mstar-controller-event.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.s5-mstar-controller-checkpoint.v1"
EXPECTED_SOURCE_COUNT = 49
_RUN_ID = re.compile(r"^s5-mstar-[0-9]{8}-[0-9]{3}$")
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


SemanticPrepare = Callable[[object, int], Awaitable[object]]
LatestStateBind = Callable[[object, int, int, tuple[int, ...]], Awaitable[object]]
CommitEvidence = Callable[
    [object, int, int, tuple[int, ...]], Awaitable[str] | str
]
ClockNs = Callable[[], int]


@dataclass(frozen=True)
class S5MStarRuntimeComposition:
    """The exact callbacks and sources consumed by the durable M* runner."""

    sources: tuple[MStarSource, ...]
    semantic_prepare: SemanticPrepare
    latest_state_bind: LatestStateBind
    commit_evidence: CommitEvidence
    telemetry_clock_ns: ClockNs

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
) -> S5MStarRuntimeComposition:
    """Bind the frozen workload to the pinned Graphiti M* semantic adapter."""

    selected = _validate_workload(episodes, namespace)
    if not callable(commit_evidence):
        raise _fail("commit_evidence_invalid")
    if not callable(epoch_clock_ns) or not callable(telemetry_clock_ns):
        raise _fail("clock_invalid")
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
    return S5MStarRuntimeComposition(
        sources=sources,
        semantic_prepare=adapter.prepare,
        latest_state_bind=adapter.bind,
        commit_evidence=commit_evidence,
        telemetry_clock_ns=telemetry_clock_ns,
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


def _failure(stage: str, error_class: str) -> dict[str, object]:
    return {
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
            failure = _failure("mstar_execution", error_class)
            evidence.append(
                "mstar_attempt_incomplete",
                method="M*",
                error_class=error_class,
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


__all__ = [
    "EXPECTED_SOURCE_COUNT",
    "S5MStarControllerError",
    "S5MStarControllerPaths",
    "S5MStarRuntimeComposition",
    "build_s5_mstar_runtime_composition",
    "execute_s5_mstar_controller",
    "inspect_s5_mstar_controller_attempt",
]
