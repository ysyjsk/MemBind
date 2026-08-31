from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.graphiti_observer import (
    BackendProjection,
    BuildStageBindings,
    GraphitiCaptureInstallation,
    GraphitiObserverError,
    RequestObservationClient,
    build_projection_delta,
    build_semantic_cost_dag,
    build_to_seam_async,
    exact_cosine_domain,
    load_backend_projection_async,
    observe_node_similarity_async,
    observer_capture_scope,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.observer_campaign import (
    ObserverAttemptJournal,
    ObserverArtifactError,
    classify_observer_failure,
    load_protocol_freeze,
    materialize_r3_artifacts,
    native_episode_kwargs,
    run_real_observer_campaign_async,
    run_observer_block_async,
    verify_observer_harness_sources,
    verify_observer_manifest,
    write_observer_artifacts,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.gates import evaluate_opportunity_gates
from saturated_fixed_work_baseline_v1_3.membind_v7.characterization import (
    CharacterizationError,
    analyze_build_pair,
    audit_r1_assumptions,
    characterize_r3_blocks,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.opportunity import DagNode, counterfactual
from saturated_fixed_work_baseline_v1_3.membind_v7.state_delta import StateDelta
from saturated_fixed_work_baseline_v1_3.membind_v7.terminal import (
    seal_system_blocked_terminal,
    validate_blocked_attempt_chain,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.provider_diagnostics import (
    build_structured_extraction_probe,
    run_structured_extraction_probe_async,
)


def _projection(version: int, nodes: dict[str, dict]) -> BackendProjection:
    return BackendProjection(
        version=version,
        backend_epoch="neo4j-schema-1",
        namespace="v7-test",
        nodes=nodes,
        edges={},
        episodes={},
    )


@dataclass(frozen=True)
class _ProbeEpisode:
    context_id: str
    source_sequence: int
    episode_id: str
    reference_time: str
    body: str


class _ProbeCompletions:
    def __init__(self, *, content: str | None = None, error: BaseException | None = None):
        self.content = content
        self.error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=self.content),
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=123,
                completion_tokens=17,
                total_tokens=140,
            ),
        )


def _probe_episode(sequence: int = 0) -> _ProbeEpisode:
    return _ProbeEpisode(
        context_id="context-0",
        source_sequence=sequence,
        episode_id=f"episode-{sequence}",
        reference_time="2023/01/01 (Sun) 00:00",
        body="[USER]\nAlice met Bob in Paris.\n[ASSISTANT]\nThey discussed MemBind.",
    )


def test_structured_extraction_probe_reconstructs_exact_safe_wire_envelope() -> None:
    probe = build_structured_extraction_probe(
        episode=_probe_episode(),
        previous_episodes=(),
        namespace="v7-probe-fresh",
        model="Qwen/Qwen3-32B",
        max_tokens=4096,
    )

    assert probe.request["model"] == "Qwen/Qwen3-32B"
    assert probe.request["max_tokens"] == 4096
    assert probe.request["top_p"] == 1.0
    assert probe.request["extra_body"] == {"enable_thinking": False}
    assert probe.request["response_format"]["type"] == "json_schema"
    assert probe.evidence["source_sequence"] == 0
    assert probe.evidence["previous_episode_count"] == 0
    encoded = json.dumps(probe.evidence, sort_keys=True)
    assert "Alice met Bob" not in encoded
    assert "Respond with a JSON object" not in probe.request["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_structured_extraction_probe_records_one_sanitized_success() -> None:
    probe = build_structured_extraction_probe(
        episode=_probe_episode(),
        previous_episodes=(),
        namespace="v7-probe-fresh",
        model="Qwen/Qwen3-32B",
        max_tokens=4096,
    )
    completions = _ProbeCompletions(
        content=json.dumps(
            {
                "extracted_entities": [
                    {"name": "Alice", "entity_type_id": 0, "episode_indices": [0]}
                ]
            }
        )
    )

    result = await run_structured_extraction_probe_async(
        probe,
        completions=completions,
        timeout_seconds=30,
    )

    assert result["status"] == "PASS"
    assert result["http_attempt_count"] == 1
    assert result["parsed_entity_count"] == 1
    assert result["usage"]["prompt_tokens"] == 123
    assert len(completions.calls) == 1
    encoded = json.dumps(result, sort_keys=True)
    assert "Alice met Bob" not in encoded
    assert "extracted_entities" not in encoded


@pytest.mark.asyncio
async def test_structured_extraction_probe_sanitizes_timeout_without_retry() -> None:
    probe = build_structured_extraction_probe(
        episode=_probe_episode(),
        previous_episodes=(),
        namespace="v7-probe-fresh",
        model="Qwen/Qwen3-32B",
        max_tokens=4096,
    )
    completions = _ProbeCompletions(error=TimeoutError("secret request body"))

    result = await run_structured_extraction_probe_async(
        probe,
        completions=completions,
        timeout_seconds=30,
    )

    assert result["status"] == "FAIL"
    assert result["classification"] == "STRUCTURED_EXTRACTION_TIMEOUT"
    assert result["http_attempt_count"] == 1
    assert result["error_type"] == "builtins.TimeoutError"
    encoded = json.dumps(result, sort_keys=True)
    assert "secret request body" not in encoded


def test_reauthorized_protocol_requires_explicit_bounded_transport(tmp_path: Path) -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "v7/R1_R3_PROTOCOL_FREEZE.json"
    )
    protocol = json.loads(source.read_text(encoding="utf-8"))
    protocol["schema_version"] = "membind.v7.r1-r3-protocol-freeze.v2"
    protocol["provider"].update(
        {
            "requested_max_tokens": 8_192,
            "http_timeout_seconds": 900.0,
            "sdk_max_retries": 0,
            "hard_attempt_limit_per_request": 1,
            "structured_output_mode": "json_schema",
        }
    )
    protocol["infrastructure_reauthorization"] = {
        "previous_terminal_state": "V7_THEORY_OR_SYSTEM_BLOCKED",
        "previous_protocol_sha256": "a" * 64,
        "previous_terminal_manifest_sha256": "b" * 64,
        "changed_fields": ["provider.requested_max_tokens"],
        "diagnostic_probe_sha256": ["c" * 64, "d" * 64],
        "diagnostic_probe_status": ["PASS", "PASS"],
    }
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol), encoding="utf-8")

    loaded = load_protocol_freeze(path)

    assert loaded["provider"]["requested_max_tokens"] == 8_192
    assert loaded["provider"]["sdk_max_retries"] == 0
    assert loaded["infrastructure_reauthorization"]["changed_fields"] == [
        "provider.requested_max_tokens"
    ]

    del protocol["provider"]["hard_attempt_limit_per_request"]
    path.write_text(json.dumps(protocol), encoding="utf-8")
    with pytest.raises(ObserverArtifactError, match="transport"):
        load_protocol_freeze(path)


def test_v3_protocol_binds_observer_harness_sources_and_detects_drift(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "v7/R1_R3_PROTOCOL_FREEZE_V2.json"
    protocol = json.loads(source.read_text(encoding="utf-8"))
    protocol["schema_version"] = "membind.v7.r1-r3-protocol-freeze.v3"
    protocol["infrastructure_reauthorization"]["changed_fields"] = [
        "observer_harness.source_sha256"
    ]
    target = tmp_path / "observer.py"
    target.write_text("frozen observer\n", encoding="ascii")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    protocol["observer_harness"] = {
        "schema_version": "membind.v7.observer-harness-freeze.v1",
        "source_sha256": {"observer.py": digest},
    }
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(protocol), encoding="utf-8")

    loaded = load_protocol_freeze(protocol_path)
    verification = verify_observer_harness_sources(tmp_path, loaded)
    assert verification["status"] == "PASS"
    assert verification["source_sha256"] == {"observer.py": digest}

    target.write_text("drifted observer\n", encoding="ascii")
    with pytest.raises(ObserverArtifactError, match="source hash"):
        verify_observer_harness_sources(tmp_path, loaded)


