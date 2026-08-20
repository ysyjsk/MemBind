from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


NOT_OBSERVABLE = "NOT_OBSERVABLE"


class DependencyKind(str, Enum):
    DATA = "DATA"
    STATE = "STATE"
    CONTROL = "CONTROL"
    PUBLICATION = "PUBLICATION"
    UNKNOWN_DEPENDENCY = "UNKNOWN_DEPENDENCY"


@dataclass(frozen=True, slots=True)
class DAGEdge:
    predecessor: str
    successor: str
    kind: DependencyKind
    evidence: str

    def __post_init__(self) -> None:
        if not self.predecessor or not self.successor:
            raise ValueError("dependency_identity_invalid")
        if self.predecessor == self.successor:
            raise ValueError("dependency_self_edge")
        if not isinstance(self.kind, DependencyKind):
            raise ValueError("dependency_kind_invalid")
        if not self.evidence:
            raise ValueError("dependency_evidence_missing")


@dataclass(frozen=True, slots=True)
class RequestRecord:
    request_id: str
    stream_id: str
    source_sequence: int
    request_kind: str
    operator_role: str
    operator_id: str
    parent_bind_id: str | None
    parent_operator_id: str | None
    operator_phase: str
    submitted_ns: int
    started_ns: int
    terminal_ns: int
    service_duration_ns: int
    token_count: int | str
    prompt_tokens: int | str
    completion_tokens: int | str
    execution_mode: str
    persistent_state_access_class: str

    def __post_init__(self) -> None:
        if not self.request_id or not self.stream_id:
            raise ValueError("request_identity_invalid")
        if self.source_sequence < 0:
            raise ValueError("source_sequence_invalid")
        for field in ("submitted_ns", "started_ns", "terminal_ns", "service_duration_ns"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field}_invalid")
        if self.started_ns < self.submitted_ns:
            raise ValueError("start_before_submission")
        if self.terminal_ns < self.started_ns:
            raise ValueError("terminal_before_start")
        if not self.request_kind or not self.operator_role:
            raise ValueError("request_metadata_invalid")


@dataclass(frozen=True, slots=True)
class PublicationRecord:
    source_sequence: int
    arrival_ns: int
    publication_ns: int

    def __post_init__(self) -> None:
        if self.source_sequence < 0:
            raise ValueError("publication_source_invalid")
        if (
            isinstance(self.arrival_ns, bool)
            or not isinstance(self.arrival_ns, int)
            or self.arrival_ns < 0
        ):
            raise ValueError("arrival_ns_invalid")
        if (
            isinstance(self.publication_ns, bool)
            or not isinstance(self.publication_ns, int)
            or self.publication_ns < self.arrival_ns
        ):
            raise ValueError("publication_ns_invalid")


@dataclass(frozen=True, slots=True)
class TraceBundle:
    history_id: str
    requests: tuple[RequestRecord, ...]
    publications: tuple[PublicationRecord, ...]
    configured_k: int
    source_count: int
    input_paths: tuple[str, ...]
    observability: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.history_id:
            raise ValueError("history_id_invalid")
        if self.configured_k <= 0:
            raise ValueError("configured_k_invalid")
        if self.source_count < 0:
            raise ValueError("source_count_invalid")
        ids = [request.request_id for request in self.requests]
        if len(ids) != len(set(ids)):
            raise ValueError("request_id_duplicate")

    @property
    def request_by_id(self) -> dict[str, RequestRecord]:
        return {request.request_id: request for request in self.requests}

    @property
    def publication_by_source(self) -> dict[int, PublicationRecord]:
        return {record.source_sequence: record for record in self.publications}


@dataclass(frozen=True, slots=True)
class ReplayResult:
    policy: str
    request_count: int
    request_start_ns: dict[str, int]
    request_terminal_ns: dict[str, int]
    request_start_order: tuple[str, ...]
    publication_ns: dict[int, int]
    freshness_ns: dict[int, int]
    makespan_ns: int
    goodput_episodes_per_second: float | None
    max_active_count: int
    request_service_duration_ns: dict[str, int]
    extra_llm_calls: int
    extra_input_tokens: int
    speculative_waste: int
    scheduler_choice_count: int
    criticality_inversion_count: int
    max_legal_choice_width: int
    multi_choice_duration_ns: int
    decision_points: tuple[dict[str, Any], ...]
    actual_publication_delta_ns: dict[int, int]
