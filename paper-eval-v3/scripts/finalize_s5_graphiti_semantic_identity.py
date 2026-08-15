#!/usr/bin/env python3
"""Persist the observed pinned Graphiti semantic-API identity.

This script is read-only with respect to Graphiti and writes one new artifact
under the isolated paper-eval-v3 lane.  It never constructs Graphiti, reads
environment secrets, opens Neo4j, or grants a live authority.
"""

from __future__ import annotations

import inspect
import json
from importlib.metadata import version
from pathlib import Path

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file
from paper_eval.s5_graphiti_semantic_binding import load_graphiti_semantic_binding


PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / "artifacts/paper_eval/native/S5_GRAPHITI_SEMANTIC_API_IDENTITY.json"
EXPECTED_VERSION = "0.29.3"
EXPECTED_COMMIT = "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"


def main() -> None:
    if OUTPUT.exists():
        raise RuntimeError(f"refusing to overwrite sealed artifact: {OUTPUT}")
    installed = version("graphiti-core")
    if installed != EXPECTED_VERSION:
        raise RuntimeError(f"Graphiti version drift: {installed}")
    binding = load_graphiti_semantic_binding()
    source_files: dict[str, str] = {}
    for name in (
        "extract_nodes",
        "resolve_extracted_nodes",
        "extract_attributes_from_nodes",
        "extract_edges",
        "resolve_extracted_edges",
        "resolve_edge_pointers",
        "process_episode_data",
    ):
        path = inspect.getsourcefile(getattr(binding, name))
        if path is None:
            raise RuntimeError(f"source file missing for {name}")
        source_files[name] = sha256_file(Path(path))
    payload = {
        "schema_version": "membind.paper-eval-v3.s5-graphiti-semantic-identity.v1",
        "status": "OBSERVED_PINNED_LOCAL_INSTALL_NOT_LIVE_AUTHORITY",
        "graphiti_version": installed,
        "graphiti_commit": EXPECTED_COMMIT,
        "identity_projection": binding.identity_projection(),
        "identity_sha256": binding.identity_sha256(),
        "source_file_sha256": source_files,
        "model_call_authorized": False,
        "neo4j_read_authorized": False,
        "neo4j_mutation_authorized": False,
        "s5_live_execution_authorized": False,
        "current_stage_pointer_update_authorized": False,
    }
    payload["payload_sha256"] = payload_sha256(
        {key: value for key, value in payload.items() if key != "payload_sha256"}
    )
    atomic_write_json(OUTPUT, payload)
    print(json.dumps({"output": str(OUTPUT), "payload_sha256": payload["payload_sha256"], "identity_sha256": binding.identity_sha256()}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
