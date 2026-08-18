"""Offline qualification of the pinned Graphiti v0.29.3 Compile region."""

from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.membind_v1.graphiti_factories import make_graphiti_node_factories
from paper_eval.membind_v1.source_log import SourceRecord
from paper_eval.membind_v31.certification import CertificationRecord, StateCutCertification
from paper_eval.membind_v31.contracts import DependencyClass, EffectClass, OperatorContract
from paper_eval.s5_graphiti_semantic_binding import load_graphiti_semantic_binding


class MemBindV31QualificationError(ValueError):
    """Pinned code, schema, or restricted-capability qualification failed."""


def _fail(code: str) -> MemBindV31QualificationError:
    return MemBindV31QualificationError(code)


def _source_hash(value: object) -> str:
    try:
        text = inspect.getsource(value)
    except (OSError, TypeError):
        raise _fail("operator_source_unavailable") from None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class QualificationResult:
    certification: StateCutCertification
    document: dict[str, object]


class _QualificationLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def generate_response(self, *_args: object, **kwargs: object) -> dict[str, object]:
        response_model = kwargs.get("response_model")
        model_name = getattr(response_model, "__name__", "")
        prompt_name = kwargs.get("prompt_name")
        if not isinstance(prompt_name, str):
            raise _fail("qualification_prompt_identity_missing")
        self.calls.append({"api": "llm.generate_response", "response_model": model_name})
        if model_name == "ExtractedEntities":
            return {
                "extracted_entities": [
                    {"name": "Alice", "entity_type_id": 0, "episode_indices": [0]},
                    {"name": "Bob", "entity_type_id": 0, "episode_indices": [0]},
                ]
            }
        if model_name == "ExtractedEdges":
            return {
                "edges": [
                    {
                        "source_entity_name": "Alice",
                        "target_entity_name": "Bob",
                        "relation_type": "KNOWS",
                        "fact": "Alice knows Bob",
                        "valid_at": None,
                        "invalid_at": None,
                        "episode_indices": [0],
                    }
                ]
            }
        raise _fail("qualification_response_model_unexpected")


class _RestrictedClients:
    def __init__(self, llm_client: _QualificationLLM) -> None:
        self.llm_client = llm_client
        self.undeclared_state_facing_calls: list[str] = []

    def __getattr__(self, name: str) -> object:
        self.undeclared_state_facing_calls.append(name)
        raise _fail("state_cut_certification_failure")


def _pinned_graphiti_version(repository_root: Path) -> str:
    """Resolve the already-created legacy venv without installing anything.

    ``paper-eval-v3`` intentionally has a tiny dependency set.  The pinned
    Graphiti distribution lives in the sibling validation venv used by all
    existing real-Graphiti tests.  Add that exact local site-packages path
    only when the current interpreter cannot resolve the distribution.
    """

    try:
        return importlib.metadata.version("graphiti-core")
    except importlib.metadata.PackageNotFoundError:
        version = f"python{sys.version_info.major}.{sys.version_info.minor}"
        site_packages = (
            Path(repository_root)
            / "membind-validation/.venv/lib"
            / version
            / "site-packages"
        )
        if not site_packages.is_dir():
            raise _fail("graphiti_distribution_missing") from None
        selected = str(site_packages)
        if selected not in sys.path:
            sys.path.append(selected)
        try:
            return importlib.metadata.version("graphiti-core")
        except importlib.metadata.PackageNotFoundError:
            raise _fail("graphiti_distribution_missing") from None


