from __future__ import annotations

import asyncio
import json
import re

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime import (
    LocalRuntimeConfigurationError,
    install_local_extraction_chunking_policy,
)


def _messages() -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": (
                "<CURRENT MESSAGE>\n[USER]\nA, B and C are discussed.\n"
                "</CURRENT MESSAGE>\n"
                '<ENTITIES>[{"name":"A"},{"name":"B"},{"name":"C"}]</ENTITIES>\n# TASK'
            ),
        }
    ]


def _pairs(content: str) -> list[str]:
    match = re.search(r"pairs_completed=(\[[^\n]+\])", content)
    assert match is not None
    return json.loads(match.group(1))


class _FiniteClient:
    max_tokens = 32_768
    call_events: list[dict[str, object]] = []

    def __init__(self, response_factory):
        self.response_factory = response_factory
        self.calls = 0

    async def generate_response(self, messages, response_model=None, **kwargs):
        self.calls += 1
        assert set(response_model.model_fields) == {"status", "pairs_completed", "edges"}
        pairs = _pairs(messages[0]["content"])
        self.call_events.append(
            {
                "finish_reason": "stop",
                "token_usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        )
        return self.response_factory(pairs)


def _edge(pair: str, fact_suffix: str = "") -> dict[str, object]:
    left, right = pair.split("||")
    return {
        "source_entity_name": left,
        "target_entity_name": right,
        "relation_type": "RELATED_TO",
        "fact": f"{left} relates to {right}{fact_suffix}",
        "valid_at": None,
        "invalid_at": None,
        "episode_indices": [0],
    }


def _install(client: _FiniteClient) -> None:
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        partition_extraction_by_turns=True,
        partition_edge_candidates=True,
        shared_bounded_structured_output=True,
        edge_partition_concurrency=1,
    )
    client._membind_entity_partition_hints.update(
        {"a": [0], "b": [0], "c": [0]}
    )


def test_runtime_executes_exact_finite_task_count_and_emits_digests() -> None:
    client = _FiniteClient(
        lambda pairs: {
            "status": "complete",
            "pairs_completed": pairs,
            "edges": [_edge(pair) for pair in pairs],
        }
    )
    _install(client)
    result = asyncio.run(
        client.generate_response(
            _messages(), max_tokens=32_768, prompt_name="extract_edges.edge"
        )
    )
    assert client.calls == 3
    assert len(result["edges"]) == 3
    plan = next(
        row
        for row in client._membind_extraction_diagnostics
        if row.get("schema_version") == "membind.v6.1.edge-task-plan.v1"
    )
    assert plan["declared_task_count"] == plan["completed_task_count"] == 3
    assert plan["maximum_provider_calls_per_source"] == 3
    assert plan["task_graph_digest"] and plan["coverage_digest"]
    assert plan["prompt_token_upper_bound"] == 300
    assert plan["completion_token_upper_bound"] == 3 * 16_384


def test_terminal_only_response_fails_closed_without_retry() -> None:
    client = _FiniteClient(lambda _pairs: {"status": "no_additional_edge", "edge": None})
    _install(client)
    with pytest.raises(LocalRuntimeConfigurationError, match="finite pair contract"):
        asyncio.run(
            client.generate_response(
                _messages(), max_tokens=32_768, prompt_name="extract_edges.edge"
            )
        )
    assert client.calls == 1


def test_repeated_edge_in_one_task_fails_closed() -> None:
    def repeated(pairs: list[str]) -> dict[str, object]:
        assert len(pairs) == 1
        return {
            "status": "complete",
            "pairs_completed": pairs,
            "edges": [
                _edge(pairs[0]),
                _edge(pairs[0], " second"),
                _edge(pairs[0], " third"),
            ],
        }

    client = _FiniteClient(repeated)
    _install(client)
    with pytest.raises(LocalRuntimeConfigurationError, match="finite pair contract"):
        asyncio.run(
            client.generate_response(
                _messages(), max_tokens=32_768, prompt_name="extract_edges.edge"
            )
        )
    assert client.calls == 1
