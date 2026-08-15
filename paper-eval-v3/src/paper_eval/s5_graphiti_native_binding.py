"""Pinned callable boundary for the shared Native Graphiti construction path.

Import resolution is injectable for offline tests.  The default loader is
lazy; constructing this object does not construct Graphiti or contact a
service.  A live authority must separately bind the module source hash and
Graphiti version/commit.
"""

from __future__ import annotations

import importlib
import inspect
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_FIELDS = {
    "api_key",
    "authority",
    "body",
    "content",
    "credential",
    "episode_body",
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


class S5GraphitiBindingError(ValueError):
    """Stable, sanitized Native binding failure."""


def _fail(code: str) -> S5GraphitiBindingError:
    return S5GraphitiBindingError(code)


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_or_legacy_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


@dataclass(frozen=True)
class S5GraphitiNativeBinding:
    module_name: str
    add_episode: Callable[..., object]
    graphiti_episode_kwargs: Callable[..., object]


def _symbol(value: object, *, name: str) -> Callable[..., object]:
    if not callable(value):
        raise _fail(f"{name}_not_callable")
    if getattr(value, "__module__", None) != "graphiti_native":
        raise _fail(f"{name}_module_drift")
    if getattr(value, "__qualname__", None) != name:
        raise _fail(f"{name}_qualname_drift")
    return value


def load_graphiti_native_binding(
    module_loader: Callable[[str], object] = importlib.import_module,
) -> S5GraphitiNativeBinding:
    """Resolve only the pinned `graphiti_native` symbols."""

    if not callable(module_loader):
        raise _fail("module_loader_not_callable")
    try:
        module = module_loader("graphiti_native")
    except Exception:
        raise _fail("graphiti_native_import_failed") from None
    if getattr(module, "__name__", None) != "graphiti_native":
        raise _fail("graphiti_native_module_drift")
    add_episode = _symbol(getattr(module, "add_episode", None), name="add_episode")
    episode_kwargs = _symbol(
        getattr(module, "graphiti_episode_kwargs", None),
        name="graphiti_episode_kwargs",
    )
    return S5GraphitiNativeBinding(
        module_name="graphiti_native",
        add_episode=add_episode,
        graphiti_episode_kwargs=episode_kwargs,
    )


def build_native_add_episode_callable(
    *,
    graphiti: object,
    binding: S5GraphitiNativeBinding,
) -> Callable[[object], Any]:
    """Return a wrapper that invokes the exact bound Native callable."""

    if not isinstance(binding, S5GraphitiNativeBinding):
        raise _fail("binding_invalid")

    async def invoke(episode: object) -> object:
        result = binding.add_episode(graphiti, episode)
        if not inspect.isawaitable(result):
            raise _fail("native_add_episode_not_awaitable")
        return await result

    return invoke


def verify_native_binding_identity(value: Mapping[str, object]) -> dict[str, object]:
    """Verify the public source identity recorded by a future production run."""

    if not isinstance(value, Mapping):
        raise _fail("identity_not_mapping")
    identity = deepcopy(dict(value))
    _assert_public(identity)
    if set(identity) != {
        "module_name",
        "add_episode_qualname",
        "graphiti_episode_kwargs_qualname",
        "source_file_sha256",
    }:
        raise _fail("identity_shape_invalid")
    if (
        identity.get("module_name") != "graphiti_native"
        or identity.get("add_episode_qualname") != "add_episode"
        or identity.get("graphiti_episode_kwargs_qualname")
        != "graphiti_episode_kwargs"
        or not isinstance(identity.get("source_file_sha256"), str)
        or _SHA256.fullmatch(identity["source_file_sha256"]) is None
    ):
        raise _fail("identity_binding_invalid")
    return identity


__all__ = [
    "S5GraphitiBindingError",
    "S5GraphitiNativeBinding",
    "build_native_add_episode_callable",
    "load_graphiti_native_binding",
    "verify_native_binding_identity",
]
