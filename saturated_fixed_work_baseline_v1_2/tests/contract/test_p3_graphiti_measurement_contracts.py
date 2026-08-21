from __future__ import annotations

from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_2.canonical_diff import canonical_diff
from saturated_fixed_work_baseline_v1_2.contracts import Availability, EpisodeInput
from saturated_fixed_work_baseline_v1_2.graphiti_adapter import (
    GraphitiNativeAdapter,
    build_graphiti_kwargs,
)
from saturated_fixed_work_baseline_v1_2.qualification import (
    QualificationError,
    qualify_instrumentation_aa,
    serial_serial_12_diagnostic,
)
from saturated_fixed_work_baseline_v1_2.reuse import collect_reuse_compatibility
from saturated_fixed_work_baseline_v1_2.telemetry import parse_vllm_026_metrics


def _episodes() -> tuple[EpisodeInput, ...]:
    timestamps = ("2023/01/01 (Sun) 00:00", "2023/01/02 (Mon) 00:00")
    return tuple(
        EpisodeInput(
            history_id="07741c45",
            session_id=f"session-{index}",
            source_sequence=index,
            source_hash=f"{index + 1:064x}",
            reference_time=timestamps[index],
            body=f"body-{index}",
            namespace="v1_2/B0/07741c45/adapter-test",
        )
        for index in range(2)
    )


@pytest.mark.asyncio
async def test_b0_adapter_matches_official_native_serial_loop() -> None:
    class FakeGraphiti:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def add_episode(self, **kwargs: object) -> str:
            self.calls.append(kwargs)
            return str(kwargs["name"])

    official = FakeGraphiti()
    adapted = FakeGraphiti()
    episodes = _episodes()
    for episode in episodes:
        await official.add_episode(
            **build_graphiti_kwargs(episode, source="MESSAGE")
        )
    adapter = GraphitiNativeAdapter(adapted, source="MESSAGE")
    results = [await adapter.add_episode(episode) for episode in episodes]
    assert adapted.calls == official.calls
    assert results == ["07741c45::episode::0000", "07741c45::episode::0001"]


@pytest.mark.asyncio
async def test_b0_adapter_preserves_native_exception() -> None:
    error = RuntimeError("native failure")

    class FailingGraphiti:
        async def add_episode(self, **kwargs: object) -> None:
            del kwargs
            raise error

    with pytest.raises(RuntimeError) as raised:
        await GraphitiNativeAdapter(FailingGraphiti(), source="MESSAGE").add_episode(
            _episodes()[0]
        )
    assert raised.value is error


def test_existing_measurement_stack_symbols_are_compatible(repository_root: Path) -> None:
    compatibility = collect_reuse_compatibility(repository_root)
    assert compatibility["compatible"] is True
    assert compatibility["symbols"] == {
        "install_native_characterization_instrumentation": True,
        "TraceRecorder": True,
        "DurableJsonlEnvelopeWriter": True,
        "interval_union_ns": True,
        "exclusive_duration_ns": True,
        "critical_path_ns": True,
        "install_c2_measurement_adapter": True,
    }


def _metrics_fixture() -> str:
    return "\n".join(
        (
            'vllm:num_requests_running{model_name="qwen3-32b-fp8"} 2',
            'vllm:num_requests_waiting{model_name="qwen3-32b-fp8"} 3',
            'vllm:kv_cache_usage_perc{model_name="qwen3-32b-fp8"} 0.5',
            'vllm:prefix_cache_queries_total{model_name="qwen3-32b-fp8"} 11',
            'vllm:prefix_cache_hits_total{model_name="qwen3-32b-fp8"} 7',
            'vllm:num_preemptions_total{model_name="qwen3-32b-fp8"} 1',
            'vllm:prompt_tokens_total{model_name="qwen3-32b-fp8"} 100',
            'vllm:generation_tokens_total{model_name="qwen3-32b-fp8"} 25',
        )
    )


def test_vllm_026_metrics_fixture_is_parsed_by_reused_parser(
    repository_root: Path,
) -> None:
    result = parse_vllm_026_metrics(
        _metrics_fixture(), timestamp_ns=10, repository_root=repository_root
    )
    assert result.availability is Availability.MEASURED
    assert result.value.values["running_requests"] == 2.0
    assert result.value.values["prefix_cache_hits"] == 7.0


