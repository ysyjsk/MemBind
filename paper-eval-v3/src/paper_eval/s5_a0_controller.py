"""Single-use production controller for the S5 Native A0 smoke.

All public artifacts are verified before authority consumption.  Only after
the consumption is durably written may the injected production dependencies
load private settings, construct Graphiti, or perform readiness/live work.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import re
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .artifacts import append_jsonl_durable, atomic_write_json, payload_sha256, sha256_file
from .s5_live_authority import (
    S5LiveAuthorityError,
    consume_s5_live_authority,
    verify_s5_live_authority,
    verify_s5_live_authority_consumption,
)
from .s5_live_preflight import S5LivePreflightError, verify_s5_live_preflight
from .s5_native_method_adapters import A0, S5EpisodeRef, S5MethodSpec
from .s5_a0_production_identity_materializer import (
    S5A0MaterializationPaths,
    materialize_s5_a0_production_identity,
    verify_s5_a0_production_identity_materialization,
)
from .s5_graphiti_native_binding import load_graphiti_native_binding
from .s5_production_identity_qualification import (
    S5ProductionIdentityQualificationError,
    bind_s5_production_identity_qualification,
    verify_s5_production_identity_qualification,
)
from .s5_production_runner import (
    S5ProductionIdentityError,
    S5ProductionRunner,
    verify_s5_production_identity,
)


EVENT_SCHEMA = "membind.paper-eval-v3.s5-a0-controller-event.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.s5-a0-controller-checkpoint.v1"
_PROJECT = Path(__file__).resolve().parents[2]
_ROOT = _PROJECT.parent
_LEGACY = _ROOT / "membind-validation"
_LEGACY_SRC = _LEGACY / "src"
_DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/"
    "longmemeval_s_cleaned.json"
)
_RESULT_VERIFIER_SOURCE = Path(__file__).with_name("s5_a0_result_finalizer.py")
_RUN_ID = re.compile(r"^s5-a0-[0-9]{8}-[0-9]{3}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
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


class S5A0ControllerError(ValueError):
    """A0 chain validation or single-use controller lifecycle failed closed."""


def _fail(code: str) -> S5A0ControllerError:
    return S5A0ControllerError(code)


@dataclass(frozen=True)
class S5A0ControllerPaths:
    production_identity: Path
    production_identity_qualification: Path
    current_stage_pointer: Path
    preflight: Path
    authority: Path
    consumption: Path
    controller_root: Path
    attempt_root: Path


@dataclass(frozen=True)
class S5A0ProductionPaths:
    """Local production inputs plus the one run-scoped controller path set."""

    controller: S5A0ControllerPaths
    runtime_config: Path
    identity_materialization: Path
    env_file: Path
    materialization_inputs: S5A0MaterializationPaths | None


@dataclass(frozen=True)
class S5A0ProductionDependencies:
    """Injectable production seams; defaults stay lazy until consumption."""

    workload_loader: Callable[[S5A0ProductionPaths], Sequence[S5EpisodeRef]] | None = None
    env_file_loader: Callable[[Path, Path], Mapping[str, str]] | None = None
    runtime_builder: Callable[..., object] | None = None
    binding_loader: Callable[[], object] | None = None
    runner_factory: Callable[..., object] = S5ProductionRunner


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_controller_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _qualified_error_class(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__qualname__}"


def _source_manifest(episodes: Sequence[S5EpisodeRef]) -> str:
    selected = tuple(episodes)
    if (
        len(selected) != 49
        or any(not isinstance(item, S5EpisodeRef) for item in selected)
        or [item.source_sequence for item in selected] != list(range(49))
    ):
        raise _fail("episode_manifest_invalid")
    return payload_sha256(
        [
            {
                "source_sequence": item.source_sequence,
                "source_sha256": item.source_sha256,
            }
            for item in selected
        ]
    )


def _sealed_checkpoint(payload: Mapping[str, object]) -> dict[str, object]:
    selected = deepcopy(dict(payload))
    _assert_public(selected)
    selected["checkpoint_sha256"] = payload_sha256(selected)
    return selected


class _ControllerEvidence:
    def __init__(self, root: Path, *, run_id: str) -> None:
        self.root = Path(root)
        self.events_path = self.root / "events.jsonl"
        self.checkpoint_path = self.root / "checkpoint.json"
        self.run_id = run_id
        if self.root.exists():
            raise _fail("controller_attempt_exists")
        self.root.mkdir(parents=True)
        self._events: list[dict[str, object]] = []

    def append(self, event_type: str, **fields: object) -> None:
        event = {
            "schema_version": EVENT_SCHEMA,
            "event_sequence": len(self._events),
            "event_type": event_type,
            "run_id": self.run_id,
            **fields,
        }
        _assert_public(event)
        append_jsonl_durable(
            self.events_path,
            {"event": event, "event_sha256": payload_sha256(event)},
        )
        self._events.append(event)

    def checkpoint(self, **fields: object) -> dict[str, object]:
        checkpoint = _sealed_checkpoint(
            {
                "schema_version": CHECKPOINT_SCHEMA,
                "run_id": self.run_id,
                "event_count": len(self._events),
                **fields,
            }
        )
        atomic_write_json(self.checkpoint_path, checkpoint)
        return checkpoint


def _preconsume_chain(
    *, paths: S5A0ControllerPaths, episodes: Sequence[S5EpisodeRef]
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, Any],
    dict[str, Any],
    str,
]:
    if not isinstance(paths, S5A0ControllerPaths):
        raise _fail("controller_paths_invalid")
    if Path(paths.consumption).exists():
        raise _fail("authority_consumption_exists")
    if Path(paths.controller_root).exists() or Path(paths.attempt_root).exists():
        raise _fail("controller_or_native_attempt_exists")
    try:
        identity = verify_s5_production_identity(
            _read_json(paths.production_identity, "production_identity_invalid")
        )
    except (S5ProductionIdentityError, ValueError):
        raise _fail("production_identity_invalid") from None
    if identity.get("method") != A0:
        raise _fail("production_identity_method_invalid")
    identity_file_sha = sha256_file(paths.production_identity)
    if identity_file_sha == "missing":
        raise _fail("production_identity_missing")

    try:
        qualification = verify_s5_production_identity_qualification(
            _read_json(
                paths.production_identity_qualification,
                "production_identity_qualification_invalid",
            )
        )
        qualification_file_sha = sha256_file(
            paths.production_identity_qualification
        )
        qualification_binding = bind_s5_production_identity_qualification(
            qualification, file_sha256=qualification_file_sha
        )
    except (S5ProductionIdentityQualificationError, ValueError):
        raise _fail("production_identity_qualification_invalid") from None
    if (
        qualification_binding.get("method") != A0
        or qualification_binding.get("production_identity_sha256")
        != identity["identity_sha256"]
        or qualification_binding.get("production_identity_file_sha256")
        != identity_file_sha
    ):
        raise _fail("production_identity_qualification_binding_invalid")

    pointer = _read_json(paths.current_stage_pointer, "current_pointer_invalid")
    pointer_file_sha = sha256_file(paths.current_stage_pointer)
    pointer_payload = pointer.get("payload")
    if (
        pointer_file_sha == "missing"
        or not isinstance(pointer_payload, Mapping)
        or pointer.get("payload_sha256") != payload_sha256(pointer_payload)
        or pointer_payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or qualification_binding["current_stage_pointer"]["file_sha256"]
        != pointer_file_sha
        or qualification_binding["current_stage_pointer"]["payload_sha256"]
        != pointer.get("payload_sha256")
    ):
        raise _fail("current_pointer_binding_invalid")

    try:
        preflight = verify_s5_live_preflight(
            _read_json(paths.preflight, "preflight_invalid")
        )
        authority = verify_s5_live_authority(
            _read_json(paths.authority, "authority_invalid")
        )
    except (S5LivePreflightError, S5LiveAuthorityError, ValueError):
        raise _fail("preflight_or_authority_invalid") from None
    authority_file_sha = sha256_file(paths.authority)
    preflight_file_sha = sha256_file(paths.preflight)
    run = authority["payload"]["run"]
    source_manifest = _source_manifest(episodes)
    if (
        not isinstance(run, Mapping)
        or run.get("method") != A0
        or authority["payload"].get("preflight_file_sha256")
        != preflight_file_sha
        or authority["payload"].get("preflight_payload_sha256")
        != preflight.get("payload_sha256")
        or authority["payload"].get("current_stage_pointer_sha256")
        != pointer_file_sha
        or authority["payload"].get("production_identity_qualification")
        != qualification_binding
        or run.get("source_manifest_sha256") != source_manifest
        or preflight["payload"].get("workload", {}).get(
            "source_manifest_sha256"
        )
        != source_manifest
        or authority_file_sha == "missing"
    ):
        raise _fail("authority_chain_binding_invalid")
    run_id = str(run.get("run_id", ""))
    namespace = str(run.get("namespace", ""))
    if _RUN_ID.fullmatch(run_id) is None or namespace != f"pev3-{run_id}":
        raise _fail("run_identity_invalid")
    for item in episodes:
        if getattr(item.native_episode, "group_id", None) != namespace:
            raise _fail("episode_namespace_binding_invalid")

    authority_sources = authority["payload"].get("source_sha256", {})
    if (
        not isinstance(authority_sources, Mapping)
        or authority_sources.get("controller") != sha256_file(Path(__file__))
    ):
        raise _fail("authority_controller_source_drift")
    if authority_sources.get("result_verifier") != sha256_file(
        _RESULT_VERIFIER_SOURCE
    ):
        raise _fail("authority_result_verifier_source_drift")
    return identity, qualification, preflight, authority, authority_file_sha


async def _await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _import_exact(module_name: str, source_path: Path) -> object:
    """Import one legacy module only from its frozen source directory."""

    expected = Path(source_path).resolve()
    source_root = str(expected.parent)
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    try:
        module = importlib.import_module(module_name)
    except Exception:
        raise _fail(f"{module_name}_import_failed") from None
    observed_file = getattr(module, "__file__", None)
    try:
        observed = Path(str(observed_file)).resolve(strict=True)
    except (OSError, RuntimeError):
        raise _fail(f"{module_name}_source_invalid") from None
    if observed != expected:
        raise _fail(f"{module_name}_source_drift")
    return module


def _default_workload_loader(
    paths: S5A0ProductionPaths,
) -> tuple[S5EpisodeRef, ...]:
    inputs = paths.materialization_inputs
    if not isinstance(inputs, S5A0MaterializationPaths):
        raise _fail("materialization_inputs_missing")
    materialization = _read_json(
        paths.identity_materialization, "identity_materialization_invalid"
    )
    runtime_config = _read_json(paths.runtime_config, "runtime_config_invalid")
    production_identity = _read_json(
        paths.controller.production_identity, "production_identity_invalid"
    )
    try:
        verify_s5_a0_production_identity_materialization(
            materialization=materialization,
            runtime_config=runtime_config,
            production_identity=production_identity,
            paths=inputs,
        )
        bundle = materialize_s5_a0_production_identity(
            paths=inputs,
            git_commit=str(materialization["git_commit"]),
            run_id=str(materialization["run_id"]),
        )
    except Exception:
        raise _fail("frozen_workload_materialization_invalid") from None
    refs: list[S5EpisodeRef] = []
    for index, episode in enumerate(bundle.native_episodes):
        digest = getattr(episode, "source_hash", None)
        try:
            refs.append(
                S5EpisodeRef(
                    source_sequence=index,
                    source_sha256=str(digest),
                    native_episode=episode,
                )
            )
        except Exception:
            raise _fail("frozen_workload_source_invalid") from None
    return tuple(refs)


def _default_env_file_loader(env_file: Path, legacy_src: Path) -> Mapping[str, str]:
    module = _import_exact("graphiti_native", legacy_src / "graphiti_native.py")
    loader = getattr(module, "load_env_file", None)
    if not callable(loader):
        raise _fail("graphiti_env_loader_missing")
    try:
        loaded = loader(Path(env_file))
    except Exception:
        raise _fail("environment_load_failed") from None
    if not isinstance(loaded, Mapping):
        raise _fail("environment_load_invalid")
    return dict(loaded)


def _default_runtime_builder(legacy_src: Path, **kwargs: object) -> object:
    module = _import_exact(
        "native_characterization_runtime",
        legacy_src / "native_characterization_runtime.py",
    )
    builder = getattr(module, "build_u0_graphiti_from_env", None)
    if not callable(builder):
        raise _fail("runtime_builder_missing")
    return builder(**kwargs)


async def ensure_s5_a0_runtime_ready(runtime: object) -> None:
    """Await the pinned Graphiti driver's initialization before construction."""

    graphiti = getattr(runtime, "graphiti", None)
    if graphiti is None:
        raise _fail("runtime_graphiti_missing")
    driver = getattr(graphiti, "driver", None)
    if driver is None:
        raise _fail("runtime_graphiti_driver_missing")
    init_task = getattr(driver, "_init_task", None)
    if init_task is not None:
        if not inspect.isawaitable(init_task):
            raise _fail("runtime_readiness_invalid")
        await init_task
        return
    readiness = getattr(driver, "build_indices_and_constraints", None)
    if not callable(readiness):
        raise _fail("runtime_readiness_missing")
    result = readiness()
    if not inspect.isawaitable(result):
        raise _fail("runtime_readiness_invalid")
    await result


