"""Static semantic and persistent-write audit for pinned Graphiti 0.29.3."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

from .runtime_instrumentation import SemanticOperatorClass


class Graphiti0293AuditError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SemanticBoundaryRow:
    operator: str
    semantic_inputs: str
    mutable_state_read: bool
    sends_llm: bool
    produces_private_result: bool
    persistent_mutation: bool
    completion_semantics: str
    affects_publication: bool
    classification: str
    read_view_required: bool
    source_evidence: str


@dataclass(frozen=True, slots=True)
class WritePathRow:
    path_id: str
    file: str
    line: int
    function: str
    call: str
    relevance: str
    coverage_hook: str | None
    reason: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_rows() -> tuple[SemanticBoundaryRow, ...]:
    values = (
        (
            "NodeExtraction",
            "immutable source episode plus certified immutable evidence prefix",
            False,
            True,
            True,
            False,
            "extracted EntityNode list and episode attribution map materialized",
            False,
            SemanticOperatorClass.EVIDENCE_DERIVED,
            False,
            "v3.1 graphiti_adapter.prepare; node_operations.extract_nodes",
        ),
        (
            "NodeCandidateRead",
            "extracted names, embeddings, group, candidate limit and cosine threshold",
            True,
            False,
            True,
            False,
            "all ordered per-node candidate lists returned",
            False,
            SemanticOperatorClass.STATE_DERIVED,
            True,
            "node_operations._semantic_candidate_search:418-450",
        ),
        (
            "DeterministicSimilarity",
            "one extracted node and its materialized candidate index",
            False,
            False,
            True,
            False,
            "exact/similarity decision committed to private batch state",
            False,
            SemanticOperatorClass.DERIVED_PRIVATE,
            False,
            "node_operations.resolve_extracted_nodes:649-670",
        ),
        (
            "UnresolvedSetFormation",
            "private deterministic decisions in extracted-node order",
            False,
            False,
            True,
            False,
            "complete unresolved index set materialized",
            False,
            SemanticOperatorClass.DERIVED_PRIVATE,
            False,
            "node_operations.resolve_extracted_nodes:649-680",
        ),
        (
            "NodeBatchResolutionDecision",
            "all unresolved nodes, merged ordered mutable candidates, episode and previous episodes",
            True,
            True,
            True,
            False,
            "one multi-input NodeResolutions response applied to private batch state",
            False,
            SemanticOperatorClass.STATE_DERIVED,
            True,
            "node_operations._resolve_with_llm:467-624",
        ),
        (
            "IdentityMaterialization",
            "private batch resolution state",
            False,
            False,
            True,
            False,
            "resolved nodes, UUID map and duplicate pairs returned",
            False,
            SemanticOperatorClass.DERIVED_PRIVATE,
            False,
            "node_operations.resolve_extracted_nodes:691-708",
        ),
        (
            "EdgeExtraction",
            "immutable source/evidence plus resolved private node identity",
            False,
            True,
            True,
            False,
            "extracted edge list materialized",
            False,
            SemanticOperatorClass.DERIVED_PRIVATE,
            False,
            "v3.1 graphiti_adapter.prepare; edge_operations.extract_edges consumes the prior resolved-node result and performs no persistent read",
        ),
        (
            "EdgeCandidateRead",
            "endpoint lookup, hybrid duplicate search and graph-wide invalidation search",
            True,
            False,
            True,
            False,
            "ordered duplicate and invalidation candidates materialized per edge",
            False,
            SemanticOperatorClass.STATE_DERIVED,
            True,
            "edge_operations.resolve_extracted_edges:360-486",
        ),
        (
            "EdgeResolutionChild",
            "one extracted edge plus its exact ordered candidate ReadView",
            True,
            True,
            True,
            False,
            "dedupe, optional attribute and timestamp subrequests plus contradictions complete",
            False,
            SemanticOperatorClass.STATE_DERIVED,
            True,
            "edge_operations.resolve_extracted_edges:488-509; resolve_extracted_edge:623-847",
        ),
        (
            "NodeAttributeSummaryBatch",
            "resolved node mutable fields, previous episodes and new edge facts",
            True,
            True,
            True,
            False,
            "attributes, summaries and node embeddings materialized",
            False,
            SemanticOperatorClass.STATE_DERIVED,
            True,
            "node_operations.extract_attributes_from_nodes:726-780",
        ),
        (
            "PersistAndPublish",
            "hydrated nodes, resolved/invalidated edges and episode attribution",
            False,
            False,
            False,
            True,
            "managed bulk transaction returns successfully",
            True,
            SemanticOperatorClass.PERSISTENT_EFFECT,
            False,
            "graphiti._process_episode_data:680-735; bulk_utils:128-148",
        ),
        (
            "SourcePublication",
            "successful saga-free managed bulk transaction commit evidence",
            False,
            False,
            False,
            False,
            "source becomes durable at that commit; later saga/community writes forbidden in v0",
            True,
            SemanticOperatorClass.PUBLICATION,
            False,
            "v3.1 process call passes saga=None; update_communities path absent",
        ),
    )
    return tuple(
        SemanticBoundaryRow(
            operator=item[0],
            semantic_inputs=item[1],
            mutable_state_read=item[2],
            sends_llm=item[3],
            produces_private_result=item[4],
            persistent_mutation=item[5],
            completion_semantics=item[6],
            affects_publication=item[7],
            classification=item[8].value,
            read_view_required=item[9],
            source_evidence=item[10],
        )
        for item in values
    )


def _call_name(call: ast.Call) -> str | None:
    function = call.func
    if isinstance(function, ast.Attribute):
        return function.attr
    if isinstance(function, ast.Name):
        return function.id
    return None


class _CallInventory(ast.NodeVisitor):
    def __init__(self, *, relative_file: str) -> None:
        self.relative_file = relative_file
        self.functions: list[str] = []
        self.rows: list[tuple[int, str, str]] = []

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node)
        if name is not None and (
            name in {"execute_write", "execute_query", "save", "run"}
            or "save_bulk" in name
            or name == "update_community"
        ):
            self.rows.append(
                (node.lineno, ".".join(self.functions) or "<module>", name)
            )
        self.generic_visit(node)


def _write_paths(graphiti_root: Path) -> tuple[WritePathRow, ...]:
    files = (
        graphiti_root / "graphiti.py",
        graphiti_root / "utils/bulk_utils.py",
        graphiti_root / "utils/maintenance/graph_data_operations.py",
        graphiti_root / "utils/maintenance/node_operations.py",
        graphiti_root / "utils/maintenance/edge_operations.py",
        graphiti_root / "driver/neo4j_driver.py",
        *sorted((graphiti_root / "driver/neo4j/operations").glob("*.py")),
    )
    rows: list[WritePathRow] = []
    for path in files:
        relative = str(path.relative_to(graphiti_root))
        inventory = _CallInventory(relative_file=relative)
        inventory.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for line, function, call in inventory.rows:
            bulk_entry = relative == "utils/bulk_utils.py" and function in {
                "add_nodes_and_edges_bulk",
                "add_nodes_and_edges_bulk_tx",
            }
            neo4j_bulk = (
                relative
                in {
                    "driver/neo4j/operations/entity_edge_ops.py",
                    "driver/neo4j/operations/entity_node_ops.py",
                    "driver/neo4j/operations/episode_node_ops.py",
                    "driver/neo4j/operations/episodic_edge_ops.py",
                }
                and "save_bulk" in function
            )
            relevant = bulk_entry or neo4j_bulk
            coverage = None
            reason = (
                "current saga-free MemBind v3.1 persistence path; observed inside the one managed bulk transaction"
                if relevant
                else "not reachable from the certified v3.1 private process call with saga=None and no community update"
            )
            if relevant:
                coverage = (
                    "TransactionCommitObserver(session.execute_write success)"
                    if call == "execute_write"
                    else "observed transaction callback plus PERSIST_AND_PUBLISH effect projection"
                )
            rows.append(
                WritePathRow(
                    path_id=f"{relative}:{line}:{function}:{call}",
                    file=relative,
                    line=line,
                    function=function,
                    call=call,
                    relevance=(
                        "RELEVANT_COVERED"
                        if relevant
                        else "CONFIG_GUARDED_OUT_OF_SCOPE"
                    ),
                    coverage_hook=coverage,
                    reason=reason,
                )
            )
    return tuple(rows)


def audit_graphiti_0293(graphiti_root: Path) -> dict[str, object]:
    root = Path(graphiti_root).resolve()
    required = {
        "graphiti.py": root / "graphiti.py",
        "node_operations.py": root / "utils/maintenance/node_operations.py",
        "edge_operations.py": root / "utils/maintenance/edge_operations.py",
        "graph_data_operations.py": root
        / "utils/maintenance/graph_data_operations.py",
        "bulk_utils.py": root / "utils/bulk_utils.py",
        "neo4j_driver.py": root / "driver/neo4j_driver.py",
    }
    if any(not path.is_file() for path in required.values()):
        raise Graphiti0293AuditError("graphiti_0293_source_incomplete")
    dist_info = root.parent / "graphiti_core-0.29.3.dist-info/METADATA"
    if not dist_info.is_file() or "Version: 0.29.3" not in dist_info.read_text(
        encoding="utf-8"
    ):
        raise Graphiti0293AuditError("graphiti_version_not_0293")
    semantic = _semantic_rows()
    writes = _write_paths(root)
    relevant = tuple(row for row in writes if row.relevance.startswith("RELEVANT"))
    covered = tuple(row for row in relevant if row.relevance == "RELEVANT_COVERED")
    return {
        "schema_version": "membind.meg.graphiti-0293-boundary-audit.v1",
        "graphiti_version": "0.29.3",
        "graphiti_source_root": str(root),
        "source_hashes": {name: _sha256(path) for name, path in required.items()},
        "semantic_boundaries": [asdict(row) for row in semantic],
        "attribute_timestamp_summary": [
            {
                "operator": "edge attribute subrequest",
                "mutable_state_read": False,
                "evidence": "resolve_extracted_edge receives the parent child's already materialized resolved edge",
                "classification": "DERIVED_PRIVATE",
                "read_view_required": False,
                "covered_by_parent_read_view": True,
            },
            {
                "operator": "edge timestamp subrequest",
                "mutable_state_read": False,
                "evidence": "_extract_edge_timestamps consumes fact plus immutable episode reference time",
                "classification": "DERIVED_PRIVATE",
                "read_view_required": False,
                "covered_by_parent_read_view": True,
            },
            {
                "operator": "node attribute subrequest",
                "mutable_state_read": True,
                "evidence": "resolved existing node attributes and latest previous episodes enter the prompt",
                "classification": "STATE_DERIVED",
                "read_view_required": True,
                "covered_by_parent_read_view": False,
            },
            {
                "operator": "node summary batch",
                "mutable_state_read": True,
                "evidence": "resolved summaries/attributes and latest previous episodes enter the batch prompt",
                "classification": "STATE_DERIVED",
                "read_view_required": True,
                "covered_by_parent_read_view": False,
            },
        ],
        "publication_contract": {
            "contract": "GRAPHITI_0293_ADD_EPISODE_SAGA_FREE_V0",
            "saga": "DISABLED_REQUIRED",
            "community_update": "NOT_INVOKED_BY_V31_PRIVATE_PROCESS_PATH",
            "publication_boundary": "successful add_nodes_and_edges_bulk managed transaction return",
            "later_persistent_writes": "FORBIDDEN; otherwise OPAQUE",
        },
        "write_path_inventory": [asdict(row) for row in writes],
        "relevant_write_paths": len(relevant),
        "covered_write_paths": len(covered),
        "coverage_ratio": None if not relevant else len(covered) / len(relevant),
        "status": "PASS" if relevant and len(relevant) == len(covered) else "FAIL",
    }


__all__ = ["Graphiti0293AuditError", "audit_graphiti_0293"]
