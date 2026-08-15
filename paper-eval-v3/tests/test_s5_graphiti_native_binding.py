"""TDD tests for the pinned Native Graphiti callable boundary."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from paper_eval.s5_graphiti_native_binding import (
    S5GraphitiBindingError,
    build_native_add_episode_callable,
    load_graphiti_native_binding,
    verify_native_binding_identity,
)


SHA = "a" * 64


def _fake_module(calls: list[tuple[object, object]]) -> SimpleNamespace:
    def graphiti_episode_kwargs(episode: object) -> dict[str, object]:
        calls.append(("kwargs", episode))
        return {"episode": episode}

    async def add_episode(graphiti: object, episode: object) -> None:
        calls.append((graphiti, episode))

    graphiti_episode_kwargs.__module__ = "graphiti_native"
    graphiti_episode_kwargs.__qualname__ = "graphiti_episode_kwargs"
    add_episode.__module__ = "graphiti_native"
    add_episode.__qualname__ = "add_episode"
    return SimpleNamespace(
        __name__="graphiti_native",
        add_episode=add_episode,
        graphiti_episode_kwargs=graphiti_episode_kwargs,
    )


def test_loader_freezes_exact_native_symbols_without_live_import() -> None:
    calls: list[tuple[object, object]] = []
    module = _fake_module(calls)
    binding = load_graphiti_native_binding(lambda name: module)
    assert binding.module_name == "graphiti_native"
    assert binding.add_episode.__name__ == "add_episode"
    assert binding.graphiti_episode_kwargs.__name__ == "graphiti_episode_kwargs"
    identity = verify_native_binding_identity(
        {
            "module_name": binding.module_name,
            "add_episode_qualname": binding.add_episode.__qualname__,
            "graphiti_episode_kwargs_qualname": binding.graphiti_episode_kwargs.__qualname__,
            "source_file_sha256": SHA,
        }
    )
    assert identity["source_file_sha256"] == SHA
    assert calls == []


@pytest.mark.asyncio
async def test_wrapper_calls_the_same_native_add_episode_object() -> None:
    calls: list[tuple[object, object]] = []
    binding = load_graphiti_native_binding(lambda _name: _fake_module(calls))
    wrapper = build_native_add_episode_callable(graphiti="graphiti", binding=binding)
    await wrapper("episode")
    assert calls == [("graphiti", "episode")]


@pytest.mark.asyncio
async def test_wrapper_rejects_non_awaitable_native_boundary() -> None:
    calls: list[tuple[object, object]] = []
    module = _fake_module(calls)

    async def wrong(_graphiti: object, _episode: object) -> None:
        return None

    module.add_episode = lambda _graphiti, _episode: None
    module.add_episode.__module__ = "graphiti_native"
    module.add_episode.__qualname__ = "add_episode"
    binding = load_graphiti_native_binding(lambda _name: module)
    wrapper = build_native_add_episode_callable(graphiti="graphiti", binding=binding)
    with pytest.raises(S5GraphitiBindingError, match="native_add_episode_not_awaitable"):
        await wrapper("episode")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda module: setattr(module, "__name__", "legacy_graphiti_native"),
        lambda module: setattr(module, "add_episode", object()),
        lambda module: setattr(module, "graphiti_episode_kwargs", object()),
    ],
)
def test_loader_fails_closed_on_symbol_or_module_drift(mutate) -> None:
    module = _fake_module([])
    mutate(module)
    with pytest.raises(S5GraphitiBindingError):
        load_graphiti_native_binding(lambda _name: module)


def test_identity_rejects_legacy_or_private_fields() -> None:
    with pytest.raises(S5GraphitiBindingError):
        verify_native_binding_identity(
            {
                "module_name": "graphiti_native",
                "add_episode_qualname": "M2",
                "graphiti_episode_kwargs_qualname": "graphiti_episode_kwargs",
                "source_file_sha256": SHA,
            }
        )
    with pytest.raises(S5GraphitiBindingError):
        verify_native_binding_identity(
            {
                "module_name": "graphiti_native",
                "add_episode_qualname": "add_episode",
                "graphiti_episode_kwargs_qualname": "graphiti_episode_kwargs",
                "source_file_sha256": SHA,
                "api_key": "forbidden",
            }
        )
