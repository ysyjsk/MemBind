"""Protocol-specific JSON-schema constraints for single-episode ingestion."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


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
