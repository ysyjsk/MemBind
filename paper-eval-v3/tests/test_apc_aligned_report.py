"""Offline aggregation contracts for the APC-aligned baseline report."""

from __future__ import annotations

from paper_eval.apc_aligned_report import reduce_apc_aligned_results


def _block(method: str, history: str, index: int) -> dict[str, object]:
    hit = 10 + index
    query = 20 + index
    return {
        "status": "PASS",
        "method": f"{method}-aligned",
        "history_id": history,
        "episode_count": 2,
        "plan_payload_sha256": "a" * 64,
        "performance": {
            "makespan_ns": 100,
            "max_outstanding_backlog": 2,
            "max_waiting_queue_depth": 1,
            "per_source": [
                {"freshness_ns": 20 + index, "queue_delay_ns": 2, "service_latency_ns": 18},
                {"freshness_ns": 30 + index, "queue_delay_ns": 3, "service_latency_ns": 27},
            ],
        },
        "correctness": {
            "checker_status": "MEASURED",
            "direct_violations_total": 1 if method == "P(C=2)" else 0,
            "counts": {
                "lost_or_missing_source_count": 0,
                "duplicate_source_or_publication_count": 0,
                "source_publication_order_violation_count": 1 if method == "P(C=2)" else 0,
                "visibility_publication_violation_count": 0,
                "temporal_provenance_hard_violation_count": 0,
            },
        },
        "vllm_telemetry": {
            "measured_duration_seconds": 1.0,
            "counters": {
                "prefix_cache_hit_delta": hit,
                "prefix_cache_query_delta": query,
                "preemption_delta": 0,
                "prompt_token_delta": 100,
                "generation_token_delta": 10,
            },
            "gauges": {
                "running_requests": {"mean": 1.0, "p95": 2.0, "max": 2.0, "time_above_zero_fraction": 1.0},
                "waiting_requests": {"mean": 0.0, "p95": 0.0, "max": 0.0, "time_above_zero_fraction": 0.0},
                "gpu_kv_cache_usage": {"mean": 0.2, "p95": 0.3, "max": 0.3, "time_above_zero_fraction": 1.0},
            },
        },
    }


def test_reducer_pools_episode_freshness_and_counter_numerators() -> None:
    histories = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
    blocks = [
        _block(method, history, index)
        for index, (method, history) in enumerate(
            (method, history) for method in ("U0", "A0", "P(C=2)") for history in histories
        )
    ]
    report = reduce_apc_aligned_results(blocks=blocks, quality_report=None)
    assert report["status"] == "CONSTRUCTION_PASS_QUALITY_PENDING"
    assert report["main_table"]["U0"]["episode_count"] == 8
    assert report["main_table"]["U0"]["makespan_ns"] == 400
    assert report["main_table"]["U0"]["goodput_episodes_per_second"] == 20_000_000
    assert report["main_table"]["P(C=2)"]["direct_violations"] == 4
    assert report["main_table"]["U0"]["prefix_cache_hit_rate"] == (10 + 11 + 12 + 13) / (20 + 21 + 22 + 23)
