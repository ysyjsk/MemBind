"""Passive wrappers for already-audited Graphiti semantic seam callables."""

from __future__ import annotations

import copy
import functools
import inspect
from collections.abc import Callable, Mapping
from typing import Any, TypeVar


T = TypeVar("T")
ObservationSink = Callable[[dict[str, Any]], Any]
NOT_OBSERVABLE = "NOT_OBSERVABLE"


def _snapshot(value: Any) -> Any:
    """Copy only for observer input; the wrapped callable receives originals."""

    try:
        return copy.deepcopy(value)
    except (TypeError, ValueError):
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, (list, tuple)):
            return type(value)(value)
        return value


def _record(args: tuple[Any, ...], kwargs: Mapping[str, Any], result: Any) -> dict[str, Any]:
    return {
        "source_id": NOT_OBSERVABLE,
        "operator_type": NOT_OBSERVABLE,
        "semantic_stage": NOT_OBSERVABLE,
        "input_semantic_hash": NOT_OBSERVABLE,
        "output_semantic_hash": NOT_OBSERVABLE,
        "candidate_identity_hash": NOT_OBSERVABLE,
        "candidate_order_hash": NOT_OBSERVABLE,
        "candidate_count": NOT_OBSERVABLE,
        "bound_state_version": NOT_OBSERVABLE,
        "batch_membership_hash": NOT_OBSERVABLE,
        "batch_order_hash": NOT_OBSERVABLE,
        "batch_size": NOT_OBSERVABLE,
        "resolution_decision_hash": NOT_OBSERVABLE,
        "effect_hash": NOT_OBSERVABLE,
        "effect_count": NOT_OBSERVABLE,
        "publication_version": NOT_OBSERVABLE,
        "args": _snapshot(args),
        "kwargs": _snapshot(dict(kwargs)),
        "result": _snapshot(result),
        "provider_calls": 0,
        "db_io": 0,
    }


def observe_call(
    callable_: Callable[..., T],
    *args: Any,
    observer: ObservationSink | None = None,
    **kwargs: Any,
) -> T:
    """Observe one synchronous seam call without changing its inputs/return."""

    if inspect.iscoroutinefunction(callable_):
        raise TypeError("ASYNC_SEAM_REQUIRES_AOBSERVE_CALL")
    result = callable_(*args, **kwargs)
    if observer is not None:
        observer(_record(args, kwargs, result))
    return result


async def aobserve_call(
    callable_: Callable[..., Any],
    *args: Any,
    observer: ObservationSink | None = None,
    **kwargs: Any,
) -> Any:
    """Async counterpart used by extraction, resolution, and effect seams."""

    result = callable_(*args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    if observer is not None:
        observer(_record(args, kwargs, result))
    return result


def wrap_seam(callable_: Callable[..., T], observer: ObservationSink) -> Callable[..., T]:
    """Return a transparent sync/async wrapper for a real seam callable."""

    if not callable(callable_) or not callable(observer):
        raise TypeError("SEAM_OBSERVER_CALLABLE_REQUIRED")
    if inspect.iscoroutinefunction(callable_):
        @functools.wraps(callable_)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return await aobserve_call(callable_, *args, observer=observer, **kwargs)

        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(callable_)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        return observe_call(callable_, *args, observer=observer, **kwargs)

    return sync_wrapper


__all__ = ["NOT_OBSERVABLE", "aobserve_call", "observe_call", "wrap_seam"]
