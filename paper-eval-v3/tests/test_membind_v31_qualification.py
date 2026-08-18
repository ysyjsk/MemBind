"""Pinned Graphiti offline qualification test for the v3.1 State-Cut."""

from __future__ import annotations

import asyncio
from pathlib import Path

from paper_eval.membind_v31.qualification import qualify_graphiti_v0293_state_cut


def test_real_pinned_extractors_qualify_without_mutable_state_capability() -> None:
    result = asyncio.run(
        qualify_graphiti_v0293_state_cut(
            project_root=Path(__file__).resolve().parents[2],
        )
    )

    assert result.certification.operator_names == (
        "graphiti.extract_edges",
        "graphiti.extract_nodes",
    )
    assert result.document["status"] == "PASS"
    assert result.document["graphiti_version"] == "0.29.3"
    assert result.document["qualification_trace"]["persistent_state_read_count"] == 0
    assert result.document["qualification_trace"]["persistent_state_write_count"] == 0
    assert result.document["qualification_trace"]["undeclared_state_facing_call_count"] == 0
    assert result.document["qualification_trace"]["raw_node_count"] == 2
    assert result.document["qualification_trace"]["raw_edge_count"] == 1
    assert "bounded private" not in repr(result.document)
