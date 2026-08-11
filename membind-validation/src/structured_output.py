"""Protocol-specific JSON-schema constraints for single-episode ingestion."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Sequence


_JSON_OBJECT_SCHEMA_PREAMBLE = (
    "\n\nRespond with a JSON object in the following format:\n\n"
)


def constrain_single_episode_indices(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy where every episode_indices array is exactly ``[0]``.

    M0, M1, and M2 all submit one current episode per extraction call. Graphiti's
    Pydantic description already says the single-episode value is ``[0]``, but
    its generated JSON schema leaves the integer array unbounded. Constrained
    decoding can otherwise produce an infinite 0,1,2,... sequence.
    """
    constrained = deepcopy(schema)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "episode_indices" and isinstance(child, dict):
                    child["type"] = "array"
                    child["minItems"] = 1
                    child["maxItems"] = 1
                    child["items"] = {"type": "integer", "const": 0}
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(constrained)
    return constrained


def replace_json_object_schema_injection(
    messages: Sequence[Any], response_model: Any
) -> dict[str, Any]:
    """Replace Graphiti's raw JSON-object schema with the effective C2 schema.

    Graphiti 0.29.3 appends the raw Pydantic schema in ``generate_response``
    before calling the provider-specific transport method.  C2 must keep that
    public prompt path while constraining every single-episode index to ``[0]``.
    The replacement is idempotent because the transport may be retried.
    """

    if not messages:
        raise ValueError("json_object schema injection requires a message")
    final = messages[-1]
    role = final.get("role") if isinstance(final, dict) else getattr(final, "role", None)
    content = (
        final.get("content")
        if isinstance(final, dict)
        else getattr(final, "content", None)
    )
    if role != "user" or not isinstance(content, str):
        raise ValueError("json_object schema injection requires a final user message")

    upstream_schema = response_model.model_json_schema()
    effective_schema = constrain_single_episode_indices(upstream_schema)
    upstream_suffix = _JSON_OBJECT_SCHEMA_PREAMBLE + json.dumps(upstream_schema)
    effective_json = json.dumps(
        effective_schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    effective_suffix = _JSON_OBJECT_SCHEMA_PREAMBLE + effective_json
    if content.endswith(effective_suffix):
        return effective_schema
    if not content.endswith(upstream_suffix):
        raise ValueError("upstream json_object schema injection is missing")

    replacement = content[: -len(upstream_suffix)] + effective_suffix
    if isinstance(final, dict):
        final["content"] = replacement
    else:
        final.content = replacement
    return effective_schema
