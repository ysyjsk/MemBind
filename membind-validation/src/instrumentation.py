"""Per-episode context and metric attribution for concurrent Graphiti calls."""

from __future__ import annotations

import contextvars
import re
from contextlib import contextmanager
from typing import Any, Iterator


EpisodeKey = tuple[str, int]
_CURRENT_EPISODE: contextvars.ContextVar[EpisodeKey | None] = contextvars.ContextVar(
    "membind_current_episode",
    default=None,
)

_WRITE_QUERY_RE = re.compile(
    r"\b(?:CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|FOREACH)\b", re.IGNORECASE
)


class _InstrumentedSession:
    def __init__(self, inner: Any, events: list[dict[str, Any]]) -> None:
        self._inner = inner
        self._events = events

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _record(self, kind: str, query: str | None = None) -> None:
        self._events.append(
            {"episode_key": current_episode_key(), "kind": kind, "query": query}
        )

    async def execute_write(self, *args: Any, **kwargs: Any) -> Any:
        self._record("write")
        return await self._inner.execute_write(*args, **kwargs)

    async def execute_read(self, *args: Any, **kwargs: Any) -> Any:
        self._record("query")
        return await self._inner.execute_read(*args, **kwargs)

    async def run(self, cypher_query_: Any, *args: Any, **kwargs: Any) -> Any:
        text = str(cypher_query_)
        self._record("write" if _WRITE_QUERY_RE.search(text) else "query", text)
        return await self._inner.run(cypher_query_, *args, **kwargs)

    async def close(self) -> Any:
        return await self._inner.close()

    async def __aenter__(self) -> "_InstrumentedSession":
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> Any:
        return await self._inner.__aexit__(*args)


def current_episode_key() -> EpisodeKey | None:
    return _CURRENT_EPISODE.get()


@contextmanager
def episode_scope(run_id: str, source_sequence: int) -> Iterator[None]:
    token = _CURRENT_EPISODE.set((str(run_id), int(source_sequence)))
    try:
        yield
    finally:
        _CURRENT_EPISODE.reset(token)


def install_driver_instrumentation(graphiti: Any) -> None:
    """Attach an idempotent query counter to a Graphiti driver instance."""
    driver = getattr(graphiti, "driver", None)
    if driver is None or not callable(getattr(driver, "execute_query", None)):
        return
    if getattr(driver, "_membind_query_instrumented", False):
        return
    original = driver.execute_query
    original_session = getattr(driver, "session", None)
    events: list[dict[str, Any]] = []

    async def counted_execute_query(cypher_query_: Any, *args: Any, **kwargs: Any) -> Any:
        text = str(cypher_query_)
        kind = "write" if _WRITE_QUERY_RE.search(text) else "query"
        event = {"episode_key": current_episode_key(), "kind": kind, "query": text}
        events.append(event)
        return await original(cypher_query_, *args, **kwargs)

    driver.execute_query = counted_execute_query
    if callable(original_session):
        def counted_session(*args: Any, **kwargs: Any) -> _InstrumentedSession:
            return _InstrumentedSession(original_session(*args, **kwargs), events)

        driver.session = counted_session
    driver.query_events = events
    driver._membind_query_instrumented = True


def apply_episode_metrics(graphiti: Any, trace: Any) -> None:
    key = (str(trace.run_id), int(trace.source_sequence))
    llm = getattr(graphiti, "llm_client", None)
    llm = getattr(llm, "inner", llm)
    llm_events = [
        event
        for event in (getattr(llm, "call_events", []) or [])
        if tuple(event.get("episode_key") or ()) == key
    ]
    trace.llm_call_count = len(llm_events)
    trace.llm_input_tokens = sum(
        int(event.get("token_usage", {}).get("prompt_tokens", 0)) for event in llm_events
    )
    trace.llm_output_tokens = sum(
        int(event.get("token_usage", {}).get("completion_tokens", 0)) for event in llm_events
    )

    embedder = getattr(graphiti, "embedder", None)
    embedding_events = [
        event
        for event in (getattr(embedder, "call_events", []) or [])
        if tuple(event.get("episode_key") or ()) == key
    ]
    trace.embedding_call_count = len(embedding_events)
    trace.extra["embedding_text_count"] = sum(
        int(event.get("text_count", 0)) for event in embedding_events
    )

    driver = getattr(graphiti, "driver", None)
    query_events = getattr(driver, "query_events", []) or []
    query_events = [
        event
        for event in query_events
        if tuple(event.get("episode_key") or ()) == key
    ]
    trace.db_query_count = sum(event.get("kind") == "query" for event in query_events)
    trace.db_write_count = sum(event.get("kind") == "write" for event in query_events)
