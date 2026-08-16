"""Sanitized, attempt-scoped evidence for one failed S5 M* execution.

The module is deliberately independent of Graphiti and provider SDK objects.
It accepts only a fixed public projection of transport metadata, classifies a
failure conservatively, and seals the sidecar before result finalization.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path

from .artifacts import atomic_write_json, payload_sha256, sha256_file


SCHEMA = "membind.paper-eval-v3.s5-mstar-failure-envelope.v2"
MAX_TRANSPORT_EVENTS = 16
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^s5-mstar-[a-z0-9][a-z0-9-]{2,127}$")
_STABLE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_QUALIFIED_CLASS = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$"
)
_EVENT_FIELDS = {
    "request_ordinal",
    "source_sequence",
    "response_format_type",
    "json_schema_name",
    "json_schema_sha256",
    "requested_max_tokens",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "finish_reason",
    "transport_outcome",
    "http_status",
    "error_class",
}
_ARTIFACT_FIELDS = {
    "schema_version",
    "run_id",
    "method",
    "production_core_identity_sha256",
    "failed_source_sequence",
    "pipeline_failure_code",
    "pipeline_error_class",
    "semantic_error_code",
    "semantic_stage",
    "upstream_error_class",
    "classification",
    "telemetry_status",
    "transport_event_count_total",
    "transport_events_truncated",
    "transport_events",
    "authority",
    "failure_envelope_sha256",
}
_AUTHORITY = {
    "resume_authorized": False,
    "namespace_cleanup_authorized": False,
    "scientific_pass_authorized": False,
    "next_method_authorized": False,
    "current_stage_pointer_update_authorized": False,
}
_CLASSIFICATIONS = {"CAP_EXHAUSTED", "STRUCTURED_INVALID", "UNCLASSIFIED"}
_TELEMETRY_STATUSES = {
    "COMPLETE",
    "NO_EVENTS",
    "SNAPSHOT_ERROR",
    "REJECTED_PRIVATE_FIELDS",
    "SANITIZED_FALLBACK",
}
_STRUCTURED_ERROR_CLASSES = {
    "json.decoder.JSONDecodeError",
    "pydantic.ValidationError",
    "pydantic_core._pydantic_core.ValidationError",
}
_UNIQUELY_ATTRIBUTABLE_STAGES = {
    "extract_nodes_failed",
    "resolve_nodes_failed",
    "extract_edges_failed",
}
_SEMANTIC_STAGE_BY_ERROR = {
    "extract_nodes_failed": "extract_nodes",
    "resolve_nodes_failed": "resolve_nodes",
    "extract_edges_failed": "extract_edges",
    "resolve_edges_failed": "resolve_edges",
    "extract_attributes_failed": "extract_attributes",
}


class S5MStarFailureEnvelopeError(ValueError):
    """The public failure projection or its seal is invalid."""


def _fail(code: str) -> S5MStarFailureEnvelopeError:
    return S5MStarFailureEnvelopeError(code)


def _optional_qualified_class(value: object, code: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _QUALIFIED_CLASS.fullmatch(value) is None:
        raise _fail(code)
    return value


def _optional_nonnegative_int(value: object, code: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _validate_event(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _EVENT_FIELDS:
        raise _fail("transport_event_shape_invalid")
    event = deepcopy(dict(value))
    ordinal = _optional_nonnegative_int(
        event.get("request_ordinal"), "request_ordinal_invalid"
    )
    if ordinal is None:
        raise _fail("request_ordinal_invalid")
    _optional_nonnegative_int(
        event.get("source_sequence"), "source_sequence_invalid"
    )
    for field in ("response_format_type", "json_schema_name", "finish_reason"):
        selected = event.get(field)
        if selected is not None and (not isinstance(selected, str) or not selected):
            raise _fail(f"{field}_invalid")
    schema_sha = event.get("json_schema_sha256")
    if schema_sha is not None and (
        not isinstance(schema_sha, str) or _SHA256.fullmatch(schema_sha) is None
    ):
        raise _fail("json_schema_sha256_invalid")
    budget = _optional_nonnegative_int(
        event.get("requested_max_tokens"), "requested_max_tokens_invalid"
    )
    if budget is None or budget < 1:
        raise _fail("requested_max_tokens_invalid")
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        _optional_nonnegative_int(event.get(field), f"{field}_invalid")
    if event.get("transport_outcome") not in {
        "response_received",
        "transport_error",
    }:
        raise _fail("transport_outcome_invalid")
    status = event.get("http_status")
    if status is not None and (
        isinstance(status, bool)
        or not isinstance(status, int)
        or status < 100
        or status > 599
    ):
        raise _fail("http_status_invalid")
    _optional_qualified_class(event.get("error_class"), "error_class_invalid")
    if (
        event["transport_outcome"] == "response_received"
        and event["error_class"] is not None
    ):
        raise _fail("transport_response_error_class_invalid")
    if (
        event["transport_outcome"] == "transport_error"
        and event["error_class"] is None
    ):
        raise _fail("transport_error_class_missing")
    return event


def _token_accounting_valid(event: Mapping[str, object]) -> bool:
    prompt = event.get("prompt_tokens")
    completion = event.get("completion_tokens")
    total = event.get("total_tokens")
    return (
        isinstance(prompt, int)
        and not isinstance(prompt, bool)
        and isinstance(completion, int)
        and not isinstance(completion, bool)
        and isinstance(total, int)
        and not isinstance(total, bool)
        and prompt + completion == total
    )


def _classification(
    *,
    failed_source_sequence: int | None,
    semantic_error_code: str | None,
    upstream_error_class: str | None,
    events: Sequence[Mapping[str, object]],
) -> str:
    if (
        failed_source_sequence is None
        # Pinned Graphiti runs one logical LLM call in these stages. Edge
        # resolution and attribute extraction fan out concurrently, so their
        # completion-order events cannot identify the request that failed.
        or semantic_error_code not in _UNIQUELY_ATTRIBUTABLE_STAGES
        or upstream_error_class not in _STRUCTURED_ERROR_CLASSES
    ):
        return "UNCLASSIFIED"
    correlated = [
        event
        for event in events
        if event.get("source_sequence") == failed_source_sequence
    ]
    if not correlated:
        return "UNCLASSIFIED"
    event = max(correlated, key=lambda item: int(item["request_ordinal"]))
    if (
        event.get("transport_outcome") != "response_received"
        or not _token_accounting_valid(event)
    ):
        return "UNCLASSIFIED"
    if (
        event.get("finish_reason") == "length"
        and event.get("completion_tokens") == event.get("requested_max_tokens")
    ):
        return "CAP_EXHAUSTED"
    if event.get("finish_reason") == "stop":
        return "STRUCTURED_INVALID"
    return "UNCLASSIFIED"


def build_s5_mstar_failure_envelope(
    *,
    run_id: str,
    production_core_identity_sha256: str,
    failed_source_sequence: int | None,
    pipeline_failure_code: str,
    pipeline_error_class: str,
    semantic_error_code: str | None,
    upstream_error_class: str | None,
    transport_events: Sequence[Mapping[str, object]],
    telemetry_status: str | None = None,
) -> dict[str, object]:
    """Validate, bound, classify, and seal one content-free failure snapshot."""

    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise _fail("run_id_invalid")
    if (
        not isinstance(production_core_identity_sha256, str)
        or _SHA256.fullmatch(production_core_identity_sha256) is None
    ):
        raise _fail("production_core_identity_invalid")
    failed_source_sequence = _optional_nonnegative_int(
        failed_source_sequence, "failed_source_sequence_invalid"
    )
    if not isinstance(pipeline_failure_code, str) or _STABLE_CODE.fullmatch(
        pipeline_failure_code.casefold()
    ) is None:
        raise _fail("pipeline_failure_code_invalid")
    pipeline_error_class = _optional_qualified_class(
        pipeline_error_class, "pipeline_error_class_invalid"
    )
    if pipeline_error_class is None:
        raise _fail("pipeline_error_class_invalid")
    if semantic_error_code is not None and (
        not isinstance(semantic_error_code, str)
        or _STABLE_CODE.fullmatch(semantic_error_code) is None
    ):
        raise _fail("semantic_error_code_invalid")
    upstream_error_class = _optional_qualified_class(
        upstream_error_class, "upstream_error_class_invalid"
    )
    if semantic_error_code is None and upstream_error_class is not None:
        raise _fail("upstream_without_semantic_code")
    if isinstance(transport_events, (str, bytes)) or not isinstance(
        transport_events, Sequence
    ):
        raise _fail("transport_events_invalid")
    validated = [_validate_event(event) for event in transport_events]
    ordinals = [int(event["request_ordinal"]) for event in validated]
    if len(ordinals) != len(set(ordinals)):
        raise _fail("request_ordinal_duplicate")
    validated.sort(key=lambda event: int(event["request_ordinal"]))
    retained = validated[-MAX_TRANSPORT_EVENTS:]
    selected_status = telemetry_status or ("COMPLETE" if validated else "NO_EVENTS")
    if selected_status not in _TELEMETRY_STATUSES:
        raise _fail("telemetry_status_invalid")
    classification = _classification(
        failed_source_sequence=failed_source_sequence,
        semantic_error_code=semantic_error_code,
        upstream_error_class=upstream_error_class,
        events=retained,
    )
    if selected_status != "COMPLETE":
        classification = "UNCLASSIFIED"
    artifact: dict[str, object] = {
        "schema_version": SCHEMA,
        "run_id": run_id,
        "method": "M*",
        "production_core_identity_sha256": production_core_identity_sha256,
        "failed_source_sequence": failed_source_sequence,
        "pipeline_failure_code": pipeline_failure_code,
        "pipeline_error_class": pipeline_error_class,
        "semantic_error_code": semantic_error_code,
        # This is the pinned Graphiti semantic stage, not an observed prompt.
        "semantic_stage": _SEMANTIC_STAGE_BY_ERROR.get(semantic_error_code),
        "upstream_error_class": upstream_error_class,
        "classification": classification,
        "telemetry_status": selected_status,
        "transport_event_count_total": len(validated),
        "transport_events_truncated": len(validated) > len(retained),
        "transport_events": retained,
        "authority": deepcopy(_AUTHORITY),
    }
    artifact["failure_envelope_sha256"] = payload_sha256(artifact)
    return verify_s5_mstar_failure_envelope(artifact)


def verify_s5_mstar_failure_envelope(
    value: Mapping[str, object],
) -> dict[str, object]:
    """Recompute shape, classification, and seal without trusting the writer."""

    if not isinstance(value, Mapping):
        raise _fail("artifact_not_mapping")
    artifact = deepcopy(dict(value))
    if set(artifact) != _ARTIFACT_FIELDS:
        raise _fail("artifact_shape_invalid")
    seal = artifact.pop("failure_envelope_sha256", None)
    if seal != payload_sha256(artifact):
        raise _fail("artifact_hash_invalid")
    if (
        artifact.get("schema_version") != SCHEMA
        or artifact.get("method") != "M*"
        or not isinstance(artifact.get("run_id"), str)
        or _RUN_ID.fullmatch(str(artifact["run_id"])) is None
        or not isinstance(artifact.get("production_core_identity_sha256"), str)
        or _SHA256.fullmatch(str(artifact["production_core_identity_sha256"]))
        is None
    ):
        raise _fail("artifact_identity_invalid")
    _optional_nonnegative_int(
        artifact.get("failed_source_sequence"),
        "failed_source_sequence_invalid",
    )
    pipeline_code = artifact.get("pipeline_failure_code")
    if not isinstance(pipeline_code, str) or _STABLE_CODE.fullmatch(
        pipeline_code.casefold()
    ) is None:
        raise _fail("pipeline_failure_code_invalid")
    pipeline_class = _optional_qualified_class(
        artifact.get("pipeline_error_class"), "pipeline_error_class_invalid"
    )
    if pipeline_class is None:
        raise _fail("pipeline_error_class_invalid")
    semantic_code = artifact.get("semantic_error_code")
    if semantic_code is not None and (
        not isinstance(semantic_code, str)
        or _STABLE_CODE.fullmatch(semantic_code) is None
    ):
        raise _fail("semantic_error_code_invalid")
    if artifact.get("semantic_stage") != _SEMANTIC_STAGE_BY_ERROR.get(
        semantic_code
    ):
        raise _fail("semantic_stage_binding_invalid")
    upstream_class = _optional_qualified_class(
        artifact.get("upstream_error_class"), "upstream_error_class_invalid"
    )
    if semantic_code is None and upstream_class is not None:
        raise _fail("upstream_without_semantic_code")
    events = artifact.get("transport_events")
    if not isinstance(events, list):
        raise _fail("transport_events_invalid")
    validated = [_validate_event(event) for event in events]
    ordinals = [int(event["request_ordinal"]) for event in validated]
    total = artifact.get("transport_event_count_total")
    truncated = artifact.get("transport_events_truncated")
    telemetry_status = artifact.get("telemetry_status")
    if (
        len(validated) > MAX_TRANSPORT_EVENTS
        or ordinals != sorted(ordinals)
        or len(ordinals) != len(set(ordinals))
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total < len(validated)
        or not isinstance(truncated, bool)
        or truncated is not (total > len(validated))
        or telemetry_status not in _TELEMETRY_STATUSES
        or (
            telemetry_status == "COMPLETE"
            and (total < 1 or not validated)
        )
        or (
            telemetry_status != "COMPLETE"
            and (total != 0 or validated or truncated)
        )
    ):
        raise _fail("telemetry_binding_invalid")
    expected_classification = (
        _classification(
            failed_source_sequence=artifact.get("failed_source_sequence"),
            semantic_error_code=semantic_code,
            upstream_error_class=artifact.get("upstream_error_class"),
            events=validated,
        )
        if telemetry_status == "COMPLETE"
        else "UNCLASSIFIED"
    )
    if (
        artifact.get("classification") not in _CLASSIFICATIONS
        or artifact.get("authority") != _AUTHORITY
        or artifact.get("classification") != expected_classification
    ):
        raise _fail("artifact_binding_invalid")
    artifact["failure_envelope_sha256"] = seal
    return artifact


def write_s5_mstar_failure_envelope(
    path: Path,
    value: Mapping[str, object],
) -> dict[str, str]:
    """Atomically persist a fresh sidecar and return its two immutable hashes."""

    selected_path = Path(path)
    if selected_path.exists():
        raise _fail("failure_envelope_exists")
    artifact = verify_s5_mstar_failure_envelope(value)
    atomic_write_json(selected_path, artifact)
    return {
        "failure_envelope_payload_sha256": str(
            artifact["failure_envelope_sha256"]
        ),
        "failure_envelope_file_sha256": sha256_file(selected_path),
        "failure_classification": str(artifact["classification"]),
    }


__all__ = [
    "S5MStarFailureEnvelopeError",
    "build_s5_mstar_failure_envelope",
    "verify_s5_mstar_failure_envelope",
    "write_s5_mstar_failure_envelope",
]
