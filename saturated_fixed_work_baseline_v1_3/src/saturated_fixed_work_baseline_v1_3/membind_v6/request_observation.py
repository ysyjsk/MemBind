"""Strict request-stability observations for the first V6 probe.

The public observation is digest-only.  A complete identity payload is kept
only in an explicitly private, mode-0600 capture when a live probe opts in.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..membind_v5.runtime.core._canonical import canonical_json


class RequestObservationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RequestObservation:
    private_payload: Mapping[str, Any]
    public_summary: Mapping[str, Any]


_CATEGORIES = {
    "messages": "prompt_formatting",
    "response_model": "schema_or_tools",
    "transport_identity": "client_or_transport_config",
    "client_identity": "client_or_transport_config",
    "model_size": "client_or_transport_config",
    "max_tokens": "client_or_transport_config",
    "flags": "client_or_transport_config",
    "previous_context_digest": "graph_state_or_version",
    "group_id": "graph_state_or_version",
    "cache_salt": "cache_policy",
    "source_sequence": "call_identity",
    "callsite": "call_identity",
    "ordinal": "call_identity",
    "prompt_name": "call_identity",
}


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def observe_request_identity(identity: Any) -> RequestObservation:
    payload_fn = getattr(identity, "payload", None)
    if not callable(payload_fn):
        raise RequestObservationError("request identity payload is unavailable")
    payload = payload_fn()
    if not isinstance(payload, Mapping):
        raise RequestObservationError("request identity payload must be a mapping")
    payload = dict(payload)
    digest = str(getattr(identity, "digest", ""))
    if not digest:
        digest = _digest(payload)
    fields = {
        key: _digest(payload.get(key))
        for key in payload
        if key != "digest"
    }
    public = {
        "schema_version": str(payload.get("schema_version", "")),
        "source_sequence": payload.get("source_sequence"),
        "callsite": payload.get("callsite"),
        "ordinal": payload.get("ordinal"),
        "digest": digest,
        "field_digests": fields,
    }
    return RequestObservation(private_payload=payload, public_summary=public)


def compare_request_observations(left: RequestObservation, right: RequestObservation) -> dict[str, Any]:
    keys = sorted(set(left.private_payload) | set(right.private_payload))
    changed = [key for key in keys if left.private_payload.get(key) != right.private_payload.get(key)]
    categories = sorted({_CATEGORIES.get(key, "unknown") for key in changed})
    return {"match": not changed, "changed_fields": changed, "categories": categories}


def write_private_request_capture(path: str | Path, observations: list[RequestObservation]) -> Path:
    target = Path(path)
    if target.exists():
        raise RequestObservationError("private capture path already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            for observation in observations:
                stream.write(json.dumps(dict(observation.private_payload), ensure_ascii=True, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    return target


__all__ = [
    "RequestObservation",
    "RequestObservationError",
    "compare_request_observations",
    "observe_request_identity",
    "write_private_request_capture",
]
