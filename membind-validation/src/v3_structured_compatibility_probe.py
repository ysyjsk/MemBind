"""Exact, database-free compatibility probe for the V3 extraction request.

The probe reconstructs Graphiti's installed entity-extraction prompt from the
frozen dataset and submits only that structured LLM request. It never creates a
Graphiti runtime, calls embedding, or touches Neo4j. Persisted output contains
only prompt/response hashes, lengths, token usage, and safe request evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from dataset import build_episodes, load_json_records, records_by_question_id, sha256_file
from graphiti_native import (
    DEFAULT_CONSTRUCTION_MODEL,
    QwenVLLMClient,
    parse_datetime,
    safe_structured_request_evidence,
    structured_retry_budgets,
)
from structured_output import constrain_single_episode_indices


SCHEMA_VERSION = "membind.v3.actual_schema_compatibility_probe.v1"
HISTORICAL_RESPONSE_HASHES = {
    2_048: "d9340f0bc347bbb5d7049aa55bee2739485755444f28e216e523c4bd3a5b0a16",
    8_192: "94fb64c3921b3e1e7bfecee99e6faa00e620fea7f949cc0d839d8b185035aef0",
}
HISTORICAL_PROMPT_TOKENS = {0: 4_515, 1: 5_795}


@dataclass(frozen=True)
class ExtractionProbe:
    """In-memory request plus a redacted contract suitable for persistence."""

    messages: list[Any]
    response_model: type[Any]
    evidence: dict[str, Any]


def _episode_node(episode: Any) -> Any:
    from graphiti_core.nodes import EpisodeType, EpisodicNode

    return EpisodicNode(
        name=episode.name,
        group_id=episode.group_id,
        labels=[],
        source=EpisodeType.message,
        source_description="LongMemEval-S haystack session",
        content=episode.body,
        valid_at=parse_datetime(episode.reference_time),
    )


def build_extraction_probe(
    data_path: str | Path,
    question_id: str,
    *,
    source_sequence: int,
) -> ExtractionProbe:
    """Reconstruct Graphiti's exact extraction prompt without querying Neo4j."""

    from graphiti_core.prompts import prompt_library
    from graphiti_core.prompts.extract_nodes import ExtractedEntities
    from graphiti_core.llm_client.client import get_extraction_language_instruction
    from graphiti_core.utils.maintenance.node_operations import (
        _build_entity_types_context,
    )
    from graphiti_core.utils.text_utils import concatenate_episodes

    data_path = Path(data_path)
    records = records_by_question_id(load_json_records(data_path))
    if question_id not in records:
        raise KeyError(f"question id not found: {question_id}")
    episodes = build_episodes(records[question_id])
    if source_sequence < 0 or source_sequence >= len(episodes):
        raise IndexError(f"source sequence out of range: {source_sequence}")
    # The target is source 1, so the complete preceding database context is the
    # single source-0 episode. Keeping the generic slice makes the assumption
    # explicit without querying a live database.
    current = _episode_node(episodes[source_sequence])
    previous = [_episode_node(episode) for episode in episodes[:source_sequence]]
    context = {
        "episode_content": concatenate_episodes([current]),
        "episode_timestamp": current.valid_at.isoformat(),
        "previous_episodes": [
            {
                "content": episode.content,
                "timestamp": episode.valid_at.isoformat() if episode.valid_at else None,
            }
            for episode in previous
        ],
        "custom_extraction_instructions": "",
        "entity_types": _build_entity_types_context(None),
        "source_description": current.source_description,
    }
    messages = prompt_library.extract_nodes.extract_message(context)
    pre_wrapper_hashes = [
        hashlib.sha256(message.content.encode("utf-8")).hexdigest()
        for message in messages
    ]
    pre_wrapper_lengths = [len(message.content) for message in messages]
    wire_messages = deepcopy(messages)
    wire_messages[0].content += get_extraction_language_instruction(question_id)
    response_format = constrain_single_episode_indices(
        {
            "type": "json_schema",
            "json_schema": {
                "name": "ExtractedEntities",
                "schema": ExtractedEntities.model_json_schema(),
            },
        }
    )
    openai_messages = [
        {"role": message.role, "content": message.content} for message in wire_messages
    ]
    request = {
        "model": DEFAULT_CONSTRUCTION_MODEL,
        "messages": openai_messages,
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": 2_048,
        "response_format": response_format,
        "seed": 20260806,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    request_evidence = safe_structured_request_evidence(request)
    current_episode = episodes[source_sequence]
    return ExtractionProbe(
        messages=messages,
        response_model=ExtractedEntities,
        evidence={
            "question_id": str(question_id),
            "source_sequence": int(source_sequence),
            "historical_prompt_tokens": HISTORICAL_PROMPT_TOKENS.get(
                source_sequence
            ),
            "source_hash": current_episode.source_hash,
            "previous_source_hashes": [
                episode.source_hash for episode in episodes[:source_sequence]
            ],
            "dataset_path": str(data_path),
            "dataset_sha256": sha256_file(data_path),
            "budget_sequence": list(structured_retry_budgets(16_384, 2_048, 8_192)),
            "database_called": False,
            "embedding_called": False,
            "pre_wrapper_message_content_sha256": pre_wrapper_hashes,
            "pre_wrapper_message_content_lengths": pre_wrapper_lengths,
            "wrapper_language_instruction_applied": True,
            "public_generate_response_path": True,
            **request_evidence,
        },
    )


def sanitize_failure_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove all response bodies and unknown fields from client failure events."""

    sanitized = []
    for index, record in enumerate(records):
        usage = record.get("token_usage")
        usage = usage if isinstance(usage, dict) else {}
        request_evidence = record.get("request_evidence")
        request_evidence = (
            dict(request_evidence) if isinstance(request_evidence, dict) else None
        )
        sanitized.append(
            {
                "ordinal": index,
                "failure_type": record.get("failure_type"),
                "max_tokens": int(record.get("max_tokens") or 0),
                "finish_reason": record.get("finish_reason"),
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
                "body_length": int(record.get("raw_response_length") or 0),
                "body_sha256": str(record.get("raw_response_sha256") or ""),
                "request_evidence": request_evidence,
            }
        )
    return sanitized


def classify_compatibility_result(
    observed_events: list[dict[str, Any]],
    *,
    parsed: bool,
) -> str:
    """Classify only exact outcomes needed by the frozen V3 service gate."""

    if parsed:
        return "frozen_actual_schema_request_parsed"
    budgets = list(HISTORICAL_RESPONSE_HASHES)
    if observed_events and len(observed_events) % len(budgets) == 0:
        exact = all(
            event.get("max_tokens") == budget
            and event.get("finish_reason") == "length"
            and event.get("completion_tokens") == budget
            and event.get("body_sha256") == HISTORICAL_RESPONSE_HASHES[budget]
            for index, event in enumerate(observed_events)
            for budget in (budgets[index % len(budgets)],)
        )
        if exact:
            return "exact_historical_truncation_reproduced"
    if observed_events:
        return "structured_failure_not_bitwise_identical_to_history"
    return "no_structured_response_observed"


def correct_compatibility_artifact(source: str | Path) -> dict[str, Any]:
    """Recompute derived fields from a body-free immutable probe artifact."""

    source = Path(source)
    result = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise TypeError("compatibility artifact root must be an object")
    observed = result.get("observed_events")
    if not isinstance(observed, list):
        raise TypeError("compatibility artifact lacks observed events")
    parsed = result.get("error_type") is None and result.get("parsed_entity_count") is not None
    high_level_attempt_count = int(result.get("structured_request_count") or 0)
    result["schema_version"] = "membind.v3.actual_schema_compatibility_probe.v2"
    result["classification"] = classify_compatibility_result(
        observed,
        parsed=parsed,
    )
    result["high_level_attempt_count"] = high_level_attempt_count
    result["outer_retry_count"] = max(0, high_level_attempt_count - 1)
    result["ok"] = bool(
        parsed and result.get("prompt_token_count_matches_history") is True
    )
    result["model_called_during_correction"] = False
    result["superseded_artifact"] = {
        "path": str(source),
        "sha256": sha256_file(source),
        "size_bytes": source.stat().st_size,
        "reason": "derived classification assumed one retry pair and outer_retry_count was hardcoded",
    }
    return result


def write_corrected_compatibility_artifact(
    source: str | Path,
    output: str | Path,
) -> dict[str, Any]:
    """Persist corrected derived fields without issuing another model request."""

    result = correct_compatibility_artifact(source)
    encoded = json.dumps(
        result,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="ascii", newline="\n") as handle:
        handle.write(encoded)
    return result


def _success_event(client: Any) -> list[dict[str, Any]]:
    record = client.consume_last_call_record()
    if not isinstance(record, dict):
        return []
    body = record.get("raw_response")
    body = body if isinstance(body, str) else ""
    usage = record.get("token_usage")
    usage = usage if isinstance(usage, dict) else {}
    call_event = client.call_events[-1] if client.call_events else {}
    return [
        {
            "ordinal": 0,
            "failure_type": None,
            "max_tokens": int(record.get("max_tokens") or 0),
            "finish_reason": call_event.get("finish_reason"),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "body_length": len(body),
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        }
    ]


async def run_compatibility_probe(
    data_path: str | Path,
    question_id: str,
    *,
    source_sequence: int = 1,
) -> dict[str, Any]:
    """Submit one bounded exact request trajectory and return redacted evidence."""

    from graphiti_core.llm_client.config import LLMConfig

    probe = build_extraction_probe(
        data_path,
        question_id,
        source_sequence=source_sequence,
    )
    api_key = os.environ.get("CONSTRUCTION_LLM_API_KEY") or os.environ.get(
        "VLLM_API_KEY"
    )
    if not api_key:
        raise RuntimeError("Set CONSTRUCTION_LLM_API_KEY or VLLM_API_KEY")
    base_url = os.environ.get(
        "CONSTRUCTION_LLM_BASE_URL", "http://10.87.5.247:8000/v1/"
    )
    model = os.environ.get("CONSTRUCTION_LLM_MODEL", DEFAULT_CONSTRUCTION_MODEL)
    client = QwenVLLMClient(
        config=LLMConfig(
            api_key=api_key,
            model=model,
            small_model=model,
            base_url=base_url,
            temperature=0.0,
            max_tokens=2_048,
        ),
        max_tokens=2_048,
        structured_output_mode="json_schema",
    )

    parsed_response: dict[str, Any] | None = None
    error_type: str | None = None
    try:
        parsed_response = await client.generate_response(
            probe.messages,
            response_model=probe.response_model,
            max_tokens=16_384,
            group_id=str(question_id),
            prompt_name="extract_nodes.extract_message",
        )
    except Exception as exc:
        error_type = type(exc).__name__

    observed = (
        _success_event(client)
        if parsed_response is not None
        else sanitize_failure_records(client.failure_events)
    )
    classification = classify_compatibility_result(
        observed,
        parsed=parsed_response is not None,
    )
    prompt_token_counts = sorted(
        {int(event.get("prompt_tokens") or 0) for event in observed}
    )
    historical_prompt_tokens = probe.evidence["historical_prompt_tokens"]
    prompt_token_count_matches = (
        historical_prompt_tokens is not None
        and prompt_token_counts == [historical_prompt_tokens]
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "mode": "one_exact_extraction_request_no_database_no_embedding",
        "ok": parsed_response is not None and prompt_token_count_matches,
        "classification": classification,
        "error_type": error_type,
        "probe_contract": probe.evidence,
        "observed_events": observed,
        "observed_prompt_token_counts": prompt_token_counts,
        "historical_prompt_token_count": historical_prompt_tokens,
        "prompt_token_count_matches_history": prompt_token_count_matches,
        "parsed_entity_count": (
            len(parsed_response.get("extracted_entities") or [])
            if parsed_response is not None
            else None
        ),
        "llm_call_count": int(client.call_count),
        "structured_request_count": int(client.structured_request_count),
        "structured_parse_failures": int(client.parse_failure_count),
        "structured_response_failures": int(
            client.structured_response_failure_count
        ),
        "high_level_attempt_count": int(client.structured_request_count),
        "outer_retry_count": max(0, int(client.structured_request_count) - 1),
        "database_called": False,
        "embedding_called": False,
        "response_bodies_persisted": False,
        "secrets_persisted": False,
    }
    encoded = json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False)
    if "raw_response" in encoded or "Authorization" in encoded or "api_key" in encoded:
        raise ValueError("compatibility probe result contains forbidden sensitive fields")
    return result


async def write_compatibility_probe(
    data_path: str | Path,
    question_id: str,
    output: str | Path,
    *,
    source_sequence: int = 1,
) -> dict[str, Any]:
    """Run and exclusively persist a compatibility probe result."""

    output = Path(output)
    if output.exists():
        raise FileExistsError(f"compatibility probe output already exists: {output}")
    result = await run_compatibility_probe(
        data_path,
        question_id,
        source_sequence=source_sequence,
    )
    encoded = json.dumps(
        result,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="ascii", newline="\n") as handle:
        handle.write(encoded)
    return result


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _evidence_source(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _matching_prompt_cache_record(
    prompt_cache_path: str | Path,
    expected_hashes: list[str],
) -> dict[str, Any]:
    matches = []
    for line in Path(prompt_cache_path).read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        record = json.loads(line)
        parts = record.get("prompt_parts")
        if not isinstance(parts, dict):
            continue
        hashes = [
            hashlib.sha256(str(parts.get(name) or "").encode("utf-8")).hexdigest()
            for name in ("system_prompt", "user_prompt")
        ]
        if hashes == expected_hashes:
            matches.append(record)
    if len(matches) != 1:
        raise ValueError(
            f"expected one retained prompt-cache match, found {len(matches)}"
        )
    return matches[0]


def analyze_runtime_drift(
    source0_probe_path: str | Path,
    source1_probe_path: str | Path,
    prompt_cache_path: str | Path,
    historical_failure_path: str | Path,
    metadata_probe_path: str | Path,
) -> dict[str, Any]:
    """Compare byte-identical controls across historical and current runtimes."""

    source0 = _read_json(source0_probe_path)
    source1 = _read_json(source1_probe_path)
    failures = _read_json(historical_failure_path)
    metadata = _read_json(metadata_probe_path)
    source0_contract = source0.get("probe_contract") or {}
    source1_contract = source1.get("probe_contract") or {}
    retained = _matching_prompt_cache_record(
        prompt_cache_path,
        list(
            source0_contract.get("pre_wrapper_message_content_sha256")
            or source0_contract.get("message_content_sha256")
            or []
        ),
    )
    retained_parts = retained["prompt_parts"]
    retained_schema = constrain_single_episode_indices(
        {
            "type": "json_schema",
            "json_schema": {
                "name": "ExtractedEntities",
                "schema": retained_parts["structured_output_schema"],
            },
        }
    )["json_schema"]["schema"]
    retained_schema_hash = hashlib.sha256(
        json.dumps(
            retained_schema,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()

    historical_source0_tokens = int(retained["token_usage"]["prompt_tokens"])
    current_source0_tokens = int(source0["observed_prompt_token_counts"][0])
    historical_source1_token_values = sorted(
        {
            int(record["token_usage"]["prompt_tokens"])
            for record in failures
            if record.get("token_usage", {}).get("prompt_tokens") is not None
        }
    )
    if historical_source1_token_values != [5_795]:
        raise ValueError(
            "historical source-1 prompt token evidence changed: "
            f"{historical_source1_token_values}"
        )
    current_source1_tokens = int(source1["observed_prompt_token_counts"][0])
    source0_delta = current_source0_tokens - historical_source0_tokens
    source1_delta = current_source1_tokens - historical_source1_token_values[0]
    source0_prompt_matches = list(
        source0_contract.get("pre_wrapper_message_content_sha256")
        or source0_contract["message_content_sha256"]
    ) == [
        hashlib.sha256(retained_parts["system_prompt"].encode("utf-8")).hexdigest(),
        hashlib.sha256(retained_parts["user_prompt"].encode("utf-8")).hexdigest(),
    ]
    schema_matches = (
        retained_schema_hash == source0_contract.get("json_schema_sha256")
        == source1_contract.get("json_schema_sha256")
    )
    decoding = retained_parts["decoding_config"]
    decoding_matches = all(
        (
            source0_contract.get("temperature") == decoding.get("temperature"),
            source0_contract.get("top_p") == decoding.get("top_p"),
            source0_contract.get("seed") == decoding.get("seed"),
            source0_contract.get("max_tokens") == decoding.get("max_tokens"),
            source1_contract.get("temperature") == decoding.get("temperature"),
            source1_contract.get("top_p") == decoding.get("top_p"),
            source1_contract.get("seed") == decoding.get("seed"),
            source1_contract.get("max_tokens") == decoding.get("max_tokens"),
        )
    )
    equal_nonzero_delta = source0_delta == source1_delta and source0_delta != 0
    public_generate_response_path = bool(
        source0_contract.get("public_generate_response_path")
        and source1_contract.get("public_generate_response_path")
    )
    drift_detected = bool(
        public_generate_response_path
        and source0_prompt_matches
        and schema_matches
        and decoding_matches
        and equal_nonzero_delta
    )

    return {
        "schema_version": "membind.v3.construction_runtime_drift.v1",
        "analysis_mode": "retained_vs_current_exact_prompt_controls",
        "evidence_sources": [
            _evidence_source(path)
            for path in (
                source0_probe_path,
                source1_probe_path,
                prompt_cache_path,
                historical_failure_path,
                metadata_probe_path,
            )
        ],
        "controls": {
            "source0_prompt_bytes_match_history": source0_prompt_matches,
            "constrained_schema_hash_matches": schema_matches,
            "decoding_parameters_match": decoding_matches,
            "source1_reconstruction_method_validated_by_source0": source0_prompt_matches,
            "public_generate_response_path": public_generate_response_path,
            "database_called": False,
            "embedding_called": False,
        },
        "token_counts": {
            "source0": {
                "historical": historical_source0_tokens,
                "current": current_source0_tokens,
            },
            "source1": {
                "historical": historical_source1_token_values[0],
                "current": current_source1_tokens,
            },
        },
        "token_deltas": {
            "source0": source0_delta,
            "source1": source1_delta,
            "equal_across_controls": source0_delta == source1_delta,
        },
        "historical_outcomes": {
            "source0": "parsed",
            "source0_body_sha256": hashlib.sha256(
                retained["raw_response"].encode("utf-8")
            ).hexdigest(),
            "source1": "length_truncated",
        },
        "current_outcomes": {
            "source0": "parsed" if source0.get("error_type") is None else "failed",
            "source0_body_sha256": source0["observed_events"][0]["body_sha256"],
            "source1": "parsed" if source1.get("error_type") is None else "failed",
            "source1_body_sha256": source1["observed_events"][0]["body_sha256"],
        },
        "current_service": {
            "vllm_version": metadata.get("version"),
            "models": metadata.get("models"),
            "server_config_available": metadata.get("server_config_available"),
            "proxy_bypass_for_target": metadata.get("proxy_bypass_for_target"),
        },
        "claims": {
            "construction_runtime_identity_drift_detected": drift_detected,
            "checkpoint_identity_proven_equal": False,
            "tokenizer_or_chat_template_identity_proven_equal": False,
            "structured_output_backend_proven_equal": False,
            "current_parsing_success_is_a_v3_pass": False,
        },
        "gate": {
            "status": (
                "blocked_construction_runtime_identity_drift"
                if drift_detected
                else (
                    "invalid_probe_bypassed_public_generate_response_wrapper"
                    if not public_generate_response_path
                    else "inconclusive_runtime_identity_comparison"
                )
            ),
            "new_v3_attempt_allowed": False,
            "required_evidence": [
                "construction process argv and startup log",
                "construction model directory immutable fingerprint",
                "tokenizer.json and chat_template/config fingerprints",
                "structured-output backend configuration",
            ],
        },
        "secrets_persisted": False,
        "response_bodies_persisted": False,
    }


def write_runtime_drift_diagnostic(
    source0_probe_path: str | Path,
    source1_probe_path: str | Path,
    prompt_cache_path: str | Path,
    historical_failure_path: str | Path,
    metadata_probe_path: str | Path,
    output: str | Path,
) -> dict[str, Any]:
    """Persist a body-free construction runtime identity comparison."""

    result = analyze_runtime_drift(
        source0_probe_path,
        source1_probe_path,
        prompt_cache_path,
        historical_failure_path,
        metadata_probe_path,
    )
    encoded = json.dumps(
        result,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if "raw_response" in encoded or "api_key" in encoded.casefold():
        raise ValueError("runtime drift diagnostic contains forbidden fields")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="ascii", newline="\n") as handle:
        handle.write(encoded)
    return result
