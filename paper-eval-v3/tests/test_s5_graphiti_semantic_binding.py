"""TDD tests for the pinned Graphiti semantic-operation binding."""

from __future__ import annotations

import copy
import json
import types
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s5_graphiti_semantic_binding import (
    S5GraphitiSemanticBindingError,
    load_graphiti_semantic_binding,
    verify_graphiti_semantic_identity,
)


def _function(name: str, module: str):
    async def operation(*args, **kwargs):
        return (name, args, kwargs)

    operation.__name__ = name
    operation.__qualname__ = name
    operation.__module__ = module
    return operation


def _modules():
    node = types.SimpleNamespace(
        extract_nodes=_function("extract_nodes", "graphiti_core.utils.maintenance.node_operations"),
        resolve_extracted_nodes=_function("resolve_extracted_nodes", "graphiti_core.utils.maintenance.node_operations"),
        extract_attributes_from_nodes=_function("extract_attributes_from_nodes", "graphiti_core.utils.maintenance.node_operations"),
    )
    edge = types.SimpleNamespace(
        extract_edges=_function("extract_edges", "graphiti_core.utils.maintenance.edge_operations"),
        resolve_extracted_edges=_function("resolve_extracted_edges", "graphiti_core.utils.maintenance.edge_operations"),
    )
    bulk = types.SimpleNamespace(
        resolve_edge_pointers=_function("resolve_edge_pointers", "graphiti_core.utils.bulk_utils")
    )

    class Graphiti:
        async def _process_episode_data(self, *args, **kwargs):
            return args, kwargs

    Graphiti._process_episode_data.__module__ = "graphiti_core.graphiti"
    Graphiti._process_episode_data.__qualname__ = "Graphiti._process_episode_data"
    modules = {
        "graphiti_core.utils.maintenance.node_operations": node,
        "graphiti_core.utils.maintenance.edge_operations": edge,
        "graphiti_core.utils.bulk_utils": bulk,
        "graphiti_core.graphiti": types.SimpleNamespace(Graphiti=Graphiti),
    }
    return modules, Graphiti


def test_loader_binds_all_semantic_symbols_and_seals_signature_projection() -> None:
    modules, graphiti_type = _modules()
    binding = load_graphiti_semantic_binding(
        lambda name: modules[name],
        lambda: graphiti_type,
    )
    projection = binding.identity_projection()
    assert set(projection["qualnames"]) == {
        "extract_nodes",
        "resolve_extracted_nodes",
        "extract_attributes_from_nodes",
        "extract_edges",
        "resolve_extracted_edges",
        "resolve_edge_pointers",
        "process_episode_data",
    }
    assert projection["qualnames"]["process_episode_data"] == "Graphiti._process_episode_data"
    identity = {
        "identity_projection": projection,
        "identity_sha256": binding.identity_sha256(),
    }
    assert verify_graphiti_semantic_identity(identity) == identity


@pytest.mark.parametrize(
    "mutate",
    [
        lambda modules, _graphiti: setattr(
            modules["graphiti_core.utils.bulk_utils"].resolve_edge_pointers,
            "__module__",
            "legacy.bulk_utils",
        ),
        lambda modules, _graphiti: setattr(
            modules["graphiti_core.utils.maintenance.node_operations"].extract_nodes,
            "__qualname__",
            "legacy_extract_nodes",
        ),
        lambda _modules, graphiti: setattr(
            graphiti._process_episode_data,
            "__qualname__",
            "Graphiti.legacy_process",
        ),
    ],
)
def test_loader_fails_closed_on_symbol_drift(mutate) -> None:
    modules, graphiti_type = _modules()
    mutate(modules, graphiti_type)
    with pytest.raises(S5GraphitiSemanticBindingError):
        load_graphiti_semantic_binding(lambda name: modules[name], lambda: graphiti_type)


def test_identity_verifier_rejects_hash_or_private_projection_tampering() -> None:
    modules, graphiti_type = _modules()
    binding = load_graphiti_semantic_binding(lambda name: modules[name], lambda: graphiti_type)
    artifact = {
        "identity_projection": binding.identity_projection(),
        "identity_sha256": binding.identity_sha256(),
    }
    tampered = copy.deepcopy(artifact)
    tampered["identity_sha256"] = "0" * 64
    with pytest.raises(S5GraphitiSemanticBindingError, match="hash"):
        verify_graphiti_semantic_identity(tampered)

    private = copy.deepcopy(artifact)
    private["identity_projection"]["prompt"] = "forbidden"
    private["identity_sha256"] = binding.identity_sha256()
    with pytest.raises(S5GraphitiSemanticBindingError, match="private"):
        verify_graphiti_semantic_identity(private)


def test_loader_is_lazy_and_does_not_instantiate_graphiti() -> None:
    modules, graphiti_type = _modules()
    calls = []

    def class_loader():
        calls.append("class")
        return graphiti_type

    load_graphiti_semantic_binding(lambda name: modules[name], class_loader)
    assert calls == ["class"]


def test_persisted_local_identity_is_observed_only_and_hash_sealed() -> None:
    path = Path(__file__).resolve().parents[1] / "artifacts/paper_eval/native/S5_GRAPHITI_SEMANTIC_API_IDENTITY.json"
    artifact = json.loads(path.read_text(encoding="utf-8"))
    payload = {
        key: value for key, value in artifact.items() if key != "payload_sha256"
    }
    assert artifact["payload_sha256"] == payload_sha256(payload)
    assert artifact["status"] == "OBSERVED_PINNED_LOCAL_INSTALL_NOT_LIVE_AUTHORITY"
    assert artifact["graphiti_version"] == "0.29.3"
    assert artifact["graphiti_commit"] == "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"
    assert artifact["identity_sha256"] == payload_sha256(
        artifact["identity_projection"]
    )
    assert artifact["s5_live_execution_authorized"] is False
    assert artifact["neo4j_mutation_authorized"] is False
