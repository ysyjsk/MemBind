"""Bounded one-episode lifecycle and relay contracts for the temporary lane.

The live runtime is injected at the boundary.  This module never selects a
mainline namespace, clears a database, or mutates Native characterization
authority.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping

from gpt55_temporary.simple_judge.config_chat_judge import (
    USER_AGENT,
    _atomic_write_json,
    prepare_attempt_dir,
)


FROZEN_HISTORY_ID = "07741c45"
FROZEN_SOURCE_SEQUENCE = 0
FROZEN_EPISODE_SHA256 = (
    "be983c489b10deea9c4d860f1e3203e4fa5d964154e004b814b2b5fee410156a"
)
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
DEFAULT_EMBEDDING_CACHE = Path("/data/predator/ly/Mem/cache/huggingface/hub")


def _namespace(attempt_id: str, artifact_root: Path) -> str:
    identity = f"{attempt_id}\0{artifact_root.as_posix()}".encode("utf-8")
    suffix = hashlib.sha256(identity).hexdigest()[:16]
    return f"tmp-api-char-{suffix}"


@dataclass(frozen=True)
class BoundedRunConfig:
    attempt_id: str
    artifact_root: Path
    history_id: str = FROZEN_HISTORY_ID
    source_sequence: int = FROZEN_SOURCE_SEQUENCE
    episode_source_sha256: str = FROZEN_EPISODE_SHA256
    model: str = DEFAULT_MODEL
    embedding_provider: str = "local_bge_m3"
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_revision: str = DEFAULT_EMBEDDING_REVISION
    embedding_dimension: int = 1024
    embedding_device: str = "cuda:0"
    embedding_cache_folder: Path = DEFAULT_EMBEDDING_CACHE
    embedding_batch_size: int = 32
    max_tokens: int = 4096
    max_coroutines: int = 4
    timeout_s: float = 180.0
    max_api_attempts: int = 64
    graph_namespace: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.attempt_id or len(self.attempt_id) > 128:
            raise ValueError("attempt_id must be a bounded identifier")
        if self.max_api_attempts <= 0:
            raise ValueError("max_api_attempts must be positive")
        if self.embedding_dimension != 1024:
            raise ValueError("the frozen local embedding dimension is 1024")
        if self.max_tokens <= 0 or self.max_coroutines <= 0 or self.timeout_s <= 0:
            raise ValueError("runtime limits must be positive")
        canonical_artifact_root = Path(self.artifact_root).resolve(strict=False)
        object.__setattr__(self, "artifact_root", canonical_artifact_root)
        object.__setattr__(
            self,
            "embedding_cache_folder",
            Path(self.embedding_cache_folder),
        )
        object.__setattr__(
            self,
            "graph_namespace",
            _namespace(self.attempt_id, canonical_artifact_root),
        )


def interval_union_ns(intervals: Iterable[tuple[int, int]]) -> int:
    """Return wall-clock occupancy without double-counting overlaps."""

    ordered: list[tuple[int, int]] = []
    for start, end in intervals:
        start_ns, end_ns = int(start), int(end)
        if end_ns < start_ns:
            raise ValueError("span interval ends before it starts")
        if end_ns > start_ns:
            ordered.append((start_ns, end_ns))
    ordered.sort()
    merged: list[tuple[int, int]] = []
    for start_ns, end_ns in ordered:
        if not merged or start_ns > merged[-1][1]:
            merged.append((start_ns, end_ns))
        else:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end_ns))
    return sum(end_ns - start_ns for start_ns, end_ns in merged)


def _message_field(message: Any, name: str) -> Any:
    if isinstance(message, Mapping):
        return message.get(name)
    return getattr(message, name, None)


def build_chat_request(
    *,
    model: str,
    messages: Iterable[Any],
    max_tokens: int,
    response_format: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert Graphiti messages without adding roles or provider-only fields."""

    forwarded: list[dict[str, str]] = []
    for message in messages:
        role = _message_field(message, "role")
        content = _message_field(message, "content")
        if not isinstance(role, str) or not isinstance(content, str):
            raise TypeError("Graphiti messages require text role and content")
        forwarded.append({"role": role, "content": content})
    if not forwarded:
        raise ValueError("at least one Graphiti message is required")
    if int(max_tokens) <= 0:
        raise ValueError("max_tokens must be positive")
    request: dict[str, Any] = {
        "model": str(model),
        "messages": forwarded,
        "max_tokens": int(max_tokens),
    }
    if response_format is not None:
        request["response_format"] = dict(response_format)
    return request


