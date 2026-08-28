from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.provider_admission import (
    provider_request_scope,
    provider_scope,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core import (
    provider_admission,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.routing import (
    CAPACITY_WEIGHTED_LEAST_OUTSTANDING,
    GRAPHITI_REQUEST_CLASS_AFFINITY,
    SEMANTIC_PHASE_AFFINITY,
    SEMANTIC_PHASE_CRITICAL_PATH_AFFINITY,
    SEMANTIC_PHASE_ELASTIC_AFFINITY,
    SEMANTIC_PHASE_EDGE_CALL_AFFINITY,
    SEMANTIC_PHASE_TOKEN_DEBT_AFFINITY,
    RoutedOpenAIClient,
    RoutingConfigurationError,
    install_routing_prompt_context,
    route_request_context,
    validate_route_evidence,
)


ENDPOINTS = (
    {
        "id": "native-replica",
        "base_url": "http://127.0.0.1:18200/v1",
        "served_model": "qwen3-8b-awq",
        "physical_gpu": 0,
    },
    {
        "id": "prepare-replica",
        "base_url": "http://127.0.0.1:18201/v1",
        "served_model": "qwen3-8b-awq",
        "physical_gpu": 1,
    },
)
SEMANTIC_PHASE_CAPACITY_BALANCED_AFFINITY = (
    "semantic_phase_capacity_balanced_affinity"
)
SEMANTIC_PHASE_LOGICAL_TOKEN_AFFINITY = "semantic_phase_logical_token_affinity"


class CompletionFixture:
    def __init__(self, endpoint_id: str, *, gate: asyncio.Event | None = None) -> None:
        self.endpoint_id = endpoint_id
        self.gate = gate
        self.calls: list[dict[str, object]] = []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.calls.append(dict(kwargs))
        if self.gate is not None:
            await self.gate.wait()
        if kwargs.get("fail"):
            raise ConnectionError("fixture failure")
        return {"endpoint_id": self.endpoint_id}

    async def close(self) -> None:
        return None


def clients(*, gate: asyncio.Event | None = None) -> dict[str, CompletionFixture]:
    return {
        "native-replica": CompletionFixture("native-replica", gate=gate),
        "prepare-replica": CompletionFixture("prepare-replica", gate=gate),
    }


def test_v61_phase_affinity_routes_strictly_and_fails_closed_without_scope() -> None:
    async def scenario() -> None:
        fixtures = clients()
        events: list[dict[str, object]] = []
        router = RoutedOpenAIClient(
            policy=SEMANTIC_PHASE_AFFINITY,
            endpoints=ENDPOINTS,
            endpoint_clients=fixtures,
            event_sink=events.append,
        )
        with provider_scope(region="PREPARE", source_sequence=3):
            prepared = await router.chat.completions.create(model="qwen3-8b-awq")
        with provider_scope(region="NATIVE", source_sequence=3):
            native = await router.chat.completions.create(model="qwen3-8b-awq")
        assert prepared["endpoint_id"] == "prepare-replica"
        assert native["endpoint_id"] == "native-replica"
        with pytest.raises(RoutingConfigurationError, match="no provider region"):
            await router.chat.completions.create(model="qwen3-8b-awq")
        assert [(row["region"], row["endpoint_id"]) for row in events] == [
            ("PREPARE", "prepare-replica"),
            ("NATIVE", "native-replica"),
        ]
        assert router.route_evidence()["balanced"] is True

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("prompt_name", "expected"),
    [
        ("extract_nodes.extract_message", "prepare-replica"),
        ("extract_nodes.extract_text", "prepare-replica"),
        ("extract_nodes.extract_json", "prepare-replica"),
        ("extract_edges.edge", "prepare-replica"),
        ("dedupe_nodes.nodes", "native-replica"),
        ("cross_encoder.rank", "native-replica"),
        (None, "native-replica"),
    ],
)
def test_static_role_uses_only_request_class(prompt_name: str | None, expected: str) -> None:
    async def scenario() -> None:
        fixtures = clients()
        events: list[dict[str, object]] = []
        router = RoutedOpenAIClient(
            policy=GRAPHITI_REQUEST_CLASS_AFFINITY,
            endpoints=ENDPOINTS,
            endpoint_clients=fixtures,
            event_sink=events.append,
        )
        with provider_scope(region="NATIVE", source_sequence=7):
            with route_request_context(prompt_name):
                result = await router.chat.completions.create(messages=[{"content": "secret"}])
        assert result["endpoint_id"] == expected
        assert events[0]["prompt_name"] == prompt_name
        assert "messages" not in events[0]

    asyncio.run(scenario())


def test_logical_prompt_wrapper_propagates_request_class_and_restores() -> None:
    async def scenario() -> None:
        fixtures = clients()
        router = RoutedOpenAIClient(
            policy=GRAPHITI_REQUEST_CLASS_AFFINITY,
            endpoints=ENDPOINTS,
            endpoint_clients=fixtures,
        )

        class LogicalClient:
            async def generate_response(self, _messages, **_kwargs):
                return await router.chat.completions.create(model="qwen3-8b-awq")

        logical = LogicalClient()
        original = logical.generate_response
        restore = install_routing_prompt_context(logical)
        result = await logical.generate_response([], prompt_name="extract_edges.edge")
        assert result["endpoint_id"] == "prepare-replica"
        restore()
        assert logical.generate_response.__func__ is original.__func__

    asyncio.run(scenario())


def test_native_dp_capacity_weighting_is_work_conserving_and_deterministic() -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        fixtures = clients(gate=gate)
        router = RoutedOpenAIClient(
            policy=CAPACITY_WEIGHTED_LEAST_OUTSTANDING,
            endpoints=ENDPOINTS,
            endpoint_clients=fixtures,
            capacity_weights={"native-replica": 2.0, "prepare-replica": 1.0},
        )
        tasks = [
            asyncio.create_task(router.chat.completions.create(request=i))
            for i in range(3)
        ]
        for _ in range(20):
            if sum(len(item.calls) for item in fixtures.values()) == 3:
                break
            await asyncio.sleep(0)
        assert len(fixtures["native-replica"].calls) == 2
        assert len(fixtures["prepare-replica"].calls) == 1
        gate.set()
        await asyncio.gather(*tasks)
        evidence = router.route_evidence()
        assert evidence["request_count"] == 3
        assert evidence["balanced"] is True

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("region", "preferred", "spillover"),
    [
        ("PREPARE", "prepare-replica", "native-replica"),
        ("NATIVE", "native-replica", "prepare-replica"),
    ],
)
def test_elastic_phase_affinity_steals_only_an_idle_replica(
    region: str, preferred: str, spillover: str
) -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        fixtures = clients(gate=gate)
        events: list[dict[str, object]] = []
        router = RoutedOpenAIClient(
            policy=SEMANTIC_PHASE_ELASTIC_AFFINITY,
            endpoints=ENDPOINTS,
            endpoint_clients=fixtures,
            event_sink=events.append,
        )
        with provider_scope(region=region, source_sequence=4):
            first = asyncio.create_task(router.chat.completions.create(request=0))
            for _ in range(20):
                if sum(len(item.calls) for item in fixtures.values()) == 1:
                    break
                await asyncio.sleep(0)
            second = asyncio.create_task(router.chat.completions.create(request=1))
            for _ in range(20):
                if sum(len(item.calls) for item in fixtures.values()) == 2:
                    break
                await asyncio.sleep(0)

        assert len(fixtures[preferred].calls) == 1
        assert len(fixtures[spillover].calls) == 1
        gate.set()
        await asyncio.gather(first, second)
        ordered = sorted(events, key=lambda row: int(row["request_index"]))
        assert ordered[0]["endpoint_id"] == preferred
        assert ordered[0]["route_reason"] == "semantic_phase_preferred"
        assert ordered[0]["spillover"] is False
        assert ordered[1]["endpoint_id"] == spillover
        assert ordered[1]["route_reason"] == "semantic_phase_idle_spillover"
        assert ordered[1]["spillover"] is True
        assert ordered[1]["selection_outstanding"] == {
            preferred: 1,
            spillover: 0,
        }
        proof = validate_route_evidence(
            ordered,
            policy=SEMANTIC_PHASE_ELASTIC_AFFINITY,
            endpoint_ids=("native-replica", "prepare-replica"),
            transport_attempt_count=2,
        )
        assert proof["status"] == "PASS"
        assert proof["spillover_count"] == 1

    asyncio.run(scenario())


