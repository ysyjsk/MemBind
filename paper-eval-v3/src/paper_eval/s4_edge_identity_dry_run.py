"""Hard fences for the bounded, cache-only S4 source-7 dry run.

All helpers are independently testable with synthetic clients.  The module
does not construct Graphiti, connect to Neo4j, or read private caches.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import re
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any


_MUTATING_CYPHER = re.compile(
    r"\b(?:CREATE|MERGE|SET|DELETE|REMOVE|DROP|LOAD\s+CSV|FOREACH)\b",
    re.IGNORECASE,
)
_CALL = re.compile(r"\bCALL\s+([^\s(]+)", re.IGNORECASE)
_READ_ONLY_PROCEDURES = (
    "db.index.fulltext.query",
    "db.index.vector.query",
)


class D2FenceError(RuntimeError):
    """A forbidden network, database, or publication path was attempted."""


class D2DiagnosticStop(RuntimeError):
    """The exact candidate evidence was collected; Graphiti must now stop."""


class D2EvidenceIncomplete(RuntimeError):
    """The bounded hook did not establish complete, unique call coverage."""


@dataclass
class D2SideEffectCounters:
    """Counters reported by the dry-run verifier; all writes must remain zero."""

    network_call_count: int = 0
    live_llm_call_count: int = 0
    live_embedding_call_count: int = 0
    cross_encoder_call_count: int = 0
    db_write_count: int = 0
    publication_count: int = 0
    neo4j_read_count: int = 0

    def public_dict(self) -> dict[str, int]:
        return {
            name: int(getattr(self, name))
            for name in self.__dataclass_fields__
        }


class LiveCallSentinel:
    """A terminal client that records and rejects every external model call."""

    def __init__(self, counters: D2SideEffectCounters) -> None:
        self.counters = counters

    def _reject(self, kind: str) -> None:
        self.counters.network_call_count += 1
        if kind == "llm":
            self.counters.live_llm_call_count += 1
        elif kind == "embedding":
            self.counters.live_embedding_call_count += 1
        elif kind == "cross_encoder":
            self.counters.cross_encoder_call_count += 1
        raise D2FenceError(f"forbidden live call: {kind}")

    async def generate_response(self, *args: Any, **kwargs: Any) -> Any:
        self._reject("llm")

    async def _generate_response(self, *args: Any, **kwargs: Any) -> Any:
        self._reject("llm")

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        self._reject("embedding")

    async def create_batch(self, *args: Any, **kwargs: Any) -> Any:
        self._reject("embedding")

    async def rank(self, *args: Any, **kwargs: Any) -> Any:
        self._reject("cross_encoder")


@contextmanager
def replace_terminal_inner(root: Any, sentinel: Any) -> Iterator[None]:
    """Replace only the leaf behind a wrapper chain and always restore it."""

    parent = root
    seen: set[int] = set()
    while True:
        if id(parent) in seen:
            raise D2FenceError("client wrapper chain contains a cycle")
        seen.add(id(parent))
        if not hasattr(parent, "inner"):
            raise D2FenceError("client wrapper chain has no terminal inner client")
        child = getattr(parent, "inner")
        if not hasattr(child, "inner"):
            break
        parent = child
    original = child
    setattr(parent, "inner", sentinel)
    try:
        yield
    finally:
        setattr(parent, "inner", original)


@contextmanager
def model_client_fence(
    graph: Any, counters: D2SideEffectCounters
) -> Iterator[None]:
    """Keep cache wrappers active while replacing every live transport leaf."""

    clients = getattr(graph, "clients", None)
    llm = getattr(graph, "llm_client", None)
    embedder = getattr(graph, "embedder", None)
    cross_encoder = getattr(graph, "cross_encoder", None)
    if (
        clients is None
        or llm is None
        or embedder is None
        or getattr(clients, "llm_client", None) is not llm
        or getattr(clients, "embedder", None) is not embedder
        or getattr(clients, "cross_encoder", None) is not cross_encoder
    ):
        raise D2FenceError("Graphiti retained model-client references drifted")

    sentinel = LiveCallSentinel(counters)
    child_refs: list[tuple[Any, Any]] = []
    with ExitStack() as stack:
        stack.enter_context(replace_terminal_inner(llm, sentinel))
        stack.enter_context(replace_terminal_inner(embedder, sentinel))
        for container_name in ("nodes", "edges"):
            container = getattr(graph, container_name, None)
            if container is None:
                continue
            for child_name in ("entity", "community"):
                child = getattr(container, child_name, None)
                if child is not None and hasattr(child, "_embedder"):
                    child_refs.append((child, getattr(child, "_embedder")))
                    setattr(child, "_embedder", embedder)
        graph.cross_encoder = sentinel
        clients.cross_encoder = sentinel
        try:
            yield
        finally:
            graph.cross_encoder = cross_encoder
            clients.cross_encoder = cross_encoder
            for child, original in reversed(child_refs):
                setattr(child, "_embedder", original)


def _is_read_only_query(query: object, routing: object) -> bool:
    if not isinstance(query, str) or routing not in {None, "r"}:
        return False
    if _MUTATING_CYPHER.search(query) is not None:
        return False
    for match in _CALL.finditer(query):
        procedure = match.group(1).casefold()
        if not procedure.startswith(_READ_ONLY_PROCEDURES):
            return False
    return True


@contextmanager
def read_only_database_fence(
    driver: Any, counters: D2SideEffectCounters
) -> Iterator[None]:
    """Permit only read-routed driver queries and reject bypass surfaces."""

    if getattr(driver, "_init_task", None) is not None:
        raise D2FenceError("driver schema initialization task must be absent")
    client = getattr(driver, "client", None)
    if client is None:
        raise D2FenceError("database driver has no native client")

    required_driver = (
        "execute_query",
        "session",
        "transaction",
        "build_indices_and_constraints",
        "delete_all_indexes",
    )
    if any(not hasattr(driver, name) for name in required_driver):
        raise D2FenceError("database driver fence surface is incomplete")
    if not hasattr(client, "execute_query") or not hasattr(client, "session"):
        raise D2FenceError("native database client fence surface is incomplete")

    originals = {
        "driver_execute": driver.execute_query,
        "driver_session": driver.session,
        "driver_transaction": driver.transaction,
        "driver_build": driver.build_indices_and_constraints,
        "driver_delete_indexes": driver.delete_all_indexes,
        "client_execute": client.execute_query,
        "client_session": client.session,
    }
    native_allowed = contextvars.ContextVar(
        "membind_s4_d2_native_query_allowed", default=False
    )

    async def native_execute(cypher_query_: str, *args: Any, **kwargs: Any) -> Any:
        if not native_allowed.get():
            counters.db_write_count += 1
            raise D2FenceError("direct native database query bypass rejected")
        return await originals["client_execute"](cypher_query_, *args, **kwargs)

    def native_session(*args: Any, **kwargs: Any) -> Any:
        counters.db_write_count += 1
        raise D2FenceError("direct native database session bypass rejected")

    async def driver_execute(cypher_query_: str, *args: Any, **kwargs: Any) -> Any:
        if not _is_read_only_query(cypher_query_, kwargs.get("routing_")):
            counters.db_write_count += 1
            raise D2FenceError("read-only database contract rejected a query")
        counters.neo4j_read_count += 1
        read_kwargs = dict(kwargs)
        read_kwargs["routing_"] = "r"
        token = native_allowed.set(True)
        try:
            return await originals["driver_execute"](
                cypher_query_, *args, **read_kwargs
            )
        finally:
            native_allowed.reset(token)

    def blocked_session(*args: Any, **kwargs: Any) -> Any:
        counters.db_write_count += 1
        raise D2FenceError("database session path is forbidden")

    @asynccontextmanager
    async def blocked_transaction(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        counters.db_write_count += 1
        raise D2FenceError("database transaction path is forbidden")
        yield  # pragma: no cover

    async def blocked_schema(*args: Any, **kwargs: Any) -> Any:
        counters.db_write_count += 1
        raise D2FenceError("database schema mutation path is forbidden")

    operation_originals: list[tuple[Any, str, Any]] = []
    seen_operations: set[int] = set()
    for attribute, operation in vars(driver).items():
        if not attribute.endswith("_ops") or operation is None:
            continue
        if id(operation) in seen_operations:
            continue
        seen_operations.add(id(operation))
        for name in dir(operation):
            if not (
                name in {"build_indices_and_constraints", "clear_data"}
                or name.startswith("delete")
                or name.startswith("save")
            ):
                continue
            original_operation = getattr(operation, name, None)
            if not callable(original_operation):
                continue

            async def blocked_operation(
                *args: Any,
                _name: str = name,
                **kwargs: Any,
            ) -> Any:
                counters.db_write_count += 1
                raise D2FenceError(
                    f"database operation mutation path is forbidden: {_name}"
                )

            operation_originals.append((operation, name, original_operation))
            setattr(operation, name, blocked_operation)

    setattr(client, "execute_query", native_execute)
    setattr(client, "session", native_session)
    setattr(driver, "execute_query", driver_execute)
    setattr(driver, "session", blocked_session)
    setattr(driver, "transaction", blocked_transaction)
    setattr(driver, "build_indices_and_constraints", blocked_schema)
    setattr(driver, "delete_all_indexes", blocked_schema)
    try:
        yield
    finally:
        for operation, name, original in reversed(operation_originals):
            setattr(operation, name, original)
        setattr(driver, "delete_all_indexes", originals["driver_delete_indexes"])
        setattr(driver, "build_indices_and_constraints", originals["driver_build"])
        setattr(driver, "transaction", originals["driver_transaction"])
        setattr(driver, "session", originals["driver_session"])
        setattr(driver, "execute_query", originals["driver_execute"])
        setattr(client, "session", originals["client_session"])
        setattr(client, "execute_query", originals["client_execute"])


@contextmanager
def publication_fence(
    graph: Any, counters: D2SideEffectCounters
) -> Iterator[None]:
    """Reject Graphiti's first persistence boundary before it can publish."""

    original = getattr(graph, "_process_episode_data", None)
    if not callable(original):
        raise D2FenceError("Graphiti publication boundary is unavailable")

    async def blocked(*args: Any, **kwargs: Any) -> Any:
        counters.publication_count += 1
        raise D2FenceError("Graphiti publication path is forbidden")

    setattr(graph, "_process_episode_data", blocked)
    try:
        yield
    finally:
        setattr(graph, "_process_episode_data", original)


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _call_correlation(edge: Any) -> str:
    source = _field(edge, "source_node_uuid")
    target = _field(edge, "target_node_uuid")
    fact = _field(edge, "fact")
    if not all(isinstance(value, str) and value for value in (source, target, fact)):
        raise D2EvidenceIncomplete("edge call correlation fields are incomplete")
    encoded = json.dumps(
        {"fact": fact, "source": source, "target": target},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class EdgeCandidateBarrier:
    """Collect every concurrent pre-prompt edge call before stopping them all."""

    def __init__(self, *, expected_call_count: int, timeout_seconds: float) -> None:
        if expected_call_count <= 0 or timeout_seconds <= 0:
            raise ValueError("barrier count and timeout must be positive")
        self.expected_call_count = int(expected_call_count)
        self.timeout_seconds = float(timeout_seconds)
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._complete = asyncio.Event()
        self._all_released = asyncio.Event()
        self._failure: D2EvidenceIncomplete | None = None
        self.call_count = 0
        self.released_call_count = 0
        self.duplicate_correlation_count = 0

    @property
    def records(self) -> list[dict[str, Any]]:
        return [self._records[key] for key in sorted(self._records)]

    async def wait_until_all_released(self) -> None:
        """Wait until every exact-call hook has crossed the shared barrier."""

        try:
            await asyncio.wait_for(
                self._all_released.wait(), timeout=self.timeout_seconds
            )
        except TimeoutError as error:
            raise D2EvidenceIncomplete(
                "edge call hooks did not all exit the diagnostic barrier"
            ) from error

    async def observe(
        self,
        *,
        extracted_edge: Any,
        related_edges: Sequence[Any],
        invalidation_edges: Sequence[Any],
        original: Any | None = None,
    ) -> Any:
        """Record one call; ``original`` is accepted only to prove it is unused."""

        del original
        correlation = _call_correlation(extracted_edge)
        async with self._lock:
            self.call_count += 1
            if self._failure is None and correlation in self._records:
                self.duplicate_correlation_count += 1
                self._failure = D2EvidenceIncomplete(
                    "duplicate edge call correlation"
                )
            if self._failure is None and self.call_count > self.expected_call_count:
                self._failure = D2EvidenceIncomplete(
                    "edge call count exceeded the bounded contract"
                )
            if self._failure is None:
                self._records[correlation] = {
                    "correlation": correlation,
                    "extracted_edge": extracted_edge,
                    "related_edges": tuple(related_edges),
                    "invalidation_edges": tuple(invalidation_edges),
                }
                if len(self._records) == self.expected_call_count:
                    self._complete.set()
            else:
                self._complete.set()

        try:
            await asyncio.wait_for(
                self._complete.wait(), timeout=self.timeout_seconds
            )
        except TimeoutError:
            async with self._lock:
                if self._failure is None:
                    self._failure = D2EvidenceIncomplete(
                        "edge call barrier timed out before complete coverage"
                    )
                self._complete.set()

        if self._failure is not None:
            raise self._failure
        if len(self._records) != self.expected_call_count:
            raise D2EvidenceIncomplete("edge call barrier coverage is incomplete")
        self.released_call_count += 1
        if self.released_call_count == self.expected_call_count:
            self._all_released.set()
        raise D2DiagnosticStop("source-7 candidate evidence collected")
