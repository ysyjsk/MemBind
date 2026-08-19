"""Offline TDD contracts for the v4 candidate production runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.membind_v31.certification import StateCutCertification
from paper_eval.membind_v31.method_plan import verify_membind_v31_method_plan
from paper_eval.membind_v31.production_executor import ProductionExecutorPaths
from paper_eval.membind_v4.autoresearch import (
    CandidateStore,
    assess_candidate,
    candidate_config,
)
from paper_eval.membind_v4.production_runner import (
    V4ProductionRunnerError,
    build_v4_candidate_live_runner,
    build_v4_candidate_plan,
    verify_prior_six_reduction,
)
from paper_eval.membind_v4.live_block import V4ProductionLoaders


def _canonical_plan() -> dict[str, object]:
    project = Path(__file__).resolve().parents[1]
    return json.loads(
        (project / "artifacts/paper_eval/membind_v31/V31_METHOD_PLAN.json").read_text(
            encoding="utf-8"
        )
    )


def _sealed(path: Path, body: dict[str, object]) -> Path:
    selected = dict(body)
    selected["payload_sha256"] = payload_sha256(selected)
    atomic_write_json(path, selected)
    return path


def _prior_six(
    tmp_path: Path,
    *,
    candidate_id: str = "c01",
    history_id: str = "07741c45",
    policy: str | None = None,
    decision: str = "EXTEND_TO_12",
) -> Path:
    root = tmp_path / "prior/candidates/c01"
    config = candidate_config("c01")
    candidate = {
        **config,
        "candidate_id": candidate_id,
        "policy": config["policy"] if policy is None else policy,
        "status": "COMPLETED",
        "source_count": 6,
        "created_at_ns": 1,
        "completed_at_ns": 2,
    }
    summary = {
        "schema_version": "membind.paper-eval-v4.summary.v1",
        "status": "PASS",
        "candidate_id": candidate_id,
        "source_count": 6,
        "history_id": history_id,
        "runner_mode": "live",
        "direct_violation_count": 0,
        "qualified_node_resolve_count": 1,
        "speculation_launch_count": 1,
        "exact_validation_completed_count": 1,
        "semantic_hit_count": 1,
        "semantic_miss_count": 0,
        "overlap_count": 1,
        "frontier_p95_service_ratio": 1.0,
    }
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
    performance = {"freshness_p95_ratio": 1.0, "makespan_ratio": 1.0}
    assessed = assess_candidate({**summary, **performance})
    reduction = {
        "schema_version": "membind.paper-eval-v4.candidate-reduction.v1",
        "status": "PASS",
        "candidate_id": candidate_id,
        "source_count": 6,
        "mechanism": mechanism,
        "performance": performance,
        "decision": (
            assessed
            if decision == assessed.get("decision")
            else {"decision": decision, "reason": "fixture_override"}
        ),
    }
    _sealed(root / "candidate.json", candidate)
    _sealed(root / "summary.json", summary)
    return _sealed(root / "reduction.json", reduction)


def test_prior_six_admission_accepts_only_sealed_extend_decision(tmp_path: Path) -> None:
    path = _prior_six(tmp_path)

    binding = verify_prior_six_reduction(
        path,
        candidate_id="c01",
        history_id="07741c45",
    )

    assert binding["decision"] == "EXTEND_TO_12"
    assert binding["candidate_id"] == "c01"
    assert binding["source_count"] == 6


def test_prior_six_admission_rejects_missing_or_tampered_reduction(tmp_path: Path) -> None:
    with pytest.raises(V4ProductionRunnerError, match="prior_six_reduction_unreadable"):
        verify_prior_six_reduction(
            tmp_path / "missing/reduction.json",
            candidate_id="c01",
            history_id="07741c45",
        )

    path = _prior_six(tmp_path / "tampered")
    reduction = json.loads(path.read_text(encoding="utf-8"))
    reduction["decision"] = {"decision": "STOP", "reason": "tampered"}
    path.write_text(json.dumps(reduction), encoding="utf-8")
    with pytest.raises(V4ProductionRunnerError, match="prior_six_reduction_payload_hash_mismatch"):
        verify_prior_six_reduction(
            path,
            candidate_id="c01",
            history_id="07741c45",
        )


@pytest.mark.parametrize(
    ("fixture_kwargs", "error"),
    (
        ({"candidate_id": "c02"}, "prior_six_candidate_identity_drift"),
        ({"policy": "DRIFTED"}, "prior_six_policy_drift"),
        ({"history_id": "6071bd76"}, "prior_six_history_drift"),
        ({"decision": "STOP"}, "prior_six_decision_invalid"),
    ),
)
def test_prior_six_admission_rejects_identity_policy_history_or_decision_drift(
    tmp_path: Path,
    fixture_kwargs: dict[str, str],
    error: str,
) -> None:
    path = _prior_six(tmp_path, **fixture_kwargs)

    with pytest.raises(V4ProductionRunnerError, match=error):
        verify_prior_six_reduction(
            path,
            candidate_id="c01",
            history_id="07741c45",
        )


@pytest.mark.parametrize("source_count", (6, 12))
def test_candidate_plan_is_fresh_verified_prefix_without_knob_drift(
    tmp_path: Path, source_count: int
) -> None:
    canonical = verify_membind_v31_method_plan(_canonical_plan())
    plan = build_v4_candidate_plan(
        canonical,
        candidate_id="c01",
        source_count=source_count,
        candidate_root=tmp_path / "candidates/c01",
    )

    assert verify_membind_v31_method_plan(plan) == plan
    assert plan["run_id"] != canonical["run_id"]
    assert plan["compile_workers"] == canonical["compile_workers"] == 2
    assert plan["lookahead"] == canonical["lookahead"] == 2
    assert plan["global_llm_admission_k"] == canonical["global_llm_admission_k"] == 2
    assert plan["history_source_sha256s"]["07741c45"] == canonical[
        "history_source_sha256s"
    ]["07741c45"][:source_count]
    assert plan["arrival_traces"]["07741c45"]["arrival_offsets_ns"] == canonical[
        "arrival_traces"
    ]["07741c45"]["arrival_offsets_ns"][:source_count]
    assert plan["blocks"][0]["source_count"] == source_count
    assert plan["blocks"][0]["namespace"] != canonical["blocks"][0]["namespace"]


@pytest.mark.parametrize("candidate_id,source_count", (("c02", 6), ("c01", 5), ("c01", 49)))
def test_candidate_plan_rejects_unimplemented_policy_or_non_preregistered_prefix(
    tmp_path: Path, candidate_id: str, source_count: int
) -> None:
    with pytest.raises(V4ProductionRunnerError):
        build_v4_candidate_plan(
            _canonical_plan(),
            candidate_id=candidate_id,
            source_count=source_count,
            candidate_root=tmp_path / "candidate",
        )


def test_candidate_live_runner_executes_exact_prefix_in_candidate_block_root(
    tmp_path: Path,
) -> None:
    canonical = verify_membind_v31_method_plan(_canonical_plan())
    certification = object.__new__(StateCutCertification)
    episodes = {
        history: tuple(range(len(canonical["history_source_sha256s"][history])))
        for history in canonical["histories"]
    }
    paths = ProductionExecutorPaths.from_repository(tmp_path)
    loaders = V4ProductionLoaders(
        load_plan=lambda _path: canonical,
        load_env=lambda _path: {"SAFE": "value"},
        load_certification=lambda _paths: certification,
        load_episodes=lambda _path, _plan: episodes,
    )
    calls: list[dict[str, object]] = []

    async def execute_block(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        plan = kwargs["verified_plan"]
        block = plan["blocks"][kwargs["block_index"]]
        count = len(kwargs["episodes"])
        return {
            "schema_version": "membind.paper-eval-v4.live-block-result.v1",
            "status": "PASS",
            "run_id": plan["run_id"],
            "history_id": block["history_id"],
            "namespace": block["namespace"],
            "source_count": count,
            "direct_violation_count": 0,
            "performance": {
                "makespan_ns": 100,
                "p95_freshness_ns": 20,
                "freshness_ns": list(range(count)),
            },
            "telemetry": {
                "persistent_write_count": 0,
                "events": [
                    {"event_type": "speculation_launched", "source_sequence": 1},
                    {"event_type": "speculation_overlap", "source_sequence": 1},
                    {"event_type": "semantic_hit", "source_sequence": 1},
                ],
            },
            "admission_observation": {"configured_limit": 2},
            "payload_sha256": "a" * 64,
        }

    runner = build_v4_candidate_live_runner(
        paths=paths,
        loaders=loaders,
        execute_block=execute_block,
    )
    store = CandidateStore.create(tmp_path / "run", "c01", source_count=6)
    result = runner(store=store, history_id="07741c45", source_count=6)

    assert result["status"] == "PASS"
    assert result["source_count"] == 6
    assert result["performance"]["freshness_ns"] == list(range(6))
    assert calls[0]["episodes"] == episodes["07741c45"][:6]
    assert calls[0]["block_root"] == store.root / "block"
    assert calls[0]["compile_workers"] == 2
    assert calls[0]["lookahead"] == 2
    assert calls[0]["stream_id"] == "07741c45"
    assert calls[0]["namespace_override"] is None


def test_candidate_live_runner_rejects_identity_drift_before_execution(tmp_path: Path) -> None:
    canonical = verify_membind_v31_method_plan(_canonical_plan())
    certification = object.__new__(StateCutCertification)
    episodes = {
        history: tuple(range(len(canonical["history_source_sha256s"][history])))
        for history in canonical["histories"]
    }
    paths = ProductionExecutorPaths.from_repository(tmp_path)
    loaders = V4ProductionLoaders(
        load_plan=lambda _path: canonical,
        load_env=lambda _path: {"SAFE": "value"},
        load_certification=lambda _paths: certification,
        load_episodes=lambda _path, _plan: episodes,
    )
    calls: list[dict[str, object]] = []
    runner = build_v4_candidate_live_runner(
        paths=paths,
        loaders=loaders,
        execute_block=lambda **kwargs: calls.append(kwargs),
    )
    store = CandidateStore.create(tmp_path / "run", "c01", source_count=6)

    with pytest.raises(V4ProductionRunnerError, match="candidate_history_invalid"):
        runner(store=store, history_id="6071bd76", source_count=6)
    assert calls == []


def test_candidate_live_runner_rejects_direct_twelve_source_without_prior_six(
    tmp_path: Path,
) -> None:
    canonical = verify_membind_v31_method_plan(_canonical_plan())
    certification = object.__new__(StateCutCertification)
    episodes = {
        history: tuple(range(len(canonical["history_source_sha256s"][history])))
        for history in canonical["histories"]
    }
    paths = ProductionExecutorPaths.from_repository(tmp_path)
    calls: list[dict[str, object]] = []
    runner = build_v4_candidate_live_runner(
        paths=paths,
        loaders=V4ProductionLoaders(
            load_plan=lambda _path: canonical,
            load_env=lambda _path: {"SAFE": "value"},
            load_certification=lambda _paths: certification,
            load_episodes=lambda _path, _plan: episodes,
        ),
        execute_block=lambda **kwargs: calls.append(kwargs),
    )
    store = CandidateStore.create(tmp_path / "run", "c01", source_count=12)

    with pytest.raises(V4ProductionRunnerError, match="prior_six_reduction_required"):
        runner(store=store, history_id="07741c45", source_count=12)
    assert calls == []