def test_edge_call_affinity_pins_continuations_and_preserves_elastic_non_edge_routing() -> None:
    async def scenario() -> None:
        fixtures = clients()
        events: list[dict[str, object]] = []
        router = RoutedOpenAIClient(
            policy=SEMANTIC_PHASE_EDGE_CALL_AFFINITY,
            endpoints=ENDPOINTS,
            endpoint_clients=fixtures,
            event_sink=events.append,
        )

        class LogicalClient:
            async def generate_response(self, _messages, **_kwargs):
                responses = []
                for index in range(3):
                    responses.append(
                        await router.chat.completions.create(request=index)
                    )
                return responses

        logical = LogicalClient()
        restore = install_routing_prompt_context(logical)
        try:
            with provider_scope(region="PREPARE", source_sequence=2):
                result = await logical.generate_response(
                    [], prompt_name="extract_edges.edge"
                )
        finally:
            restore()

        assert [row["endpoint_id"] for row in result] == [
            "prepare-replica",
            "prepare-replica",
            "prepare-replica",
        ]
        edge_rows = [row for row in events if row["prompt_name"] == "extract_edges.edge"]
        assert [row["route_reason"] for row in edge_rows] == [
            "edge_call_affinity_preferred",
            "edge_call_affinity_reuse",
            "edge_call_affinity_reuse",
        ]
        assert [row["logical_group_first_transport"] for row in edge_rows] == [
            True,
            False,
            False,
        ]
        proof = validate_route_evidence(
            edge_rows,
            policy=SEMANTIC_PHASE_EDGE_CALL_AFFINITY,
            endpoint_ids=("native-replica", "prepare-replica"),
            transport_attempt_count=3,
            logical_group_events=router.route_evidence()["edge_group_events"],
        )
        assert proof["status"] == "PASS"
        assert router.route_evidence()["balanced"] is True

    asyncio.run(scenario())


