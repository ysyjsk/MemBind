from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6_1.shared_structured_output import (
    BoundedStructuredOutputAdapter,
    EdgePage,
    PageCapExhausted,
    SharedStructuredOutputContract,
    canonical_edge_tuple,
    adapter_identity,
    bounded_ascii_pattern,
    finite_edge_page_model,
    SHARED_CONSTRUCTION_MAX_TOKENS,
    SHARED_MAX_TOKENS,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime import (
    _bounded_node_response_model,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.structured_output_recovery import (
    schema_worst_case_characters,
)


def edge(source: str, target: str, fact: str = "f") -> dict[str, str]:
    return {
        "source_entity_name": source,
        "target_entity_name": target,
        "relation_type": "RELATED_TO",
        "fact": fact,
    }


def _xgrammar_accepts(schema: dict, payload: object) -> bool:
    import xgrammar

    compiler = xgrammar.GrammarCompiler(
        xgrammar.TokenizerInfo([]), cache_enabled=False
    )
    compiled = compiler.compile_json_schema(schema, any_whitespace=False)
    matcher = xgrammar.GrammarMatcher(
        compiled, terminate_without_stop_token=True
    )
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(", ", ": "),
    )
    return matcher.accept_string(rendered) and matcher.is_terminated()


def test_bounded_ascii_pattern_has_an_explicit_finite_quantifier() -> None:
    assert bounded_ascii_pattern(1, 256) == (
        r'^(?:[\x20-\x21\x23-\x5b\x5d-\x7e]|\\["\\/bfnrt]){1,256}$'
    )
    assert "*" not in bounded_ascii_pattern(1, 1_900)


def test_installed_xgrammar_enforces_node_schema_physical_bounds() -> None:
    schema = _bounded_node_response_model(1).model_json_schema()

    def payload(name: str, *, count: int = 1) -> dict:
        return {
            "extracted_entities": [
                {
                    "name": name,
                    "entity_type_id": 0,
                    "episode_indices": [0],
                }
                for _ in range(count)
            ]
        }

    assert _xgrammar_accepts(schema, payload("a" * 256))
    assert not _xgrammar_accepts(schema, payload("a" * 257))
    assert not _xgrammar_accepts(schema, payload("\u7532"))
    assert not _xgrammar_accepts(schema, payload("a", count=2))
    name_schema = next(iter(schema["$defs"].values()))["properties"]["name"]
    assert name_schema["pattern"] == bounded_ascii_pattern(1, 256)
    assert "*" not in name_schema["pattern"]


def test_installed_xgrammar_enforces_edge_schema_physical_bounds() -> None:
    schema = finite_edge_page_model(
        1,
        ("A", "B"),
        termination_discriminator=True,
    ).model_json_schema()

    def payload(fact: str) -> dict:
        return {
            "status": "new_edge",
            "edge": {
                "source_entity_name": "A",
                "target_entity_name": "B",
                "relation_type": "RELATED_TO",
                "fact": fact,
                "valid_at": None,
                "invalid_at": None,
                "episode_indices": [0],
            },
        }

    assert _xgrammar_accepts(schema, payload("a" * 1_900))
    assert not _xgrammar_accepts(schema, payload("a" * 1_901))
    assert not _xgrammar_accepts(schema, payload("\u7532"))
    edge_schema = next(iter(schema["$defs"].values()))
    for field, limits in {
        "relation_type": (1, 128),
        "fact": (1, 1_900),
        "valid_at": (0, 40),
        "invalid_at": (0, 40),
    }.items():
        selected = edge_schema["properties"][field]
        if "anyOf" in selected:
            selected = next(
                branch
                for branch in selected["anyOf"]
                if branch.get("type") == "string"
            )
        assert selected["pattern"] == bounded_ascii_pattern(*limits)
        assert "*" not in selected["pattern"]


def test_contract_is_arm_agnostic_and_finite() -> None:
    contract = SharedStructuredOutputContract(page_capacity=2, max_pages=3)
    assert contract.arm_identity is None
    assert contract.schema["properties"]["status"]["enum"] == [
        "new_edge",
        "no_additional_edge",
    ]
    assert contract.schema["properties"]["edge"]["properties"]["fact"]["maxLength"] > 0
    assert contract.termination == "explicit_cursor_exhaustion"


def test_canonical_cursor_continuation_is_constant_size_over_history_growth() -> None:
    contract = SharedStructuredOutputContract()
    edges = [
        {
            **edge("A", "B", f"fact-{index:04d}-" + "x" * 1_850),
            "valid_at": None,
            "invalid_at": None,
        }
        for index in range(128)
    ]

    lengths = [len(contract.continuation(edges[:count])) for count in range(1, 129)]
    assert max(lengths) == min(lengths)
    assert contract.continuation(()) == "EDGE_CURSOR: null"
    assert "fact-0127" in contract.continuation(edges)
    assert "fact-0126" not in contract.continuation(edges)


def test_shared_adapter_identity_is_frozen_and_arm_agnostic() -> None:
    identity = adapter_identity()
    assert identity["adapter_version"] == (
        "shared-bounded-structured-output-v6-explicit-terminal-confirmation"
    )
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
    assert identity["retry_policy"] == (
        "single_attempt_per_distinct_schema_request_fail_closed_v2"
    )
    assert identity["terminal_confirmation_policy"] == (
        "one_distinct_terminal_only_request_after_provider_repeat_not_context_retry_v1"
    )
    assert identity["terminal_confirmation_is_context_retry"] is False
    assert identity["json_whitespace_mode"] == "disable_any_whitespace_vllm_v1"
    assert identity["physical_serialization_bound"] is True
    assert identity["response_variant"] == "schema_enforced_canonical_cursor"


def test_endpoint_grounded_identity_separates_template_and_concrete_schema() -> None:
    template = adapter_identity()
    concrete = adapter_identity(("A", "B"))
    assert concrete["arm_identity"] is None
    assert concrete["schema_scope"] == "endpoint_grounded_concrete"
    assert concrete["schema_template_sha256"] == template["schema_template_sha256"]
    assert concrete["schema_sha256"] != template["schema_sha256"]
    assert concrete["endpoint_names"] == ["A", "B"]


def test_legacy_recovery_identity_uses_explicit_no_additional_edge_discriminator() -> None:
    identity = adapter_identity(("A", "B"), recovery=True, cursor_protocol=False)
    normal = adapter_identity(("A", "B"), cursor_protocol=False)
    assert identity["arm_identity"] is None
    assert identity["response_variant"] == "duplicate_recovery"
    assert normal["response_variant"] == "page"
    assert identity["prompt_template_sha256"] == normal["prompt_template_sha256"]
    assert identity["schema_template_sha256"] != normal["schema_template_sha256"]
    model = finite_edge_page_model(
        1,
        ("A", "B"),
        name_prefix="MemBindEndpointGrounded",
        edge_name="MemBindEndpointGroundedEdge1_2",
        page_name="MemBindEndpointGroundedRecoveryEdgePage1_2",
        termination_discriminator=True,
    )
    schema = model.model_json_schema()
    assert schema["properties"]["status"]["enum"] == [
        "new_edge",
        "no_additional_edge",
    ]


def test_legacy_final_abstention_identity_allows_only_no_additional_edge() -> None:
    identity = adapter_identity(
        ("A", "B"),
        recovery=True,
        no_additional_only=True,
        cursor_protocol=False,
    )
    assert identity["response_variant"] == "duplicate_recovery_final_abstention"
    model = finite_edge_page_model(
        1,
        ("A", "B"),
        name_prefix="MemBindEndpointGrounded",
        edge_name="MemBindEndpointGroundedEdge1_2",
        page_name="MemBindEndpointGroundedRecoveryEdgePage1_2",
        termination_discriminator=True,
        no_additional_only=True,
    )
    schema = model.model_json_schema()
    assert schema["properties"]["status"]["const"] == "no_additional_edge"
    assert schema["properties"]["edge"]["type"] == "null"
    assert set(schema["required"]) == {"status", "edge"}
    assert "edge" in schema["properties"]
    assert identity["schema_sha256"] == hashlib.sha256(
        json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_recovery_edge_schema_excludes_the_repeated_canonical_tuple() -> None:
    repeated = canonical_edge_tuple(edge("A", "B", "A knows B"))
    model = finite_edge_page_model(
        1,
        ("A", "B"),
        name_prefix="Shared",
        termination_discriminator=True,
        excluded_edge=repeated,
    )
    edge_schema = model.model_json_schema()["$defs"]
    edge_schema = next(value for value in edge_schema.values() if "not" in value)
    assert edge_schema["not"]["required"] == [
        "source_entity_name",
        "target_entity_name",
        "relation_type",
        "fact",
        "valid_at",
        "invalid_at",
    ]
    assert edge_schema["not"]["properties"]["fact"]["const"] == "A knows B"


def test_rolling_cursor_schema_excludes_one_1900_character_edge_and_stays_admissible() -> None:
    repeated = canonical_edge_tuple(
        {
            **edge("A", "B", "x" * 1_900),
            "valid_at": None,
            "invalid_at": None,
        }
    )
    base = finite_edge_page_model(
        1,
        ("A", "B"),
        name_prefix="Shared",
        termination_discriminator=True,
    ).model_json_schema()
    rolling = finite_edge_page_model(
        1,
        ("A", "B"),
        name_prefix="SharedRolling",
        termination_discriminator=True,
        excluded_edge=repeated,
    ).model_json_schema()
    excluded = next(value for value in rolling["$defs"].values() if "not" in value)
    assert excluded["not"]["properties"]["fact"]["const"] == "x" * 1_900
    assert schema_worst_case_characters(rolling) <= SHARED_MAX_TOKENS
    assert schema_worst_case_characters(rolling) == schema_worst_case_characters(base)

    base_identity = adapter_identity(("A", "B"))
    rolling_identity = adapter_identity(("A", "B"), excluded_edge=repeated)
    assert rolling_identity["schema_template_sha256"] == base_identity["schema_template_sha256"]
    assert rolling_identity["schema_sha256"] != base_identity["schema_sha256"]
    assert rolling_identity["cursor_exclusion_policy"] == (
        "single_previous_canonical_tuple_not_const_v1"
    )


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
    assert result.termination == "explicit_cursor_exhaustion"


def test_duplicate_only_page_has_one_recovery_then_invalid() -> None:
    calls = 0

    def fetch(_continuation: str) -> EdgePage:
        nonlocal calls
        calls += 1
        return EdgePage((edge("a", "b"),))

    adapter = BoundedStructuredOutputAdapter(
        contract=SharedStructuredOutputContract(page_capacity=1, max_pages=3),
        page_fetcher=fetch,
        authoritative_entities=("a", "b"),
    )
    with pytest.raises(PageCapExhausted, match="strict canonical successor"):
        asyncio.run(adapter.collect())
    assert calls == 2


def test_page_epoch_saturation_continues_until_explicit_exhaustion() -> None:
    pages = iter(
        [
            EdgePage((edge("a", "b", f"fact-{index:04d}"),))
            for index in range(65)
        ]
        + [EdgePage(())]
    )
    adapter = BoundedStructuredOutputAdapter(
        contract=SharedStructuredOutputContract(page_capacity=1, max_pages=64),
        page_fetcher=lambda _continuation: next(pages),
        authoritative_entities=("a", "b"),
    )
    result = asyncio.run(adapter.collect())
    assert len(result.edges) == 65
    assert result.page_count == 66
    assert result.termination == "explicit_cursor_exhaustion"


def test_unknown_endpoint_is_rejected() -> None:
    adapter = BoundedStructuredOutputAdapter(
        contract=SharedStructuredOutputContract(page_capacity=1, max_pages=1),
        page_fetcher=lambda _continuation: EdgePage((edge("a", "foreign"),)),
        authoritative_entities=("a", "b"),
    )
    with pytest.raises(ValueError, match="authoritative"):
        asyncio.run(adapter.collect())


def test_self_edge_candidate_is_rejected_without_poisoning_valid_page() -> None:
    from saturated_fixed_work_baseline_v1_3.membind_v6_1.shared_structured_output import validate_edge_page

    page = {"edges": [edge("a", "a"), edge("a", "b")]}
    contract = SharedStructuredOutputContract(page_capacity=2, max_pages=1)
    validated = validate_edge_page(
        page, contract=contract, authoritative_entities=("a", "b")
    )
    assert len(validated) == 1
    assert validated[0]["source_entity_name"] == "a"
