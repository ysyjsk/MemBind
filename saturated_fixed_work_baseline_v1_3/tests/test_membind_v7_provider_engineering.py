from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.provider_diagnostics import (
    V7ProviderDiagnosticError,
    build_bailian_engineering_artifact,
    build_minimal_json_schema_probe,
    build_structured_edge_extraction_probe,
    build_structured_extraction_probe,
    load_engineering_provider_freeze,
    run_bailian_construction_probes_async,
    run_structured_extraction_probe_async,
)


@dataclass(frozen=True)
class _Episode:
    context_id: str
    source_sequence: int
    episode_id: str
    reference_time: str
    body: str


def _episode(sequence: int = 0) -> _Episode:
    return _Episode(
        context_id="context-0",
        source_sequence=sequence,
        episode_id=f"episode-{sequence}",
        reference_time="2023/01/01 (Sun) 00:00",
        body="[USER]\nAlice met Bob in Paris.\n[ASSISTANT]\nThey discussed MemBind.",
    )


class _QueuedCompletions:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        selected = self.responses.pop(0)
        content = json.dumps(selected["content"], ensure_ascii=True)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=selected.get("finish_reason", "stop"),
                    message=SimpleNamespace(content=content),
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=selected.get("prompt_tokens", 100),
                completion_tokens=selected.get("completion_tokens", 20),
                total_tokens=selected.get("total_tokens", 120),
            ),
        )


class _RejectedRequestCompletions:
    async def create(self, **kwargs: object) -> object:
        del kwargs

        class ProviderRequestError(Exception):
            status_code = 400
            code = "invalid_parameter"
            param = "response_format.json_schema"
            type = "invalid_request_error"
            body = {
                "message": "raw provider diagnostic must not be persisted",
                "code": code,
                "param": param,
                "type": type,
            }

        raise ProviderRequestError("secret request and provider message")


def _provider_freeze_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "v7/BAILIAN_ENGINEERING_PROVIDER_FREEZE.json"
    )


def _provider_freeze_v2_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "v7/BAILIAN_ENGINEERING_PROVIDER_FREEZE_V2.json"
    )


