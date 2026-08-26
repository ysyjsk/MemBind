from __future__ import annotations

import json

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.development_model_probe import (
    DevelopmentModelProbeError,
    sanitize_probe_execution,
    select_development_model,
)


CANDIDATES = [
    "qwen3.5-122b-a10b",
    "qwen3.5-397b-a17b",
    "qwen3.5-plus-2026-04-20",
]


def _result(model: str, *, edge_passes: int = 5) -> dict[str, object]:
    return {
        "model": model,
        "available": True,
        "node": {"status": "PASS", "classification": "STRUCTURED_EXTRACTION_PARSED"},
        "edges": [
            {"status": "PASS", "classification": "STRUCTURED_EXTRACTION_PARSED"}
            for _ in range(edge_passes)
        ],
    }


def test_probe_sanitizer_drops_request_response_hashes_and_provider_payloads() -> None:
    raw = {
        "status": "PASS",
        "classification": "STRUCTURED_EXTRACTION_PARSED",
        "probe_kind": "extract_edges.edge",
        "http_attempt_count": 1,
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        "parsed_item_count": 2,
        "duration_ns": 123,
        "response_content_sha256": "a" * 64,
        "response_content_bytes": 999,
        "probe_contract": {
            "message_content_sha256": ["b" * 64],
            "private_prompt": "must not persist",
        },
        "provider_error": {"message": "private provider output"},
    }

    safe = sanitize_probe_execution(raw)

    assert safe == {
        "status": "PASS",
        "classification": "STRUCTURED_EXTRACTION_PARSED",
        "probe_kind": "extract_edges.edge",
        "http_attempt_count": 1,
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        "parsed_item_count": 2,
        "duration_ns": 123,
        "raw_request_persisted": False,
        "raw_response_persisted": False,
        "response_hash_persisted": False,
    }
    encoded = json.dumps(safe, sort_keys=True)
    assert "private" not in encoded
    assert "content_sha256" not in encoded


def test_selection_uses_frozen_order_and_requires_all_edge_repetitions() -> None:
    results = [
        _result(CANDIDATES[0], edge_passes=4),
        _result(CANDIDATES[1]),
        _result(CANDIDATES[2]),
    ]

    selected = select_development_model(
        candidates=CANDIDATES,
        results=results,
        required_edge_repetitions=5,
    )

    assert selected["status"] == "SELECTED"
    assert selected["selected_model"] == CANDIDATES[1]
    assert selected["eligible_models"] == CANDIDATES[1:]
    assert selected["selection_rule"] == "FIRST_FULL_PASS_IN_FROZEN_ORDER"


def test_selection_fails_closed_when_no_candidate_fully_passes() -> None:
    results = [
        _result(CANDIDATES[0], edge_passes=4),
        {**_result(CANDIDATES[1]), "available": False},
        {**_result(CANDIDATES[2]), "node": {"status": "FAIL"}},
    ]

    selected = select_development_model(
        candidates=CANDIDATES,
        results=results,
        required_edge_repetitions=5,
    )

    assert selected["status"] == "NO_ELIGIBLE_MODEL"
    assert selected["selected_model"] is None
    assert selected["eligible_models"] == []


def test_selection_rejects_candidate_or_result_identity_drift() -> None:
    with pytest.raises(DevelopmentModelProbeError, match="identity"):
        select_development_model(
            candidates=CANDIDATES,
            results=[_result(CANDIDATES[1]), _result(CANDIDATES[0]), _result(CANDIDATES[2])],
            required_edge_repetitions=5,
        )
