"""Minimal, durable S1 U0 serial smoke runner."""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .artifacts import (
    append_jsonl_durable,
    atomic_write_json,
    payload_sha256,
)


class NamespaceMismatch(RuntimeError):
    """The durable prefix cannot be reconciled with the live namespace."""


@dataclass(frozen=True)
class RunResult:
    status: str
    completed_source_sequences: list[int]
    error_class: str | None = None
    retrieval_result_ids: tuple[str, ...] = ()


def _sequence(item: Any) -> int:
    if isinstance(item, int):
        return item
    if isinstance(item, Mapping):
        return int(item["source_sequence"])
    return int(getattr(item, "source_sequence"))


def _default_episode_kwargs(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return dict(item)
    return {"source_sequence": _sequence(item)}


def _result_id(item: Any) -> str | None:
    if isinstance(item, Mapping):
        value = item.get("uuid") or item.get("id")
    else:
        value = getattr(item, "uuid", None) or getattr(item, "id", None)
    return str(value) if value is not None else None


def _error_class(error: BaseException) -> str:
    return type(error).__name__


class DurableRun:
    """Exactly one serial U0 attempt with append-only events and atomic state."""

    def __init__(
        self,
        artifact_root: Path,
        run_id: str,
        history_id: str,
        namespace: str,
        *,
        episode_kwargs: Callable[[Any], Mapping[str, Any]] = _default_episode_kwargs,
        namespace_probe: Callable[[], Awaitable[Mapping[str, Any]]] | None = None,
        event_sink: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> None:
        for label, value in {
            "run_id": run_id,
            "history_id": history_id,
            "namespace": namespace,
        }.items():
            if not value or value in {".", ".."} or "/" in value or "\\" in value:
                raise ValueError(f"invalid {label}")
        self.run_id = run_id
        self.history_id = history_id
        self.namespace = namespace
        self.run_dir = Path(artifact_root) / run_id
        self.checkpoint_path = self.run_dir / "checkpoint.json"
        self.events_path = self.run_dir / "events.jsonl"
        self.episode_kwargs = episode_kwargs
        self.namespace_probe = namespace_probe
        self.event_sink = event_sink

    def _base_checkpoint(self) -> dict[str, Any]:
        return {
            "schema_version": "membind.paper-eval-v3.s1-checkpoint.v1",
            "run_id": self.run_id,
            "history_id": self.history_id,
            "namespace": self.namespace,
            "status": "running",
            "completed_source_sequences": [],
            "namespace_state": None,
            "retrieval_result_ids": [],
            "error_class": None,
        }

    def _write_checkpoint(self, checkpoint: Mapping[str, Any]) -> None:
        body = dict(checkpoint)
        body.pop("payload_sha256", None)
        body["payload_sha256"] = payload_sha256(body)
        atomic_write_json(self.checkpoint_path, body)

    def load_checkpoint(
        self,
        *,
        namespace_nonempty: bool = False,
        namespace_state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.checkpoint_path.exists():
            if namespace_nonempty:
                raise NamespaceMismatch("namespace is nonempty without matching checkpoint")
            return self._base_checkpoint()
        import json

        checkpoint = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        stored_hash = checkpoint.pop("payload_sha256", None)
        if stored_hash != payload_sha256(checkpoint):
            raise NamespaceMismatch("checkpoint payload hash mismatch")
        for key, expected in {
            "run_id": self.run_id,
            "history_id": self.history_id,
            "namespace": self.namespace,
        }.items():
            if checkpoint.get(key) != expected:
                raise NamespaceMismatch(f"checkpoint {key} mismatch")
        stored_state = checkpoint.get("namespace_state")
        if namespace_state is not None and stored_state != dict(namespace_state):
            raise NamespaceMismatch("namespace state differs from durable checkpoint")
        checkpoint["payload_sha256"] = stored_hash
        return checkpoint

    def _event(self, event_type: str, source_sequence: int | None, **extra: Any) -> None:
        event = {
            "schema_version": "membind.paper-eval-v3.s1-event.v1",
            "run_id": self.run_id,
            "history_id": self.history_id,
            "namespace": self.namespace,
            "event_type": event_type,
            "source_sequence": source_sequence,
            "timestamp_ns": time.time_ns(),
            **extra,
        }
        event["payload_sha256"] = payload_sha256(event)
        append_jsonl_durable(self.events_path, event)
        if self.event_sink is not None:
            self.event_sink(dict(event))

    async def _probe_namespace(self) -> dict[str, Any] | None:
        if self.namespace_probe is None:
            return None
        value = self.namespace_probe()
        if not inspect.isawaitable(value):
            raise TypeError("namespace_probe must return an awaitable")
        return dict(await value)

    async def execute(
        self,
        graph: Any,
        episodes: Sequence[Any],
        *,
        query: str,
    ) -> RunResult:
        sequences = [_sequence(item) for item in episodes]
        if sequences != list(range(len(sequences))):
            raise ValueError("S1 episodes must be the contiguous source-order sequence")

        checkpoint: dict[str, Any] | None = None
        result: RunResult | None = None
        try:
            current_state = await self._probe_namespace()
            namespace_nonempty = bool(
                current_state
                and any(int(value) for value in current_state.values() if isinstance(value, int))
            )
            checkpoint = self.load_checkpoint(
                namespace_nonempty=namespace_nonempty,
                namespace_state=current_state if self.checkpoint_path.exists() else None,
            )
            completed = [int(value) for value in checkpoint["completed_source_sequences"]]
            if completed != sequences[: len(completed)]:
                raise NamespaceMismatch("checkpoint is not a contiguous source prefix")
            if checkpoint.get("status") == "completed":
                return RunResult(
                    "completed",
                    completed,
                    None,
                    tuple(str(value) for value in checkpoint.get("retrieval_result_ids", [])),
                )
            if not self.checkpoint_path.exists():
                checkpoint["namespace_state"] = current_state
                self._write_checkpoint(checkpoint)

            for item in episodes[len(completed) :]:
                source_sequence = _sequence(item)
                self._event("intent", source_sequence)
                try:
                    await graph.add_episode(**dict(self.episode_kwargs(item)))
                except Exception as error:
                    self._event(
                        "failure",
                        source_sequence,
                        error_class=_error_class(error),
                        failure_stage="add_episode",
                    )
                    checkpoint.update(
                        status="incomplete",
                        error_class=_error_class(error),
                    )
                    self._write_checkpoint(checkpoint)
                    result = RunResult("incomplete", completed, _error_class(error))
                    return result

                self._event("publication", source_sequence)
                completed.append(source_sequence)
                checkpoint.update(
                    status="running",
                    completed_source_sequences=list(completed),
                    namespace_state=await self._probe_namespace(),
                    error_class=None,
                )
                self._write_checkpoint(checkpoint)

            try:
                retrieval = await graph.search(
                    query=query,
                    group_ids=[self.namespace],
                    num_results=10,
                )
                result_ids = tuple(
                    value
                    for value in (_result_id(item) for item in retrieval)
                    if value is not None
                )
            except Exception as error:
                self._event(
                    "failure",
                    None,
                    error_class=_error_class(error),
                    failure_stage="retrieval",
                )
                checkpoint.update(status="incomplete", error_class=_error_class(error))
                self._write_checkpoint(checkpoint)
                result = RunResult("incomplete", completed, _error_class(error))
                return result

            self._event("retrieval", None, result_ids=list(result_ids), top_k=10)
            checkpoint.update(
                status="completed",
                completed_source_sequences=list(completed),
                namespace_state=await self._probe_namespace(),
                retrieval_result_ids=list(result_ids),
                error_class=None,
            )
            self._write_checkpoint(checkpoint)
            result = RunResult("completed", completed, None, result_ids)
            return result
        finally:
            close = getattr(graph, "close", None)
            if callable(close):
                closed = close()
                if inspect.isawaitable(closed):
                    await closed
