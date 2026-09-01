from __future__ import annotations

import json
from typing import Any

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime import (
    LOCAL_EDGE_FACT_MAX_CHARS,
    LOCAL_TIMESTAMP_BATCH_MAX_ITEMS,
    LocalRuntimeConfigurationError,
    _bounded_attribute_response_model,
    _bounded_edge_duplicate_model,
    _bounded_edge_page_model,
    _bounded_node_response_model,
    _edge_candidate_capacities,
    install_local_extraction_chunking_policy,
)

from saturated_fixed_work_baseline_v1_3.membind_v6_1.structured_output_recovery import (
    RecoveryIdentity,
    SchemaBoundednessError,
    StructuredOutputLengthTruncation,
    StructuredOutputBudgetError,
    StructuredOutputMalformed,
    StructuredRecoveryController,
    build_schema_bound_certificate,
    classify_structured_failure,
    choose_edge_page_capacity,
    choose_node_schema_capacity,
    parse_structured_content,
    recovery_policy_sha256,
    schema_worst_case_characters,
    validate_schema_boundedness,
    reliability_identity,
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


def test_recursive_schema_validator_rejects_unknown_untyped_values() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"payload": {}},
    }
    with pytest.raises(SchemaBoundednessError) as raised:
        validate_schema_boundedness(schema)
    assert "schema_type_missing" in {
        issue.reason for issue in raised.value.issues
    }


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


def test_edge_wire_schema_fits_pinned_graphiti_completion_budget() -> None:
    schema = _bounded_edge_page_model(1).model_json_schema()
    certificate = build_schema_bound_certificate(
        messages=[{"role": "user", "content": "fixture"}],
        schema=schema,
        token_counter=lambda _messages: 1,
        context_limit=65_536,
        effective_max_tokens=16_384,
        safety_margin_tokens=32,
    )
    assert LOCAL_EDGE_FACT_MAX_CHARS > 0
    assert certificate.schema_worst_case_tokens <= 16_384
    assert certificate.status == "PASS"


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


def test_node_schema_capacity_binds_to_partition_completion_budget() -> None:
    schemas = {
        capacity: _bounded_node_response_model(capacity).model_json_schema()
        for capacity in range(16, 0, -1)
    }

    selected = choose_node_schema_capacity(
        messages=[{"role": "user", "content": "fixture"}],
        schemas_by_capacity=schemas,
        requested_capacity=16,
        token_counter=lambda _messages: 100,
        context_limit=65_536,
        effective_max_tokens=8_192,
        safety_margin_tokens=32,
    )

    assert selected.capacity == 5
    assert selected.rejected_capacities == (16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6)
    assert selected.certificate.status == "PASS"
    assert selected.certificate.schema_worst_case_tokens <= 8_192


def test_node_schema_capacity_fails_closed_when_even_one_entity_cannot_fit() -> None:
    schemas = {1: _bounded_node_response_model(1).model_json_schema()}

    with pytest.raises(StructuredOutputBudgetError, match="node schema capacity"):
        choose_node_schema_capacity(
            messages=[{"role": "user", "content": "fixture"}],
            schemas_by_capacity=schemas,
            requested_capacity=1,
            token_counter=lambda _messages: 100,
            context_limit=65_536,
            effective_max_tokens=1_000,
            safety_margin_tokens=32,
        )


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


def test_caller_supplied_attribute_schema_is_bounded_before_provider() -> None:
    from pydantic import BaseModel

    class Attributes(BaseModel):
        label: str
        aliases: list[str] = []
        score: int = 0

    bounded = _bounded_attribute_response_model(Attributes)
    schema = bounded.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["label"]["maxLength"] == 250
    assert schema["properties"]["aliases"]["maxItems"] == 8
    assert schema["properties"]["aliases"]["items"]["maxLength"] == 250
    assert schema["properties"]["score"]["minimum"] < 0
    assert schema["properties"]["score"]["maximum"] > 0
    assert validate_schema_boundedness(schema).status == "PASS"


def test_caller_supplied_any_attribute_fails_closed() -> None:
    from pydantic import BaseModel

    class Attributes(BaseModel):
        payload: Any

    with pytest.raises(LocalRuntimeConfigurationError, match="schema_type_missing"):
        _bounded_attribute_response_model(Attributes)