def test_v4_token_limit_reauthorization_requires_exact_length_evidence(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "v7/R1_R3_PROTOCOL_FREEZE_V3.json"
    protocol = json.loads(source.read_text(encoding="utf-8"))
    protocol["schema_version"] = "membind.v7.r1-r3-protocol-freeze.v4"
    protocol["provider"]["requested_max_tokens"] = 16_384
    protocol["infrastructure_reauthorization"].update(
        {
            "previous_protocol_sha256": "a" * 64,
            "changed_fields": [
                "provider.requested_max_tokens",
                "observer_harness.source_sha256",
            ],
            "invalid_attempt": {
                "run_id": "v7-real-observer-v3-test",
                "attempt_journal_sha256": "b" * 64,
                "failure_artifact_sha256": "c" * 64,
                "completed_block_count": 1,
                "failure_class": "INFRASTRUCTURE_PROVIDER_STRUCTURED_OUTPUT_INVALID",
                "error_type": "json.decoder.JSONDecodeError",
                "finish_reason": "length",
                "completion_tokens": 8_192,
                "previous_requested_max_tokens": 8_192,
                "gate_outcome": "NOT_EVALUATED",
                "treatment_calls": 0,
                "response_replay_calls": 0,
            },
        }
    )
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol), encoding="utf-8")

    loaded = load_protocol_freeze(path)

    assert loaded["provider"]["requested_max_tokens"] == 16_384

    invalid = protocol["infrastructure_reauthorization"]["invalid_attempt"]
    invalid["finish_reason"] = "stop"
    path.write_text(json.dumps(protocol), encoding="utf-8")
    with pytest.raises(ObserverArtifactError, match="length evidence"):
        load_protocol_freeze(path)

    invalid["finish_reason"] = "length"
    invalid["completion_tokens"] = 8_191
    path.write_text(json.dumps(protocol), encoding="utf-8")
    with pytest.raises(ObserverArtifactError, match="length evidence"):
        load_protocol_freeze(path)


def test_v5_provider_cap_probe_requires_prior_16384_request_evidence(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "v7/R1_R3_PROTOCOL_FREEZE_V4.json"
    protocol = json.loads(source.read_text(encoding="utf-8"))
    protocol["schema_version"] = "membind.v7.r1-r3-protocol-freeze.v5"
    protocol["provider"]["requested_max_tokens"] = 32_768
    protocol["infrastructure_reauthorization"]["previous_protocol_sha256"] = "a" * 64
    protocol["infrastructure_reauthorization"]["changed_fields"] = [
        "provider.requested_max_tokens",
        "observer_harness.source_sha256",
    ]
    protocol["infrastructure_reauthorization"]["invalid_attempt"] = {
        "run_id": "v7-real-observer-v4-test",
        "attempt_journal_sha256": "b" * 64,
        "failure_artifact_sha256": "c" * 64,
        "completed_block_count": 0,
        "failure_class": "INFRASTRUCTURE_PROVIDER_STRUCTURED_OUTPUT_INVALID",
        "error_type": "json.decoder.JSONDecodeError",
        "finish_reason": "length",
        "completion_tokens": 8_192,
        "previous_requested_max_tokens": 16_384,
        "observed_provider_cap_tokens": 8_192,
        "gate_outcome": "NOT_EVALUATED",
        "treatment_calls": 0,
        "response_replay_calls": 0,
    }
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(protocol), encoding="utf-8")

    loaded = load_protocol_freeze(path)

    assert loaded["provider"]["requested_max_tokens"] == 32_768

    protocol["infrastructure_reauthorization"]["invalid_attempt"][
        "observed_provider_cap_tokens"
    ] = 8_191
    path.write_text(json.dumps(protocol), encoding="utf-8")
    with pytest.raises(ObserverArtifactError, match="length evidence"):
        load_protocol_freeze(path)


def test_backend_projection_delta_has_exact_insert_update_delete_images() -> None:
    before = _projection(
        3,
        {
            "deleted": {"name": "gone", "name_embedding": [0.0, 1.0], "group_id": "v7-test"},
            "updated": {"name": "old", "name_embedding": [1.0, 0.0], "group_id": "v7-test"},
        },
    )
    after = _projection(
        4,
        {
            "inserted": {"name": "new", "name_embedding": [0.5, 0.5], "group_id": "v7-test"},
            "updated": {"name": "changed", "name_embedding": [1.0, 0.0], "group_id": "v7-test"},
        },
    )

    delta = build_projection_delta(before, after)

    assert [(change.key, change.operation) for change in delta.changes] == [
        ("deleted", "delete"),
        ("inserted", "insert"),
        ("updated", "update"),
    ]
    by_key = {change.key: change for change in delta.changes}
    assert by_key["deleted"].before["name"] == "gone"
    assert by_key["deleted"].after == {}
    assert by_key["inserted"].before == {}
    assert by_key["inserted"].after["name"] == "new"
    assert by_key["updated"].changed_fields == frozenset({"name"})


def test_projection_delta_rejects_namespace_or_epoch_drift() -> None:
    before = _projection(1, {})
    after = BackendProjection(2, "neo4j-schema-2", "other", {}, {}, {})

    with pytest.raises(GraphitiObserverError, match="namespace"):
        build_projection_delta(before, after)


def test_exact_cosine_domain_keeps_full_domain_and_marks_boundary_ties() -> None:
    result = exact_cosine_domain(
        query=[1.0, 0.0],
        domain={
            "a": [1.0, 0.0],
            "b": [0.8, 0.6],
            "c": [0.8, -0.6],
            "d": [0.0, 1.0],
        },
        limit=2,
        min_score=0.6,
    )

    assert [row["uuid"] for row in result["domain"]] == ["a", "b", "c", "d"]
    assert result["result"] == ["a", "b"]
    assert result["cutoff"] == pytest.approx(0.8)
    assert result["boundary_ties"] == ["c"]
    assert result["tie_contract"] == "UNKNOWN"


def test_exact_cosine_domain_rejects_missing_or_wrong_dimension_embeddings() -> None:
    with pytest.raises(GraphitiObserverError, match="embedding"):
        exact_cosine_domain(query=[1.0, 0.0], domain={"a": None}, limit=2, min_score=0.6)
    with pytest.raises(GraphitiObserverError, match="dimension"):
        exact_cosine_domain(query=[1.0, 0.0], domain={"a": [1.0]}, limit=2, min_score=0.6)


@dataclass
class _Item:
    uuid: str
    name_embedding: list[float] | None = None
    fact_embedding: list[float] | None = None


