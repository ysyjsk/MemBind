"""Authority-first durable controller for exactly one S6 calibration cell."""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import (
    append_jsonl_durable,
    atomic_write_json,
    finalize_envelope,
    payload_sha256,
    sha256_file,
)
from .s5_mstar_pipeline import MStarSource
from .s5_native_method_adapters import S5EpisodeRef
from .s6_block_result import verify_s6_work_volume
from .s6_live_authority import (
    consume_s6_live_authority,
    verify_s6_live_authority,
    verify_s6_live_authority_binding,
    verify_s6_live_authority_consumption,
    verify_s6_live_preflight,
)
from .s6_calibration_contract import verify_s6_matrix_freeze
from .s6_mstar_grid import S6MStarSpec, run_s6_mstar
from .s6_pstar_grid import S6PStarSpec, run_s6_pstar


EVENT_SCHEMA = "membind.paper-eval-v3.s6-block-controller-event.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.s6-block-controller-checkpoint.v1"
ATTEMPT_MANIFEST_SCHEMA = "membind.paper-eval-v3.s6-block-attempt-manifest.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROJECT = Path(__file__).resolve().parents[2]
_SOURCE_PATHS = {
    "authority": Path(__file__).with_name("s6_live_authority.py"),
    "calibration_contract": Path(__file__).with_name("s6_calibration_contract.py"),
    "block_controller": Path(__file__).resolve(),
    "block_postprocess": Path(__file__).with_name("s6_block_postprocess.py"),
    "production_runtime": Path(__file__).with_name("s6_production.py"),
    "authority_test": _PROJECT / "tests/test_s6_live_authority.py",
}
_PRIVATE_FIELDS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "credential",
    "episode",
    "messages",
    "password",
    "prompt",
    "raw_output",
    "raw_response",
    "request",
    "response",
    "secret",
    "token",
}


class S6BlockControllerError(ValueError):
    """The one-cell authority chain or controller lifecycle failed closed."""


def _fail(code: str) -> S6BlockControllerError:
    return S6BlockControllerError(code)


@dataclass(frozen=True)
class S6BlockControllerPaths:
    matrix_freeze: Path
    preflight: Path
    authority: Path
    consumption: Path
    controller_root: Path
    attempt_root: Path


@dataclass(frozen=True)
class S6BlockRuntime:
    native_add_episode: Callable[[object], Awaitable[object]] | None = None
    semantic_prepare: Callable[[object, int], Awaitable[object]] | None = None
    latest_state_bind: (
        Callable[[object, int, int, tuple[int, ...]], Awaitable[object]] | None
    ) = None
    production_core_identity_sha256: str | None = None
    recover_publication: Callable[[MStarSource, int], Awaitable[object]] | None = None
    work_volume_snapshot: Callable[[], Mapping[str, object]] | None = None
    close: Callable[[], Awaitable[object] | object] | None = None


RuntimeFactory = Callable[[dict[str, object]], S6BlockRuntime | Awaitable[S6BlockRuntime]]


def _public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_controller_field")
            _public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _public(child)


def _load(path: Path, code: str) -> dict[str, object]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _qualified_error_class(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__qualname__}"


