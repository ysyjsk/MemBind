"""Read-only diagnosis for retained V3 structured-output failure records.

This module never calls a model or database and never persists response bodies.
It proves only properties supported by immutable failure bytes and the installed
Graphiti response schema; causal attribution remains deliberately conservative.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = "membind.v3.structured_failure_diagnostic.v1"
DEFAULT_BUDGETS = (2_048, 8_192)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _field_path(path: str, field: str) -> str:
    if field.isidentifier():
        return f"{path}.{field}"
    return f"{path}[{json.dumps(field, ensure_ascii=True)}]"


def find_unbounded_arrays(value: Any, *, root: str = "$") -> list[str]:
    """Return deterministic JSON paths for arrays lacking a finite maxItems."""

    found: list[str] = []

    def visit(current: Any, path: str) -> None:
        if isinstance(current, dict):
            if current.get("type") == "array" and not isinstance(
                current.get("maxItems"), int
            ):
                found.append(path)
            for key in sorted(current):
                visit(current[key], _field_path(path, str(key)))
        elif isinstance(current, list):
            for index, item in enumerate(current):
                visit(item, f"{path}[{index}]")

    visit(value, root)
    return found


def _validated_record(record: Any, index: int) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise TypeError(f"failure record {index} must be an object")
    body = record.get("raw_response")
    if not isinstance(body, str):
        raise TypeError(f"failure record {index} has no response body")
    encoded = body.encode("utf-8")
    expected_length = record.get("raw_response_length")
    if expected_length != len(body):
        raise ValueError(f"raw response length mismatch at record {index}")
    expected_hash = record.get("raw_response_sha256")
    if expected_hash != _sha256_bytes(encoded):
        raise ValueError(f"raw response SHA256 mismatch at record {index}")
    budget = record.get("max_tokens")
    if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
        raise ValueError(f"invalid max_tokens at record {index}")
    usage = record.get("token_usage")
    if not isinstance(usage, dict):
        raise TypeError(f"failure record {index} has no token usage")
    return record


def _attempt_signature(records: Sequence[dict[str, Any]]) -> str:
    safe = [
        {
            "max_tokens": record["max_tokens"],
            "finish_reason": record.get("finish_reason"),
            "body_length": record["raw_response_length"],
            "body_sha256": record["raw_response_sha256"],
            "token_usage": record["token_usage"],
        }
        for record in records
    ]
    return _sha256_bytes(_canonical_json(safe).encode("ascii"))


def analyze_failure_records(
    records: Iterable[dict[str, Any]],
    *,
    expected_budgets: Sequence[int] = DEFAULT_BUDGETS,
) -> dict[str, Any]:
    """Summarize bounded retry trajectories without retaining response text."""

    budgets = tuple(int(value) for value in expected_budgets)
    if not budgets or any(value <= 0 for value in budgets):
        raise ValueError("expected budgets must contain positive integers")
    validated = [_validated_record(record, index) for index, record in enumerate(records)]
    if not validated:
        raise ValueError("failure artifact contains no records")
    if len(validated) % len(budgets):
        raise ValueError("failure record count does not align with bounded retry budgets")

    attempts = [
        validated[index : index + len(budgets)]
        for index in range(0, len(validated), len(budgets))
    ]
    budget_sequences = [
        [int(record["max_tokens"]) for record in attempt] for attempt in attempts
    ]
    response_hashes: dict[str, set[str]] = defaultdict(set)
    for record in validated:
        response_hashes[str(record["max_tokens"])].add(record["raw_response_sha256"])

    prefix_count = 0
    if len(budgets) >= 2:
        for attempt in attempts:
            if all(
                later["raw_response"].startswith(earlier["raw_response"])
                for earlier, later in zip(attempt, attempt[1:])
            ):
                prefix_count += 1

    signatures = [_attempt_signature(attempt) for attempt in attempts]
    identical_attempt_count = sum(signature == signatures[0] for signature in signatures)
    all_length = all(record.get("finish_reason") == "length" for record in validated)
    all_saturated = all(
        record["token_usage"].get("completion_tokens") == record["max_tokens"]
        for record in validated
    )
    expected_sequence_observed = all(sequence == list(budgets) for sequence in budget_sequences)
    deterministic_repetition = bool(
        len(attempts) > 1
        and expected_sequence_observed
        and identical_attempt_count == len(attempts)
        and prefix_count == len(attempts)
        and all_length
        and all_saturated
    )

    return {
        "record_count": len(validated),
        "request_attempt_count": len(attempts),
        "expected_budgets": list(budgets),
        "budget_sequences": budget_sequences,
        "expected_budget_sequence_observed": expected_sequence_observed,
        "all_finish_reason_length": all_length,
        "all_completion_budgets_saturated": all_saturated,
        "primary_prefix_of_retry_count": prefix_count,
        "identical_attempt_pair_count": identical_attempt_count,
        "unique_response_count_by_budget": {
            budget: len(hashes) for budget, hashes in sorted(response_hashes.items())
        },
        "prompt_token_counts": sorted(
            {
                int(record["token_usage"]["prompt_tokens"])
                for record in validated
                if record["token_usage"].get("prompt_tokens") is not None
            }
        ),
        "attempt_signature_sha256": signatures,
        "response_evidence": [
            {
                "ordinal": index,
                "max_tokens": int(record["max_tokens"]),
                "finish_reason": record.get("finish_reason"),
                "completion_tokens": int(
                    record["token_usage"].get("completion_tokens") or 0
                ),
                "body_length": int(record["raw_response_length"]),
                "body_sha256": str(record["raw_response_sha256"]),
            }
            for index, record in enumerate(validated)
        ],
        "deterministic_repetition_across_attempts": deterministic_repetition,
    }


def _installed_extraction_schema() -> dict[str, Any]:
    from graphiti_core.prompts.extract_nodes import ExtractedEntities

    from graphiti_native import constrain_single_episode_indices

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "ExtractedEntities",
            "schema": ExtractedEntities.model_json_schema(),
        },
    }
    return constrain_single_episode_indices(response_format)["json_schema"]["schema"]


def analyze_failure_artifact(source: str | Path) -> dict[str, Any]:
    """Analyze an immutable failure artifact and the installed extraction schema."""

    source = Path(source)
    records = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise TypeError("failure artifact root must be an array")
    retry_analysis = analyze_failure_records(records)
    schema = _installed_extraction_schema()
    unbounded_arrays = find_unbounded_arrays(schema, root="$[schema]")
    extracted_entities_path = "$[schema].properties.extracted_entities"
    schema_permits_unbounded_entities = extracted_entities_path in unbounded_arrays
    deterministic_truncation = bool(
        retry_analysis["deterministic_repetition_across_attempts"]
        and schema_permits_unbounded_entities
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_mode": "offline_retained_failure_artifact",
        "source": {
            "path": str(source),
            "sha256": _sha256_file(source),
            "size_bytes": source.stat().st_size,
        },
        "retry_analysis": retry_analysis,
        "schema_analysis": {
            "response_model": "graphiti_core.prompts.extract_nodes.ExtractedEntities",
            "constrained_schema_sha256": _sha256_bytes(
                _canonical_json(schema).encode("ascii")
            ),
            "unbounded_array_paths": unbounded_arrays,
            "extracted_entities_has_finite_max_items": not schema_permits_unbounded_entities,
            "episode_indices_constraint": {
                "min_items": 1,
                "max_items": 1,
                "item_const": 0,
            },
        },
        "classification": (
            "deterministic_length_truncation_with_schema_permitted_unbounded_array"
            if deterministic_truncation
            else "insufficient_evidence_for_deterministic_unbounded_array_truncation"
        ),
        "claims": {
            "recorded_attempts_are_bitwise_repeated": bool(
                retry_analysis["deterministic_repetition_across_attempts"]
            ),
            "frozen_retry_budget_was_sufficient": False,
            "schema_permits_unbounded_entity_count": schema_permits_unbounded_entities,
            "schema_caused_model_repetition_proven": False,
            "guided_decoding_configuration_root_cause_proven": False,
            "qwen_model_root_cause_proven": False,
            "protocol_change_authorized": False,
        },
        "next_evidence_required": [
            "vllm_process_argv_or_startup_log_with_guided_decoding_configuration",
            "frozen_request_compatibility_probe_covering_the_actual_extraction_schema",
        ],
    }


def write_failure_diagnostic(source: str | Path, output: str | Path) -> dict[str, Any]:
    """Persist an ASCII-only diagnostic atomically with exclusive creation."""

    payload = analyze_failure_artifact(source)
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if "raw_response" in encoded:
        raise ValueError("diagnostic must not persist response bodies")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="ascii", newline="\n") as handle:
        handle.write(encoded)
    return payload
