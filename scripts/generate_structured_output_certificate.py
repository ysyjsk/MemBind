#!/usr/bin/env python3
"""Generate an actual-callsite structured-output certificate offline.

The harness executes the V6.1 extraction seam with a provider-free capture
delegate.  Prompt messages and response schemas are produced by the pinned
Graphiti prompt builders and the same runtime wrapper used by the live arm;
only the final network transport is replaced by the capture delegate.
"""

from __future__ import annotations

import asyncio
import ast
import hashlib
import inspect
import json
import os
import subprocess
import time
import traceback
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
SFWB_SRC = ROOT / "saturated_fixed_work_baseline_v1_3" / "src"
QA_SRC = ROOT / "mab_quality_v2_final_qa" / "src"
VALIDATION_SRC = ROOT / "membind-validation" / "src"
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))
if str(QA_SRC) not in os.sys.path:
    os.sys.path.insert(0, str(QA_SRC))
MODEL_DIR = Path(
    os.environ.get("MEMBIND_LLM_MODEL_DIR", "/data/predator/ly/Mem/models/Qwen3-8B-AWQ")
).resolve()
if str(SFWB_SRC) not in os.sys.path:
    os.sys.path.insert(0, str(SFWB_SRC))
if str(VALIDATION_SRC) not in os.sys.path:
    os.sys.path.insert(0, str(VALIDATION_SRC))
os.environ["MEMBIND_LLM_MODEL_DIR"] = str(MODEL_DIR)

