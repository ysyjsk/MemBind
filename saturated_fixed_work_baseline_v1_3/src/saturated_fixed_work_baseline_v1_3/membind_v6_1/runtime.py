"""Strict local-profile Graphiti runtime without changing frozen U0 code."""

from __future__ import annotations

import asyncio
import heapq
import hashlib
import inspect
import json
import os
import re
import time
import unicodedata
from copy import deepcopy
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Literal, Mapping
from urllib.parse import urlsplit, urlunsplit

from native_characterization_runtime import U0Config, U0Runtime


LOCAL_PROFILE_ID = "local-qwen3-14b-awq-v1"
LOCAL_LLM_BASE_URL = "http://127.0.0.1:18100/v1"
LOCAL_LLM_MODEL = "qwen3-14b-awq"
LOCAL_EMBEDDING_BASE_URL = "http://127.0.0.1:18101/v1"
LOCAL_EMBEDDING_MODEL = "qwen3-embedding-0.6b"
LOCAL_EMBEDDING_DIMENSION = 1024
LOCAL_CONTEXT_LIMIT = 65_536
LOCAL_MAX_COROUTINES = 8
LOCAL_HTTP_TIMEOUT_SECONDS = 3_600.0
LOCAL_SDK_MAX_RETRIES = 0
LOCAL_EXTRACTION_CHUNK_OUTPUT_TOKENS = 8_192
LOCAL_EXTRACTION_CHUNKING_POLICY = "dialogue_turn_partition_merge_v1"
LOCAL_EXTRACTION_CHUNK_TRIGGER_TOKENS = 28_000
LOCAL_EXTRACTION_CHUNK_CURRENT_CHARS = 3_000
LOCAL_EDGE_PAGE_CAPACITY = 1
LOCAL_EDGE_MAX_PAGES = 64
LOCAL_EDGE_PARTITION_CONCURRENCY = 1
LOCAL_EDGE_PHYSICAL_CONCURRENCY = 2
LOCAL_NODE_PARTITION_CONCURRENCY = 1
LOCAL_NODE_MAX_ENTITIES_PER_CHUNK = 64
LOCAL_NODE_MAX_NAME_CHARS = 256
_CONTEXT_ERROR_RE = re.compile(
    r"maximum context length is\s+(?P<context>\d+)\s+tokens.*?"
    r"prompt contains at least\s+(?P<input>\d+)\s+input tokens",
    re.IGNORECASE | re.DOTALL,
)


class LocalRuntimeConfigurationError(RuntimeError):
    """The activated process does not match the local experiment profile."""


class _EdgePagePriorityGate:
    """Capacity gate with bounded source priority and cancellation-safe grants."""

    def __init__(self, capacity: int, *, priority_burst: int | None = None) -> None:
        if capacity < 1:
            raise ValueError("edge page gate capacity must be positive")
        selected_burst = None if priority_burst is None else int(priority_burst)
        if selected_burst is not None and selected_burst < 1:
            raise ValueError("edge page priority burst must be positive")
        self.capacity = int(capacity)
        self.priority_burst = selected_burst
        self.available = int(capacity)
        self._next_ticket = 0
        self._waiters: list[tuple[int, int, asyncio.Future[int]]] = []
        self._drain_scheduled = False
        self._last_granted_source: int | None = None
        self._consecutive_source_grants = 0
        self._grant_evidence: dict[int, dict[str, Any]] = {}

    def _schedule_drain(self) -> None:
        if self._drain_scheduled:
            return
        self._drain_scheduled = True
        asyncio.get_running_loop().call_soon(self._drain)

    def _drain(self) -> None:
        self._drain_scheduled = False
        while self.available and self._waiters:
            if any(future.cancelled() for _, _, future in self._waiters):
                self._waiters = [
                    row for row in self._waiters if not row[2].cancelled()
                ]
                heapq.heapify(self._waiters)
                if not self._waiters:
                    break
            preferred_source = min(row[0] for row in self._waiters)
            preferred_grants = (
                self._consecutive_source_grants
                if self._last_granted_source == preferred_source
                else 0
            )
            alternate_indexes = [
                index
                for index, (source, _ticket, _future) in enumerate(self._waiters)
                if source != preferred_source
            ]
            bounded_aging = bool(
                self.priority_burst is not None
                and alternate_indexes
                and preferred_grants >= self.priority_burst
            )
            if bounded_aging:
                selected_index = min(
                    alternate_indexes,
                    key=lambda index: (
                        self._waiters[index][1],
                        self._waiters[index][0],
                    ),
                )
                selected = self._waiters[selected_index]
                last = self._waiters.pop()
                if selected_index < len(self._waiters):
                    self._waiters[selected_index] = last
                    heapq.heapify(self._waiters)
                source_sequence, ticket, future = selected
            else:
                source_sequence, ticket, future = heapq.heappop(self._waiters)
            pending_sources = sorted(
                {source for source, _ticket, waiter in self._waiters if not waiter.cancelled()}
            )
            self.available -= 1
            if self._last_granted_source == source_sequence:
                self._consecutive_source_grants += 1
            else:
                self._last_granted_source = source_sequence
                self._consecutive_source_grants = 1
            self._grant_evidence[ticket] = {
                "admission_reason": (
                    "bounded_waiter_aging" if bounded_aging else "source_priority"
                ),
                "selected_source_sequence": source_sequence,
                "preferred_source_sequence": preferred_source,
                "preferred_consecutive_grants_before": preferred_grants,
                "selected_consecutive_grants_after": self._consecutive_source_grants,
                "priority_burst_limit": self.priority_burst,
                "pending_source_sequences_after": pending_sources,
            }
            future.set_result(ticket)

    async def acquire(self, source_sequence: int) -> int:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[int] = loop.create_future()
        ticket = self._next_ticket
        self._next_ticket += 1
        heapq.heappush(self._waiters, (int(source_sequence), ticket, future))
        self._schedule_drain()
        try:
            return await future
        except BaseException:
            if future.done() and not future.cancelled():
                self.release()
            else:
                future.cancel()
            raise

    def release(self) -> None:
        self.available += 1
        if self.available > self.capacity:
            self.available -= 1
            raise RuntimeError("edge page gate released beyond capacity")
        self._schedule_drain()

    def grant_evidence(self, ticket: int) -> dict[str, Any]:
        try:
            return dict(self._grant_evidence[int(ticket)])
        except KeyError as exc:
            raise RuntimeError("edge page grant evidence is unavailable") from exc


class _AdaptiveEdgePagePriorityGate:
    """Priority gate with topology-derived capacity and congestion feedback.

    The gate starts at the existing partition-worker width and may use the
    already authenticated work-conserving ceiling when queueing dominates.
    Repeated service dilation shrinks the target again, preventing a larger
    page fan-out from turning GPU contention into longer critical paths.
    """

    def __init__(
        self,
        max_capacity: int,
        *,
        initial_capacity: int,
        priority_burst: int | None = None,
    ) -> None:
        if max_capacity < 1:
            raise ValueError("adaptive edge page gate capacity must be positive")
        if initial_capacity < 1 or initial_capacity > max_capacity:
            raise ValueError("adaptive edge page gate initial capacity is invalid")
        selected_burst = None if priority_burst is None else int(priority_burst)
        if selected_burst is not None and selected_burst < 1:
            raise ValueError("edge page priority burst must be positive")
        self.capacity = int(max_capacity)
        self.initial_capacity = int(initial_capacity)
        self.target_capacity = int(initial_capacity)
        self.priority_burst = selected_burst
        self.available = int(initial_capacity)
        self._active = 0
        self._next_ticket = 0
        self._waiters: list[tuple[int, int, asyncio.Future[int]]] = []
        self._drain_scheduled = False
        self._last_granted_source: int | None = None
        self._consecutive_source_grants = 0
        self._grant_evidence: dict[int, dict[str, Any]] = {}
        self._service_ewma_ns: int | None = None
        self._congestion_streak = 0
        self._observations = 0

    def _schedule_drain(self) -> None:
        if self._drain_scheduled:
            return
        self._drain_scheduled = True
        asyncio.get_running_loop().call_soon(self._drain)

    def _drain(self) -> None:
        self._drain_scheduled = False
        while self._active < self.target_capacity and self._waiters:
            if any(future.cancelled() for _, _, future in self._waiters):
                self._waiters = [
                    row for row in self._waiters if not row[2].cancelled()
                ]
                heapq.heapify(self._waiters)
                if not self._waiters:
                    break
            preferred_source = min(row[0] for row in self._waiters)
            preferred_grants = (
                self._consecutive_source_grants
                if self._last_granted_source == preferred_source
                else 0
            )
            alternate_indexes = [
                index
                for index, (source, _ticket, _future) in enumerate(self._waiters)
                if source != preferred_source
            ]
            bounded_aging = bool(
                self.priority_burst is not None
                and alternate_indexes
                and preferred_grants >= self.priority_burst
            )
            if bounded_aging:
                selected_index = min(
                    alternate_indexes,
                    key=lambda index: (
                        self._waiters[index][1],
                        self._waiters[index][0],
                    ),
                )
                selected = self._waiters[selected_index]
                last = self._waiters.pop()
                if selected_index < len(self._waiters):
                    self._waiters[selected_index] = last
                    heapq.heapify(self._waiters)
                source_sequence, ticket, future = selected
            else:
                source_sequence, ticket, future = heapq.heappop(self._waiters)
            pending_sources = sorted(
                {source for source, _ticket, waiter in self._waiters if not waiter.cancelled()}
            )
            self._active += 1
            self.available = self.target_capacity - self._active
            if self._last_granted_source == source_sequence:
                self._consecutive_source_grants += 1
            else:
                self._last_granted_source = source_sequence
                self._consecutive_source_grants = 1
            self._grant_evidence[ticket] = {
                "admission_reason": (
                    "bounded_waiter_aging" if bounded_aging else "source_priority"
                ),
                "selected_source_sequence": source_sequence,
                "preferred_source_sequence": preferred_source,
                "preferred_consecutive_grants_before": preferred_grants,
                "selected_consecutive_grants_after": self._consecutive_source_grants,
                "priority_burst_limit": self.priority_burst,
                "pending_source_sequences_after": pending_sources,
                "adaptive_target_capacity_at_grant": self.target_capacity,
                "adaptive_service_ewma_ns_at_grant": self._service_ewma_ns,
            }
            future.set_result(ticket)

    async def acquire(self, source_sequence: int) -> int:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[int] = loop.create_future()
        ticket = self._next_ticket
        self._next_ticket += 1
        heapq.heappush(self._waiters, (int(source_sequence), ticket, future))
        self._schedule_drain()
        try:
            return await future
        except BaseException:
            if future.done() and not future.cancelled():
                self.release()
            else:
                future.cancel()
            raise

    def release(
        self,
        *,
        queue_wait_ns: int | None = None,
        service_ns: int | None = None,
    ) -> None:
        if self._active <= 0:
            raise RuntimeError("adaptive edge page gate released without an active grant")
        active_before = self._active
        self._active -= 1
        if service_ns is not None and int(service_ns) > 0:
            observed = int(service_ns)
            self._observations += 1
            previous = self._service_ewma_ns
            if previous is not None:
                congested = (
                    active_before >= self.target_capacity
                    and observed > (previous * 5) // 4
                )
                self._congestion_streak = self._congestion_streak + 1 if congested else 0
                if self._congestion_streak >= 2 and self.target_capacity > self.initial_capacity:
                    self.target_capacity -= 1
                    self._congestion_streak = 0
                elif (
                    queue_wait_ns is not None
                    and int(queue_wait_ns) * 2 > observed
                    and self.target_capacity < self.capacity
                ):
                    self.target_capacity += 1
                    self._congestion_streak = 0
            elif (
                queue_wait_ns is not None
                and int(queue_wait_ns) * 2 > observed
                and self.target_capacity < self.capacity
            ):
                self.target_capacity += 1
            self._service_ewma_ns = (
                observed
                if previous is None
                else (previous + observed) // 2
            )
        self.available = self.target_capacity - self._active
        self._schedule_drain()

    def grant_evidence(self, ticket: int) -> dict[str, Any]:
        try:
            return dict(self._grant_evidence[int(ticket)])
        except KeyError as exc:
            raise RuntimeError("adaptive edge page grant evidence is unavailable") from exc

    def state(self) -> dict[str, int | None]:
        return {
            "capacity": self.capacity,
            "initial_capacity": self.initial_capacity,
            "target_capacity": self.target_capacity,
            "active": self._active,
            "available": self.available,
            "observations": self._observations,
            "service_ewma_ns": self._service_ewma_ns,
            "congestion_streak": self._congestion_streak,
        }


