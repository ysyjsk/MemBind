"""TDD contract for sanitized, attempt-scoped M* failure evidence."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s5_mstar_failure_envelope import (
    S5MStarFailureEnvelopeError,
    build_s5_mstar_failure_envelope,
    verify_s5_mstar_failure_envelope,
    write_s5_mstar_failure_envelope,
)


def _event(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "request_ordinal": 3,
        "source_sequence": 8,
        "response_format_type": "json_schema",
        "json_schema_name": "ExtractedEdges",
        "json_schema_sha256": "a" * 64,
        "requested_max_tokens": 16_384,
        "prompt_tokens": 19_265,
        "completion_tokens": 16_384,
        "total_tokens": 35_649,
        "finish_reason": "length",
        "transport_outcome": "response_received",
        "http_status": None,
        "error_class": None,
    }
    value.update(overrides)
    return value


def _build(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "run_id": "s5-mstar-20260816-telemetry-test",
        "production_core_identity_sha256": "b" * 64,
        "failed_source_sequence": 8,
        "pipeline_failure_code": "LATEST_STATE_BIND_FAILED",
        "pipeline_error_class": (
            "paper_eval.s5_graphiti_mstar_semantics."
            "S5GraphitiMStarSemanticError"
        ),
        "semantic_error_code": "extract_edges_failed",
        "upstream_error_class": "json.decoder.JSONDecodeError",
        "transport_events": [_event()],
    }
    values.update(overrides)
    return build_s5_mstar_failure_envelope(**values)


@pytest.mark.parametrize(
    ("event", "upstream_error_class", "classification"),
    [
        (_event(), "json.decoder.JSONDecodeError", "CAP_EXHAUSTED"),
        (
            _event(finish_reason="stop", completion_tokens=1_203, total_tokens=20_468),
            "json.decoder.JSONDecodeError",
            "STRUCTURED_INVALID",
        ),
        (
            _event(completion_tokens=16_383, total_tokens=35_648),
            "json.decoder.JSONDecodeError",
            "UNCLASSIFIED",
        ),
        (
            _event(source_sequence=9),
            "json.decoder.JSONDecodeError",
            "UNCLASSIFIED",
        ),
        (_event(), None, "UNCLASSIFIED"),
    ],
)
def test_failure_envelope_classification_is_strict_and_sealed(
    event: dict[str, object],
    upstream_error_class: str | None,
    classification: str,
) -> None:
    artifact = _build(
        upstream_error_class=upstream_error_class,
        transport_events=[event],
    )

    assert artifact["classification"] == classification
    assert artifact["semantic_stage"] == "extract_edges"
    assert artifact["transport_events"] == [event]
    assert artifact["failure_envelope_sha256"] == payload_sha256(
        {
            key: value
            for key, value in artifact.items()
            if key != "failure_envelope_sha256"
        }
    )
    assert verify_s5_mstar_failure_envelope(artifact) == artifact


@pytest.mark.parametrize(
    "semantic_error_code",
    ["resolve_edges_failed", "extract_attributes_failed"],
)
def test_concurrent_stage_events_are_not_guessed_from_completion_order(
    semantic_error_code: str,
) -> None:
    artifact = _build(
        semantic_error_code=semantic_error_code,
        transport_events=[
            _event(request_ordinal=10, finish_reason="stop"),
            _event(request_ordinal=11, finish_reason="length"),
        ],
    )

    assert artifact["classification"] == "UNCLASSIFIED"


def test_latest_correlated_event_determines_safe_stage_classification() -> None:
    artifact = _build(
        transport_events=[
            _event(request_ordinal=10),
            _event(
                request_ordinal=11,
                finish_reason="stop",
                completion_tokens=1_203,
                total_tokens=20_468,
            ),
        ],
    )

    assert artifact["classification"] == "STRUCTURED_INVALID"


@pytest.mark.parametrize(
    ("semantic_error_code", "event"),
    [
        ("process_episode_data_failed", _event()),
        (
            "extract_edges_failed",
            _event(total_tokens=None),
        ),
        (
            "extract_edges_failed",
            _event(total_tokens=35_650),
        ),
        (
            "extract_edges_failed",
            _event(
                transport_outcome="transport_error",
                error_class="builtins.ConnectionError",
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
                finish_reason=None,
            ),
        ),
    ],
)
def test_ambiguous_or_incomplete_telemetry_is_unclassified(
    semantic_error_code: str,
    event: dict[str, object],
) -> None:
    artifact = _build(
        semantic_error_code=semantic_error_code,
        transport_events=[event],
    )

    assert artifact["classification"] == "UNCLASSIFIED"


@pytest.mark.parametrize(
    "private_field",
    ["messages", "raw_response", "prompt", "authorization", "api_key"],
)
def test_failure_envelope_rejects_private_or_unknown_transport_fields(
    private_field: str,
) -> None:
    event = _event(**{private_field: "PRIVATE-SENTINEL"})
    with pytest.raises(S5MStarFailureEnvelopeError):
        _build(transport_events=[event])


def test_failure_envelope_write_is_atomic_hash_bound_and_tamper_evident(
    tmp_path: Path,
) -> None:
    artifact = _build()
    path = tmp_path / "attempt" / "failure_envelope.json"
    binding = write_s5_mstar_failure_envelope(path, artifact)

    assert binding["failure_classification"] == "CAP_EXHAUSTED"
    assert binding["failure_envelope_payload_sha256"] == artifact[
        "failure_envelope_sha256"
    ]
    assert len(binding["failure_envelope_file_sha256"]) == 64
    encoded = path.read_text(encoding="utf-8")
    assert "PRIVATE-SENTINEL" not in encoded
    assert json.loads(encoded) == artifact

    tampered = copy.deepcopy(artifact)
    tampered["classification"] = "STRUCTURED_INVALID"
    with pytest.raises(S5MStarFailureEnvelopeError):
        verify_s5_mstar_failure_envelope(tampered)
