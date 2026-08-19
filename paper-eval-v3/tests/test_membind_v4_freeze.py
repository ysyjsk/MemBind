"""TDD contracts for the one-way v4 method freeze."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.membind_v4.autoresearch import CandidateStore, assess_candidate
from paper_eval.membind_v4.freeze import (
    FORMAL_HISTORY_IDS,
    V4FreezeError,
    build_frozen_method,
    verify_frozen_method,
)


def _sealed(path: Path, **fields: object) -> Path:
    body = {"schema_version": "fixture.v1", "status": "PASS", **fields}
    body["payload_sha256"] = payload_sha256(body)
    atomic_write_json(path, body)
    return path


def _candidate(
    tmp_path: Path,
    *,
    source_count: int = 12,
    decision: str = "FREEZE",
    runner_mode: str = "live",
    launch: bool = True,
) -> Path:
    store = CandidateStore.create(tmp_path / "run", "c01", source_count=source_count)
    if launch:
        store.event("speculation_launched", source_sequence=1)
    store.event("speculation_overlap", source_sequence=1)
    store.event("semantic_hit", source_sequence=1)
    summary = store.finalize(
        status="PASS",
        history_id="07741c45",
        runner_mode=runner_mode,
        direct_violation_count=0,
        freshness_p95_ratio=0.90,
        hidden_critical_time_ns=1,
    )
    mechanism_fields = (
        "qualified_node_resolve_count",
        "speculation_launch_count",
        "exact_validation_completed_count",
        "semantic_hit_count",
        "semantic_miss_count",
        "overlap_count",
        "hidden_critical_time_ns",
        "direct_violation_count",
    )
    mechanism = {field: summary.get(field, 0) for field in mechanism_fields}
    performance = {"freshness_p95_ratio": 0.90, "makespan_ratio": 1.0}
    assessed = assess_candidate({**summary, **performance})
    persisted_decision = (
        assessed
        if decision == assessed.get("decision")
        else {"decision": decision, "reason": "fixture_override"}
    )
    reduction = {
        "schema_version": "membind.paper-eval-v4.candidate-reduction.v1",
        "candidate_id": "c01",
        "source_count": source_count,
        "status": "PASS",
        "mechanism": mechanism,
        "performance": performance,
        "decision": persisted_decision,
    }
    reduction["payload_sha256"] = payload_sha256(reduction)
    atomic_write_json(store.root / "reduction.json", reduction)
    return store.root


def test_freeze_binds_candidate_evidence_and_exact_formal_histories(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    p0 = _sealed(tmp_path / "BASELINE_BINDING.json")
    role = _sealed(tmp_path / "ROLE_PROFILE.json")
    prefix = _sealed(tmp_path / "PREFIX_REFERENCE.json")
    result = build_frozen_method(
        candidate_root=candidate,
        baseline_binding_path=p0,
        role_profile_path=role,
        prefix_reference_path=prefix,
        output_root=tmp_path / "frozen",
        code_commit="a" * 40,
        focused_test={"status": "PASS", "passed": 51},
    )
    assert result["status"] == "FROZEN"
    assert tuple(result["formal_history_ids"]) == FORMAL_HISTORY_IDS
    assert result["thresholds"]["global_k"] == 2
    assert (tmp_path / "frozen" / "V4_FROZEN_METHOD.md").is_file()
    assert verify_frozen_method(tmp_path / "frozen" / "V4_FROZEN_METHOD.json") == result


def test_freeze_rejects_six_source_or_non_freeze_candidate(tmp_path: Path) -> None:
    evidence = {
        "baseline_binding_path": _sealed(tmp_path / "BASELINE_BINDING.json"),
        "role_profile_path": _sealed(tmp_path / "ROLE_PROFILE.json"),
        "prefix_reference_path": _sealed(tmp_path / "PREFIX_REFERENCE.json"),
        "output_root": tmp_path / "frozen",
    }
    with pytest.raises(V4FreezeError, match="freeze_requires_decision_prefix"):
        build_frozen_method(candidate_root=_candidate(tmp_path / "six", source_count=6), **evidence)
    with pytest.raises(V4FreezeError, match="candidate_not_freeze"):
        build_frozen_method(candidate_root=_candidate(tmp_path / "stop", decision="STOP"), **evidence)
    with pytest.raises(V4FreezeError, match="freeze_requires_live_candidate"):
        build_frozen_method(
            candidate_root=_candidate(tmp_path / "fixture", runner_mode="fixture"),
            **evidence,
        )


def test_freeze_recomputes_and_rejects_forged_freeze_label(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path, launch=False, decision="FREEZE")

    with pytest.raises(V4FreezeError, match="candidate_decision_drift"):
        build_frozen_method(
            candidate_root=candidate,
            baseline_binding_path=_sealed(tmp_path / "BASELINE_BINDING.json"),
            role_profile_path=_sealed(tmp_path / "ROLE_PROFILE.json"),
            prefix_reference_path=_sealed(tmp_path / "PREFIX_REFERENCE.json"),
            output_root=tmp_path / "frozen",
        )


@pytest.mark.parametrize("section", ("mechanism", "performance"))
def test_freeze_rejects_reduction_evidence_drift(
    tmp_path: Path,
    section: str,
) -> None:
    candidate = _candidate(tmp_path)
    path = candidate / "reduction.json"
    reduction = json.loads(path.read_text(encoding="utf-8"))
    if section == "mechanism":
        reduction["mechanism"]["exact_validation_completed_count"] = 0
    else:
        reduction["performance"]["freshness_p95_ratio"] = 1.0
    reduction.pop("payload_sha256")
    reduction["payload_sha256"] = payload_sha256(reduction)
    atomic_write_json(path, reduction)

    with pytest.raises(
        V4FreezeError,
        match=(
            "candidate_mechanism_evidence_drift"
            if section == "mechanism"
            else "candidate_decision_drift"
        ),
    ):
        build_frozen_method(
            candidate_root=candidate,
            baseline_binding_path=_sealed(tmp_path / "BASELINE_BINDING.json"),
            role_profile_path=_sealed(tmp_path / "ROLE_PROFILE.json"),
            prefix_reference_path=_sealed(tmp_path / "PREFIX_REFERENCE.json"),
            output_root=tmp_path / "frozen",
        )


def test_freeze_requires_completed_candidate_manifest(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    path = candidate / "candidate.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.pop("payload_sha256")
    manifest["status"] = "RUNNING"
    manifest["payload_sha256"] = payload_sha256(manifest)
    atomic_write_json(path, manifest)

    with pytest.raises(V4FreezeError, match="candidate_manifest_not_completed"):
        build_frozen_method(
            candidate_root=candidate,
            baseline_binding_path=_sealed(tmp_path / "BASELINE_BINDING.json"),
            role_profile_path=_sealed(tmp_path / "ROLE_PROFILE.json"),
            prefix_reference_path=_sealed(tmp_path / "PREFIX_REFERENCE.json"),
            output_root=tmp_path / "frozen",
        )


def test_frozen_method_detects_tampering(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    result = build_frozen_method(
        candidate_root=candidate,
        baseline_binding_path=_sealed(tmp_path / "BASELINE_BINDING.json"),
        role_profile_path=_sealed(tmp_path / "ROLE_PROFILE.json"),
        prefix_reference_path=_sealed(tmp_path / "PREFIX_REFERENCE.json"),
        output_root=tmp_path / "frozen",
    )
    path = tmp_path / "frozen" / "V4_FROZEN_METHOD.json"
    result["thresholds"]["global_k"] = 3
    path.write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(V4FreezeError, match="frozen_method_payload_hash_mismatch"):
        verify_frozen_method(path)
