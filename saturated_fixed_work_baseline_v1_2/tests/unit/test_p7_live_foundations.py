from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from saturated_fixed_work_baseline_v1_2.dataset import (
    EXPECTED_EPISODE_COUNTS,
    DatasetError,
    freeze_development_dataset,
    load_episode_inputs,
)
from saturated_fixed_work_baseline_v1_2.live import (
    FORMAL_ORDER,
    build_formal_plan,
    derive_cache_salt,
    derive_namespace,
)
from saturated_fixed_work_baseline_v1_2.preflight import (
    PreflightError,
    PreflightEvidence,
    SamplerQualification,
    validate_preflight,
)
from saturated_fixed_work_baseline_v1_2.schedules import Method
from saturated_fixed_work_baseline_v1_2.transport import (
    CacheSaltError,
    SaltedOpenAITransport,
    install_runtime_cache_salt,
)


def test_load_episode_inputs_matches_frozen_manifest(repository_root: Path) -> None:
    frozen = freeze_development_dataset(repository_root)
    manifest_by_history = {
        row["history_id"]: row for row in frozen["history_manifests"]
    }
    for history_id, expected_count in EXPECTED_EPISODE_COUNTS.items():
        namespace = f"sfwb-v1-2/{Method.B0_NATIVE_SERIAL.value}/{history_id}/test"
        episodes = load_episode_inputs(repository_root, history_id, namespace)
        assert len(episodes) == expected_count
        assert [row.source_sequence for row in episodes] == list(range(expected_count))
        assert [row.source_hash for row in episodes] == manifest_by_history[history_id][
            "source_hashes"
        ]
        assert all(row.namespace == namespace and row.body for row in episodes)


def test_load_episode_inputs_rejects_non_frozen_history(repository_root: Path) -> None:
    with pytest.raises(DatasetError, match="HISTORY_NOT_FROZEN"):
        load_episode_inputs(repository_root, "not-frozen", "namespace")


