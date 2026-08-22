"""Small deterministic, typed canonicalization helpers used by V5 identities."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any


def freeze(value: Any) -> Any:
    """Return an immutable representation and reject ambiguous values."""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise TypeError("non-finite float is not canonicalizable")
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return freeze(value.value)
    if isinstance(value, type):
        return (("__type__", f"{value.__module__}.{value.__qualname__}"),)
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return freeze(value.model_dump(mode="json"))
    if is_dataclass(value):
        return freeze(asdict(value))
    if isinstance(value, Mapping):
        items = ((str(key), freeze(item)) for key, item in value.items())
        return tuple(sorted(items, key=lambda item: item[0]))
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((freeze(item) for item in value), key=repr))
    raise TypeError(f"unsupported canonical value type: {type(value).__name__}")


def thaw(value: Any) -> Any:
    if isinstance(value, tuple):
        if all(isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str) for item in value):
            return {key: thaw(item) for key, item in value}
        return [thaw(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    import json

    return json.dumps(thaw(freeze(value)), ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
