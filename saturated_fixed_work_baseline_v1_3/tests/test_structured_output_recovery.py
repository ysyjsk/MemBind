from __future__ import annotations

import json

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6_1.structured_output_recovery import (
    SchemaBoundednessError,
    build_schema_bound_certificate,
    choose_edge_page_capacity,
    schema_worst_case_characters,
    validate_schema_boundedness,
)


def finite_edge_schema(*, max_items: int = 1, fact_max_length: int = 256) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "edges": {
                "type": "array",
                "maxItems": max_items,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "source_entity_name": {"type": "string", "enum": ["甲", "B"]},
                        "target_entity_name": {"type": "string", "enum": ["甲", "B"]},
                        "relation_type": {"type": "string", "maxLength": 128},
                        "fact": {"type": "string", "maxLength": fact_max_length},
                        "valid_at": {
                            "anyOf": [
                                {"type": "string", "maxLength": 40},
                                {"type": "null"},
                            ]
                        },
                        "episode_indices": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 1,
                            "items": {"type": "integer", "const": 0},
                        },
                    },
                    "required": [
                        "source_entity_name",
                        "target_entity_name",
                        "relation_type",
                        "fact",
                    ],
                },
            }
        },
        "required": ["edges"],
    }


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda schema: schema["properties"]["edges"].pop("maxItems"), "array_max_items_missing"),
        (
            lambda schema: schema["properties"]["edges"]["items"]["properties"]["fact"].pop("maxLength"),
            "string_max_length_missing",
        ),
        (
            lambda schema: schema["properties"]["edges"]["items"].pop("additionalProperties"),
            "object_additional_properties_open",
        ),
        (
            lambda schema: schema["properties"]["edges"]["items"]["properties"]["episode_indices"].update(
                items={"type": "integer"}
            ),
            "number_range_missing",
        ),
    ],
)
def test_recursive_schema_validator_fails_closed(mutation, reason: str) -> None:
    schema = finite_edge_schema()
    mutation(schema)
    with pytest.raises(SchemaBoundednessError) as raised:
        validate_schema_boundedness(schema)
    assert reason in {issue.reason for issue in raised.value.issues}


def test_schema_character_bound_covers_cjk_and_json_escaping() -> None:
    schema = finite_edge_schema(fact_max_length=16)
    report = validate_schema_boundedness(schema)
    assert report.status == "PASS"
    maximum = schema_worst_case_characters(schema)
    payload = {
        "edges": [
            {
                "source_entity_name": "甲",
                "target_entity_name": "B",
                "relation_type": "R" * 128,
                "fact": '甲\\"\n' * 4,
                "valid_at": "2" * 40,
                "episode_indices": [0],
            }
        ]
    }
    assert len(json.dumps(payload, ensure_ascii=True, separators=(",", ":"))) <= maximum


def test_certificate_uses_exact_prompt_count_and_fails_closed() -> None:
    schema = finite_edge_schema(fact_max_length=64)
    messages = [{"role": "user", "content": "甲" * 17}]
    calls: list[object] = []

    def exact_counter(value) -> int:
        calls.append(value)
        return 101

    certificate = build_schema_bound_certificate(
        messages=messages,
        schema=schema,
        token_counter=exact_counter,
        context_limit=4096,
        effective_max_tokens=4096,
        safety_margin_tokens=32,
    )
    assert calls == [messages]
    assert certificate.exact_prompt_tokens == 101
    assert certificate.schema_worst_case_tokens <= certificate.effective_completion_budget
    assert certificate.status == "PASS"

    rejected = build_schema_bound_certificate(
        messages=messages,
        schema=schema,
        token_counter=exact_counter,
        context_limit=certificate.schema_worst_case_tokens + 100,
        effective_max_tokens=certificate.schema_worst_case_tokens - 1,
        safety_margin_tokens=32,
    )
    assert rejected.status == "FAIL"
    assert set(rejected.failure_reasons) == {
        "completion_budget_below_schema_bound",
        "context_budget_exhausted",
    }


def test_page_capacity_selection_is_deterministic_and_never_calls_provider() -> None:
    calls = 0

    def counter(_messages) -> int:
        nonlocal calls
        calls += 1
        return 100

    schemas = {2: finite_edge_schema(max_items=2, fact_max_length=3000), 1: finite_edge_schema(max_items=1, fact_max_length=3000)}
    selected = choose_edge_page_capacity(
        messages=[{"role": "user", "content": "fixture"}],
        schemas_by_capacity=schemas,
        requested_capacity=2,
        token_counter=counter,
        context_limit=65536,
        effective_max_tokens=32768,
        safety_margin_tokens=32,
    )
    assert selected.capacity in {1, 2}
    assert selected.capacity == choose_edge_page_capacity(
        messages=[{"role": "user", "content": "fixture"}],
        schemas_by_capacity=schemas,
        requested_capacity=2,
        token_counter=counter,
        context_limit=65536,
        effective_max_tokens=32768,
        safety_margin_tokens=32,
    ).capacity
    assert calls == 4