def _context_retry_max_tokens(
    error: BaseException,
    requested: int,
    *,
    safety_margin: int,
) -> int | None:
    match = _CONTEXT_ERROR_RE.search(str(error))
    if match is None:
        return None
    remaining = int(match["context"]) - int(match["input"]) - int(safety_margin)
    effective = min(int(requested) - 1, remaining)
    return effective if effective > 0 else None


def _chat_message_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        row = dict(value)
    else:
        model_dump = getattr(value, "model_dump", None)
        row = dict(model_dump(mode="python")) if callable(model_dump) else {
            "role": getattr(value, "role", None),
            "content": getattr(value, "content", None),
        }
    if not isinstance(row.get("role"), str) or not isinstance(row.get("content"), str):
        raise LocalRuntimeConfigurationError("local token counter requires text chat messages")
    return {"role": row["role"], "content": row["content"]}


@lru_cache(maxsize=1)
def _local_chat_tokenizer() -> Any:
    from transformers import AutoTokenizer

    model_dir = Path(_required("MEMBIND_LLM_MODEL_DIR")).resolve()
    if not model_dir.is_dir():
        raise LocalRuntimeConfigurationError("MEMBIND_LLM_MODEL_DIR is unavailable")
    return AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)


def _local_prompt_tokens(messages: Sequence[Any]) -> int:
    normalized = [_chat_message_dict(value) for value in messages]
    token_ids = _local_chat_tokenizer().apply_chat_template(
        normalized,
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=False,
        return_dict=False,
    )
    return len(token_ids)


def local_prompt_token_count(messages: Sequence[Any]) -> int:
    """Public exact prompt counter used by the V6.1 weighted admission path."""

    return _local_prompt_tokens(messages)


def install_local_context_budget_adapter(
    llm_client: Any,
    *,
    token_counter: Callable[[Sequence[Any]], int] | None = None,
) -> Callable[[], None]:
    """Fit each wire completion to the remaining locally-tokenized context."""

    completions = getattr(
        getattr(getattr(llm_client, "client", None), "chat", None),
        "completions",
        None,
    )
    original_create = getattr(completions, "create", None)
    if completions is None or not callable(original_create):
        raise LocalRuntimeConfigurationError("local Graphiti LLM transport seam is unavailable")
    safety_margin = int(os.environ.get("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32"))
    count_tokens = token_counter or _local_prompt_tokens
    if token_counter is None:
        _local_chat_tokenizer()

    async def create_with_effective_budget(*args: Any, **kwargs: Any) -> Any:
        request_kwargs = dict(kwargs)
        requested = int(request_kwargs.get("max_tokens") or getattr(llm_client, "max_tokens", 0))
        if requested <= 0:
            return await original_create(*args, **request_kwargs)
        messages = request_kwargs.get("messages")
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
            raise LocalRuntimeConfigurationError("local chat request is missing messages")
        input_tokens = int(count_tokens(messages))
        available = LOCAL_CONTEXT_LIMIT - input_tokens - safety_margin
        if available <= 0:
            raise LocalRuntimeConfigurationError("local chat prompt leaves no completion budget")
        requested = min(requested, available)
        request_kwargs["max_tokens"] = requested
        for _retry in range(6):
            try:
                return await original_create(*args, **request_kwargs)
            except Exception as exc:
                effective = _context_retry_max_tokens(
                    exc,
                    requested,
                    safety_margin=safety_margin,
                )
                if effective is None or effective >= requested:
                    raise
                requested = effective
                request_kwargs["max_tokens"] = effective
                # The admission guard wraps this adapter. Account for this
                # inner wire retry in the owning provider call's counter.
                from .provider import _record_managed_transport_retry

                _record_managed_transport_retry()
        return await original_create(*args, **request_kwargs)

    setattr(completions, "create", create_with_effective_budget)

    def restore() -> None:
        setattr(completions, "create", original_create)

    return restore


def _normalized_url(value: str) -> str:
    parsed = urlsplit(value.rstrip("/"))
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path.rstrip("/"), "", ""))


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise LocalRuntimeConfigurationError(f"{name} is required")
    return value


def _expect(name: str, expected: str, *, normalize_url: bool = False) -> str:
    value = _required(name)
    observed = _normalized_url(value) if normalize_url else value
    target = _normalized_url(expected) if normalize_url else expected
    if observed != target:
        raise LocalRuntimeConfigurationError(f"{name} does not match {LOCAL_PROFILE_ID}")
    return value


def _integer(name: str, expected: int) -> int:
    raw = _required(name)
    try:
        value = int(raw)
    except ValueError as exc:
        raise LocalRuntimeConfigurationError(f"{name} must be an integer") from exc
    if value != expected:
        raise LocalRuntimeConfigurationError(f"{name} does not match {LOCAL_PROFILE_ID}")
    return value


def _float(name: str, expected: float) -> float:
    raw = _required(name)
    try:
        value = float(raw)
    except ValueError as exc:
        raise LocalRuntimeConfigurationError(f"{name} must be numeric") from exc
    if value != expected:
        raise LocalRuntimeConfigurationError(f"{name} does not match {LOCAL_PROFILE_ID}")
    return value


def build_local_openai_transport(
    *,
    api_key: str,
    base_url: str,
    timeout_seconds: float = LOCAL_HTTP_TIMEOUT_SECONDS,
    max_retries: int = LOCAL_SDK_MAX_RETRIES,
) -> Any:
    """Build the fixed single-attempt transport used by local construction calls."""

    import httpx2
    from openai import AsyncOpenAI

    if timeout_seconds <= 0:
        raise LocalRuntimeConfigurationError("local HTTP timeout must be positive")
    if max_retries != LOCAL_SDK_MAX_RETRIES:
        raise LocalRuntimeConfigurationError("local construction SDK retries must be disabled")
    # One explicit pool is shared by Graphiti construction and reranking.  The
    # default five-second keep-alive expiry repeatedly retired localhost
    # connections during long generations and exposed suffix fan-out to
    # avoidable reconnect/reset failures.
    http_client = httpx2.AsyncClient(
        timeout=httpx2.Timeout(float(timeout_seconds)),
        limits=httpx2.Limits(
            max_connections=LOCAL_MAX_COROUTINES,
            max_keepalive_connections=LOCAL_MAX_COROUTINES,
            keepalive_expiry=60.0,
        ),
        follow_redirects=False,
        trust_env=False,
    )
    return AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=float(timeout_seconds),
        max_retries=int(max_retries),
        http_client=http_client,
    )


def install_local_single_attempt_policy(llm_client: Any) -> None:
    """Disable Graphiti's retry decorator for one local client instance.

    This is installed on the constructed instance rather than changing the shared
    ``graphiti_native`` implementation used by frozen 32B experiments.
    """

    original = getattr(llm_client, "_generate_response", None)
    if not callable(original):
        raise LocalRuntimeConfigurationError("local Graphiti retry seam is unavailable")

    async def single_attempt(*args: Any, **kwargs: Any) -> Any:
        return await original(*args, **kwargs)

    setattr(llm_client, "_generate_response_with_retry", single_attempt)


_CURRENT_MESSAGE_PATTERNS = (
    re.compile(r"(<CURRENT MESSAGE>)(.*?)(</CURRENT MESSAGE>)", re.IGNORECASE | re.DOTALL),
    re.compile(r"(<CURRENT_MESSAGE>)(.*?)(</CURRENT_MESSAGE>)", re.IGNORECASE | re.DOTALL),
)
_TURN_MARKER_RE = re.compile(r"(?m)^\[(?:USER|ASSISTANT)\]\s*$")
_ENTITIES_BLOCK_PATTERN = re.compile(
    r"(<ENTITIES>)(?P<body>.*?)(</ENTITIES>)", re.IGNORECASE | re.DOTALL
)


def _message_content(value: Any) -> str | None:
    if isinstance(value, Mapping):
        content = value.get("content")
    else:
        content = getattr(value, "content", None)
    return content if isinstance(content, str) else None


def _replace_current_message(messages: Sequence[Any], replacement: str) -> list[Any] | None:
    cloned = deepcopy(list(messages))
    for message in cloned:
        content = _message_content(message)
        if content is None:
            continue
        for pattern in _CURRENT_MESSAGE_PATTERNS:
            match = pattern.search(content)
            if match is None:
                continue
            updated = content[: match.start(2)] + replacement + content[match.end(2) :]
            if isinstance(message, Mapping):
                message["content"] = updated
            else:
                setattr(message, "content", updated)
            return cloned
    return None


def _entity_block(messages: Sequence[Any]) -> tuple[int, re.Match[str], list[dict[str, Any]]] | None:
    """Return the structured Graphiti entity block without retaining prompt text."""

    for index, message in enumerate(messages):
        content = _message_content(message)
        if content is None:
            continue
        match = _ENTITIES_BLOCK_PATTERN.search(content)
        if match is None:
            continue
        try:
            values = json.loads(match.group("body"))
        except json.JSONDecodeError:
            return None
        if not isinstance(values, list) or not all(isinstance(value, Mapping) for value in values):
            return None
        return index, match, [dict(value) for value in values]
    return None


def _replace_entity_block(
    messages: Sequence[Any],
    *,
    entity_values: Sequence[Mapping[str, Any]],
    scope_instruction: str | None = None,
) -> list[Any] | None:
    """Clone messages and replace only the entity JSON consumed by Graphiti."""

    cloned = deepcopy(list(messages))
    for index, message in enumerate(cloned):
        content = _message_content(message)
        if content is None:
            continue
        match = _ENTITIES_BLOCK_PATTERN.search(content)
        if match is None:
            continue
        body = json.dumps(list(entity_values), ensure_ascii=False, indent=2)
        updated = content[: match.start("body")] + "\n" + body + "\n" + content[match.end("body") :]
        if scope_instruction:
            marker = re.search(r"(?m)^# TASK\s*$", updated)
            insertion = (
                "\n\n<EDGE_PARTITION_SCOPE>\n"
                f"{scope_instruction}\n"
                "This partition is semantically closed: do not emit an edge whose source "
                "or target is outside the ENTITIES list above.\n"
                "</EDGE_PARTITION_SCOPE>\n"
            )
            if marker is not None:
                updated = updated[: marker.start()] + insertion + updated[marker.start() :]
            else:
                updated += insertion
        if isinstance(message, Mapping):
            message["content"] = updated
        else:
            setattr(message, "content", updated)
        return cloned
    return None


