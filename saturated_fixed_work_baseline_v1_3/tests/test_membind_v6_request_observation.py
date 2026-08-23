from __future__ import annotations

import os

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.request_identity import build_request_identity
from saturated_fixed_work_baseline_v1_3.membind_v6.request_observation import (
    RequestObservationError,
    compare_request_observations,
    observe_request_identity,
    write_private_request_capture,
)


def _identity(**overrides):
    values = {
        "source_sequence": 1,
        "callsite": "extract_nodes.extract_message",
        "ordinal": 0,
        "messages": [{"role": "user", "content": "hello"}],
        "response_model": {"type": "json_schema", "schema": {"type": "object"}},
        "max_tokens": 16384,
        "model_size": "medium",
        "group_id": "g",
        "prompt_name": "extract_nodes.extract_message",
        "flags": {"attribute_extraction": False},
        "client_identity": {"class": "client", "source_hash": "abc"},
        "transport_identity": {"top_p": 1.0, "seed": 20260806},
        "cache_salt": "",
        "previous_context_digest": "state-1",
    }
    values.update(overrides)
    return build_request_identity(**values)


def test_exact_identity_matches_and_public_observation_has_no_payload() -> None:
    left = observe_request_identity(_identity())
    right = observe_request_identity(_identity())
    result = compare_request_observations(left, right)
    assert result == {"match": True, "changed_fields": [], "categories": []}
    assert "messages" not in left.public_summary
    assert left.public_summary["digest"] == right.public_summary["digest"]


@pytest.mark.parametrize(
    "field, value, category",
    [
        ("messages", [{"role": "user", "content": "changed"}], "prompt_formatting"),
        ("response_model", {"type": "json_object"}, "schema_or_tools"),
        ("transport_identity", {"top_p": 0.9, "seed": 20260806}, "client_or_transport_config"),
        ("previous_context_digest", "state-2", "graph_state_or_version"),
        ("ordinal", 1, "call_identity"),
    ],
)
def test_any_identity_change_is_a_fail_closed_miss(field, value, category) -> None:
    left = observe_request_identity(_identity())
    right = observe_request_identity(_identity(**{field: value}))
    result = compare_request_observations(left, right)
    assert result["match"] is False
    assert field in result["changed_fields"]
    assert category in result["categories"]


def test_private_capture_is_explicit_and_mode_restricted(tmp_path) -> None:
    observation = observe_request_identity(_identity())
    path = write_private_request_capture(tmp_path / "private.jsonl", [observation])
    assert path.stat().st_mode & 0o077 == 0
    assert "hello" in path.read_text()
    with pytest.raises(RequestObservationError, match="exists"):
        write_private_request_capture(path, [observation])