@pytest.mark.asyncio
async def test_build_to_seam_runs_native_build_order_but_never_publishes() -> None:
    calls: list[str] = []
    episode = _Item("episode")
    extracted = [_Item("extracted")]
    resolved = [_Item("resolved", name_embedding=[1.0, 0.0])]
    edge = _Item("edge", fact_embedding=[1.0, 0.0])

    async def retrieve(_graphiti, _episode_kwargs):
        calls.append("previous")
        return [_Item("previous")]

    def make_episode(_graphiti, _episode_kwargs, _now):
        calls.append("episode")
        return episode

    async def extract(_graphiti, observed_episode, previous, _episode_kwargs):
        assert observed_episode is episode and [item.uuid for item in previous] == ["previous"]
        calls.append("extract_nodes")
        return extracted, {"resolved": [0]}

    async def resolve(_graphiti, observed, observed_episode, previous, _episode_kwargs):
        assert observed is extracted and observed_episode is episode and previous
        calls.append("resolve_nodes")
        return resolved, {"extracted": "resolved"}, []

    async def edges(_graphiti, observed_episode, observed_extracted, previous, observed_nodes, uuid_map, _episode_kwargs):
        assert observed_episode is episode and observed_extracted is extracted and previous
        assert observed_nodes is resolved and uuid_map == {"extracted": "resolved"}
        calls.append("resolve_edges")
        return [edge], [], [edge]

    async def attributes(_graphiti, observed_nodes, observed_episode, previous, new_edges, _episode_kwargs):
        assert observed_nodes is resolved and observed_episode is episode and previous and new_edges == [edge]
        calls.append("attributes")
        return observed_nodes

    bindings = BuildStageBindings(
        now=lambda: "2026-08-25T00:00:00+00:00",
        retrieve_previous=retrieve,
        make_episode=make_episode,
        extract_nodes=extract,
        resolve_nodes=resolve,
        extract_resolve_edges=edges,
        extract_attributes=attributes,
        continuation_k=lambda **kwargs: {"seam": "guarded", **kwargs},
    )

    result = await build_to_seam_async(
        object(),
        {"group_id": "v7-test", "update_communities": False, "saga": None},
        publication_frontier=3,
        backend_epoch="neo4j-schema-1",
        bindings=bindings,
    )

    assert calls == ["previous", "episode", "extract_nodes", "resolve_nodes", "resolve_edges", "attributes"]
    assert result.publication_calls == 0
    assert result.continuation_k["publication_frontier"] == 3
    assert result.continuation_k["nodes"] is resolved
    assert result.continuation_k["entity_edges"] == [edge]


@pytest.mark.asyncio
async def test_build_to_seam_fails_closed_when_continuation_embeddings_are_missing() -> None:
    node = _Item("node", name_embedding=None)
    edge = _Item("edge", fact_embedding=[1.0])

    async def retrieve(*_args):
        return []

    async def extract(*_args):
        return [node], {"node": [0]}

    async def resolve(*_args):
        return [node], {"node": "node"}, []

    async def edges(*_args):
        return [edge], [], [edge]

    async def attributes(*_args):
        return [node]

    bindings = BuildStageBindings(
        now=lambda: "now",
        retrieve_previous=retrieve,
        make_episode=lambda *_args: _Item("episode"),
        extract_nodes=extract,
        resolve_nodes=resolve,
        extract_resolve_edges=edges,
        extract_attributes=attributes,
        continuation_k=lambda **kwargs: kwargs,
    )
    with pytest.raises(GraphitiObserverError, match="node embedding"):
        await build_to_seam_async(
            object(),
            {"group_id": "v7-test", "update_communities": False, "saga": None},
            publication_frontier=0,
            backend_epoch="epoch",
            bindings=bindings,
        )


def _decision_input(**overrides):
    value = {
        "schema_version": "membind.v7.r3-decision-input.v1",
        "real_graphiti_evidence": True,
        "independent_block_count": 2,
        "source_count_per_block": 6,
        "selected_operator": "node_cosine",
        "selected_seam": "graphiti.add_episode.pre_process_episode_data",
        "t6b_status": "SUPPORTED_WITH_GUARD",
        "core_assumptions_supported": True,
        "observer_harness_bound": True,
        "false_stable_count": 0,
        "false_unaffected_count": 0,
        "stable_prediction_count": 4,
        "early_memory_specific": True,
        "csp": 0.25,
        "csp_preregistered_min": 0.1,
        "sca_within_bound": True,
        "meaningful_reconvergence": True,
        "gross_saved_cp_lb_ns": 100,
        "certificate_cost_ub_ns": 10,
        "repair_cost_ub_ns": 10,
        "required_online_headroom_ns": 20,
        "m1_sufficient": True,
        "m2_extension_eligible": False,
        "replay_allowed": False,
        "sealed_manifest_sha256": "a" * 64,
    }
    value.update(overrides)
    return value


def test_gate_selects_only_m1_when_all_preregistered_conditions_hold() -> None:
    result = evaluate_opportunity_gates(_decision_input())
    assert result["authorized"] is True
    assert result["treatment_authorized"] is True
    assert result["selected_method"] == "M1"
    assert all(result["gates"].values())


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"real_graphiti_evidence": False}, "real Graphiti"),
        ({"independent_block_count": 1}, "independent"),
        ({"false_stable_count": 1}, "false STABLE"),
        ({"early_memory_specific": False}, "early"),
        ({"gross_saved_cp_lb_ns": 30}, "margin"),
        ({"sealed_manifest_sha256": None}, "manifest"),
        ({"observer_harness_bound": False}, "harness"),
    ],
)
def test_gate_fails_closed_to_null(override: dict, reason: str) -> None:
    result = evaluate_opportunity_gates(_decision_input(**override))
    assert result["authorized"] is False
    assert result["treatment_authorized"] is False
    assert result["selected_method"] == "NULL"
    assert reason.casefold() in " ".join(result["reasons"]).casefold()


def test_counterfactual_zeroes_removed_work_without_detaching_predecessors() -> None:
    nodes = (
        DagNode("input", (), 7.0),
        DagNode("read", ("input",), 5.0),
        DagNode("demand", ("read",), 3.0),
    )
    result = counterfactual(nodes, removed={"read"})
    assert result.baseline.cost == 15.0
    assert result.candidate.cost == 10.0
    assert result.candidate.path == ("input", "read", "demand")


class _Delegate:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_response(self, messages, **kwargs):
        self.calls += 1
        return {"answer": kwargs.get("prompt_name")}


class _FailingDelegate(_Delegate):
    async def generate_response(self, messages, **kwargs):
        self.calls += 1
        raise RuntimeError("provider failed")


class _SequencedDelegate(_Delegate):
    async def generate_response(self, messages, **kwargs):
        self.calls += 1
        return {"answer": f"provider-secret-{self.calls}"}


@pytest.mark.asyncio
async def test_request_observer_records_complete_digest_identity_without_raw_prompt() -> None:
    delegate = _Delegate()
    records: list[dict] = []
    client = RequestObservationClient(
        delegate,
        sink=records.append,
        model_epoch="qwen3-32b@siliconflow-v1",
    )
    messages = [{"role": "user", "content": "private episode body"}]
    with observer_capture_scope(phase="OLD", source_sequence=2, state_version=2):
        result = await client.generate_response(
            messages,
            prompt_name="extract_nodes.extract_message",
            response_model=dict,
            api_key="must-not-be-recorded",
        )

    assert result == {"answer": "extract_nodes.extract_message"}
    assert delegate.calls == 1
    assert len(records) == 1
    row = records[0]
    assert row["phase"] == "OLD" and row["source_sequence"] == 2
    assert row["ordinal"] == 0
    assert len(row["request_identity"]) == 64
    assert len(row["response_digest"]) == 64
    assert set(row["field_digests"]) >= {"messages", "response_model", "kwargs", "model_epoch"}
    encoded = json.dumps(row, sort_keys=True)
    assert "private episode body" not in encoded
    assert "must-not-be-recorded" not in encoded
    assert "answer" not in encoded


@pytest.mark.asyncio
async def test_request_observer_failed_request_has_no_response_digest() -> None:
    records: list[dict] = []
    client = RequestObservationClient(
        _FailingDelegate(),
        sink=records.append,
        model_epoch="qwen3-8b@local-v1",
    )
    with observer_capture_scope(phase="OLD", source_sequence=0, state_version=0):
        with pytest.raises(RuntimeError, match="provider failed"):
            await client.generate_response([], prompt_name="dedupe_nodes.nodes")

    assert len(records) == 1
    assert records[0]["status"] == "FAILED"
    assert records[0]["response_digest"] is None