def test_capacity_balanced_phase_affinity_uses_manifest_weights_and_preferred_ties() -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        fixtures = clients(gate=gate)
        events: list[dict[str, object]] = []
        weights = {"native-replica": 2.0, "prepare-replica": 1.0}
        router = RoutedOpenAIClient(
            policy=SEMANTIC_PHASE_CAPACITY_BALANCED_AFFINITY,
            endpoints=ENDPOINTS,
            endpoint_clients=fixtures,
            capacity_weights=weights,
            event_sink=events.append,
        )
        with provider_scope(region="NATIVE", source_sequence=5):
            tasks = [
                asyncio.create_task(router.chat.completions.create(request=index))
                for index in range(6)
            ]
        for _ in range(30):
            if sum(len(item.calls) for item in fixtures.values()) == 6:
                break
            await asyncio.sleep(0)

        assert len(fixtures["native-replica"].calls) == 4
        assert len(fixtures["prepare-replica"].calls) == 2
        gate.set()
        await asyncio.gather(*tasks)

        ordered = sorted(events, key=lambda row: int(row["request_index"]))
        assert ordered[0]["endpoint_id"] == "native-replica"
        assert ordered[0]["route_reason"] == "semantic_phase_capacity_preferred"
        assert ordered[0]["selection_outstanding"] == {
            "native-replica": 0,
            "prepare-replica": 0,
        }
        assert all(row["capacity_weights"] == weights for row in ordered)
        proof = validate_route_evidence(
            ordered,
            policy=SEMANTIC_PHASE_CAPACITY_BALANCED_AFFINITY,
            endpoint_ids=("native-replica", "prepare-replica"),
            transport_attempt_count=6,
            capacity_weights=weights,
        )
        assert proof["status"] == "PASS"
        assert proof["capacity_weights"] == weights
        assert router.route_evidence()["balanced"] is True

        for field, value in (
            ("endpoint_id", "prepare-replica"),
            ("route_reason", "semantic_phase_capacity_spillover"),
            ("capacity_weights", {"native-replica": 1.0, "prepare-replica": 1.0}),
        ):
            tampered = copy.deepcopy(ordered)
            tampered[0][field] = value
            with pytest.raises(RoutingConfigurationError, match="capacity-balanced"):
                validate_route_evidence(
                    tampered,
                    policy=SEMANTIC_PHASE_CAPACITY_BALANCED_AFFINITY,
                    endpoint_ids=("native-replica", "prepare-replica"),
                    transport_attempt_count=6,
                    capacity_weights=weights,
                )

    asyncio.run(scenario())


