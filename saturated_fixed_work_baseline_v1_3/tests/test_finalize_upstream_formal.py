from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "saturated_fixed_work_baseline_v1_3"
    / "scripts"
    / "finalize_formal_three_arm.py"
)


def _module():
    scripts = str(SCRIPT.parent)
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("finalize_formal_three_arm", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts)
    return module


def _construction(module):
    rows = []
    for history in range(5):
        for replicate in range(3):
            for index, arm in enumerate(module.ARMS):
                rows.append(
                    {
                        "history_index": history,
                        "history_id": f"history-{history}",
                        "replicate_id": replicate,
                        "arm": arm,
                        "t_build_ns": float(100 + history + replicate + index * 10),
                    }
                )
    return rows


def _quality(module):
    rows = []
    for history in range(5):
        for replicate in range(3):
            for arm in module.ARMS:
                for question in range(60):
                    rows.append(
                        {
                            "history_index": history,
                            "replicate_id": replicate,
                            "arm": arm,
                            "qa_pair_id": f"q-{question}",
                            "correct": (question + module.ARM_ORDER[arm]) % 3 != 0,
                            "question_38_anomaly": question == 38,
                        }
                    )
    return rows


def test_performance_reducer_emits_15_pairs_5_histories_and_b_ceiling() -> None:
    module = _module()
    replicate, history, ceiling = module.paired_performance(_construction(module))
    assert len(replicate) == 15
    assert len(history) == 5
    assert len(ceiling) == 15
    assert all(row["a_vs_c_ratio"] > 0 for row in replicate)
    assert all(row["b_role"].startswith("RELAXED_ORDER_CEILING") for row in ceiling)


def test_quality_reducer_emits_paired_delta_disagreement_and_q38() -> None:
    module = _module()
    pairs, summary = module.paired_quality(_quality(module))
    assert len(pairs) == 15
    assert summary["status"] == "PASS"
    assert summary["total_disagreements"] > 0
    assert summary["question_38_anomaly"]["rows"] == 45


def test_cluster_uncertainty_uses_five_history_units() -> None:
    module = _module()
    history = [
        {"a_vs_c_geometric_mean": value}
        for value in (1.1, 1.2, 1.3, 0.9, 1.0)
    ]
    result = module._cluster_bootstrap(history)
    assert result["cluster_unit"] == "official_history"
    assert result["bootstrap_resamples"] == 3125
    assert len(result["bootstrap_percentile_95_interval"]) == 2


def test_formal_finalizer_names_only_upstream_arms() -> None:
    module = _module()
    source = SCRIPT.read_text(encoding="utf-8")
    assert tuple(module.ARMS) == (
        "GRAPHITI_SERIAL_UPSTREAM_CORE_MAB8192",
        "MEMBIND_V6_1_UPSTREAM_CORE_MAB8192",
        "GRAPHITI_ASYNC_UPSTREAM_CORE_MAB8192",
    )
    assert "SHARED_BOUNDED_SO" not in source
