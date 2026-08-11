"""Immutable-preflight orchestration for one temporary live experiment.

This module owns only ordering and durable checkpoints.  Dataset, embedding,
database, Graphiti, and experiment execution are injected so importing or
testing it cannot open an external resource.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from gpt55_temporary.api_characterization.live_runtime import (
    PreflightRejected,
    read_successful_preflight,
)
from gpt55_temporary.simple_judge.config_chat_judge import (
    _atomic_write_json,
    prepare_attempt_dir,
)


DEFAULT_MODEL = "gpt-5.4-mini"
_PREFLIGHT_FILES = (
    "00_manifest.json",
    "02_transport.json",
    "04_summary.json",
)


@dataclass(frozen=True)
class LiveExperimentConfig:
    """Paths and identity needed before any live dependency is constructed."""

    attempt_id: str
    preflight_attempt_dir: Path
    artifact_root: Path
    expected_model: str = DEFAULT_MODEL

    def __post_init__(self) -> None:
        if not str(self.attempt_id):
            raise ValueError("attempt_id is required")
        if not str(self.expected_model):
            raise ValueError("expected_model is required")
        object.__setattr__(self, "preflight_attempt_dir", Path(self.preflight_attempt_dir))
        object.__setattr__(self, "artifact_root", Path(self.artifact_root))


class ResourceHandoff:
    """Transfer one constructed Graphiti from the outer gate exactly once."""

    def __init__(self, resource: Any) -> None:
        self._resource = resource
        self._claimed = False

    @property
    def claimed(self) -> bool:
        return self._claimed

    def claim(self, resource: Any) -> None:
        if self._claimed:
            raise RuntimeError("live resource ownership was already transferred")
        if resource is not self._resource:
            raise RuntimeError("live resource handoff identity mismatch")
        self._claimed = True


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fingerprint_preflight_artifact(attempt_dir: str | Path) -> dict[str, str]:
    """Read the gate files without following mutable indirection."""

    root = Path(attempt_dir)
    if root.is_symlink() or not root.is_dir():
        raise PreflightRejected(None, "preflight_artifact_unreadable")
    fingerprints: dict[str, str] = {}
    try:
        for name in _PREFLIGHT_FILES:
            path = root / name
            if path.is_symlink() or not path.is_file():
                raise PreflightRejected(None, "preflight_artifact_unreadable")
            fingerprints[name] = _sha256(path.read_bytes())
    except OSError:
        raise PreflightRejected(None, "preflight_artifact_unreadable") from None
    return fingerprints


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _close_resource(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        await _maybe_await(close())


def _blocked_checkpoint(
    *,
    config: LiveExperimentConfig,
    run_dir: Path,
    failure: PreflightRejected,
    fingerprints: Mapping[str, str],
) -> dict[str, Any]:
    checkpoint = {
        "schema_version": "membind.temporary-api-live-checkpoint.v1",
        "attempt_id": config.attempt_id,
        "status": "blocked_preflight",
        "http_status": failure.status_code,
        "classification": failure.classification,
        "expected_model": config.expected_model,
        "preflight_artifact_id": config.preflight_attempt_dir.name,
        "preflight_file_sha256": dict(fingerprints),
        "diagnostic_only": True,
        "mainline_state_advanced": False,
        "live_dependency_construction_started": False,
    }
    _atomic_write_json(run_dir / "checkpoint.json", checkpoint)
    return checkpoint


def _compatibility_blocked_checkpoint(
    *,
    config: LiveExperimentConfig,
    run_dir: Path,
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist a sanitized structured-Chat failure before local setup."""

    checkpoint = {
        "schema_version": "membind.temporary-api-live-checkpoint.v1",
        "attempt_id": config.attempt_id,
        "status": "blocked_compatibility_preflight",
        "http_status": report.get("status_code"),
        "classification": str(
            report.get("classification") or "structured_chat_preflight_failed"
        ),
        "attempt_count": int(report.get("attempt_count", 0) or 0),
        "error_code": report.get("error_code"),
        "expected_model": config.expected_model,
        "diagnostic_only": True,
        "mainline_state_advanced": False,
        "remote_compatibility_preflight_started": True,
        "live_dependency_construction_started": False,
    }
    _atomic_write_json(run_dir / "checkpoint.json", checkpoint)
    return checkpoint


