"""Isolated resource-matched Qwen3-8B dual-replica Graphiti runtime."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from native_characterization_runtime import U0Config, U0Runtime

from .graphiti_compat import install_candidate_provenance_guard
from .routing import EndpointSpec, RoutedOpenAIClient, install_routing_prompt_context
from .summary_materialization import (
    SUMMARY_POLICY_ID,
    install_grounded_summary_materialization,
)
from .runtime import (
    LOCAL_CONTEXT_LIMIT,
    LOCAL_HTTP_TIMEOUT_SECONDS,
    LOCAL_MAX_COROUTINES,
    LOCAL_SDK_MAX_RETRIES,
    LocalRuntimeConfigurationError,
    _normalized_url,
    build_local_openai_transport,
    close_local_u0_runtime,
    install_local_context_budget_adapter,
    install_local_extraction_chunking_policy,
    install_local_single_attempt_policy,
    _local_chat_tokenizer,
    local_prompt_token_count,
)
from .shared_structured_output import adapter_identity


PROFILE_ID_8B = "local-qwen3-8b-awq-dualreplica-v1"
LLM_MODEL_8B = "qwen3-8b-awq"
EMBEDDING_MODEL_8B = "qwen3-embedding-0.6b"
EMBEDDING_DIMENSION_8B = 1024
NATIVE_ENDPOINT_ID = "native-replica"
PREPARE_ENDPOINT_ID = "prepare-replica"
EDGE_PARTITION_WORKERS_8B = 2
EDGE_PHYSICAL_PAGE_LANES_8B = 2
NODE_PARTITION_WORKERS_8B = 2
_ENTITIES_BLOCK_RE = re.compile(
    r"<ENTITIES>\s*(?P<entities>.*?)\s*</ENTITIES>",
    re.IGNORECASE | re.DOTALL,
)
# Model markers are namespace tokens, not arbitrary substrings.  The attempt
# id is a hexadecimal value and may legitimately contain strings such as
# ``14b`` by chance; substring matching would reject an otherwise isolated
# 8B attempt nondeterministically.
_FOREIGN_MODEL_NAMESPACE_TOKEN_RE = re.compile(
    r"(?<![a-z0-9])(14b|32b|fp8)(?![a-z0-9])",
    re.IGNORECASE,
)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise LocalRuntimeConfigurationError(f"{name} is required for {PROFILE_ID_8B}")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LocalRuntimeConfigurationError(f"invalid 8B runtime JSON: {path}") from exc
    if not isinstance(value, dict):
        raise LocalRuntimeConfigurationError(f"8B runtime JSON is not an object: {path}")
    return value


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_8b_platform_manifest() -> tuple[Path, dict[str, Any]]:
    """Load and authenticate the live-validated immutable platform manifest."""

    profile_root = Path(_required("MEMBIND_PROFILE_ROOT")).resolve()
    pointer_path = profile_root / "latest.json"
    pointer = _read_json(pointer_path)
    if pointer.get("profile_id") != PROFILE_ID_8B:
        raise LocalRuntimeConfigurationError("8B platform pointer has the wrong profile")
    manifest_path = Path(str(pointer.get("manifest_path", ""))).resolve()
    if profile_root not in manifest_path.parents or not manifest_path.is_file():
        raise LocalRuntimeConfigurationError("8B platform manifest is outside the profile root")
    manifest = _read_json(manifest_path)
    payload_hash = manifest.get("payload_sha256")
    calculated = _canonical_hash(
        {key: value for key, value in manifest.items() if key != "payload_sha256"}
    )
    if (
        manifest.get("profile_id") != PROFILE_ID_8B
        or manifest.get("platform_status") != "LIVE_VALIDATED_RESOURCE_MATCHED"
        or manifest.get("platform_formal_eligible") is not True
        or payload_hash != calculated
        or pointer.get("payload_sha256") != payload_hash
        or pointer.get("file_sha256") != _file_hash(manifest_path)
    ):
        raise LocalRuntimeConfigurationError("8B platform manifest authentication failed")
    return manifest_path, manifest


def load_8b_routing_contract(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    contract = _read_json(resolved)
    if (
        contract.get("schema_version") != "membind.routing-policy.v1"
        or contract.get("profile_id") != PROFILE_ID_8B
        or not isinstance(contract.get("endpoint_set"), list)
        or not isinstance(contract.get("router"), dict)
    ):
        raise LocalRuntimeConfigurationError("invalid 8B routing contract")
    for value in contract["endpoint_set"]:
        EndpointSpec.from_mapping(value)
    return contract


def _expected_endpoint_set() -> list[dict[str, Any]]:
    return [
        {
            "id": NATIVE_ENDPOINT_ID,
            "base_url": _normalized_url(_required("NATIVE_LLM_BASE_URL")),
            "served_model": _required("NATIVE_LLM_MODEL"),
            "physical_gpu": int(_required("MEMBIND_NATIVE_LLM_GPU")),
        },
        {
            "id": PREPARE_ENDPOINT_ID,
            "base_url": _normalized_url(_required("PREPARE_LLM_BASE_URL")),
            "served_model": _required("PREPARE_LLM_MODEL"),
            "physical_gpu": int(_required("MEMBIND_PREPARE_LLM_GPU")),
        },
    ]


def _normalized_endpoint_set(values: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                "id": str(value["id"]),
                "base_url": _normalized_url(str(value["base_url"])),
                "served_model": str(value["served_model"]),
                "physical_gpu": int(value["physical_gpu"]),
            }
            for value in values
        ],
        key=lambda value: value["id"],
    )


def runtime_8b_manifest(
    routing_contract: Mapping[str, Any],
    *,
    strict_native: bool = False,
    enable_grounded_summary_materialization: bool = False,
    enable_endpoint_schema_grounding: bool = False,
    enable_work_conserving_edge_admission: bool = False,
    enable_adaptive_edge_admission: bool = False,
    dedupe_candidate_page_capacity: int | None = None,
    shared_bounded_structured_output: bool = False,
) -> dict[str, Any]:
    """Validate runtime identity and resolve the selected fair routing arm."""

    if _required("MEMBIND_PROFILE_ID") != PROFILE_ID_8B:
        raise LocalRuntimeConfigurationError("the isolated 8B profile is not activated")
    if _required("NATIVE_LLM_MODEL") != LLM_MODEL_8B or _required("PREPARE_LLM_MODEL") != LLM_MODEL_8B:
        raise LocalRuntimeConfigurationError("8B endpoint model identity mismatch")
    if _required("EMBEDDING_MODEL") != EMBEDDING_MODEL_8B:
        raise LocalRuntimeConfigurationError("8B embedding model identity mismatch")
    if int(_required("EMBEDDING_DIM")) != EMBEDDING_DIMENSION_8B:
        raise LocalRuntimeConfigurationError("8B embedding dimension mismatch")
    if int(_required("GRAPHITI_MAX_COROUTINES")) != LOCAL_MAX_COROUTINES:
        raise LocalRuntimeConfigurationError("8B Graphiti concurrency mismatch")
    if int(_required("CONSTRUCTION_MIN_CONTEXT_TOKENS")) != LOCAL_CONTEXT_LIMIT:
        raise LocalRuntimeConfigurationError("8B context contract mismatch")
    if int(_required("CONSTRUCTION_SDK_MAX_RETRIES")) != LOCAL_SDK_MAX_RETRIES:
        raise LocalRuntimeConfigurationError("8B SDK retries must be disabled")
    if float(_required("CONSTRUCTION_HTTP_TIMEOUT_SECONDS")) != LOCAL_HTTP_TIMEOUT_SECONDS:
        raise LocalRuntimeConfigurationError("8B HTTP timeout mismatch")
    if _required("CONSTRUCTION_TOP_P") != "1.0" or _required("CONSTRUCTION_SEED") != "20260806":
        raise LocalRuntimeConfigurationError("8B deterministic decoding contract mismatch")

    platform_path, platform = load_8b_platform_manifest()
    selected_endpoints = _normalized_endpoint_set(list(routing_contract["endpoint_set"]))
    platform_endpoints = _normalized_endpoint_set(list(platform["llm_endpoints"]))
    expected = _normalized_endpoint_set(_expected_endpoint_set())
    if len(selected_endpoints) == 2:
        if selected_endpoints != platform_endpoints or selected_endpoints != expected:
            raise LocalRuntimeConfigurationError("headline route endpoint set differs from the platform")
    elif len(selected_endpoints) == 1:
        if selected_endpoints != [value for value in expected if value["id"] == NATIVE_ENDPOINT_ID]:
            raise LocalRuntimeConfigurationError("single-GPU route does not use the native endpoint")
    else:
        raise LocalRuntimeConfigurationError("8B route endpoint count is invalid")

    requested_max_tokens = int(_required("CONSTRUCTION_MAX_TOKENS"))
    overflow_max_tokens = int(_required("CONSTRUCTION_OVERFLOW_MAX_TOKENS"))
    if requested_max_tokens != 32_768 or overflow_max_tokens != 32_768:
        raise LocalRuntimeConfigurationError("8B completion budget must remain frozen at 32768")
    policy = routing_contract["router"].get("policy")
    edge_page_lanes = (
        max(EDGE_PHYSICAL_PAGE_LANES_8B, EDGE_PARTITION_WORKERS_8B * 2)
        if enable_work_conserving_edge_admission
        else EDGE_PHYSICAL_PAGE_LANES_8B
    )
    return {
        "schema_version": "membind.local-qwen3-8b-dual-runtime.v1",
        "profile_id": PROFILE_ID_8B,
        "platform_manifest": {
            "path": str(platform_path),
            "payload_sha256": platform["payload_sha256"],
            "file_sha256": _file_hash(platform_path),
        },
        "construction": {
            "served_model_id": LLM_MODEL_8B,
            "model_revision": _required("CONSTRUCTION_MODEL_REVISION"),
            "endpoint_set": selected_endpoints,
            "routing_policy": policy,
            "routing_contract": dict(routing_contract),
            "context_limit": LOCAL_CONTEXT_LIMIT,
            "requested_max_tokens": requested_max_tokens,
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": 20260806,
            "thinking": False,
            "sdk_max_retries": 0,
            "http_timeout_seconds": LOCAL_HTTP_TIMEOUT_SECONDS,
            "extraction_chunking_policy": (
                "upstream_graphiti_unmodified"
                if strict_native
                else "source_grounded_bounded_nodes_v5"
            ),
            "node_partition_execution_policy": (
                "upstream_graphiti_unmodified"
                if strict_native
                else "deterministic_shared_cap_partition_pipeline_v1"
            ),
            "node_partition_workers": NODE_PARTITION_WORKERS_8B,
            "node_physical_partition_lanes": NODE_PARTITION_WORKERS_8B,
            "certified_previous_context_policy": (
                "upstream_graphiti_unmodified"
                if strict_native
                else "current_evidence_only_certified_extraction_v1"
            ),
            "edge_partition_policy": (
                "upstream_graphiti_unmodified"
                if strict_native
                else "grounded_base_domain_adjacent_user_cover_v6_selected"
            ),
            "edge_pagination_policy": (
                "upstream_graphiti_unmodified"
                if strict_native
                else "marginal_work_pruned_actor_domain_cap2_v19_selected"
            ),
            "edge_execution_policy": (
                "upstream_graphiti_unmodified"
                if strict_native
                else "global_cap_preserving_cross_partition_pipeline_v1"
            ),
            "edge_partition_workers": EDGE_PARTITION_WORKERS_8B,
            "edge_physical_page_lanes": edge_page_lanes,
            "edge_page_admission_policy": (
                "upstream_graphiti_unmodified"
                if strict_native
                else "durable_frontier_source_priority_v1"
            ),
            "edge_physical_admission_policy": (
                "upstream_graphiti_unmodified"
                if strict_native
                else "adaptive_congestion_topology_derived_edge_admission_v1"
                if enable_adaptive_edge_admission
                else "arbiter_work_conserving_partition_derived_v1"
                if enable_work_conserving_edge_admission
                else "fixed_partition_page_gate_v1"
            ),
            "edge_admission_initial_lanes": EDGE_PARTITION_WORKERS_8B,
            "edge_endpoint_schema_policy": (
                "upstream_graphiti_unmodified"
                if strict_native
                else "entity_block_literal_endpoint_grounding_v1"
                if enable_endpoint_schema_grounding
                else "graphiti_edge_endpoint_string_v1"
            ),
            "semantic_shortcut_policy": (
                "none" if strict_native else "empty_edges_when_distinct_entity_count_lt_2_v1"
            ),
            "node_resolution_policy": (
                "upstream_graphiti_unmodified"
                if strict_native
                else "per_entity_candidate_provenance_name_predicate_pushdown_v3"
            ),
            "dedupe_candidate_partition_policy": (
                "upstream_graphiti_unmodified"
                if strict_native
                else f"native_dedupe_existing_candidate_pages_v1_capacity_{dedupe_candidate_page_capacity}"
                if dedupe_candidate_page_capacity is not None
                else "disabled"
            ),
            "entity_summary_policy": (
                "upstream_graphiti_unmodified"
                if strict_native
                else SUMMARY_POLICY_ID
                if enable_grounded_summary_materialization
                else "graphiti_native_batched_summary_v1"
            ),
            "shared_structured_output": (
                adapter_identity() if shared_bounded_structured_output else None
            ),
        },
        "embedding": {
            "base_url": _normalized_url(_required("EMBEDDING_BASE_URL")),
            "served_model_id": EMBEDDING_MODEL_8B,
            "dimension": EMBEDDING_DIMENSION_8B,
            "physical_gpu": int(_required("MEMBIND_EMBED_GPU")),
        },
        "neo4j": {
            "uri": _required("NEO4J_URI"),
            "database": os.environ.get("NEO4J_DATABASE", "neo4j"),
        },
        "graphiti_max_coroutines": LOCAL_MAX_COROUTINES,
        "observed_llm_capacity": platform["observed_llm_capacity"],
    }


def _capacity_weights(manifest: Mapping[str, Any], endpoint_ids: set[str]) -> dict[str, float]:
    observed = manifest["observed_llm_capacity"]
    result: dict[str, float] = {}
    for endpoint_id in endpoint_ids:
        row = observed.get(endpoint_id)
        if not isinstance(row, Mapping):
            raise LocalRuntimeConfigurationError(f"missing live capacity: {endpoint_id}")
        tokens = row.get("observed_kv_tokens")
        if not isinstance(tokens, int) or tokens < LOCAL_CONTEXT_LIMIT:
            raise LocalRuntimeConfigurationError(f"insufficient live capacity: {endpoint_id}")
        result[endpoint_id] = float(tokens)
    return result


def _distinct_prompt_entity_count(messages: Any) -> int | None:
    """Parse Graphiti's entity JSON block without retaining prompt content."""

    if not isinstance(messages, (list, tuple)):
        return None
    for message in messages:
        if isinstance(message, Mapping):
            content = message.get("content")
        else:
            content = getattr(message, "content", None)
        if not isinstance(content, str):
            continue
        match = _ENTITIES_BLOCK_RE.search(content)
        if match is None:
            continue
        try:
            values = json.loads(match.group("entities"))
        except json.JSONDecodeError:
            return None
        if not isinstance(values, list):
            return None
        names = {
            " ".join(str(value.get("name", "")).split()).casefold()
            for value in values
            if isinstance(value, Mapping) and str(value.get("name", "")).strip()
        }
        return len(names)
    return None