@pytest.mark.asyncio
async def test_request_observer_single_call_branch_oracle_replays_old_logical_response() -> None:
    records: list[dict] = []
    delegate = _SequencedDelegate()
    client = RequestObservationClient(
        delegate,
        sink=records.append,
        model_epoch="qwen3-8b@local-v1",
        single_call_branch_oracle=True,
    )
    messages = [{"role": "user", "content": "same logical request"}]
    with observer_capture_scope(phase="OLD", source_sequence=3, state_version=2):
        old = await client.generate_response(messages, prompt_name="dedupe_nodes.nodes")
    with observer_capture_scope(phase="FRESH_NATIVE", source_sequence=3, state_version=3):
        fresh = await client.generate_response(messages, prompt_name="dedupe_nodes.nodes")

    assert delegate.calls == 2  # second transport is retained for baseline timing
    assert old == fresh == {"answer": "provider-secret-1"}
    assert records[0]["response_binding"] == "PROVIDER_SINGLE_CALL"
    assert records[1]["response_binding"] == "OLD_SINGLE_CALL_REPLAY"
    assert records[1]["response_digest"] == records[0]["response_digest"]
    assert records[1]["transport_response_digest"] != records[1]["response_digest"]
    encoded = json.dumps(records, sort_keys=True)
    assert "provider-secret" not in encoded


@pytest.mark.asyncio
async def test_request_observer_branch_oracle_does_not_replay_changed_request() -> None:
    records: list[dict] = []
    client = RequestObservationClient(
        _SequencedDelegate(),
        sink=records.append,
        model_epoch="qwen3-8b@local-v1",
        single_call_branch_oracle=True,
    )
    with observer_capture_scope(phase="OLD", source_sequence=3, state_version=2):
        await client.generate_response([{"role": "user", "content": "old"}], prompt_name="dedupe_nodes.nodes")
    with observer_capture_scope(phase="FRESH_NATIVE", source_sequence=3, state_version=3):
        fresh = await client.generate_response([{"role": "user", "content": "changed"}], prompt_name="dedupe_nodes.nodes")

    assert fresh == {"answer": "provider-secret-2"}
    assert records[1]["response_binding"] == "PROVIDER_SINGLE_CALL"


@pytest.mark.asyncio
async def test_request_observer_fails_closed_outside_capture_scope() -> None:
    delegate = _Delegate()
    client = RequestObservationClient(delegate, sink=lambda _row: None, model_epoch="epoch")
    with pytest.raises(GraphitiObserverError, match="scope"):
        await client.generate_response([], prompt_name="test")
    assert delegate.calls == 0


@pytest.mark.asyncio
async def test_observer_block_orders_shadow_before_predecessor_publication_and_pairs_deltas() -> None:
    events: list[str] = []
    state = {"version": 0, "nodes": {}}

    async def project(version: int) -> BackendProjection:
        assert version == state["version"]
        return _projection(version, dict(state["nodes"]))

    async def prepare(sequence: int, state_version: int) -> dict:
        events.append(f"prepare:{sequence}@{state_version}")
        return {"source_sequence": sequence, "state_version": state_version, "publication_calls": 0}

    async def publish(sequence: int, state_version: int) -> dict:
        events.append(f"publish:{sequence}@{state_version}")
        assert state_version == state["version"]
        state["nodes"][f"n{sequence}"] = {
            "name": f"node-{sequence}",
            "name_embedding": [1.0, 0.0],
            "group_id": "v7-test",
        }
        state["version"] += 1
        return {"source_sequence": sequence, "state_version": state_version, "publication_calls": 1}

    result = await run_observer_block_async(
        source_count=3,
        prepare=prepare,
        publish=publish,
        project=project,
    )

    assert events == [
        "prepare:1@0",
        "publish:0@0",
        "prepare:2@1",
        "publish:1@1",
        "publish:2@2",
    ]
    assert len(result["transitions"]) == 3
    assert len(result["pairs"]) == 2
    assert result["pairs"][0]["old_build"]["source_sequence"] == 1
    assert result["pairs"][0]["fresh_build"]["source_sequence"] == 1
    assert result["pairs"][0]["delta"].changes[0].operation == "insert"
    assert result["shadow_publication_calls"] == 0
    assert result["native_publication_calls"] == 3


@pytest.mark.asyncio
async def test_observer_block_rejects_shadow_publication() -> None:
    async def project(version: int) -> BackendProjection:
        return _projection(version, {})

    async def prepare(sequence: int, state_version: int) -> dict:
        return {"source_sequence": sequence, "state_version": state_version, "publication_calls": 1}

    async def publish(sequence: int, state_version: int) -> dict:
        return {"source_sequence": sequence, "state_version": state_version, "publication_calls": 1}

    with pytest.raises(ObserverArtifactError, match="shadow"):
        await run_observer_block_async(source_count=2, prepare=prepare, publish=publish, project=project)


def test_observer_artifact_manifest_is_fresh_hash_bound_and_tamper_evident(tmp_path: Path) -> None:
    root = tmp_path / "campaign"
    result = write_observer_artifacts(
        root,
        {
            "R1_ASSUMPTION_AUDIT.json": {"status": "PASS"},
            "R2_TWO_SOURCE_CAUSAL_TRACE.json": {"status": "PASS"},
            "R3_BLOCKS.json": [{"block": 1}, {"block": 2}],
        },
        campaign_identity={"run_id": "v7-test", "provider": "siliconflow"},
    )
    assert result["status"] == "SEALED"
    assert verify_observer_manifest(root)["status"] == "PASS"
    with pytest.raises(ObserverArtifactError, match="fresh"):
        write_observer_artifacts(root, {}, campaign_identity={})

    (root / "R1_ASSUMPTION_AUDIT.json").write_text('{"status":"tampered"}\n', encoding="ascii")
    with pytest.raises(ObserverArtifactError, match="digest"):
        verify_observer_manifest(root)


class _QueryResult:
    def __init__(self, records):
        self.records = records


class _ProjectionDriver:
    provider = "neo4j"
    _database = "v7-test"

    def __init__(self):
        self.queries: list[str] = []

    async def execute_query(self, query, **kwargs):
        self.queries.append(query)
        assert kwargs.get("params") == {"group_id": "v7-test"}
        if "RETURN 'node' AS kind" in query:
            return _QueryResult(
                [
                    {
                        "uuid": "n1",
                        "name": "one",
                        "group_id": "v7-test",
                        "name_embedding": [1.0, 0.0],
                        "summary": "summary",
                        "labels": ["Entity"],
                        "attributes": {"uuid": "n1", "custom": 1},
                    }
                ]
            )
        if "RETURN 'edge' AS kind" in query:
            return _QueryResult(
                [
                    {
                        "uuid": "e1",
                        "source_node_uuid": "n1",
                        "target_node_uuid": "n1",
                        "group_id": "v7-test",
                        "fact": "self",
                        "fact_embedding": [1.0, 0.0],
                        "attributes": {"uuid": "e1"},
                    }
                ]
            )
        if "RETURN 'episode' AS kind" in query:
            return _QueryResult([{"uuid": "ep1", "name": "episode", "group_id": "v7-test", "content": "body"}])
        raise AssertionError("unexpected query")


@pytest.mark.asyncio
async def test_backend_projection_reads_all_three_state_kinds_with_embeddings() -> None:
    driver = _ProjectionDriver()
    projection = await load_backend_projection_async(
        driver,
        namespace="v7-test",
        version=2,
        backend_epoch="neo4j-schema-1",
    )
    assert len(driver.queries) == 3
    assert projection.nodes["n1"]["name_embedding"] == [1.0, 0.0]
    assert projection.edges["e1"]["source_node_uuid"] == "n1"
    assert projection.episodes["ep1"]["content"] == "body"
    assert len(projection.digest) == 64


@dataclass
class _SearchNode:
    uuid: str


