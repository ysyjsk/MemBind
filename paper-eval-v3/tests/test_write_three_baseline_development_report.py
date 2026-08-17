"""RED-first tests for actual observability projection into the report."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.graph_quality_suite import (
    GraphQualityTarget,
    graph_quality_targets_sha256,
    persist_question_bundle,
    summarize_graph_quality_results,
)


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts/write_three_baseline_development_report.py"
METHODS = ("U0", "A0", "P(C=2)")
HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
METHOD_SLUGS = {"U0": "u0", "A0": "a0", "P(C=2)": "pc2"}
CURRENT_RUNTIME = {"implementation": "current-runtime"}
OLD_RUNTIME = {"implementation": "old-runtime"}
CURRENT_QUALITY = {
    "retrieval_config_sha256": "a" * 64,
    "reader_config_sha256": "b" * 64,
    "judge_config_sha256": "c" * 64,
}
OLD_QUALITY = {
    **CURRENT_QUALITY,
    "reader_config_sha256": "d" * 64,
}


def _script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "write_three_baseline_development_report", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _targets() -> tuple[GraphQualityTarget, ...]:
    return tuple(
        GraphQualityTarget(
            method=method,
            history_id=history_id,
            namespace=f"current-{method}-{history_id}",
            episode_count=49,
            construction_result_sha256=f"{index + 1:064x}",
        )
        for index, (method, history_id) in enumerate(
            (method, history_id)
            for method in METHODS
            for history_id in HISTORIES
        )
    )


def _private() -> dict[str, object]:
    return {"schema_version": "private.v1", "answer": "PRIVATE"}


def _public(
    target: GraphQualityTarget,
    *,
    overlay_run_id: str,
    drift: str | None = None,
) -> dict[str, object]:
    private = _private()
    runtime = OLD_RUNTIME if drift == "runtime" else CURRENT_RUNTIME
    quality = OLD_QUALITY if drift == "quality" else CURRENT_QUALITY
    namespace = (
        f"old-{target.method}-{target.history_id}"
        if drift == "namespace"
        else target.namespace
    )
    construction_sha = (
        "9" * 64
        if drift == "construction"
        else target.construction_result_sha256
    )
    body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v3.graph-quality-public.v1",
        "overlay_run_id": overlay_run_id,
        "method": target.method,
        "history_id": target.history_id,
        "namespace_sha256": hashlib.sha256(namespace.encode("utf-8")).hexdigest(),
        "construction_result_sha256": construction_sha,
        "runtime_identity": runtime,
        "runtime_identity_sha256": payload_sha256(runtime),
        "quality_identity": quality,
        "qa_accuracy": 1.0,
        "judge_valid_denominator": 1,
        "headline_eligible": True,
        "edge_attributed_source_coverage_at_10": 1.0,
        "private_artifact_sha256": payload_sha256(private),
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def _prepare_graph_report(
    module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    drift: str | None = None,
    drift_targets_sha256: bool = False,
) -> tuple[GraphQualityTarget, ...]:
    overlay_run_id = "gq-report-001"
    targets = _targets()
    root = tmp_path / overlay_run_id
    expected_rows = [
        _public(target, overlay_run_id=overlay_run_id) for target in targets
    ]
    actual_rows = [
        _public(target, overlay_run_id=overlay_run_id, drift=drift)
        for target in targets
    ]
    units = []
    for target, public in zip(targets, actual_rows, strict=True):
        attempt_root = (
            root
            / "units"
            / METHOD_SLUGS[target.method]
            / target.history_id
            / "attempt-001"
        )
        persist_question_bundle(
            attempt_root,
            public_artifact=public,
            private_artifact=_private(),
        )
        units.append(
            {
                "method": target.method,
                "history_id": target.history_id,
                "attempt_ordinal": 1,
                "public_payload_sha256": public["payload_sha256"],
                "qa_accuracy": 1.0,
                "judge_valid_denominator": 1,
                "edge_attributed_source_coverage_at_10": 1.0,
            }
        )
    report: dict[str, object] = {
        "schema_version": "membind.paper-eval-v3.graph-quality-report.v1",
        "overlay_run_id": overlay_run_id,
        "status": "PASS",
        "target_count": 12,
        "targets_sha256": (
            "f" * 64
            if drift_targets_sha256
            else graph_quality_targets_sha256(targets)
        ),
        "summary": summarize_graph_quality_results(expected_rows),
        "units": units,
    }
    report["payload_sha256"] = payload_sha256(report)
    root.mkdir(parents=True, exist_ok=True)
    (root / "GRAPH_QUALITY_RESULTS.json").write_text(
        json.dumps(report),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "GRAPH_QUALITY_RUNS_ROOT", tmp_path)
    monkeypatch.setattr(
        module,
        "discover_graph_quality_targets",
        lambda **_kwargs: targets,
    )
    return targets


def test_freshness_projection_uses_the_real_unified_observability_shape() -> None:
    module = _script_module()

    observed = module._freshness_samples(
        [
            {"latency_ns": {"freshness": 11}},
            {"latency_ns": {"freshness": 22}},
        ],
        expected_count=2,
    )

    assert observed == [11, 22]


def test_graph_report_rejects_targets_hash_drift_before_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _script_module()
    _prepare_graph_report(
        module,
        tmp_path,
        monkeypatch,
        drift_targets_sha256=True,
    )
    monkeypatch.setattr(
        module,
        "summarize_graph_quality_results",
        lambda _rows: (_ for _ in ()).throw(
            AssertionError("summary ran before target hash verification")
        ),
    )

    with pytest.raises(module.ReportInputError, match="target.*hash|inventory"):
        module._verified_graph_quality_report(
            overlay_run_id="gq-report-001",
            native_run_id="nb-test",
            suite_run_id="bs-test",
        )


def test_graph_report_accepts_twelve_fully_rebound_bundles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _script_module()
    _prepare_graph_report(module, tmp_path, monkeypatch)

    report = module._verified_graph_quality_report(
        overlay_run_id="gq-report-001",
        native_run_id="nb-test",
        suite_run_id="bs-test",
    )

    assert report["status"] == "PASS"
    assert report["target_count"] == 12


@pytest.mark.parametrize(
    "drift",
    ["namespace", "construction", "runtime", "quality"],
)
def test_complete_old_bundles_fail_before_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    module = _script_module()
    _prepare_graph_report(module, tmp_path, monkeypatch, drift=drift)
    monkeypatch.setattr(
        module,
        "summarize_graph_quality_results",
        lambda _rows: (_ for _ in ()).throw(
            AssertionError("summary ran before bundle identity verification")
        ),
    )

    with pytest.raises(module.ReportInputError, match="target.*identity"):
        module._verified_graph_quality_report(
            overlay_run_id="gq-report-001",
            native_run_id="nb-test",
            suite_run_id="bs-test",
        )
