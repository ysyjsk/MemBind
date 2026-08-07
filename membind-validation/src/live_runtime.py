"""Graphiti lifecycle helpers for isolated live experiment runs."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from typing import Any


COUNT_NODES_QUERY = "MATCH (n) RETURN count(n) AS node_count"
WarmUp = Callable[[Any], Awaitable[Any]]


class GraphNotEmptyError(RuntimeError):
    """Raised when a lifecycle gate expected an empty graph."""


async def clear_database(graphiti: Any) -> None:
    """Clear all graph data through the graphiti-core 0.29.3 driver API."""

    driver = _driver(graphiti)
    graph_ops = getattr(driver, "graph_ops", None)
    clear_data = getattr(graph_ops, "clear_data", None)
    if not callable(clear_data):
        raise TypeError("graphiti.driver.graph_ops.clear_data is required")
    await clear_data(driver)


async def count_nodes(graphiti: Any) -> int:
    """Return the total number of nodes in Graphiti's configured database."""

    driver = _driver(graphiti)
    execute_query = getattr(driver, "execute_query", None)
    if not callable(execute_query):
        raise TypeError("graphiti.driver.execute_query is required")

    result = await execute_query(COUNT_NODES_QUERY)
    records = _query_records(result)
    if not records:
        raise RuntimeError("node count query returned no records")

    try:
        value = records[0]["node_count"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("node count query did not return node_count") from exc
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError(f"node count query returned invalid value: {value!r}")
    return value


async def prepare_clean_graph(graphiti: Any, warm_up: WarmUp) -> None:
    """Build schema and warm Graphiti while leaving an asserted-empty graph."""

    if not callable(warm_up):
        raise TypeError("warm_up must be an async callable")

    await clear_database(graphiti)
    init_task = getattr(_driver(graphiti), "_init_task", None)
    if init_task is not None:
        await init_task
    else:
        build_indices = getattr(graphiti, "build_indices_and_constraints", None)
        if not callable(build_indices):
            raise TypeError("graphiti.build_indices_and_constraints is required")
        await build_indices()
    await _verify_empty(graphiti, "after initial clear")

    try:
        await warm_up(graphiti)
    finally:
        await clear_database(graphiti)
        await _verify_empty(graphiti, "after warm-up clear")


async def close(graphiti: Any | None) -> None:
    """Close Graphiti, with a driver fallback for injectable test runtimes."""

    if graphiti is None:
        return

    close_method = getattr(graphiti, "close", None)
    if not callable(close_method):
        close_method = getattr(getattr(graphiti, "driver", None), "close", None)
    if not callable(close_method):
        raise TypeError("graphiti.close or graphiti.driver.close is required")

    result = close_method()
    if inspect.isawaitable(result):
        await result


def _driver(graphiti: Any) -> Any:
    driver = getattr(graphiti, "driver", None)
    if driver is None:
        raise TypeError("graphiti.driver is required")
    return driver


def _query_records(result: Any) -> Sequence[Any]:
    records = getattr(result, "records", None)
    if records is not None:
        return records
    if isinstance(result, tuple) and result:
        return result[0]
    if isinstance(result, list):
        return result
    raise RuntimeError(f"unsupported query result: {type(result).__name__}")


async def _verify_empty(graphiti: Any, phase: str) -> None:
    node_count = await count_nodes(graphiti)
    if node_count != 0:
        raise GraphNotEmptyError(f"graph has {node_count} nodes {phase}")
