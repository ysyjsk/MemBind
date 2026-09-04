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

from .structured_output_recovery import (
    BOUNDED_JSON_WHITESPACE_MODE,
    bounded_ascii_pattern,
)

# The shared formal substrate uses finite pair tasks; the cursor collector
# below remains only as a bounded provider-free compatibility helper.
# Graphiti pins ``extract_edges.edge`` to a 16,384 completion-token request.
# The local 8B construction client remains configured for 32,768 tokens for
# non-edge operators; this lower bound is the wire budget of each shared edge
# page and is enforced at the callsite.
SHARED_MAX_TOKENS = 16_384
SHARED_CONSTRUCTION_MAX_TOKENS = 32_768
SHARED_PAGE_CAPACITY = 1
SHARED_MAX_PAGES = 64
SHARED_FACT_MAX_LENGTH = 1_900
SHARED_MAX_PAIRS_PER_TASK = 1
# Keep the finite task's worst-case wire witness below Graphiti's pinned
# 16,384 completion-token budget. A third relation is rejected explicitly by
# the semantic task validator rather than silently truncated.
SHARED_MAX_RELATIONS_PER_PAIR = 2
SHARED_FACT_PATTERN = bounded_ascii_pattern(1, SHARED_FACT_MAX_LENGTH)
SHARED_ADAPTER_VERSION = "shared-bounded-structured-output-v7-finite-pair-tasks"
SHARED_RETRY_POLICY = "single_attempt_finite_task_fail_closed_v1"
SHARED_TERMINAL_CONFIRMATION_POLICY = (
    "prohibited_terminal_only_success_v1"
)
SHARED_PROMPT_TEMPLATE = (
    "graphiti.extract_edges.edge|finite-pair-task-v1|"
    "declared-pairs|pair-acknowledgement|bounded-relations|fail-closed"
)


def bounded_ascii_field(
    default: Any,
    *,
    min_length: int,
    max_length: int,
    description: str | None = None,
) -> Any:
    """Apply semantic ASCII validation and the xgrammar-safe wire pattern."""

    from pydantic import Field

    return Field(
        default,
        min_length=min_length,
        max_length=max_length,
        pattern=r"^[\x00-\x7f]*$",
        description=description,
        json_schema_extra={
            "pattern": bounded_ascii_pattern(min_length, max_length),
        },
    )


def bounded_ascii_type(
    min_length: int,
    max_length: int,
) -> Any:
    """Return a string annotation whose union branch keeps the wire pattern."""

    from typing import Annotated

    return Annotated[
        str,
        bounded_ascii_field(
            ...,
            min_length=min_length,
            max_length=max_length,
        ),
    ]


class PageCapExhausted(RuntimeError):
    """The bounded page protocol cannot prove convergence or complete coverage."""


