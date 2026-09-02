"""Arm-agnostic bounded structured-output compatibility substrate.

The adapter owns only the wire contract and deterministic page collection.  It
does not inspect an arm name, scheduler policy, or model identity; callers pass
the same instance to A, B, and C.  Malformed/truncated pages are rejected by
the underlying provider client and never salvaged here.
"""

from __future__ import annotations

import inspect
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence


SHARED_ADAPTER_VERSION = "shared-bounded-structured-output-v1"
SHARED_MAX_TOKENS = 16_384
SHARED_PAGE_CAPACITY = 1
SHARED_MAX_PAGES = 64
SHARED_FACT_MAX_LENGTH = 1_900
SHARED_RETRY_POLICY = "single_attempt_no_retry_until_lucky_v1"


class PageCapExhausted(RuntimeError):
    """The bounded page protocol cannot prove convergence or complete coverage."""


def canonical_edge_tuple(edge: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(edge.get(key, "")) for key in (
        "source_entity_name", "target_entity_name", "relation_type",
        "fact", "valid_at", "invalid_at",
    ))


@dataclass(frozen=True, slots=True)
class EdgePage:
    edges: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class CollectedEdges:
    edges: tuple[Mapping[str, Any], ...]
    termination: str
    page_count: int


def _finite_schema(
    page_capacity: int,
    endpoint_names: Sequence[str] = (),
    fact_max_length: int = SHARED_FACT_MAX_LENGTH,
) -> dict[str, Any]:
    if page_capacity < 1:
        raise ValueError("page capacity must be positive")
    names = tuple(dict.fromkeys(str(name) for name in endpoint_names if str(name).strip()))
    endpoint = {"type": "string", "minLength": 1, "maxLength": 256}
    if names:
        endpoint = {"type": "string", "enum": list(names)}
    edge = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source_entity_name": endpoint,
            "target_entity_name": endpoint,
            "relation_type": {"type": "string", "minLength": 1, "maxLength": 128},
            "fact": {"type": "string", "minLength": 1, "maxLength": fact_max_length},
            "valid_at": {"anyOf": [{"type": "string", "maxLength": 40}, {"type": "null"}]},
            "invalid_at": {"anyOf": [{"type": "string", "maxLength": 40}, {"type": "null"}]},
            "episode_indices": {
                "type": "array", "minItems": 1, "maxItems": 1,
                "items": {"type": "integer", "const": 0},
            },
        },
        "required": ["source_entity_name", "target_entity_name", "relation_type", "fact"],
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {"edges": {"type": "array", "minItems": 0, "maxItems": page_capacity, "items": edge}},
        "required": ["edges"],
    }


@dataclass(frozen=True, slots=True)
class SharedStructuredOutputContract:
    page_capacity: int = SHARED_PAGE_CAPACITY
    max_pages: int = SHARED_MAX_PAGES
    fact_max_length: int = SHARED_FACT_MAX_LENGTH
    arm_identity: None = None

    def __post_init__(self) -> None:
        if self.page_capacity < 1 or self.max_pages < 1:
            raise ValueError("shared structured-output capacities must be positive")

    @property
    def schema(self) -> dict[str, Any]:
        return _finite_schema(self.page_capacity)

    @property
    def termination(self) -> str:
        return "empty_page_only"

    @property
    def continuation_prefix(self) -> str:
        return "ALREADY_RETURNED_EDGES"

    def continuation(self, returned: Sequence[Mapping[str, Any]]) -> str:
        canonical = [dict(edge) for edge in returned]
        return f"{self.continuation_prefix}: {json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(',', ':'))}"


@lru_cache(maxsize=64)
def finite_edge_page_model(
    page_capacity: int = SHARED_PAGE_CAPACITY,
    endpoint_names: tuple[str, ...] = (),
    fact_max_length: int = SHARED_FACT_MAX_LENGTH,
    name_prefix: str = "Shared",
    edge_name: str | None = None,
    page_name: str | None = None,
) -> Any:
    """Build the finite Pydantic wire model used by every formal arm."""

    from pydantic import ConfigDict, Field, create_model
    from typing import Literal

    names = tuple(dict.fromkeys(str(name) for name in endpoint_names if str(name).strip()))
    endpoint_type: Any = Literal.__getitem__(names) if names else str
    edge_name = edge_name or (
        f"{name_prefix}SingleEdge"
        if page_capacity == 1
        else f"{name_prefix}BoundedEdge{page_capacity}"
    )
    edge_model = create_model(
        edge_name,
        source_entity_name=(endpoint_type, Field(..., max_length=256)),
        target_entity_name=(endpoint_type, Field(..., max_length=256)),
        relation_type=(str, Field(..., min_length=1, max_length=128)),
        fact=(str, Field(..., min_length=1, max_length=fact_max_length)),
        valid_at=(str | None, Field(default=None, max_length=40)),
        invalid_at=(str | None, Field(default=None, max_length=40)),
        episode_indices=(list[Literal[0]], Field(default_factory=lambda: [0], min_length=1, max_length=1)),
        __config__=ConfigDict(extra="forbid"),
    )
    return create_model(
        page_name or (
            f"{name_prefix}SingleEdgePage"
            if page_capacity == 1
            else f"{name_prefix}BoundedEdgePage{page_capacity}"
        ),
        edges=(list[edge_model], Field(default_factory=list, max_length=page_capacity)),
        __config__=ConfigDict(extra="forbid"),
    )


