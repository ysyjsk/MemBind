"""Observational pre-prompt plumbing for the bilateral S4 candidate sidecar."""

from __future__ import annotations

import contextvars
import copy
import inspect
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from typing import Any

from .artifacts import payload_sha256
from .s4_candidate_sidecar import (
    CaptureSidecarStore,
    ReplaySidecarBinder,
    activate_replay_binding,
    build_candidate_call_record,
    replay_binding_sha256,
)


EDGE_PROMPT = "dedupe_edges.resolve_edge"
_PART_FIELDS = {
    "decoding_config",
    "model_revision",
    "structured_output_schema",
    "system_prompt",
    "user_prompt",
}
_PROJECTION_FIELDS = {
    "invalidation",
    "logical_call_sha256",
    "related",
    "source_hash",
    "source_sequence",
}


class CandidateSidecarRuntimeError(RuntimeError):
    """The observational sidecar runtime could not prove exact correlation."""


_CURRENT_PROJECTION: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("s4_candidate_projection", default=None)
)


def _validated_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _PROJECTION_FIELDS:
        raise CandidateSidecarRuntimeError("candidate projection shape drift")
    try:
        record = build_candidate_call_record(
            source_sequence=value["source_sequence"],
            source_hash=value["source_hash"],
            logical_call_sha256=value["logical_call_sha256"],
            prompt_sha256="0" * 64,
            related=value["related"],
            invalidation=value["invalidation"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CandidateSidecarRuntimeError(
            "candidate projection is malformed"
        ) from error
    return {
        "source_sequence": record["source_sequence"],
        "source_hash": record["source_hash"],
        "logical_call_sha256": record["logical_call_sha256"],
        "related": copy.deepcopy(record["partitions"]["related"]),
        "invalidation": copy.deepcopy(record["partitions"]["invalidation"]),
    }


def current_candidate_projection() -> dict[str, Any] | None:
    """Return a copy of the projection active in this async task."""

    selected = _CURRENT_PROJECTION.get()
    return copy.deepcopy(selected) if selected is not None else None


@contextmanager
def activate_candidate_projection(value: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    """Activate one validated projection and restore the prior context."""

    selected = _validated_projection(value)
    token = _CURRENT_PROJECTION.set(selected)
    try:
        yield copy.deepcopy(selected)
    finally:
        _CURRENT_PROJECTION.reset(token)


def _parts(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        selected = asdict(value)
    elif isinstance(value, Mapping):
        selected = dict(value)
    else:
        try:
            selected = {name: getattr(value, name) for name in _PART_FIELDS}
        except AttributeError as error:
            raise CandidateSidecarRuntimeError("prompt parts are malformed") from error
    if set(selected) != _PART_FIELDS:
        raise CandidateSidecarRuntimeError("prompt parts shape drift")
    return selected


def _prompt_name(value: Any) -> str | None:
    decoding = _parts(value).get("decoding_config")
    if not isinstance(decoding, Mapping):
        return None
    selected = decoding.get("prompt_name")
    return str(selected) if selected is not None else None


def _prompt_hash(value: Any) -> str:
    return payload_sha256(_parts(value))


class CandidateSidecarPromptCache:
    """Correlate the actual normalized prompt hash at the cache boundary."""

    def __init__(
        self,
        inner: Any,
        *,
        mode: str,
        store: CaptureSidecarStore | None = None,
        binder: ReplaySidecarBinder | None = None,
    ) -> None:
        if mode not in {"capture", "replay"}:
            raise ValueError("unsupported candidate sidecar cache mode")
        if (mode == "capture") != (store is not None) or (mode == "replay") != (
            binder is not None
        ):
            raise ValueError("candidate sidecar cache dependency drift")
        if mode == "replay" and getattr(inner, "read_only", None) is not True:
            raise ValueError("candidate sidecar replay requires a read-only cache")
        self.inner = inner
        self.mode = mode
        self.store = store
        self.binder = binder
        self.capture_append_count = 0
        self.capture_reuse_count = 0
        self.replay_binding_count = 0
        self._capture_calls: set[tuple[int, str]] = set()

    @classmethod
    def capture(
        cls,
        inner: Any,
        *,
        store: CaptureSidecarStore,
    ) -> "CandidateSidecarPromptCache":
        return cls(inner, mode="capture", store=store)

    @classmethod
    def replay(
        cls,
        inner: Any,
        *,
        binder: ReplaySidecarBinder,
    ) -> "CandidateSidecarPromptCache":
        return cls(inner, mode="replay", binder=binder)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    @staticmethod
    def _projection() -> dict[str, Any]:
        selected = current_candidate_projection()
        if selected is None:
            raise CandidateSidecarRuntimeError(
                "candidate projection is not active for edge prompt"
            )
        return selected

    def _capture_record(
        self,
        parts: Any,
        record: Any,
        *,
        projection: Mapping[str, Any] | None = None,
    ) -> None:
        projection = dict(projection or self._projection())
        call_key = (
            int(projection["source_sequence"]),
            str(projection["logical_call_sha256"]),
        )
        if call_key in self._capture_calls:
            raise CandidateSidecarRuntimeError(
                "candidate call correlation repeated in one process"
            )
        actual_hash = getattr(record, "prompt_hash", None)
        record_parts = getattr(record, "prompt_parts", None)
        if (
            actual_hash != _prompt_hash(parts)
            or _parts(record_parts) != _parts(parts)
            or _prompt_hash(record_parts) != actual_hash
        ):
            raise CandidateSidecarRuntimeError("capture record prompt hash drift")
        sidecar_record = build_candidate_call_record(
            source_sequence=projection["source_sequence"],
            source_hash=projection["source_hash"],
            logical_call_sha256=projection["logical_call_sha256"],
            prompt_sha256=actual_hash,
            related=projection["related"],
            invalidation=projection["invalidation"],
        )
        assert self.store is not None
        if self.store.ensure(sidecar_record):
            self.capture_append_count += 1
        else:
            self.capture_reuse_count += 1
        self._capture_calls.add(call_key)

    def _replay_lease(self) -> Any:
        projection = self._projection()
        assert self.binder is not None
        return self.binder.prepare(
            source_sequence=projection["source_sequence"],
            source_hash=projection["source_hash"],
            logical_call_sha256=projection["logical_call_sha256"],
            related=projection["related"],
            invalidation=projection["invalidation"],
        )

    def _replay_get(self, callback: Any) -> Any:
        lease = self._replay_lease()
        binding = lease.binding
        committed = False
        try:
            with activate_replay_binding(binding):
                selected = callback()
            if inspect.isawaitable(selected):
                raise CandidateSidecarRuntimeError(
                    "async prompt cache operation is unsupported"
                )
            if selected is None:
                raise CandidateSidecarRuntimeError(
                    "sidecar replay prompt cache miss"
                )
            if (
                getattr(selected, "sidecar_binding_sha256", None)
                != replay_binding_sha256(binding)
                or getattr(selected, "sidecar_logical_call_sha256", None)
                != binding["logical_call_sha256"]
            ):
                raise CandidateSidecarRuntimeError(
                    "sidecar replay acknowledgement drift"
                )
            lease.commit()
            committed = True
            self.replay_binding_count += 1
            return selected
        finally:
            if not committed:
                lease.rollback()

    def get(self, parts: Any) -> Any | None:
        if _prompt_name(parts) != EDGE_PROMPT:
            return self.inner.get(parts)
        if self.mode == "capture":
            projection = self._projection()
            selected = self.inner.get(parts)
            if selected is not None:
                self._capture_record(
                    parts,
                    selected,
                    projection=projection,
                )
            return selected
        return self._replay_get(lambda: self.inner.get(parts))

    def put(self, parts: Any, *args: Any, **kwargs: Any) -> Any:
        target = _prompt_name(parts) == EDGE_PROMPT
        projection = None
        if target:
            if self.mode != "capture":
                raise CandidateSidecarRuntimeError(
                    "sidecar replay attempted a prompt cache write"
                )
            projection = self._projection()
            _prompt_hash(parts)
        selected = self.inner.put(parts, *args, **kwargs)
        if target:
            self._capture_record(parts, selected, projection=projection)
        return selected

    def record_unexpected(self, *args: Any, **kwargs: Any) -> Any:
        return self.inner.record_unexpected(*args, **kwargs)

    def resolve(self, parts: Any, *args: Any, **kwargs: Any) -> Any:
        if _prompt_name(parts) != EDGE_PROMPT:
            return self.inner.resolve(parts, *args, **kwargs)
        if self.mode == "capture":
            projection = self._projection()
            selected = self.inner.resolve(parts, *args, **kwargs)
            if inspect.isawaitable(selected):
                raise CandidateSidecarRuntimeError(
                    "async prompt cache resolve is unsupported"
                )
            self._capture_record(parts, selected, projection=projection)
            return selected
        return self._replay_get(
            lambda: self.inner.resolve(parts, *args, **kwargs)
        )


def _argument(
    args: tuple[Any, ...],
    kwargs: Mapping[str, Any],
    index: int,
    name: str,
) -> Any:
    if len(args) > index:
        return args[index]
    if name in kwargs:
        return kwargs[name]
    raise CandidateSidecarRuntimeError(f"edge hook argument {name} is missing")


@contextmanager
def install_candidate_sidecar_hook(
    edge_operations_module: Any,
    *,
    projector: Any,
) -> Iterator[None]:
    """Install an observational wrapper at Graphiti's pre-prompt edge boundary."""

    original = getattr(edge_operations_module, "resolve_extracted_edge", None)
    if not callable(original) or not callable(projector):
        raise CandidateSidecarRuntimeError("edge sidecar hook is unavailable")

    async def observe(*args: Any, **kwargs: Any) -> Any:
        projected = projector(
            extracted_edge=_argument(args, kwargs, 1, "extracted_edge"),
            related_edges=_argument(args, kwargs, 2, "related_edges"),
            invalidation_edges=_argument(args, kwargs, 3, "existing_edges"),
            episode=_argument(args, kwargs, 4, "episode"),
        )
        if inspect.isawaitable(projected):
            projected = await projected
        with activate_candidate_projection(projected):
            result = original(*args, **kwargs)
            return await result if inspect.isawaitable(result) else result

    setattr(edge_operations_module, "resolve_extracted_edge", observe)
    try:
        yield
    finally:
        setattr(edge_operations_module, "resolve_extracted_edge", original)
