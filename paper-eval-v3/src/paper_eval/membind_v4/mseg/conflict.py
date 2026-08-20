"""Conservative read/effect-scope conflict certification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MSEGConflictError(ValueError):
    """A memory scope is malformed."""


def _fail(code: str) -> MSEGConflictError:
    return MSEGConflictError(code)


class ConflictClass(str, Enum):
    CERTIFIED_NON_CONFLICTING = "CERTIFIED_NON_CONFLICTING"
    CONFLICTING = "CONFLICTING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class MemoryScope:
    namespace: str
    read_items: frozenset[str]
    effect_items: frozenset[str]
    complete: bool
    unresolved_reason: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, str) or not self.namespace:
            raise _fail("namespace_invalid")
        for values, code in (
            (self.read_items, "read_scope_invalid"),
            (self.effect_items, "effect_scope_invalid"),
        ):
            if not isinstance(values, frozenset) or not all(
                isinstance(value, str) and value for value in values
            ):
                raise _fail(code)
        if not isinstance(self.complete, bool):
            raise _fail("scope_completeness_invalid")
        if self.complete and self.unresolved_reason is not None:
            raise _fail("known_scope_has_unresolved_reason")
        if not self.complete and (
            not isinstance(self.unresolved_reason, str) or not self.unresolved_reason
        ):
            raise _fail("unknown_scope_reason_missing")

    @classmethod
    def known(
        cls,
        *,
        namespace: str,
        read_items: set[str] | frozenset[str],
        effect_items: set[str] | frozenset[str],
    ) -> "MemoryScope":
        return cls(
            namespace=namespace,
            read_items=frozenset(read_items),
            effect_items=frozenset(effect_items),
            complete=True,
            unresolved_reason=None,
        )

    @classmethod
    def unknown(
        cls,
        *,
        namespace: str,
        reason: str,
        read_items: set[str] | frozenset[str] = frozenset(),
        effect_items: set[str] | frozenset[str] = frozenset(),
    ) -> "MemoryScope":
        return cls(
            namespace=namespace,
            read_items=frozenset(read_items),
            effect_items=frozenset(effect_items),
            complete=False,
            unresolved_reason=reason,
        )


def classify_operator_conflict(left: MemoryScope, right: MemoryScope) -> ConflictClass:
    if not isinstance(left, MemoryScope) or not isinstance(right, MemoryScope):
        raise _fail("memory_scope_invalid")
    if left.namespace != right.namespace:
        return ConflictClass.CERTIFIED_NON_CONFLICTING
    known_conflict = bool(
        left.read_items & right.effect_items
        or right.read_items & left.effect_items
        or left.effect_items & right.effect_items
    )
    if known_conflict:
        return ConflictClass.CONFLICTING
    if not left.complete or not right.complete:
        return ConflictClass.UNKNOWN
    return ConflictClass.CERTIFIED_NON_CONFLICTING
