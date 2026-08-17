"""Offline RED/GREEN contracts for process-global vLLM APC telemetry."""

from __future__ import annotations

from paper_eval.apc_vllm_telemetry import (
    PrometheusSnapshot,
    parse_vllm_prometheus,
    reduce_vllm_telemetry,
)


def test_parser_selects_only_the_frozen_vllm_026_metrics() -> None:
    text = """
vllm:num_requests_running{engine="0",model_name="qwen3-32b-fp8"} 2.0
vllm:num_requests_waiting{engine="0",model_name="qwen3-32b-fp8"} 1.0
vllm:num_requests_waiting_by_reason{engine="0",model_name="qwen3-32b-fp8",reason="capacity"} 1.0
vllm:kv_cache_usage_perc{engine="0",model_name="qwen3-32b-fp8"} 0.4
vllm:prefix_cache_queries_total{engine="0",model_name="qwen3-32b-fp8"} 100
vllm:prefix_cache_hits_total{engine="0",model_name="qwen3-32b-fp8"} 25
vllm:num_preemptions_total{engine="0",model_name="qwen3-32b-fp8"} 3
vllm:prompt_tokens_total{engine="0",model_name="qwen3-32b-fp8"} 1000
vllm:generation_tokens_total{engine="0",model_name="qwen3-32b-fp8"} 50
"""
    snapshot = parse_vllm_prometheus(text, timestamp_ns=7)
    assert snapshot.values == {
        "running_requests": 2.0,
        "waiting_requests": 1.0,
        "gpu_kv_cache_usage": 0.4,
        "prefix_cache_queries": 100.0,
        "prefix_cache_hits": 25.0,
        "preemptions": 3.0,
        "prompt_tokens": 1000.0,
        "generation_tokens": 50.0,
    }


def _snapshot(timestamp_ns: int, **values: float) -> PrometheusSnapshot:
    return PrometheusSnapshot(timestamp_ns=timestamp_ns, values=values)


def test_telemetry_uses_counter_deltas_and_time_series_gauges() -> None:
    samples = [
        _snapshot(
            0,
            prefix_cache_queries=100,
            prefix_cache_hits=40,
            preemptions=2,
            prompt_tokens=1000,
            generation_tokens=100,
            running_requests=0,
            waiting_requests=0,
            gpu_kv_cache_usage=0.0,
        ),
        _snapshot(
            10,
            prefix_cache_queries=130,
            prefix_cache_hits=55,
            preemptions=3,
            prompt_tokens=1600,
            generation_tokens=160,
            running_requests=2,
            waiting_requests=1,
            gpu_kv_cache_usage=0.5,
        ),
        _snapshot(
            20,
            prefix_cache_queries=150,
            prefix_cache_hits=65,
            preemptions=3,
            prompt_tokens=2000,
            generation_tokens=200,
            running_requests=0,
            waiting_requests=0,
            gpu_kv_cache_usage=0.2,
        ),
    ]

    result = reduce_vllm_telemetry(samples)

    assert result["counters"]["prefix_cache_query_delta"] == 50
    assert result["counters"]["prefix_cache_hit_delta"] == 25
    assert result["prefix_cache_hit_rate"] == 0.5
    assert result["counters"]["preemption_delta"] == 1
    assert result["gauges"]["running_requests"]["max"] == 2
    assert result["gauges"]["waiting_requests"]["time_above_zero_fraction"] == 0.5
    assert result["gauges"]["gpu_kv_cache_usage"]["max"] == 0.5


def test_counter_reset_inside_measured_window_fails_closed() -> None:
    samples = [
        _snapshot(0, prefix_cache_queries=10, prefix_cache_hits=5),
        _snapshot(1, prefix_cache_queries=1, prefix_cache_hits=0),
    ]

    try:
        reduce_vllm_telemetry(samples)
    except ValueError as error:
        assert "counter" in str(error)
    else:
        raise AssertionError("counter reset must fail closed")