def adapter_identity() -> dict[str, Any]:
    """Return source, schema, prompt, and policy identity for the substrate."""

    contract = SharedStructuredOutputContract()
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    schema_hash = hashlib.sha256(
        json.dumps(contract.schema, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    continuation_hash = hashlib.sha256(contract.continuation(()).encode()).hexdigest()
    return {
        "adapter_version": SHARED_ADAPTER_VERSION,
        "adapter_source_sha256": source_hash,
        "schema_sha256": schema_hash,
        "continuation_prompt_sha256": continuation_hash,
        "page_capacity": contract.page_capacity,
        "max_pages": contract.max_pages,
        "max_tokens": SHARED_MAX_TOKENS,
        "retry_policy": SHARED_RETRY_POLICY,
        "termination_policy": contract.termination,
        "arm_identity": None,
    }


def validate_edge_page(
    page: Mapping[str, Any],
    *,
    contract: SharedStructuredOutputContract,
    authoritative_entities: Sequence[str],
    reject_invalid_endpoints: bool = True,
) -> tuple[dict[str, Any], ...]:
    """Validate one provider page before merge; never salvage partial output."""

    if not isinstance(page, Mapping) or not isinstance(page.get("edges"), list):
        raise TypeError("bounded page response has no edges list")
    edges = page["edges"]
    if len(edges) > contract.page_capacity:
        raise PageCapExhausted("page cardinality exceeds bound")
    allowed = frozenset(" ".join(str(value).split()).casefold() for value in authoritative_entities)
    validated: list[dict[str, Any]] = []
    for raw in edges:
        if not isinstance(raw, Mapping):
            raise TypeError("bounded page contains a non-object edge")
        edge = dict(raw)
        source = " ".join(str(edge.get("source_entity_name", "")).split()).casefold()
        target = " ".join(str(edge.get("target_entity_name", "")).split()).casefold()
        if source not in allowed or target not in allowed:
            if reject_invalid_endpoints:
                raise ValueError("edge endpoint is outside authoritative entity set")
        if source == target:
            if reject_invalid_endpoints:
                raise ValueError("edge endpoints must be distinct")
        validated.append(edge)
    return tuple(validated)


class BoundedStructuredOutputAdapter:
    """Collect pages with endpoint grounding, stable dedupe and fail-closed bounds."""

    def __init__(
        self,
        *,
        contract: SharedStructuredOutputContract,
        page_fetcher: Callable[[str], EdgePage | Awaitable[EdgePage]],
        authoritative_entities: Sequence[str],
    ) -> None:
        self.contract = contract
        self.page_fetcher = page_fetcher
        self.authoritative_entities = frozenset(" ".join(str(value).split()).casefold() for value in authoritative_entities)
        if not self.authoritative_entities:
            raise ValueError("authoritative entity set cannot be empty")

    async def collect(self) -> CollectedEdges:
        accepted: list[Mapping[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        duplicate_recovery_used = False
        for page_index in range(self.contract.max_pages):
            continuation = self.contract.continuation(accepted)
            page = self.page_fetcher(continuation)
            if inspect.isawaitable(page):
                page = await page
            if not isinstance(page, EdgePage):
                raise TypeError("bounded page fetcher returned an invalid page")
            if len(page.edges) > self.contract.page_capacity:
                raise PageCapExhausted("page cardinality exceeds bound")
            if not page.edges:
                return CollectedEdges(tuple(accepted), "empty_page", page_index + 1)
            fresh: list[Mapping[str, Any]] = []
            for raw in page.edges:
                if not isinstance(raw, Mapping):
                    raise TypeError("bounded page contains a non-object edge")
                edge = dict(raw)
                source = " ".join(str(edge.get("source_entity_name", "")).split()).casefold()
                target = " ".join(str(edge.get("target_entity_name", "")).split()).casefold()
                if source not in self.authoritative_entities or target not in self.authoritative_entities:
                    raise ValueError("edge endpoint is outside authoritative entity set")
                if source == target:
                    raise ValueError("edge endpoints must be distinct")
                identity = canonical_edge_tuple(edge)
                if identity in seen:
                    continue
                seen.add(identity)
                fresh.append(edge)
            if not fresh:
                if duplicate_recovery_used:
                    raise PageCapExhausted("duplicate-only page after deterministic recovery")
                duplicate_recovery_used = True
                continue
            duplicate_recovery_used = False
            accepted.extend(fresh)
        raise PageCapExhausted("page bound exhausted before empty-page termination")


__all__ = [
    "BoundedStructuredOutputAdapter", "CollectedEdges", "EdgePage", "PageCapExhausted",
    "SharedStructuredOutputContract", "SHARED_ADAPTER_VERSION", "SHARED_MAX_TOKENS",
    "SHARED_PAGE_CAPACITY", "SHARED_MAX_PAGES", "SHARED_FACT_MAX_LENGTH",
    "adapter_identity", "canonical_edge_tuple", "finite_edge_page_model", "validate_edge_page",
]