def install_empty_edge_shortcut(llm_client: Any) -> Callable[[], None]:
    """Skip edge generation only when two distinct endpoints cannot exist."""

    original = getattr(llm_client, "generate_response", None)
    if not callable(original):
        raise LocalRuntimeConfigurationError("8B semantic shortcut seam is unavailable")
    evidence: list[dict[str, Any]] = []
    llm_client._membind_semantic_shortcuts = evidence
    restored = False

    async def generate_response(messages: Any, **kwargs: Any) -> Any:
        prompt_name = kwargs.get("prompt_name")
        if prompt_name == "extract_edges.edge":
            entity_count = _distinct_prompt_entity_count(messages)
            if entity_count is not None and entity_count < 2:
                evidence.append(
                    {
                        "event": "EMPTY_EDGE_SHORTCUT",
                        "schema_version": "membind.v6.1.semantic-shortcut.v1",
                        "prompt_name": prompt_name,
                        "distinct_entity_count": entity_count,
                        "reason": "edge_contract_requires_two_distinct_entities",
                        "monotonic_ns": time.monotonic_ns(),
                    }
                )
                return {"edges": []}
        return await original(messages, **kwargs)

    setattr(llm_client, "generate_response", generate_response)

    def restore() -> None:
        nonlocal restored
        if restored:
            return
        restored = True
        setattr(llm_client, "generate_response", original)

    return restore