async def close_s5_a0_runtime(runtime: object) -> None:
    """Close the Graphiti lifecycle exactly once, with driver fallback."""

    graphiti = getattr(runtime, "graphiti", None)
    if graphiti is None:
        raise _fail("runtime_graphiti_missing")
    close = getattr(graphiti, "close", None)
    if not callable(close):
        close = getattr(getattr(graphiti, "driver", None), "close", None)
    if not callable(close):
        raise _fail("runtime_close_missing")
    try:
        await _await(close())
    except S5A0ControllerError:
        raise
    except Exception:
        raise _fail("runtime_close_failed") from None


def _rebind_workload(
    refs: Sequence[S5EpisodeRef], namespace: str
) -> tuple[S5EpisodeRef, ...]:
    selected = tuple(refs)
    if (
        len(selected) != 49
        or any(not isinstance(item, S5EpisodeRef) for item in selected)
        or [item.source_sequence for item in selected] != list(range(49))
    ):
        raise _fail("frozen_workload_invalid")
    rebound: list[S5EpisodeRef] = []
    for item in selected:
        try:
            episode = replace(item.native_episode, group_id=namespace)
        except (TypeError, ValueError):
            raise _fail("frozen_episode_rebind_failed") from None
        if getattr(episode, "group_id", None) != namespace:
            raise _fail("frozen_episode_rebind_failed")
        rebound.append(
            S5EpisodeRef(
                source_sequence=item.source_sequence,
                source_sha256=item.source_sha256,
                native_episode=episode,
            )
        )
    return tuple(rebound)