class _CreateRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def create(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.calls.append((args, kwargs))
        return {"ok": True}


@pytest.mark.asyncio
async def test_cache_salt_transport_only_injects_extra_body() -> None:
    completions = _CreateRecorder()
    embeddings = _CreateRecorder()
    inner = SimpleNamespace(
        chat=SimpleNamespace(completions=completions), embeddings=embeddings
    )
    transport = SaltedOpenAITransport(inner, "block-salt")
    messages = [{"role": "user", "content": "unchanged"}]
    await transport.chat.completions.create(
        model="frozen-model",
        messages=messages,
        temperature=0.0,
        extra_body={"guided_json": {"type": "object"}},
    )
    await transport.embeddings.create(
        model="frozen-embedding", input=["unchanged"], extra_body={"encoding": "float"}
    )
    completion_request = completions.calls[0][1]
    embedding_request = embeddings.calls[0][1]
    assert completion_request["messages"] is messages
    assert completion_request["temperature"] == 0.0
    assert completion_request["extra_body"] == {
        "guided_json": {"type": "object"},
        "cache_salt": "block-salt",
    }
    assert embedding_request["input"] == ["unchanged"]
    assert embedding_request["extra_body"] == {
        "encoding": "float",
        "cache_salt": "block-salt",
    }


@pytest.mark.parametrize("salt", ["", "x" * 65, "contains space", "nonascii-\u4e2d"])
def test_cache_salt_rejects_invalid_values(salt: str) -> None:
    with pytest.raises(CacheSaltError, match="CACHE_SALT_INVALID"):
        SaltedOpenAITransport(SimpleNamespace(), salt)


def test_runtime_cache_salt_wraps_llm_embedding_and_reranker_without_admission() -> None:
    llm_transport = SimpleNamespace(chat=SimpleNamespace(completions=_CreateRecorder()))
    embed_transport = SimpleNamespace(embeddings=_CreateRecorder())
    reranker_transport = SimpleNamespace(chat=SimpleNamespace(completions=_CreateRecorder()))
    runtime = SimpleNamespace(
        llm_client=SimpleNamespace(client=llm_transport),
        embedder=SimpleNamespace(client=embed_transport),
        reranker=SimpleNamespace(client=reranker_transport),
        graphiti=SimpleNamespace(),
    )
    install_runtime_cache_salt(runtime, "formal-block-001")
    assert isinstance(runtime.llm_client.client, SaltedOpenAITransport)
    assert isinstance(runtime.embedder.client, SaltedOpenAITransport)
    assert isinstance(runtime.reranker.client, SaltedOpenAITransport)
    assert not hasattr(runtime, "admission")
    assert not hasattr(runtime.graphiti, "admission")


def test_formal_plan_is_exact_alternating_order_and_unique() -> None:
    assert FORMAL_ORDER == (
        ("07741c45", Method.B0_NATIVE_SERIAL),
        ("07741c45", Method.B1_NAIVE_WHOLE_UPDATE_ASYNC),
        ("b6019101", Method.B1_NAIVE_WHOLE_UPDATE_ASYNC),
        ("b6019101", Method.B0_NATIVE_SERIAL),
        ("6071bd76", Method.B0_NATIVE_SERIAL),
        ("6071bd76", Method.B1_NAIVE_WHOLE_UPDATE_ASYNC),
        ("a2f3aa27", Method.B1_NAIVE_WHOLE_UPDATE_ASYNC),
        ("a2f3aa27", Method.B0_NATIVE_SERIAL),
    )
    plan = build_formal_plan("sfwb-v1-2-dev-20260821-001")
    assert [(row.history_id, row.method) for row in plan] == list(FORMAL_ORDER)
    assert len({row.namespace for row in plan}) == 8
    assert len({row.cache_salt for row in plan}) == 8
    assert all(1 <= len(row.cache_salt) <= 64 for row in plan)
    assert all(row.attempt_ordinal == 1 for row in plan)


def test_namespace_and_salt_change_for_replacement_attempt() -> None:
    run_id = "sfwb-v1-2-dev-20260821-001"
    namespace_1 = derive_namespace(
        run_id, Method.B0_NATIVE_SERIAL, "07741c45", attempt_ordinal=1
    )
    namespace_2 = derive_namespace(
        run_id, Method.B0_NATIVE_SERIAL, "07741c45", attempt_ordinal=2
    )
    salt_1 = derive_cache_salt(run_id, "block-001", attempt_ordinal=1)
    salt_2 = derive_cache_salt(run_id, "block-001", attempt_ordinal=2)
    assert namespace_1 != namespace_2
    assert salt_1 != salt_2
    assert "B0_NATIVE_SERIAL" in namespace_1
    assert "07741c45" in namespace_1


def _passing_preflight() -> PreflightEvidence:
    return PreflightEvidence(
        tests_all_green=True,
        repository_identity_verified=True,
        data_identity_verified=True,
        provider_identity_verified=True,
        qa_identity_verified=True,
        historical_resource_match=True,
        live_resource_envelope_verified=True,
        construction_canary_passed=True,
        embedding_canary_passed=True,
        neo4j_canary_passed=True,
        construction_cache_salt_passed=True,
        embedding_cache_salt_passed=True,
        warmup_manifest_verified=True,
        construction_idle_samples=(True, True),
        embedding_idle=True,
        neo4j_idle=True,
        no_other_clients=True,
        sampler=SamplerQualification(
            duration_s=60.2,
            expected_samples=61,
            actual_samples=61,
            coverage=1.0,
            gap_p95_s=1.02,
            gap_max_s=1.09,
        ),
    )


def test_preflight_accepts_only_complete_live_evidence() -> None:
    result = validate_preflight(_passing_preflight())
    assert result["status"] == "PASS"
    assert result["formal_run_authorized"] is True


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("historical_resource_match", "HISTORICAL_RESOURCE_MISMATCH"),
        ("live_resource_envelope_verified", "LIVE_RESOURCE_ENVELOPE_UNVERIFIED"),
        ("construction_cache_salt_passed", "CONSTRUCTION_CACHE_SALT_UNQUALIFIED"),
        ("no_other_clients", "OTHER_CLIENT_CONTAMINATION"),
    ],
)
def test_preflight_fails_closed_on_core_gate(field: str, code: str) -> None:
    values = _passing_preflight().__dict__ | {field: False}
    with pytest.raises(PreflightError, match=code):
        validate_preflight(PreflightEvidence(**values))


def test_preflight_fails_sampler_coverage() -> None:
    evidence = _passing_preflight()
    values = evidence.__dict__ | {
        "sampler": SamplerQualification(
            duration_s=60.0,
            expected_samples=61,
            actual_samples=40,
            coverage=40 / 61,
            gap_p95_s=2.1,
            gap_max_s=8.0,
        )
    }
    with pytest.raises(PreflightError, match="SAMPLER_QUALIFICATION_FAILED"):
        validate_preflight(PreflightEvidence(**values))