class ApiAttemptCapExceeded(RuntimeError):
    """The bounded run exhausted its total remote request allowance."""


class RelayChatGraphitiClient:
    """Small async transport facade shared by the future live Graphiti adapter."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str,
        transport: Any,
        max_api_attempts: int,
    ) -> None:
        if max_api_attempts <= 0:
            raise ValueError("max_api_attempts must be positive")
        self.endpoint = str(endpoint)
        self._api_key = str(api_key)
        self.model = str(model)
        self.transport = transport
        self.max_api_attempts = int(max_api_attempts)
        self.attempt_count = 0

    async def complete(
        self,
        *,
        messages: Iterable[Any],
        max_tokens: int,
        response_format: Mapping[str, Any] | None = None,
    ) -> Any:
        if self.attempt_count >= self.max_api_attempts:
            raise ApiAttemptCapExceeded("remote API attempt cap reached")
        payload = build_chat_request(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        self.attempt_count += 1
        return await self.transport.post_json(
            url=self.endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            payload=payload,
            max_retries=0,
        )


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _result_counts(value: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in ("nodes", "edges", "episodic_edges", "communities", "community_edges"):
        item = getattr(value, name, None)
        counts[name] = len(item) if isinstance(item, (list, tuple)) else 0
    return counts


async def run_bounded(
    *,
    config: BoundedRunConfig,
    episode_loader: Callable[..., Any],
    graphiti_factory: Callable[[BoundedRunConfig], Any],
    instrumentor: Any,
    cleanup_group: Callable[..., Awaitable[None] | None],
    resource_handoff: Any | None = None,
) -> dict[str, Any]:
    """Run exactly one frozen episode and durably finalize every exit path."""
    run_dir: Path | None = None
    graphiti: Any | None = None
    output: Any = None
    failure: BaseException | None = None
    cleanup_error: BaseException | None = None
    close_error: BaseException | None = None
    failed_phase: str | None = None
    add_episode_completed = False
    install_attempted = False
    install_completed = False
    restore_attempted = False
    finalize_attempted = False
    current_phase = "graphiti_factory"

    def record_failure(exc: BaseException, phase: str) -> None:
        nonlocal failure, failed_phase
        if failure is None:
            failure = exc
            failed_phase = phase

    async def restore_instrumentation() -> None:
        nonlocal restore_attempted
        if not install_attempted or restore_attempted:
            return
        restore_attempted = True
        await _maybe_await(instrumentor.restore())

    async def finalize_trace() -> None:
        nonlocal finalize_attempted
        if not install_completed or finalize_attempted:
            return
        finalize_attempted = True
        finalize = getattr(instrumentor, "finalize", None)
        if callable(finalize):
            await _maybe_await(finalize())

    try:
        graphiti = await _maybe_await(graphiti_factory(config))
        if resource_handoff is not None:
            current_phase = "resource_handoff"
            resource_handoff.claim(graphiti)

        current_phase = "artifact_dir_claim"
        run_dir = prepare_attempt_dir(config.artifact_root, config.attempt_id)
        current_phase = "episode_load"
        episode = episode_loader(
            history_id=config.history_id,
            source_sequence=config.source_sequence,
            expected_sha256=config.episode_source_sha256,
        )
        manifest = {
            "schema_version": "membind.temporary-api-characterization.manifest.v1",
            "attempt_id": config.attempt_id,
            "lane": "temporary_api_characterization",
            "diagnostic_only": True,
            "mainline_state_advanced": False,
            "history_id": config.history_id,
            "source_sequence": config.source_sequence,
            "episode_source_sha256": config.episode_source_sha256,
            "graph_namespace": config.graph_namespace,
            "model": config.model,
            "embedding": {
                "provider": config.embedding_provider,
                "model": config.embedding_model,
                "revision": config.embedding_revision,
                "dimension": config.embedding_dimension,
                "device": config.embedding_device,
                "cache_folder": str(config.embedding_cache_folder),
                "batch_size": config.embedding_batch_size,
            },
            "max_api_attempts": config.max_api_attempts,
            "max_tokens": config.max_tokens,
            "max_coroutines": config.max_coroutines,
            "timeout_s": config.timeout_s,
            "planned_add_episode_count": 1,
        }
        current_phase = "manifest_checkpoint"
        _atomic_write_json(run_dir / "00_manifest.json", manifest)
        current_phase = "runtime_ready_checkpoint"
        _atomic_write_json(
            run_dir / "01_runtime_ready.json",
            {
                "schema_version": "membind.temporary-api-characterization.runtime.v1",
                "attempt_id": config.attempt_id,
                "status": "ready",
            },
        )
        current_phase = "instrumentor_install"
        install_attempted = True
        instrumentor.install(graphiti)
        install_completed = True
        try:
            current_phase = "episode_scope"
            episode_scope = getattr(instrumentor, "episode_scope", None)
            scope = (
                episode_scope()
                if callable(episode_scope)
                else contextlib.nullcontext()
            )
            with scope:
                current_phase = "add_episode"
                output = await graphiti.add_episode(
                    name=episode.name,
                    episode_body=episode.episode_body,
                    source_description=episode.source_description,
                    reference_time=episode.reference_time,
                    source=episode.source,
                    group_id=config.graph_namespace,
                )
                # Completion is a property of the Graphiti call, even if the
                # tracing scope later fails while unwinding.
                add_episode_completed = True
        except BaseException as exc:
            record_failure(exc, current_phase)

        try:
            await restore_instrumentation()
        except BaseException as exc:
            record_failure(exc, "instrumentation_restore")
        try:
            await finalize_trace()
        except BaseException as exc:
            record_failure(exc, "trace_finalize")

        if failure is not None:
            raise failure
    except BaseException as exc:
        record_failure(exc, current_phase)
        try:
            await restore_instrumentation()
        except BaseException as restore_exc:
            record_failure(restore_exc, "instrumentation_restore")
        try:
            await finalize_trace()
        except BaseException as finalize_exc:
            record_failure(finalize_exc, "trace_finalize")
    finally:
        if graphiti is not None:
            try:
                await _maybe_await(cleanup_group(group_id=config.graph_namespace))
            except BaseException as exc:
                cleanup_error = exc
                record_failure(exc, "scoped_cleanup")
            try:
                await _maybe_await(graphiti.close())
            except BaseException as exc:
                close_error = exc
                record_failure(exc, "graphiti_close")

    episode_status = (
        "success"
        if add_episode_completed
        else "cancelled"
        if isinstance(failure, asyncio.CancelledError)
        else "failed"
    )
    if run_dir is not None:
        episode_record: dict[str, Any] = {
            "schema_version": "membind.temporary-api-characterization.episode.v1",
            "attempt_id": config.attempt_id,
            "status": episode_status,
            "completed_add_episode_count": int(add_episode_completed),
        }
        if add_episode_completed:
            episode_record["result_counts"] = _result_counts(output)
        elif failure is not None:
            episode_record["error_code"] = (
                f"{type(failure).__module__}.{type(failure).__qualname__}"
            )
        _atomic_write_json(run_dir / "02_episode_result.json", episode_record)
        _atomic_write_json(
            run_dir / "03_cleanup.json",
            {
                "schema_version": "membind.temporary-api-characterization.cleanup.v1",
                "attempt_id": config.attempt_id,
                "graph_namespace": config.graph_namespace,
                "status": (
                    "success"
                    if cleanup_error is None and close_error is None
                    else "failed"
                ),
                "cleanup_status": "success" if cleanup_error is None else "failed",
                "cleanup_error_code": (
                    None
                    if cleanup_error is None
                    else f"{type(cleanup_error).__module__}.{type(cleanup_error).__qualname__}"
                ),
                "close_status": "success" if close_error is None else "failed",
                "close_error_code": (
                    None
                    if close_error is None
                    else f"{type(close_error).__module__}.{type(close_error).__qualname__}"
                ),
            },
        )

    status = (
        "success"
        if failure is None
        else "cancelled"
        if isinstance(failure, asyncio.CancelledError)
        else "failed"
    )
    summary = {
        "schema_version": "membind.temporary-api-characterization.summary.v1",
        "attempt_id": config.attempt_id,
        "status": status,
        "episode_status": episode_status,
        "completed_add_episode_count": int(add_episode_completed),
        "error_code": (
            None
            if failure is None
            else f"{type(failure).__module__}.{type(failure).__qualname__}"
        ),
        "terminal_failure_phase": failed_phase,
        "graph_namespace": config.graph_namespace,
        "diagnostic_only": True,
        "mainline_state_advanced": False,
    }
    if run_dir is not None:
        _atomic_write_json(run_dir / "04_summary.json", summary)
    if failure is not None:
        raise failure
    return summary