def _consumed_s5_checker(
    *, paths: S5A0ControllerPaths, authority: Mapping[str, Any]
) -> Callable[[object], object]:
    authority_file_sha = sha256_file(paths.authority)

    def check(action: object) -> object:
        action_name = getattr(action, "value", action)
        if action_name != "native_characterization_c0":
            raise _fail("runtime_live_action_invalid")
        try:
            consumption = verify_s5_live_authority_consumption(
                _read_json(paths.consumption, "authority_consumption_invalid")
            )
        except Exception:
            raise _fail("authority_consumption_invalid") from None
        payload = consumption["payload"]
        if (
            payload.get("method") != A0
            or payload.get("run") != authority["payload"].get("run")
            or payload.get("authority_file_sha256") != authority_file_sha
            or payload.get("authority_payload_sha256")
            != authority.get("payload_sha256")
        ):
            raise _fail("authority_consumption_binding_invalid")
        return {"status": "S5_AUTHORITY_CONSUMED"}

    return check


def _failure(stage: str, error_class: str) -> dict[str, object]:
    return {
        "status": "incomplete_non_mergeable",
        "failure_stage": stage,
        "error_class": error_class,
        "resume_authorized": False,
        "namespace_cleanup_authorized": False,
        "scientific_pass_authorized": False,
        "next_method_authorized": False,
        "current_stage_pointer_update_authorized": False,
    }