@pytest.mark.asyncio
async def test_node_similarity_observer_captures_complete_domain_and_preserves_native_return() -> None:
    rows: list[dict] = []
    expected = [_SearchNode("a"), _SearchNode("b")]

    async def native(*_args, **_kwargs):
        return expected

    async def domain_loader(_driver, group_ids):
        assert group_ids == ["v7-test"]
        return {"a": [1.0, 0.0], "b": [0.8, 0.6], "c": [0.0, 1.0]}

    with observer_capture_scope(phase="FRESH_NATIVE", source_sequence=1, state_version=1):
        result = await observe_node_similarity_async(
            native,
            object(),
            [1.0, 0.0],
            {},
            ["v7-test"],
            2,
            0.6,
            sink=rows.append,
            domain_loader=domain_loader,
            query_epoch="embedder-1",
            index_epoch="neo4j-exact-cosine-1",
            config_epoch="graphiti-021d3a57",
        )
    assert result is expected
    assert rows[0]["actual_result"] == ["a", "b"]
    assert [row["uuid"] for row in rows[0]["complete_domain"]] == ["a", "b", "c"]
    assert rows[0]["completeness_status"] == "COMPLETE"
    assert rows[0]["witness"]["query_epoch"] == "embedder-1"


@pytest.mark.asyncio
async def test_node_similarity_observer_never_repairs_native_order_mismatch() -> None:
    rows: list[dict] = []
    native_result = [_SearchNode("b"), _SearchNode("a")]

    async def native(*_args, **_kwargs):
        return native_result

    async def domain_loader(_driver, _group_ids):
        return {"a": [1.0, 0.0], "b": [0.8, 0.6]}

    with observer_capture_scope(phase="OLD", source_sequence=1, state_version=0):
        result = await observe_node_similarity_async(
            native,
            object(),
            [1.0, 0.0],
            {},
            ["v7-test"],
            2,
            0.6,
            sink=rows.append,
            domain_loader=domain_loader,
            query_epoch="embedder-1",
            index_epoch="index-1",
            config_epoch="config-1",
        )
    assert result is native_result
    assert rows[0]["reference_result"] == ["a", "b"]
    assert rows[0]["actual_result"] == ["b", "a"]
    assert rows[0]["completeness_status"] == "INCOMPLETE"


@dataclass
class _Provider:
    value: str = "neo4j"


class _CaptureDriver:
    provider = _Provider()
    _database = "v7-test"


class _CaptureGraphiti:
    def __init__(self):
        self.driver = _CaptureDriver()
        self.store_raw_episode_content = True
        self.llm_client = _Delegate()
        self.clients = type("Clients", (), {})()
        self.clients.llm_client = self.llm_client
        self.process_calls = 0

    async def retrieve_episodes(self, reference_time, last_n=10, group_ids=None, source=None, driver=None, saga=None):
        return [{"uuid": "previous", "content": "old", "valid_at": reference_time}]

    async def _process_episode_data(
        self,
        episode,
        nodes,
        entity_edges,
        now,
        group_id,
        saga=None,
        saga_previous_episode_uuid=None,
        node_episode_index_map=None,
    ):
        self.process_calls += 1
        return "native-result"


class _NodeModule:
    async def node_similarity_search(self, *_args, **_kwargs):
        return [_SearchNode("n1")]


@pytest.mark.asyncio
async def test_graphiti_capture_is_transparent_and_restores_all_patches() -> None:
    graphiti = _CaptureGraphiti()
    module = _NodeModule()
    original_retrieve = graphiti.retrieve_episodes
    original_process = graphiti._process_episode_data
    original_search = module.node_similarity_search
    capture = GraphitiCaptureInstallation(
        graphiti,
        model_epoch="qwen-epoch",
        query_epoch="embed-epoch",
        index_epoch="index-epoch",
        config_epoch="config-epoch",
        backend_epoch="backend-epoch",
        node_module=module,
        domain_loader=lambda _driver, _groups: {"n1": [1.0, 0.0]},
    )
    capture.install()
    episode_kwargs = {
        "group_id": "v7-test",
        "update_communities": False,
        "saga": None,
        "saga_previous_episode_uuid": None,
    }
    with capture.scope(
        phase="FRESH_NATIVE",
        source_sequence=1,
        state_version=1,
        episode_kwargs=episode_kwargs,
    ) as scope:
        previous = await graphiti.retrieve_episodes("2026-08-25", group_ids=["v7-test"])
        search_result = await module.node_similarity_search(
            graphiti.driver,
            [1.0, 0.0],
            {},
            ["v7-test"],
            1,
            0.6,
        )
        native_result = await graphiti._process_episode_data(
            {"uuid": "episode", "name": "ep", "group_id": "v7-test"},
            [{"uuid": "n1", "name_embedding": [1.0, 0.0]}],
            [
                {
                    "uuid": "e1",
                    "source_node_uuid": "n1",
                    "target_node_uuid": "n1",
                    "fact_embedding": [1.0, 0.0],
                }
            ],
            "2026-08-25T00:00:00+00:00",
            "v7-test",
            node_episode_index_map={"n1": [0]},
        )
    record = scope.to_record()
    assert previous[0]["uuid"] == "previous"
    assert search_result[0].uuid == "n1"
    assert native_result == "native-result"
    assert graphiti.process_calls == 1
    assert record["publication_calls"] == 1
    assert record["previous_episode"]["order"] == ["previous"]
    assert record["reads"][0]["actual_result"] == ["n1"]
    assert record["continuation"]["status"] == "SUPPORTED_WITH_GUARD"

    dependency_pairs = {
        (edge["source"], edge["target"])
        for edge in record["dependency_edges"]
    }
    assert {
        ("previous_episode", "node_extraction"),
        ("previous_episode", "node_resolution"),
        ("previous_episode", "edge_extraction"),
        ("previous_episode", "edge_resolution"),
        ("previous_episode", "attributes_summary"),
    } <= dependency_pairs

    capture.restore()
    assert graphiti.retrieve_episodes == original_retrieve
    assert graphiti._process_episode_data == original_process
    assert module.node_similarity_search == original_search
    assert graphiti.llm_client is capture.original_llm_client


def test_capture_installation_restore_is_idempotent() -> None:
    graphiti = _CaptureGraphiti()
    capture = GraphitiCaptureInstallation(
        graphiti,
        model_epoch="m",
        query_epoch="q",
        index_epoch="i",
        config_epoch="c",
        backend_epoch="b",
        node_module=_NodeModule(),
        domain_loader=lambda _driver, _groups: {},
    )
    capture.install()
    with pytest.raises(GraphitiObserverError, match="installed"):
        capture.install()
    capture.restore()
    capture.restore()


def _read(
    *,
    phase: str,
    result=("a", "b"),
    query=(1.0, 0.0),
    ties=(),
    duration=10,
):
    return {
        "phase": phase,
        "source_sequence": 1,
        "state_version": 0 if phase == "OLD" else 1,
        "operator": "node_cosine",
        "occurrence": 0,
        "query": list(query),
        "query_digest": "query",
        "filter": {},
        "filter_fingerprint": "filter",
        "group_ids": ["v7-test"],
        "limit": 2,
        "min_score": 0.6,
        "actual_result": list(result),
        "reference_result": list(result),
        "complete_domain": [
            {"uuid": "a", "embedding": [1.0, 0.0], "score": 1.0},
            {"uuid": "b", "embedding": [0.8, 0.6], "score": 0.8},
            {"uuid": "c", "embedding": [0.0, 1.0], "score": 0.0},
        ],
        "cutoff": 0.8,
        "boundary_ties": list(ties),
        "query_epoch": "embed-1",
        "index_epoch": "index-1",
        "config_epoch": "config-1",
        "completeness_status": "COMPLETE",
        "native_start_ns": 100,
        "native_end_ns": 100 + duration,
        "observer_start_ns": 90,
        "observer_end_ns": 100 + duration,
    }


def _build(phase: str, *, previous="same", request="same", result=("a", "b"), query=(1.0, 0.0)):
    return {
        "phase": phase,
        "source_sequence": 1,
        "state_version": 0 if phase == "OLD" else 1,
        "previous_episode": {"projection_digest": previous, "duration_ns": 5},
        "reads": [_read(phase=phase, result=result, query=query)],
        "requests": [
            {
                "prompt_name": "extract_nodes.extract_message",
                "ordinal": 0,
                "request_identity": request,
                "duration_ns": 50,
            }
        ],
        "continuation": {"status": "SUPPORTED_WITH_GUARD"},
        "continuation_k": {"seam": "k"},
        "duration_ns": 100,
        "publication_calls": 0 if phase == "OLD" else 1,
    }