def test_logical_token_affinity_pins_expansions_and_replays_group_debt() -> None:
    async def scenario() -> None:
        fixtures = clients()
        events: list[dict[str, object]] = []
        weights = {"native-replica": 2.0, "prepare-replica": 1.0}
        router = RoutedOpenAIClient(
            policy=SEMANTIC_PHASE_LOGICAL_TOKEN_AFFINITY,
            endpoints=ENDPOINTS,
            endpoint_clients=fixtures,
            capacity_weights=weights,
            event_sink=events.append,
        )

        class LogicalClient:
            async def generate_response(
                self,
                _messages,
                *,
                ready: asyncio.Event,
                release: asyncio.Event,
                **_kwargs,
            ):
                first = await router.chat.completions.create(page=0)
                ready.set()
                await release.wait()
                second = await router.chat.completions.create(page=1)
                return [first["endpoint_id"], second["endpoint_id"]]

        logical = LogicalClient()
        restore = install_routing_prompt_context(logical)
        first_ready = asyncio.Event()
        first_release = asyncio.Event()
        with provider_scope(region="PREPARE", source_sequence=0):
            with provider_admission.provider_request_scope(request_tokens=900):
                first = asyncio.create_task(
                    logical.generate_response(
                        [],
                        prompt_name="extract_edges.edge",
                        ready=first_ready,
                        release=first_release,
                    )
                )
        await first_ready.wait()

        second_ready = asyncio.Event()
        second_release = asyncio.Event()
        second_release.set()
        with provider_scope(region="PREPARE", source_sequence=1):
            with provider_admission.provider_request_scope(request_tokens=100):
                second = asyncio.create_task(
                    logical.generate_response(
                        [],
                        prompt_name="extract_edges.edge",
                        ready=second_ready,
                        release=second_release,
                    )
                )
        assert await second == ["native-replica", "native-replica"]
        first_release.set()
        assert await first == ["prepare-replica", "prepare-replica"]
        restore()

        runtime = router.route_evidence()
        assert runtime["balanced"] is True
        assert runtime["active_logical_groups"] == {
            "native-replica": 0,
            "prepare-replica": 0,
        }
        assert runtime["active_token_debt"] == {
            "native-replica": 0,
            "prepare-replica": 0,
        }
        ordered = sorted(events, key=lambda row: int(row["request_index"]))
        groups: dict[int, set[str]] = {}
        for row in ordered:
            groups.setdefault(int(row["logical_group_id"]), set()).add(
                str(row["endpoint_id"])
            )
        assert sorted(groups.values(), key=lambda value: next(iter(value))) == [
            {"native-replica"},
            {"prepare-replica"},
        ]
        proof = validate_route_evidence(
            ordered,
            policy=SEMANTIC_PHASE_LOGICAL_TOKEN_AFFINITY,
            endpoint_ids=("native-replica", "prepare-replica"),
            transport_attempt_count=4,
            capacity_weights=weights,
            logical_group_events=runtime["logical_group_events"],
        )
        assert proof["status"] == "PASS"
        assert proof["logical_group_count"] == 2

        tampered = copy.deepcopy(runtime["logical_group_events"])
        tampered[0]["request_tokens"] = 1
        with pytest.raises(RoutingConfigurationError, match="logical-token"):
            validate_route_evidence(
                ordered,
                policy=SEMANTIC_PHASE_LOGICAL_TOKEN_AFFINITY,
                endpoint_ids=("native-replica", "prepare-replica"),
                transport_attempt_count=4,
                capacity_weights=weights,
                logical_group_events=tampered,
            )

    asyncio.run(scenario())


