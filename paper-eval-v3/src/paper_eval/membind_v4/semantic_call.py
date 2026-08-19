"""Exact semantic identity for MemBind v4 NodeResolve calls.

This module contains no Graphiti, transport, or database code.  A semantic
call is a content-safe, deterministic description of the request and all
state-derived inputs that can affect Graphiti's NodeResolve result.  The
memory version is provenance only and is intentionally excluded from the
fingerprint; it is checked separately when pairing stale and exact calls.

``NO_LLM`` is represented explicitly.  It never receives a fabricated empty
prompt/token digest: prompt identity fields must be ``None`` in that mode.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from paper_eval.artifacts import canonical_bytes, payload_sha256


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA = "membind.paper-eval-v4.semantic-call.v1"
_FINGERPRINT_SCHEMA = "membind.paper-eval-v4.semantic-call-fingerprint.v1"
_EXECUTION_MODES = {"LLM", "NO_LLM"}


class SemanticCallError(ValueError):
    """A semantic request is malformed or cannot be reused safely."""


def _fail(code: str) -> SemanticCallError:
    return SemanticCallError(code)


def _nonnegative(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _mapping(value: object, code: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    try:
        decoded = json.loads(canonical_bytes(dict(value)).decode("utf-8"))
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(decoded, dict):
        raise _fail(code)
    return decoded


def _mapping_sequence(value: object, code: str) -> tuple[dict[str, object], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    result: list[dict[str, object]] = []
    for item in value:
        result.append(_mapping(item, code))
    return tuple(result)


def _candidate_ids(value: object, code: str) -> tuple[str | int, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    result: list[str | int] = []
    for item in value:
        if isinstance(item, bool) or not (
            (isinstance(item, int) and item >= 0)
            or (isinstance(item, str) and bool(item))
        ):
            raise _fail(code)
        result.append(item)
    if len(set(result)) != len(result):
        raise _fail(code)
    return tuple(result)


def _string_sequence(value: object, code: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    result = tuple(value)
    if any(not isinstance(item, str) or not item for item in result):
        raise _fail(code)
    return result


@dataclass(frozen=True, slots=True)
class SemanticCall:
    """Content-safe identity for one state-bound NodeResolve semantic call."""

    source_sequence: int
    state_version: int
    operator_identity: dict[str, object]
    model_identity: dict[str, object]
    decoding_identity: dict[str, object]
    response_schema: dict[str, object]
    rendered_request_sha256: str | None
    token_sequence_sha256: str | None
    prompt_tokens: int | None
    extracted_nodes: tuple[dict[str, object], ...]
    candidate_order: tuple[str | int, ...]
    candidate_bindings: tuple[dict[str, object], ...]
    previous_episodes: tuple[dict[str, object], ...]
    episode_context: dict[str, object]
    entity_types: tuple[str, ...]
    execution_mode: str
    operator_revision: str

    @classmethod
    def create(
        cls,
        *,
        source_sequence: int,
        state_version: int,
        operator_identity: Mapping[str, object],
        model_identity: Mapping[str, object],
        decoding_identity: Mapping[str, object],
        response_schema: Mapping[str, object],
        rendered_request_sha256: str | None = None,
        token_sequence_sha256: str | None = None,
        prompt_tokens: int | None = None,
        extracted_nodes: Sequence[Mapping[str, object]] | None = None,
        extracted_node_mapping: Sequence[Mapping[str, object]] | None = None,
        candidate_order: Sequence[str | int] = (),
        candidate_bindings: Sequence[Mapping[str, object]] = (),
        previous_episodes: Sequence[Mapping[str, object]] = (),
        episode_context: Mapping[str, object] | None = None,
        entity_types: Sequence[str] = (),
        execution_mode: str = "LLM",
        operator_revision: str = "graphiti-node-resolve-v4-1",
    ) -> "SemanticCall":
        if extracted_nodes is not None and extracted_node_mapping is not None:
            raise _fail("extracted_nodes_alias_conflict")
        nodes_value = extracted_nodes if extracted_nodes is not None else extracted_node_mapping
        if nodes_value is None:
            raise _fail("extracted_nodes_invalid")
        mode = execution_mode.upper() if isinstance(execution_mode, str) else execution_mode
        if mode not in _EXECUTION_MODES:
            raise _fail("execution_mode_invalid")
        if not isinstance(operator_revision, str) or not operator_revision:
            raise _fail("operator_revision_invalid")
        rendered: str | None
        token: str | None
        prompt: int | None
        if mode == "NO_LLM":
            if rendered_request_sha256 is not None or token_sequence_sha256 is not None:
                raise _fail("no_llm_prompt_hash_forbidden")
            if prompt_tokens is not None:
                raise _fail("no_llm_prompt_tokens_forbidden")
            rendered = token = prompt = None
        else:
            rendered = _sha(rendered_request_sha256, "llm_prompt_hash_required")
            token = _sha(token_sequence_sha256, "llm_token_hash_required")
            prompt = _nonnegative(prompt_tokens, "llm_prompt_tokens_required")
        order = _candidate_ids(candidate_order, "candidate_order_invalid")
        bindings = _mapping_sequence(candidate_bindings, "candidate_bindings_invalid")
        if tuple(item.get("candidate_id") for item in bindings) != order:
            raise _fail("candidate_binding_order_mismatch")
        for binding in bindings:
            candidate_id = binding.get("candidate_id")
            if isinstance(candidate_id, bool) or not (
                (isinstance(candidate_id, int) and candidate_id >= 0)
                or (isinstance(candidate_id, str) and bool(candidate_id))
            ):
                raise _fail("candidate_binding_invalid")
            if not isinstance(binding.get("uuid"), str) or not binding["uuid"]:
                raise _fail("candidate_binding_invalid")
            projection = binding.get("projection", binding.get("canonical_projection"))
            if not isinstance(projection, Mapping):
                raise _fail("candidate_binding_invalid")
        return cls(
            source_sequence=_nonnegative(source_sequence, "source_sequence_invalid"),
            state_version=_nonnegative(state_version, "state_version_invalid"),
            operator_identity=_mapping(operator_identity, "operator_identity_invalid"),
            model_identity=_mapping(model_identity, "model_identity_invalid"),
            decoding_identity=_mapping(decoding_identity, "decoding_identity_invalid"),
            response_schema=_mapping(response_schema, "response_schema_invalid"),
            rendered_request_sha256=rendered,
            token_sequence_sha256=token,
            prompt_tokens=prompt,
            extracted_nodes=_mapping_sequence(nodes_value, "extracted_nodes_invalid"),
            candidate_order=order,
            candidate_bindings=bindings,
            previous_episodes=_mapping_sequence(previous_episodes, "previous_episodes_invalid"),
            episode_context=_mapping({} if episode_context is None else episode_context, "episode_context_invalid"),
            entity_types=_string_sequence(entity_types, "entity_types_invalid"),
            execution_mode=mode,
            operator_revision=operator_revision,
        )

    def fingerprint_payload(self) -> dict[str, object]:
        """Return every semantic input except source/version provenance."""

        return {
            "schema_version": _FINGERPRINT_SCHEMA,
            "operator_identity": self.operator_identity,
            "model_identity": self.model_identity,
            "decoding_identity": self.decoding_identity,
            "response_schema": self.response_schema,
            "rendered_request_sha256": self.rendered_request_sha256,
            "token_sequence_sha256": self.token_sequence_sha256,
            "prompt_tokens": self.prompt_tokens,
            "extracted_nodes": list(self.extracted_nodes),
            "candidate_order": list(self.candidate_order),
            "candidate_bindings": list(self.candidate_bindings),
            "previous_episodes": list(self.previous_episodes),
            "episode_context": self.episode_context,
            "entity_types": list(self.entity_types),
            "execution_mode": self.execution_mode,
            "operator_revision": self.operator_revision,
        }

    @property
    def fingerprint(self) -> str:
        return payload_sha256(self.fingerprint_payload())

    def to_record(self) -> dict[str, object]:
        record = self.fingerprint_payload()
        record["schema_version"] = _SCHEMA
        record["source_sequence"] = self.source_sequence
        record["state_version"] = self.state_version
        record["fingerprint"] = self.fingerprint
        return record

    def verify(self) -> "SemanticCall":
        if self.execution_mode not in _EXECUTION_MODES:
            raise _fail("execution_mode_invalid")
        # Re-run all field checks and canonicalize the value before trusting it.
        recreated = self.create(
            source_sequence=self.source_sequence,
            state_version=self.state_version,
            operator_identity=self.operator_identity,
            model_identity=self.model_identity,
            decoding_identity=self.decoding_identity,
            response_schema=self.response_schema,
            rendered_request_sha256=self.rendered_request_sha256,
            token_sequence_sha256=self.token_sequence_sha256,
            prompt_tokens=self.prompt_tokens,
            extracted_nodes=self.extracted_nodes,
            candidate_order=self.candidate_order,
            candidate_bindings=self.candidate_bindings,
            previous_episodes=self.previous_episodes,
            episode_context=self.episode_context,
            entity_types=self.entity_types,
            execution_mode=self.execution_mode,
            operator_revision=self.operator_revision,
        )
        if recreated != self:
            raise _fail("semantic_call_not_canonical")
        return self

    @classmethod
    def from_record(cls, value: Mapping[str, object]) -> "SemanticCall":
        if not isinstance(value, Mapping) or value.get("schema_version") != _SCHEMA:
            raise _fail("semantic_call_schema_invalid")
        try:
            selected = cls.create(
                source_sequence=value["source_sequence"],
                state_version=value["state_version"],
                operator_identity=value["operator_identity"],
                model_identity=value["model_identity"],
                decoding_identity=value["decoding_identity"],
                response_schema=value["response_schema"],
                rendered_request_sha256=value["rendered_request_sha256"],
                token_sequence_sha256=value["token_sequence_sha256"],
                prompt_tokens=value["prompt_tokens"],
                extracted_nodes=value["extracted_nodes"],
                candidate_order=value["candidate_order"],
                candidate_bindings=value["candidate_bindings"],
                previous_episodes=value["previous_episodes"],
                episode_context=value["episode_context"],
                entity_types=value["entity_types"],
                execution_mode=value["execution_mode"],
                operator_revision=value["operator_revision"],
            )
        except KeyError:
            raise _fail("semantic_call_field_missing") from None
        if value.get("fingerprint") != selected.fingerprint:
            raise _fail("fingerprint_mismatch")
        return selected


@dataclass(frozen=True, slots=True)
class SemanticCallDecision:
    decision: str
    reason: str
    speculative_fingerprint: str
    exact_fingerprint: str


def validate_semantic_call_pair(
    speculative: SemanticCall, exact: SemanticCall
) -> SemanticCallDecision:
    """Return REUSE only for exact semantic identity; otherwise fail closed."""

    if not isinstance(speculative, SemanticCall) or not isinstance(exact, SemanticCall):
        raise _fail("semantic_call_type_invalid")
    speculative.verify()
    exact.verify()
    if speculative.source_sequence != exact.source_sequence:
        raise _fail("source_sequence_mismatch")
    if speculative.state_version >= exact.state_version:
        raise _fail("state_order_invalid")
    decision = "REUSE" if speculative.fingerprint == exact.fingerprint else "REEXECUTE"
    reason = (
        "SEMANTIC_CALL_FINGERPRINT_MATCH"
        if decision == "REUSE"
        else "SEMANTIC_CALL_FINGERPRINT_MISMATCH"
    )
    return SemanticCallDecision(
        decision=decision,
        reason=reason,
        speculative_fingerprint=speculative.fingerprint,
        exact_fingerprint=exact.fingerprint,
    )


def semantic_call_fingerprint(call: SemanticCall) -> str:
    """Return a verified fingerprint without exposing prompt contents."""

    if not isinstance(call, SemanticCall):
        raise _fail("semantic_call_type_invalid")
    call.verify()
    return call.fingerprint


# Familiar names used by the v3.1 prototype are exported as compatibility
# aliases, while the implementation remains v4-local and behaviorally strict.
validate_speculation = validate_semantic_call_pair
SpeculationDecision = SemanticCallDecision


__all__ = [
    "SemanticCall",
    "SemanticCallDecision",
    "SemanticCallError",
    "SpeculationDecision",
    "semantic_call_fingerprint",
    "validate_semantic_call_pair",
    "validate_speculation",
]