def _distinct_entity_values(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep one stable entity object per normalized name for pair expansion."""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        name = " ".join(str(value.get("name", "")).split())
        identity = name.casefold()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(dict(value))
    return result


def _edge_pair_partitions(
    messages: Sequence[Any],
) -> tuple[list[list[Any]], int, int] | None:
    """Expand an edge prompt into a complete, pairwise candidate cover.

    Every unordered pair of distinct entities appears in exactly one physical
    request.  Since Graphiti edges are binary relations, this is the smallest
    generally valid semantic partition that cannot hide cross-partition facts.
    """

    block = _entity_block(messages)
    if block is None:
        return None
    _, _, values = block
    entities = _distinct_entity_values(values)
    if len(entities) <= 2:
        return None
    partitions: list[list[Any]] = []
    for left in range(len(entities)):
        for right in range(left + 1, len(entities)):
            pair = (entities[left], entities[right])
            names = [str(value.get("name", "")) for value in pair]
            partition = _replace_entity_block(
                messages,
                entity_values=pair,
                scope_instruction=(
                    "Evaluate only relationships between the two candidate entities "
                    f"{json.dumps(names, ensure_ascii=False)}."
                ),
            )
            if partition is None:
                return None
            partitions.append(partition)
    return partitions, len(entities), len(partitions)


def _current_message_text(messages: Sequence[Any]) -> str | None:
    for message in messages:
        content = _message_content(message)
        if content is None:
            continue
        for pattern in _CURRENT_MESSAGE_PATTERNS:
            match = pattern.search(content)
            if match is not None:
                return match.group(2)
    return None


def _edge_turn_local_partitions(
    messages: Sequence[Any],
    *,
    entity_partition_hints: Mapping[str, Sequence[int]] | None = None,
    entity_partition_sources: Mapping[int, str] | None = None,
    partition_metadata: list[dict[str, Any]] | None = None,
    actor_domain_cover: bool = False,
    actor_domain_adjacent_domain: bool = True,
    actor_domain_boundary_join: bool = False,
) -> tuple[list[list[Any]], int, int, int] | None:
    """Build evidence-local edge candidate sets from the node extraction cover.

    Node extraction already partitions the dialogue into complete USER/ASSISTANT
    turns.  Reusing that provenance keeps relations local to the text that can
    support them, while a one-turn overlap preserves conversational continuity.
    No facts are filtered from a model response; the final merge remains the
    original Graphiti edge schema.
    """

    block = _entity_block(messages)
    if block is None:
        return None
    _, _, values = block
    entities = _distinct_entity_values(values)
    if len(entities) <= 2:
        return None
    by_identity = {" ".join(str(value.get("name", "")).split()).casefold(): value for value in entities}
    groups: dict[int, set[str]] = {}
    hints = entity_partition_hints or {}
    for identity, value in by_identity.items():
        partition_ids = hints.get(identity, ())
        assigned = False
        for partition_id in partition_ids:
            try:
                normalized_partition = int(partition_id)
            except (TypeError, ValueError):
                continue
            groups.setdefault(normalized_partition, set()).add(identity)
            assigned = True
        if assigned:
            continue
        # Fallback for entities returned by a non-partitioned node request.
        text = _current_message_text(messages) or ""
        segments = _turn_segments(text)
        for partition_id, segment in enumerate(segments):
            if identity in segment.casefold():
                groups.setdefault(partition_id, set()).add(identity)
                assigned = True
        if not assigned:
            groups.setdefault(-1, set()).add(identity)

    source_text_by_id = {
        int(partition_id): str(source_text)
        for partition_id, source_text in (entity_partition_sources or {}).items()
        if isinstance(partition_id, int) and isinstance(source_text, str)
    }
    if not source_text_by_id:
        # Direct provider-free fixtures do not execute the preceding node call.
        # Reconstruct the same deterministic turn cover without permitting this
        # fallback inside a live provider scope (enforced by the caller).
        full_text = _current_message_text(messages) or ""
        source_text_by_id = {
            partition_id: source_text
            for partition_id, source_text in enumerate(_turn_segments(full_text))
        }

    ordered_ids = sorted(index for index in groups if groups[index])
    if not ordered_ids:
        return None
    missing_source_ids = [index for index in ordered_ids if index not in source_text_by_id]
    if missing_source_ids:
        raise LocalRuntimeConfigurationError(
            "edge entity provenance has no matching source text for partitions "
            + json.dumps(missing_source_ids)
        )

    base_groups = [(index, set(groups[index])) for index in ordered_ids]
    # Adjacent-turn windows cover facts expressed across a speaker response
    # boundary without creating the O(n^2) pairwise expansion rejected by the
    # live 42-entity diagnostic.
    candidate_groups: list[
        tuple[tuple[int, ...], set[str], str, str, set[str], set[str]]
    ] = []

    def append_views(source_ids: tuple[int, ...], identities: set[str]) -> None:
        source_text = "".join(source_text_by_id[source_id] for source_id in source_ids)
        if not actor_domain_cover:
            if len(identities) >= 2:
                candidate_groups.append(
                    (source_ids, identities, source_text, "entity_cover", set(), set())
                )
            return

        user_text = "".join(
            segment
            for segment in _turn_segments(source_text)
            if segment.lstrip().upper().startswith("[USER]")
        )
        normalized_user_text = " ".join(user_text.split()).casefold()
        normalized_source_text = " ".join(source_text.split()).casefold()
        user_identities = {
            identity
            for identity in identities
            if identity == "user" or identity in normalized_user_text
        }
        if "user" in by_identity and len(user_identities) >= 2:
            user_identities.add("user")
            candidate_groups.append(
                (source_ids, user_identities, user_text, "user_state", set(), set())
            )

        if len(source_ids) == 1 or actor_domain_adjacent_domain:
            domain_identities = {
                identity
                for identity in identities - {"user", "assistant"}
                if identity in normalized_source_text
            }
            if len(domain_identities) >= 2:
                candidate_groups.append(
                    (source_ids, domain_identities, source_text, "domain", set(), set())
                )

    for source_id, group in base_groups:
        append_views((source_id,), group)
    for (left_id, left), (right_id, right) in zip(base_groups, base_groups[1:]):
        append_views((left_id, right_id), left | right)
        if actor_domain_cover and actor_domain_boundary_join:
            source_ids = (left_id, right_id)
            source_text = source_text_by_id[left_id] + source_text_by_id[right_id]
            normalized_source_text = " ".join(source_text.split()).casefold()
            left_domain = left - {"user", "assistant"}
            right_domain = right - {"user", "assistant"}
            boundary_identities = {
                identity
                for identity in left_domain | right_domain
                if identity in normalized_source_text
            }
            if len(boundary_identities) >= 2 and left_domain and right_domain:
                candidate_groups.append(
                    (
                        source_ids,
                        boundary_identities,
                        source_text,
                        "domain_boundary_join",
                        left_domain,
                        right_domain,
                    )
                )
    # A fallback group is retained as one semantic unit.  It is expected to be
    # small; if it is large, the diagnostics expose that fact before expansion.
    unique_groups: list[
        tuple[tuple[int, ...], list[dict[str, Any]], str, str, set[str], set[str]]
    ] = []
    seen_group_ids: set[tuple[tuple[int, ...], frozenset[str], str, str]] = set()
    for (
        source_ids,
        identities,
        source_text,
        view_kind,
        cross_left,
        cross_right,
    ) in candidate_groups:
        # The source window is part of the semantic identity.  Equal entity
        # sets grounded in different text must remain separate candidates.
        key = source_ids, frozenset(identities), hashlib.sha256(
            source_text.encode("utf-8")
        ).hexdigest(), view_kind
        if key in seen_group_ids:
            continue
        seen_group_ids.add(key)
        candidates = [
            by_identity[identity]
            for identity in by_identity
            if identity in identities
        ]
        unique_groups.append(
            (source_ids, candidates, source_text, view_kind, cross_left, cross_right)
        )
    if not unique_groups:
        return None
    partitions: list[list[Any]] = []
    for source_ids, candidate, source_text, view_kind, cross_left, cross_right in unique_groups:
        scope_instruction = (
            "Evaluate only cross-boundary relationships with at least one endpoint "
            "from each of the two adjacent source partitions. Relationships whose "
            "endpoints both originate from only one side are covered by base partitions."
            if view_kind == "domain_boundary_join"
            else (
                "Evaluate only relationships among the candidate entities in this "
                "evidence-local partition. The partition is complete for its source text."
            )
        )
        partition = _replace_entity_block(
            messages,
            entity_values=candidate,
            scope_instruction=scope_instruction,
        )
        if partition is None:
            return None
        scoped_partition = _replace_current_message(partition, source_text)
        if scoped_partition is None:
            return None
        partitions.append(scoped_partition)
        if partition_metadata is not None:
            partition_metadata.append(
                {
                    "evidence_source_partition_ids": list(source_ids),
                    "evidence_view_kind": view_kind,
                    "cross_boundary_required": view_kind == "domain_boundary_join",
                    "_cross_left_endpoint_names": set(cross_left),
                    "_cross_right_endpoint_names": set(cross_right),
                    "evidence_source_hashes": [
                        hashlib.sha256(source_text_by_id[source_id].encode("utf-8")).hexdigest()
                        for source_id in source_ids
                    ],
                    "current_message_chars": len(source_text),
                }
            )
    return (
        partitions,
        len(entities),
        len(partitions),
        max(len(candidate) for _, candidate, _, _, _, _ in unique_groups),
    )


def _turn_segments(text: str) -> list[str]:
    markers = list(_TURN_MARKER_RE.finditer(text))
    if not markers:
        return [text]
    segments: list[str] = []
    if markers[0].start() > 0:
        segments.append(text[: markers[0].start()])
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        segments.append(text[marker.start() : end])
    return [segment for segment in segments if segment.strip()]


def _partition_current_message(
    messages: Sequence[Any],
    *,
    prompt_limit: int,
    token_counter: Callable[[Sequence[Any]], int],
    current_char_limit: int = LOCAL_EXTRACTION_CHUNK_CURRENT_CHARS,
) -> list[list[Any]]:
    current_text: str | None = None
    for message in messages:
        content = _message_content(message)
        if content is None:
            continue
        for pattern in _CURRENT_MESSAGE_PATTERNS:
            match = pattern.search(content)
            if match is not None:
                current_text = match.group(2)
                break
        if current_text is not None:
            break
    if current_text is None:
        return [list(messages)]

    segments = _turn_segments(current_text)
    if len(segments) <= 1:
        return [list(messages)]

    partitions: list[str] = []
    current = ""
    for segment in segments:
        candidate = current + segment
        candidate_messages = _replace_current_message(messages, candidate)
        if candidate_messages is None:
            return [list(messages)]
        if current and (
            len(candidate) > current_char_limit
            or token_counter(candidate_messages) > prompt_limit
        ):
            partitions.append(current)
            current = segment
        else:
            current = candidate
    if current:
        partitions.append(current)

    if len(partitions) <= 1:
        return [list(messages)]
    result: list[list[Any]] = []
    for partition in partitions:
        candidate_messages = _replace_current_message(messages, partition)
        if candidate_messages is not None:
            result.append(candidate_messages)
    return result or [list(messages)]


def _merge_extraction_responses(prompt_name: str | None, responses: Sequence[Any]) -> Any:
    key = "extracted_entities" if str(prompt_name).startswith("extract_nodes.") else "edges"
    if not responses or not all(isinstance(response, Mapping) for response in responses):
        return responses[0] if responses else {key: []}
    merged: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for response in responses:
        values = response.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, Mapping):
                continue
            item = dict(value)
            if key == "extracted_entities":
                identity = " ".join(str(item.get("name", "")).split()).casefold()
            else:
                identity = json.dumps(
                    {
                        field: item.get(field)
                        for field in (
                            "source_entity_name",
                            "target_entity_name",
                            "relation_type",
                            "fact",
                            "valid_at",
                            "invalid_at",
                        )
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            if not identity:
                continue
            existing_index = seen.get(identity)
            if existing_index is None:
                item["episode_indices"] = [0]
                seen[identity] = len(merged)
                merged.append(item)
                continue
            existing = merged[existing_index]
            indices = sorted(
                {
                    *[int(index) for index in existing.get("episode_indices", []) if isinstance(index, int)],
                    *[int(index) for index in item.get("episode_indices", []) if isinstance(index, int)],
                    0,
                }
            )
            existing["episode_indices"] = indices
    return {key: merged}


def _edge_identity(value: Mapping[str, Any]) -> str:
    return json.dumps(
        {
            field: value.get(field)
            for field in (
                "source_entity_name",
                "target_entity_name",
                "relation_type",
                "fact",
                "valid_at",
                "invalid_at",
            )
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@lru_cache(maxsize=4)
def _bounded_edge_page_model(page_capacity: int = LOCAL_EDGE_PAGE_CAPACITY) -> Any:
    """Return a bounded delta page that remains valid under ExtractedEdges."""

    from pydantic import Field, create_model
    from graphiti_core.prompts.extract_edges import Edge

    if page_capacity < 1:
        raise ValueError("edge page capacity must be positive")
    model_name = (
        "MemBindSingleEdgePage"
        if page_capacity == 1
        else f"MemBindBoundedEdgePage{page_capacity}"
    )
    return create_model(
        model_name,
        edges=(list[Edge], Field(default_factory=list, max_length=page_capacity)),
    )


@lru_cache(maxsize=64)
def _endpoint_grounded_edge_page_model(
    page_capacity: int,
    endpoint_names: tuple[str, ...],
) -> Any:
    """Constrain edge endpoints to the entities in the current evidence block.

    The ordinary ``Edge`` schema leaves both endpoint fields unconstrained.
    Graphiti then spends additional fixed-point pages generating candidates that
    are rejected by the deterministic endpoint predicate.  For V6.1's
    partitioned extraction, the entity block is already authoritative; using
    it as a structured-output enum removes only impossible candidates while
    preserving the full relation/fact surface and pagination protocol.
    """

    names = tuple(dict.fromkeys(str(name) for name in endpoint_names if str(name)))
    if page_capacity < 1:
        raise ValueError("edge page capacity must be positive")
    if not names:
        return _bounded_edge_page_model(page_capacity)
    from pydantic import Field, create_model
    from graphiti_core.prompts.extract_edges import Edge

    endpoint_type = Literal.__getitem__(names)
    edge_model = create_model(
        f"MemBindEndpointGroundedEdge{page_capacity}_{len(names)}",
        __base__=Edge,
        source_entity_name=(
            endpoint_type,
            Field(..., description="The name of the source entity from the ENTITIES list"),
        ),
        target_entity_name=(
            endpoint_type,
            Field(..., description="The name of the target entity from the ENTITIES list"),
        ),
    )
    return create_model(
        f"MemBindEndpointGroundedEdgePage{page_capacity}_{len(names)}",
        edges=(list[edge_model], Field(default_factory=list, max_length=page_capacity)),
    )


def _edge_page_messages(
    messages: Sequence[Any],
    previous_edges: Sequence[Mapping[str, Any]],
    *,
    page_capacity: int = LOCAL_EDGE_PAGE_CAPACITY,
    duplicate_recovery_edge: Mapping[str, Any] | None = None,
    memory_utility_order: bool = False,
) -> list[Any]:
    """Add continuation state to one edge request without artifact leakage."""

    cloned = deepcopy(list(messages))
    already_returned = [
        {
            field: edge.get(field)
            for field in (
                "source_entity_name",
                "target_entity_name",
                "relation_type",
                "fact",
                "valid_at",
                "invalid_at",
            )
        }
        for edge in previous_edges
    ]
    recovery_instruction = ""
    if duplicate_recovery_edge is not None:
        rejected = {
            field: duplicate_recovery_edge.get(field)
            for field in (
                "source_entity_name",
                "target_entity_name",
                "relation_type",
                "fact",
                "valid_at",
                "invalid_at",
            )
        }
        recovery_instruction = (
            "<DUPLICATE_RECOVERY>\n"
            "The previous response repeated this already-returned edge:\n"
            + json.dumps(rejected, ensure_ascii=False, sort_keys=True)
            + "\nThat repeat is not evidence that extraction is complete. Scan the ENTITIES "
            "in listed order and return the first different supported factual edge whose "
            "full tuple is absent from ALREADY_RETURNED_EDGES. Return {\"edges\": []} "
            "only if no such edge exists.\n"
            "</DUPLICATE_RECOVERY>\n"
        )
    page_instruction = (
        "Return exactly one not-yet-returned factual edge supported by CURRENT_MESSAGE. "
        if page_capacity == 1
        else (
            f"Return up to {page_capacity} distinct not-yet-returned factual edges "
            "supported by CURRENT_MESSAGE. "
        )
    )
    utility_instruction = ""
    if memory_utility_order:
        utility_instruction = (
            "<MEMORY_UTILITY_ORDER>\n"
            "Choose supported edges in this deterministic order without discarding any "
            "supported fact:\n"
            "1. Explicit USER state: decisions, plans, preferences, possessions, balances, "
            "quantities, dates, and experienced events. Preserve exact values and named "
            "entities.\n"
            "2. Concrete relationships between named domain entities in the source text, "
            "including location, service, membership, capability, and comparison facts.\n"
            "3. Generic conversational acts such as ASSISTANT MENTIONS, PROVIDES, or "
            "RECOMMENDS.\n"
            "Within a tier, follow CURRENT_MESSAGE order and then ENTITIES order. Do not "
            "convert an option or recommendation from ASSISTANT into a USER decision, "
            "plan, possession, or action.\n"
            "</MEMORY_UTILITY_ORDER>\n"
        )
    instruction = (
        "\n\n<EDGE_PAGINATION>\n"
        + page_instruction
        + "Return {\"edges\": []} only when no additional supported edge remains. "
        "Never repeat or paraphrase an already returned edge.\n"
        + recovery_instruction
        + utility_instruction
        + "<ALREADY_RETURNED_EDGES>\n"
        + json.dumps(already_returned, ensure_ascii=False, sort_keys=True)
        + "\n</ALREADY_RETURNED_EDGES>\n"
        "</EDGE_PAGINATION>\n"
    )
    for message in cloned:
        content = _message_content(message)
        if content is None:
            continue
        if "<ENTITIES>" not in content.upper():
            continue
        marker = re.search(r"(?m)^# TASK\s*$", content)
        updated = (
            content[: marker.start()] + instruction + content[marker.start() :]
            if marker is not None
            else content + instruction
        )
        if isinstance(message, Mapping):
            message["content"] = updated
        else:
            setattr(message, "content", updated)
        return cloned
    raise LocalRuntimeConfigurationError("edge pagination prompt seam is unavailable")


def _provider_scope_key() -> tuple[str | None, int | None]:
    """Resolve the active episode scope without imposing it on unit fixtures."""

    try:
        from ..membind_v5.runtime.core.provider_admission import current_provider_scope

        region, source_sequence = current_provider_scope()
    except Exception:
        return None, None
    return region, None if source_sequence is None else int(source_sequence)


@lru_cache(maxsize=32)
def _bounded_summary_response_model(max_items: int) -> Any:
    """Mirror Graphiti's summary contract in the structured-output schema."""

    from pydantic import Field, create_model
    from graphiti_core.utils.text_utils import MAX_SUMMARY_CHARS

    if max_items < 1:
        raise ValueError("summary response capacity must be positive")
    item_model = create_model(
        "MemBindBoundedSummarizedEntity",
        name=(str, Field(..., description="Name of the entity being summarized")),
        summary=(
            str,
            Field(
                ...,
                max_length=MAX_SUMMARY_CHARS,
                description="Updated summary for the entity",
            ),
        ),
    )
    return create_model(
        f"MemBindBoundedSummarizedEntities{max_items}",
        summaries=(
            list[item_model],
            Field(
                ...,
                max_length=max_items,
                description=(
                    "List of entity summaries. Only include entities that need summary updates."
                ),
            ),
        ),
    )


@lru_cache(maxsize=64)
def _bounded_node_response_model(max_items: int) -> Any:
    """Bound node extraction to what one evidence chunk can represent."""

    from pydantic import Field, create_model

    if max_items < 1 or max_items > LOCAL_NODE_MAX_ENTITIES_PER_CHUNK:
        raise ValueError("node response capacity is outside the local contract")
    item_model = create_model(
        "MemBindBoundedExtractedEntity",
        name=(
            str,
            Field(
                ...,
                min_length=1,
                max_length=LOCAL_NODE_MAX_NAME_CHARS,
                description="Name of the extracted entity",
            ),
        ),
        entity_type_id=(
            int,
            Field(
                ...,
                description=(
                    "ID of the classified entity type. Must be one of the provided "
                    "entity_type_id integers."
                ),
            ),
        ),
        episode_indices=(
            list[int],
            Field(
                default_factory=lambda: [0],
                max_length=1,
                description="The single episode index for this extraction request",
            ),
        ),
    )
    return create_model(
        f"MemBindBoundedExtractedEntities{max_items}",
        extracted_entities=(
            list[item_model],
            Field(
                ...,
                max_length=max_items,
                description="Distinct entities supported by the current evidence chunk",
            ),
        ),
    )


def _node_schema_capacity(messages: Sequence[Any]) -> int:
    source_text = _current_message_text(messages) or ""
    lexical_tokens = re.findall(r"(?u)\b\w[\w'-]*\b", source_text)
    return max(1, min(LOCAL_NODE_MAX_ENTITIES_PER_CHUNK, len(lexical_tokens)))


def _node_grounding_tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return tuple(re.findall(r"(?u)\w+", normalized))


def _contains_token_sequence(source: Sequence[str], candidate: Sequence[str]) -> bool:
    if not candidate or len(candidate) > len(source):
        return False
    width = len(candidate)
    candidate_tuple = tuple(candidate)
    return any(
        tuple(source[index : index + width]) == candidate_tuple
        for index in range(len(source) - width + 1)
    )


def _audit_node_response(
    messages: Sequence[Any],
    result: Any,
    *,
    prompt_name: str | None,
    schema_max_items: int | None,
) -> tuple[Any, dict[str, Any] | None]:
    """Keep only entities with normalized lexical evidence in this source partition."""

    if prompt_name not in {
        "extract_nodes.extract_message",
        "extract_nodes.extract_text",
        "extract_nodes.extract_json",
    } or not isinstance(result, Mapping):
        return result, None
    entities = result.get("extracted_entities")
    source_text = _current_message_text(messages)
    if not isinstance(entities, list) or source_text is None:
        return result, None

    source_tokens = _node_grounding_tokens(source_text)
    accepted: list[dict[str, Any]] = []
    accepted_names: set[str] = set()
    ungrounded_count = 0
    duplicate_count = 0
    malformed_count = 0
    for entity in entities:
        if not isinstance(entity, Mapping):
            malformed_count += 1
            continue
        name = str(entity.get("name", "")).strip()
        identity = " ".join(name.split()).casefold()
        name_tokens = _node_grounding_tokens(name)
        if not identity or not name_tokens:
            malformed_count += 1
            continue
        if identity in accepted_names:
            duplicate_count += 1
            continue
        if not _contains_token_sequence(source_tokens, name_tokens):
            ungrounded_count += 1
            continue
        accepted_names.add(identity)
        accepted.append(dict(entity))

    sanitized = dict(result)
    sanitized["extracted_entities"] = accepted
    status = "pass"
    if ungrounded_count or malformed_count:
        status = "filtered_ungrounded"
    elif duplicate_count:
        status = "filtered_duplicate"
    return sanitized, {
        "schema_version": "membind.v6.1.node-response-audit.v1",
        "event": "NODE_RESPONSE_AUDIT",
        "prompt_name": prompt_name,
        "source_text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "source_text_chars": len(source_text),
        "returned_entity_count": len(entities),
        "accepted_entity_count": len(accepted),
        "lexically_grounded_count": len(accepted),
        "ungrounded_entity_count": ungrounded_count,
        "duplicate_entity_count": duplicate_count,
        "malformed_entity_count": malformed_count,
        "schema_max_items": int(schema_max_items or 0),
        "schema_name_max_chars": LOCAL_NODE_MAX_NAME_CHARS,
        "status": status,
    }


def _audit_summary_response(
    messages: Sequence[Any],
    result: Any,
    *,
    prompt_name: str | None,
) -> tuple[Any, dict[str, Any] | None]:
    """Reject summary rows outside the requested entity flight and audit coverage."""

    if prompt_name not in {
        "extract_nodes.extract_summaries_batch",
        "extract_nodes.extract_entity_summaries_from_episodes",
    } or not isinstance(result, Mapping):
        return result, None
    entity_block = _entity_block(messages)
    summaries = result.get("summaries")
    if entity_block is None or not isinstance(summaries, list):
        return result, None
    requested = {
        " ".join(str(value.get("name", "")).split()).casefold()
        for value in entity_block[2]
        if str(value.get("name", "")).strip()
    }
    accepted: list[Mapping[str, Any]] = []
    accepted_names: set[str] = set()
    unknown_count = 0
    duplicate_count = 0
    for summary in summaries:
        if not isinstance(summary, Mapping):
            unknown_count += 1
            continue
        identity = " ".join(str(summary.get("name", "")).split()).casefold()
        if identity not in requested:
            unknown_count += 1
            continue
        if identity in accepted_names:
            duplicate_count += 1
            continue
        accepted_names.add(identity)
        accepted.append(dict(summary))
    sanitized = dict(result)
    sanitized["summaries"] = accepted
    status = "pass"
    if unknown_count:
        status = "filtered_unknown"
    elif duplicate_count:
        status = "filtered_duplicate"
    from graphiti_core.utils.text_utils import MAX_SUMMARY_CHARS

    return sanitized, {
        "schema_version": "membind.v6.1.summary-response-audit.v1",
        "event": "SUMMARY_RESPONSE_AUDIT",
        "prompt_name": prompt_name,
        "requested_entity_count": len(requested),
        "returned_summary_count": len(summaries),
        "accepted_summary_count": len(accepted),
        "unknown_summary_count": unknown_count,
        "duplicate_summary_count": duplicate_count,
        "omitted_requested_count": len(requested - accepted_names),
        "schema_max_items": len(requested),
        "schema_summary_max_chars": MAX_SUMMARY_CHARS,
        "status": status,
    }


def install_local_extraction_chunking_policy(
    llm_client: Any,
    *,
    token_counter: Callable[[Sequence[Any]], int] | None = None,
    chunk_char_limit: int = LOCAL_EXTRACTION_CHUNK_CURRENT_CHARS,
    partition_extraction_by_turns: bool = False,
    partition_edge_candidates: bool = False,
    node_partition_concurrency: int = LOCAL_NODE_PARTITION_CONCURRENCY,
    edge_partition_concurrency: int = LOCAL_EDGE_PARTITION_CONCURRENCY,
    edge_physical_concurrency: int = LOCAL_EDGE_PHYSICAL_CONCURRENCY,
    edge_duplicate_recovery: bool = False,
    edge_page_capacity: int = LOCAL_EDGE_PAGE_CAPACITY,
    memory_utility_order: bool = False,
    actor_domain_cover: bool = False,
    actor_domain_adjacent_domain: bool = True,
    actor_domain_boundary_join: bool = False,
    edge_frontier_priority: bool = False,
    edge_priority_burst: int | None = None,
    edge_endpoint_schema_grounding: bool = False,
    edge_adaptive_admission: bool = False,
) -> None:
    """Partition local extraction prompts while preserving Graphiti semantics.

    ``partition_edge_candidates`` is an opt-in 8B repair.  It expands an edge
    request into a complete pairwise candidate cover, so a model cannot spend
    its completion budget enumerating an unbounded relation set.  The default
    remains disabled for the frozen 14B runtime.
    """

    original = getattr(llm_client, "generate_response", None)
    if not callable(original):
        raise LocalRuntimeConfigurationError("local extraction seam is unavailable")
    count_tokens = token_counter or _local_prompt_tokens
    safety_margin = int(os.environ.get("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32"))
    if edge_partition_concurrency < 1:
        raise LocalRuntimeConfigurationError("edge partition concurrency must be positive")
    if edge_physical_concurrency < 1:
        raise LocalRuntimeConfigurationError("edge physical concurrency must be positive")
    if node_partition_concurrency < 1:
        raise LocalRuntimeConfigurationError("node partition concurrency must be positive")
    if edge_page_capacity < 1:
        raise LocalRuntimeConfigurationError("edge page capacity must be positive")
    if edge_priority_burst is not None and edge_priority_burst < 1:
        raise LocalRuntimeConfigurationError("edge page priority burst must be positive")
    edge_page_semaphore = asyncio.Semaphore(edge_physical_concurrency)
    edge_page_priority_gate = (
        _AdaptiveEdgePagePriorityGate(
            edge_physical_concurrency,
            initial_capacity=min(edge_physical_concurrency, edge_partition_concurrency),
            priority_burst=edge_priority_burst,
        )
        if edge_adaptive_admission
        else _EdgePagePriorityGate(
            edge_physical_concurrency,
            priority_burst=edge_priority_burst,
        )
    )
    node_partition_semaphore = asyncio.Semaphore(node_partition_concurrency)
    node_active_partition_requests = 0
    node_shared_max_active_partition_requests = 0
    edge_active_page_requests = 0
    edge_shared_max_active_page_requests = 0
    diagnostics: list[dict[str, Any]] = []
    setattr(llm_client, "_membind_extraction_diagnostics", diagnostics)
    entity_partition_hints_by_scope: dict[
        tuple[str | None, int | None], dict[str, list[int]]
    ] = {(None, None): {}}
    setattr(llm_client, "_membind_entity_partition_hints_by_scope", entity_partition_hints_by_scope)
    # Provider-free tests and direct probes use the unscoped bucket.
    setattr(llm_client, "_membind_entity_partition_hints", entity_partition_hints_by_scope[(None, None)])
    entity_partition_sources_by_scope: dict[
        tuple[str | None, int | None], dict[int, str]
    ] = {(None, None): {}}
    setattr(
        llm_client,
        "_membind_entity_partition_sources_by_scope",
        entity_partition_sources_by_scope,
    )
    setattr(
        llm_client,
        "_membind_entity_partition_sources",
        entity_partition_sources_by_scope[(None, None)],
    )

    async def chunked_generate(
        messages: list[Any],
        response_model: Any = None,
        max_tokens: int | None = None,
        model_size: Any = None,
        group_id: str | None = None,
        prompt_name: str | None = None,
        *,
        attribute_extraction: bool = False,
    ) -> Any:
        requested = int(max_tokens or getattr(llm_client, "max_tokens", 0) or 0)

        async def invoke(
            request_messages: list[Any],
            request_max_tokens: int | None,
            request_response_model: Any = response_model,
            *,
            partition_id: int | None = None,
            partition_count: int | None = None,
            partition_kind: str | None = None,
            page_index: int | None = None,
            queue_wait_ns: int | None = None,
            physical_active_at_start: int | None = None,
        ) -> Any:
            entity_info = _entity_block(request_messages)
            entity_count = None
            if entity_info is not None:
                entity_count = len(_distinct_entity_values(entity_info[2]))
            effective_response_model = request_response_model
            node_schema_max_items: int | None = None
            if prompt_name in {
                "extract_nodes.extract_message",
                "extract_nodes.extract_text",
                "extract_nodes.extract_json",
            }:
                node_schema_max_items = _node_schema_capacity(request_messages)
                effective_response_model = _bounded_node_response_model(node_schema_max_items)
            if prompt_name in {
                "extract_nodes.extract_summaries_batch",
                "extract_nodes.extract_entity_summaries_from_episodes",
            } and entity_count:
                effective_response_model = _bounded_summary_response_model(entity_count)
            call_kwargs: dict[str, Any] = {
                "response_model": effective_response_model,
                "max_tokens": request_max_tokens,
                "group_id": group_id,
                "prompt_name": prompt_name,
                "attribute_extraction": attribute_extraction,
            }
            if model_size is not None:
                call_kwargs["model_size"] = model_size
            prompt_tokens: int | None
            try:
                prompt_tokens = int(count_tokens(request_messages))
            except Exception:
                prompt_tokens = None
            row: dict[str, Any] = {
                "schema_version": "membind.v6.1.extraction-diagnostic.v1",
                "prompt_name": prompt_name,
                "prompt_tokens": prompt_tokens,
                "requested_max_tokens": None if request_max_tokens is None else int(request_max_tokens),
                "distinct_entity_count": entity_count,
                "partition_id": partition_id,
                "partition_count": partition_count,
                "partition_kind": partition_kind,
                "page_index": page_index,
                "status": "started",
            }
            if node_schema_max_items is not None:
                row["node_schema_max_items"] = node_schema_max_items
                row["node_schema_name_max_chars"] = LOCAL_NODE_MAX_NAME_CHARS
            if queue_wait_ns is not None:
                row["queue_wait_ns"] = queue_wait_ns
            if physical_active_at_start is not None:
                row["physical_active_at_start"] = physical_active_at_start
            call_event_count = len(getattr(llm_client, "call_events", ()) or ())
            service_start_ns = time.monotonic_ns()
            try:
                result = await original(request_messages, **call_kwargs)
            except BaseException as exc:
                row.update(
                    {
                        "status": "failure",
                        "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                        "service_ns": time.monotonic_ns() - service_start_ns,
                    }
                )
                call_events = getattr(llm_client, "call_events", ()) or ()
                if len(call_events) > call_event_count:
                    last = call_events[-1]
                    if isinstance(last, Mapping):
                        row["finish_reason"] = last.get("finish_reason")
                        usage = last.get("token_usage")
                        if isinstance(usage, Mapping):
                            row["completion_tokens"] = usage.get("completion_tokens")
                            row["observed_prompt_tokens"] = usage.get("prompt_tokens")
                diagnostics.append(row)
                raise
            row["status"] = "success"
            row["service_ns"] = time.monotonic_ns() - service_start_ns
            result, summary_audit = _audit_summary_response(
                request_messages,
                result,
                prompt_name=prompt_name,
            )
            if summary_audit is not None:
                diagnostics.append(summary_audit)
            result, node_audit = _audit_node_response(
                request_messages,
                result,
                prompt_name=prompt_name,
                schema_max_items=node_schema_max_items,
            )
            if node_audit is not None:
                diagnostics.append(node_audit)
            if str(prompt_name).startswith("extract_nodes.") and isinstance(result, Mapping):
                extracted = result.get("extracted_entities")
                if isinstance(extracted, list):
                    if isinstance(partition_id, int):
                        scope = _provider_scope_key()
                        scoped_hints = entity_partition_hints_by_scope.setdefault(scope, {})
                        source_text = _current_message_text(request_messages)
                        if source_text is None:
                            raise LocalRuntimeConfigurationError(
                                "partitioned node extraction has no CURRENT MESSAGE source"
                            )
                        scoped_sources = entity_partition_sources_by_scope.setdefault(scope, {})
                        existing_source = scoped_sources.setdefault(partition_id, source_text)
                        if existing_source != source_text:
                            raise LocalRuntimeConfigurationError(
                                "node partition source provenance changed within one provider scope"
                            )
                        for item in extracted:
                            if not isinstance(item, Mapping):
                                continue
                            identity = " ".join(str(item.get("name", "")).split()).casefold()
                            if identity:
                                scoped_hints.setdefault(identity, []).append(partition_id)
            # The Qwen transport records this without prompt/response content.
            call_events = getattr(llm_client, "call_events", ()) or ()
            if call_events:
                last = call_events[-1]
                if isinstance(last, Mapping):
                    row["finish_reason"] = last.get("finish_reason")
                    usage = last.get("token_usage")
                    if isinstance(usage, Mapping):
                        row["completion_tokens"] = usage.get("completion_tokens")
                        row["observed_prompt_tokens"] = usage.get("prompt_tokens")
            diagnostics.append(row)
            return result

        if prompt_name not in {
            "extract_nodes.extract_message",
            "extract_nodes.extract_text",
            "extract_nodes.extract_json",
            "extract_edges.edge",
        }:
            return await invoke(messages, max_tokens)

        if prompt_name == "extract_edges.edge" and partition_edge_candidates:
            scope = _provider_scope_key()
            scoped_sources = entity_partition_sources_by_scope.get(scope, {})
            if scope != (None, None) and not scoped_sources:
                raise LocalRuntimeConfigurationError(
                    "live edge extraction is missing node-partition source provenance"
                )
            evidence_partition_metadata: list[dict[str, Any]] = []
            expanded = _edge_turn_local_partitions(
                messages,
                entity_partition_hints=entity_partition_hints_by_scope.get(
                    scope, {}
                ),
                entity_partition_sources=scoped_sources,
                partition_metadata=evidence_partition_metadata,
                actor_domain_cover=actor_domain_cover,
                actor_domain_adjacent_domain=actor_domain_adjacent_domain,
                actor_domain_boundary_join=actor_domain_boundary_join,
            )
            if expanded is not None:
                partitions, entity_count, partition_count, max_partition_entities = expanded
                edge_page_model = _bounded_edge_page_model(edge_page_capacity)
                local_active_page_requests = 0
                local_max_active_page_requests = 0

                async def invoke_edge_page(
                    page_messages: list[Any],
                    *,
                    partition_id: int,
                    page_index: int,
                    response_model: Any,
                ) -> tuple[Any, int, int, int | None, dict[str, Any] | None]:
                    nonlocal edge_active_page_requests
                    nonlocal edge_shared_max_active_page_requests
                    nonlocal local_active_page_requests
                    nonlocal local_max_active_page_requests
                    queued_ns = time.monotonic_ns()
                    priority_source = (
                        int(scope[1]) if isinstance(scope[1], int) else 2**31 - 1
                    )
                    if edge_adaptive_admission or edge_frontier_priority:
                        priority_ticket = await edge_page_priority_gate.acquire(priority_source)
                        priority_evidence = edge_page_priority_gate.grant_evidence(
                            priority_ticket
                        )
                    else:
                        await edge_page_semaphore.acquire()
                        priority_ticket = None
                        priority_evidence = None
                    admitted_ns = time.monotonic_ns()
                    edge_active_page_requests += 1
                    local_active_page_requests += 1
                    edge_shared_max_active_page_requests = max(
                        edge_shared_max_active_page_requests,
                        edge_active_page_requests,
                    )
                    local_max_active_page_requests = max(
                        local_max_active_page_requests,
                        local_active_page_requests,
                    )
                    service_start_ns = time.monotonic_ns()
                    observed_service_ns: int | None = None
                    try:
                        page = await invoke(
                            page_messages,
                            requested,
                            response_model,
                            partition_id=partition_id,
                            partition_count=partition_count,
                            partition_kind="edge_turn_local_page",
                            page_index=page_index,
                            queue_wait_ns=admitted_ns - queued_ns,
                            physical_active_at_start=edge_active_page_requests,
                        )
                        observed_service_ns = time.monotonic_ns() - service_start_ns
                        return (
                            page,
                            admitted_ns - queued_ns,
                            observed_service_ns,
                            priority_ticket,
                            priority_evidence,
                        )
                    finally:
                        local_active_page_requests -= 1
                        edge_active_page_requests -= 1
                        if edge_adaptive_admission:
                            edge_page_priority_gate.release(
                                queue_wait_ns=admitted_ns - queued_ns,
                                service_ns=(
                                    observed_service_ns
                                    if observed_service_ns is not None
                                    else time.monotonic_ns() - service_start_ns
                                ),
                            )
                        elif edge_frontier_priority:
                            edge_page_priority_gate.release()
                        else:
                            edge_page_semaphore.release()

                async def run_partition(
                    partition_id: int,
                    partition: list[Any],
                ) -> tuple[list[Any], str, int]:
                    partition_responses: list[Any] = []
                    pagination_history: list[Mapping[str, Any]] = []
                    seen_raw_identities: set[str] = set()
                    accepted_partition_edges: list[Mapping[str, Any]] = []
                    duplicate_recovery_edge: Mapping[str, Any] | None = None
                    duplicate_recovery_used = False
                    partition_entity_block = _entity_block(partition)
                    if partition_entity_block is None:
                        raise LocalRuntimeConfigurationError(
                            "edge partition has no structured entity block"
                        )
                    allowed_endpoint_names = {
                        " ".join(str(value.get("name", "")).split()).casefold()
                        for value in partition_entity_block[2]
                        if str(value.get("name", "")).strip()
                    }
                    partition_evidence = evidence_partition_metadata[partition_id]
                    partition_endpoint_names = tuple(
                        dict.fromkeys(
                            str(value.get("name", "")).strip()
                            for value in partition_entity_block[2]
                            if isinstance(value, Mapping)
                            and str(value.get("name", "")).strip()
                        )
                    )
                    partition_page_model = (
                        _endpoint_grounded_edge_page_model(
                            edge_page_capacity,
                            partition_endpoint_names,
                        )
                        if edge_endpoint_schema_grounding
                        else edge_page_model
                    )
                    cross_boundary_required = bool(
                        partition_evidence.get("cross_boundary_required")
                    )
                    cross_left_endpoint_names = set(
                        partition_evidence.get("_cross_left_endpoint_names", set())
                    )
                    cross_right_endpoint_names = set(
                        partition_evidence.get("_cross_right_endpoint_names", set())
                    )
                    for page_index in range(LOCAL_EDGE_MAX_PAGES):
                        is_duplicate_recovery = duplicate_recovery_edge is not None
                        page_messages = _edge_page_messages(
                            partition,
                            pagination_history,
                            page_capacity=edge_page_capacity,
                            duplicate_recovery_edge=duplicate_recovery_edge,
                            memory_utility_order=memory_utility_order,
                        )
                        (
                            page,
                            queue_wait_ns,
                            service_ns,
                            priority_ticket,
                            priority_evidence,
                        ) = await invoke_edge_page(
                            page_messages,
                            partition_id=partition_id,
                            page_index=page_index,
                            response_model=partition_page_model,
                        )
                        if not isinstance(page, Mapping):
                            raise LocalRuntimeConfigurationError("edge page response is not an object")
                        page_edges = page.get("edges")
                        if not isinstance(page_edges, list):
                            raise LocalRuntimeConfigurationError("edge page response has no edges list")
                        if len(page_edges) > edge_page_capacity or not all(
                            isinstance(edge, Mapping) for edge in page_edges
                        ):
                            raise LocalRuntimeConfigurationError("edge page returned an invalid cardinality")
                        delta: list[Mapping[str, Any]] = []
                        page_identities: set[str] = set()
                        raw_unique_progress: list[Mapping[str, Any]] = []
                        duplicate_count = 0
                        invalid_endpoint_count = 0
                        non_boundary_edge_count = 0
                        for raw_edge in page_edges:
                            edge = dict(raw_edge)
                            identity = _edge_identity(edge)
                            source_name = " ".join(
                                str(edge.get("source_entity_name", "")).split()
                            ).casefold()
                            target_name = " ".join(
                                str(edge.get("target_entity_name", "")).split()
                            ).casefold()
                            if (
                                source_name not in allowed_endpoint_names
                                or target_name not in allowed_endpoint_names
                            ):
                                invalid_endpoint_count += 1
                            crosses_boundary = (
                                (source_name in cross_left_endpoint_names and target_name in cross_right_endpoint_names)
                                or (
                                    source_name in cross_right_endpoint_names
                                    and target_name in cross_left_endpoint_names
                                )
                            )
                            if cross_boundary_required and not crosses_boundary:
                                non_boundary_edge_count += 1
                            if identity in seen_raw_identities or identity in page_identities:
                                duplicate_count += 1
                                continue
                            page_identities.add(identity)
                            raw_unique_progress.append(edge)
                            if (
                                source_name not in allowed_endpoint_names
                                or target_name not in allowed_endpoint_names
                                or (cross_boundary_required and not crosses_boundary)
                            ):
                                continue
                            delta.append(edge)
                        diagnostics.append(
                            {
                                "schema_version": "membind.v6.1.edge-page-delta.v1",
                                "event": "EDGE_PAGINATION_PAGE",
                                "prompt_name": prompt_name,
                                "partition_id": partition_id,
                                "partition_count": partition_count,
                                "page_index": page_index,
                                "page_capacity": edge_page_capacity,
                                "raw_edge_count": len(page_edges),
                                "raw_unique_progress_edge_count": len(raw_unique_progress),
                                "delta_edge_count": len(delta),
                                "duplicate_edge_count": duplicate_count,
                                "duplicate_recovery_request": is_duplicate_recovery,
                                "duplicate_recovery_succeeded": (
                                    is_duplicate_recovery and bool(raw_unique_progress)
                                ),
                                "invalid_endpoint_edge_count": invalid_endpoint_count,
                                "non_boundary_edge_count": non_boundary_edge_count,
                                "cumulative_raw_distinct_edge_count": (
                                    len(pagination_history) + len(raw_unique_progress)
                                ),
                                "cumulative_distinct_edge_count": (
                                    len(accepted_partition_edges) + len(delta)
                                ),
                                "queue_wait_ns": queue_wait_ns,
                                "service_ns": service_ns,
                                "priority_source_sequence": (
                                    int(scope[1]) if isinstance(scope[1], int) else None
                                ),
                                "priority_ticket": priority_ticket,
                                "priority_admission_reason": (
                                    priority_evidence.get("admission_reason")
                                    if priority_evidence is not None
                                    else None
                                ),
                                "priority_preferred_source_sequence": (
                                    priority_evidence.get("preferred_source_sequence")
                                    if priority_evidence is not None
                                    else None
                                ),
                                "priority_burst_limit": (
                                    priority_evidence.get("priority_burst_limit")
                                    if priority_evidence is not None
                                    else None
                                ),
                                "priority_preferred_consecutive_grants_before": (
                                    priority_evidence.get(
                                        "preferred_consecutive_grants_before"
                                    )
                                    if priority_evidence is not None
                                    else None
                                ),
                                "status": "observed",
                            }
                        )
                        if not page_edges:
                            diagnostics.append(
                                {
                                    "schema_version": "membind.v6.1.edge-fixed-point.v1",
                                    "prompt_name": prompt_name,
                                    "partition_id": partition_id,
                                    "partition_count": partition_count,
                                    "page_index": page_index,
                                    "distinct_edge_count": len(accepted_partition_edges),
                                    "raw_distinct_edge_count": len(pagination_history),
                                    "event": "EDGE_PAGINATION_EMPTY_PAGE",
                                    "termination_reason": "empty_page",
                                    "status": "converged",
                                }
                            )
                            return partition_responses, "empty_page", page_index + 1
                        if not raw_unique_progress:
                            if (
                                edge_duplicate_recovery
                                and not is_duplicate_recovery
                                and not duplicate_recovery_used
                            ):
                                duplicate_recovery_used = True
                                duplicate_recovery_edge = dict(page_edges[0])
                                diagnostics.append(
                                    {
                                        "schema_version": (
                                            "membind.v6.1.edge-duplicate-recovery.v1"
                                        ),
                                        "prompt_name": prompt_name,
                                        "partition_id": partition_id,
                                        "partition_count": partition_count,
                                        "page_index": page_index,
                                        "event": "EDGE_PAGINATION_DUPLICATE_RECOVERY",
                                        "status": "scheduled",
                                    }
                                )
                                continue
                            diagnostics.append(
                                {
                                    "schema_version": "membind.v6.1.edge-fixed-point.v1",
                                    "prompt_name": prompt_name,
                                    "partition_id": partition_id,
                                    "partition_count": partition_count,
                                    "page_index": page_index,
                                    "distinct_edge_count": len(accepted_partition_edges),
                                    "raw_distinct_edge_count": len(pagination_history),
                                    "event": "EDGE_PAGINATION_ZERO_DELTA",
                                    "termination_reason": "zero_delta",
                                    "status": "converged",
                                }
                            )
                            return partition_responses, "zero_delta", page_index + 1
                        duplicate_recovery_edge = None
                        pagination_history.extend(raw_unique_progress)
                        seen_raw_identities.update(page_identities)
                        if delta:
                            accepted_partition_edges.extend(delta)
                            partition_responses.append({"edges": list(delta)})
                    raise LocalRuntimeConfigurationError("edge pagination exceeded bounded progress")

                partition_results: list[tuple[list[Any], str, int] | None] = [
                    None
                ] * partition_count
                if edge_partition_concurrency == 1:
                    for partition_id, partition in enumerate(partitions):
                        partition_results[partition_id] = await run_partition(
                            partition_id,
                            partition,
                        )
                else:
                    work_queue: asyncio.Queue[tuple[int, list[Any]]] = asyncio.Queue()
                    for partition_id, partition in enumerate(partitions):
                        work_queue.put_nowait((partition_id, partition))

                    async def partition_worker() -> None:
                        while True:
                            try:
                                partition_id, partition = work_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                return
                            partition_results[partition_id] = await run_partition(
                                partition_id,
                                partition,
                            )

                    workers = [
                        asyncio.create_task(partition_worker())
                        for _ in range(min(edge_partition_concurrency, partition_count))
                    ]
                    try:
                        await asyncio.gather(*workers)
                    except BaseException:
                        for worker in workers:
                            worker.cancel()
                        await asyncio.gather(*workers, return_exceptions=True)
                        raise

                responses: list[Any] = []
                termination_reasons: list[str] = []
                pages_per_partition: list[int] = []
                for result in partition_results:
                    if result is None:
                        raise LocalRuntimeConfigurationError(
                            "edge partition worker did not publish a result"
                        )
                    partition_responses, termination_reason, page_count = result
                    responses.extend(partition_responses)
                    termination_reasons.append(termination_reason)
                    pages_per_partition.append(page_count)
                # Keep expansion auditable while omitting entity names/content.
                diagnostics.append(
                    {
                        "schema_version": "membind.v6.1.extraction-expansion.v1",
                        "prompt_name": prompt_name,
                        "distinct_entity_count": entity_count,
                        "partition_count": partition_count,
                        "max_partition_entity_count": max_partition_entities,
                        "partition_kind": "edge_turn_local_page",
                        "page_capacity": edge_page_capacity,
                        "max_pages_per_partition": LOCAL_EDGE_MAX_PAGES,
                        "partition_worker_concurrency": edge_partition_concurrency,
                        "physical_page_concurrency": edge_physical_concurrency,
                        "adaptive_admission_enabled": edge_adaptive_admission,
                        "adaptive_admission_state": (
                            edge_page_priority_gate.state()
                            if edge_adaptive_admission
                            else None
                        ),
                        "frontier_priority_enabled": edge_frontier_priority,
                        "frontier_priority_burst": edge_page_priority_gate.priority_burst,
                        "endpoint_schema_grounding_enabled": edge_endpoint_schema_grounding,
                        "duplicate_recovery_enabled": edge_duplicate_recovery,
                        "memory_utility_order_enabled": memory_utility_order,
                        "actor_domain_cover_enabled": actor_domain_cover,
                        "actor_domain_adjacent_domain_enabled": actor_domain_adjacent_domain,
                        "actor_domain_boundary_join_enabled": actor_domain_boundary_join,
                        "max_active_page_requests": local_max_active_page_requests,
                        "shared_max_active_page_requests": edge_shared_max_active_page_requests,
                        "pages_per_partition": pages_per_partition,
                        "evidence_window_count": len(evidence_partition_metadata),
                        "evidence_source_partition_ids": [
                            row["evidence_source_partition_ids"]
                            for row in evidence_partition_metadata
                        ],
                        "evidence_view_kinds": [
                            row["evidence_view_kind"]
                            for row in evidence_partition_metadata
                        ],
                        "cross_boundary_required": [
                            row["cross_boundary_required"]
                            for row in evidence_partition_metadata
                        ],
                        "evidence_source_hashes": [
                            row["evidence_source_hashes"]
                            for row in evidence_partition_metadata
                        ],
                        "current_message_chars": [
                            row["current_message_chars"]
                            for row in evidence_partition_metadata
                        ],
                        "merge_partition_order": list(range(partition_count)),
                        "termination_reason_counts": {
                            reason: termination_reasons.count(reason)
                            for reason in sorted(set(termination_reasons))
                        },
                        "page_count": sum(pages_per_partition),
                        "status": "merged",
                    }
                )
                return _merge_extraction_responses(prompt_name, responses)
        prompt_limit = LOCAL_CONTEXT_LIMIT - safety_margin - LOCAL_EXTRACTION_CHUNK_OUTPUT_TOKENS
        if not partition_extraction_by_turns:
            if requested <= LOCAL_EXTRACTION_CHUNK_OUTPUT_TOKENS:
                return await invoke(messages, max_tokens)
            prompt_tokens = int(count_tokens(messages))
            if prompt_tokens <= LOCAL_EXTRACTION_CHUNK_TRIGGER_TOKENS:
                return await invoke(messages, max_tokens)
        partitions = _partition_current_message(
            messages,
            prompt_limit=prompt_limit,
            token_counter=count_tokens,
            current_char_limit=chunk_char_limit,
        )
        if len(partitions) <= 1:
            return await invoke(messages, max_tokens)
        chunk_budget = min(requested, LOCAL_EXTRACTION_CHUNK_OUTPUT_TOKENS)
        if (
            not str(prompt_name).startswith("extract_nodes.")
            or node_partition_concurrency == 1
        ):
            responses: list[Any] = []
            for partition_id, partition in enumerate(partitions):
                responses.append(
                    await invoke(
                        partition,
                        chunk_budget,
                        partition_id=partition_id,
                        partition_count=len(partitions),
                        partition_kind="dialogue_turn",
                    )
                )
            return _merge_extraction_responses(prompt_name, responses)

        local_active_partition_requests = 0
        local_max_active_partition_requests = 0
        responses_by_partition: list[Any | None] = [None] * len(partitions)

        async def invoke_node_partition(
            partition_id: int,
            partition: list[Any],
        ) -> None:
            nonlocal node_active_partition_requests
            nonlocal node_shared_max_active_partition_requests
            nonlocal local_active_partition_requests
            nonlocal local_max_active_partition_requests
            queued_ns = time.monotonic_ns()
            await node_partition_semaphore.acquire()
            admitted_ns = time.monotonic_ns()
            node_active_partition_requests += 1
            local_active_partition_requests += 1
            node_shared_max_active_partition_requests = max(
                node_shared_max_active_partition_requests,
                node_active_partition_requests,
            )
            local_max_active_partition_requests = max(
                local_max_active_partition_requests,
                local_active_partition_requests,
            )
            try:
                responses_by_partition[partition_id] = await invoke(
                    partition,
                    chunk_budget,
                    partition_id=partition_id,
                    partition_count=len(partitions),
                    partition_kind="dialogue_turn",
                    queue_wait_ns=admitted_ns - queued_ns,
                    physical_active_at_start=node_active_partition_requests,
                )
            finally:
                local_active_partition_requests -= 1
                node_active_partition_requests -= 1
                node_partition_semaphore.release()

        work_queue: asyncio.Queue[tuple[int, list[Any]]] = asyncio.Queue()
        for partition_id, partition in enumerate(partitions):
            work_queue.put_nowait((partition_id, partition))

        async def node_partition_worker() -> None:
            while True:
                try:
                    partition_id, partition = work_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                await invoke_node_partition(partition_id, partition)

        workers = [
            asyncio.create_task(node_partition_worker())
            for _ in range(min(node_partition_concurrency, len(partitions)))
        ]
        try:
            await asyncio.gather(*workers)
        except BaseException:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            raise

        if any(response is None for response in responses_by_partition):
            raise LocalRuntimeConfigurationError(
                "node partition worker did not publish a response"
            )
        scope = _provider_scope_key()
        for partition_ids in entity_partition_hints_by_scope.get(scope, {}).values():
            partition_ids[:] = sorted(set(partition_ids))
        diagnostics.append(
            {
                "schema_version": "membind.v6.1.node-partition-pipeline.v1",
                "prompt_name": prompt_name,
                "partition_count": len(partitions),
                "partition_worker_concurrency": node_partition_concurrency,
                "physical_partition_concurrency": node_partition_concurrency,
                "max_active_partition_requests": local_max_active_partition_requests,
                "shared_max_active_partition_requests": (
                    node_shared_max_active_partition_requests
                ),
                "merge_partition_order": list(range(len(partitions))),
                "status": "merged",
            }
        )
        responses = [response for response in responses_by_partition if response is not None]
        return _merge_extraction_responses(prompt_name, responses)

    setattr(llm_client, "generate_response", chunked_generate)


def local_runtime_manifest() -> dict[str, Any]:
    """Resolve and validate the public, non-secret local runtime identity."""

    profile = _expect("MEMBIND_PROFILE_ID", LOCAL_PROFILE_ID)
    construction_url = _expect(
        "CONSTRUCTION_LLM_BASE_URL", LOCAL_LLM_BASE_URL, normalize_url=True
    )
    construction_model = _expect("CONSTRUCTION_LLM_MODEL", LOCAL_LLM_MODEL)
    embedding_url = _expect(
        "EMBEDDING_BASE_URL", LOCAL_EMBEDDING_BASE_URL, normalize_url=True
    )
    embedding_model = _expect("EMBEDDING_MODEL", LOCAL_EMBEDDING_MODEL)
    embedding_dimension = _integer("EMBEDDING_DIM", LOCAL_EMBEDDING_DIMENSION)
    max_coroutines = _integer("GRAPHITI_MAX_COROUTINES", LOCAL_MAX_COROUTINES)
    http_timeout_seconds = _float(
        "CONSTRUCTION_HTTP_TIMEOUT_SECONDS", LOCAL_HTTP_TIMEOUT_SECONDS
    )
    sdk_max_retries = _integer("CONSTRUCTION_SDK_MAX_RETRIES", LOCAL_SDK_MAX_RETRIES)
    _expect("CONSTRUCTION_TOP_P", "1.0")
    _expect("CONSTRUCTION_SEED", "20260806")
    _expect("CONSTRUCTION_MIN_CONTEXT_TOKENS", str(LOCAL_CONTEXT_LIMIT))
    _expect("CONSTRUCTION_EXPECTED_VLLM_VERSION", "0.26.0")
    neo4j_uri = _required("NEO4J_URI")
    requested_max_tokens = int(os.environ.get("CONSTRUCTION_MAX_TOKENS", "2048"))
    overflow_max_tokens = int(os.environ.get("CONSTRUCTION_OVERFLOW_MAX_TOKENS", "8192"))
    if requested_max_tokens <= 0 or overflow_max_tokens < requested_max_tokens:
        raise LocalRuntimeConfigurationError("construction token budget is invalid")
    return {
        "schema_version": "membind.local-runtime-manifest.v1",
        "profile_id": profile,
        "construction": {
            "base_url": _normalized_url(construction_url),
            "served_model_id": construction_model,
            "model_revision": os.environ.get("CONSTRUCTION_MODEL_REVISION"),
            "context_limit": LOCAL_CONTEXT_LIMIT,
            "requested_max_tokens": requested_max_tokens,
            "overflow_max_tokens": overflow_max_tokens,
            "context_overflow_strategy": "local_chat_token_count_with_bounded_wire_retry_v1",
            "context_retry_limit": 6,
            "context_safety_tokens": int(
                os.environ.get("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
            ),
            "http_timeout_seconds": http_timeout_seconds,
            "transport_pool": {
                "max_connections": LOCAL_MAX_COROUTINES,
                "max_keepalive_connections": LOCAL_MAX_COROUTINES,
                "keepalive_expiry_seconds": 60.0,
                "trust_env": False,
            },
            "sdk_max_retries": sdk_max_retries,
            "graphiti_retry_policy": "single_attempt_no_tenacity",
            "extraction_chunking_policy": LOCAL_EXTRACTION_CHUNKING_POLICY,
            "temperature": float(os.environ.get("CONSTRUCTION_TEMPERATURE", "0.0")),
            "top_p": 1.0,
            "seed": 20260806,
            "thinking": False,
        },
        "embedding": {
            "base_url": _normalized_url(embedding_url),
            "served_model_id": embedding_model,
            "dimension": embedding_dimension,
        },
        "neo4j": {"uri": neo4j_uri, "database": os.environ.get("NEO4J_DATABASE", "neo4j")},
        "graphiti_max_coroutines": max_coroutines,
    }


def public_runtime_environment(*, repo_root: Path | None = None) -> dict[str, Any]:
    """Add code identity to the validated profile without exposing secrets."""

    manifest = local_runtime_manifest()
    root = (repo_root or Path(__file__).resolve().parents[5]).resolve()
    manifest["repo_root"] = str(root)
    manifest["python"] = os.path.realpath(os.sys.executable)
    manifest["profile_manifest_sha256"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return manifest


def build_local_u0_runtime() -> U0Runtime:
    """Construct the no-cache U0 path for the activated 14B local profile."""

    manifest = local_runtime_manifest()
    construction_key = _required("CONSTRUCTION_LLM_API_KEY")
    embedding_key = _required("EMBEDDING_API_KEY")
    neo4j_user = _required("NEO4J_USER")
    neo4j_password = _required("NEO4J_PASSWORD")

    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_native import QwenVLLMClient

    config = U0Config(
        construction_base_url=str(manifest["construction"]["base_url"]),
        construction_model=str(manifest["construction"]["served_model_id"]),
        construction_model_revision=str(manifest["construction"].get("model_revision") or "unknown"),
        embedding_base_url=str(manifest["embedding"]["base_url"]),
        embedding_model=str(manifest["embedding"]["served_model_id"]),
        embedding_dimension=int(manifest["embedding"]["dimension"]),
        neo4j_uri=str(manifest["neo4j"]["uri"]),
        max_coroutines=int(manifest["graphiti_max_coroutines"]),
        structured_output_mode="json_schema",
        requested_max_tokens=int(manifest["construction"]["requested_max_tokens"]),
        context_limit=int(manifest["construction"]["context_limit"]),
        safety_margin_tokens=int(os.environ.get("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")),
    )
    llm_config = LLMConfig(
        api_key=construction_key,
        model=config.construction_model,
        small_model=config.construction_model,
        base_url=config.construction_base_url,
        temperature=0.0,
        max_tokens=config.requested_max_tokens,
    )
    construction_transport = build_local_openai_transport(
        api_key=construction_key,
        base_url=config.construction_base_url,
        timeout_seconds=float(manifest["construction"]["http_timeout_seconds"]),
        max_retries=int(manifest["construction"]["sdk_max_retries"]),
    )
    llm_client = QwenVLLMClient(
        config=llm_config,
        client=construction_transport,
        max_tokens=config.requested_max_tokens,
        structured_output_mode=config.structured_output_mode,
    )
    install_local_single_attempt_policy(llm_client)
    install_local_extraction_chunking_policy(llm_client)
    embedder = OpenAIEmbedder(
        OpenAIEmbedderConfig(
            api_key=embedding_key,
            base_url=config.embedding_base_url,
            embedding_model=config.embedding_model,
            embedding_dim=config.embedding_dimension,
        )
    )
    reranker = OpenAIRerankerClient(config=llm_config, client=construction_transport)
    graphiti = Graphiti(
        uri=config.neo4j_uri,
        user=neo4j_user,
        password=neo4j_password,
        llm_client=llm_client,
        embedder=embedder,
        cross_encoder=reranker,
        max_coroutines=config.max_coroutines,
    )
    runtime = U0Runtime(
        graphiti=graphiti,
        llm_client=llm_client,
        embedder=embedder,
        reranker=reranker,
        config=config,
        classification="U0_LOCAL_QWEN3_14B_AWQ_V1",
    )
    runtime._membind_owned_transports = (construction_transport, embedder.client)
    runtime._membind_runtime_closed = False
    return runtime


async def close_local_u0_runtime(runtime: U0Runtime) -> None:
    """Close the database and every HTTP transport owned by a local runtime."""

    if bool(getattr(runtime, "_membind_runtime_closed", False)):
        return
    errors: list[BaseException] = []
    graphiti_close = getattr(getattr(runtime, "graphiti", None), "close", None)
    if callable(graphiti_close):
        try:
            pending = graphiti_close()
            if inspect.isawaitable(pending):
                await pending
        except BaseException as exc:
            errors.append(exc)
    seen_transports: set[int] = set()
    for transport in getattr(runtime, "_membind_owned_transports", ()):
        transport_id = id(transport)
        if transport_id in seen_transports:
            continue
        seen_transports.add(transport_id)
        close_transport = getattr(transport, "close", None)
        if callable(close_transport):
            try:
                pending = close_transport()
                if inspect.isawaitable(pending):
                    await pending
            except BaseException as exc:
                errors.append(exc)
    if errors:
        # A partial close must remain retryable. AsyncOpenAI and Neo4j close
        # operations are idempotent, so a later cleanup pass may safely close
        # every component again and recover the component that failed here.
        raise errors[0]
    runtime._membind_runtime_closed = True


def assert_namespace_identity(namespace: str) -> None:
    if not isinstance(namespace, str) or not namespace.startswith(f"{LOCAL_PROFILE_ID}-"):
        raise LocalRuntimeConfigurationError("namespace is outside the local profile")
    if "32b" in namespace.casefold() or "fp8" in namespace.casefold():
        raise LocalRuntimeConfigurationError("namespace mixes a frozen 32B identity")


def local_frozen_config() -> Mapping[str, Any]:
    """Return the immutable public config embedded in each local artifact."""

    return {
        "schema_version": "membind.local-qwen3-14b-awq-v1.config.v1",
        "status": "FROZEN_FOR_LOCAL_CAMPAIGN",
        **local_runtime_manifest(),
    }


__all__ = [
    "LOCAL_PROFILE_ID",
    "LocalRuntimeConfigurationError",
    "assert_namespace_identity",
    "build_local_u0_runtime",
    "build_local_openai_transport",
    "close_local_u0_runtime",
    "install_local_single_attempt_policy",
    "install_local_extraction_chunking_policy",
    "install_local_context_budget_adapter",
    "local_frozen_config",
    "local_runtime_manifest",
    "public_runtime_environment",
]
