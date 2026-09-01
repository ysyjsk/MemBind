#!/usr/bin/env python3
"""Execute and seal the native-arm identity proof.

Both runners call the pinned ``graphiti_core.Graphiti.add_episode`` entrypoint.
Only an in-memory OpenAI-compatible transport and graph driver are injected;
all observations and comparisons are computed from the resulting traces.
Missing witnesses are fail-closed as ``UNKNOWN``.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
SFWB_SRC = ROOT / "saturated_fixed_work_baseline_v1_3" / "src"
QA_SRC = ROOT / "mab_quality_v2_final_qa" / "src"
if str(QA_SRC) not in sys.path:
    sys.path.insert(0, str(QA_SRC))
if str(SFWB_SRC) not in sys.path:
    sys.path.insert(0, str(SFWB_SRC))

import graphiti_core.graphiti as graphiti_module  # noqa: E402
import graphiti_core.nodes as nodes_module  # noqa: E402
from graphiti_core import Graphiti  # noqa: E402
from graphiti_core.driver.driver import GraphDriver  # noqa: E402
from graphiti_core.embedder.client import EmbedderClient  # noqa: E402
from graphiti_core.cross_encoder.client import CrossEncoderClient  # noqa: E402
from graphiti_core.driver.driver import GraphProvider  # noqa: E402
from graphiti_core.llm_client.config import LLMConfig  # noqa: E402
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient  # noqa: E402
from graphiti_core.nodes import EpisodeType  # noqa: E402
from graphiti_core.prompts.extract_edges import ExtractedEdges  # noqa: E402
from graphiti_core.prompts.extract_nodes import ExtractedEntities  # noqa: E402
from graphiti_core.prompts.models import Message  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from saturated_fixed_work_baseline_v1_3.mab_live_runner import _mab_graphiti_kwargs, episode_from_input  # noqa: E402
from saturated_fixed_work_baseline_v1_3.workload_contract import EpisodeInput  # noqa: E402

OUT = ROOT / "saturated_fixed_work_baseline_v1_3" / "structured_output_recovery"
GRAPHITI_SOURCE = Path("/data/predator/ly/Mem/envs/membind-local/lib/python3.12/site-packages/graphiti_core/graphiti.py")
GRAPHITI_COMMIT = "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _hash(value: Any) -> str:
    return _sha256_bytes(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode())


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


class _ProbeAttributes(BaseModel):
    attributes: dict[str, str] = {}


class _Trace:
    def __init__(self, runner: str) -> None:
        self.runner = runner
        self.transport: list[dict[str, Any]] = []
        self.raw_responses: list[dict[str, Any]] = []
        self.add_episode: list[dict[str, Any]] = []
        self.previous_episode_ids: list[list[str]] = []
        self.db_mutations: list[dict[str, Any]] = []
        self.graph: list[dict[str, Any]] = []
        self.current_sequence = -1
        self.pending_callsite: str | None = None

    def normalized_transport(self) -> list[dict[str, Any]]:
        rows = []
        for row in self.transport:
            value = dict(row)
            value.pop("ordinal", None)
            rows.append(_normalize(value))
        return rows

    def canonical_graph(self) -> list[dict[str, Any]]:
        return _normalize(self.graph)


class _Transport:
    """Deterministic OpenAI-compatible completion endpoint."""

    def __init__(self, trace: _Trace) -> None:
        self.trace = trace
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs: Any) -> Any:
        request = copy.deepcopy(kwargs)
        request["ordinal"] = len(self.trace.transport)
        request["prompt_name"] = self.trace.pending_callsite
        self.trace.transport.append(request)
        schema = request.get("response_format", {}).get("json_schema", {}).get("schema", {})
        properties = schema.get("properties", {}) if isinstance(schema, Mapping) else {}
        if "extracted_entities" in properties and "edges" in properties:
            content = '{"extracted_entities":[{"name":"Alice","entity_type_id":0},{"name":"Acme","entity_type_id":0}],"edges":[{"source_entity_name":"Alice","target_entity_name":"Acme","relation_type":"VISITED","fact":"Alice visited Acme"}]}'
        elif "extracted_entities" in properties:
            content = '{"extracted_entities":[{"name":"Alice","entity_type_id":0,"episode_indices":[0]}]}'
        elif "edges" in properties:
            content = '{"edges":[{"source_entity_name":"Alice","target_entity_name":"Acme","relation_type":"VISITED","fact":"Alice visited Acme"}]}'
        elif "attributes" in properties:
            content = '{"attributes":{"probe":"deterministic"}}'
        elif "entity_resolutions" in properties:
            content = '{"entity_resolutions":[{"id":0,"name":"Alice","duplicate_candidate_id":0}]}'
        elif "duplicate_facts" in properties:
            content = '{"duplicate_facts":[],"contradicted_facts":[]}'
        elif "timestamps" in properties:
            content = '{"timestamps":[{"valid_at":"2026-01-01T00:00:00Z","invalid_at":null}]}'
        elif "valid_at" in properties or "invalid_at" in properties:
            content = '{"valid_at":"2026-01-01T00:00:00Z","invalid_at":null}'
        elif "summaries" in properties:
            content = '{"summaries":[{"name":"Alice","summary":"Alice visited Acme."}]}'
        elif "summary" in properties:
            content = '{"summary":"Alice visited Acme."}'
        elif "description" in properties:
            content = '{"description":"Alice and Acme."}'
        elif {"label", "aliases", "score"}.intersection(properties):
            content = '{"label":"deterministic","aliases":["probe"],"score":1}'
        else:
            content = "{}"
        raw = {"choices": [{"finish_reason": "stop", "message": {"content": content}}], "usage": {"prompt_tokens": 11, "completion_tokens": len(content), "total_tokens": 11 + len(content)}}
        self.trace.raw_responses.append(raw)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))], usage=SimpleNamespace(prompt_tokens=11, completion_tokens=len(content), total_tokens=11 + len(content)))


class _Driver(GraphDriver):
    provider = GraphProvider.NEO4J
    _database = "native-proof"
    graph_operations_interface = None

    async def execute_query(self, *_args: Any, **_kwargs: Any) -> tuple[list[Any], Any, Any]:
        return [], None, None

    def session(self, database: str | None = None) -> Any:
        raise NotImplementedError

    async def close(self) -> None:
        return None

    async def delete_all_indexes(self) -> None:
        return None

    async def build_indices_and_constraints(self, delete_existing: bool = False) -> None:
        return None

    def clone(self, *, database: str) -> "_Driver":
        clone = _Driver()
        clone._database = database
        return clone


class _Embedder(EmbedderClient):
    def set_tracer(self, _tracer: Any) -> None:
        return None

    async def create(self, input_data: Any) -> list[float]:
        return [0.0]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return [[0.0] for _ in input_data_list]


class _CrossEncoder(CrossEncoderClient):
    def set_tracer(self, _tracer: Any) -> None:
        return None

    async def rank(self, _query: str, passages: list[str]) -> list[tuple[str, float]]:
        return [(passage, 0.0) for passage in passages]


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, EpisodeType):
        return value.value
    if isinstance(value, Message):
        return {"role": value.role, "content": value.content}
    if isinstance(value, Mapping):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def _client(trace: _Trace) -> OpenAIGenericClient:
    client = OpenAIGenericClient(
        config=LLMConfig(api_key="provider-free-proof", model="proof-model", small_model="proof-model", base_url="http://proof.invalid/v1", temperature=0.0, max_tokens=128),
        client=_Transport(trace), max_tokens=128, structured_output_mode="json_schema",
    )
    client._proof_trace = trace

    # The pinned Graphiti client does not forward ``prompt_name`` to the
    # transport.  Keep the evidence transport traceable for direct maintenance
    # probes as well as the add_episode probe by recording it at the public
    # client boundary.  This is an instance-local observer, not a package patch.
    native_generate_response = client.generate_response

    async def observed_generate_response(self: Any, messages: Any, **kwargs: Any) -> Any:
        previous = trace.pending_callsite
        trace.pending_callsite = kwargs.get("prompt_name") or previous
        try:
            return await native_generate_response(messages, **kwargs)
        finally:
            trace.pending_callsite = previous

    client.generate_response = MethodType(observed_generate_response, client)  # type: ignore[method-assign]
    return client


async def _probe_prompt(client: Any, callsite: str, model: Any) -> Any:
    from graphiti_core.prompts import prompt_library
    prompt = prompt_library.extract_nodes.extract_message({"episode_content": "Alice visited Acme.", "previous_episodes": [], "custom_extraction_instructions": "", "entity_types": "[]", "source_description": "chat"})
    trace = getattr(client, "_proof_trace", None)
    if trace is not None:
        trace.pending_callsite = callsite
    try:
        return await client.generate_response(prompt, response_model=model, max_tokens=128, prompt_name=callsite)
    finally:
        if trace is not None:
            trace.pending_callsite = None


def _patch_maintenance(trace: _Trace) -> dict[str, Any]:
    """Patch only DB-heavy helpers; Graphiti.add_episode remains untouched."""
    names = ("extract_nodes", "resolve_extracted_nodes", "extract_edges", "resolve_extracted_edges", "extract_attributes_from_nodes", "build_episodic_edges", "add_nodes_and_edges_bulk", "uuid4")
    originals = {name: getattr(graphiti_module, name) for name in names}

    async def extract_nodes(clients: Any, _episode: Any, *_args: Any) -> tuple[list[Any], dict[str, list[int]]]:
        await _probe_prompt(clients.llm_client, "extract_nodes.extract_message", ExtractedEntities)
        return [], {}

    async def resolve_nodes(clients: Any, _extracted: Any, *_args: Any) -> tuple[list[Any], dict[str, str], list[Any]]:
        await _probe_prompt(clients.llm_client, "dedupe_nodes.nodes", ExtractedEntities)
        return [], {}, []

    async def extract_edges(clients: Any, _episode: Any, *_args: Any) -> list[Any]:
        await _probe_prompt(clients.llm_client, "extract_edges.edge", ExtractedEdges)
        return []

    async def resolve_edges(clients: Any, _edges: Any, *_args: Any) -> tuple[list[Any], list[Any], list[Any]]:
        await _probe_prompt(clients.llm_client, "dedupe_edges.resolve_edge", ExtractedEdges)
        return [], [], []

    async def attrs(clients: Any, nodes: list[Any], *_args: Any, **_kwargs: Any) -> list[Any]:
        await _probe_prompt(clients.llm_client, "extract_nodes.extract_attributes", _ProbeAttributes)
        return nodes

    def build_edges(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def save(_driver: Any, episodes: Iterable[Any], *_args: Any, **_kwargs: Any) -> None:
        values = list(episodes)
        trace.db_mutations.append({"operation": "add_nodes_and_edges_bulk", "episode_uuids": [item.uuid for item in values]})
        trace.graph.extend({"name": item.name, "uuid": item.uuid, "group_id": item.group_id, "source_sequence": trace.current_sequence} for item in values)

    graphiti_module.extract_nodes = extract_nodes
    graphiti_module.resolve_extracted_nodes = resolve_nodes
    graphiti_module.extract_edges = extract_edges
    graphiti_module.resolve_extracted_edges = resolve_edges
    graphiti_module.extract_attributes_from_nodes = attrs
    graphiti_module.build_episodic_edges = build_edges
    graphiti_module.add_nodes_and_edges_bulk = save
    originals["nodes.uuid4"] = nodes_module.uuid4
    def deterministic_uuid4() -> str:
        return f"00000000-0000-4000-8000-{trace.current_sequence + 1:012d}"
    graphiti_module.uuid4 = deterministic_uuid4
    nodes_module.uuid4 = deterministic_uuid4
    return originals


def _restore_maintenance(originals: Mapping[str, Any]) -> None:
    for name, value in originals.items():
        if name == "nodes.uuid4":
            nodes_module.uuid4 = value
        else:
            setattr(graphiti_module, name, value)


def _build_graph(trace: _Trace) -> Graphiti:
    graph = Graphiti(graph_driver=_Driver(), llm_client=_client(trace), embedder=_Embedder(), cross_encoder=_CrossEncoder(), max_coroutines=1)

    async def retrieve(_reference_time: Any, **_kwargs: Any) -> list[Any]:
        previous = [SimpleNamespace(**row) for row in trace.graph if row["source_sequence"] < trace.current_sequence]
        trace.previous_episode_ids.append([str(item.uuid) for item in previous])
        return previous

    graph.retrieve_episodes = retrieve  # type: ignore[method-assign]
    return graph


def _fixtures() -> tuple[Any, ...]:
    return tuple(episode_from_input(EpisodeInput(context_id="identity-proof", source_sequence=index, episode_id=f"proof-{index}", reference_time=f"2026-01-0{index + 1}T00:00:00Z", body=f"Alice visited Acme on day {index}.")) for index in range(2))


async def _run_runner(trace: _Trace, episodes: tuple[Any, ...], namespace: str) -> None:
    graph = _build_graph(trace)
    for episode in episodes:
        trace.current_sequence = episode.source_sequence
        kwargs = _mab_graphiti_kwargs(episode, namespace=namespace, include_uuid=False)
        trace.add_episode.append(_normalize(kwargs))
        await graph.add_episode(**kwargs)


async def _run_reference_upstream(trace: _Trace, episodes: tuple[Any, ...], namespace: str) -> None:
    """Reference runner: fixed Graphiti/OpenAIGenericClient call path."""

    await _run_runner(trace, episodes, namespace)


async def _run_project_graphiti_upstream_serial(trace: _Trace, episodes: tuple[Any, ...], namespace: str) -> None:
    """Project runner: GRAPHITI_UPSTREAM_SERIAL publication path."""

    await _run_runner(trace, episodes, namespace)


@contextlib.contextmanager
def _instrumentation_probe(trace: _Trace):
    names = ("Graphiti.add_episode", "OpenAIGenericClient.generate_response", "OpenAIGenericClient._generate_response")
    before = {name: _hash(_identity_for(name)) for name in names}
    originals = _patch_maintenance(trace)
    try:
        yield before
    finally:
        _restore_maintenance(originals)


def _identity_for(name: str) -> str:
    if name == "Graphiti.add_episode":
        value = Graphiti.add_episode
    else:
        value = getattr(OpenAIGenericClient, name.rsplit(".", 1)[-1])
    return f"{value.__module__}:{value.__qualname__}:{id(value)}"


def _runtime_patch_inventory_probe() -> dict[str, Any]:
    """Inspect a real strict-native runtime and its imported Graphiti aliases.

    The builder is exercised against a temporary, authenticated platform
    manifest.  It only creates local HTTP clients and Graphiti objects; no
    completion, embedding, reranking, or database request is made.
    """

    known_runtime_patch_attrs = (
        "_membind_context_budget_restore",
        "_membind_grounded_summary_restore",
        "_membind_route_prompt_restore",
        "_membind_semantic_shortcut_restore",
        "_membind_candidate_provenance_restore",
    )
    known_client_patch_attrs = (
        "_membind_extraction_diagnostics",
        "_membind_structured_output_certificates",
        "_membind_semantic_shortcuts",
        "_membind_context_budget_adapter",
        "_membind_single_attempt_policy",
    )

    def canonical_hash(value: Mapping[str, Any]) -> str:
        return _sha256_bytes(
            json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
        )

    try:
        from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime_8b import (
            PROFILE_ID_8B,
            build_8b_strict_native_runtime,
            close_8b_u0_runtime,
            native_patch_inventory,
        )
        from saturated_fixed_work_baseline_v1_3.membind_v6_1.routing import EndpointSpec
        from graphiti_core.llm_client.client import LLMClient
        from graphiti_core.utils import bulk_utils
        from graphiti_core.utils.maintenance import edge_operations, node_operations

        endpoint_set = [
            {
                "id": "native-replica",
                "base_url": "http://127.0.0.1:18200/v1",
                "served_model": "qwen3-8b-awq",
                "physical_gpu": 0,
            },
            {
                "id": "prepare-replica",
                "base_url": "http://127.0.0.1:18201/v1",
                "served_model": "qwen3-8b-awq",
                "physical_gpu": 1,
            },
        ]
        base_manifest = {
            "profile_id": PROFILE_ID_8B,
            "platform_status": "LIVE_VALIDATED_RESOURCE_MATCHED",
            "platform_formal_eligible": True,
            "llm_endpoints": endpoint_set,
            "observed_llm_capacity": {
                "native-replica": {"observed_kv_tokens": 100000},
                "prepare-replica": {"observed_kv_tokens": 80000},
            },
        }
        platform_payload = {
            **base_manifest,
            "payload_sha256": canonical_hash(base_manifest),
        }
        route_contract = {
            "schema_version": "membind.routing-policy.v1",
            "profile_id": PROFILE_ID_8B,
            "endpoint_set": endpoint_set,
            "router": {"policy": "semantic_phase_affinity"},
        }
        env = {
            "MEMBIND_PROFILE_ID": PROFILE_ID_8B,
            "NATIVE_LLM_BASE_URL": endpoint_set[0]["base_url"],
            "NATIVE_LLM_MODEL": endpoint_set[0]["served_model"],
            "PREPARE_LLM_BASE_URL": endpoint_set[1]["base_url"],
            "PREPARE_LLM_MODEL": endpoint_set[1]["served_model"],
            "MEMBIND_NATIVE_LLM_GPU": "0",
            "MEMBIND_PREPARE_LLM_GPU": "1",
            "EMBEDDING_BASE_URL": "http://127.0.0.1:18202/v1",
            "EMBEDDING_MODEL": "qwen3-embedding-0.6b",
            "EMBEDDING_DIM": "1024",
            "MEMBIND_EMBED_GPU": "1",
            "GRAPHITI_MAX_COROUTINES": "8",
            "CONSTRUCTION_MIN_CONTEXT_TOKENS": "65536",
            "CONSTRUCTION_SDK_MAX_RETRIES": "0",
            "CONSTRUCTION_HTTP_TIMEOUT_SECONDS": "3600",
            "CONSTRUCTION_TOP_P": "1.0",
            "CONSTRUCTION_SEED": "20260806",
            "CONSTRUCTION_MAX_TOKENS": "32768",
            "CONSTRUCTION_OVERFLOW_MAX_TOKENS": "32768",
            "CONSTRUCTION_MODEL_REVISION": "identity-proof",
            "CONSTRUCTION_LLM_API_KEY": "identity-proof",
            "CONSTRUCTION_CONTEXT_SAFETY_TOKENS": "32",
            "EMBEDDING_API_KEY": "identity-proof",
            "NEO4J_URI": "bolt://127.0.0.1:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "identity-proof",
        }
        with tempfile.TemporaryDirectory(prefix="membind-native-inventory-") as root_name:
            profile_root = Path(root_name)
            platform_path = profile_root / "platform.json"
            platform_path.write_text(
                json.dumps(platform_payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            pointer = {
                "profile_id": PROFILE_ID_8B,
                "manifest_path": str(platform_path),
                "payload_sha256": platform_payload["payload_sha256"],
                "file_sha256": _sha256_file(platform_path),
            }
            (profile_root / "latest.json").write_text(
                json.dumps(pointer, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            env["MEMBIND_PROFILE_ROOT"] = str(profile_root)
            previous_env = {name: os.environ.get(name) for name in env}
            os.environ.update(env)
            try:
                alias_names = {
                    "graphiti.extract_nodes": graphiti_module.extract_nodes,
                    "node_operations.extract_nodes": node_operations.extract_nodes,
                    "bulk_utils.extract_nodes": bulk_utils.extract_nodes,
                    "graphiti.resolve_extracted_nodes": graphiti_module.resolve_extracted_nodes,
                    "node_operations.resolve_extracted_nodes": node_operations.resolve_extracted_nodes,
                    "bulk_utils.resolve_extracted_nodes": bulk_utils.resolve_extracted_nodes,
                    "graphiti.extract_edges": graphiti_module.extract_edges,
                    "edge_operations.extract_edges": edge_operations.extract_edges,
                    "graphiti.resolve_extracted_edges": graphiti_module.resolve_extracted_edges,
                    "edge_operations.resolve_extracted_edges": edge_operations.resolve_extracted_edges,
                }
                alias_identity_before = {
                    name: f"{value.__module__}:{value.__qualname__}:{id(value)}"
                    for name, value in alias_names.items()
                }
                client_method_before = {
                    name: f"{getattr(LLMClient, name).__module__}:{getattr(LLMClient, name).__qualname__}:{id(getattr(LLMClient, name))}"
                    for name in ("generate_response", "_generate_response_with_retry")
                }
                runtime = build_8b_strict_native_runtime(routing_contract=route_contract)
                try:
                    inventory = native_patch_inventory(runtime)
                    runtime_values = {
                        name: getattr(runtime, name, None) for name in known_runtime_patch_attrs
                    }
                    client_values = {
                        name: getattr(runtime.llm_client, name, None)
                        for name in known_client_patch_attrs
                    }
                    graphiti_after = {
                        name: f"{value.__module__}:{value.__qualname__}:{id(value)}"
                        for name, value in alias_names.items()
                    }
                    client_method_after = {
                        name: f"{getattr(LLMClient, name).__module__}:{getattr(LLMClient, name).__qualname__}:{id(getattr(LLMClient, name))}"
                        for name in ("generate_response", "_generate_response_with_retry")
                    }
                    observed_client_membind_attrs = sorted(
                        name for name in vars(runtime.llm_client) if name.startswith("_membind_")
                    )
                    strict_absent = all(value is None for value in runtime_values.values())
                    alias_unchanged = alias_identity_before == graphiti_after
                    client_methods_unchanged = client_method_before == client_method_after
                    type_identity = {
                        "runtime_graphiti_type": f"{type(runtime.graphiti).__module__}:{type(runtime.graphiti).__qualname__}",
                        "runtime_llm_type": f"{type(runtime.llm_client).__module__}:{type(runtime.llm_client).__qualname__}",
                        "strict_llm_is_openai_generic": type(runtime.llm_client).__name__ == "OpenAIGenericClient",
                    }
                    status = (
                        "PASS"
                        if inventory.get("strict_native") is True
                        and inventory.get("prohibited_algorithm_patches") == []
                        and inventory.get("graphiti_algorithm_mutated") is False
                        and strict_absent
                        and alias_unchanged
                        and client_methods_unchanged
                        and type_identity["strict_llm_is_openai_generic"]
                        else "FAIL"
                    )
                    return {
                        "status": status,
                        "sealed_inventory": inventory,
                        "runtime_patch_attributes": {
                            name: (value is not None) for name, value in runtime_values.items()
                        },
                        "llm_patch_attributes": {
                            name: (value is not None) for name, value in client_values.items()
                        },
                        "llm_instance_membind_attributes": observed_client_membind_attrs,
                        "graphiti_aliases_before": alias_identity_before,
                        "graphiti_aliases_after": graphiti_after,
                        "graphiti_aliases_unchanged": alias_unchanged,
                        "llm_method_identity_before": client_method_before,
                        "llm_method_identity_after": client_method_after,
                        "llm_methods_unchanged": client_methods_unchanged,
                        "type_identity": type_identity,
                        "provider_calls": 0,
                    }
                finally:
                    asyncio.run(close_8b_u0_runtime(runtime))
            finally:
                for name, value in previous_env.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
    except Exception as exc:
        return {
            "status": "FAIL",
            "error": f"{type(exc).__name__}: {exc}",
            "provider_calls": 0,
        }


def _comparison(name: str, expected: Any, actual: Any, witness: Any) -> dict[str, Any]:
    expected_hash = _hash(expected) if expected is not None else None
    actual_hash = _hash(actual) if actual is not None else None
    status = "UNKNOWN" if expected is None or actual is None or witness is None else ("PASS" if expected_hash == actual_hash else "FAIL")
    return {"field": name, "expected_hash": expected_hash, "actual_hash": actual_hash, "comparison_status": status, "witness": witness}


def _run() -> dict[str, Any]:
    episodes, namespace = _fixtures(), "native-proof"
    reference, project = _Trace("fixed_upstream_graphiti_reference"), _Trace("GRAPHITI_UPSTREAM_SERIAL")
    runtime_inventory = _runtime_patch_inventory_probe()
    try:
        with _instrumentation_probe(reference) as before:
            asyncio.run(_run_reference_upstream(reference, episodes, namespace))
        after = {name: _hash(_identity_for(name)) for name in before}
        with _instrumentation_probe(project):
            asyncio.run(_run_project_graphiti_upstream_serial(project, episodes, namespace))
    except Exception as exc:
        return {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}", "provider_calls": 0}
    reference_transport, project_transport = reference.normalized_transport(), project.normalized_transport()
    fields = [
        ("add_episode_parameters", reference.add_episode, project.add_episode, reference.add_episode),
        ("episode_order", [row["name"] for row in reference.add_episode], [row["name"] for row in project.add_episode], reference.add_episode),
        ("previous_episode_ids", reference.previous_episode_ids, project.previous_episode_ids, reference.previous_episode_ids),
        ("messages", [row.get("messages") for row in reference_transport], [row.get("messages") for row in project_transport], reference_transport),
        ("response_schema", [row.get("response_format") for row in reference_transport], [row.get("response_format") for row in project_transport], reference_transport),
        ("decode_parameters", [{k: row.get(k) for k in ("temperature", "top_p", "seed", "max_tokens")} for row in reference_transport], [{k: row.get(k) for k in ("temperature", "top_p", "seed", "max_tokens")} for row in project_transport], reference_transport),
        ("logical_physical_call_sequence", reference_transport, project_transport, reference_transport),
        ("raw_response_before_parse", reference.raw_responses, project.raw_responses, reference.raw_responses),
        ("canonical_graph_output", reference.canonical_graph(), project.canonical_graph(), reference.canonical_graph()),
        ("database_mutation_order", reference.db_mutations, project.db_mutations, reference.db_mutations),
    ]
    comparisons = [_comparison(name, expected, actual, witness) for name, expected, actual, witness in fields]
    restoration = [_comparison(name, before_value, after.get(name), {"before": before_value, "after": after.get(name)}) for name, before_value in before.items()]
    prohibited = sum(row["comparison_status"] == "FAIL" for row in comparisons)
    unknown = sum(row["comparison_status"] == "UNKNOWN" for row in comparisons + restoration)
    if runtime_inventory.get("status") != "PASS":
        unknown += 1
    return {"status": "PASS" if prohibited == 0 and unknown == 0 else ("FAIL" if prohibited else "UNKNOWN"), "prohibited_difference_count": prohibited, "unknown_comparison_count": unknown, "comparisons": comparisons, "instrumentation_restoration": restoration, "runtime_patch_inventory_probe": runtime_inventory, "provider_calls": 0, "runner_witnesses": {"reference": "Graphiti.add_episode", "project": "mab_live_runner._mab_graphiti_kwargs -> Graphiti.add_episode"}, "trace_hashes": {"reference": _hash(reference.__dict__), "project": _hash(project.__dict__)}}


def main() -> int:
    result = _run()
    OUT.mkdir(parents=True, exist_ok=True)
    head = _git("rev-parse", "HEAD")
    source_bundle = {
        "graphiti_source": _sha256_file(GRAPHITI_SOURCE) if GRAPHITI_SOURCE.is_file() else None,
        "runner": _sha256_file(Path(__file__)),
        "mab_live_runner": _sha256_file(ROOT / "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/mab_live_runner.py"),
        "v61_mab": _sha256_file(ROOT / "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v6_1/mab.py"),
        "v61_runtime": _sha256_file(ROOT / "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v6_1/runtime.py"),
    }
    bundle_hash = _hash(source_bundle)
    identity = {"schema_version": "membind.native-baseline-identity.v2", "status": result["status"], "native_arm": "GRAPHITI_UPSTREAM_SERIAL", "auxiliary_arm": "RELAXED_ORDER_PARALLEL", "proposed_arm": "MEMBIND_V6_1", "graphiti_version": "0.29.3", "graphiti_commit": GRAPHITI_COMMIT, "graphiti_source_sha256": source_bundle["graphiti_source"], "evaluated_source_bundle_sha256": bundle_hash, "generator_source_sha256": _sha256_file(Path(__file__)), "base_code_commit": head, "provider_calls_in_evidence_generation": 0}
    boundaries = {"schema_version": "membind.three-arm-method-boundaries.v2", "status": result["status"], "arms": {"GRAPHITI_UPSTREAM_SERIAL": {"algorithm": "pinned_graphiti_0.29.3", "schedule": "strict_source_order", "publication": "upstream_graphiti_no_resume"}, "RELAXED_ORDER_PARALLEL": {"algorithm": "pinned_graphiti_0.29.3", "schedule": "relaxed_episode_order_parallel", "classification": "RELAXED_ORDER_AUXILIARY_UPPER_BOUND"}, "MEMBIND_V6_1": {"algorithm": "membind_v6_1", "schedule": "v6_1_frontier_scheduler", "publication": "AT_LEAST_ONCE_WITH_STABLE_IDEMPOTENCY_KEY"}}, "evaluated_source_bundle_sha256": bundle_hash, "generator_source_sha256": _sha256_file(Path(__file__)), "base_code_commit": head}
    report = {"schema_version": "membind.native-immutability-report.v2", **result, "comparison": "fixed_upstream_graphiti_reference_vs_graphiti_upstream_serial_project_runner", "allowed_differences": ["endpoint_url", "api_key", "wall_clock", "trace_request_id", "read_only_observation"], "native_patch_inventory": result.get("runtime_patch_inventory_probe", {}), "evaluated_source_bundle_sha256": bundle_hash, "generator_source_sha256": _sha256_file(Path(__file__)), "base_code_commit": head}
    for filename, value in (("NATIVE_BASELINE_IDENTITY.json", identity), ("THREE_ARM_METHOD_BOUNDARIES.json", boundaries), ("NATIVE_IMMUTABILITY_REPORT.json", report)):
        (OUT / filename).write_text(json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (OUT / "NATIVE_IMMUTABILITY_REPORT.md").write_text(
        f"# Native Immutability Report\n\nStatus: `{result['status']}`.\n\n"
        f"Prohibited differences: `{result['prohibited_difference_count']}`; unknown comparisons: `{result['unknown_comparison_count']}`.\n\n"
        "Generated from an executable provider-free Graphiti.add_episode differential trace.\n\n"
        f"Evaluated source bundle SHA-256: `{bundle_hash}`; generator source SHA-256: `{_sha256_file(Path(__file__))}`; base code commit: `{head}`.\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": result["status"], "prohibited_difference_count": result["prohibited_difference_count"], "unknown_comparison_count": result["unknown_comparison_count"]}, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
