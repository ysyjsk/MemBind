"""Pinned Graphiti 0.29.3 source/callsite adapter.

This module is the only V5 location allowed to know Graphiti module paths.  The
core runtime receives typed contracts and never imports the upstream package.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.contracts import HoistCertificate, OperatorContract


EXPECTED_GRAPHITI_VERSION = "0.29.3"
EXPECTED_GRAPHITI_COMMIT = "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"


@dataclass(frozen=True, slots=True)
class Graphiti0293Adapter:
    graphiti_source: Path
    node_source: Path
    edge_source: Path
    package_version: str
    source_hashes: dict[str, str]
    callsites: dict[str, tuple[str, ...]]

    @classmethod
    def inspect_installed(cls) -> "Graphiti0293Adapter":
        package = importlib.import_module("graphiti_core")
        graphiti_module = importlib.import_module("graphiti_core.graphiti")
        node_module = importlib.import_module("graphiti_core.utils.maintenance.node_operations")
        edge_module = importlib.import_module("graphiti_core.utils.maintenance.edge_operations")
        try:
            from importlib.metadata import version

            package_version = version("graphiti-core")
        except Exception:
            package_version = "unknown"
        paths = {
            "graphiti.py": Path(inspect.getsourcefile(graphiti_module) or ""),
            "node_operations.py": Path(inspect.getsourcefile(node_module) or ""),
            "edge_operations.py": Path(inspect.getsourcefile(edge_module) or ""),
        }
        if any(not path.is_file() for path in paths.values()):
            raise RuntimeError("Graphiti source files are not readable")
        callsites = {
            key: tuple(_call_names(path.read_text(encoding="utf-8")))
            for key, path in paths.items()
        }
        hashes = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items()}
        return cls(paths["graphiti.py"], paths["node_operations.py"], paths["edge_operations.py"], package_version, hashes, callsites)

    def assert_expected(self, *, expected_version: str = EXPECTED_GRAPHITI_VERSION) -> None:
        if self.package_version != expected_version:
            raise RuntimeError(f"Graphiti version mismatch: expected {expected_version}, got {self.package_version}")
        for label in ("graphiti.py", "node_operations.py", "edge_operations.py"):
            if not self.source_hashes.get(label):
                raise RuntimeError(f"missing Graphiti source hash: {label}")

    def certified_contracts(self) -> tuple[OperatorContract, ...]:
        return (
            OperatorContract(
                name="extract_nodes.generate_response",
                oracle_effects=frozenset({"llm"}),
                inputs=frozenset({"source", "source_prefix", "config"}),
                control_dependencies=frozenset({"source", "config", "source_prefix"}),
                bindable=True,
                certified=True,
            ),
            OperatorContract(
                name="extract_edges.generate_response",
                oracle_effects=frozenset({"llm"}),
                inputs=frozenset({"source", "source_prefix", "config", "oracle_output"}),
                control_dependencies=frozenset({"source", "config", "source_prefix"}),
                bindable=True,
                certified=True,
            ),
        )

    def opaque_contracts(self) -> tuple[OperatorContract, ...]:
        return (
            OperatorContract(
                name="resolve_extracted_nodes",
                reads_memory=frozenset({"neo4j"}),
                inputs=frozenset({"derived_memory", "extracted_nodes"}),
            ),
            OperatorContract(
                name="extract_attributes_from_nodes",
                reads_memory=frozenset({"derived_memory"}),
                oracle_effects=frozenset({"llm"}),
                inputs=frozenset({"derived_memory", "nodes"}),
            ),
            OperatorContract(
                name="process_episode_data",
                writes_memory=frozenset({"neo4j"}),
                inputs=frozenset({"derived_memory", "nodes", "edges"}),
            ),
        )

    def certificate(self) -> HoistCertificate:
        certificate = HoistCertificate.from_contracts(
            self.certified_contracts(),
            source_hashes=self.source_hashes,
        )
        certificate.validate()
        return certificate

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "membind.v5.graphiti-0293-adapter.v1",
            "package_version": self.package_version,
            "expected_commit": EXPECTED_GRAPHITI_COMMIT,
            "source_files": {key: str(path) for key, path in (("graphiti.py", self.graphiti_source), ("node_operations.py", self.node_source), ("edge_operations.py", self.edge_source))},
            "source_hashes": dict(self.source_hashes),
            "callsites": {key: list(value) for key, value in self.callsites.items()},
            "certified": [contract.name for contract in self.certified_contracts()],
            "opaque": [contract.name for contract in self.opaque_contracts()],
            "certificate_digest": self.certificate().digest(),
        }


def _call_names(path_text: str) -> list[str]:
    tree = ast.parse(path_text)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else "<dynamic>"
            if target in {"extract_nodes", "extract_edges", "generate_response", "resolve_extracted_nodes", "process_episode_data"}:
                names.append(target)
    return sorted(set(names))