def test_pair_analysis_uses_delta_score_bound_and_keeps_fresh_as_truth_only() -> None:
    delta = build_projection_delta(
        _projection(0, {"a": {"name_embedding": [1.0, 0.0]}, "b": {"name_embedding": [0.8, 0.6]}}),
        _projection(
            1,
            {
                "a": {"name_embedding": [1.0, 0.0]},
                "b": {"name_embedding": [0.8, 0.6]},
                "d": {"name_embedding": [0.0, 1.0]},
            },
        ),
    )
    analysis = analyze_build_pair(_build("OLD"), _build("FRESH_NATIVE"), delta)
    row = analysis["read_rows"][0]
    assert row["prediction"] == "STABLE"
    assert row["truth"] == "SAME"
    assert row["certificate_inputs"] == ["old_witness", "state_delta", "changed_node_post_scores"]
    assert analysis["demand_prediction"] == "STABLE"
    assert analysis["demand_truth"] == "SAME"
    assert analysis["false_stable_count"] == 0
    assert analysis["false_unaffected_count"] == 0


def test_previous_episode_change_blocks_early_demand_stable_even_when_request_repeats() -> None:
    delta = StateDelta(0, 1, ())
    old = _build("OLD", previous="old-window", request="same")
    fresh = _build("FRESH_NATIVE", previous="new-window", request="same")
    analysis = analyze_build_pair(old, fresh, delta)
    assert analysis["request_truth"] == "SAME"
    assert analysis["demand_prediction"] == "UNKNOWN"
    assert "previous_episode" in analysis["demand_reasons"]
    assert analysis["early_memory_specific"] is False


def test_query_drift_is_changed_truth_and_never_false_stable() -> None:
    analysis = analyze_build_pair(
        _build("OLD", query=(1.0, 0.0)),
        _build("FRESH_NATIVE", query=(0.0, 1.0), result=("c",)),
        StateDelta(0, 1, ()),
    )
    assert analysis["read_rows"][0]["prediction"] == "UNKNOWN"
    assert analysis["read_rows"][0]["truth"] == "CHANGED"
    assert analysis["false_stable_count"] == 0


def test_r3_characterization_aggregates_two_real_blocks_and_preserves_null_when_gate_b_fails() -> None:
    pair = {
        "source_sequence": 1,
        "old_build": _build("OLD", previous="old"),
        "fresh_build": _build("FRESH_NATIVE", previous="new"),
        "delta": StateDelta(0, 1, ()),
    }
    blocks = [
        {
            "real_graphiti_evidence": True,
            "source_count": 6,
            "block_id": "a",
            "pairs": [pair],
        },
        {
            "real_graphiti_evidence": True,
            "source_count": 6,
            "block_id": "b",
            "pairs": [pair],
        },
    ]
    result = characterize_r3_blocks(
        blocks,
        thresholds={
            "csp_min": 0.1,
            "sca_work_max": 4.0,
            "reconvergence_min": 0.25,
            "required_headroom_floor_ns": 20,
            "required_headroom_ratio": 0.1,
        },
    )
    assert result["decision_input"]["real_graphiti_evidence"] is True
    assert result["decision_input"]["independent_block_count"] == 2
    assert result["decision_input"]["early_memory_specific"] is False
    assert result["method_selection"]["selected_method"] == "NULL"
    assert result["method_selection"]["authorized"] is False


def test_r3_materializer_hash_binds_decision_to_evidence_manifest(tmp_path: Path) -> None:
    characterization = {
        "pair_analyses": [{"source_sequence": 1}],
        "certificate_confusion": {"STABLE/SAME": 1},
        "false_unaffected_count": 0,
        "csp": 0.2,
        "semantic_change_amplification": {"sca_work": 1.0},
        "reconvergence": {"mean_rate": 1.0},
        "critical_opportunity": {"gross_saved_cp_lb_ns": 100},
        "decision_input": _decision_input(csp_preregistered_min=0.1),
        "method_selection": {"status": "stale-before-seal"},
    }
    root = tmp_path / "r3"
    sealed = materialize_r3_artifacts(
        root,
        r1={"status": "PASS", "real_graphiti_evidence": True},
        r2={"status": "PASS", "real_graphiti_evidence": True},
        blocks=[{"block_id": "a"}, {"block_id": "b"}],
        characterization=characterization,
        campaign_identity={"run_id": "v7-real", "provider": "siliconflow"},
    )
    decision = json.loads((root / "R3_DECISION_INPUT.json").read_text(encoding="ascii"))
    evidence_digest = __import__("hashlib").sha256((root / "EVIDENCE_MANIFEST.json").read_bytes()).hexdigest()
    assert decision["sealed_manifest_sha256"] == evidence_digest
    method = json.loads((root / "METHOD_SELECTION.json").read_text(encoding="ascii"))
    assert method["selected_method"] == "M1"
    assert sealed["method_selection"]["authorized"] is True
    assert verify_observer_manifest(root)["evidence_manifest_sha256"] == evidence_digest


def test_provider_timeout_is_invalid_infrastructure_and_never_a_gate_result() -> None:
    class APITimeoutError(RuntimeError):
        pass

    try:
        try:
            raise APITimeoutError("request body and secret must not be retained")
        except APITimeoutError as inner:
            raise RuntimeError("outer wrapper") from inner
    except RuntimeError as error:
        result = classify_observer_failure(error)

    assert result == {
        "failure_class": "INFRASTRUCTURE_PROVIDER_TIMEOUT",
        "attempt_validity": "INVALID_FOR_R1_R3_GATES",
        "replacement_eligible": True,
        "gate_outcome": "NOT_EVALUATED",
        "selected_method": None,
    }
    assert "request body" not in json.dumps(result)


def test_structured_json_decode_failure_is_invalid_provider_output_not_scientific_null() -> None:
    error = json.JSONDecodeError("unterminated", "{\"value\": \"", 10)
    result = classify_observer_failure(error)
    assert result == {
        "failure_class": "INFRASTRUCTURE_PROVIDER_STRUCTURED_OUTPUT_INVALID",
        "attempt_validity": "INVALID_FOR_R1_R3_GATES",
        "replacement_eligible": True,
        "gate_outcome": "NOT_EVALUATED",
        "selected_method": None,
    }


def test_native_episode_kwargs_preserve_fresh_node_semantics() -> None:
    episode = {
        "context_id": "context-0",
        "source_sequence": 3,
        "episode_id": "stable-workload-id-is-not-a-graphiti-existing-node-id",
        "reference_time": "2026-08-25T00:00:00+00:00",
        "body": "body",
    }
    kwargs = native_episode_kwargs(episode, "fresh-v7-namespace")
    assert kwargs["name"] == "context-0::episode::0003"
    assert kwargs["uuid"] is None
    assert kwargs["group_id"] == "fresh-v7-namespace"


def test_observer_attempt_journal_is_append_only_sanitized_and_hash_bound(tmp_path: Path) -> None:
    path = tmp_path / ".v7-real-observer-005.attempt.jsonl"
    journal = ObserverAttemptJournal.create(
        path,
        run_id="v7-real-observer-005",
        protocol_sha256="a" * 64,
        output_root_name="v7-real-observer-005",
    )
    journal.record_progress(event="BLOCK_START", block_id="R1-R2", completed_block_count=0)
    journal.record_failure(
        failure={
            "failure_class": "INFRASTRUCTURE_PROVIDER_TIMEOUT",
            "attempt_validity": "INVALID_FOR_R1_R3_GATES",
            "replacement_eligible": True,
            "gate_outcome": "NOT_EVALUATED",
            "selected_method": None,
        },
        error_type="openai.APITimeoutError",
        error_message_sha256="b" * 64,
        completed_block_count=0,
    )
    journal.close()

    raw = path.read_text(encoding="ascii")
    rows = [json.loads(line) for line in raw.splitlines()]
    assert [row["event"] for row in rows] == [
        "ATTEMPT_START",
        "BLOCK_START",
        "ATTEMPT_FAILURE",
    ]
    assert rows[-1]["gate_outcome"] == "NOT_EVALUATED"
    assert rows[-1]["treatment_calls"] == 0
    assert rows[-1]["response_replay_calls"] == 0
    assert "api_key" not in raw.casefold()
    assert "secret" not in raw.casefold()
    assert journal.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        ObserverAttemptJournal.create(
            path,
            run_id="v7-real-observer-005",
            protocol_sha256="a" * 64,
            output_root_name="v7-real-observer-005",
        )


