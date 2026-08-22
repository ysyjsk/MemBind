"""Exact native-callsite binding with fail-closed strict semantics."""

from __future__ import annotations

import contextvars
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .request_identity import RequestIdentity
from .transcript import BindingMismatch, TranscriptStore


class BindingScopeError(RuntimeError):
    pass


class NativeBindingScope(AbstractContextManager["NativeBindingScope"]):
    _current: contextvars.ContextVar["NativeBindingScope | None"] = contextvars.ContextVar("membind_v5_binding_scope", default=None)

    def __init__(self, store: TranscriptStore, *, source_sequence: int, strict: bool = True) -> None:
        self.store = store
        self.source_sequence = int(source_sequence)
        self.strict = bool(strict)
        self._token: contextvars.Token[NativeBindingScope | None] | None = None
        self._consumed: list[str] = []

    def __enter__(self) -> "NativeBindingScope":
        if self._current.get() is not None:
            raise BindingScopeError("nested binding scopes are not supported")
        self._token = self._current.set(self)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if exc is None and self.store.unconsumed_for_source(self.source_sequence):
                raise BindingMismatch("unconsumed transcript at native scope finalization")
        finally:
            if self._token is not None:
                self._current.reset(self._token)
                self._token = None

    @classmethod
    def current(cls) -> "NativeBindingScope | None":
        return cls._current.get()

    def consume(self, identity: RequestIdentity) -> Any:
        if identity.source_sequence != self.source_sequence:
            raise BindingMismatch("source sequence mismatch")
        value = self.store.consume(identity)
        self._consumed.append(identity.digest)
        return value

    async def invoke(
        self,
        identity: RequestIdentity,
        delegate: Callable[[], Awaitable[Any]],
        *,
        certified: bool,
    ) -> Any:
        if certified:
            try:
                return self.consume(identity)
            except BindingMismatch:
                if self.strict:
                    raise
        return await delegate()


def bind_or_delegate(
    identity: RequestIdentity,
    store: TranscriptStore,
    delegate: Callable[[], Awaitable[Any]],
    *,
    certified: bool,
    strict: bool = True,
) -> Awaitable[Any]:
    scope = NativeBindingScope.current()
    if scope is None:
        if certified and strict:
            raise BindingScopeError("certified call outside native binding scope")
        return delegate()
    return scope.invoke(identity, delegate, certified=certified)