def test_logical_token_affinity_releases_debt_when_logical_call_is_cancelled() -> None:
    async def scenario() -> None:
        fixtures = clients()
        router = RoutedOpenAIClient(
            policy=SEMANTIC_PHASE_LOGICAL_TOKEN_AFFINITY,
            endpoints=ENDPOINTS,
            endpoint_clients=fixtures,
            capacity_weights={"native-replica": 2.0, "prepare-replica": 1.0},
        )

        class LogicalClient:
            async def generate_response(self, _messages, *, ready, release, **_kwargs):
                await router.chat.completions.create(page=0)
                ready.set()
                await release.wait()

        logical = LogicalClient()
        restore = install_routing_prompt_context(logical)
        ready = asyncio.Event()
        release = asyncio.Event()
        with provider_scope(region="NATIVE", source_sequence=2):
            with provider_admission.provider_request_scope(request_tokens=700):
                task = asyncio.create_task(
                    logical.generate_response(
                        [], prompt_name="dedupe_edges.resolve_edge", ready=ready, release=release
                    )
                )
        await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        restore()
        evidence = router.route_evidence()
        assert evidence["balanced"] is True
        assert evidence["logical_group_events"][-1]["status"] == "cancelled"

    asyncio.run(scenario())


def test_token_debt_affinity_prices_each_transport_without_pinning_continuations() -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        fixtures = clients(gate=gate)
        events: list[dict[str, object]] = []
        weights = {"native-replica": 1.0, "prepare-replica": 1.0}
        router = RoutedOpenAIClient(
            policy=SEMANTIC_PHASE_TOKEN_DEBT_AFFINITY,
            endpoints=ENDPOINTS,
            endpoint_clients=fixtures,
            capacity_weights=weights,
            event_sink=events.append,
        )

        async def call(index: int, request_tokens: int) -> object:
            with provider_scope(region="NATIVE", source_sequence=3):
                with provider_admission.provider_request_scope(
                    request_tokens=request_tokens
                ):
                    return await router.chat.completions.create(request=index)

        tasks = [
            asyncio.create_task(call(0, 8)),
            asyncio.create_task(call(1, 8)),
            asyncio.create_task(call(2, 4)),
        ]
        for _ in range(30):
            if sum(len(item.calls) for item in fixtures.values()) == 3:
                break
            await asyncio.sleep(0)

        # The second request is sent to the other replica because the first
        # request already contributes eight tokens of active debt.  The third
        # request is free to choose again; no logical continuation is pinned.
        assert len(fixtures["native-replica"].calls) == 2
        assert len(fixtures["prepare-replica"].calls) == 1
        gate.set()
        await asyncio.gather(*tasks)

        ordered = sorted(events, key=lambda row: int(row["request_index"]))
        assert [row["endpoint_id"] for row in ordered] == [
            "native-replica",
            "prepare-replica",
            "native-replica",
        ]
        assert ordered[0]["route_reason"] == "semantic_phase_token_debt_preferred"
        assert ordered[1]["route_reason"] == "semantic_phase_token_debt_spillover"
        assert ordered[0]["selection_token_debt"] == {
            "native-replica": 0,
            "prepare-replica": 0,
        }
        assert ordered[1]["selection_token_debt"] == {
            "native-replica": 8,
            "prepare-replica": 0,
        }
        assert ordered[2]["selection_token_debt"] == {
            "native-replica": 8,
            "prepare-replica": 8,
        }
        proof = validate_route_evidence(
            ordered,
            policy=SEMANTIC_PHASE_TOKEN_DEBT_AFFINITY,
            endpoint_ids=("native-replica", "prepare-replica"),
            transport_attempt_count=3,
            capacity_weights=weights,
        )
        assert proof["status"] == "PASS"
        assert router.route_evidence()["balanced"] is True

        tampered = copy.deepcopy(ordered)
        tampered[1]["request_tokens"] = 7
        with pytest.raises(RoutingConfigurationError, match="token-debt"):
            validate_route_evidence(
                tampered,
                policy=SEMANTIC_PHASE_TOKEN_DEBT_AFFINITY,
                endpoint_ids=("native-replica", "prepare-replica"),
                transport_attempt_count=3,
                capacity_weights=weights,
            )

    asyncio.run(scenario())