def build_8b_u0_runtime(
    *,
    routing_contract: Mapping[str, Any],
    route_event_sink: Callable[[dict[str, Any]], None] | None = None,
    summary_entity_page_capacity: int | None = None,
    dedupe_candidate_page_capacity: int | None = None,
    enable_grounded_summary_materialization: bool = False,
    enable_endpoint_schema_grounding: bool = False,
    enable_work_conserving_edge_admission: bool = False,
    enable_adaptive_edge_admission: bool = False,
    strict_native: bool = False,
    shared_bounded_structured_output: bool = False,
) -> U0Runtime:
    """Build one local runtime.

    ``strict_native`` is intentionally explicit.  The default builder is the
    V6.1 substrate and may install the bounded-output/work-reduction patches;
    the strict builder below shares only transport construction and the pinned
    Graphiti object construction, never those patches.
    """

    manifest = runtime_8b_manifest(
        routing_contract,
        strict_native=strict_native,
        enable_grounded_summary_materialization=enable_grounded_summary_materialization,
        enable_endpoint_schema_grounding=enable_endpoint_schema_grounding,
        enable_work_conserving_edge_admission=enable_work_conserving_edge_admission,
        enable_adaptive_edge_admission=enable_adaptive_edge_admission,
        dedupe_candidate_page_capacity=dedupe_candidate_page_capacity,
        shared_bounded_structured_output=shared_bounded_structured_output,
    )
    construction_key = _required("CONSTRUCTION_LLM_API_KEY")
    embedding_key = _required("EMBEDDING_API_KEY")
    neo4j_user = _required("NEO4J_USER")
    neo4j_password = _required("NEO4J_PASSWORD")

    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
    from graphiti_native import QwenVLLMClient

    resolution_restore: Callable[[], None] | None = None
    resolution_evidence: list[dict[str, Any]] = []
    if not strict_native:
        resolution_restore, resolution_evidence = install_candidate_provenance_guard()
    endpoint_specs = tuple(
        EndpointSpec.from_mapping(value) for value in manifest["construction"]["endpoint_set"]
    )
    endpoint_clients = {
        endpoint.endpoint_id: build_local_openai_transport(
            api_key=construction_key,
            base_url=endpoint.base_url,
            timeout_seconds=LOCAL_HTTP_TIMEOUT_SECONDS,
            max_retries=LOCAL_SDK_MAX_RETRIES,
        )
        for endpoint in endpoint_specs
    }
    router = RoutedOpenAIClient(
        policy=str(manifest["construction"]["routing_policy"]),
        endpoints=endpoint_specs,
        endpoint_clients=endpoint_clients,
        capacity_weights=_capacity_weights(
            manifest, {endpoint.endpoint_id for endpoint in endpoint_specs}
        ),
        event_sink=route_event_sink,
    )
    native_url = next(
        endpoint.base_url for endpoint in endpoint_specs if endpoint.endpoint_id == NATIVE_ENDPOINT_ID
    )
    config = U0Config(
        construction_base_url=native_url,
        construction_model=LLM_MODEL_8B,
        construction_model_revision=str(manifest["construction"]["model_revision"]),
        embedding_base_url=str(manifest["embedding"]["base_url"]),
        embedding_model=EMBEDDING_MODEL_8B,
        embedding_dimension=EMBEDDING_DIMENSION_8B,
        neo4j_uri=str(manifest["neo4j"]["uri"]),
        max_coroutines=LOCAL_MAX_COROUTINES,
        structured_output_mode="json_schema",
        requested_max_tokens=int(manifest["construction"]["requested_max_tokens"]),
        context_limit=LOCAL_CONTEXT_LIMIT,
        safety_margin_tokens=int(_required("CONSTRUCTION_CONTEXT_SAFETY_TOKENS")),
    )
    llm_config = LLMConfig(
        api_key=construction_key,
        model=LLM_MODEL_8B,
        small_model=LLM_MODEL_8B,
        base_url=native_url,
        temperature=0.0,
        max_tokens=config.requested_max_tokens,
    )
    structured_certificates: list[dict[str, Any]] = []
    llm_client_type = OpenAIGenericClient if strict_native else QwenVLLMClient
    llm_kwargs: dict[str, Any] = {
        "config": llm_config,
        "client": router,
        "max_tokens": config.requested_max_tokens,
        "structured_output_mode": config.structured_output_mode,
    }
    if not strict_native:
        llm_kwargs.update(
            {
                "structured_output_recovery_enabled": True,
                "vllm_options_enabled": True,
                "structured_output_token_counter": local_prompt_token_count,
                "structured_output_output_token_counter": lambda value: len(
                    _local_chat_tokenizer().encode(value, add_special_tokens=False)
                ),
                "structured_output_context_limit": LOCAL_CONTEXT_LIMIT,
                "structured_output_safety_margin": config.safety_margin_tokens,
                "structured_output_certificate_sink": structured_certificates.append,
                "managed_recovery_enabled": True,
            }
        )
    llm_client = llm_client_type(
        **llm_kwargs,
    )
    context_budget_restore: Callable[[], None] | None = None
    if not strict_native:
        install_local_single_attempt_policy(llm_client)
        context_budget_restore = install_local_context_budget_adapter(llm_client)
        install_local_extraction_chunking_policy(
            llm_client,
            partition_extraction_by_turns=True,
            partition_edge_candidates=True,
            # A one-entity default keeps the finite summary/dedupe response space
            # under the frozen completion bound even when the caller does not
            # provide an explicit paging override.  Larger capacities remain an
            # explicit, preflight-certified variant.
            summary_entity_page_capacity=summary_entity_page_capacity or 1,
            dedupe_candidate_page_capacity=dedupe_candidate_page_capacity or 1,
            node_partition_concurrency=NODE_PARTITION_WORKERS_8B,
            edge_page_capacity=2,
            actor_domain_cover=True,
            actor_domain_adjacent_domain=False,
            edge_partition_concurrency=EDGE_PARTITION_WORKERS_8B,
            edge_physical_concurrency=(
                int(manifest["construction"]["edge_physical_page_lanes"])
            ),
            edge_frontier_priority=True,
            edge_endpoint_schema_grounding=enable_endpoint_schema_grounding,
            edge_adaptive_admission=enable_adaptive_edge_admission,
            shared_bounded_structured_output=shared_bounded_structured_output,
        )
    llm_client._membind_structured_output_certificates = structured_certificates
    summary_restore: Callable[[], None] | None = None
    summary_evidence: list[dict[str, Any]] = []
    if not strict_native and enable_grounded_summary_materialization:
        diagnostics = getattr(llm_client, "_membind_extraction_diagnostics", None)
        if not isinstance(diagnostics, list):
            raise LocalRuntimeConfigurationError(
                "grounded summary evidence has no sealed diagnostics sink"
            )
        summary_restore, summary_evidence = install_grounded_summary_materialization(
            evidence_sink=diagnostics.append
        )
    shortcut_restore: Callable[[], None] | None = None
    prompt_restore: Callable[[], None] | None = None
    if not strict_native:
        shortcut_restore = install_empty_edge_shortcut(llm_client)
        prompt_restore = install_routing_prompt_context(llm_client)
    embedder = OpenAIEmbedder(
        OpenAIEmbedderConfig(
            api_key=embedding_key,
            base_url=config.embedding_base_url,
            embedding_model=config.embedding_model,
            embedding_dim=config.embedding_dimension,
        )
    )
    reranker = OpenAIRerankerClient(config=llm_config, client=router)
    graphiti = Graphiti(
        uri=config.neo4j_uri,
        user=neo4j_user,
        password=neo4j_password,
        llm_client=llm_client,
        embedder=embedder,
        cross_encoder=reranker,
        max_coroutines=config.max_coroutines,
    )
    runtime = U0Runtime(
        graphiti=graphiti,
        llm_client=llm_client,
        embedder=embedder,
        reranker=reranker,
        config=config,
        classification=(
            f"GRAPHITI_UPSTREAM_{PROFILE_ID_8B}_{manifest['construction']['routing_policy']}"
            if strict_native
            else f"U0_{PROFILE_ID_8B}_{manifest['construction']['routing_policy']}"
        ),
    )
    runtime._membind_owned_transports = (router, embedder.client)
    runtime._membind_runtime_closed = False
    runtime._membind_route_client = router
    runtime._membind_route_prompt_restore = prompt_restore
    runtime._membind_semantic_shortcut_restore = shortcut_restore
    runtime._membind_candidate_provenance_restore = resolution_restore
    runtime._membind_context_budget_restore = context_budget_restore
    runtime._membind_candidate_provenance_evidence = resolution_evidence
    runtime._membind_grounded_summary_restore = summary_restore
    runtime._membind_grounded_summary_evidence = summary_evidence
    runtime._membind_8b_runtime_manifest = manifest
    runtime._membind_strict_native = bool(strict_native)
    runtime._membind_shared_bounded_structured_output = bool(shared_bounded_structured_output)
    runtime._membind_shared_structured_output_identity = (
        adapter_identity() if shared_bounded_structured_output else None
    )
    runtime._membind_patch_inventory = {
        "schema_version": "membind.native-patch-inventory.v1",
        "status": "PASS",
        "strict_native": bool(strict_native),
        "prohibited_algorithm_patches": [] if strict_native else [
            "candidate_provenance_guard",
            "structured_output_recovery",
            "single_attempt_retry_override",
            "context_budget_adapter",
            "extraction_chunking_and_paging",
            "empty_edge_shortcut",
            "routing_prompt_context",
        ],
        "read_only_transport_adapters": ["RoutedOpenAIClient"],
        "graphiti_algorithm_mutated": False,
    }
    return runtime


