"""Private semantic continuations with exact ReadView revalidation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum

from .read_view import ReadViewStatus, SemanticReadView


class SemanticContinuationError(ValueError):
    """A continuation is not private, complete, or correctly attributed."""


def _fail(code: str) -> SemanticContinuationError:
    return SemanticContinuationError(code)


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise _fail(code)
    return value


def _hash(value: object, code: str) -> str:
    selected = _text(value, code).lower()
    if _HEX64.fullmatch(selected) is None:
        raise _fail(code)
    return selected


@dataclass(frozen=True, slots=True)
class SemanticRequestIdentity:
    """Exact request, model, schema, and prompt identity."""

    rendered_request_hash: str
    model_id: str
    schema_hash: str
    prompt_name: str
    prompt_hash: str
    identity_hash: str

    def __post_init__(self) -> None:
        body = {
            "model_id": _text(self.model_id, "continuation_model_id_invalid"),
            "prompt_hash": _hash(
                self.prompt_hash, "continuation_prompt_hash_invalid"
            ),
            "prompt_name": _text(
                self.prompt_name, "continuation_prompt_name_invalid"
            ),
            "rendered_request_hash": _hash(
                self.rendered_request_hash, "rendered_request_hash_invalid"
            ),
            "schema_hash": _hash(
                self.schema_hash, "continuation_schema_hash_invalid"
            ),
        }
        expected = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if self.identity_hash != expected:
            raise _fail("continuation_request_identity_hash_mismatch")

    @classmethod
    def create(
        cls,
        *,
        rendered_request_hash: str,
        model_id: str,
        schema_hash: str,
        prompt_name: str,
        prompt_hash: str,
    ) -> "SemanticRequestIdentity":
        body = {
            "model_id": _text(model_id, "continuation_model_id_invalid"),
            "prompt_hash": _hash(prompt_hash, "continuation_prompt_hash_invalid"),
            "prompt_name": _text(prompt_name, "continuation_prompt_name_invalid"),
            "rendered_request_hash": _hash(
                rendered_request_hash, "rendered_request_hash_invalid"
            ),
            "schema_hash": _hash(schema_hash, "continuation_schema_hash_invalid"),
        }
        identity_hash = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(**body, identity_hash=identity_hash)


class ContinuationValidationStatus(str, Enum):
    VALIDATION_HIT = "VALIDATION_HIT"
    VALIDATION_MISS = "VALIDATION_MISS"
    OPAQUE = "OPAQUE"


@dataclass(frozen=True, slots=True)
class SemanticContinuation:
    operator_instance_id: str
    read_view_digest: str | None
    read_view_status: ReadViewStatus
    request_identity: SemanticRequestIdentity
    llm_output_hash: str
    deterministic_post_processing_identity: str
    descendant_operator_ids: tuple[str, ...]
    visibility: str = "PRIVATE"

    def __post_init__(self) -> None:
        operator = _text(
            self.operator_instance_id, "continuation_operator_id_invalid"
        )
        if not isinstance(self.read_view_status, ReadViewStatus):
            raise _fail("continuation_read_view_status_invalid")
        if self.read_view_status is ReadViewStatus.STABLE_READVIEW:
            _hash(self.read_view_digest, "continuation_read_view_digest_required")
        elif self.read_view_digest is not None:
            raise _fail("uncertified_continuation_has_read_view_digest")
        if not isinstance(self.request_identity, SemanticRequestIdentity):
            raise _fail("continuation_request_identity_invalid")
        _hash(self.llm_output_hash, "continuation_output_hash_invalid")
        _text(
            self.deterministic_post_processing_identity,
            "continuation_post_processing_identity_invalid",
        )
        if self.visibility != "PRIVATE":
            raise _fail("private_continuation_only")
        if not isinstance(self.descendant_operator_ids, tuple):
            raise _fail("continuation_descendants_invalid")
        descendants = tuple(
            _text(item, "continuation_descendant_id_invalid")
            for item in self.descendant_operator_ids
        )
        if len(descendants) != len(set(descendants)) or operator in descendants:
            raise _fail("continuation_descendants_invalid")

    @classmethod
    def create(
        cls,
        *,
        operator_instance_id: str,
        read_view: SemanticReadView,
        request_identity: SemanticRequestIdentity,
        llm_output_hash: str,
        deterministic_post_processing_identity: str,
        descendant_operator_ids: tuple[str, ...],
        persistent_write_intent: bool = False,
        publication_intent: bool = False,
    ) -> "SemanticContinuation":
        operator = _text(operator_instance_id, "continuation_operator_id_invalid")
        if not isinstance(read_view, SemanticReadView):
            raise _fail("continuation_read_view_invalid")
        if read_view.operator_instance_id != operator:
            raise _fail("continuation_read_view_operator_mismatch")
        if not isinstance(request_identity, SemanticRequestIdentity):
            raise _fail("continuation_request_identity_invalid")
        if not isinstance(persistent_write_intent, bool) or not isinstance(
            publication_intent, bool
        ):
            raise _fail("continuation_intent_invalid")
        if persistent_write_intent or publication_intent:
            raise _fail("private_continuation_only")
        if not isinstance(descendant_operator_ids, tuple):
            raise _fail("continuation_descendants_invalid")
        descendants = tuple(
            _text(item, "continuation_descendant_id_invalid")
            for item in descendant_operator_ids
        )
        if len(descendants) != len(set(descendants)) or operator in descendants:
            raise _fail("continuation_descendants_invalid")
        return cls(
            operator_instance_id=operator,
            read_view_digest=read_view.read_view_digest,
            read_view_status=read_view.status,
            request_identity=request_identity,
            llm_output_hash=_hash(llm_output_hash, "continuation_output_hash_invalid"),
            deterministic_post_processing_identity=_text(
                deterministic_post_processing_identity,
                "continuation_post_processing_identity_invalid",
            ),
            descendant_operator_ids=descendants,
        )


@dataclass(frozen=True, slots=True)
class ContinuationValidationResult:
    status: ContinuationValidationStatus
    stale_read_view_digest: str | None
    exact_read_view_digest: str | None
    invalidated_operator_ids: tuple[str, ...]
    independence_certified: bool
    codes: tuple[str, ...] = ()


def validate_semantic_continuation(
    continuation: SemanticContinuation,
    exact_read_view: SemanticReadView,
    *,
    effect_scopes_disjoint: bool = False,
) -> ContinuationValidationResult:
    """Compare exact semantic digests; effect disjointness grants no bypass."""

    if not isinstance(continuation, SemanticContinuation):
        raise _fail("continuation_invalid")
    if not isinstance(exact_read_view, SemanticReadView):
        raise _fail("exact_read_view_invalid")
    if not isinstance(effect_scopes_disjoint, bool):
        raise _fail("effect_disjoint_flag_invalid")
    if exact_read_view.operator_instance_id != continuation.operator_instance_id:
        raise _fail("exact_read_view_operator_mismatch")
    if (
        continuation.read_view_status is not ReadViewStatus.STABLE_READVIEW
        or exact_read_view.status is not ReadViewStatus.STABLE_READVIEW
        or continuation.read_view_digest is None
        or exact_read_view.read_view_digest is None
    ):
        codes = ["read_view_not_evaluable"]
        if effect_scopes_disjoint:
            codes.append("effect_disjointness_not_semantic_independence")
        return ContinuationValidationResult(
            status=ContinuationValidationStatus.OPAQUE,
            stale_read_view_digest=continuation.read_view_digest,
            exact_read_view_digest=exact_read_view.read_view_digest,
            invalidated_operator_ids=(),
            independence_certified=False,
            codes=tuple(codes),
        )
    if continuation.read_view_digest == exact_read_view.read_view_digest:
        return ContinuationValidationResult(
            status=ContinuationValidationStatus.VALIDATION_HIT,
            stale_read_view_digest=continuation.read_view_digest,
            exact_read_view_digest=exact_read_view.read_view_digest,
            invalidated_operator_ids=(),
            independence_certified=False,
        )
    codes = ["exact_semantic_read_view_changed"]
    if effect_scopes_disjoint:
        codes.append("effect_disjointness_not_semantic_independence")
    return ContinuationValidationResult(
        status=ContinuationValidationStatus.VALIDATION_MISS,
        stale_read_view_digest=continuation.read_view_digest,
        exact_read_view_digest=exact_read_view.read_view_digest,
        invalidated_operator_ids=(
            continuation.operator_instance_id,
            *continuation.descendant_operator_ids,
        ),
        independence_certified=False,
        codes=tuple(codes),
    )


__all__ = [
    "ContinuationValidationResult",
    "ContinuationValidationStatus",
    "SemanticContinuation",
    "SemanticContinuationError",
    "SemanticRequestIdentity",
    "validate_semantic_continuation",
]