async def _await(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


def _source_inventory(
    sources: Sequence[S5EpisodeRef] | Sequence[MStarSource], method: str
) -> tuple[tuple[object, ...], list[dict[str, object]], str]:
    if isinstance(sources, (str, bytes)) or not isinstance(sources, Sequence):
        raise _fail("sources_invalid")
    selected = tuple(sources)
    expected_type = S5EpisodeRef if method == "P*" else MStarSource
    if (
        not selected
        or any(not isinstance(item, expected_type) for item in selected)
        or [item.source_sequence for item in selected] != list(range(len(selected)))
    ):
        raise _fail("sources_invalid")
    manifest = [
        {
            "source_sequence": item.source_sequence,
            "source_sha256": item.source_sha256,
        }
        for item in selected
    ]
    return selected, manifest, payload_sha256(manifest)


def _current_source_closure(method: str) -> dict[str, str]:
    paths = dict(_SOURCE_PATHS)
    paths["method_runner"] = Path(__file__).with_name(
        "s6_pstar_grid.py" if method == "P*" else "s6_mstar_grid.py"
    )
    result = {key: sha256_file(path) for key, path in sorted(paths.items())}
    if any(value == "missing" or _SHA256.fullmatch(value) is None for value in result.values()):
        raise _fail("current_source_closure_unavailable")
    return result


class _ControllerEvidence:
    def __init__(self, root: Path, run_id: str) -> None:
        self.root = Path(root)
        if self.root.exists():
            raise _fail("controller_attempt_exists")
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
        _public(event)
        append_jsonl_durable(
            self.events_path,
            {"event": event, "event_sha256": payload_sha256(event)},
        )
        self.events.append(event)

    def checkpoint(self, **fields: object) -> dict[str, object]:
        checkpoint = {
            "schema_version": CHECKPOINT_SCHEMA,
            "run_id": self.run_id,
            "event_count": len(self.events),
            **fields,
        }
        _public(checkpoint)
        checkpoint["checkpoint_sha256"] = payload_sha256(checkpoint)
        atomic_write_json(self.checkpoint_path, checkpoint)
        return checkpoint


class _AttemptEvidence:
    def __init__(
        self,
        root: Path,
        *,
        cell: Mapping[str, object],
        manifest: Sequence[Mapping[str, object]],
        execution_identity_sha256: str,
        consumption: Mapping[str, object],
        git_commit: str,
    ) -> None:
        self.root = Path(root)
        if self.root.exists():
            raise _fail("native_attempt_exists")
        self.root.mkdir(parents=True)
        self.events_path = self.root / "events.jsonl"
        self.result_path = self.root / "result.json"
        self.events: list[dict[str, object]] = []
        manifest_payload = {
            "schema_version": ATTEMPT_MANIFEST_SCHEMA,
            "stage": "S6_DEVELOPMENT_ONLY_CONCURRENCY_CALIBRATION",
            "cell": deepcopy(dict(cell)),
            "execution_identity_sha256": execution_identity_sha256,
            "source_count": len(manifest),
            "source_manifest": deepcopy(list(manifest)),
            "source_manifest_sha256": payload_sha256(list(manifest)),
            "authority_consumption_payload_sha256": consumption["payload_sha256"],
        }
        atomic_write_json(
            self.root / "manifest.json",
            finalize_envelope(
                payload=manifest_payload,
                protocol_version=PROTOCOL_VERSION,
                git_commit=git_commit,
                run_id=f"{cell['run_id']}-attempt-manifest",
            ),
        )

    async def persist(self, event: Mapping[str, object]) -> None:
        selected = deepcopy(dict(event))
        _public(selected)
        append_jsonl_durable(
            self.events_path,
            {"event": selected, "event_sha256": payload_sha256(selected)},
        )
        self.events.append(selected)

    def finalize(
        self, *, evidence: Mapping[str, object], cell: Mapping[str, object], git_commit: str
    ) -> dict[str, object]:
        artifact = finalize_envelope(
            payload=deepcopy(dict(evidence)),
            protocol_version=PROTOCOL_VERSION,
            git_commit=git_commit,
            run_id=f"{cell['run_id']}-runner-result",
        )
        atomic_write_json(self.result_path, artifact)
        return artifact

    def persist_work_volume(
        self,
        *,
        value: Mapping[str, object],
        cell: Mapping[str, object],
        git_commit: str,
    ) -> dict[str, object]:
        payload = verify_s6_work_volume(value)
        artifact = finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=git_commit,
            run_id=f"{cell['run_id']}-work-volume",
        )
        atomic_write_json(self.root / "work_volume.json", artifact)
        return artifact

    @property
    def completed_source_count(self) -> int:
        terminal_sources = {
            event.get("source_sequence")
            for event in self.events
            if event.get("event_type") == "source_terminal"
        }
        if terminal_sources:
            return len(terminal_sources)
        return sum(event.get("event_type") == "publication" for event in self.events)


