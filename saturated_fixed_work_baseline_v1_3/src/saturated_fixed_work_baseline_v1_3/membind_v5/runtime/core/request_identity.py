"""Exact logical request identity; prompt rendering remains Graphiti-owned."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ._canonical import canonical_json, freeze, thaw


def semantic_wire_hash(messages: Any, *, response_model: Any, max_tokens: int | None, transport_identity: Any) -> str:
    """Hash the client-finalizer input snapshot, not a second rendered prompt."""

    payload = {
        "messages": messages,
        "response_model": response_model,
        "max_tokens": max_tokens,
        "transport_identity": transport_identity,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    schema_version: str
    source_sequence: int
    callsite: str
    ordinal: int
    messages: tuple[Any, ...]
    response_model: Any
    max_tokens: int | None
    model_size: str | None
    group_id: str | None
    prompt_name: str | None
    flags: Any
    client_identity: Any
    transport_identity: Any
    cache_salt: str
    previous_context_digest: str
    digest: str

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_sequence": self.source_sequence,
            "callsite": self.callsite,
            "ordinal": self.ordinal,
            "messages": thaw(self.messages),
            "response_model": thaw(self.response_model),
            "max_tokens": self.max_tokens,
            "model_size": self.model_size,
            "group_id": self.group_id,
            "prompt_name": self.prompt_name,
            "flags": thaw(self.flags),
            "client_identity": thaw(self.client_identity),
            "transport_identity": thaw(self.transport_identity),
            "cache_salt": self.cache_salt,
            "previous_context_digest": self.previous_context_digest,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.payload(), "digest": self.digest}


def build_request_identity(
    *,
    source_sequence: int,
    callsite: str,
    ordinal: int,
    messages: Any,
    response_model: Any,
    max_tokens: int | None,
    model_size: str | None,
    group_id: str | None,
    prompt_name: str | None,
    flags: Any,
    client_identity: Any,
    transport_identity: Any,
    cache_salt: str,
    previous_context_digest: str,
    schema_version: str = "membind.v5.logical-request.v1",
) -> RequestIdentity:
    if isinstance(source_sequence, bool) or source_sequence < 0:
        raise ValueError("source_sequence must be a non-negative integer")
    if isinstance(ordinal, bool) or ordinal < 0:
        raise ValueError("ordinal must be a non-negative integer")
    frozen_messages = freeze(messages)
    frozen_model = freeze(response_model)
    frozen_flags = freeze(flags)
    frozen_client = freeze(client_identity)
    frozen_transport = freeze(transport_identity)
    payload = {
        "schema_version": schema_version,
        "source_sequence": int(source_sequence),
        "callsite": str(callsite),
        "ordinal": int(ordinal),
        "messages": thaw(frozen_messages),
        "response_model": thaw(frozen_model),
        "max_tokens": max_tokens,
        "model_size": model_size,
        "group_id": group_id,
        "prompt_name": prompt_name,
        "flags": thaw(frozen_flags),
        "client_identity": thaw(frozen_client),
        "transport_identity": thaw(frozen_transport),
        "cache_salt": str(cache_salt),
        "previous_context_digest": str(previous_context_digest),
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return RequestIdentity(
        schema_version=schema_version,
        source_sequence=int(source_sequence),
        callsite=str(callsite),
        ordinal=int(ordinal),
        messages=frozen_messages,
        response_model=frozen_model,
        max_tokens=max_tokens,
        model_size=model_size,
        group_id=group_id,
        prompt_name=prompt_name,
        flags=frozen_flags,
        client_identity=frozen_client,
        transport_identity=frozen_transport,
        cache_salt=str(cache_salt),
        previous_context_digest=str(previous_context_digest),
        digest=digest,
    )
