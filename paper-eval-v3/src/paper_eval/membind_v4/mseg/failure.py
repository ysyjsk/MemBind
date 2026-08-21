"""Provider-free failure causality evidence for the MEG runtime seam.

Failure records are an observability side-channel.  They never change the
exception raised by the production path and never infer missing lineage.
"""

from __future__ import annotations

import hashlib
import traceback
from dataclasses import asdict, dataclass
from typing import Any

from .version_token import MemoryVersionToken


OPAQUE = "OPAQUE"


def _optional_text(value: object | None) -> str:
    if value is None:
        return OPAQUE
    if not isinstance(value, str) or not value:
        raise ValueError("failure_text_invalid")
    return value


def _qualified(error: BaseException | None) -> str:
    if error is None:
        return OPAQUE
    kind = type(error)
    return f"{kind.__module__}.{kind.__qualname__}"


def _message(error: BaseException | None) -> str:
    return OPAQUE if error is None else (str(error) or OPAQUE)


def _chain(error: BaseException | None) -> tuple[dict[str, str], ...]:
    """Retain both explicit causes and implicit contexts without guessing."""

    result: list[dict[str, str]] = []
    seen: set[int] = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        result.append({"exception_type": _qualified(current), "exception_message": _message(current)})
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    return tuple(result)


def _traceback_hash(error: BaseException | None) -> str:
    if error is None:
        return OPAQUE
    rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    if not rendered:
        return OPAQUE
    return hashlib.sha256(rendered.encode("utf-8", "replace")).hexdigest()


def _epoch_value(value: object | None) -> int | str:
    if value is None:
        return OPAQUE
    snapshot = getattr(value, "snapshot", None)
    if callable(snapshot):
        value = snapshot()
    counter = getattr(value, "counter", None)
    if isinstance(counter, int) and counter >= 0:
        return counter
    if isinstance(value, int) and value >= 0:
        return value
    return OPAQUE


@dataclass(frozen=True, slots=True)
class SemanticFailureRecord:
    run_id: str
    source_sequence: int | str
    phase: str
    semantic_operator_id: str
    semantic_operator_type: str
    semantic_subrequest_role: str
    request_id: str
    exception_type: str
    exception_message: str
    exception_chain: tuple[dict[str, str], ...]
    root_exception_type: str
    root_exception_message: str
    parent_semantic_operator_id: str
    last_completed_semantic_predecessors: tuple[str, ...]
    memory_version_token: MemoryVersionToken | str
    mutation_epoch: int | str
    transaction_started: bool | str
    transaction_committed: bool | str
    persistent_effect_started: bool | str
    publication_started: bool | str
    traceback_hash: str
    implementation_seam_hash: str
    top_level_classification: str

    @classmethod
    def from_exception(
        cls,
        error: BaseException | None,
        *,
        run_id: str,
        source_sequence: int | None = None,
        phase: str = OPAQUE,
        semantic_operator_id: str | None = None,
        semantic_operator_type: str | None = None,
        semantic_subrequest_role: str | None = None,
        request_id: str | None = None,
        parent_semantic_operator_id: str | None = None,
        last_completed_semantic_predecessors: tuple[str, ...] = (),
        memory_version_token: MemoryVersionToken | None = None,
        mutation_epoch: object | None = None,
        transaction_started: bool | None = None,
        transaction_committed: bool | None = None,
        persistent_effect_started: bool | None = None,
        publication_started: bool | None = None,
        implementation_seam_hash: str | None = None,
        top_level_classification: str = OPAQUE,
    ) -> "SemanticFailureRecord":
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("failure_run_id_invalid")
        sequence: int | str = OPAQUE if source_sequence is None else source_sequence
        if not isinstance(sequence, (int, str)) or isinstance(sequence, bool):
            raise ValueError("failure_source_sequence_invalid")
        chain = _chain(error)
        root = error
        while root is not None and (root.__cause__ is not None or (root.__context__ is not None and not root.__suppress_context__)):
            root = root.__cause__ or root.__context__
        predecessors = tuple(last_completed_semantic_predecessors)
        if any(not isinstance(item, str) or not item for item in predecessors):
            raise ValueError("failure_predecessors_invalid")
        return cls(
            run_id=run_id,
            source_sequence=sequence,
            phase=_optional_text(phase),
            semantic_operator_id=_optional_text(semantic_operator_id),
            semantic_operator_type=_optional_text(semantic_operator_type),
            semantic_subrequest_role=_optional_text(semantic_subrequest_role),
            request_id=_optional_text(request_id),
            exception_type=_qualified(error),
            exception_message=_message(error),
            exception_chain=chain or ({"exception_type": OPAQUE, "exception_message": OPAQUE},),
            root_exception_type=_qualified(root),
            root_exception_message=_message(root),
            parent_semantic_operator_id=_optional_text(parent_semantic_operator_id),
            last_completed_semantic_predecessors=predecessors,
            memory_version_token=memory_version_token if memory_version_token is not None else OPAQUE,
            mutation_epoch=_epoch_value(mutation_epoch),
            transaction_started=OPAQUE if transaction_started is None else transaction_started,
            transaction_committed=OPAQUE if transaction_committed is None else transaction_committed,
            persistent_effect_started=OPAQUE if persistent_effect_started is None else persistent_effect_started,
            publication_started=OPAQUE if publication_started is None else publication_started,
            traceback_hash=_traceback_hash(error),
            implementation_seam_hash=_optional_text(implementation_seam_hash),
            top_level_classification=_optional_text(top_level_classification),
        )

    @property
    def causality_observable(self) -> bool:
        return self.root_exception_type != OPAQUE and self.root_exception_message != OPAQUE

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        token = value.get("memory_version_token")
        if isinstance(token, dict):
            value["memory_version_token"] = token
        return value


__all__ = ["OPAQUE", "SemanticFailureRecord"]