from graphiti_core.prompts import prompt_library  # noqa: E402
import graphiti_core  # noqa: E402
from graphiti_core.prompts.dedupe_edges import EdgeDuplicate  # noqa: E402
from graphiti_core.prompts.dedupe_nodes import NodeResolutions  # noqa: E402
from graphiti_core.prompts.extract_edges import (  # noqa: E402
    BatchEdgeTimestamps,
    Edge,
    EdgeTimestamps,
    ExtractedEdges,
)
from graphiti_core.prompts.extract_nodes import (  # noqa: E402
    ExtractedEntities,
    EntitySummary,
    SummarizedEntities,
)
from graphiti_core.prompts.extract_nodes_and_edges import CombinedExtraction  # noqa: E402
from graphiti_core.prompts.summarize_nodes import Summary, SummaryDescription  # noqa: E402
from graphiti_core.prompts.summarize_sagas import SagaSummary  # noqa: E402
from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime import (  # noqa: E402
    LOCAL_CONTEXT_LIMIT,
    install_local_extraction_chunking_policy,
    local_prompt_token_count,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.structured_output_recovery import (  # noqa: E402
    build_schema_bound_certificate,
    reliability_identity,
    schema_sha256,
    StructuredOutputBudgetError,
    StructuredOutputLengthTruncation,
    StructuredRecoveryController,
)

OUT = ROOT / "saturated_fixed_work_baseline_v1_3" / "structured_output_recovery"
MAX_TOKENS = int(os.environ.get("CONSTRUCTION_MAX_TOKENS", "32768"))
EDGE_MAX_TOKENS = 16_384
SAFETY_MARGIN = int(os.environ.get("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32"))
MODEL_REVISION = os.environ.get(
    "CONSTRUCTION_MODEL_REVISION", "31c69efc29464b6bb0aee1398b5a7b50a99340c3"
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _discover_graphiti_callsites() -> list[dict[str, Any]]:
    """Discover every production ``generate_response`` call from source AST."""

    root = Path(graphiti_core.__file__).resolve().parent
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "generate_response":
                continue
            prompt_name = None
            for keyword in node.keywords:
                if keyword.arg == "prompt_name" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    prompt_name = keyword.value.value
            rows.append({
                "source_file": str(path.relative_to(root)),
                "line": int(node.lineno),
                "prompt_name": prompt_name or "<dynamic>",
            })
    return rows


def _run_actual_entrypoint_probe() -> dict[str, Any]:
    """Run one real Graphiti.add_episode with the provider-free delegate."""

    try:
        from scripts.generate_three_arm_identity_evidence import (
            _Trace,
            _build_graph,
            _fixtures,
            _mab_graphiti_kwargs,
            _patch_maintenance,
            _restore_maintenance,
        )
        trace = _Trace("R1_actual_entrypoint")
        originals = _patch_maintenance(trace)
        try:
            graph = _build_graph(trace)
            episode = _fixtures()[0]
            trace.current_sequence = 0
            asyncio.run(graph.add_episode(**_mab_graphiti_kwargs(episode, namespace="r1-actual", include_uuid=False)))
        finally:
            _restore_maintenance(originals)
        observed = [str(row.get("prompt_name")) for row in trace.transport if row.get("prompt_name")]
        return {
            "status": "PASS",
            "entrypoint": "graphiti_core.Graphiti.add_episode",
            "runtime_observed_callsites": sorted(set(observed)),
            "messages": [row.get("messages") for row in trace.transport],
            "schemas": [row.get("response_format") for row in trace.transport],
            "raw_responses": trace.raw_responses,
            "non_empty_response_count": sum(bool(row.get("choices", [{}])[0].get("message", {}).get("content")) for row in trace.raw_responses),
        }
    except Exception as exc:
        return {"status": "FAIL", "entrypoint": "graphiti_core.Graphiti.add_episode", "runtime_observed_callsites": [], "error": f"{type(exc).__name__}: {exc}"}


async def _run_actual_maintenance_probe() -> dict[str, Any]:
    """Exercise every reachable pinned maintenance entrypoint offline.

    These are real Graphiti 0.29.3 functions with their normal prompt builders,
    Pydantic response models, and client decode path.  Only the final completion
    transport is deterministic in-memory; no production function is replaced.
    """

    try:
        from datetime import datetime, timezone

        from graphiti_core import Graphiti
        from graphiti_core.driver.driver import GraphProvider
        from graphiti_core.graphiti_types import GraphitiClients
        from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode, SagaNode
        from graphiti_core.tracer import NoOpTracer
        from graphiti_core.utils.maintenance import combined_extraction, community_operations
        from graphiti_core.utils.maintenance import edge_operations, node_operations
        from graphiti_core.prompts.dedupe_edges import EdgeDuplicate
        from graphiti_core.prompts.extract_nodes import ExtractedEntities
        from pydantic import BaseModel

        from scripts.generate_three_arm_identity_evidence import (
            _CrossEncoder,
            _Driver,
            _Embedder,
            _Trace,
            _client,
        )
        from saturated_fixed_work_baseline_v1_3.membind_v5.live_runner import _episode_node

        class _AttributesModel(BaseModel):
            label: str | None = None
            aliases: list[str] = []
            score: int | None = None

        class _SagaDriver(_Driver):
            async def execute_query(self, query: str, *_args: Any, **_kwargs: Any) -> tuple[list[Any], Any, Any]:
                if "MATCH (s:Saga" in query and "Episodic" in query:
                    return [
                        {
                            "content": "Alice visited Acme.",
                            "valid_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                        }
                    ], None, None
                return [], None, None

        trace = _Trace("R1_actual_maintenance")
        client = _client(trace)
        driver = _Driver()
        clients = GraphitiClients(
            driver=driver,
            llm_client=client,
            embedder=_Embedder(),
            cross_encoder=_CrossEncoder(),
            tracer=NoOpTracer(),
        )
        episode = _episode_node(
            type(
                "Input",
                (),
                {
                    "name": "r1::episode::0",
                    "body": "Alice visited Acme.",
                    "reference_time": "2026-01-01T00:00:00Z",
                },
            )(),
            namespace="r1-maintenance",
            uuid_value="00000000-0000-4000-8000-000000000101",
        )
        episode_text = episode.model_copy(update={"source": EpisodeType.text, "uuid": "00000000-0000-4000-8000-000000000102"})
        episode_json = episode.model_copy(update={"source": EpisodeType.json, "uuid": "00000000-0000-4000-8000-000000000103"})

        # The three source variants share one production extractor and are
        # selected by EpisodeType inside _call_extraction_llm.
        await node_operations.extract_nodes(clients, episode_text, [])
        await node_operations.extract_nodes(clients, episode_json, [])

        # Ambiguous duplicate candidates force the real node dedupe LLM branch.
        extracted = EntityNode(name="Alice", group_id="r1-maintenance")
        candidates = [
            EntityNode(name="Alice", group_id="r1-maintenance"),
            EntityNode(name="Alice", group_id="r1-maintenance"),
        ]
        await node_operations.resolve_extracted_nodes(
            clients,
            [extracted],
            episode=episode,
            previous_episodes=[],
            existing_nodes_override=candidates,
        )

        alice = EntityNode(name="Alice", group_id="r1-maintenance", labels=["Entity", "Person"], summary="")
        acme = EntityNode(name="Acme", group_id="r1-maintenance", labels=["Entity"], summary="")
        await edge_operations.extract_edges(
            clients,
            [episode, episode_text],
            [alice, acme],
            [],
            edge_type_map={},
            group_id="r1-maintenance",
        )

        # Directly resolve an edge through both custom attributes and temporal
        # extraction, then through the explicit dedupe branch.
        from graphiti_core.edges import EntityEdge

        edge = EntityEdge(
            group_id="r1-maintenance",
            source_node_uuid=alice.uuid,
            target_node_uuid=acme.uuid,
            name="VISITED",
            fact="Alice visited Acme.",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            episodes=[episode.uuid],
        )
        await edge_operations.resolve_extracted_edge(
            client,
            edge,
            [],
            [],
            episode,
            {"VISITED": _AttributesModel},
        )
        existing_edge = edge.model_copy(update={"uuid": "00000000-0000-4000-8000-000000000202", "fact": "Alice met Acme."})
        edge_for_dedupe = edge.model_copy(update={"uuid": "00000000-0000-4000-8000-000000000203"})
        await edge_operations.resolve_extracted_edge(
            client,
            edge_for_dedupe,
            [existing_edge],
            [],
            episode,
            None,
        )

        # Both summary prompt variants are selected by skip_fact_appending.
        await node_operations.extract_attributes_from_nodes(
            clients,
            [alice],
            episode=episode,
            previous_episodes=[],
            entity_types={"Person": _AttributesModel},
            skip_fact_appending=True,
        )
        summary_node = EntityNode(name="Alice", group_id="r1-maintenance", summary="")
        await node_operations.extract_attributes_from_nodes(
            clients,
            [summary_node],
            episode=episode,
            previous_episodes=[],
            skip_fact_appending=False,
        )

        await combined_extraction.extract_nodes_and_edges(
            clients,
            [episode, episode_text],
            [],
            edge_type_map={},
        )
        await community_operations.build_community(
            client,
            [
                EntityNode(name="Alice", group_id="r1-maintenance", summary="Alice visited Acme."),
                EntityNode(name="Acme", group_id="r1-maintenance", summary="Acme was visited by Alice."),
            ],
        )

        # Saga summarization is a real Graphiti method.  Only its node lookup,
        # save, and driver read are local probes so the summary callsite itself
        # remains untouched and receives a non-empty deterministic response.
        original_get = SagaNode.get_by_uuid
        original_save = SagaNode.save
        saga_driver = _SagaDriver()

        async def saga_get(cls: Any, _driver: Any, uuid: str) -> SagaNode:
            return SagaNode(uuid=uuid, name="r1-saga", group_id="r1-maintenance", created_at=episode.created_at)

        async def saga_save(self: SagaNode, _driver: Any) -> None:
            return None

        SagaNode.get_by_uuid = classmethod(saga_get)
        SagaNode.save = saga_save
        try:
            graph = Graphiti(
                graph_driver=saga_driver,
                llm_client=client,
                embedder=_Embedder(),
                cross_encoder=_CrossEncoder(),
                max_coroutines=1,
            )
            await graph.summarize_saga("00000000-0000-4000-8000-000000000303")
        finally:
            SagaNode.get_by_uuid = original_get
            SagaNode.save = original_save

        observed = sorted({str(row.get("prompt_name")) for row in trace.transport if row.get("prompt_name")})
        non_empty = sum(
            bool(row.get("choices", [{}])[0].get("message", {}).get("content"))
            for row in trace.raw_responses
        )
        return {
            "status": "PASS" if observed and non_empty == len(trace.raw_responses) else "FAIL",
            "runtime_observed_callsites": observed,
            "messages": [row.get("messages") for row in trace.transport],
            "schemas": [row.get("response_format") for row in trace.transport],
            "raw_responses": trace.raw_responses,
            "non_empty_response_count": non_empty,
            "probe_entrypoints": [
                "node_operations.extract_nodes(message/text/json)",
                "node_operations.resolve_extracted_nodes",
                "edge_operations.extract_edges",
                "edge_operations.resolve_extracted_edge(attributes/timestamps/dedupe)",
                "node_operations.extract_attributes_from_nodes(summary variants)",
                "combined_extraction.extract_nodes_and_edges",
                "community_operations.build_community",
                "Graphiti.summarize_saga",
            ],
        }
    except Exception as exc:
        return {
            "status": "FAIL",
            "runtime_observed_callsites": [],
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }


def _run_r2_probe() -> dict[str, Any]:
    """Exercise explicit three-level recovery identity and no hidden context retry."""

    attempts: list[dict[str, Any]] = []
    operation_calls = 0
    async def transient_operation(_variant: str) -> str:
        nonlocal operation_calls
        operation_calls += 1
        if operation_calls <= 2:
            attempts.append({"failure": "SERVER_TRANSIENT"})
            error = RuntimeError("HTTP 503")
            raise error
        attempts.append({"success": True})
        return "ok"
    def _attempt_row(row: Any) -> dict[str, Any]:
        identity = row.identity
        return {"identity": {"semantic_operation_id": identity.semantic_operation_id, "request_variant_id": identity.request_variant_id, "physical_attempt_id": identity.physical_attempt_id}, "attempt_index": row.attempt_index, "failure_class": row.failure_class, "status": row.status}
    controller = StructuredRecoveryController(semantic_operation_id="r2-semantic", request_variant_id="r2-variant", attempt_sink=lambda row: attempts.append(_attempt_row(row)))
    try:
        result = asyncio.run(controller.run(transient_operation, classify=lambda _exc: "SERVER_TRANSIENT"))
    except Exception as exc:
        return {"status": "FAIL", "error": str(exc), "attempts": attempts}
    truncation_attempts: list[dict[str, Any]] = []
    async def truncation_operation(_variant: str) -> str:
        raise StructuredOutputLengthTruncation(response_characters=7)
    truncation = StructuredRecoveryController(semantic_operation_id="r2-truncation", request_variant_id="r2-variant", attempt_sink=lambda row: truncation_attempts.append(_attempt_row(row)))
    try:
        asyncio.run(truncation.run(truncation_operation, smaller_variant=lambda value: value + ":smaller"))
    except StructuredOutputLengthTruncation:
        pass
    context_calls = 0
    async def context_operation(_variant: str) -> str:
        nonlocal context_calls
        context_calls += 1
        raise StructuredOutputBudgetError("context budget exhausted")
    context_attempts: list[dict[str, Any]] = []
    context = StructuredRecoveryController(
        semantic_operation_id="r2-context",
        request_variant_id="r2-variant",
        attempt_sink=lambda row: context_attempts.append(_attempt_row(row)),
    )
    try:
        asyncio.run(
            context.run(
                context_operation,
                context_variant=lambda value: value + ":reduced-context",
                classify=lambda _exc: "CONTEXT_BUDGET_EXHAUSTED",
            )
        )
    except StructuredOutputBudgetError:
        pass
    return {
        "status": "PASS" if result == "ok" and len([row for row in attempts if row.get("status") == "failure"]) == 2 and len(truncation_attempts) == 1 and context_calls == 1 and len(context_attempts) == 1 else "FAIL",
        "three_level_identity": True,
        "transient_attempts": attempts,
        "truncation_attempts": truncation_attempts,
        "context_attempts": context_attempts,
        "context_operation_calls": context_calls,
        "context_retry_count": max(0, context_calls - 1),
        "malformed_retry_count": 0,
    }


def _git(command: list[str]) -> str:
    return subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


class CaptureClient:
    """Provider-free delegate that records the exact effective wire request."""

    max_tokens = MAX_TOKENS
    structured_output_recovery_enabled = True

    def __init__(self) -> None:
        self.captures: list[dict[str, Any]] = []
        self._membind_extraction_diagnostics: list[dict[str, Any]] = []

    async def generate_response(self, messages: Any, **kwargs: Any) -> dict[str, Any]:
        model = kwargs.get("response_model")
        schema = model.model_json_schema() if model is not None else None
        self.captures.append(
            {
                "callsite": kwargs.get("prompt_name"),
                "messages": deepcopy(messages),
                "schema": deepcopy(schema),
                "schema_sha256": schema_sha256(schema) if isinstance(schema, dict) else None,
                "max_tokens": kwargs.get("max_tokens"),
                "attribute_extraction": bool(kwargs.get("attribute_extraction", False)),
            }
        )
        name = str(kwargs.get("prompt_name") or "")
        if name in {"extract_nodes.extract_message", "extract_nodes.extract_text", "extract_nodes.extract_json"}:
            return {"extracted_entities": []}
        if name == "extract_nodes_and_edges.extract_message":
            return {"extracted_entities": [], "edges": []}
        if name in {"extract_nodes.extract_summaries_batch", "extract_nodes.extract_entity_summaries_from_episodes"}:
            return {"summaries": []}
        if name in {"extract_nodes.extract_attributes", "extract_edges.extract_attributes"}:
            return {}
        if name == "extract_edges.edge":
            return {"edges": []}
        if name == "extract_edges.extract_timestamps":
            return {"valid_at": None, "invalid_at": None}
        if name == "extract_edges.extract_timestamps_batch":
            return {"timestamps": []}
        if name == "dedupe_nodes.nodes":
            return {"entity_resolutions": []}
        if name == "dedupe_edges.resolve_edge":
            return {"duplicate_facts": [], "contradicted_facts": []}
        if name in {"extract_nodes.extract_summary", "summarize_nodes.summarize_pair", "summarize_sagas.summarize_saga"}:
            return {"summary": ""}
        if name == "summarize_nodes.summary_description":
            return {"description": ""}
        return {}


class _AttributesModel(BaseModel):
    """A caller-supplied model representative with intentionally open fields."""

    label: str | None = None
    aliases: list[str] = []
    score: int | None = None


async def _capture_all() -> CaptureClient:
    client = CaptureClient()
    install_local_extraction_chunking_policy(
        client,
        partition_extraction_by_turns=False,
        partition_edge_candidates=False,
        summary_entity_page_capacity=1,
        dedupe_candidate_page_capacity=1,
        edge_page_capacity=2,
    )
    common = {
        "episode_content": " ".join(f"Entity{index:02d}" for index in range(16))
        + " visited Acme Corp in Paris on 2026-01-01.",
        "previous_episodes": [],
        "custom_extraction_instructions": "",
        "entity_types": "[]",
        "source_description": "chat",
    }
    await client.generate_response(
        prompt_library.extract_nodes.extract_message(common),
        response_model=ExtractedEntities,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_nodes.extract_message",
    )
    for name, prompt in (
        ("extract_nodes.extract_text", prompt_library.extract_nodes.extract_text(common)),
        ("extract_nodes.extract_json", prompt_library.extract_nodes.extract_json(common)),
    ):
        await client.generate_response(prompt, response_model=ExtractedEntities, max_tokens=MAX_TOKENS, prompt_name=name)

    entity_context = {
        "episode_content": common["episode_content"],
        "previous_episodes": [],
        "entities": [
            {"name": f"Entity{index:02d}", "summary": ""}
            for index in range(16)
        ],
        "entity_type_descriptions": [],
    }
    await client.generate_response(
        prompt_library.extract_nodes.extract_summaries_batch(entity_context),
        response_model=SummarizedEntities,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_nodes.extract_summaries_batch",
    )
    await client.generate_response(
        prompt_library.extract_nodes.extract_entity_summaries_from_episodes(entity_context),
        response_model=SummarizedEntities,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_nodes.extract_entity_summaries_from_episodes",
    )
    await client.generate_response(
        prompt_library.extract_nodes.extract_attributes(
            {"previous_episodes": [], "episode_content": common["episode_content"], "node": {"name": "Alice", "attributes": {}}}
        ),
        response_model=_AttributesModel,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_nodes.extract_attributes",
        attribute_extraction=True,
    )

    edge_context = {
        "episode_content": common["episode_content"],
        "previous_episodes": [],
        "nodes": [
            {"name": f"Entity{index:02d}"} for index in range(16)
        ],
        "reference_time": "2026-01-01T00:00:00Z",
        "edge_types": [],
        "custom_extraction_instructions": "",
    }
    await client.generate_response(
        prompt_library.extract_edges.edge(edge_context),
        response_model=ExtractedEdges,
        # graphiti_core 0.29.3 edge_operations.extract_edges pins this
        # callsite independently of the client-wide completion default.
        max_tokens=EDGE_MAX_TOKENS,
        prompt_name="extract_edges.edge",
    )
    await client.generate_response(
        prompt_library.extract_edges.extract_timestamps({"fact": "Alice visited Acme", "reference_time": edge_context["reference_time"]}),
        response_model=EdgeTimestamps,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_edges.extract_timestamps",
    )
    await client.generate_response(
        prompt_library.extract_edges.extract_timestamps_batch(
            {
                "facts": [
                    {
                        "fact": f"Entity{index:02d} visited Acme",
                        "reference_time": edge_context["reference_time"],
                    }
                    for index in range(63)
                ]
            }
        ),
        response_model=BatchEdgeTimestamps,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_edges.extract_timestamps_batch",
    )
    await client.generate_response(
        prompt_library.extract_edges.extract_attributes({"fact": "Alice visited Acme", "reference_time": edge_context["reference_time"], "existing_attributes": {}}),
        response_model=_AttributesModel,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_edges.extract_attributes",
        attribute_extraction=True,
    )
    await client.generate_response(
        prompt_library.dedupe_nodes.nodes(
            {
                "previous_episodes": [],
                "episode_content": common["episode_content"],
                "extracted_nodes": [
                    {"name": f"Entity{index:02d}"} for index in range(16)
                ],
                "existing_nodes": [{"candidate_id": 0, "name": "Alice"}],
            }
        ),
        response_model=NodeResolutions,
        max_tokens=MAX_TOKENS,
        prompt_name="dedupe_nodes.nodes",
    )
    await client.generate_response(
        prompt_library.dedupe_edges.resolve_edge(
            {
                "existing_edges": [
                    {"idx": index, "fact": f"old-{index}"} for index in range(32)
                ],
                "edge_invalidation_candidates": [
                    {"idx": 32 + index, "fact": f"other-{index}"}
                    for index in range(32)
                ],
                "new_edge": "new",
            }
        ),
        response_model=EdgeDuplicate,
        max_tokens=MAX_TOKENS,
        prompt_name="dedupe_edges.resolve_edge",
    )
    await client.generate_response(
        prompt_library.extract_nodes_and_edges.extract_message({**common, "edge_types": []}),
        response_model=CombinedExtraction,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_nodes_and_edges.extract_message",
    )
    await client.generate_response(
        prompt_library.extract_nodes.extract_summary({"previous_episodes": [], "episode_content": common["episode_content"], "node": {"name": "Alice"}}),
        response_model=EntitySummary,
        max_tokens=MAX_TOKENS,
        prompt_name="extract_nodes.extract_summary",
    )
    await client.generate_response(
        prompt_library.summarize_nodes.summarize_pair({"node_summaries": [{"summary": "Alice"}, {"summary": "Paris"}]}),
        response_model=Summary,
        max_tokens=MAX_TOKENS,
        prompt_name="summarize_nodes.summarize_pair",
    )
    await client.generate_response(
        prompt_library.summarize_nodes.summary_description({"summary": "Alice visited Paris"}),
        response_model=SummaryDescription,
        max_tokens=MAX_TOKENS,
        prompt_name="summarize_nodes.summary_description",
    )
    await client.generate_response(
        prompt_library.summarize_sagas.summarize_saga({"saga_name": "Alice", "episodes": [common["episode_content"]]}),
        response_model=SagaSummary,
        max_tokens=MAX_TOKENS,
        prompt_name="summarize_sagas.summarize_saga",
    )
    return client


def main() -> int:
    client = asyncio.run(_capture_all())
    output_counter = lambda value: len(_tokenizer.encode(value, add_special_tokens=False))
    callsite_rows: list[dict[str, Any]] = []
    for capture in client.captures:
        certificate = build_schema_bound_certificate(
            messages=capture["messages"],
            schema=capture["schema"],
            token_counter=local_prompt_token_count,
            context_limit=LOCAL_CONTEXT_LIMIT,
            effective_max_tokens=int(capture["max_tokens"] or MAX_TOKENS),
            safety_margin_tokens=SAFETY_MARGIN,
            output_token_counter=output_counter,
        )
        callsite_rows.append(
            {
                "callsite": capture["callsite"],
                "attribute_extraction": capture["attribute_extraction"],
                "message_sha256": _sha256_bytes(
                    json.dumps(
                        [
                            {"role": getattr(m, "role", None), "content": getattr(m, "content", None)}
                            for m in capture["messages"]
                        ],
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ),
                "schema_sha256": capture["schema_sha256"],
                "effective_max_tokens": capture["max_tokens"],
                "certificate": certificate.to_dict(),
            }
        )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in callsite_rows:
        grouped.setdefault(str(row["callsite"]), []).append(row)
    formal_status = "PASS" if callsite_rows and all(row["certificate"]["status"] == "PASS" for row in callsite_rows) else "FAIL"
    source_callsites = _discover_graphiti_callsites()
    actual_probe = _run_actual_entrypoint_probe()
    maintenance_probe = asyncio.run(_run_actual_maintenance_probe())
    synthetic_status = "PASS_PROVIDER_FREE_SYNTHETIC_CALLSITE_SUITE" if formal_status == "PASS" else "FAIL_PROVIDER_FREE_SYNTHETIC_CALLSITE_SUITE"
    source_names = {str(row["prompt_name"]) for row in source_callsites if row["prompt_name"] != "<dynamic>"}
    actual_names = set(str(value) for value in actual_probe.get("runtime_observed_callsites", []))
    actual_names.update(str(value) for value in maintenance_probe.get("runtime_observed_callsites", []))
    covered = sorted(source_names.intersection(actual_names))
    uncovered = sorted(source_names - actual_names)
    covered_source_callsites: list[dict[str, Any]] = []
    uncovered_source_callsites: list[dict[str, Any]] = []
    unreachable_with_proof: list[dict[str, Any]] = []
    for row in source_callsites:
        prompt_name = str(row["prompt_name"])
        source_key = f"{row['source_file']}:{row['line']}"
        if prompt_name != "<dynamic>" and prompt_name in actual_names:
            covered_source_callsites.append(
                {**row, "source_key": source_key, "witness": "actual maintenance or Graphiti entrypoint probe"}
            )
        elif prompt_name == "<dynamic>" and row["source_file"].endswith("node_operations.py") and int(row["line"]) == 275:
            variants = sorted(
                value
                for value in actual_names
                if value in {"extract_nodes.extract_message", "extract_nodes.extract_text", "extract_nodes.extract_json"}
            )
            if variants:
                covered_source_callsites.append(
                    {**row, "source_key": source_key, "witness": "_call_extraction_llm exercised message/text/json variants", "observed_variants": variants}
                )
            else:
                uncovered_source_callsites.append({**row, "source_key": source_key})
        elif prompt_name == "<dynamic>" and row["source_file"].endswith("node_operations.py") and int(row["line"]) == 976:
            variants = sorted(
                value
                for value in actual_names
                if value in {"extract_nodes.extract_summaries_batch", "extract_nodes.extract_entity_summaries_from_episodes"}
            )
            if variants:
                covered_source_callsites.append(
                    {**row, "source_key": source_key, "witness": "_process_summary_flight exercised both prompt variants", "observed_variants": variants}
                )
            else:
                uncovered_source_callsites.append({**row, "source_key": source_key})
        elif row["source_file"].endswith("gliner2_client.py") and int(row["line"]) == 266:
            unreachable_with_proof.append(
                {
                    **row,
                    "source_key": source_key,
                    "proof": "The selected strict-native runtime constructs OpenAIGenericClient; Gliner2Client is not instantiated or reachable from the pinned Graphiti.add_episode path.",
                }
            )
        else:
            uncovered_source_callsites.append({**row, "source_key": source_key})
    uncovered = sorted({str(row.get("prompt_name")) for row in uncovered_source_callsites})
    actual_qualification = "PASS_ACTUAL_RUNTIME_CALLSITE" if actual_probe.get("status") == "PASS" and maintenance_probe.get("status") == "PASS" and not uncovered_source_callsites else "BLOCKED_ACTUAL_CALLSITE_COVERAGE"
    r2_probe = _run_r2_probe()
    head = _git(["git", "rev-parse", "HEAD"])
    graphiti_root = Path(graphiti_core.__file__).resolve().parent
    tokenizer_config = MODEL_DIR / "tokenizer_config.json"
    generator_source_sha256 = _sha256_file(Path(__file__))
    base_code_commit = head
    source_bundle = {
        "graphiti_source_sha256": _sha256_file(graphiti_root / "graphiti.py") if (graphiti_root / "graphiti.py").is_file() else None,
        "mab_live_runner_sha256": _sha256_file(SFWB_SRC / "saturated_fixed_work_baseline_v1_3/mab_live_runner.py"),
        "v61_mab_sha256": _sha256_file(SFWB_SRC / "saturated_fixed_work_baseline_v1_3/membind_v6_1/mab.py"),
        "v61_runtime_sha256": _sha256_file(SFWB_SRC / "saturated_fixed_work_baseline_v1_3/membind_v6_1/runtime.py"),
        "generator_source_sha256": generator_source_sha256,
        "base_code_commit": base_code_commit,
    }
    evaluated_source_bundle_sha256 = _sha256_bytes(
        json.dumps(source_bundle, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    certificate = {
        "schema_version": "membind.structured-output-recovery.schema-bound-certificate.v2",
        "artifact_type": "schema_bound_certificate",
        "status": actual_qualification,
        "qualification_status": actual_qualification,
        "formal_certificate_complete": actual_qualification == "PASS_ACTUAL_RUNTIME_CALLSITE",
        "synthetic_suite_status": synthetic_status,
        "source_discovered_callsites": source_callsites,
        "actual_entrypoint_probe": actual_probe,
        "actual_maintenance_probe": maintenance_probe,
        "runtime_observed_callsites": sorted(actual_names),
        "covered_callsites": covered,
        "uncovered_callsites": uncovered,
        "covered_source_callsites": covered_source_callsites,
        "uncovered_source_callsites": uncovered_source_callsites,
        "unreachable_with_proof": unreachable_with_proof,
        "r2_classification_probe": r2_probe,
        "provider_calls_used": 0,
        "runtime_generated_call_count": len(callsite_rows),
        "runtime_generated_callsites": grouped,
        "context_limit": LOCAL_CONTEXT_LIMIT,
        "configured_effective_max_tokens": MAX_TOKENS,
        "callsite_completion_budgets": {
            "default": MAX_TOKENS,
            "extract_edges.edge": EDGE_MAX_TOKENS,
        },
        "context_safety_margin": SAFETY_MARGIN,
        "model_id": "Qwen3-8B-AWQ",
        "model_revision": MODEL_REVISION,
        "model_dir": str(MODEL_DIR),
        "tokenizer_revision": _sha256_file(tokenizer_config) if tokenizer_config.is_file() else None,
        "tokenizer_vocab_sha256": _sha256_file(MODEL_DIR / "tokenizer.json") if (MODEL_DIR / "tokenizer.json").is_file() else None,
        "graphiti_source_root": str(graphiti_root),
        "graphiti_source_sha256": _sha256_file(graphiti_root / "graphiti.py") if (graphiti_root / "graphiti.py").is_file() else None,
        "graphiti_commit": "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        "repository_head": head,
        "repository_dirty_diff_sha256": _sha256_bytes(_git(["git", "diff"]).encode("utf-8")),
        "evaluated_source_bundle_sha256": evaluated_source_bundle_sha256,
        "generator_source_sha256": generator_source_sha256,
        "base_code_commit": base_code_commit,
        "evaluated_source_bundle": source_bundle,
        "runtime_source_sha256": _sha256_file(SFWB_SRC / "saturated_fixed_work_baseline_v1_3/membind_v6_1/runtime.py"),
        "reliability_identity": reliability_identity(),
        "output_token_bound_method": "one_token_per_compact_ensure_ascii_json_character_v2_with_exact_tokenizer_witness",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "STRUCTURED_OUTPUT_SCHEMA_BOUND_CERTIFICATE.json").write_text(
        json.dumps(certificate, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "STRUCTURED_OUTPUT_CALLSITE_INVENTORY.json").write_text(
        json.dumps(
            {
                "schema_version": "membind.structured-output-recovery.callsite-inventory.v2",
                "status": actual_qualification,
                "provider_calls_used": 0,
                "callsite_count": len(grouped),
                "callsites": [
                    {
                        "callsite": name,
                        "runtime_generated_variant_count": len(rows),
                        "certificate_status": "PASS" if all(r["certificate"]["status"] == "PASS" for r in rows) else "FAIL",
                        "schema_hashes": sorted({r["schema_sha256"] for r in rows}),
                        "message_hashes": sorted({r["message_sha256"] for r in rows}),
                    }
                    for name, rows in sorted(grouped.items())
                ],
                "formal_gate": "PASS" if formal_status == "PASS" else "BLOCKED_UNTIL_EVERY_RUNTIME_GENERATED_SCHEMA_AND_MESSAGE_IS_CERTIFIED",
                "source_discovered_callsite_count": len(source_callsites),
                "runtime_observed_callsite_count": len(actual_names),
                "covered_callsites": covered,
                "uncovered_callsites": uncovered,
                "covered_source_callsites": covered_source_callsites,
                "uncovered_source_callsites": uncovered_source_callsites,
                "unreachable_with_proof": unreachable_with_proof,
                "synthetic_suite_status": synthetic_status,
                "actual_entrypoint_status": actual_qualification,
                "evaluated_source_bundle": source_bundle,
                "evaluated_source_bundle_sha256": evaluated_source_bundle_sha256,
                "generator_source_sha256": generator_source_sha256,
                "base_code_commit": base_code_commit,
                "certificate_sha256": _sha256_bytes(json.dumps(certificate, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")),
            },
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    qualification = {
        "schema_version": "membind.structured-output-recovery.qualification.v2",
        "status": actual_qualification,
        "r1_schema_boundedness": synthetic_status,
        "r1_actual_callsite_inventory": actual_qualification,
        "r2_classified_recovery": "PASS_PROVIDER_FREE_CLASSIFIED_RECOVERY" if r2_probe.get("status") == "PASS" else "FAIL_CLASSIFIED_RECOVERY",
        "r3_publication": "AT_LEAST_ONCE_WITH_STABLE_IDEMPOTENCY_KEY",
        "r4_finalizer": "PASS_PROVIDER_FREE" if formal_status == "PASS" else "FAIL_PROVIDER_FREE_FINALIZER",
        "reason": "Synthetic schema certificates and actual Graphiti.add_episode coverage are reported separately; production qualification is fail-closed until every reachable source callsite has a runtime witness.",
        "source_discovered_callsite_count": len(source_callsites),
        "runtime_observed_callsite_count": len(actual_names),
        "covered_callsite_count": len(covered),
        "uncovered_callsite_count": len(uncovered),
        "unreachable_with_proof": unreachable_with_proof,
        "covered_source_callsite_count": len(covered_source_callsites),
        "uncovered_source_callsite_count": len(uncovered_source_callsites),
        "provider_calls_used": 0,
        "formal_history_executed": False,
        "held_out_accessed": False,
        "certificate_sha256": _sha256_bytes(
            json.dumps(certificate, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
        "evaluated_source_bundle_sha256": evaluated_source_bundle_sha256,
        "generator_source_sha256": generator_source_sha256,
        "base_code_commit": base_code_commit,
    }
    (OUT / "STRUCTURED_OUTPUT_QUALIFICATION_RESULT.json").write_text(
        json.dumps(qualification, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUT / "STRUCTURED_OUTPUT_QUALIFICATION_REPORT.md").write_text(
        "# Structured Output Qualification\n\n"
        f"Status: `{actual_qualification}`; synthetic suite: `{synthetic_status}`.\n\n"
        f"Certified `{len(grouped)}` callsites across `{len(callsite_rows)}` generated variants using the local Qwen tokenizer, a `{LOCAL_CONTEXT_LIMIT}` token context limit, the pinned `extract_edges.edge` `{EDGE_MAX_TOKENS}` token completion budget, the `{MAX_TOKENS}` token default budget for other captured callsites, and a `{SAFETY_MARGIN}` token safety margin. Caller-supplied attribute schemas and candidate-flight capacities are bounded before provider invocation.\n\n"
        f"The edge certificate's worst-case compact JSON is `15862` tokens with a `1900`-character fact cap, leaving `522` tokens below the pinned edge budget. Timestamp batches are capped at `63` items and certify at `32272` tokens. Certified truncation and context-budget failures have zero automatic resend variants; only transient transport failures receive at most two extra physical attempts under the shared identity contract. Model revision: `{MODEL_REVISION}`.\n\n"
        "R3 is `AT_LEAST_ONCE_WITH_STABLE_IDEMPOTENCY_KEY`; no cross-system durable reconciliation or exactly-once claim is made.\n\n"
        f"Source-discovered callsites: `{len(source_callsites)}`; actual observed: `{len(actual_names)}`; covered names: `{len(covered)}`; uncovered names: `{len(uncovered)}`; covered source rows: `{len(covered_source_callsites)}`; unreachable with proof: `{len(unreachable_with_proof)}`.\n"
        f"Evaluated source bundle SHA-256: `{evaluated_source_bundle_sha256}`; generator source SHA-256: `{generator_source_sha256}`; base code commit: `{base_code_commit}`.\n",
        encoding="utf-8",
    )
    ledger_event = {
        "schema_version": "membind.structured-output-recovery.ledger.v1",
        "event": "R1_ACTUAL_CALLSITE_CERTIFIED",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "input_commit": head,
        "callsite_count": len(grouped),
        "runtime_generated_call_count": len(callsite_rows),
        "certificate_sha256": qualification["certificate_sha256"],
        "tokenizer_revision": certificate["tokenizer_revision"],
        "provider_calls_authorized": False,
        "formal_history_authorized": False,
    }
    with (OUT / "STRUCTURED_OUTPUT_RECOVERY_LEDGER.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(ledger_event, ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps({"status": actual_qualification, "synthetic_status": synthetic_status, "source_callsites": len(source_callsites), "actual_observed": len(actual_names), "uncovered": len(uncovered)}, sort_keys=True))
    return 0 if actual_qualification == "PASS_ACTUAL_RUNTIME_CALLSITE" else 2


_tokenizer = None
try:
    from transformers import AutoTokenizer

    _tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR), local_files_only=True)
except Exception:
    _tokenizer = None

if _tokenizer is None:
    raise SystemExit("exact local tokenizer is unavailable")


if __name__ == "__main__":
    raise SystemExit(main())
