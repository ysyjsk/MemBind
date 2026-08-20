from __future__ import annotations

import hashlib
import json
from pathlib import Path

from paper_eval.artifacts import canonical_bytes
from paper_eval.membind_v4.mseg.reducer import audit_llm_trace_observability


def _write_record(path: Path, row: dict[str, object]) -> None:
    record = {
        "schema_version": "test-wrapper.v1",
        "row": row,
    }
    wrapper = {
        "record": record,
        "record_sha256": hashlib.sha256(canonical_bytes(record)).hexdigest(),
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(wrapper, sort_keys=True) + "\n")


def test_sealed_style_trace_fails_closed_without_operator_identity(tmp_path: Path) -> None:
    trace = tmp_path / "llm.jsonl"
    _write_record(
        trace,
        {
            "event_type": "llm_request_submitted",
            "request_id": "req-0",
            "request_kind": "FRONTIER",
            "stream_id": "07741c45",
            "source_sequence": 0,
            "timestamp_ns": 10,
            "token_count": 100,
        },
    )
    _write_record(
        trace,
        {
            "event_type": "llm_request_start",
            "request_id": "req-0",
            "timestamp_ns": 20,
        },
    )
    _write_record(
        trace,
        {
            "event_type": "llm_request_terminal",
            "request_id": "req-0",
            "timestamp_ns": 30,
            "status": "ok",
        },
    )

    audit = audit_llm_trace_observability(trace, history_id="07741c45")

    assert audit["request_count"] == 1
    assert audit["complete_client_lifecycle_count"] == 1
    assert audit["fine_grained_identity_recovered"] is False
    assert audit["mseg_recovered"] is False
    assert audit["field_coverage"]["operator_role"]["observed_count"] == 0
    assert audit["field_coverage"]["operator_role"]["status"] == "NOT_OBSERVABLE"
    assert "operator_identity_missing" in audit["blocking_reasons"]
    assert "deterministic_operator_trace_missing" in audit["blocking_reasons"]


def test_prompt_length_and_request_order_are_not_role_attribution(tmp_path: Path) -> None:
    trace = tmp_path / "llm.jsonl"
    for index, token_count in enumerate((10, 999)):
        request_id = f"req-{index}"
        _write_record(
            trace,
            {
                "event_type": "llm_request_submitted",
                "request_id": request_id,
                "request_kind": "FRONTIER",
                "stream_id": "07741c45",
                "source_sequence": index,
                "timestamp_ns": index * 10,
                "token_count": token_count,
            },
        )
        _write_record(
            trace,
            {
                "event_type": "llm_request_start",
                "request_id": request_id,
                "timestamp_ns": index * 10 + 1,
            },
        )
        _write_record(
            trace,
            {
                "event_type": "llm_request_terminal",
                "request_id": request_id,
                "timestamp_ns": index * 10 + 2,
                "status": "ok",
            },
        )

    audit = audit_llm_trace_observability(trace, history_id="07741c45")

    assert audit["role_attribution_method"] == "NONE"
    assert audit["role_count"] == "NOT_OBSERVABLE"
    assert audit["prohibited_inferences"] == [
        "prompt_or_token_length",
        "request_order",
        "prompt_or_prefix_similarity",
    ]