def _native_failure_class(value: Mapping[str, object]) -> str:
    payload = value.get("payload")
    if isinstance(payload, Mapping):
        events = payload.get("events")
        if isinstance(events, Sequence) and not isinstance(events, (str, bytes)):
            for event in reversed(events):
                if isinstance(event, Mapping) and isinstance(
                    event.get("error_class"), str
                ):
                    return str(event["error_class"])
    return "paper_eval.s5_a0_controller.NativeAttemptIncomplete"


async def execute_s5_a0_controller(
    *,
    paths: S5A0ControllerPaths,
    episodes: Sequence[S5EpisodeRef],
    git_commit: str,
    env_loader: Callable[[], Mapping[str, str]],
    runtime_factory: Callable[[Mapping[str, str]], object],
    readiness: Callable[[object], Awaitable[object] | object],
    binding_loader: Callable[[], object],
    runner_factory: Callable[..., object] = S5ProductionRunner,
    close_runtime: Callable[[object], Awaitable[object] | object],
) -> dict[str, object]:
    """Consume one valid authority, then execute exactly one fresh A0 attempt."""

    identity, _qualification, _preflight, authority, authority_file_sha = (
        _preconsume_chain(paths=paths, episodes=episodes)
    )
    run = authority["payload"]["run"]
    run_id = str(run["run_id"])
    try:
        consumption = consume_s5_live_authority(
            authority=authority,
            authority_file_sha256=authority_file_sha,
            output_path=paths.consumption,
            git_commit=git_commit,
            run_id=f"{run_id}-authority-consumption",
        )
    except (S5LiveAuthorityError, OSError, ValueError):
        raise _fail("authority_consumption_failed") from None

    evidence = _ControllerEvidence(paths.controller_root, run_id=run_id)
    evidence.append(
        "authority_consumed",
        method=A0,
        authority_file_sha256=authority_file_sha,
        authority_payload_sha256=authority["payload_sha256"],
        consumption_payload_sha256=consumption["payload_sha256"],
    )
    evidence.checkpoint(
        status="authority_consumed",
        resume_authorized=False,
        namespace_cleanup_authorized=False,
        scientific_pass_authorized=False,
        next_method_authorized=False,
        current_stage_pointer_update_authorized=False,
    )

    runtime: object | None = None
    stage = "runtime_construction"
    try:
        settings = env_loader()
        runtime = runtime_factory(settings)
        evidence.append("runtime_constructed", method=A0)
        stage = "runtime_readiness"
        await _await(readiness(runtime))
        evidence.append("runtime_ready", method=A0)
        stage = "native_execution"
        binding = binding_loader()
        spec = S5MethodSpec(
            run_id=run_id,
            method=A0,
            native_path_identity_sha256=str(
                identity["graphiti_native_source_sha256"]
            ),
        )
        runner = runner_factory(
            attempt_root=paths.attempt_root,
            spec=spec,
            identity=identity,
            graphiti=getattr(runtime, "graphiti"),
            binding=binding,
            episodes=tuple(episodes),
        )
        evidence.append("native_runner_started", method=A0)
        native_result = await _await(runner.run())
        stage = "runtime_close"
        runtime_to_close = runtime
        runtime = None
        await _await(close_runtime(runtime_to_close))
        evidence.append("runtime_closed", method=A0)
        if not isinstance(native_result, Mapping) or (
            native_result.get("status") != "complete"
            or not isinstance(native_result.get("payload"), Mapping)
            or native_result["payload"].get("status") != "PASS"
        ):
            error_class = (
                _native_failure_class(native_result)
                if isinstance(native_result, Mapping)
                else "paper_eval.s5_a0_controller.NativeAttemptInvalid"
            )
            failure = _failure("native_execution", error_class)
            evidence.append(
                "native_attempt_incomplete",
                method=A0,
                error_class=error_class,
            )
            evidence.checkpoint(**failure)
            return failure
        evidence.append(
            "raw_runner_evidence_complete",
            method=A0,
            production_identity_sha256=identity["identity_sha256"],
        )
        result = {
            "status": "controller_complete_evidence_only",
            "native_attempt_status": "complete",
            "resume_authorized": False,
            "namespace_cleanup_authorized": False,
            "scientific_pass_authorized": False,
            "next_method_authorized": False,
            "current_stage_pointer_update_authorized": False,
        }
        evidence.checkpoint(**result)
        return result
    except Exception as error:
        failure = _failure(stage, _qualified_error_class(error))
        evidence.append(
            "controller_failure",
            method=A0,
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
                # The already persisted attempt outcome remains non-resumable.
                pass


async def execute_s5_a0_production(
    *,
    paths: S5A0ProductionPaths,
    git_commit: str,
    dependencies: S5A0ProductionDependencies | None = None,
) -> dict[str, object]:
    """Compose the frozen workload and lazy production dependencies for A0."""

    if not isinstance(paths, S5A0ProductionPaths):
        raise _fail("production_paths_invalid")
    deps = dependencies or S5A0ProductionDependencies()
    if not isinstance(deps, S5A0ProductionDependencies):
        raise _fail("production_dependencies_invalid")
    try:
        authority = verify_s5_live_authority(
            _read_json(paths.controller.authority, "authority_invalid")
        )
    except Exception:
        raise _fail("authority_invalid") from None
    run = authority["payload"].get("run")
    if not isinstance(run, Mapping):
        raise _fail("authority_run_invalid")
    namespace = str(run.get("namespace", ""))
    if namespace != f"pev3-{run.get('run_id', '')}":
        raise _fail("authority_namespace_invalid")
    workload_loader = deps.workload_loader or _default_workload_loader
    try:
        refs = workload_loader(paths)
    except S5A0ControllerError:
        raise
    except Exception:
        raise _fail("frozen_workload_load_failed") from None
    episodes = _rebind_workload(refs, namespace)

    legacy_src = (
        Path(paths.materialization_inputs.graphiti_native).parent
        if isinstance(paths.materialization_inputs, S5A0MaterializationPaths)
        else _LEGACY_SRC
    )
    env_file_loader = deps.env_file_loader or _default_env_file_loader

    def env_loader() -> Mapping[str, str]:
        # execute_s5_a0_controller invokes this only after durable consumption.
        selected = env_file_loader(Path(paths.env_file), legacy_src)
        if not isinstance(selected, Mapping):
            raise _fail("environment_load_invalid")
        return dict(selected)

    consumed_checker = _consumed_s5_checker(
        paths=paths.controller,
        authority=authority,
    )

    def runtime_factory(_settings: Mapping[str, str]) -> object:
        builder = deps.runtime_builder
        kwargs = {
            "authorization_checker": consumed_checker,
            "live_action": "native_characterization_c0",
            "env_loader": lambda: None,
            "structured_output_mode": "json_schema",
        }
        if builder is not None:
            return builder(**kwargs)
        return _default_runtime_builder(legacy_src, **kwargs)

    binding_loader = deps.binding_loader or load_graphiti_native_binding
    return await execute_s5_a0_controller(
        paths=paths.controller,
        episodes=episodes,
        git_commit=git_commit,
        env_loader=env_loader,
        runtime_factory=runtime_factory,
        readiness=ensure_s5_a0_runtime_ready,
        binding_loader=binding_loader,
        runner_factory=deps.runner_factory,
        close_runtime=close_s5_a0_runtime,
    )


def inspect_s5_a0_controller_attempt(root: Path) -> dict[str, object]:
    """Integrity-check controller evidence without granting resume authority."""

    root = Path(root)
    try:
        lines = (root / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise _fail("controller_events_unreadable") from None
    events: list[dict[str, object]] = []
    for line in lines:
        try:
            record = json.loads(line)
        except (UnicodeError, json.JSONDecodeError):
            raise _fail("controller_event_invalid") from None
        if (
            not isinstance(record, dict)
            or set(record) != {"event", "event_sha256"}
            or not isinstance(record.get("event"), dict)
            or record.get("event_sha256") != payload_sha256(record["event"])
        ):
            raise _fail("controller_event_invalid")
        event = dict(record["event"])
        _assert_public(event)
        events.append(event)
    if (
        not events
        or [event.get("event_sequence") for event in events]
        != list(range(len(events)))
        or events[0].get("event_type") != "authority_consumed"
    ):
        raise _fail("controller_event_sequence_invalid")
    checkpoint = _read_json(root / "checkpoint.json", "controller_checkpoint_invalid")
    stored = checkpoint.pop("checkpoint_sha256", None)
    if (
        stored != payload_sha256(checkpoint)
        or checkpoint.get("schema_version") != CHECKPOINT_SCHEMA
        or checkpoint.get("event_count") != len(events)
        or checkpoint.get("resume_authorized") is not False
        or checkpoint.get("current_stage_pointer_update_authorized") is not False
    ):
        raise _fail("controller_checkpoint_invalid")
    checkpoint["checkpoint_sha256"] = stored
    _assert_public(checkpoint)
    return {"events": events, "checkpoint": checkpoint}


def _default_materialization_paths() -> S5A0MaterializationPaths:
    return S5A0MaterializationPaths(
        native_baseline_freeze=(
            _PROJECT / "artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json"
        ),
        current_stage_pointer=_PROJECT / "runtime/CURRENT_STAGE_STATUS.json",
        graphiti_semantic_identity=(
            _PROJECT
            / "artifacts/paper_eval/native/S5_GRAPHITI_SEMANTIC_API_IDENTITY.json"
        ),
        dataset=_DATASET,
        frozen_split=_LEGACY / "artifacts/dataset/frozen_split_v1_3.json",
        dataset_builder=_LEGACY_SRC / "dataset.py",
        graphiti_native=_LEGACY_SRC / "graphiti_native.py",
        runtime_factory=_LEGACY_SRC / "native_characterization_runtime.py",
        scheduler=_PROJECT / "src/paper_eval/s5_native_method_adapters.py",
        scheduler_test=_PROJECT / "tests/test_s5_native_method_adapters.py",
        durable_store=_PROJECT / "src/paper_eval/s5_durable_attempt_store.py",
        durable_store_test=_PROJECT / "tests/test_s5_durable_attempt_store.py",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute one qualified, single-use S5 Native A0 smoke"
    )
    parser.add_argument("--production-identity", type=Path, required=True)
    parser.add_argument(
        "--production-identity-qualification", type=Path, required=True
    )
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--identity-materialization", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
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
    run_root = Path(args.run_root)
    controller_paths = S5A0ControllerPaths(
        production_identity=args.production_identity,
        production_identity_qualification=args.production_identity_qualification,
        current_stage_pointer=args.current_stage_pointer,
        preflight=args.preflight,
        authority=args.authority,
        consumption=run_root / "authority_consumption.json",
        controller_root=run_root / "controller",
        attempt_root=run_root / "attempt",
    )
    production_paths = S5A0ProductionPaths(
        controller=controller_paths,
        runtime_config=args.runtime_config,
        identity_materialization=args.identity_materialization,
        env_file=args.env_file,
        materialization_inputs=_default_materialization_paths(),
    )
    try:
        result = asyncio.run(
            execute_s5_a0_production(
                paths=production_paths,
                git_commit=str(args.git_commit),
            )
        )
    except Exception as error:
        failure = {
            "status": "error",
            "error_class": type(error).__name__,
        }
        if isinstance(error, S5A0ControllerError) and error.args:
            failure["code"] = str(error.args[0])
        print(
            json.dumps(failure, ensure_ascii=True, sort_keys=True),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result.get("status") == "controller_complete_evidence_only" else 2


__all__ = [
    "S5A0ControllerError",
    "S5A0ControllerPaths",
    "S5A0ProductionDependencies",
    "S5A0ProductionPaths",
    "build_parser",
    "close_s5_a0_runtime",
    "ensure_s5_a0_runtime_ready",
    "execute_s5_a0_controller",
    "execute_s5_a0_production",
    "inspect_s5_a0_controller_attempt",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
