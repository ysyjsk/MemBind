"""Pinned Graphiti semantic-operation binding for the M* production path.

The loader is lazy and side-effect free.  It binds the exact Graphiti 0.29.3
node/edge extraction, resolution, attribute, pointer, and commit symbols used
by the future M* adapter, and records their public qualnames/signatures for an
identity hash.  It does not construct Graphiti or contact Neo4j.
"""

from __future__ import annotations

import importlib
import inspect
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .artifacts import payload_sha256


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_FIELDS = {"api_key", "password", "prompt", "raw_output", "response", "secret"}
_SYMBOLS = (
    "extract_nodes",
    "resolve_extracted_nodes",
    "extract_attributes_from_nodes",
    "extract_edges",
    "resolve_extracted_edges",
    "resolve_edge_pointers",
    "process_episode_data",
)


class S5GraphitiSemanticBindingError(ValueError):
    """Stable fail-closed semantic binding error."""


def _fail(code: str) -> S5GraphitiSemanticBindingError:
    return S5GraphitiSemanticBindingError(code)


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_binding_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


def _callable(value: object, *, name: str, module: str, qualname: str) -> Callable[..., Any]:
    if not callable(value):
        raise _fail(f"{name}_not_callable")
    if getattr(value, "__module__", None) != module:
        raise _fail(f"{name}_module_drift")
    if getattr(value, "__qualname__", None) != qualname:
        raise _fail(f"{name}_qualname_drift")
    return value


@dataclass(frozen=True)
class S5GraphitiSemanticBinding:
    """Exact callable set for Graphiti extraction/resolution/commit."""

    extract_nodes: Callable[..., Any]
    resolve_extracted_nodes: Callable[..., Any]
    extract_attributes_from_nodes: Callable[..., Any]
    extract_edges: Callable[..., Any]
    resolve_extracted_edges: Callable[..., Any]
    resolve_edge_pointers: Callable[..., Any]
    process_episode_data: Callable[..., Any]
    loader_verified: bool = False

    def signature_projection(self) -> dict[str, str]:
        projection = {
            name: str(inspect.signature(getattr(self, name)))
            for name in _SYMBOLS
        }
        _assert_public(projection)
        return projection

    def identity_projection(self) -> dict[str, object]:
        projection = {
            "module_paths": {
                "extract_nodes": "graphiti_core.utils.maintenance.node_operations",
                "resolve_extracted_nodes": "graphiti_core.utils.maintenance.node_operations",
                "extract_attributes_from_nodes": "graphiti_core.utils.maintenance.node_operations",
                "extract_edges": "graphiti_core.utils.maintenance.edge_operations",
                "resolve_extracted_edges": "graphiti_core.utils.maintenance.edge_operations",
                "resolve_edge_pointers": "graphiti_core.utils.bulk_utils",
                "process_episode_data": "graphiti_core.graphiti.Graphiti",
            },
            "qualnames": {
                name: getattr(getattr(self, name), "__qualname__", "")
                for name in _SYMBOLS
            },
            "signatures": self.signature_projection(),
        }
        _assert_public(projection)
        return projection

    def identity_sha256(self) -> str:
        return payload_sha256(self.identity_projection())


def load_graphiti_semantic_binding(
    module_loader: Callable[[str], object] = importlib.import_module,
    graphiti_class_loader: Callable[[], object] | None = None,
) -> S5GraphitiSemanticBinding:
    """Load and validate only the pinned Graphiti semantic symbols."""

    if not callable(module_loader):
        raise _fail("module_loader_not_callable")
    try:
        node_module = module_loader("graphiti_core.utils.maintenance.node_operations")
        edge_module = module_loader("graphiti_core.utils.maintenance.edge_operations")
        bulk_module = module_loader("graphiti_core.utils.bulk_utils")
    except Exception:
        raise _fail("graphiti_semantic_module_import_failed") from None
    if graphiti_class_loader is None:
        def graphiti_class_loader() -> object:
            module = module_loader("graphiti_core.graphiti")
            return getattr(module, "Graphiti")
    try:
        graphiti_type = graphiti_class_loader()
    except Exception:
        raise _fail("graphiti_class_import_failed") from None

    bound = {
        "extract_nodes": _callable(
            getattr(node_module, "extract_nodes", None),
            name="extract_nodes",
            module="graphiti_core.utils.maintenance.node_operations",
            qualname="extract_nodes",
        ),
        "resolve_extracted_nodes": _callable(
            getattr(node_module, "resolve_extracted_nodes", None),
            name="resolve_extracted_nodes",
            module="graphiti_core.utils.maintenance.node_operations",
            qualname="resolve_extracted_nodes",
        ),
        "extract_attributes_from_nodes": _callable(
            getattr(node_module, "extract_attributes_from_nodes", None),
            name="extract_attributes_from_nodes",
            module="graphiti_core.utils.maintenance.node_operations",
            qualname="extract_attributes_from_nodes",
        ),
        "extract_edges": _callable(
            getattr(edge_module, "extract_edges", None),
            name="extract_edges",
            module="graphiti_core.utils.maintenance.edge_operations",
            qualname="extract_edges",
        ),
        "resolve_extracted_edges": _callable(
            getattr(edge_module, "resolve_extracted_edges", None),
            name="resolve_extracted_edges",
            module="graphiti_core.utils.maintenance.edge_operations",
            qualname="resolve_extracted_edges",
        ),
        "resolve_edge_pointers": _callable(
            getattr(bulk_module, "resolve_edge_pointers", None),
            name="resolve_edge_pointers",
            module="graphiti_core.utils.bulk_utils",
            qualname="resolve_edge_pointers",
        ),
    }
    process = getattr(graphiti_type, "_process_episode_data", None)
    bound["process_episode_data"] = _callable(
        process,
        name="process_episode_data",
        module="graphiti_core.graphiti",
        qualname="Graphiti._process_episode_data",
    )
    return S5GraphitiSemanticBinding(**bound, loader_verified=True)


def verify_graphiti_semantic_identity(value: Mapping[str, object]) -> dict[str, object]:
    """Verify a previously recorded semantic identity projection."""

    if not isinstance(value, Mapping):
        raise _fail("semantic_identity_not_mapping")
    identity = deepcopy(dict(value))
    _assert_public(identity)
    if set(identity) != {"identity_projection", "identity_sha256"}:
        raise _fail("semantic_identity_shape_invalid")
    projection = identity.get("identity_projection")
    if not isinstance(projection, Mapping):
        raise _fail("semantic_identity_projection_invalid")
    if not isinstance(identity.get("identity_sha256"), str) or _SHA256.fullmatch(
        identity["identity_sha256"]
    ) is None:
        raise _fail("semantic_identity_hash_invalid")
    if identity["identity_sha256"] != payload_sha256(projection):
        raise _fail("semantic_identity_hash_mismatch")
    return identity


__all__ = [
    "S5GraphitiSemanticBinding",
    "S5GraphitiSemanticBindingError",
    "load_graphiti_semantic_binding",
    "verify_graphiti_semantic_identity",
]
