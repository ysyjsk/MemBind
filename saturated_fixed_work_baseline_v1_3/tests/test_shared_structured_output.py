from __future__ import annotations

import asyncio

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6_1.shared_structured_output import (
    BoundedStructuredOutputAdapter,
    EdgePage,
    PageCapExhausted,
    SharedStructuredOutputContract,
    canonical_edge_tuple,
    adapter_identity,
    finite_edge_page_model,
    SHARED_CONSTRUCTION_MAX_TOKENS,
    SHARED_MAX_TOKENS,
)


def edge(source: str, target: str, fact: str = "f") -> dict[str, str]:
    return {
        "source_entity_name": source,
        "target_entity_name": target,
        "relation_type": "RELATED_TO",
        "fact": fact,
    }


def test_contract_is_arm_agnostic_and_finite() -> None:
    contract = SharedStructuredOutputContract(page_capacity=2, max_pages=3)
    assert contract.arm_identity is None
    assert contract.schema["properties"]["edges"]["maxItems"] == 2
    assert contract.schema["properties"]["edges"]["items"]["properties"]["fact"]["maxLength"] > 0
    assert contract.termination == "empty_page_only"


def test_shared_adapter_identity_is_frozen_and_arm_agnostic() -> None:
    identity = adapter_identity()
    assert identity["adapter_version"] == "shared-bounded-structured-output-v1"
    assert len(identity["adapter_source_sha256"]) == 64
    assert len(identity["schema_sha256"]) == 64
    assert len(identity["continuation_prompt_sha256"]) == 64
    assert identity["arm_identity"] is None
    assert identity["max_tokens"] == 16_384
    assert identity["wire_max_tokens"] == SHARED_MAX_TOKENS
    assert identity["construction_request_max_tokens"] == SHARED_CONSTRUCTION_MAX_TOKENS
    assert identity["schema_scope"] == "template"
    assert identity["schema_template_sha256"] == identity["schema_sha256"]
    assert len(identity["prompt_template_sha256"]) == 64
    assert identity["retry_policy"] == "single_attempt_no_retry_until_lucky_v1"


def test_endpoint_grounded_identity_separates_template_and_concrete_schema() -> None:
    template = adapter_identity()
    concrete = adapter_identity(("A", "B"))
    assert concrete["arm_identity"] is None
    assert concrete["schema_scope"] == "endpoint_grounded_concrete"
    assert concrete["schema_template_sha256"] == template["schema_template_sha256"]
    assert concrete["schema_sha256"] != template["schema_sha256"]
    assert concrete["endpoint_names"] == ["A", "B"]


def test_recovery_identity_uses_explicit_no_additional_edge_discriminator() -> None:
    identity = adapter_identity(("A", "B"), recovery=True)
    normal = adapter_identity(("A", "B"))
    assert identity["arm_identity"] is None
    assert identity["response_variant"] == "duplicate_recovery"
    assert normal["response_variant"] == "page"
    assert identity["prompt_template_sha256"] == normal["prompt_template_sha256"]
    assert identity["schema_template_sha256"] != normal["schema_template_sha256"]
    model = finite_edge_page_model(
        1,
        ("A", "B"),
        name_prefix="Shared",
        termination_discriminator=True,
    )
    schema = model.model_json_schema()
    assert schema["properties"]["status"]["enum"] == [
        "edge",
        "no_additional_edge",
    ]
    assert set(schema["required"]) == {"edges", "status"}


def test_shared_finite_model_supports_grounded_endpoints() -> None:
    model = finite_edge_page_model(1, ("A", "B"), name_prefix="Shared")
    schema = model.model_json_schema()
    assert schema["properties"]["edges"]["maxItems"] == 1
    edge_schema = next(iter(schema["$defs"].values()))
    assert edge_schema["properties"]["source_entity_name"]["enum"] == ["A", "B"]


def test_adapter_covers_all_edges_without_duplicates() -> None:
    pages = iter([
        EdgePage((edge("a", "b"), edge("b", "c"))),
        EdgePage((edge("c", "d"),)),
        EdgePage(()),
    ])
    adapter = BoundedStructuredOutputAdapter(
        contract=SharedStructuredOutputContract(page_capacity=2, max_pages=3),
        page_fetcher=lambda _continuation: next(pages),
        authoritative_entities=("a", "b", "c", "d"),
    )
    result = asyncio.run(adapter.collect())
    assert [canonical_edge_tuple(value) for value in result.edges] == [
        canonical_edge_tuple(edge("a", "b")),
        canonical_edge_tuple(edge("b", "c")),
        canonical_edge_tuple(edge("c", "d")),
    ]
    assert result.termination == "empty_page"


def test_duplicate_only_page_has_one_recovery_then_invalid() -> None:
    pages = iter([EdgePage((edge("a", "b"),)), EdgePage((edge("a", "b"),)), EdgePage((edge("a", "b"),))])
    adapter = BoundedStructuredOutputAdapter(
        contract=SharedStructuredOutputContract(page_capacity=1, max_pages=3),
        page_fetcher=lambda _continuation: next(pages),
        authoritative_entities=("a", "b"),
    )
    with pytest.raises(PageCapExhausted, match="duplicate-only"):
        asyncio.run(adapter.collect())


def test_page_bound_exhaustion_fails_closed() -> None:
    pages = iter([EdgePage((edge("a", "b"),)), EdgePage((edge("b", "c"),))])
    adapter = BoundedStructuredOutputAdapter(
        contract=SharedStructuredOutputContract(page_capacity=1, max_pages=2),
        page_fetcher=lambda _continuation: next(pages),
        authoritative_entities=("a", "b", "c"),
    )
    with pytest.raises(PageCapExhausted, match="page bound"):
        asyncio.run(adapter.collect())


def test_unknown_endpoint_is_rejected() -> None:
    adapter = BoundedStructuredOutputAdapter(
        contract=SharedStructuredOutputContract(page_capacity=1, max_pages=1),
        page_fetcher=lambda _continuation: EdgePage((edge("a", "foreign"),)),
        authoritative_entities=("a", "b"),
    )
    with pytest.raises(ValueError, match="authoritative"):
        asyncio.run(adapter.collect())