def _preconsume(
    *,
    paths: S6BlockControllerPaths,
    sources: Sequence[S5EpisodeRef] | Sequence[MStarSource],
    git_commit: str,
) -> tuple[
    dict[str, object],
    dict[str, object],
    tuple[object, ...],
    list[dict[str, object]],
]:
    if not isinstance(paths, S6BlockControllerPaths):
        raise _fail("controller_paths_invalid")
    if (
        Path(paths.consumption).exists()
        or Path(paths.controller_root).exists()
        or Path(paths.attempt_root).exists()
    ):
        raise _fail("block_attempt_already_started")
    try:
        freeze = verify_s6_matrix_freeze(_load(paths.matrix_freeze, "matrix_freeze_invalid"))
        preflight = verify_s6_live_preflight(_load(paths.preflight, "preflight_invalid"))
        authority = verify_s6_live_authority(_load(paths.authority, "authority_invalid"))
        authority = verify_s6_live_authority_binding(
            authority,
            matrix_freeze=freeze,
            matrix_file_sha256=sha256_file(paths.matrix_freeze),
        )
    except S6BlockControllerError:
        raise
    except Exception:
        raise _fail("authority_chain_invalid") from None
    payload = authority["payload"]
    cell = payload["cell"]
    method = str(cell["method"])
    selected_sources, manifest, manifest_sha256 = _source_inventory(sources, method)
    if payload["workload"] != {
        "source_count": len(manifest),
        "source_manifest_sha256": manifest_sha256,
    }:
        raise _fail("source_manifest_binding_invalid")
    if (
        payload["preflight"]
        != {
            "file_sha256": sha256_file(paths.preflight),
            "payload_sha256": preflight["payload_sha256"],
        }
        or preflight["payload"]["cell"] != cell
        or preflight["payload"]["workload"] != payload["workload"]
        or preflight["payload"]["execution_identity_sha256"]
        != payload["execution_identity_sha256"]
    ):
        raise _fail("preflight_binding_invalid")
    if payload["source_sha256"] != _current_source_closure(method):
        raise _fail("source_closure_drift")
    try:
        consumption = consume_s6_live_authority(
            authority=authority,
            authority_file_sha256=sha256_file(paths.authority),
            output_path=paths.consumption,
            git_commit=git_commit,
        )
        verify_s6_live_authority_consumption(consumption)
    except Exception:
        raise _fail("authority_consumption_failed") from None
    return authority, consumption, selected_sources, manifest