def test_vllm_missing_field_is_invalid_not_zero(repository_root: Path) -> None:
    result = parse_vllm_026_metrics(
        _metrics_fixture().replace(
            'vllm:num_requests_waiting{model_name="qwen3-32b-fp8"} 3\n', ""
        ),
        timestamp_ns=10,
        repository_root=repository_root,
    )
    assert result.availability is Availability.INVALID
    assert result.value is None
    assert "waiting_requests" in result.reason


def _graph() -> dict[str, object]:
    return {
        "entities": [
            {
                "group_id": "g",
                "name": "Alice",
                "labels": ["Entity"],
                "summary": "original",
                "attributes": {"kind": "person"},
            }
        ],
        "edges": [
            {
                "source_entity_key": "Alice",
                "target_entity_key": "Paris",
                "relation_type": "VISITED",
                "fact": "visited",
                "valid_at": "2023-01-01",
                "invalid_at": None,
                "expired_at": None,
                "attributes": {},
                "source_episode_sequence": 0,
            }
        ],
        "episodes": [
            {"source_sequence": 0, "source_hash": "1" * 64, "session_id": "s0"}
        ],
    }


@pytest.mark.parametrize(
    ("mutation", "category"),
    [
        (lambda graph: graph["entities"][0].update(name="Bob"), "entity_key"),
        (lambda graph: graph["entities"][0].update(summary="changed"), "attribute"),
        (lambda graph: graph["edges"][0].update(valid_at="2024-01-01"), "temporal"),
        (lambda graph: graph["episodes"][0].update(source_hash="2" * 64), "source_link"),
    ],
)
def test_canonical_diff_identifies_semantic_difference_category(
    repository_root: Path, mutation: object, category: str
) -> None:
    import copy

    reference = _graph()
    candidate = copy.deepcopy(reference)
    mutation(candidate)
    diff = canonical_diff(reference, candidate, repository_root=repository_root)
    assert diff["exact_match"] is False
    assert diff["difference_counts"][category] > 0


def test_canonical_diff_normalizes_only_the_explicit_formal_namespaces(
    repository_root: Path,
) -> None:
    import copy

    reference_namespace = "v1_2/B0/07741c45/run/attempt-001"
    candidate_namespace = "v1_2/B1/07741c45/run/attempt-001"
    reference = _graph()
    reference["entities"][0]["group_id"] = reference_namespace
    candidate = copy.deepcopy(reference)
    candidate["entities"][0]["group_id"] = candidate_namespace

    unprojected = canonical_diff(
        reference,
        candidate,
        repository_root=repository_root,
    )
    projected = canonical_diff(
        reference,
        candidate,
        repository_root=repository_root,
        reference_namespace=reference_namespace,
        candidate_namespace=candidate_namespace,
    )

    assert unprojected["exact_match"] is False
    assert projected["exact_match"] is True
    assert projected["difference_counts"] == {
        "entity_key": 0,
        "edge_key": 0,
        "attribute": 0,
        "temporal": 0,
        "source_link": 0,
    }
    assert projected["namespace_projection"] == {
        "applied": True,
        "logical_group_id": "__FORMAL_HISTORY_NAMESPACE__",
        "reference_replacements": 1,
        "candidate_replacements": 1,
    }


def test_canonical_diff_rejects_one_sided_namespace_projection(
    repository_root: Path,
) -> None:
    with pytest.raises(ValueError, match="CANONICAL_NAMESPACE_PAIR_REQUIRED"):
        canonical_diff(
            _graph(),
            _graph(),
            repository_root=repository_root,
            reference_namespace="v1_2/B0/07741c45/run/attempt-001",
        )


def test_instrumentation_aa_and_serial_serial_scope() -> None:
    graph = _graph()
    certificate = qualify_instrumentation_aa(
        baseline_graph=graph,
        instrumented_graph=graph,
        baseline_duration_ns=1_000,
        instrumented_duration_ns=1_010,
        max_overhead_fraction=0.02,
    )
    assert certificate["qualified"] is True
    assert certificate["overhead_fraction"] == pytest.approx(0.01)
    diagnostic = serial_serial_12_diagnostic(graph, graph)
    assert diagnostic["episode_count"] == 12
    assert diagnostic["scope"] == "12_EPISODE_QUALIFICATION_ONLY"
    assert diagnostic["full_history_nondeterminism_floor"] is None
    with pytest.raises(QualificationError, match="INSTRUMENTATION_OVERHEAD_EXCEEDED"):
        qualify_instrumentation_aa(
            baseline_graph=graph,
            instrumented_graph=graph,
            baseline_duration_ns=1_000,
            instrumented_duration_ns=1_030,
            max_overhead_fraction=0.02,
        )