def test_error_and_cancellation_release_endpoint_counters() -> None:
    async def scenario() -> None:
        events: list[dict[str, object]] = []
        failure_clients = clients()
        failure_router = RoutedOpenAIClient(
            policy=CAPACITY_WEIGHTED_LEAST_OUTSTANDING,
            endpoints=ENDPOINTS,
            endpoint_clients=failure_clients,
            event_sink=events.append,
        )
        with pytest.raises(ConnectionError):
            await failure_router.chat.completions.create(fail=True)
        assert failure_router.route_evidence()["balanced"] is True

        gate = asyncio.Event()
        cancel_clients = clients(gate=gate)
        cancel_router = RoutedOpenAIClient(
            policy=CAPACITY_WEIGHTED_LEAST_OUTSTANDING,
            endpoints=ENDPOINTS,
            endpoint_clients=cancel_clients,
            event_sink=events.append,
        )
        task = asyncio.create_task(cancel_router.chat.completions.create())
        for _ in range(20):
            if sum(len(item.calls) for item in cancel_clients.values()) == 1:
                break
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancel_router.route_evidence()["balanced"] is True
        assert [row["status"] for row in events] == ["failure", "cancelled"]

    asyncio.run(scenario())


def test_route_proof_matches_transport_count_and_phase_bindings() -> None:
    rows = [
        {
            "schema_version": "membind.v6.1.llm-route.v1",
            "policy": SEMANTIC_PHASE_AFFINITY,
            "request_index": 1,
            "endpoint_id": "native-replica",
            "region": "NATIVE",
            "status": "success",
        },
        {
            "schema_version": "membind.v6.1.llm-route.v1",
            "policy": SEMANTIC_PHASE_AFFINITY,
            "request_index": 0,
            "endpoint_id": "prepare-replica",
            "region": "PREPARE",
            "status": "success",
        },
    ]
    proof = validate_route_evidence(
        rows,
        policy=SEMANTIC_PHASE_AFFINITY,
        endpoint_ids=("native-replica", "prepare-replica"),
        transport_attempt_count=2,
    )
    assert proof["status"] == "PASS"
    assert proof["all_transports_routed"] is True
    invalid = [dict(row) for row in rows]
    invalid[0]["endpoint_id"] = "prepare-replica"
    with pytest.raises(RoutingConfigurationError, match="phase affinity"):
        validate_route_evidence(
            invalid,
            policy=SEMANTIC_PHASE_AFFINITY,
            endpoint_ids=("native-replica", "prepare-replica"),
            transport_attempt_count=2,
        )