def test_observer_attempt_journal_persists_only_sanitized_provider_response_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".v7-provider-observation.attempt.jsonl"
    journal = ObserverAttemptJournal.create(
        path,
        run_id="v7-provider-observation",
        protocol_sha256="a" * 64,
        output_root_name="v7-provider-observation",
    )
    journal.record_provider_response(
        lane="r3-a",
        finish_reason="length",
        prompt_tokens=123,
        completion_tokens=8192,
        content_bytes=37289,
        content_sha256="b" * 64,
        phase="FRESH_NATIVE",
        source_sequence=2,
        request_ordinal=4,
        prompt_name="extract_nodes.extract_message",
    )
    journal.close()

    raw = path.read_text(encoding="ascii")
    row = json.loads(raw.splitlines()[-1])
    assert row["event"] == "PROVIDER_RESPONSE"
    assert row["finish_reason"] == "length"
    assert row["completion_tokens"] == 8192
    assert row["hard_attempt_count"] == 1
    assert row["phase"] == "FRESH_NATIVE"
    assert row["source_sequence"] == 2
    assert row["request_ordinal"] == 4
    assert row["prompt_name"] == "extract_nodes.extract_message"
    assert row["treatment_calls"] == row["response_replay_calls"] == 0
    assert "content" not in row
    assert "message" not in row


def test_r1_dependency_audit_requires_previous_episode_direct_consumer_closure() -> None:
    kinds_only = [
        {"source": "a", "target": "b", "kind": kind}
        for kind in (
            "data",
            "control",
            "existence",
            "ordered-collection",
            "environment/oracle",
            "effect/publication",
        )
    ]
    build = _build("OLD")
    build["dependency_edges"] = kinds_only
    build["continuation"] = {"status": "SUPPORTED_WITH_GUARD"}
    fresh = _build("FRESH_NATIVE")
    fresh["dependency_edges"] = kinds_only
    fresh["continuation"] = {"status": "SUPPORTED_WITH_GUARD"}
    delta = StateDelta(0, 1, ())
    block = {
        "real_graphiti_evidence": True,
        "transitions": [{"delta": delta}],
        "pairs": [{"old_build": build, "fresh_build": fresh}],
        "shadow_publication_calls": 0,
    }

    result = audit_r1_assumptions(block)
    assert result["dependency_edge_kinds_complete"] is True
    assert result["previous_episode_dependency_complete"] is False
    assert result["assumptions"]["A4"] == "UNKNOWN"
    assert result["core_assumptions_supported"] is False


def test_r3_counterfactual_fails_closed_without_complete_semantic_dag() -> None:
    pair = {
        "source_sequence": 1,
        "old_build": _build("OLD"),
        "fresh_build": _build("FRESH_NATIVE"),
        "delta": StateDelta(0, 1, ()),
    }
    blocks = [
        {
            "real_graphiti_evidence": True,
            "source_count": 6,
            "block_id": block_id,
            "pairs": [pair],
        }
        for block_id in ("a", "b")
    ]
    result = characterize_r3_blocks(
        blocks,
        thresholds={
            "csp_min": 0.0,
            "sca_work_max": 10.0,
            "reconvergence_min": 0.0,
            "required_headroom_floor_ns": 0,
            "required_headroom_ratio": 0.0,
        },
    )

    assert result["critical_opportunity"]["status"] == (
        "UNKNOWN_INCOMPLETE_SEMANTIC_DAG"
    )
    assert result["critical_opportunity"]["gross_saved_cp_lb_ns"] is None
    assert result["decision_input"]["gross_saved_cp_lb_ns"] is None
    assert result["method_selection"]["authorized"] is False
    assert result["method_selection"]["selected_method"] == "NULL"


def test_r3_complete_semantic_dag_recomputes_path_switch_instead_of_summing_reads() -> None:
    pair = {
        "source_sequence": 1,
        "old_build": _build("OLD"),
        "fresh_build": _build("FRESH_NATIVE"),
        "delta": StateDelta(0, 1, ()),
        "semantic_dag": {
            "schema_version": "membind.v7.semantic-cost-dag.v1",
            "status": "COMPLETE",
            "nodes": [
                {
                    "node_id": "input",
                    "predecessors": [],
                    "cost_ns": 5,
                    "state_dependent": True,
                },
                {
                    "node_id": "stable-read",
                    "predecessors": ["input"],
                    "cost_ns": 10,
                    "state_dependent": True,
                    "read_key": ["node_cosine", 0],
                },
                {
                    "node_id": "other-branch",
                    "predecessors": ["input"],
                    "cost_ns": 12,
                    "state_dependent": True,
                },
                {
                    "node_id": "join",
                    "predecessors": ["stable-read", "other-branch"],
                    "cost_ns": 1,
                    "state_dependent": True,
                },
            ],
            "cost_nodes": [],
        },
    }
    blocks = [
        {
            "real_graphiti_evidence": True,
            "source_count": 6,
            "block_id": block_id,
            "pairs": [pair],
        }
        for block_id in ("a", "b")
    ]
    result = characterize_r3_blocks(
        blocks,
        thresholds={
            "csp_min": 0.0,
            "sca_work_max": 10.0,
            "reconvergence_min": 0.0,
            "required_headroom_floor_ns": 0,
            "required_headroom_ratio": 0.0,
        },
    )

    assert result["critical_opportunity"]["status"] == "COMPLETE"
    assert result["critical_opportunity"]["gross_saved_cp_lb_ns"] == 0
    pair_metric = result["critical_opportunity"]["pair_dag_metrics"][0]
    assert pair_metric["baseline_cp_ns"] == 18
    assert pair_metric["gross_candidate_cp_ns"] == 18
    assert pair_metric["baseline_path"] == ["input", "other-branch", "join"]
    assert pair_metric["gross_path"] == ["input", "other-branch", "join"]
    assert result["method_selection"]["authorized"] is False


def test_semantic_cost_dag_requires_pinned_phase_chain_and_binds_read_fork_join() -> None:
    build = _build("FRESH_NATIVE")
    build["start_ns"] = 0
    build["end_ns"] = 100
    build["duration_ns"] = 100
    build["reads"][0].update(
        {
            "observer_start_ns": 25,
            "native_start_ns": 30,
            "native_end_ns": 40,
            "observer_end_ns": 40,
        }
    )
    build["trace"] = [
        {
            "span_id": "root",
            "parent_span_id": None,
            "phase": "add-episode",
            "start_ns": 0,
            "end_ns": 100,
            "duration_ns": 100,
            "status": "ok",
        },
        *[
            {
                "span_id": f"phase-{index}",
                "parent_span_id": "root",
                "phase": phase,
                "start_ns": start,
                "end_ns": end,
                "duration_ns": end - start,
                "status": "ok",
            }
            for index, (phase, start, end) in enumerate(
                (
                    ("previous-context", 0, 5),
                    ("node-extraction", 5, 20),
                    ("node-resolution", 20, 50),
                    ("edge-extraction", 50, 65),
                    ("edge-resolution", 65, 80),
                    ("attributes-summary", 80, 90),
                    ("publication", 90, 100),
                )
            )
        ],
    ]

    dag = build_semantic_cost_dag(build)
    assert dag["status"] == "COMPLETE"
    read_node = next(row for row in dag["nodes"] if row.get("read_key"))
    assert read_node["read_key"] == ["node_cosine", 0]
    assert read_node["cost_ns"] == 10
    assert read_node["predecessors"] == ["node-resolution-shell"]
    certificate = dag["cost_nodes"][0]
    assert certificate["kind"] == "certificate"
    assert certificate["cost_ns"] == 5
    assert certificate["gates"] == ["node-resolution-join"]

    broken = dict(build)
    broken["trace"] = [row for row in build["trace"] if row["phase"] != "edge-resolution"]
    unknown = build_semantic_cost_dag(broken)
    assert unknown["status"] == "UNKNOWN"
    assert unknown["reason"] == "required semantic phase is missing or ambiguous"


