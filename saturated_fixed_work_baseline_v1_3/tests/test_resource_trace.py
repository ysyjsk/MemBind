from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "saturated_fixed_work_baseline_v1_3/scripts/run_mab_upstream_8b.py"


def _module():
    spec = importlib.util.spec_from_file_location("run_mab_upstream_resource_trace", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(ts: float, generation: float, running: float, gpu_util: float) -> dict:
    return {
        "captured_unix": ts,
        "endpoints": {
            "native-replica": {
                "endpoint_id": "native-replica",
                "status": "PASS",
                "metrics": {
                    "vllm:generation_tokens_total{engine=\"0\"}": generation,
                    "vllm:num_requests_running{engine=\"0\"}": running,
                },
                "generation_tokens": generation,
                "running": running,
                "waiting": 0.0,
                "kv_cache_usage_perc": 0.2,
            },
        },
        "gpu": [{"index": 0, "uuid": "GPU-0", "memory_used_mib": 100,
                 "memory_total_mib": 1000, "utilization_gpu_pct": gpu_util}],
    }


def test_resource_trace_contains_identity_samples_deltas_and_statistics() -> None:
    module = _module()
    result = module._build_resource_evidence(
        cell_id="h0-r0-A",
        attempt_id="attempt-1",
        namespace="namespace-1",
        endpoint_identity={"native-replica": {"port": 18200, "physical_gpu": 0}},
        construction_start=_snapshot(1.0, 10, 1, 10),
        periodic_samples=[_snapshot(2.0, 15, 2, 20)],
        construction_end=_snapshot(3.0, 25, 0, 30),
    )
    assert result["status"] == "PASS"
    assert result["cell_id"] == "h0-r0-A"
    assert result["attempt_id"] == "attempt-1"
    assert result["namespace"] == "namespace-1"
    assert len(result["samples"]) == 3
    assert result["construction_start"]["captured_unix"] == 1.0
    assert result["construction_end"]["captured_unix"] == 3.0
    assert result["counter_delta"]["native-replica"]["generation_tokens"] == 15.0
    assert result["statistics"]["native-replica"]["generation_tokens"]["peak"] == 25.0
    assert result["statistics"]["native-replica"]["generation_tokens"]["mean"] == 50 / 3
    assert result["sampling_missingness_rate"] == 0.0
    assert result["endpoint_identity"]["native-replica"]["physical_gpu"] == 0
