"""Secret-free, database-free diagnostics for V7 structured extraction.

The campaign runner is intentionally too expensive to use as a provider
compatibility probe.  This module reconstructs exact Graphiti node and edge
extraction requests, executes at most one HTTP attempt per probe, and returns
only hashes, sizes, timing, usage, and coarse outcome classifications.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel
from typing_extensions import Literal


class V7ProviderDiagnosticError(ValueError):
    """The provider diagnostic contract is invalid."""


@dataclass(frozen=True, slots=True)
class StructuredExtractionProbe:
    """In-memory raw request plus its persistable, content-free projection."""

    request: dict[str, Any]
    response_model: type[Any]
    evidence: dict[str, Any]
    probe_kind: str
    result_field: str | None


@dataclass(frozen=True, slots=True)
class StructuredExtractionExecution:
    """Sanitized evidence plus parsed values retained only in process memory."""

    result: dict[str, Any]
    parsed_items: tuple[Any, ...]


class _EngineeringSchemaAck(BaseModel):
    status: Literal["ok"]


_BAILIAN_ENGINEERING_IDENTITY_V1 = {
    "schema_version": "membind.v7.engineering-provider-freeze.v1",
    "status": "ENGINEERING_VALIDATION_ONLY",
    "authority": "alibaba-bailian-openai-compatible-engineering-v1",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "construction_model": "qwen3.5-35b-a3b",
    "api_key_env": "DASHSCOPE_API_KEY",
    "temperature": 0.0,
    "top_p": 1.0,
    "structured_output_mode": "json_schema",
    "sdk_max_retries": 0,
    "hard_attempt_limit_per_probe": 1,
    "formal_r1_r3_eligible": False,
    "gate_a_e_evaluated": False,
    "gate_outcome": "NOT_EVALUATED",
    "treatment_authorized": False,
    "diagnostic_only": True,
    "scientific_method_selection_update_allowed": False,
    "database_allowed": False,
    "embedding_allowed": False,
    "response_replay_allowed": False,
    "raw_request_persistence_allowed": False,
    "raw_response_persistence_allowed": False,
    "credential_persistence_allowed": False,
}

_BAILIAN_ENGINEERING_IDENTITY_V2 = {
    **_BAILIAN_ENGINEERING_IDENTITY_V1,
    "schema_version": "membind.v7.engineering-provider-freeze.v2",
    "authority": "alibaba-bailian-openai-compatible-engineering-json-object-v1",
    "structured_output_mode": "json_object",
}


def load_engineering_provider_freeze(path: str | Path) -> dict[str, Any]:
    """Load the fixed Bailian engineering identity and reject authority drift."""

    selected = Path(path)
    try:
        value = json.loads(selected.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise V7ProviderDiagnosticError(
            "engineering provider freeze is unreadable"
        ) from error
    if not isinstance(value, dict):
        raise V7ProviderDiagnosticError("engineering provider freeze is invalid")
    identities = {
        "membind.v7.engineering-provider-freeze.v1": _BAILIAN_ENGINEERING_IDENTITY_V1,
        "membind.v7.engineering-provider-freeze.v2": _BAILIAN_ENGINEERING_IDENTITY_V2,
    }
    identity = identities.get(value.get("schema_version"))
    if identity is None:
        raise V7ProviderDiagnosticError(
            "engineering-only provider freeze version is unsupported"
        )
    for field, expected in identity.items():
        if value.get(field) != expected:
            raise V7ProviderDiagnosticError(
                f"engineering-only provider freeze field drifted: {field}"
            )
    if value.get("request_extension") != {
        "extra_body": {"enable_thinking": False}
    }:
        raise V7ProviderDiagnosticError(
            "engineering-only provider request extension drifted"
        )
    budgets = value.get("probe_budgets")
    expected_budgets = {
        "minimal_json_schema_max_tokens": 256,
        "extract_nodes_extract_message_max_tokens": 16_384,
        "extract_edges_edge_max_tokens": 16_384,
    }
    if budgets != expected_budgets:
        raise V7ProviderDiagnosticError("engineering-only probe budgets drifted")
    workload = value.get("probe_workload")
    if (
        not isinstance(workload, dict)
        or workload.get("dataset_sha256")
        != "97fd80207f3419fc57c3684db824334224546d6bdd62c17ef52cd116eec9ffc8"
        or workload.get("context_index") != 1
        or workload.get("source_sequence") != 0
    ):
        raise V7ProviderDiagnosticError("engineering-only probe workload drifted")
    if value.get("embedding") != {
        "status": "NOT_FROZEN",
        "model": None,
        "dimension": None,
    }:
        raise V7ProviderDiagnosticError(
            "engineering-only embedding identity must remain unfrozen"
        )
    if value["schema_version"] == "membind.v7.engineering-provider-freeze.v2":
        if value.get("schema_policy") != {
            "prompt_schema_injection": "graphiti-json-object-constrained-pydantic-v1",
            "response_validation": "pydantic-v2",
            "free_form_acceptance": False,
        }:
            raise V7ProviderDiagnosticError(
                "engineering-only JSON Object schema policy drifted"
            )
        if value.get("output_limit_policy") != {
            "max_tokens_sent": False,
            "logical_graphiti_node_max_tokens": 16_384,
            "logical_graphiti_edge_max_tokens": 16_384,
            "provider_output_limit": "PROVIDER_DEFAULT_UNKNOWN",
            "completion_requirement": "finish_reason_stop_and_pydantic_valid",
        }:
            raise V7ProviderDiagnosticError(
                "engineering-only JSON Object output policy drifted"
            )
        contract = value.get("provider_contract_evidence")
        if (
            not isinstance(contract, dict)
            or contract.get("url")
            != "https://help.aliyun.com/zh/model-studio/qwen-structured-output"
            or contract.get("response_content_bytes") != 263_087
            or contract.get("response_content_sha256")
            != "1f4eedf51e17b12c0a39ef6394dd69ffd303795a7f31994420e85b53152ab183"
            or contract.get("qwen3_5_open_source_json_object_supported") is not True
            or contract.get("qwen3_5_open_source_json_schema_listed") is not False
            or contract.get("json_schema_strict_true_documented") is not True
            or contract.get("structured_output_disable_max_tokens_documented")
            is not True
        ):
            raise V7ProviderDiagnosticError(
                "engineering-only provider contract evidence drifted"
            )
        reauthorization = value.get("infrastructure_reauthorization")
        if (
            not isinstance(reauthorization, dict)
            or reauthorization.get("previous_provider_freeze_sha256")
            != "9ad6994b4b3bb64a2d4ff46c78c97d72b8c7fd2ca65127233969b61e3dd4052b"
            or reauthorization.get("failed_probe_artifact_sha256")
            != [
                "bc8cbd1307e1a50a3e6644dcb0f908042ee97466350f21bd5617f9361c51592e",
                "f547c526a1da9cf00907aa98593a47fa4035b221be8f4aac7f3635f6e74ee63b",
            ]
            or reauthorization.get("changed_fields")
            != [
                "authority",
                "structured_output_mode",
                "schema_policy",
                "output_limit_policy",
            ]
        ):
            raise V7ProviderDiagnosticError(
                "engineering-only JSON Object reauthorization drifted"
            )
    model_probe = value.get("model_list_identity_probe")
    if (
        not isinstance(model_probe, dict)
        or model_probe.get("http_status") != 200
        or model_probe.get("target_present") is not True
        or model_probe.get("model_count") != 241
        or model_probe.get("response_content_bytes") != 21_392
        or model_probe.get("response_content_sha256")
        != "3a6834f03f8d80ec974e82bb68c2144f42b76f3f2ae68e2d7d48fb1270b010a1"
    ):
        raise V7ProviderDiagnosticError(
            "engineering-only model-list identity evidence drifted"
        )
    return deepcopy(value)


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise V7ProviderDiagnosticError(
            "engineering provider freeze cannot be hashed"
        ) from error


def build_bailian_engineering_artifact(
    *,
    run_id: str,
    provider_freeze_path: str | Path,
    dataset_sha256: str,
    source_sha256: Mapping[str, str],
    timeout_seconds: float,
    chain_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind a sanitized construction probe chain to its engineering authority."""

    if (
        not isinstance(run_id, str)
        or not run_id
        or "/" in run_id
        or "\\" in run_id
    ):
        raise V7ProviderDiagnosticError("engineering probe run id is invalid")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise V7ProviderDiagnosticError("engineering probe timeout is invalid")
    selected_path = Path(provider_freeze_path)
    provider = load_engineering_provider_freeze(selected_path)
    expected_dataset = provider["probe_workload"]["dataset_sha256"]
    if dataset_sha256 != expected_dataset:
        raise V7ProviderDiagnosticError(
            "engineering probe dataset differs from provider freeze"
        )
    if not source_sha256:
        raise V7ProviderDiagnosticError("engineering probe source binding is empty")
    selected_sources: dict[str, str] = {}
    for name, digest in sorted(source_sha256.items()):
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise V7ProviderDiagnosticError(
                "engineering probe source binding is invalid"
            )
        selected_sources[name] = digest
    if not isinstance(chain_result, Mapping):
        raise V7ProviderDiagnosticError("engineering probe chain result is invalid")
    required_chain_values = {
        "database_called": False,
        "embedding_called": False,
        "treatment_calls": 0,
        "response_replay_calls": 0,
        "formal_r1_r3_eligible": False,
        "gate_a_e_evaluated": False,
        "gate_outcome": "NOT_EVALUATED",
        "treatment_authorized": False,
        "diagnostic_only": True,
        "scientific_method_selection_updated": False,
        "raw_request_persisted": False,
        "raw_response_persisted": False,
        "credentials_recorded": False,
    }
    for field, expected in required_chain_values.items():
        if chain_result.get(field) != expected:
            raise V7ProviderDiagnosticError(
                f"engineering-only probe chain field drifted: {field}"
            )
    expected_max_tokens_sent = (
        provider.get("output_limit_policy", {}).get("max_tokens_sent")
        if isinstance(provider.get("output_limit_policy"), Mapping)
        else True
    )
    if (
        chain_result.get("structured_output_mode")
        != provider["structured_output_mode"]
        or chain_result.get("max_tokens_sent") is not expected_max_tokens_sent
    ):
        raise V7ProviderDiagnosticError(
            "engineering-only probe chain transport differs from provider freeze"
        )
    chain = deepcopy(dict(chain_result))
    artifact = {
        "schema_version": "membind.v7.bailian-engineering-probe-artifact.v1",
        "status": chain.get("status"),
        "classification": chain.get("classification"),
        "mode": "ENGINEERING_PROBE",
        "run_id": run_id,
        "provider": {
            "authority": provider["authority"],
            "base_url": provider["base_url"],
            "construction_model": provider["construction_model"],
            "temperature": provider["temperature"],
            "top_p": provider["top_p"],
            "structured_output_mode": provider["structured_output_mode"],
            "enable_thinking": provider["request_extension"]["extra_body"][
                "enable_thinking"
            ],
            "sdk_max_retries": provider["sdk_max_retries"],
            "hard_attempt_limit_per_probe": provider[
                "hard_attempt_limit_per_probe"
            ],
            "schema_policy": deepcopy(provider.get("schema_policy")),
            "output_limit_policy": deepcopy(provider.get("output_limit_policy")),
        },
        "provider_freeze_path": selected_path.name,
        "provider_freeze_sha256": _sha256_file(selected_path),
        "dataset_sha256": dataset_sha256,
        "source_sha256": selected_sources,
        "timeout_seconds": float(timeout_seconds),
        "construction_probe": chain,
        "engineering_observer_eligible": chain.get("status") == "PASS",
        "embedding_identity_status": provider["embedding"]["status"],
        "database_called": False,
        "embedding_called": False,
        "treatment_calls": 0,
        "response_replay_calls": 0,
        "formal_r1_r3_eligible": False,
        "gate_a_e_evaluated": False,
        "gate_outcome": "NOT_EVALUATED",
        "treatment_authorized": False,
        "diagnostic_only": True,
        "scientific_method_selection_updated": False,
        "raw_request_persisted": False,
        "raw_response_persisted": False,
        "credentials_recorded": False,
    }
    encoded = json.dumps(artifact, ensure_ascii=True, sort_keys=True, allow_nan=False)
    for forbidden in ("Authorization", "api_key", "episode_body"):
        if forbidden in encoded:
            raise V7ProviderDiagnosticError(
                "engineering probe artifact contains a forbidden field"
            )
    return artifact


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _episode_node(episode: Any, *, namespace: str) -> Any:
    from graphiti_core.nodes import EpisodeType, EpisodicNode

    from .observer_campaign import _reference_time

    sequence = _field(episode, "source_sequence")
    context_id = _field(episode, "context_id")
    body = _field(episode, "body")
    reference_time = _field(episode, "reference_time")
    if (
        isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 0
        or not isinstance(context_id, str)
        or not context_id
        or not isinstance(body, str)
        or not body
        or reference_time is None
    ):
        raise V7ProviderDiagnosticError("structured probe episode is incomplete")
    return EpisodicNode(
        name=f"{context_id}::episode::{sequence:04d}",
        group_id=namespace,
        labels=[],
        source=EpisodeType.message,
        source_description="MemoryAgentBench LongMemEval session",
        content=body,
        valid_at=_reference_time(reference_time),
    )