def build_8b_strict_native_runtime(
    *,
    routing_contract: Mapping[str, Any],
    route_event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> U0Runtime:
    """Build A/B with no V6.1 runtime patch or recovery behavior installed."""

    return build_8b_u0_runtime(
        routing_contract=routing_contract,
        route_event_sink=route_event_sink,
        strict_native=True,
    )


def build_8b_shared_bounded_runtime(
    *,
    routing_contract: Mapping[str, Any],
    route_event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> U0Runtime:
    """Build the finite structured-output substrate shared by formal A/B/C."""

    runtime = build_8b_u0_runtime(
        routing_contract=routing_contract,
        route_event_sink=route_event_sink,
        enable_endpoint_schema_grounding=True,
        enable_work_conserving_edge_admission=True,
        enable_adaptive_edge_admission=False,
        shared_bounded_structured_output=True,
    )
    identity = getattr(runtime, "_membind_shared_structured_output_identity", None)
    if not isinstance(identity, Mapping) or identity.get("arm_identity") is not None:
        raise LocalRuntimeConfigurationError("shared structured-output identity is invalid")
    return runtime


def native_patch_inventory(runtime: Any) -> dict[str, Any]:
    """Return the machine-readable patch inventory sealed with a runtime."""

    value = getattr(runtime, "_membind_patch_inventory", None)
    if not isinstance(value, Mapping):
        raise LocalRuntimeConfigurationError("runtime patch inventory is missing")
    return dict(value)


async def close_8b_u0_runtime(runtime: U0Runtime) -> None:
    for name in (
        "_membind_context_budget_restore",
        "_membind_grounded_summary_restore",
        "_membind_route_prompt_restore",
        "_membind_semantic_shortcut_restore",
        "_membind_candidate_provenance_restore",
    ):
        restore = getattr(runtime, name, None)
        if callable(restore):
            restore()
            setattr(runtime, name, None)
    await close_local_u0_runtime(runtime)


def assert_8b_namespace_identity(namespace: str) -> None:
    if not isinstance(namespace, str) or not namespace.startswith(f"{PROFILE_ID_8B}-"):
        raise LocalRuntimeConfigurationError("namespace is outside the isolated 8B profile")
    if _FOREIGN_MODEL_NAMESPACE_TOKEN_RE.search(namespace):
        raise LocalRuntimeConfigurationError("8B namespace mixes another model identity")


def frozen_8b_config(
    routing_contract: Mapping[str, Any],
    *,
    strict_native: bool = False,
    enable_grounded_summary_materialization: bool = False,
    enable_endpoint_schema_grounding: bool = False,
    enable_work_conserving_edge_admission: bool = False,
    enable_adaptive_edge_admission: bool = False,
    shared_bounded_structured_output: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": "membind.local-qwen3-8b-dual.config.v1",
        "status": "FROZEN_FOR_RESOURCE_MATCHED_CAMPAIGN",
        **runtime_8b_manifest(
            routing_contract,
            strict_native=strict_native,
            enable_grounded_summary_materialization=enable_grounded_summary_materialization,
            enable_endpoint_schema_grounding=enable_endpoint_schema_grounding,
            enable_work_conserving_edge_admission=enable_work_conserving_edge_admission,
            enable_adaptive_edge_admission=enable_adaptive_edge_admission,
            shared_bounded_structured_output=shared_bounded_structured_output,
        ),
    }


def public_8b_environment(
    routing_contract: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    strict_native: bool = False,
    enable_grounded_summary_materialization: bool = False,
    enable_endpoint_schema_grounding: bool = False,
    enable_work_conserving_edge_admission: bool = False,
    enable_adaptive_edge_admission: bool = False,
    shared_bounded_structured_output: bool = False,
) -> dict[str, Any]:
    manifest = runtime_8b_manifest(
        routing_contract,
        strict_native=strict_native,
        enable_grounded_summary_materialization=enable_grounded_summary_materialization,
        enable_endpoint_schema_grounding=enable_endpoint_schema_grounding,
        enable_work_conserving_edge_admission=enable_work_conserving_edge_admission,
        enable_adaptive_edge_admission=enable_adaptive_edge_admission,
        shared_bounded_structured_output=shared_bounded_structured_output,
    )
    manifest["repo_root"] = str((repo_root or Path(__file__).resolve().parents[4]).resolve())
    manifest["python"] = os.path.realpath(os.sys.executable)
    manifest["runtime_manifest_sha256"] = _canonical_hash(manifest)
    return manifest


__all__ = [
    "PROFILE_ID_8B",
    "assert_8b_namespace_identity",
    "build_8b_u0_runtime",
    "build_8b_shared_bounded_runtime",
    "build_8b_strict_native_runtime",
    "native_patch_inventory",
    "close_8b_u0_runtime",
    "frozen_8b_config",
    "install_empty_edge_shortcut",
    "load_8b_platform_manifest",
    "load_8b_routing_contract",
    "public_8b_environment",
    "runtime_8b_manifest",
]