def test_semantic_cost_dag_handles_overlapping_read_observer_overhead() -> None:
    build = _build("OLD")
    build.update({"start_ns": 0, "end_ns": 100, "duration_ns": 100})
    build["reads"] = [
        {
            "operator": "node_cosine",
            "occurrence": index,
            "observer_start_ns": 20 + index,
            "native_start_ns": 22 + index,
            "native_end_ns": 58,
            "observer_end_ns": 90 + index,
        }
        for index in range(9)
    ]
    build["trace"] = [
        {
            "span_id": "root",
            "parent_span_id": None,
            "phase": "build-to-seam",
            "start_ns": 0,
            "end_ns": 100,
            "duration_ns": 100,
            "status": "ok",
        },
        *[
            {
                "span_id": f"phase-{index}",
                "parent_span_id": "root",
                "phase": phase,
                "start_ns": start,
                "end_ns": end,
                "duration_ns": end - start,
                "status": "ok",
            }
            for index, (phase, start, end) in enumerate(
                (
                    ("previous-context", 0, 5),
                    ("node-extraction", 5, 20),
                    ("node-resolution", 20, 60),
                    ("edge-extraction", 60, 75),
                    ("edge-resolution", 75, 85),
                    ("attributes-summary", 85, 95),
                )
            )
        ],
    ]
    dag = build_semantic_cost_dag(build)
    assert dag["status"] == "COMPLETE"
    assert next(node for node in dag["nodes"] if node["node_id"] == "node-resolution-shell")["cost_ns"] == 4


@pytest.mark.asyncio
async def test_real_campaign_reports_durable_block_progress_in_fixed_order(tmp_path: Path) -> None:
    protocol = {
        "workload": {
            "r1_r2": {"context_index": 0, "source_start": 0, "source_count": 2},
            "r3_blocks": [
                {"block_id": "R3-A", "context_index": 1, "source_start": 0, "source_count": 6},
                {"block_id": "R3-B", "context_index": 2, "source_start": 0, "source_count": 6},
            ],
        },
        "thresholds": {},
    }
    contexts = [list(range(2)), list(range(6)), list(range(6))]
    progress: list[dict] = []

    async def block_runner(**kwargs):
        count = len(kwargs["episodes"])
        return {
            "real_graphiti_evidence": True,
            "source_count": count,
            "block_id": kwargs["block_id"],
            "pairs": [],
            "shadow_publication_calls": 0,
            "native_publication_calls": count,
            "treatment_calls": 0,
        }

    with pytest.raises(CharacterizationError, match="incomplete"):
        await run_real_observer_campaign_async(
            protocol=protocol,
            contexts=contexts,
            episode_builder=lambda context: [
                {
                    "context_id": "ctx",
                    "source_sequence": sequence,
                    "episode_id": f"e-{sequence}",
                    "reference_time": "2026-08-25T00:00:00+00:00",
                    "body": "body",
                }
                for sequence in context
            ],
            runtime_builder_factory=lambda _lane: lambda: None,
            output_root=tmp_path / "campaign",
            run_id="v7-progress-test",
            block_runner=block_runner,
            progress_observer=lambda row: progress.append(dict(row)),
        )

    assert [(row["event"], row["block_id"], row["completed_block_count"]) for row in progress] == [
        ("BLOCK_START", "R1-R2", 0),
        ("BLOCK_COMPLETE", "R1-R2", 1),
    ]


def _blocked_attempts() -> list[dict]:
    timeout_digest = "9" * 64
    return [
        {
            "run_id": "v7-real-observer-004",
            "replacement_of": None,
            "failure_class": "INFRASTRUCTURE_PROVIDER_TIMEOUT",
            "attempt_validity": "INVALID_FOR_R1_R3_GATES",
            "gate_outcome": "NOT_EVALUATED",
            "selected_method": None,
            "error_type": "openai.APITimeoutError",
            "error_message_sha256": timeout_digest,
            "completed_block_count": 0,
            "treatment_calls": 0,
            "response_replay_calls": 0,
        },
        {
            "run_id": "v7-real-observer-005",
            "replacement_of": "v7-real-observer-004",
            "failure_class": "OBSERVER_RUNTIME_FAILURE",
            "attempt_validity": "INVALID_FOR_R1_R3_GATES",
            "gate_outcome": "NOT_EVALUATED",
            "selected_method": None,
            "error_type": "graphiti_core.errors.NodeNotFoundError",
            "error_message_sha256": "8" * 64,
            "completed_block_count": 0,
            "treatment_calls": 0,
            "response_replay_calls": 0,
        },
        {
            "run_id": "v7-real-observer-006",
            "replacement_of": "v7-real-observer-005",
            "failure_class": "INFRASTRUCTURE_PROVIDER_TIMEOUT",
            "attempt_validity": "INVALID_FOR_R1_R3_GATES",
            "gate_outcome": "NOT_EVALUATED",
            "selected_method": None,
            "error_type": "openai.APITimeoutError",
            "error_message_sha256": timeout_digest,
            "completed_block_count": 0,
            "treatment_calls": 0,
            "response_replay_calls": 0,
        },
    ]


def test_blocked_terminal_requires_complete_replacement_chain_and_repeated_timeout() -> None:
    result = validate_blocked_attempt_chain(_blocked_attempts())
    assert result["terminal_state"] == "V7_THEORY_OR_SYSTEM_BLOCKED"
    assert result["blocker"] == "SILICONFLOW_STRUCTURED_EXTRACTION_TIMEOUT"
    assert result["gate_a_e_evaluated"] is False
    assert result["selected_method"] is None
    with pytest.raises(ObserverArtifactError, match="replacement chain"):
        validate_blocked_attempt_chain([_blocked_attempts()[0], _blocked_attempts()[2]])
    changed = _blocked_attempts()
    changed[-1]["error_message_sha256"] = "7" * 64
    with pytest.raises(ObserverArtifactError, match="timeout signature"):
        validate_blocked_attempt_chain(changed)


def test_system_blocked_terminal_is_hash_sealed_and_authorizes_no_treatment(tmp_path: Path) -> None:
    root = tmp_path / "blocked"
    result = seal_system_blocked_terminal(
        root,
        protocol_sha256="a" * 64,
        attempts=_blocked_attempts(),
        evidence_files=[
            {"path": ".attempt-004.failure.json", "sha256": "1" * 64},
            {"path": ".attempt-006.failure.json", "sha256": "2" * 64},
        ],
        harness_source_sha256="b" * 64,
    )
    method = json.loads((root / "METHOD_SELECTION.json").read_text(encoding="ascii"))
    terminal = json.loads((root / "V7_TERMINAL_STATE.json").read_text(encoding="ascii"))
    assert result["verification"]["status"] == "PASS"
    assert method["status"] == "NOT_EVALUATED_SYSTEM_BLOCKED"
    assert method["authorized"] is False
    assert method["treatment_authorized"] is False
    assert method["selected_method"] is None
    assert terminal["state"] == "V7_THEORY_OR_SYSTEM_BLOCKED"
