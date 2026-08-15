"""Offline tests for the bounded, read-only S4 live preflight."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from paper_eval.artifacts import sha256_file
from paper_eval.s4_preflight import (
    CAPTURE_NAMESPACE,
    HISTORICAL_S1_NAMESPACE,
    REPLAY_NAMESPACE,
    collect_s4_preflight,
    evaluate_s4_preflight,
    finalize_s4_preflight,
    parse_single_model_card,
    verify_s4_preflight,
)


def _empty() -> dict:
    return {"node_count": 0, "relationship_count": 0, "episode_names": []}


def _s1_state() -> dict:
    return {
        "node_count": 294,
        "relationship_count": 438,
        "episode_names": [f"07741c45::episode::{index:04d}" for index in range(49)],
    }


def _observations() -> dict:
    return {
        "construction": {
            "served_model_id": "qwen3-32b-fp8",
            "vllm_version": "0.26.0",
            "max_model_len": 65536,
        },
        "embedding": {"served_model_id": "qwen3-embedding-0.6b"},
        "neo4j_connectivity": True,
        "namespace_states": {
            CAPTURE_NAMESPACE: _empty(),
            REPLAY_NAMESPACE: _empty(),
            HISTORICAL_S1_NAMESPACE: _s1_state(),
        },
    }


def test_preflight_passes_only_the_frozen_read_only_identity() -> None:
    result = evaluate_s4_preflight(
        observations=_observations(),
        expected_historical_s1_state=_s1_state(),
    )

    assert result["verdict"] == "PASS"
    assert result["failures"] == []
    assert result["construction"] == {
        "served_model_id": "qwen3-32b-fp8",
        "vllm_version": "0.26.0",
        "max_model_len": 65536,
    }
    assert result["embedding"] == {
        "served_model_id": "qwen3-embedding-0.6b"
    }
    assert result["namespace_checks"] == {
        "capture_empty": True,
        "replay_empty": True,
        "historical_s1_unchanged": True,
    }
    assert result["authority"] == {
        "s4_authority_creation_authorized": True,
        "s4_live_execution_authorized": False,
        "pilot_execution_authorized": False,
    }
    assert "episode_names" not in str(result)


@pytest.mark.parametrize(
    ("path", "value", "failure"),
    [
        (("construction", "served_model_id"), "wrong", "construction_model"),
        (("construction", "vllm_version"), "0.25.0", "vllm_version"),
        (("construction", "max_model_len"), 40960, "max_model_len"),
        (("embedding", "served_model_id"), "wrong", "embedding_model"),
        (("neo4j_connectivity",), False, "neo4j_connectivity"),
    ],
)
def test_preflight_fails_closed_on_runtime_identity_drift(
    path: tuple[str, ...], value: object, failure: str
) -> None:
    observations = _observations()
    target = observations
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    result = evaluate_s4_preflight(
        observations=observations,
        expected_historical_s1_state=_s1_state(),
    )

    assert result["verdict"] == "FAIL"
    assert failure in result["failures"]
    assert result["authority"]["s4_authority_creation_authorized"] is False


def test_preflight_rejects_new_namespace_contamination_and_s1_drift() -> None:
    observations = _observations()
    observations["namespace_states"][CAPTURE_NAMESPACE]["node_count"] = 1
    observations["namespace_states"][HISTORICAL_S1_NAMESPACE][
        "relationship_count"
    ] += 1

    result = evaluate_s4_preflight(
        observations=observations,
        expected_historical_s1_state=_s1_state(),
    )

    assert result["verdict"] == "FAIL"
    assert result["failures"] == [
        "capture_namespace_not_empty",
        "historical_s1_namespace_drift",
    ]


@pytest.mark.asyncio
async def test_collector_uses_only_models_version_and_read_only_neo4j_probes() -> None:
    calls: list[tuple] = []

    async def get_json(base_url: str, path: str) -> dict:
        calls.append(("http", base_url, path))
        if path == "/version":
            return {"version": "0.26.0"}
        if base_url.endswith(":8000/v1/"):
            return {
                "data": [
                    {"id": "qwen3-32b-fp8", "max_model_len": 65536}
                ]
            }
        return {"data": [{"id": "qwen3-embedding-0.6b"}]}

    async def neo4j_connectivity() -> bool:
        calls.append(("neo4j_connectivity",))
        return True

    async def namespace_state(namespace: str) -> dict:
        calls.append(("namespace", namespace))
        return _s1_state() if namespace == HISTORICAL_S1_NAMESPACE else _empty()

    result = await collect_s4_preflight(
        get_json=get_json,
        neo4j_connectivity=neo4j_connectivity,
        namespace_state=namespace_state,
        expected_historical_s1_state=_s1_state(),
    )

    assert result["verdict"] == "PASS"
    assert calls == [
        ("http", "http://10.87.5.247:8000/v1/", "/models"),
        ("http", "http://10.87.5.247:8000", "/version"),
        ("http", "http://10.87.5.247:8001/v1", "/models"),
        ("neo4j_connectivity",),
        ("namespace", CAPTURE_NAMESPACE),
        ("namespace", REPLAY_NAMESPACE),
        ("namespace", HISTORICAL_S1_NAMESPACE),
    ]
    assert not any("chat" in str(call).lower() for call in calls)


@pytest.mark.asyncio
async def test_retry_preflight_uses_new_attempt_namespaces() -> None:
    capture = "pev3-s4-u0-capture-20260814-004"
    replay = "pev3-s4-d0-replay-20260814-004"
    namespaces: list[str] = []

    async def get_json(base_url: str, path: str) -> dict:
        if path == "/version":
            return {"version": "0.26.0"}
        if ":8000" in base_url:
            return {"data": [{"id": "qwen3-32b-fp8", "max_model_len": 65536}]}
        return {"data": [{"id": "qwen3-embedding-0.6b"}]}

    async def namespace_state(namespace: str) -> dict:
        namespaces.append(namespace)
        return _s1_state() if namespace == HISTORICAL_S1_NAMESPACE else _empty()

    result = await collect_s4_preflight(
        get_json=get_json,
        neo4j_connectivity=lambda: True,
        namespace_state=namespace_state,
        expected_historical_s1_state=_s1_state(),
        capture_namespace=capture,
        replay_namespace=replay,
    )

    assert result["verdict"] == "PASS"
    assert namespaces == [capture, replay, HISTORICAL_S1_NAMESPACE]


def test_preflight_rejects_missing_duplicate_or_malformed_model_cards() -> None:
    for bad in (None, {}, [], {"data": []}, {"data": [{"id": "x"}, {"id": "y"}]}):
        with pytest.raises(ValueError):
            parse_single_model_card(bad)


def test_preflight_artifact_is_hash_bound_sanitized_and_exclusive(
    tmp_path: Path,
) -> None:
    output = tmp_path / "S4_PREFLIGHT.json"
    result = finalize_s4_preflight(
        output_path=output,
        evaluation=evaluate_s4_preflight(
            observations=_observations(),
            expected_historical_s1_state=_s1_state(),
        ),
        s4_contract_file_sha256="1" * 64,
        s4_contract_sha256="2" * 64,
        s1_checkpoint_file_sha256="3" * 64,
        source_sha256={
            "preflight": "4" * 64,
            "production": "5" * 64,
            "test": "6" * 64,
        },
        git_commit="deadbeef",
        run_id="s4-preflight-20260814-001",
    )

    assert verify_s4_preflight(result) == result
    assert sha256_file(output) != "missing"
    assert result["payload"]["s4_contract_sha256"] == "2" * 64
    assert result["payload"]["s1_checkpoint_file_sha256"] == "3" * 64
    assert result["payload"]["authority"] == {
        "s4_authority_creation_authorized": True,
        "s4_live_execution_authorized": False,
        "pilot_execution_authorized": False,
    }
    serialized = output.read_text(encoding="utf-8").lower()
    assert "episode_names" not in serialized
    assert "api_key" not in serialized
    with pytest.raises(FileExistsError):
        finalize_s4_preflight(
            output_path=output,
            evaluation=result["payload"]["evaluation"],
            s4_contract_file_sha256="1" * 64,
            s4_contract_sha256="2" * 64,
            s1_checkpoint_file_sha256="3" * 64,
            source_sha256={
                "preflight": "4" * 64,
                "production": "5" * 64,
                "test": "6" * 64,
            },
            git_commit="deadbeef",
            run_id="s4-preflight-20260814-001",
        )


def test_preflight_verifier_rejects_hash_authority_and_shape_tamper(
    tmp_path: Path,
) -> None:
    result = finalize_s4_preflight(
        output_path=tmp_path / "preflight.json",
        evaluation=evaluate_s4_preflight(
            observations=_observations(),
            expected_historical_s1_state=_s1_state(),
        ),
        s4_contract_file_sha256="1" * 64,
        s4_contract_sha256="2" * 64,
        s1_checkpoint_file_sha256="3" * 64,
        source_sha256={
            "preflight": "4" * 64,
            "production": "5" * 64,
            "test": "6" * 64,
        },
        git_commit="deadbeef",
        run_id="s4-preflight-20260814-001",
    )

    for mutate in (
        lambda value: value["payload"].update(verdict="FAIL"),
        lambda value: value["payload"]["authority"].update(
            s4_live_execution_authorized=True
        ),
        lambda value: value.update(extra="drift"),
    ):
        altered = copy.deepcopy(result)
        mutate(altered)
        with pytest.raises(ValueError):
            verify_s4_preflight(altered)