async def qualify_graphiti_v0293_state_cut(*, project_root: Path) -> QualificationResult:
    """Execute real extractors with no mutable-state capability and seal evidence."""

    root = Path(project_root)
    version = _pinned_graphiti_version(root)
    if version != "0.29.3":
        raise _fail("graphiti_version_mismatch")

    from graphiti_core.edges import EntityEdge
    from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode
    from graphiti_core.prompts.extract_edges import ExtractedEdges, edge as edge_prompt
    from graphiti_core.prompts.extract_nodes import ExtractedEntities, extract_message

    binding = load_graphiti_semantic_binding()
    factories = make_graphiti_node_factories(
        episodic_node_type=EpisodicNode,
        entity_node_type=EntityNode,
        message_source=EpisodeType.message,
    )
    source = SourceRecord.create(
        source_sequence=0,
        episode_uuid="00000000-0000-5000-8000-000000000001",
        group_id="membind-v31-offline-qualification",
        reference_time_ns=1_700_000_000_000_000_000,
        source_filter="message",
        episode_projection={
            "name": "qualification-episode",
            "body": "bounded private qualification fixture",
            "source_description": "offline qualification",
            "reference_time": "2023-11-14T22:13:20+00:00",
        },
    )
    episode = factories.episode_factory(source)
    llm = _QualificationLLM()
    clients = _RestrictedClients(llm)
    try:
        nodes, node_map = await binding.extract_nodes(
            clients,
            episode,
            [],
            None,
            [],
            None,
        )
        edges = await binding.extract_edges(
            clients,
            episode,
            list(nodes),
            [],
            {("Entity", "Entity"): []},
            source.group_id,
            None,
            None,
        )
    except MemBindV31QualificationError:
        raise
    except Exception as error:
        raise _fail(f"real_extractor_qualification_failed:{type(error).__qualname__}") from None
    if len(nodes) != 2 or len(edges) != 1 or not isinstance(edges[0], EntityEdge):
        raise _fail("real_extractor_output_shape_invalid")
    if clients.undeclared_state_facing_calls:
        raise _fail("state_cut_certification_failure")
    if set(node_map) != {node.uuid for node in nodes}:
        raise _fail("node_episode_index_map_invalid")

    package_root = Path(inspect.getfile(type(episode))).resolve().parents[1]
    backend_identity = payload_sha256(
        {
            "distribution": "graphiti-core",
            "version": version,
            "semantic_binding_sha256": binding.identity_sha256(),
            "package_root_name": package_root.name,
        }
    )
    adapter_path = root / "paper-eval-v3/src/paper_eval/membind_v31/graphiti_adapter.py"
    adapter_identity = sha256_file(adapter_path)
    if adapter_identity == "missing":
        raise _fail("adapter_source_missing")
    node_operator = _source_hash(binding.extract_nodes)
    edge_operator = _source_hash(binding.extract_edges)
    code_revision = payload_sha256(
        {
            "adapter_sha256": adapter_identity,
            "edge_operator_sha256": edge_operator,
            "node_operator_sha256": node_operator,
            "semantic_binding_sha256": binding.identity_sha256(),
        }
    )
    trace = {
        "allowed_api_calls": list(llm.calls),
        "future_evidence_access_count": 0,
        "persistent_state_read_count": 0,
        "persistent_state_write_count": 0,
        "undeclared_external_side_effect_count": 0,
        "undeclared_state_facing_call_count": len(clients.undeclared_state_facing_calls),
        "raw_node_count": len(nodes),
        "raw_edge_count": len(edges),
    }
    config_identity = payload_sha256(
        {
            "edge_type_map": {"Entity->Entity": []},
            "entity_types": None,
            "excluded_entity_types": [],
            "previous_episode_limit": 10,
            "source": "message",
        }
    )
    common = {
        "memory_backend_identity_sha256": backend_identity,
        "adapter_identity_sha256": adapter_identity,
        "code_revision_sha256": code_revision,
        "config_identity_sha256": config_identity,
        "allowed_evidence_inputs": ("current_source", "evidence_snapshot"),
        "allowed_apis": ("llm.generate_response",),
        "forbidden_apis": (
            "graph_driver.execute_query",
            "memory.search",
            "memory.write",
        ),
        "persistent_state_read_count": 0,
        "persistent_state_write_count": 0,
        "undeclared_external_side_effect_count": 0,
        "future_evidence_access_count": 0,
        "undeclared_state_facing_call_count": 0,
    }
    node_trace = payload_sha256({"operator": "graphiti.extract_nodes", "trace": trace})
    edge_trace = payload_sha256({"operator": "graphiti.extract_edges", "trace": trace})
    node_record = CertificationRecord.create(
        operator_contract=OperatorContract.create(
            operator_name="graphiti.extract_nodes",
            dependency_class=DependencyClass.EVIDENCE_BOUND,
            effect_class=EffectClass.PURE,
        ),
        operator_identity_sha256=node_operator,
        prompt_identity_sha256=_source_hash(extract_message),
        schema_identity_sha256=payload_sha256(ExtractedEntities.model_json_schema()),
        allowed_upstream_outputs=(),
        qualification_trace_sha256=node_trace,
        **common,
    )
    edge_record = CertificationRecord.create(
        operator_contract=OperatorContract.create(
            operator_name="graphiti.extract_edges",
            dependency_class=DependencyClass.EVIDENCE_BOUND,
            effect_class=EffectClass.PURE,
        ),
        operator_identity_sha256=edge_operator,
        prompt_identity_sha256=_source_hash(edge_prompt),
        schema_identity_sha256=payload_sha256(ExtractedEdges.model_json_schema()),
        allowed_upstream_outputs=("graphiti.extract_nodes",),
        qualification_trace_sha256=edge_trace,
        **common,
    )
    bundle = StateCutCertification.create([node_record, edge_record])
    document: dict[str, object] = {
        "schema_version": "membind.paper-eval-v3.membind-v31-state-cut-qualification.v1",
        "status": "PASS",
        "graphiti_version": version,
        "compile_operators": list(bundle.operator_names),
        "state_cut_certification_sha256": bundle.certification_sha256,
        "qualification_trace": trace,
        "operator_records": [
            {
                "operator_contract": record.operator_contract.payload(),
                "operator_contract_sha256": record.operator_contract_sha256,
                "certification": record.payload(),
                "certification_sha256": record.certification_sha256,
            }
            for record in bundle.records
        ],
    }
    document["payload_sha256"] = payload_sha256(document)
    return QualificationResult(certification=bundle, document=document)


__all__ = [
    "MemBindV31QualificationError",
    "QualificationResult",
    "qualify_graphiti_v0293_state_cut",
]