@pytest.mark.asyncio
async def test_timestamp_batch_over_certificate_limit_fails_before_delegate() -> None:
    calls = 0

    class Client:
        max_tokens = 32_768
        structured_output_recovery_enabled = True

        async def generate_response(self, _messages, **_kwargs):
            nonlocal calls
            calls += 1
            return {"timestamps": []}

    facts = [
        {"fact": f"fact-{index}", "reference_time": "2026-01-01T00:00:00Z"}
        for index in range(LOCAL_TIMESTAMP_BATCH_MAX_ITEMS + 1)
    ]
    client = Client()
    install_local_extraction_chunking_policy(client, token_counter=lambda _messages: 1)
    with pytest.raises(LocalRuntimeConfigurationError, match="timestamp batch"):
        await client.generate_response(
            [
                {
                    "role": "user",
                    "content": f"<FACTS>\n{facts!r}\n</FACTS>",
                }
            ],
            response_model=object(),
            max_tokens=32_768,
            prompt_name="extract_edges.extract_timestamps_batch",
        )
    assert calls == 0


def test_edge_dedupe_schema_binds_existing_and_full_candidate_flights() -> None:
    messages = [
        {
            "role": "user",
            "content": (
                "<EXISTING FACTS>\n"
                "[{'idx': 0, 'fact': 'a'}, {'idx': 1, 'fact': 'b'}]\n"
                "</EXISTING FACTS>\n"
                "<FACT INVALIDATION CANDIDATES>\n"
                "[{'idx': 2, 'fact': 'c'}]\n"
                "</FACT INVALIDATION CANDIDATES>"
            ),
        }
    ]
    assert _edge_candidate_capacities(messages) == (2, 3)
    schema = _bounded_edge_duplicate_model(2, 3).model_json_schema()
    assert schema["properties"]["duplicate_facts"]["items"]["maximum"] == 1
    assert schema["properties"]["contradicted_facts"]["items"]["maximum"] == 2


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
    assert row["physical_attempt_ids"] == [
        f"0:extract_nodes.extract_message:0:{index}" for index in range(3)
    ]