async def execute_s6_block_controller(
    *,
    paths: S6BlockControllerPaths,
    sources: Sequence[S5EpisodeRef] | Sequence[MStarSource],
    runtime_factory: RuntimeFactory,
    git_commit: str,
    clock_ns: Callable[[], int],
) -> dict[str, object]:
    """Consume once, then construct the runtime and execute exactly one cell."""

    if not callable(runtime_factory) or not callable(clock_ns):
        raise _fail("controller_dependency_invalid")
    authority, consumption, selected_sources, manifest = _preconsume(
        paths=paths, sources=sources, git_commit=git_commit
    )
    payload = authority["payload"]
    cell = payload["cell"]
    method = str(cell["method"])
    evidence = _ControllerEvidence(paths.controller_root, str(cell["run_id"]))
    evidence.append(
        "authority_consumed",
        method=method,
        cell_index=cell["cell_index"],
        consumption_payload_sha256=consumption["payload_sha256"],
    )
    attempt = _AttemptEvidence(
        paths.attempt_root,
        cell=cell,
        manifest=manifest,
        execution_identity_sha256=str(payload["execution_identity_sha256"]),
        consumption=consumption,
        git_commit=git_commit,
    )
    stage = "runtime_construction"
    runtime: S6BlockRuntime | None = None
    runner_result: dict[str, object] | None = None
    failure: BaseException | None = None
    try:
        runtime_value = await _await(runtime_factory(deepcopy(dict(cell))))
        if not isinstance(runtime_value, S6BlockRuntime):
            raise _fail("runtime_invalid")
        runtime = runtime_value
        evidence.append("runtime_constructed", method=method)
        stage = "method_execution"
        evidence.append("method_runner_started", method=method)
        if method == "P*":
            if not callable(runtime.native_add_episode):
                raise _fail("pstar_runtime_invalid")
            runner_result = await run_s6_pstar(
                spec=S6PStarSpec(
                    run_id=str(cell["run_id"]),
                    configured_concurrency=int(cell["configured_concurrency"]),
                    execution_identity_sha256=str(
                        payload["execution_identity_sha256"]
                    ),
                ),
                episodes=selected_sources,
                native_add_episode=runtime.native_add_episode,
                persist_event=attempt.persist,
                clock_ns=clock_ns,
            )
        else:
            if (
                not callable(runtime.semantic_prepare)
                or not callable(runtime.latest_state_bind)
                or not isinstance(runtime.production_core_identity_sha256, str)
                or _SHA256.fullmatch(runtime.production_core_identity_sha256) is None
            ):
                raise _fail("mstar_runtime_invalid")
            runner_result = await run_s6_mstar(
                spec=S6MStarSpec(
                    run_id=str(cell["run_id"]),
                    configured_concurrency=int(cell["configured_concurrency"]),
                    production_core_identity_sha256=(
                        runtime.production_core_identity_sha256
                    ),
                    execution_identity_sha256=str(
                        payload["execution_identity_sha256"]
                    ),
                ),
                sources=selected_sources,
                semantic_prepare=runtime.semantic_prepare,
                latest_state_bind=runtime.latest_state_bind,
                persist_event=attempt.persist,
                clock_ns=clock_ns,
                recover_publication=runtime.recover_publication,
            )
        runner_artifact = attempt.finalize(
            evidence=runner_result, cell=cell, git_commit=git_commit
        )
        evidence.append(
            "method_runner_complete",
            method=method,
            runner_status=runner_result["status"],
            runner_payload_sha256=runner_artifact["payload_sha256"],
        )
        stage = "work_volume_snapshot"
        if not callable(runtime.work_volume_snapshot):
            raise _fail("work_volume_snapshot_missing")
        raw_work_volume = await _await(runtime.work_volume_snapshot())
        if not isinstance(raw_work_volume, Mapping):
            raise _fail("work_volume_snapshot_invalid")
        work_volume_artifact = attempt.persist_work_volume(
            value=raw_work_volume,
            cell=cell,
            git_commit=git_commit,
        )
        evidence.append(
            "work_volume_persisted",
            method=method,
            work_volume_payload_sha256=work_volume_artifact["payload_sha256"],
        )
    except Exception as error:
        failure = error
    finally:
        if runtime is not None and callable(runtime.close):
            try:
                await _await(runtime.close())
                evidence.append("runtime_closed", method=method)
            except Exception as error:
                if failure is None:
                    failure = error
                    stage = "runtime_close"

    if failure is not None:
        checkpoint = evidence.checkpoint(
            method=method,
            cell_index=cell["cell_index"],
            status="incomplete_non_mergeable",
            failure_stage=stage,
            error_class=_qualified_error_class(failure),
            runner_status=None,
            runner_result_payload_sha256=None,
            completed_source_count=attempt.completed_source_count,
        )
        raise _fail(
            f"block_incomplete_non_mergeable:{stage}:{checkpoint['error_class']}"
        ) from None
    if runner_result is None:
        raise _fail("runner_result_missing")
    result_artifact = _load(attempt.result_path, "runner_result_invalid")
    checkpoint = evidence.checkpoint(
        method=method,
        cell_index=cell["cell_index"],
        status="controller_complete_evidence_only",
        failure_stage=None,
        error_class=None,
        runner_status=runner_result["status"],
        runner_result_payload_sha256=result_artifact["payload_sha256"],
        completed_source_count=attempt.completed_source_count,
    )
    return {
        "status": checkpoint["status"],
        "method": method,
        "cell_index": cell["cell_index"],
        "runner_status": runner_result["status"],
        "runner_result_payload_sha256": result_artifact["payload_sha256"],
        "completed_source_count": attempt.completed_source_count,
        "postprocess_required": True,
    }


def inspect_s6_block_controller(root: Path) -> dict[str, object]:
    selected = Path(root)
    checkpoint = _load(selected / "checkpoint.json", "controller_checkpoint_invalid")
    seal = checkpoint.pop("checkpoint_sha256", None)
    if (
        checkpoint.get("schema_version") != CHECKPOINT_SCHEMA
        or seal != payload_sha256(checkpoint)
    ):
        raise _fail("controller_checkpoint_invalid")
    events: list[dict[str, object]] = []
    try:
        lines = (selected / "events.jsonl").read_text(encoding="utf-8").splitlines()
        for line in lines:
            row = json.loads(line)
            event = row.get("event")
            if (
                not isinstance(event, Mapping)
                or row.get("event_sha256") != payload_sha256(event)
            ):
                raise _fail("controller_events_invalid")
            events.append(dict(event))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail("controller_events_invalid") from None
    if (
        [event.get("event_sequence") for event in events] != list(range(len(events)))
        or checkpoint.get("event_count") != len(events)
    ):
        raise _fail("controller_event_accounting_invalid")
    checkpoint["checkpoint_sha256"] = seal
    return {"events": events, "checkpoint": checkpoint}


__all__ = [
    "S6BlockControllerError",
    "S6BlockControllerPaths",
    "S6BlockRuntime",
    "execute_s6_block_controller",
    "inspect_s6_block_controller",
]
