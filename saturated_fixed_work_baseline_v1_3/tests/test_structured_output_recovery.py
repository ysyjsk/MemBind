from __future__ import annotations

import json

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime import (
    install_local_extraction_chunking_policy,
)

from saturated_fixed_work_baseline_v1_3.membind_v6_1.structured_output_recovery import (
    RecoveryIdentity,
    SchemaBoundednessError,
    StructuredOutputLengthTruncation,
    StructuredOutputMalformed,
    StructuredRecoveryController,
    build_schema_bound_certificate,
    classify_structured_failure,
    choose_edge_page_capacity,
    parse_structured_content,
    recovery_policy_sha256,
    schema_worst_case_characters,
    validate_schema_boundedness,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.provider import V61ProviderClient
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission import (
    CapacityAuthority,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.provider_admission import (
    provider_scope,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.transcript import (
    TranscriptStore,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.admission import (
    ForegroundAdmissionArbiter,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.policy import V61Policy


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


def test_finish_reason_precedes_json_parse_and_malformed_stop_is_distinct() -> None:
    with pytest.raises(StructuredOutputLengthTruncation):
        parse_structured_content(
            '{"edges":[{"fact":"unterminated',
            finish_reason="length",
            max_tokens=8,
        )
    assert classify_structured_failure(
        finish_reason="length", response_present=True
    ) == "OUTPUT_LENGTH_TRUNCATION"
    with pytest.raises(StructuredOutputMalformed) as raised:
        parse_structured_content('{"edges":', finish_reason="stop")
    assert classify_structured_failure(
        finish_reason="stop", error=raised.value, response_present=True
    ) == "MALFORMED_STRUCTURED_OUTPUT"


def test_provider_free_retry_counts_physical_attempts_without_transport_guard() -> None:
    class Delegate:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_response(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls < 3:
                raise ConnectionError("connection reset in fixture delegate")
            return {"ok": True}

    async def scenario() -> tuple[Delegate, list[dict[str, object]]]:
        delegate = Delegate()
        events: list[dict[str, object]] = []
        arbiter = ForegroundAdmissionArbiter(
            CapacityAuthority(2),
            policy=V61Policy(lookahead=1, future_cap=1, native_future_quota=0),
            event_sink=events.append,
        )
        client = V61ProviderClient(
            delegate,
            store=TranscriptStore(),
            arbiter=arbiter,
            mode="capture",
            durable_frontier=lambda: -1,
            event_sink=events.append,
        )
        with provider_scope(region="PREPARE", source_sequence=0):
            result = await client.generate_response(
                [{"role": "user", "content": "fixture"}],
                prompt_name="extract_nodes.extract_message",
                max_tokens=32,
            )
        assert result == {"ok": True}
        return delegate, [row for row in events if row.get("event") == "V61_PROVIDER_CALL"]

    import asyncio

    delegate, rows = asyncio.run(scenario())
    assert delegate.calls == 3
    assert len(rows) == 1
    row = rows[0]
    assert row["logical_attempt_count"] == 3
    assert row["physical_attempt_count"] == 3
    assert row["transport_attempt_count"] == 3
    assert row["transport_retry_count"] == 2


@pytest.mark.asyncio
async def test_recovery_controller_bounds_transient_and_truncation_attempts() -> None:
    variants: list[str] = []
    attempts = []

    async def operation(variant: str) -> dict[str, str]:
        variants.append(variant)
        if len(variants) == 1:
            raise StructuredOutputLengthTruncation(response_characters=4)
        return {"variant": variant}

    controller = StructuredRecoveryController(
        semantic_operation_id="source:3:edge:0",
        request_variant_id="page:2",
        attempt_sink=attempts.append,
    )
    result = await controller.run(
        operation,
        smaller_variant=lambda variant: "page:1" if variant == "page:2" else None,
    )
    assert result == {"variant": "page:1"}
    assert variants == ["page:2", "page:1"]
    assert len(attempts) == 2
    assert attempts[0].identity.request_variant_id == "page:2"
    assert attempts[1].identity.request_variant_id == "page:1"
    assert all(isinstance(item.identity, RecoveryIdentity) for item in attempts)
    assert recovery_policy_sha256()


@pytest.mark.asyncio
async def test_recovery_controller_stops_persistent_transient_and_never_retries_malformed() -> None:
    calls = 0

    async def always_transient(_variant: str) -> None:
        nonlocal calls
        calls += 1
        raise ConnectionError("connection reset")

    controller = StructuredRecoveryController(
        semantic_operation_id="source:4:node:0", request_variant_id="base"
    )
    with pytest.raises(ConnectionError):
        await controller.run(always_transient)
    assert calls == 3

    malformed_calls = 0

    async def malformed(_variant: str) -> None:
        nonlocal malformed_calls
        malformed_calls += 1
        raise StructuredOutputMalformed("bad json", position=2)

    with pytest.raises(StructuredOutputMalformed):
        await StructuredRecoveryController(
            semantic_operation_id="source:4:edge:0", request_variant_id="base"
        ).run(malformed)
    assert malformed_calls == 1


@pytest.mark.asyncio
async def test_edge_length_stop_uses_one_smaller_page_variant() -> None:
    calls: list[int] = []

    class Client:
        max_tokens = 32_768

        async def generate_response(self, _messages, *, response_model=None, **_kwargs):
            calls.append(len(calls))
            if len(calls) == 1:
                raise StructuredOutputLengthTruncation(
                    finish_reason="length", response_characters=64
                )
            return {"edges": []}

    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        edge_page_capacity=2,
    )
    result = await client.generate_response(
        [
            {
                "role": "user",
                "content": (
                    "<CURRENT MESSAGE>facts</CURRENT MESSAGE>"
                    '<ENTITIES>[{"name":"A"},{"name":"B"}]</ENTITIES>'
                ),
            }
        ],
        response_model=object(),
        max_tokens=65_536,
        prompt_name="extract_edges.edge",
    )
    assert result == {"edges": []}
    assert calls == [0, 1]
    physical = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("schema_version") == "membind.v6.1.extraction-diagnostic.v1"
    ]
    assert [row["status"] for row in physical] == ["failure", "success"]
    assert physical[0]["failure_class"] == "OUTPUT_LENGTH_TRUNCATION"
    assert physical[0]["certified_edge_page_capacity"] == 2
    assert physical[1]["certified_edge_page_capacity"] == 1