def test_transient_physical_retries_reuse_proxy_identity_until_next_operation() -> None:
    class Delegate:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_response(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls < 3:
                raise ConnectionError("connection reset in fixture delegate")
            return {"ok": True}

    async def scenario():
        delegate = Delegate()
        events: list[dict[str, object]] = []
        client = V61ProviderClient(
            delegate,
            store=TranscriptStore(),
            arbiter=ForegroundAdmissionArbiter(
                CapacityAuthority(2),
                policy=V61Policy(lookahead=1, future_cap=1, native_future_quota=0),
            ),
            mode="capture",
            durable_frontier=lambda: -1,
            event_sink=events.append,
        )
        with provider_scope(region="PREPARE", source_sequence=0):
            await client.generate_response(
                [{"role": "user", "content": "fixture"}],
                prompt_name="extract_nodes.extract_message",
                max_tokens=32,
            )
            await client.generate_response(
                [{"role": "user", "content": "fixture"}],
                prompt_name="extract_nodes.extract_message",
                max_tokens=32,
            )
        return delegate, client

    import asyncio

    delegate, client = asyncio.run(scenario())
    assert delegate.calls == 4
    observed = [row["public_summary"] for row in client.observations]
    assert [row["ordinal"] for row in observed] == [0, 0, 0, 1]
    assert len({row["digest"] for row in observed[:3]}) == 1
    assert observed[3]["digest"] != observed[0]["digest"]
    assert client.provider_calls[0]["semantic_operation_id"].endswith(":0")
    assert client.provider_calls[0]["request_variant_id"] == observed[0]["digest"]
    assert len(set(client.provider_calls[0]["physical_attempt_ids"])) == 3
    assert client.provider_calls[1]["semantic_operation_id"].endswith(":1")


def test_v61_structured_failure_is_not_retried_or_captured() -> None:
    class Delegate:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_response(self, *_args, **_kwargs):
            self.calls += 1
            raise StructuredOutputMalformed("bad json", position=3)

    async def scenario() -> tuple[Delegate, list[dict[str, object]], TranscriptStore]:
        delegate = Delegate()
        events: list[dict[str, object]] = []
        store = TranscriptStore()
        arbiter = ForegroundAdmissionArbiter(
            CapacityAuthority(2),
            policy=V61Policy(lookahead=1, future_cap=1, native_future_quota=0),
            event_sink=events.append,
        )
        client = V61ProviderClient(
            delegate,
            store=store,
            arbiter=arbiter,
            mode="capture",
            durable_frontier=lambda: -1,
            event_sink=events.append,
        )
        with provider_scope(region="PREPARE", source_sequence=0):
            with pytest.raises(StructuredOutputMalformed):
                await client.generate_response(
                    [{"role": "user", "content": "fixture"}],
                    prompt_name="extract_nodes.extract_message",
                    max_tokens=32,
                )
        return delegate, [row for row in events if row.get("event") == "V61_PROVIDER_CALL"], store

    import asyncio

    delegate, rows, store = asyncio.run(scenario())
    assert delegate.calls == 1
    assert len(rows) == 1
    assert rows[0]["failure_class"] == "MALFORMED_STRUCTURED_OUTPUT"
    assert rows[0]["logical_attempt_count"] == 1
    assert rows[0]["physical_attempt_count"] == 1
    assert len(rows[0]["physical_attempt_ids"]) == 1
    assert store.summary()["logical_captured"] == 0


def test_shared_reliability_identity_is_single_frozen_policy() -> None:
    identity = reliability_identity()
    assert identity["runtime_reliability_profile"] == "shared-structured-output-recovery-v1"
    assert identity["schema_revision"] == "finite-edge-schema-v1"
    assert identity["recovery_policy_revision"] == "classified-request-recovery-v1"
    assert len(identity["recovery_policy_sha256"]) == 64


@pytest.mark.asyncio
async def test_recovery_controller_fails_closed_on_certified_truncation() -> None:
    variants: list[str] = []
    attempts = []

    async def operation(variant: str) -> dict[str, str]:
        variants.append(variant)
        raise StructuredOutputLengthTruncation(response_characters=4)

    controller = StructuredRecoveryController(
        semantic_operation_id="source:3:edge:0",
        request_variant_id="page:2",
        attempt_sink=attempts.append,
    )
    with pytest.raises(StructuredOutputLengthTruncation):
        await controller.run(
            operation,
            smaller_variant=lambda variant: "page:1" if variant == "page:2" else None,
        )
    assert variants == ["page:2"]
    assert len(attempts) == 1
    assert attempts[0].identity.request_variant_id == "page:2"
    assert all(isinstance(item.identity, RecoveryIdentity) for item in attempts)
    assert recovery_policy_sha256()


@pytest.mark.asyncio
async def test_recovery_controller_does_not_retry_context_budget_variant() -> None:
    calls = 0

    async def operation(_variant: str) -> None:
        nonlocal calls
        calls += 1
        raise StructuredOutputBudgetError("context budget exhausted")

    controller = StructuredRecoveryController(
        semantic_operation_id="source:5:node:0", request_variant_id="base"
    )
    with pytest.raises(StructuredOutputBudgetError):
        await controller.run(
            operation,
            context_variant=lambda variant: f"{variant}:reduced-context",
            classify=lambda _error: "CONTEXT_BUDGET_EXHAUSTED",
        )
    assert calls == 1
    assert len(controller.attempts) == 1
    assert controller.attempts[0].identity.request_variant_id == "base"


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
async def test_edge_length_stop_fails_closed_without_hidden_resend() -> None:
    calls: list[int] = []

    class Client:
        max_tokens = 32_768

        async def generate_response(self, _messages, *, response_model=None, **_kwargs):
            calls.append(len(calls))
            raise StructuredOutputLengthTruncation(
                finish_reason="length", response_characters=64
            )

    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        edge_page_capacity=2,
    )
    with pytest.raises(StructuredOutputLengthTruncation):
        await client.generate_response(
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
    assert calls == [0]
    physical = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("schema_version") == "membind.v6.1.extraction-diagnostic.v1"
    ]
    assert [row["status"] for row in physical] == ["failure"]
    assert physical[0]["failure_class"] == "OUTPUT_LENGTH_TRUNCATION"
    assert physical[0]["certified_edge_page_capacity"] == 2
