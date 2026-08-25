"""Secret-free, database-free diagnostics for V7 structured extraction.

The campaign runner is intentionally too expensive to use as a provider
compatibility probe.  This module reconstructs the exact first Graphiti entity
extraction wire request, executes at most one HTTP attempt, and returns only
hashes, sizes, timing, usage, and a coarse outcome classification.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class V7ProviderDiagnosticError(ValueError):
    """The provider diagnostic contract is invalid."""


@dataclass(frozen=True, slots=True)
class StructuredExtractionProbe:
    """In-memory raw request plus its persistable, content-free projection."""

    request: dict[str, Any]
    response_model: type[Any]
    evidence: dict[str, Any]


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


def build_structured_extraction_probe(
    *,
    episode: Any,
    previous_episodes: Sequence[Any],
    namespace: str,
    model: str,
    max_tokens: int,
) -> StructuredExtractionProbe:
    """Reconstruct Graphiti's exact single-episode entity extraction request."""

    if not isinstance(namespace, str) or not namespace:
        raise V7ProviderDiagnosticError("structured probe namespace is missing")
    if not isinstance(model, str) or not model:
        raise V7ProviderDiagnosticError("structured probe model is missing")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
        raise V7ProviderDiagnosticError("structured probe max_tokens is invalid")

    from graphiti_core.llm_client.client import get_extraction_language_instruction
    from graphiti_core.prompts import prompt_library
    from graphiti_core.prompts.extract_nodes import ExtractedEntities
    from graphiti_core.utils.maintenance.node_operations import _build_entity_types_context
    from graphiti_core.utils.text_utils import concatenate_episodes
    from graphiti_native import safe_structured_request_evidence
    from structured_output import constrain_single_episode_indices

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
    response_format = constrain_single_episode_indices(
        {
            "type": "json_schema",
            "json_schema": {
                "name": "ExtractedEntities",
                "schema": ExtractedEntities.model_json_schema(),
            },
        }
    )
    request = {
        "model": model,
        "messages": [
            {"role": message.role, "content": message.content} for message in messages
        ],
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "response_format": response_format,
        "top_p": 1.0,
        "extra_body": {"enable_thinking": False},
    }
    request_evidence = safe_structured_request_evidence(request)
    evidence = {
        "schema_version": "membind.v7.structured-extraction-probe-contract.v1",
        "mode": "one_exact_wire_attempt_no_database_no_embedding",
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
    names = {item.__name__ for item in type(error).__mro__}
    if isinstance(error, TimeoutError) or "APITimeoutError" in names:
        return "STRUCTURED_EXTRACTION_TIMEOUT"
    if names & {
        "JSONDecodeError",
        "ValidationError",
        "UnicodeDecodeError",
        "EmptyResponseError",
    }:
        return "STRUCTURED_EXTRACTION_RESPONSE_INVALID"
    return "STRUCTURED_EXTRACTION_TRANSPORT_FAILURE"


def _strip_code_fences(content: str) -> str:
    selected = content.strip()
    if selected.startswith("```json") and selected.endswith("```"):
        return selected[7:-3].strip()
    if selected.startswith("```") and selected.endswith("```"):
        return selected[3:-3].strip()
    return selected


async def run_structured_extraction_probe_async(
    probe: StructuredExtractionProbe,
    *,
    completions: Any,
    timeout_seconds: float,
) -> dict[str, Any]:
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
        parsed = json.loads(_strip_code_fences(content))
        if not isinstance(parsed, Mapping):
            raise V7ProviderDiagnosticError("structured probe response root is invalid")
        validated = probe.response_model(**parsed)
        entities = getattr(validated, "extracted_entities", None)
        if not isinstance(entities, list):
            raise V7ProviderDiagnosticError("structured probe entity result is invalid")
        result: dict[str, Any] = {
            "schema_version": "membind.v7.structured-extraction-probe-result.v1",
            "status": "PASS",
            "classification": "STRUCTURED_EXTRACTION_PARSED",
            "probe_contract": probe.evidence,
            "http_attempt_count": 1,
            "finish_reason": getattr(choice, "finish_reason", None),
            "usage": _usage(getattr(response, "usage", None)),
            "parsed_entity_count": len(entities),
            "response_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "error_type": None,
            "error_message_sha256": None,
        }
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        result = {
            "schema_version": "membind.v7.structured-extraction-probe-result.v1",
            "status": "FAIL",
            "classification": _classification(error),
            "probe_contract": probe.evidence,
            "http_attempt_count": 1,
            "finish_reason": None,
            "usage": _usage(None),
            "parsed_entity_count": None,
            "response_content_sha256": None,
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
    return result


__all__ = [
    "StructuredExtractionProbe",
    "V7ProviderDiagnosticError",
    "build_structured_extraction_probe",
    "run_structured_extraction_probe_async",
]
