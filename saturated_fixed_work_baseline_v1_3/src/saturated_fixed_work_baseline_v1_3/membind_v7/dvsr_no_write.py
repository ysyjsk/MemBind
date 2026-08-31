"""Three-layer no-write evidence for DVSR speculative branches."""

from __future__ import annotations

from typing import Any


NO_WRITE_SCHEMA = "membind.dvsr.no-write-proof.v2"


def build_no_write_proof(
    *,
    api_write_count: int,
    shadow_publication_count: int,
    graph_projection_before_digest: str | None,
    graph_projection_after_digest: str | None,
) -> dict[str, Any]:
    for name, value in (
        ("api_write_count", api_write_count),
        ("shadow_publication_count", shadow_publication_count),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if not isinstance(graph_projection_before_digest, str) or not isinstance(graph_projection_after_digest, str):
        return {
            "schema_version": NO_WRITE_SCHEMA,
            "status": "UNKNOWN_INCOMPLETE_EVIDENCE",
            "api_write_count": api_write_count,
            "shadow_publication_count": shadow_publication_count,
            "state_projection_equal": None,
            "graph_projection_before_digest": graph_projection_before_digest,
            "graph_projection_after_digest": graph_projection_after_digest,
            "reasons": ["canonical_graph_projection_missing"],
        }
    equal = graph_projection_before_digest == graph_projection_after_digest
    reasons: list[str] = []
    if api_write_count:
        reasons.append("api_write_count_nonzero")
    if shadow_publication_count:
        reasons.append("shadow_publication_count_nonzero")
    if not equal:
        reasons.append("canonical_graph_projection_changed")
    return {
        "schema_version": NO_WRITE_SCHEMA,
        "status": "PASS" if not reasons else "FAIL",
        "api_write_count": api_write_count,
        "shadow_publication_count": shadow_publication_count,
        "state_projection_equal": equal,
        "graph_projection_before_digest": graph_projection_before_digest,
        "graph_projection_after_digest": graph_projection_after_digest,
        "reasons": reasons,
    }


__all__ = ["NO_WRITE_SCHEMA", "build_no_write_proof"]
