"""Focused smoke-gate tests for crash-safe correctness remeasurement."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from paper_eval.artifacts import atomic_write_json, payload_sha256


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts/run_apc_aligned_pipeline.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_apc_aligned_pipeline", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_gate_prefers_hash_bound_correctness_remeasurement(tmp_path: Path) -> None:
    module = _module()
    block = {
        "method": "U0-aligned",
        "payload_sha256": "a" * 64,
        "correctness": {"checker_status": "MEASURED", "direct_violations_total": 99},
    }
    amendment = {
        "schema_version": "membind.paper-eval-v3.apc-aligned-correctness-remeasurement.v1",
        "status": "PASS",
        "run_id": "apc-baseline-smoke-001",
        "entries": [
            {
                "block_index": 0,
                "method": "U0-aligned",
                "source_block_payload_sha256": "a" * 64,
                "correctness": {
                    "checker_status": "MEASURED",
                    "direct_violations_total": 0,
                },
            }
        ],
    }
    amendment["payload_sha256"] = payload_sha256(amendment)
    atomic_write_json(tmp_path / "CORRECTNESS_REMEASUREMENT.json", amendment)

    selected = module._smoke_correctness(
        root=tmp_path,
        run_id="apc-baseline-smoke-001",
        block_index=0,
        block=block,
    )

    assert selected["direct_violations_total"] == 0