def test_bailian_provider_freeze_is_engineering_only_and_fail_closed(
    tmp_path: Path,
) -> None:
    frozen = load_engineering_provider_freeze(_provider_freeze_path())

    assert frozen["authority"] == "alibaba-bailian-openai-compatible-engineering-v1"
    assert frozen["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert frozen["construction_model"] == "qwen3.5-35b-a3b"
    assert frozen["formal_r1_r3_eligible"] is False
    assert frozen["gate_a_e_evaluated"] is False
    assert frozen["treatment_authorized"] is False
    assert frozen["diagnostic_only"] is True
    assert frozen["sdk_max_retries"] == 0
    assert frozen["hard_attempt_limit_per_probe"] == 1
    assert frozen["embedding"]["status"] == "NOT_FROZEN"
    assert frozen["embedding"]["model"] is None

    for forbidden_field in (
        "formal_r1_r3_eligible",
        "gate_a_e_evaluated",
        "treatment_authorized",
    ):
        tampered = json.loads(json.dumps(frozen))
        tampered[forbidden_field] = True
        path = tmp_path / f"{forbidden_field}.json"
        path.write_text(json.dumps(tampered), encoding="ascii")
        with pytest.raises(V7ProviderDiagnosticError, match="engineering-only"):
            load_engineering_provider_freeze(path)


def test_minimal_bailian_schema_probe_uses_frozen_request_policy() -> None:
    probe = build_minimal_json_schema_probe(
        model="qwen3.5-35b-a3b",
        max_tokens=256,
    )

    assert probe.probe_kind == "minimal_json_schema"
    assert probe.request["response_format"]["type"] == "json_schema"
    assert probe.request["extra_body"] == {"enable_thinking": False}
    assert probe.request["temperature"] == 0.0
    assert probe.request["top_p"] == 1.0
    encoded = json.dumps(probe.evidence, sort_keys=True)
    assert "Return exactly" not in encoded


def test_bailian_v2_freezes_explicit_json_object_compatibility() -> None:
    frozen = load_engineering_provider_freeze(_provider_freeze_v2_path())

    assert frozen["authority"] == (
        "alibaba-bailian-openai-compatible-engineering-json-object-v1"
    )
    assert frozen["construction_model"] == "qwen3.5-35b-a3b"
    assert frozen["structured_output_mode"] == "json_object"
    assert frozen["schema_policy"] == {
        "prompt_schema_injection": "graphiti-json-object-constrained-pydantic-v1",
        "response_validation": "pydantic-v2",
        "free_form_acceptance": False,
    }
    assert frozen["output_limit_policy"]["max_tokens_sent"] is False
    assert frozen["formal_r1_r3_eligible"] is False
    assert frozen["gate_a_e_evaluated"] is False
    assert frozen["treatment_authorized"] is False


def test_json_object_node_probe_injects_schema_and_omits_provider_max_tokens() -> None:
    probe = build_structured_extraction_probe(
        episode=_episode(),
        previous_episodes=(),
        namespace="v7-bailian-probe",
        model="qwen3.5-35b-a3b",
        max_tokens=16_384,
        structured_output_mode="json_object",
        send_max_tokens=False,
    )

    assert probe.request["response_format"] == {"type": "json_object"}
    assert "max_tokens" not in probe.request
    assert "Respond with a JSON object" in probe.request["messages"][-1]["content"]
    assert "extracted_entities" in probe.request["messages"][-1]["content"]
    assert probe.evidence["logical_max_tokens"] == 16_384
    assert probe.evidence["max_tokens_sent"] is False
    assert probe.evidence["injected_json_schema_name"] == "ExtractedEntities"
    assert len(probe.evidence["injected_json_schema_sha256"]) == 64
    encoded = json.dumps(probe.evidence, sort_keys=True)
    assert "Alice met Bob" not in encoded


def test_edge_probe_reconstructs_graphiti_edge_prompt_without_retaining_content() -> None:
    probe = build_structured_edge_extraction_probe(
        episode=_episode(),
        previous_episodes=(),
        namespace="v7-bailian-probe",
        model="qwen3.5-35b-a3b",
        max_tokens=16_384,
        entity_names=("Alice", "Bob", "Paris"),
    )

    assert probe.probe_kind == "extract_edges.edge"
    assert probe.request["max_tokens"] == 16_384
    assert probe.request["response_format"]["json_schema"]["name"] == "ExtractedEdges"
    assert "<ENTITIES>" in probe.request["messages"][1]["content"]
    assert "Alice" in probe.request["messages"][1]["content"]
    assert probe.evidence["entity_name_count"] == 3
    assert probe.evidence["previous_episode_count"] == 0
    encoded = json.dumps(probe.evidence, sort_keys=True)
    assert "Alice" not in encoded
    assert "Bob" not in encoded
    assert "Paris" not in encoded


@pytest.mark.asyncio
async def test_edge_probe_records_generic_item_count_and_response_size() -> None:
    probe = build_structured_edge_extraction_probe(
        episode=_episode(),
        previous_episodes=(),
        namespace="v7-bailian-probe",
        model="qwen3.5-35b-a3b",
        max_tokens=16_384,
        entity_names=("Alice", "Bob", "Paris"),
    )
    payload = {
        "edges": [
            {
                "source_entity_name": "Alice",
                "target_entity_name": "Bob",
                "relation_type": "MET",
                "fact": "Alice met Bob in Paris.",
                "valid_at": None,
                "invalid_at": None,
                "episode_indices": [0],
            }
        ]
    }
    completions = _QueuedCompletions([{"content": payload}])

    result = await run_structured_extraction_probe_async(
        probe,
        completions=completions,
        timeout_seconds=30,
    )

    content = json.dumps(payload, ensure_ascii=True)
    assert result["status"] == "PASS"
    assert result["probe_kind"] == "extract_edges.edge"
    assert result["parsed_item_count"] == 1
    assert result["parsed_edge_count"] == 1
    assert result["response_content_bytes"] == len(content.encode("utf-8"))
    encoded = json.dumps(result, sort_keys=True)
    assert "Alice met Bob" not in encoded
    assert '"edges"' not in encoded


@pytest.mark.asyncio
async def test_parseable_length_response_fails_construction_probe() -> None:
    probe = build_structured_extraction_probe(
        episode=_episode(),
        previous_episodes=(),
        namespace="v7-bailian-probe",
        model="qwen3.5-35b-a3b",
        max_tokens=32,
    )
    completions = _QueuedCompletions(
        [
            {
                "content": {"extracted_entities": []},
                "finish_reason": "length",
                "completion_tokens": 32,
                "total_tokens": 132,
            }
        ]
    )

    result = await run_structured_extraction_probe_async(
        probe,
        completions=completions,
        timeout_seconds=30,
    )

    assert result["status"] == "FAIL"
    assert result["classification"] == "STRUCTURED_EXTRACTION_INCOMPLETE"
    assert result["finish_reason"] == "length"
    assert result["usage"]["completion_tokens"] == 32
    assert result["parsed_item_count"] == 0


@pytest.mark.asyncio
async def test_provider_request_rejection_retains_only_safe_error_metadata() -> None:
    probe = build_minimal_json_schema_probe(
        model="qwen3.5-35b-a3b",
        max_tokens=256,
    )

    result = await run_structured_extraction_probe_async(
        probe,
        completions=_RejectedRequestCompletions(),
        timeout_seconds=30,
    )

    assert result["status"] == "FAIL"
    assert result["classification"] == "STRUCTURED_EXTRACTION_REQUEST_REJECTED"
    assert result["provider_error"] == {
        "http_status": 400,
        "code": "invalid_parameter",
        "param": "response_format.json_schema",
        "type": "invalid_request_error",
    }
    encoded = json.dumps(result, sort_keys=True)
    assert "raw provider diagnostic" not in encoded
    assert "secret request" not in encoded


@pytest.mark.asyncio
async def test_bailian_probe_chain_keeps_node_names_in_memory_only() -> None:
    completions = _QueuedCompletions(
        [
            {"content": {"status": "ok"}, "completion_tokens": 4},
            {
                "content": {
                    "extracted_entities": [
                        {"name": "Alice", "entity_type_id": 0, "episode_indices": [0]},
                        {"name": "Bob", "entity_type_id": 0, "episode_indices": [0]},
                        {"name": "Paris", "entity_type_id": 0, "episode_indices": [0]},
                    ]
                },
                "completion_tokens": 30,
            },
            {"content": {"edges": []}, "completion_tokens": 3},
        ]
    )

    result = await run_bailian_construction_probes_async(
        episode=_episode(),
        previous_episodes=(),
        namespace="v7-bailian-probe",
        model="qwen3.5-35b-a3b",
        minimal_max_tokens=256,
        node_max_tokens=16_384,
        edge_max_tokens=16_384,
        completions=completions,
        timeout_seconds=30,
    )

    assert result["status"] == "PASS"
    assert result["construction_probe_passed"] is True
    assert [item["probe_kind"] for item in result["probes"]] == [
        "minimal_json_schema",
        "extract_nodes.extract_message",
        "extract_edges.edge",
    ]
    assert len(completions.calls) == 3
    assert "Alice" in completions.calls[2]["messages"][1]["content"]
    encoded = json.dumps(result, sort_keys=True)
    assert "Alice" not in encoded
    assert "Bob" not in encoded
    assert "Paris" not in encoded
    assert result["database_called"] is False
    assert result["embedding_called"] is False
    assert result["treatment_calls"] == 0
    assert result["response_replay_calls"] == 0
    assert result["formal_r1_r3_eligible"] is False
    assert result["gate_outcome"] == "NOT_EVALUATED"

    artifact = build_bailian_engineering_artifact(
        run_id="v7-bailian-engineering-test",
        provider_freeze_path=_provider_freeze_path(),
        dataset_sha256=(
            "97fd80207f3419fc57c3684db824334224546d6bdd62c17ef52cd116eec9ffc8"
        ),
        source_sha256={
            "provider_diagnostics.py": "a" * 64,
            "probe_v7_bailian_engineering.py": "b" * 64,
        },
        timeout_seconds=900.0,
        chain_result=result,
    )

    assert artifact["status"] == "PASS"
    assert artifact["mode"] == "ENGINEERING_PROBE"
    assert artifact["provider"]["construction_model"] == "qwen3.5-35b-a3b"
    assert artifact["engineering_observer_eligible"] is True
    assert artifact["formal_r1_r3_eligible"] is False
    assert artifact["gate_a_e_evaluated"] is False
    assert artifact["gate_outcome"] == "NOT_EVALUATED"
    assert artifact["treatment_authorized"] is False
    assert artifact["scientific_method_selection_updated"] is False
    assert artifact["credentials_recorded"] is False
    assert "api_key" not in json.dumps(artifact, sort_keys=True).lower()


@pytest.mark.asyncio
async def test_json_object_probe_chain_validates_graphiti_models_without_token_limit() -> None:
    completions = _QueuedCompletions(
        [
            {"content": {"status": "ok"}, "completion_tokens": 4},
            {
                "content": {
                    "extracted_entities": [
                        {"name": "Alice", "entity_type_id": 0, "episode_indices": [0]},
                        {"name": "Bob", "entity_type_id": 0, "episode_indices": [0]},
                    ]
                },
                "completion_tokens": 20,
            },
            {"content": {"edges": []}, "completion_tokens": 3},
        ]
    )

    result = await run_bailian_construction_probes_async(
        episode=_episode(),
        previous_episodes=(),
        namespace="v7-bailian-json-object-probe",
        model="qwen3.5-35b-a3b",
        minimal_max_tokens=256,
        node_max_tokens=16_384,
        edge_max_tokens=16_384,
        completions=completions,
        timeout_seconds=30,
        structured_output_mode="json_object",
        send_max_tokens=False,
    )

    assert result["status"] == "PASS"
    assert result["structured_output_mode"] == "json_object"
    assert all("max_tokens" not in call for call in completions.calls)
    assert all(call["response_format"] == {"type": "json_object"} for call in completions.calls)
    assert all(
        "Respond with a JSON object" in call["messages"][-1]["content"]
        for call in completions.calls
    )