def canonical_edge_tuple(edge: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple("" if edge.get(key) is None else str(edge.get(key)) for key in (
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
    *,
    termination_discriminator: bool = False,
    excluded_edge: tuple[Any, ...] = (),
    no_additional_only: bool = False,
) -> dict[str, Any]:
    if page_capacity < 1:
        raise ValueError("page capacity must be positive")
    names = tuple(dict.fromkeys(str(name) for name in endpoint_names if str(name).strip()))
    endpoint = {
        "type": "string",
        "minLength": 1,
        "maxLength": 256,
        "pattern": bounded_ascii_pattern(1, 256),
    }
    if names:
        endpoint = {"type": "string", "enum": list(names)}
    edge = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source_entity_name": endpoint,
            "target_entity_name": endpoint,
            "relation_type": {
                "type": "string",
                "minLength": 1,
                "maxLength": 128,
                "pattern": bounded_ascii_pattern(1, 128),
            },
            "fact": {
                "type": "string",
                "minLength": 1,
                "maxLength": fact_max_length,
                "pattern": bounded_ascii_pattern(1, fact_max_length),
            },
            "valid_at": {
                "anyOf": [
                    {
                        "type": "string",
                        "maxLength": 40,
                        "pattern": bounded_ascii_pattern(0, 40),
                    },
                    {"type": "null"},
                ]
            },
            "invalid_at": {
                "anyOf": [
                    {
                        "type": "string",
                        "maxLength": 40,
                        "pattern": bounded_ascii_pattern(0, 40),
                    },
                    {"type": "null"},
                ]
            },
            "episode_indices": {
                "type": "array", "minItems": 1, "maxItems": 1,
                "items": {"type": "integer", "const": 0},
            },
        },
        "required": ["source_entity_name", "target_entity_name", "relation_type", "fact"],
    }
    if excluded_edge:
        fields = (
            "source_entity_name",
            "target_entity_name",
            "relation_type",
            "fact",
            "valid_at",
            "invalid_at",
        )
        edge["not"] = {
            "properties": {
                name: {"const": value}
                for name, value in zip(fields, excluded_edge, strict=True)
            },
            "required": list(fields),
        }
    if termination_discriminator:
        properties: dict[str, Any] = {
            "status": {"type": "string", "enum": (
                ["no_additional_edge"]
                if no_additional_only
                else ["new_edge", "no_additional_edge"]
            )},
            "edge": {"type": "null"} if no_additional_only else edge,
        }
        required = ["status", "edge"]
    else:
        properties = {
            "edges": {"type": "array", "minItems": 0, "maxItems": page_capacity, "items": edge}
        }
        required = ["edges"]
    return {
        "type": "object", "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


@dataclass(frozen=True, slots=True)
class SharedStructuredOutputContract:
    page_capacity: int = SHARED_PAGE_CAPACITY
    max_pages: int = SHARED_MAX_PAGES
    fact_max_length: int = SHARED_FACT_MAX_LENGTH
    max_pairs_per_task: int = SHARED_MAX_PAIRS_PER_TASK
    max_relations_per_pair: int = SHARED_MAX_RELATIONS_PER_PAIR
    arm_identity: None = None

    def __post_init__(self) -> None:
        if (
            self.page_capacity < 1
            or self.max_pages < 1
            or self.max_pairs_per_task < 1
            or self.max_relations_per_pair < 1
        ):
            raise ValueError("shared structured-output capacities must be positive")

    @property
    def schema(self) -> dict[str, Any]:
        return finite_edge_task_model(
            max_pairs_per_task=self.max_pairs_per_task,
            max_relations_per_pair=self.max_relations_per_pair,
            fact_max_length=self.fact_max_length,
        ).model_json_schema()

    @property
    def termination(self) -> str:
        return "declared_pair_task_completion"

    @property
    def continuation_prefix(self) -> str:
        return "EDGE_CURSOR"

    def continuation(self, returned: Sequence[Mapping[str, Any]]) -> str:
        if not returned:
            return f"{self.continuation_prefix}: null"
        cursor = {
            field: returned[-1].get(field)
            for field in (
                "source_entity_name",
                "target_entity_name",
                "relation_type",
                "fact",
                "valid_at",
                "invalid_at",
            )
        }
        return (
            f"{self.continuation_prefix}: "
            + json.dumps(
                cursor,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )


@lru_cache(maxsize=64)
def finite_edge_page_model(
    page_capacity: int = SHARED_PAGE_CAPACITY,
    endpoint_names: tuple[str, ...] = (),
    fact_max_length: int = SHARED_FACT_MAX_LENGTH,
    name_prefix: str = "Shared",
    edge_name: str | None = None,
    page_name: str | None = None,
    termination_discriminator: bool = False,
    excluded_edge: tuple[Any, ...] = (),
    no_additional_only: bool = False,
) -> Any:
    """Build the finite Pydantic wire model used by every formal arm."""

    from pydantic import ConfigDict, Field, create_model
    from typing import Literal, Union

    names = tuple(dict.fromkeys(str(name) for name in endpoint_names if str(name).strip()))
    endpoint_type: Any = Literal.__getitem__(names) if names else str
    datetime_type = bounded_ascii_type(0, 40)
    edge_name = edge_name or (
        f"{name_prefix}SingleEdge"
        if page_capacity == 1
        else f"{name_prefix}BoundedEdge{page_capacity}"
    )
    edge_config = ConfigDict(extra="forbid")
    if excluded_edge:
        fields = (
            "source_entity_name",
            "target_entity_name",
            "relation_type",
            "fact",
            "valid_at",
            "invalid_at",
        )
        edge_config = ConfigDict(
            extra="forbid",
            json_schema_extra={
                "not": {
                    "properties": {
                        name: {"const": value}
                        for name, value in zip(fields, excluded_edge, strict=True)
                    },
                    "required": list(fields),
                }
            },
        )
    edge_model = create_model(
        edge_name,
        source_entity_name=(
            endpoint_type,
            (
                Field(...)
                if names
                else bounded_ascii_field(
                    ...,
                    min_length=1,
                    max_length=256,
                )
            ),
        ),
        target_entity_name=(
            endpoint_type,
            (
                Field(...)
                if names
                else bounded_ascii_field(
                    ...,
                    min_length=1,
                    max_length=256,
                )
            ),
        ),
        relation_type=(
            str,
            bounded_ascii_field(
                ...,
                min_length=1,
                max_length=128,
            ),
        ),
        fact=(
            str,
            bounded_ascii_field(
                ...,
                min_length=1,
                max_length=fact_max_length,
            ),
        ),
        valid_at=(
            datetime_type | None,
            Field(... if excluded_edge else None),
        ),
        invalid_at=(
            datetime_type | None,
            Field(... if excluded_edge else None),
        ),
        episode_indices=(list[Literal[0]], Field(... if excluded_edge else [0], min_length=1, max_length=1)),
        __config__=edge_config,
    )
    if termination_discriminator:
        fields: dict[str, Any] = {
            "status": (
                Literal["no_additional_edge"]
                if no_additional_only
                else Literal["new_edge", "no_additional_edge"],
                ...,
            ),
            "edge": ((type(None) if no_additional_only else edge_model | None), ...),
        }
    else:
        fields = {
            "edges": (
                list[edge_model],
                Field(default_factory=list, max_length=page_capacity),
            )
        }
    return create_model(
        page_name or (
            f"{name_prefix}RecoveryEdgePage"
            if termination_discriminator
            else f"{name_prefix}SingleEdgePage"
            if page_capacity == 1
            else f"{name_prefix}BoundedEdgePage{page_capacity}"
        ),
        **fields,
        __config__=ConfigDict(extra="forbid"),
    )


@lru_cache(maxsize=64)
def finite_edge_task_model(
    max_pairs_per_task: int = SHARED_MAX_PAIRS_PER_TASK,
    max_relations_per_pair: int = SHARED_MAX_RELATIONS_PER_PAIR,
    endpoint_names: tuple[str, ...] = (),
    pair_tuples: tuple[tuple[str, str], ...] = (),
    fact_max_length: int = SHARED_FACT_MAX_LENGTH,
    name_prefix: str = "MemBind",
) -> Any:
    """Build the finite pair-task response model.

    ``pairs_completed`` is intentionally a string list rather than an inferred
    property of ``edges``: an empty relation set must still acknowledge its
    declared pair.  The model bounds physical output; semantic cap handling is
    performed by ``validate_edge_task_result``.
    """

    if max_pairs_per_task < 1 or max_relations_per_pair < 1:
        raise ValueError("edge task bounds must be positive")
    names = tuple(dict.fromkeys(str(name) for name in endpoint_names if str(name).strip()))
    endpoint_type: Any
    from typing import Literal, Union

    endpoint_type = Literal.__getitem__(names) if names else str
    edge_model = finite_edge_page_model(
        max(1, max_pairs_per_task * max_relations_per_pair),
        names,
        fact_max_length,
        name_prefix=f"{name_prefix}Task",
        edge_name=f"{name_prefix}TaskEdge{max_pairs_per_task}_{max_relations_per_pair}_{len(names)}",
        page_name=f"{name_prefix}TaskEdgePage{max_pairs_per_task}_{max_relations_per_pair}_{len(names)}",
    ).model_fields["edges"].annotation.__args__[0]
    from pydantic import ConfigDict, Field, create_model

    # Endpoint enums alone still permit a cross-pair combination when a task
    # batches overlapping pairs (for example A-B and A-C).  Build a compact
    # union of direction-specific edge models so xgrammar rejects B-C before
    # the provider can emit it.  Runtime validation remains authoritative.
    normalized_pairs = tuple(
        dict.fromkeys(
            (str(left).strip(), str(right).strip())
            for left, right in pair_tuples
            if str(left).strip() and str(right).strip() and str(left).strip() != str(right).strip()
        )
    )
    if normalized_pairs:
        edge_fields = {
            field_name: (field.annotation, field)
            for field_name, field in edge_model.model_fields.items()
        }
        direction_models = []
        for pair_index, (left, right) in enumerate(normalized_pairs):
            for direction, (source, target) in enumerate(((left, right), (right, left))):
                direction_fields = dict(edge_fields)
                direction_fields["source_entity_name"] = (
                    Literal.__getitem__((source,)),
                    Field(...),
                )
                direction_fields["target_entity_name"] = (
                    Literal.__getitem__((target,)),
                    Field(...),
                )
                direction_models.append(
                    create_model(
                        f"{name_prefix}TaskPair{pair_index}_{direction}",
                        **direction_fields,
                        __config__=ConfigDict(extra="forbid"),
                    )
                )
        edge_model = Union.__getitem__(tuple(direction_models))

    # The acknowledgement is part of the finite task domain, not free-form
    # model text.  Bind it to exact pair IDs whenever this concrete task has
    # declared pairs; otherwise keep the arm-agnostic template bounded.
    pair_ids = tuple(f"{left}||{right}" for left, right in normalized_pairs)
    pair_id_type = (
        Literal.__getitem__(pair_ids)
        if pair_ids
        else bounded_ascii_type(1, 600)
    )
    return create_model(
        f"{name_prefix}FiniteEdgeTask{max_pairs_per_task}_{max_relations_per_pair}_{len(names)}",
        status=(Literal["complete"], ...),
        pairs_completed=(list[pair_id_type], Field(..., min_length=1, max_length=max_pairs_per_task)),
        # The empty relation set is valid, but the field itself must be present.
        # A default would make xgrammar omit it, leaving pair acknowledgement
        # ambiguous at the wire boundary.
        edges=(list[edge_model], Field(..., min_length=0, max_length=max_pairs_per_task * max_relations_per_pair)),
        __config__=ConfigDict(extra="forbid"),
    )


def adapter_identity(
    endpoint_names: Sequence[str] = (),
    *,
    page_capacity: int = SHARED_PAGE_CAPACITY,
    fact_max_length: int = SHARED_FACT_MAX_LENGTH,
    recovery: bool = False,
    excluded_edge: tuple[Any, ...] = (),
    no_additional_only: bool = False,
    cursor_protocol: bool = False,
) -> dict[str, Any]:
    """Return source, schema, prompt, and policy identity for the substrate.

    ``schema_template_sha256`` is stable across all requests and arms.  When
    endpoint grounding is active, ``schema_sha256`` additionally identifies
    the concrete enum schema for that evidence block; it is deliberately not
    used as the cross-arm method identity because entity sets vary by source.
    """

    contract = SharedStructuredOutputContract(
        page_capacity=int(page_capacity), fact_max_length=int(fact_max_length)
    )
    normalized_endpoints = tuple(
        dict.fromkeys(str(name).strip() for name in endpoint_names if str(name).strip())
    )
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    def _identity_schema(
        names: tuple[str, ...],
        active_exclusion: tuple[Any, ...] = (),
    ) -> dict[str, Any]:
        if not cursor_protocol and not recovery and not no_additional_only:
            return finite_edge_task_model(
                max_pairs_per_task=contract.max_pairs_per_task,
                max_relations_per_pair=contract.max_relations_per_pair,
                endpoint_names=names,
                fact_max_length=contract.fact_max_length,
            ).model_json_schema()
        if names:
            return finite_edge_page_model(
                contract.page_capacity,
                names,
                contract.fact_max_length,
                name_prefix="MemBindEndpointGrounded",
                edge_name=(
                    f"MemBindEndpointGroundedEdge{contract.page_capacity}_{len(names)}"
                ),
                page_name=(
                    f"MemBindEndpointGroundedRecoveryEdgePage{contract.page_capacity}_{len(names)}"
                    if recovery
                    else f"MemBindEndpointGroundedEdgePage{contract.page_capacity}_{len(names)}"
                ),
                termination_discriminator=bool(cursor_protocol or recovery),
                excluded_edge=active_exclusion,
                no_additional_only=bool(no_additional_only),
            ).model_json_schema()
        return finite_edge_page_model(
            contract.page_capacity,
            (),
            contract.fact_max_length,
            name_prefix="MemBind",
            termination_discriminator=bool(cursor_protocol or recovery),
            excluded_edge=active_exclusion,
            no_additional_only=bool(no_additional_only),
        ).model_json_schema()

    schema_template = _identity_schema(())
    schema_template_hash = hashlib.sha256(
        json.dumps(schema_template, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    concrete_schema = _identity_schema(normalized_endpoints, excluded_edge)
    schema_hash = hashlib.sha256(
        json.dumps(concrete_schema, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    continuation_hash = hashlib.sha256(
        (
            contract.continuation(())
            if cursor_protocol or recovery
            else SHARED_PROMPT_TEMPLATE
        ).encode("utf-8")
    ).hexdigest()
    prompt_hash = hashlib.sha256(SHARED_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()
    return {
        "adapter_version": SHARED_ADAPTER_VERSION,
        "adapter_source_sha256": source_hash,
        "schema_scope": "endpoint_grounded_concrete" if normalized_endpoints else "template",
        "schema_template_sha256": schema_template_hash,
        "schema_sha256": schema_hash,
        "continuation_prompt_sha256": continuation_hash,
        "prompt_template_sha256": prompt_hash,
        "page_capacity": contract.page_capacity,
        "max_pages": contract.max_pages,
        "max_pages_semantics": "finite_task_count",
        "total_page_cap": 1,
        "max_pairs_per_task": contract.max_pairs_per_task,
        "max_relations_per_pair": contract.max_relations_per_pair,
        "saturation_policy": "declared_pair_task_completion_or_fail_closed_v1",
        "cursor_exclusion_policy": "prohibited_in_formal_path",
        "cursor_exclusion_history_size": 0,
        "fact_character_policy": "official_history_ascii_xgrammar_finite_quantifier_v2",
        "json_whitespace_mode": BOUNDED_JSON_WHITESPACE_MODE,
        "json_whitespace_authority": (
            "authenticated_platform_manifest_process_contract_v1"
        ),
        "json_separators": [", ", ": "],
        "physical_serialization_bound": True,
        "wire_max_tokens": SHARED_MAX_TOKENS,
        "construction_request_max_tokens": SHARED_CONSTRUCTION_MAX_TOKENS,
        # ``max_tokens`` is retained as a compatibility alias for consumers
        # that already expect the page-level bound.
        "max_tokens": SHARED_MAX_TOKENS,
        "retry_policy": SHARED_RETRY_POLICY,
        "terminal_confirmation_policy": SHARED_TERMINAL_CONFIRMATION_POLICY,
        "terminal_confirmation_is_context_retry": False,
        "terminal_only_success_allowed": False,
        "edge_task_protocol": "finite_pair_task_v1",
        "termination_policy": contract.termination,
        "endpoint_names": list(normalized_endpoints) if normalized_endpoints else None,
        "response_variant": (
            "duplicate_recovery_final_abstention"
            if recovery and not cursor_protocol and no_additional_only
            else "duplicate_recovery"
            if recovery and not cursor_protocol
            else "legacy_cursor_for_offline_compatibility_only"
            if cursor_protocol
            else "finite_pair_task"
        ),
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
        # A provider may emit a malformed self-edge after otherwise valid
        # candidates.  Reject that candidate without discarding the entire
        # bounded page; the accepted tuple set remains canonical and the
        # continuation asks for further unseen edges.  Unknown endpoints stay
        # fatal because they indicate a grounding breach rather than a local
        # semantic candidate error.
        if source == target:
            if reject_invalid_endpoints:
                continue
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
        cursor: tuple[str, ...] | None = None
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
                return CollectedEdges(
                    tuple(accepted),
                    "explicit_cursor_exhaustion",
                    page_index + 1,
                )
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
                    # Ignore a malformed self-edge candidate while preserving
                    # the rest of this bounded page for canonical progress.
                    continue
                identity = canonical_edge_tuple(edge)
                if cursor is not None and identity <= cursor:
                    raise PageCapExhausted(
                        "edge cursor response is not a strict canonical successor"
                    )
                cursor = identity
                if identity in seen:
                    raise PageCapExhausted(
                        "edge cursor response is not a strict canonical successor"
                    )
                seen.add(identity)
                fresh.append(edge)
            if not fresh:
                raise PageCapExhausted(
                    "edge cursor response made no strict canonical progress"
                )
            accepted.extend(fresh)
        raise PageCapExhausted(
            "bounded edge task exhausted its finite page budget without proof of completion"
        )


__all__ = [
    "BoundedStructuredOutputAdapter", "CollectedEdges", "EdgePage", "PageCapExhausted",
    "SharedStructuredOutputContract", "SHARED_ADAPTER_VERSION", "SHARED_MAX_TOKENS",
    "SHARED_PAGE_CAPACITY", "SHARED_MAX_PAGES", "SHARED_FACT_MAX_LENGTH",
    "SHARED_FACT_PATTERN", "bounded_ascii_field", "bounded_ascii_pattern", "bounded_ascii_type",
    "SHARED_CONSTRUCTION_MAX_TOKENS", "SHARED_PROMPT_TEMPLATE",
    "SHARED_MAX_PAIRS_PER_TASK", "SHARED_MAX_RELATIONS_PER_PAIR",
    "adapter_identity", "canonical_edge_tuple", "finite_edge_page_model", "finite_edge_task_model", "validate_edge_page",
]