def test_critical_path_route_uses_measured_finish_work_and_seals_decisions() -> None:
    async def scenario() -> None:
        release_first = asyncio.Event()
        fixtures = {
            "native-replica": CompletionFixture("native-replica"),
            "prepare-replica": CompletionFixture("prepare-replica", gate=release_first),
        }
        events: list[dict[str, object]] = []
        router = RoutedOpenAIClient(
            policy=SEMANTIC_PHASE_CRITICAL_PATH_AFFINITY,
            endpoints=ENDPOINTS,
            endpoint_clients=fixtures,
            event_sink=events.append,
        )

        async def call(source_sequence: int) -> dict[str, object]:
            with provider_scope(region="PREPARE", source_sequence=source_sequence):
                with provider_request_scope(request_tokens=100):
                    return await router.chat.completions.create(
                        messages=[{"role": "user", "content": "x"}],
                        max_tokens=8,
                    )

        first = asyncio.create_task(call(0))
        await asyncio.sleep(0)
        second = asyncio.create_task(call(1))
        await asyncio.sleep(0)
        assert not first.done()
        assert second.done()
        assert await second == {"endpoint_id": "native-replica"}
        release_first.set()
        assert await first == {"endpoint_id": "prepare-replica"}

        runtime = router.route_evidence()
        assert runtime["balanced"] is True
        assert runtime["critical_scheduler"]["balanced"] is True
        ordered = sorted(events, key=lambda row: int(row["request_index"]))
        assert [row["endpoint_id"] for row in ordered] == [
            "prepare-replica",
            "native-replica",
        ]
        assert ordered[0]["route_reason"] == "critical_path_preferred"
        assert ordered[1]["route_reason"] == "critical_path_earliest_finish_spillover"
        assert ordered[1]["critical_path_decision"]["candidate_scores"] == {
            "native-replica": 100,
            "prepare-replica": 200,
        }
        proof = validate_route_evidence(
            events,
            policy=SEMANTIC_PHASE_CRITICAL_PATH_AFFINITY,
            endpoint_ids=("native-replica", "prepare-replica"),
            transport_attempt_count=2,
        )
        assert proof["status"] == "PASS"

    asyncio.run(scenario())


def test_critical_path_service_sample_excludes_physical_admission_wait() -> None:
    async def scenario() -> None:
        fixtures = clients()
        events: list[dict[str, object]] = []
        router = RoutedOpenAIClient(
            policy=SEMANTIC_PHASE_CRITICAL_PATH_AFFINITY,
            endpoints=ENDPOINTS,
            endpoint_clients=fixtures,
            event_sink=events.append,
        )
        permit_ready = asyncio.Event()

        async def acquire(**_kwargs: object) -> object:
            await permit_ready.wait()
            return object()

        async def release(_permit: object) -> None:
            return None

        router._membind_physical_admission_enabled = True
        router._membind_physical_admission_acquire = acquire
        router._membind_physical_admission_release = release

        async def call() -> dict[str, object]:
            with provider_scope(region="PREPARE", source_sequence=0):
                with provider_request_scope(request_tokens=100):
                    return await router.chat.completions.create(request=0)

        task = asyncio.create_task(call())
        await asyncio.sleep(0)
        await asyncio.sleep(0.01)
        permit_ready.set()
        await task

        row = events[0]
        assert isinstance(row["service_start_ns"], int)
        assert isinstance(row["service_end_ns"], int)
        assert isinstance(row["service_duration_ns"], int)
        assert row["service_start_ns"] >= row["start_ns"]
        assert row["service_duration_ns"] == (
            row["service_end_ns"] - row["service_start_ns"]
        )
        assert row["duration_ns"] >= row["service_duration_ns"]
        assert router.route_evidence()["critical_scheduler"]["balanced"] is True

    asyncio.run(scenario())