def _validate_request_identity(*, model: str, max_tokens: int) -> None:
    if not isinstance(model, str) or not model:
        raise V7ProviderDiagnosticError("structured probe model is missing")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise V7ProviderDiagnosticError("structured probe max_tokens is invalid")


def _effective_schema(response_model: type[Any]) -> dict[str, Any]:
    from structured_output import constrain_single_episode_indices

    wrapper = constrain_single_episode_indices(
        {"schema": response_model.model_json_schema()}
    )
    return wrapper["schema"]


def _response_format(
    response_model: type[Any], *, structured_output_mode: str
) -> dict[str, Any]:
    if structured_output_mode == "json_object":
        return {"type": "json_object"}
    if structured_output_mode != "json_schema":
        raise V7ProviderDiagnosticError(
            "structured probe output mode is unsupported"
        )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": response_model.__name__,
            "schema": _effective_schema(response_model),
        },
    }


def _request(
    *,
    model: str,
    messages: Sequence[Any],
    response_model: type[Any],
    max_tokens: int,
    structured_output_mode: str,
    send_max_tokens: bool,
) -> dict[str, Any]:
    selected_messages = [
        {
            "role": message.get("role")
            if isinstance(message, Mapping)
            else message.role,
            "content": message.get("content")
            if isinstance(message, Mapping)
            else message.content,
        }
        for message in messages
    ]
    if structured_output_mode == "json_object":
        injected_schema = json.dumps(
            _effective_schema(response_model),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        selected_messages[-1]["content"] += (
            "\n\nRespond with a JSON object in the following format:\n\n"
            + injected_schema
        )
    request = {
        "model": model,
        "messages": selected_messages,
        "temperature": 0.0,
        "response_format": _response_format(
            response_model,
            structured_output_mode=structured_output_mode,
        ),
        "top_p": 1.0,
        "extra_body": {"enable_thinking": False},
    }
    if send_max_tokens:
        request["max_tokens"] = max_tokens
    return request


def _request_evidence(
    request: dict[str, Any],
    *,
    response_model: type[Any],
    logical_max_tokens: int,
    structured_output_mode: str,
    send_max_tokens: bool,
) -> dict[str, Any]:
    from graphiti_native import safe_structured_request_evidence

    result = safe_structured_request_evidence(request)
    result.update(
        {
            "logical_max_tokens": logical_max_tokens,
            "max_tokens_sent": send_max_tokens,
            "injected_json_schema_name": (
                response_model.__name__
                if structured_output_mode == "json_object"
                else None
            ),
            "injected_json_schema_sha256": (
                hashlib.sha256(
                    json.dumps(
                        _effective_schema(response_model),
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("ascii")
                ).hexdigest()
                if structured_output_mode == "json_object"
                else None
            ),
        }
    )
    return result


def build_minimal_json_schema_probe(
    *,
    model: str,
    max_tokens: int,
    structured_output_mode: str = "json_schema",
    send_max_tokens: bool = True,
) -> StructuredExtractionProbe:
    """Build a content-neutral provider JSON-schema compatibility probe."""

    _validate_request_identity(model=model, max_tokens=max_tokens)
    request = _request(
        model=model,
        messages=(
            {"role": "system", "content": "Return only the requested JSON value."},
            {"role": "user", "content": 'Return exactly {"status":"ok"}.'},
        ),
        response_model=_EngineeringSchemaAck,
        max_tokens=max_tokens,
        structured_output_mode=structured_output_mode,
        send_max_tokens=send_max_tokens,
    )
    return StructuredExtractionProbe(
        request=request,
        response_model=_EngineeringSchemaAck,
        evidence={
            "schema_version": "membind.v7.structured-extraction-probe-contract.v2",
            "mode": "one_exact_wire_attempt_no_database_no_embedding",
            "probe_kind": "minimal_json_schema",
            "database_called": False,
            "embedding_called": False,
            "response_replay_calls": 0,
            **_request_evidence(
                request,
                response_model=_EngineeringSchemaAck,
                logical_max_tokens=max_tokens,
                structured_output_mode=structured_output_mode,
                send_max_tokens=send_max_tokens,
            ),
        },
        probe_kind="minimal_json_schema",
        result_field=None,
    )


def build_structured_extraction_probe(
    *,
    episode: Any,
    previous_episodes: Sequence[Any],
    namespace: str,
    model: str,
    max_tokens: int,
    structured_output_mode: str = "json_schema",
    send_max_tokens: bool = True,
) -> StructuredExtractionProbe:
    """Reconstruct Graphiti's exact single-episode entity extraction request."""

    if not isinstance(namespace, str) or not namespace:
        raise V7ProviderDiagnosticError("structured probe namespace is missing")
    _validate_request_identity(model=model, max_tokens=max_tokens)

    from graphiti_core.llm_client.client import get_extraction_language_instruction
    from graphiti_core.prompts import prompt_library
    from graphiti_core.prompts.extract_nodes import ExtractedEntities
    from graphiti_core.utils.maintenance.node_operations import _build_entity_types_context
    from graphiti_core.utils.text_utils import concatenate_episodes

    current = _episode_node(episode, namespace=namespace)
    previous = [
        _episode_node(item, namespace=namespace) for item in previous_episodes
    ]
    expected_sequence = len(previous)
    if int(_field(episode, "source_sequence")) != expected_sequence:
        raise V7ProviderDiagnosticError(
            "structured probe previous-episode frontier is inconsistent"
        )
    context = {
        "episode_content": concatenate_episodes([current]),
        "episode_timestamp": current.valid_at.isoformat(),
        "previous_episodes": [
            {
                "content": item.content,
                "timestamp": item.valid_at.isoformat() if item.valid_at else None,
            }
            for item in previous
        ],
        "custom_extraction_instructions": "",
        "entity_types": _build_entity_types_context(None),
        "source_description": current.source_description,
    }
    messages = deepcopy(prompt_library.extract_nodes.extract_message(context))
    messages[0].content += get_extraction_language_instruction(namespace)
    request = _request(
        model=model,
        messages=messages,
        response_model=ExtractedEntities,
        max_tokens=max_tokens,
        structured_output_mode=structured_output_mode,
        send_max_tokens=send_max_tokens,
    )
    request_evidence = _request_evidence(
        request,
        response_model=ExtractedEntities,
        logical_max_tokens=max_tokens,
        structured_output_mode=structured_output_mode,
        send_max_tokens=send_max_tokens,
    )
    evidence = {
        "schema_version": "membind.v7.structured-extraction-probe-contract.v2",
        "mode": "one_exact_wire_attempt_no_database_no_embedding",
        "probe_kind": "extract_nodes.extract_message",
        "context_id_sha256": hashlib.sha256(
            str(_field(episode, "context_id")).encode("utf-8")
        ).hexdigest(),
        "source_sequence": int(_field(episode, "source_sequence")),
        "source_body_sha256": hashlib.sha256(
            str(_field(episode, "body")).encode("utf-8")
        ).hexdigest(),
        "previous_episode_count": len(previous),
        "namespace_sha256": hashlib.sha256(namespace.encode("utf-8")).hexdigest(),
        "database_called": False,
        "embedding_called": False,
        "response_replay_calls": 0,
        **request_evidence,
    }
    return StructuredExtractionProbe(
        request=request,
        response_model=ExtractedEntities,
        evidence=evidence,
        probe_kind="extract_nodes.extract_message",
        result_field="extracted_entities",
    )


def build_structured_edge_extraction_probe(
    *,
    episode: Any,
    previous_episodes: Sequence[Any],
    namespace: str,
    model: str,
    max_tokens: int,
    entity_names: Sequence[str],
    structured_output_mode: str = "json_schema",
    send_max_tokens: bool = True,
) -> StructuredExtractionProbe:
    """Reconstruct Graphiti's exact single-episode edge extraction request."""

    if not isinstance(namespace, str) or not namespace:
        raise V7ProviderDiagnosticError("structured edge probe namespace is missing")
    _validate_request_identity(model=model, max_tokens=max_tokens)
    if isinstance(entity_names, (str, bytes)):
        raise V7ProviderDiagnosticError("structured edge probe entity names are invalid")
    selected_names: list[str] = []
    for value in entity_names:
        if not isinstance(value, str) or not value.strip():
            raise V7ProviderDiagnosticError(
                "structured edge probe entity name is invalid"
            )
        selected = value.strip()
        if selected not in selected_names:
            selected_names.append(selected)
    if len(selected_names) < 2:
        raise V7ProviderDiagnosticError(
            "structured edge probe requires at least two entities"
        )

    from graphiti_core.llm_client.client import get_extraction_language_instruction
    from graphiti_core.prompts import prompt_library
    from graphiti_core.prompts.extract_edges import ExtractedEdges
    from graphiti_core.utils.text_utils import concatenate_episodes

    current = _episode_node(episode, namespace=namespace)
    previous = [
        _episode_node(item, namespace=namespace) for item in previous_episodes
    ]
    expected_sequence = len(previous)
    if int(_field(episode, "source_sequence")) != expected_sequence:
        raise V7ProviderDiagnosticError(
            "structured edge probe previous-episode frontier is inconsistent"
        )
    context = {
        "episode_content": concatenate_episodes([current]),
        "nodes": [
            {"name": name, "entity_types": ["Entity"]}
            for name in selected_names
        ],
        "previous_episodes": [
            {
                "content": item.content,
                "timestamp": item.valid_at.isoformat() if item.valid_at else None,
            }
            for item in previous
        ],
        "reference_time": current.valid_at,
        "edge_types": [],
        "custom_extraction_instructions": "",
    }
    messages = deepcopy(prompt_library.extract_edges.edge(context))
    messages[0].content += get_extraction_language_instruction(namespace)
    request = _request(
        model=model,
        messages=messages,
        response_model=ExtractedEdges,
        max_tokens=max_tokens,
        structured_output_mode=structured_output_mode,
        send_max_tokens=send_max_tokens,
    )
    evidence = {
        "schema_version": "membind.v7.structured-extraction-probe-contract.v2",
        "mode": "one_exact_wire_attempt_no_database_no_embedding",
        "probe_kind": "extract_edges.edge",
        "context_id_sha256": hashlib.sha256(
            str(_field(episode, "context_id")).encode("utf-8")
        ).hexdigest(),
        "source_sequence": int(_field(episode, "source_sequence")),
        "source_body_sha256": hashlib.sha256(
            str(_field(episode, "body")).encode("utf-8")
        ).hexdigest(),
        "previous_episode_count": len(previous),
        "entity_name_count": len(selected_names),
        "namespace_sha256": hashlib.sha256(namespace.encode("utf-8")).hexdigest(),
        "database_called": False,
        "embedding_called": False,
        "response_replay_calls": 0,
        **_request_evidence(
            request,
            response_model=ExtractedEdges,
            logical_max_tokens=max_tokens,
            structured_output_mode=structured_output_mode,
            send_max_tokens=send_max_tokens,
        ),
    }
    return StructuredExtractionProbe(
        request=request,
        response_model=ExtractedEdges,
        evidence=evidence,
        probe_kind="extract_edges.edge",
        result_field="edges",
    )


def _usage(value: Any) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        observed = value.get(field) if isinstance(value, Mapping) else getattr(value, field, None)
        result[field] = (
            observed
            if isinstance(observed, int) and not isinstance(observed, bool) and observed >= 0
            else None
        )
    return result


def _error_type(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__qualname__}"


def _classification(error: BaseException) -> str:
    status_code = getattr(error, "status_code", None)
    if (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and 400 <= status_code < 500
    ):
        return "STRUCTURED_EXTRACTION_REQUEST_REJECTED"
    names = {item.__name__ for item in type(error).__mro__}
    if isinstance(error, TimeoutError) or "APITimeoutError" in names:
        return "STRUCTURED_EXTRACTION_TIMEOUT"
    if names & {
        "JSONDecodeError",
        "ValidationError",
        "UnicodeDecodeError",
        "EmptyResponseError",
        "V7ProviderDiagnosticError",
    }:
        return "STRUCTURED_EXTRACTION_RESPONSE_INVALID"
    return "STRUCTURED_EXTRACTION_TRANSPORT_FAILURE"


def _safe_provider_error(error: BaseException) -> dict[str, Any] | None:
    status_code = getattr(error, "status_code", None)
    if (
        not isinstance(status_code, int)
        or isinstance(status_code, bool)
        or status_code < 100
        or status_code > 599
    ):
        status_code = None
    body = getattr(error, "body", None)
    body = body if isinstance(body, Mapping) else {}
    result: dict[str, Any] = {"http_status": status_code}
    allowed = frozenset(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/-"
    )
    for field in ("code", "param", "type"):
        value = getattr(error, field, None)
        if value is None:
            value = body.get(field)
        result[field] = (
            value
            if isinstance(value, str)
            and 0 < len(value) <= 128
            and all(character in allowed for character in value)
            else None
        )
    return result if any(value is not None for value in result.values()) else None


def _strip_code_fences(content: str) -> str:
    selected = content.strip()
    if selected.startswith("```json") and selected.endswith("```"):
        return selected[7:-3].strip()
    if selected.startswith("```") and selected.endswith("```"):
        return selected[3:-3].strip()
    return selected


async def _execute_structured_extraction_probe_async(
    probe: StructuredExtractionProbe,
    *,
    completions: Any,
    timeout_seconds: float,
) -> StructuredExtractionExecution:
    """Execute exactly one provider attempt and return secret-free evidence."""

    if not isinstance(probe, StructuredExtractionProbe):
        raise V7ProviderDiagnosticError("structured probe contract is invalid")
    if not callable(getattr(completions, "create", None)):
        raise V7ProviderDiagnosticError("structured probe completions client is invalid")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise V7ProviderDiagnosticError("structured probe timeout is invalid")

    started = time.monotonic_ns()
    finish_reason: Any = None
    observed_usage = _usage(None)
    response_content_sha256: str | None = None
    response_content_bytes: int | None = None
    parsed_items: tuple[Any, ...] = ()
    try:
        response = await asyncio.wait_for(
            completions.create(**deepcopy(probe.request)),
            timeout=float(timeout_seconds),
        )
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or len(choices) != 1:
            raise V7ProviderDiagnosticError("structured probe response choices are invalid")
        choice = choices[0]
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise V7ProviderDiagnosticError("structured probe response body is empty")
        content_bytes = content.encode("utf-8")
        response_content_bytes = len(content_bytes)
        response_content_sha256 = hashlib.sha256(content_bytes).hexdigest()
        finish_reason = _field(choice, "finish_reason")
        observed_usage = _usage(_field(response, "usage"))
        parsed = json.loads(_strip_code_fences(content))
        if not isinstance(parsed, Mapping):
            raise V7ProviderDiagnosticError("structured probe response root is invalid")
        validated = probe.response_model(**parsed)
        if probe.result_field is None:
            parsed_items = (validated,)
        else:
            selected_items = getattr(validated, probe.result_field, None)
            if not isinstance(selected_items, list):
                raise V7ProviderDiagnosticError(
                    "structured probe parsed item result is invalid"
                )
            parsed_items = tuple(selected_items)
        requested_max_tokens = probe.request.get("max_tokens")
        completion_tokens = observed_usage["completion_tokens"]
        complete = (
            finish_reason == "stop"
            and isinstance(completion_tokens, int)
            and (
                requested_max_tokens is None
                or (
                    isinstance(requested_max_tokens, int)
                    and completion_tokens < requested_max_tokens
                )
            )
        )
        accepted_items = parsed_items if complete else ()
        result: dict[str, Any] = {
            "schema_version": "membind.v7.structured-extraction-probe-result.v2",
            "status": "PASS" if complete else "FAIL",
            "classification": (
                "STRUCTURED_EXTRACTION_PARSED"
                if complete
                else "STRUCTURED_EXTRACTION_INCOMPLETE"
            ),
            "probe_kind": probe.probe_kind,
            "probe_contract": probe.evidence,
            "http_attempt_count": 1,
            "finish_reason": finish_reason,
            "usage": observed_usage,
            "parsed_item_count": len(accepted_items),
            "parsed_entity_count": (
                len(accepted_items)
                if probe.probe_kind == "extract_nodes.extract_message"
                else None
            ),
            "parsed_edge_count": (
                len(accepted_items)
                if probe.probe_kind == "extract_edges.edge"
                else None
            ),
            "response_content_bytes": response_content_bytes,
            "response_content_sha256": response_content_sha256,
            "provider_error": None,
            "error_type": None,
            "error_message_sha256": None,
        }
        parsed_items = accepted_items
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        result = {
            "schema_version": "membind.v7.structured-extraction-probe-result.v2",
            "status": "FAIL",
            "classification": _classification(error),
            "probe_kind": probe.probe_kind,
            "probe_contract": probe.evidence,
            "http_attempt_count": 1,
            "finish_reason": finish_reason,
            "usage": observed_usage,
            "parsed_item_count": None,
            "parsed_entity_count": None,
            "parsed_edge_count": None,
            "response_content_bytes": response_content_bytes,
            "response_content_sha256": response_content_sha256,
            "provider_error": _safe_provider_error(error),
            "error_type": _error_type(error),
            "error_message_sha256": hashlib.sha256(
                str(error).encode("utf-8", errors="backslashreplace")
            ).hexdigest(),
        }
    result.update(
        {
            "duration_ns": time.monotonic_ns() - started,
            "database_called": False,
            "embedding_called": False,
            "response_replay_calls": 0,
            "raw_request_persisted": False,
            "raw_response_persisted": False,
            "credentials_recorded": False,
        }
    )
    encoded = json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False)
    if (
        result.get("raw_request_persisted") is not False
        or result.get("raw_response_persisted") is not False
    ):
        raise V7ProviderDiagnosticError(
            "structured probe result retains a raw provider payload"
        )
    for forbidden in ("Authorization", "api_key", "episode_body"):
        if forbidden in encoded:
            raise V7ProviderDiagnosticError(
                "structured probe result contains a forbidden field"
            )
    return StructuredExtractionExecution(result=result, parsed_items=parsed_items)


async def run_structured_extraction_probe_async(
    probe: StructuredExtractionProbe,
    *,
    completions: Any,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Execute one provider attempt and discard all parsed values before return."""

    execution = await _execute_structured_extraction_probe_async(
        probe,
        completions=completions,
        timeout_seconds=timeout_seconds,
    )
    return execution.result


async def run_bailian_construction_probes_async(
    *,
    episode: Any,
    previous_episodes: Sequence[Any],
    namespace: str,
    model: str,
    minimal_max_tokens: int,
    node_max_tokens: int,
    edge_max_tokens: int,
    completions: Any,
    timeout_seconds: float,
    structured_output_mode: str = "json_schema",
    send_max_tokens: bool = True,
) -> dict[str, Any]:
    """Run the bounded minimal -> node -> edge engineering probe chain."""

    probes: list[dict[str, Any]] = []
    minimal = build_minimal_json_schema_probe(
        model=model,
        max_tokens=minimal_max_tokens,
        structured_output_mode=structured_output_mode,
        send_max_tokens=send_max_tokens,
    )
    minimal_execution = await _execute_structured_extraction_probe_async(
        minimal,
        completions=completions,
        timeout_seconds=timeout_seconds,
    )
    probes.append(minimal_execution.result)

    node_execution: StructuredExtractionExecution | None = None
    edge_execution: StructuredExtractionExecution | None = None
    if minimal_execution.result["status"] == "PASS":
        node = build_structured_extraction_probe(
            episode=episode,
            previous_episodes=previous_episodes,
            namespace=namespace,
            model=model,
            max_tokens=node_max_tokens,
            structured_output_mode=structured_output_mode,
            send_max_tokens=send_max_tokens,
        )
        node_execution = await _execute_structured_extraction_probe_async(
            node,
            completions=completions,
            timeout_seconds=timeout_seconds,
        )
        probes.append(node_execution.result)

    if node_execution is not None and node_execution.result["status"] == "PASS":
        entity_names: list[str] = []
        for item in node_execution.parsed_items:
            name = getattr(item, "name", None)
            if isinstance(name, str) and name.strip() and name.strip() not in entity_names:
                entity_names.append(name.strip())
        if len(entity_names) >= 2:
            edge = build_structured_edge_extraction_probe(
                episode=episode,
                previous_episodes=previous_episodes,
                namespace=namespace,
                model=model,
                max_tokens=edge_max_tokens,
                entity_names=entity_names,
                structured_output_mode=structured_output_mode,
                send_max_tokens=send_max_tokens,
            )
            edge_execution = await _execute_structured_extraction_probe_async(
                edge,
                completions=completions,
                timeout_seconds=timeout_seconds,
            )
            probes.append(edge_execution.result)

    passed = (
        len(probes) == 3
        and edge_execution is not None
        and all(item.get("status") == "PASS" for item in probes)
    )
    return {
        "schema_version": "membind.v7.bailian-construction-probe-chain.v1",
        "status": "PASS" if passed else "FAIL",
        "classification": (
            "BAILIAN_CONSTRUCTION_COMPATIBLE"
            if passed
            else "BAILIAN_CONSTRUCTION_INCOMPATIBLE"
        ),
        "construction_probe_passed": passed,
        "structured_output_mode": structured_output_mode,
        "max_tokens_sent": send_max_tokens,
        "probes": probes,
        "probe_order_complete": len(probes) == 3,
        "http_attempt_count": sum(
            int(item.get("http_attempt_count") or 0) for item in probes
        ),
        "database_called": False,
        "embedding_called": False,
        "treatment_calls": 0,
        "response_replay_calls": 0,
        "formal_r1_r3_eligible": False,
        "gate_a_e_evaluated": False,
        "gate_outcome": "NOT_EVALUATED",
        "treatment_authorized": False,
        "diagnostic_only": True,
        "scientific_method_selection_updated": False,
        "raw_request_persisted": False,
        "raw_response_persisted": False,
        "credentials_recorded": False,
    }


__all__ = [
    "StructuredExtractionExecution",
    "StructuredExtractionProbe",
    "V7ProviderDiagnosticError",
    "build_bailian_engineering_artifact",
    "build_minimal_json_schema_probe",
    "build_structured_edge_extraction_probe",
    "build_structured_extraction_probe",
    "load_engineering_provider_freeze",
    "run_bailian_construction_probes_async",
    "run_structured_extraction_probe_async",
]