async def run_live_experiment(
    *,
    config: LiveExperimentConfig,
    dataset_loader: Callable[[], Any],
    embedding_factory: Callable[[], Any],
    neo4j_factory: Callable[[], Any],
    graphiti_factory: Callable[..., Any],
    experiment_runner: Callable[..., Any],
    preflight_reader: Callable[..., Mapping[str, Any]] = read_successful_preflight,
    compatibility_preflight: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Gate all live construction behind one unchanged successful artifact."""

    fingerprints: dict[str, str] = {}
    failure: PreflightRejected | None = None
    report: Mapping[str, Any] | None = None
    try:
        fingerprints = fingerprint_preflight_artifact(config.preflight_attempt_dir)
        try:
            report = preflight_reader(
                attempt_dir=config.preflight_attempt_dir,
                expected_model=config.expected_model,
            )
            report = await _maybe_await(report)
            if not isinstance(report, Mapping) or not bool(report.get("ok")):
                status_code = report.get("status_code") if isinstance(report, Mapping) else None
                classification = (
                    str(report.get("classification") or "preflight_artifact_not_successful")
                    if isinstance(report, Mapping)
                    else "preflight_artifact_not_successful"
                )
                failure = PreflightRejected(status_code, classification)
        except PreflightRejected as exc:
            failure = exc
        after = fingerprint_preflight_artifact(config.preflight_attempt_dir)
        if after != fingerprints:
            failure = PreflightRejected(None, "preflight_artifact_changed_during_gate")
    except PreflightRejected as exc:
        failure = exc

    if failure is not None:
        run_dir = prepare_attempt_dir(config.artifact_root, config.attempt_id)
        return _blocked_checkpoint(
            config=config,
            run_dir=run_dir,
            failure=failure,
            fingerprints=fingerprints,
        )

    if report is None:
        raise RuntimeError("preflight gate produced no report")
    run_dir = prepare_attempt_dir(config.artifact_root, config.attempt_id)
    _atomic_write_json(
        run_dir / "00_preflight_gate.json",
        {
            "schema_version": "membind.temporary-api-preflight-gate.v1",
            "attempt_id": config.attempt_id,
            "status": "success",
            "expected_model": config.expected_model,
            "preflight_artifact_id": config.preflight_attempt_dir.name,
            "preflight_file_sha256": fingerprints,
            "attempt_count": int(report.get("attempt_count", 0)),
            "http_status": report.get("status_code"),
            "diagnostic_only": True,
            "mainline_state_advanced": False,
        },
    )

    if compatibility_preflight is not None:
        try:
            compatibility = await _maybe_await(
                compatibility_preflight(run_dir=run_dir)
            )
            if not isinstance(compatibility, Mapping):
                raise TypeError("compatibility_preflight must return a mapping")
            compatibility_report = dict(compatibility)
        except BaseException as exc:
            compatibility_report = {
                "ok": False,
                "status_code": getattr(exc, "status_code", None),
                "classification": "structured_chat_preflight_exception",
                "attempt_count": int(getattr(exc, "attempt_count", 0) or 0),
                "error_code": f"{type(exc).__module__}.{type(exc).__qualname__}",
            }
        if not bool(compatibility_report.get("ok")):
            return _compatibility_blocked_checkpoint(
                config=config,
                run_dir=run_dir,
                report=compatibility_report,
            )

    try:
        dataset = await _maybe_await(dataset_loader())
        embedding = await _maybe_await(embedding_factory())
        neo4j = await _maybe_await(neo4j_factory())
        try:
            graphiti = await _maybe_await(
                graphiti_factory(dataset=dataset, embedding=embedding, neo4j=neo4j)
            )
        except BaseException:
            # The outer gate owns Neo4j until Graphiti construction succeeds.
            # Preserve the construction failure if close also fails.
            try:
                await _close_resource(neo4j)
            except BaseException:
                pass
            raise

        handoff = ResourceHandoff(graphiti)
        runner_failure: BaseException | None = None
        result: Any = None
        try:
            result = await _maybe_await(
                experiment_runner(
                    graphiti=graphiti,
                    dataset=dataset,
                    run_dir=run_dir,
                    resource_handoff=handoff,
                )
            )
            if not isinstance(result, Mapping):
                raise TypeError("experiment_runner must return a mapping")
        except BaseException as exc:
            runner_failure = exc
        finally:
            if not handoff.claimed:
                try:
                    await _close_resource(graphiti)
                except BaseException as exc:
                    if runner_failure is None:
                        runner_failure = exc
        if runner_failure is not None:
            raise runner_failure
    except BaseException as exc:
        status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
        _atomic_write_json(
            run_dir / "checkpoint.json",
            {
                "schema_version": "membind.temporary-api-live-checkpoint.v1",
                "attempt_id": config.attempt_id,
                "status": status,
                "error_code": f"{type(exc).__module__}.{type(exc).__qualname__}",
                "diagnostic_only": True,
                "mainline_state_advanced": False,
                "live_dependency_construction_started": True,
            },
        )
        raise

    result_dict = dict(result)
    _atomic_write_json(
        run_dir / "checkpoint.json",
        {
            "schema_version": "membind.temporary-api-live-checkpoint.v1",
            "attempt_id": config.attempt_id,
            "status": str(result_dict.get("status") or "success"),
            "diagnostic_only": True,
            "mainline_state_advanced": False,
            "live_dependency_construction_started": True,
        },
    )
    return result_dict
