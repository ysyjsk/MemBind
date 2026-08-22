"""Revision-pinned dependency and source-closure contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class OperatorContract:
    name: str
    reads_memory: frozenset[str] = frozenset()
    writes_memory: frozenset[str] = frozenset()
    local_effects: frozenset[str] = frozenset()
    oracle_effects: frozenset[str] = frozenset()
    inputs: frozenset[str] = frozenset()
    control_dependencies: frozenset[str] = frozenset()
    bindable: bool = False
    certified: bool = False
    exception_policy: str = "abort_without_publication"

    @property
    def classification(self) -> str:
        return "HOISTABLE" if self.is_hoistable else "OPAQUE"

    @property
    def is_hoistable(self) -> bool:
        return (
            not self.reads_memory
            and not self.writes_memory
            and bool(self.oracle_effects)
            and self.inputs <= {"source", "config", "source_prefix", "oracle_output"}
            and self.control_dependencies <= {"source", "config", "source_prefix"}
            and self.bindable
            and self.certified
            and self.exception_policy == "abort_without_publication"
        )


@dataclass(frozen=True, slots=True)
class HoistCertificate:
    schema_version: str
    graphiti_revision: str
    membind_revision: str
    certified_operators: tuple[str, ...]
    assumptions: tuple[str, ...]
    source_hashes: Mapping[str, str]
    abort_policy: str = "wasted_preparation_no_publication"

    @classmethod
    def from_contracts(
        cls,
        contracts: Iterable[OperatorContract],
        *,
        graphiti_revision: str = "v0.29.3/021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        membind_revision: str = "c4c9577208ab41d1cd148778e0a6eab4daafe6ac",
        source_hashes: Mapping[str, str] | None = None,
    ) -> "HoistCertificate":
        selected = tuple(contracts)
        invalid = [contract.name for contract in selected if not contract.is_hoistable]
        if invalid:
            raise ValueError(f"not hoistable: {','.join(invalid)}")
        return cls(
            schema_version="membind.v5.hoist-certificate.v1",
            graphiti_revision=graphiti_revision,
            membind_revision=membind_revision,
            certified_operators=tuple(contract.name for contract in selected),
            assumptions=(
                "fresh_isolated_namespace",
                "single_writer",
                "frozen_source_prefix",
                "native_callsite_exact_binding",
                "local_effects_non_escape",
                "normal_control_source_closed",
            ),
            source_hashes=dict(source_hashes or {}),
        )

    def validate(self) -> None:
        if not self.certified_operators:
            raise ValueError("certificate has no certified operator")
        if self.abort_policy != "wasted_preparation_no_publication":
            raise ValueError("unsupported abort policy")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "graphiti_revision": self.graphiti_revision,
            "membind_revision": self.membind_revision,
            "certified_operators": list(self.certified_operators),
            "assumptions": list(self.assumptions),
            "source_hashes": dict(self.source_hashes),
            "abort_policy": self.abort_policy,
        }

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PreviousSourceProjector:
    """Reconstruct native previous-source context from a frozen prefix."""

    def __init__(
        self,
        sources: Iterable[Mapping[str, Any]],
        *,
        limit: int = 10,
        stable_tie_breaker: Callable[[Mapping[str, Any]], Any] | None = lambda row: row.get("sequence"),
        group_id: str | None = None,
        source: str | None = None,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        self._sources = tuple(dict(row) for row in sources)
        self.limit = limit
        self.stable_tie_breaker = stable_tie_breaker
        self.group_id = group_id
        self.source = source

    @staticmethod
    def _time(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        raise ValueError("source valid_at is not datetime/ISO")

    def project(self, *, sequence: int, valid_at: datetime, group_id: str | None = None, source: str | None = None) -> tuple[dict[str, Any], ...]:
        candidates = []
        for row in self._sources:
            row_sequence = row.get("sequence")
            if not isinstance(row_sequence, int) or row_sequence >= sequence:
                continue
            if group_id is not None and row.get("group_id", group_id) != group_id:
                continue
            if source is not None and row.get("source", source) != source:
                continue
            if self._time(row["valid_at"]) > valid_at:
                continue
            candidates.append(row)
        candidates.sort(key=lambda row: self._time(row["valid_at"]), reverse=True)
        for left, right in zip(candidates, candidates[1:]):
            if self._time(left["valid_at"]) == self._time(right["valid_at"]):
                if self.stable_tie_breaker is None:
                    raise ValueError("timestamp tie cannot be deterministically ordered")
                candidates.sort(key=lambda row: (self._time(row["valid_at"]), self.stable_tie_breaker(row)), reverse=True)
                break
        return tuple(dict(row) for row in candidates[: self.limit])

