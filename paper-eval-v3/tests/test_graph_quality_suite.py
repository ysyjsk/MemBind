"""RED-first contracts for the resumable three-method quality overlay."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.graph_quality_suite import (
    GraphQualityDiscoveryHooks,
    GraphQualityTarget,
    GraphQualitySuiteError,
    discover_graph_quality_targets,
    graph_quality_targets_sha256,
    load_or_restore_question_bundle,
    persist_question_bundle,
    run_graph_quality_targets,
    summarize_graph_quality_results,
    verify_public_target,
    verify_target_inventory,
)
from paper_eval.graph_quality_overlay import GraphQualityQuestionResult


METHODS = ("U0", "A0", "P(C=2)")
HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
RUNTIME_IDENTITY = {
    "implementation": "offline-test-runtime",
    "embedding": {"served_model_id": "test-embedding", "sdk_hidden_retries": 0},
}
QUALITY_IDENTITY = {
    "retrieval_config_sha256": "a" * 64,
    "reader_config_sha256": "b" * 64,
    "judge_config_sha256": "c" * 64,
}


def _target(method: str, history_id: str) -> GraphQualityTarget:
    return GraphQualityTarget(
        method=method,
        history_id=history_id,
        namespace=f"namespace-{method}-{history_id}",
        episode_count=49,
        construction_result_sha256=(
            f"{METHODS.index(method) * len(HISTORIES) + HISTORIES.index(history_id) + 1:064x}"
        ),
    )


def _private() -> dict[str, object]:
    return {
        "schema_version": "private.v1",
        "question": "PRIVATE QUESTION",
        "reader_answer": "PRIVATE ANSWER",
    }


def _public(
    method: str = "U0",
    history_id: str = "07741c45",
    *,
    qa: float | None = 1.0,
    denominator: int = 1,
    construction_result_sha256: str | None = None,
    overlay_run_id: str = "gq-dev-001",
    namespace: str | None = None,
    runtime_identity: dict[str, object] | None = None,
    quality_identity: dict[str, str] | None = None,
) -> dict[str, object]:
    private = _private()
    runtime = runtime_identity or RUNTIME_IDENTITY
    target_namespace = namespace or f"namespace-{method}-{history_id}"
    value: dict[str, object] = {
        "schema_version": "membind.paper-eval-v3.graph-quality-public.v1",
        "overlay_run_id": overlay_run_id,
        "method": method,
        "history_id": history_id,
        "namespace_sha256": hashlib.sha256(
            target_namespace.encode("utf-8")
        ).hexdigest(),
        "construction_result_sha256": construction_result_sha256 or "e" * 64,
        "runtime_identity": runtime,
        "runtime_identity_sha256": payload_sha256(runtime),
        "quality_identity": quality_identity or QUALITY_IDENTITY,
        "qa_accuracy": qa,
        "judge_valid_denominator": denominator,
        "headline_eligible": bool(denominator),
        "edge_attributed_source_coverage_at_10": 1.0,
        "private_artifact_sha256": payload_sha256(private),
    }
    value["payload_sha256"] = payload_sha256(value)
    return value


def test_target_inventory_is_exact_method_major_twelve_units() -> None:
    targets = [_target(method, history) for method in METHODS for history in HISTORIES]

    observed = verify_target_inventory(targets)

    assert tuple((item.method, item.history_id) for item in observed) == tuple(
        (method, history) for method in METHODS for history in HISTORIES
    )
    with pytest.raises(GraphQualitySuiteError, match="inventory"):
        verify_target_inventory(targets[:-1])
    with pytest.raises(GraphQualitySuiteError, match="inventory"):
        verify_target_inventory([*targets, targets[0]])


def test_public_target_helpers_freeze_inventory_and_bundle_identity() -> None:
    targets = [_target(method, history) for method in METHODS for history in HISTORIES]
    first = targets[0]
    public = _public(
        first.method,
        first.history_id,
        construction_result_sha256=first.construction_result_sha256,
    )

    verified = verify_public_target(
        public,
        first,
        overlay_run_id="gq-dev-001",
        runtime_identity=RUNTIME_IDENTITY,
        quality_identity=QUALITY_IDENTITY,
    )
    first_hash = graph_quality_targets_sha256(targets)
    second_hash = graph_quality_targets_sha256(tuple(targets))

    assert verified == public
    assert first_hash == second_hash
    assert len(first_hash) == 64

    changed = list(targets)
    changed[0] = GraphQualityTarget(
        method=first.method,
        history_id=first.history_id,
        namespace="different-namespace",
        episode_count=first.episode_count,
        construction_result_sha256=first.construction_result_sha256,
    )
    assert graph_quality_targets_sha256(changed) != first_hash
    with pytest.raises(GraphQualitySuiteError, match="inventory"):
        graph_quality_targets_sha256(targets[:-1])

    stale = dict(public)
    stale["overlay_run_id"] = "gq-stale-001"
    stale["payload_sha256"] = payload_sha256(
        {key: value for key, value in stale.items() if key != "payload_sha256"}
    )
    with pytest.raises(GraphQualitySuiteError, match="target identity"):
        verify_public_target(
            stale,
            first,
            overlay_run_id="gq-dev-001",
            runtime_identity=RUNTIME_IDENTITY,
            quality_identity=QUALITY_IDENTITY,
        )


def test_discovery_requires_one_verified_completed_attempt_per_live_block(
    tmp_path: Path,
) -> None:
    native_run = "nb-discovery-001"
    suite_run = "bs-discovery-001"
    native_root = tmp_path / "native"
    suite_root = tmp_path / "suite"
    u0_rows = [
        {
            "history_id": history_id,
            "episode_count": 40 + index,
            "history_result_payload_sha256": f"{index + 1:064x}",
        }
        for index, history_id in enumerate(HISTORIES)
    ]

    def verify_u0(_root: Path, run_id: str) -> dict[str, object]:
        assert run_id == native_run
        return {
            "source_run_id": native_run,
            "histories": u0_rows,
            "payload_sha256": "f" * 64,
        }

    def inspect_block(root: Path, block: dict[str, object]) -> dict[str, object]:
        assert root.name == "attempt-001"
        history_index = HISTORIES.index(str(block["history_id"]))
        payload = {
            "run_id": block["namespace"],
            "method": block["method"],
            "history_id": block["history_id"],
            "status": "PASS",
            "episode_count": 40 + history_index,
        }
        return {
            "block": block,
            "status": "completed",
            "artifacts_verified": True,
            "result": {
                "payload": payload,
                "result_payload_sha256": (
                    f"{100 + history_index + (0 if block['method'] == 'A0' else 10):064x}"
                ),
            },
        }

    for method_slug in ("a0", "pc2"):
        for history_id in HISTORIES:
            (suite_root / suite_run / "blocks" / method_slug / history_id / "attempt-001").mkdir(
                parents=True
            )
    suite_report: dict[str, object] = {
        "schema_version": "membind.paper-eval-v3.three-baseline-report.v1",
        "run_id": suite_run,
        "status": "PASS",
        "execution_order": ["U0_REUSED", "A0", "P(C=2)"],
        "fairness": {"quality_identity_verified": True},
        "u0": {
            "source_run_id": native_run,
            "payload_sha256": "f" * 64,
        },
        "blocks": [
            {
                "method": method,
                "history_id": history_id,
                "episode_count": 40 + HISTORIES.index(history_id),
                "result_payload_sha256": (
                    f"{100 + HISTORIES.index(history_id) + (0 if method == 'A0' else 10):064x}"
                ),
            }
            for method in ("A0", "P(C=2)")
            for history_id in HISTORIES
        ],
    }
    suite_report["payload_sha256"] = payload_sha256(suite_report)
    report_path = suite_root / suite_run / "THREE_BASELINE_RESULTS.json"
    report_path.write_text(json.dumps(suite_report), encoding="utf-8")

    targets = discover_graph_quality_targets(
        native_runs_root=native_root,
        suite_runs_root=suite_root,
        native_run_id=native_run,
        suite_run_id=suite_run,
        hooks=GraphQualityDiscoveryHooks(
            verify_u0=verify_u0,
            inspect_block=inspect_block,
        ),
    )

    assert len(targets) == 12
    assert [target.method for target in targets[:5]] == [
        "U0",
        "U0",
        "U0",
        "U0",
        "A0",
    ]
    assert targets[4].namespace.endswith("-a001")

    missing = suite_root / suite_run / "blocks" / "pc2" / HISTORIES[-1] / "attempt-001"
    missing.rmdir()
    with pytest.raises(GraphQualitySuiteError, match="not complete"):
        discover_graph_quality_targets(
            native_runs_root=native_root,
            suite_runs_root=suite_root,
            native_run_id=native_run,
            suite_run_id=suite_run,
            hooks=GraphQualityDiscoveryHooks(
                verify_u0=verify_u0,
                inspect_block=inspect_block,
            ),
        )

    report_path.unlink()
    with pytest.raises(GraphQualitySuiteError, match="suite report"):
        discover_graph_quality_targets(
            native_runs_root=native_root,
            suite_runs_root=suite_root,
            native_run_id=native_run,
            suite_run_id=suite_run,
            hooks=GraphQualityDiscoveryHooks(
                verify_u0=verify_u0,
                inspect_block=inspect_block,
            ),
        )


def test_private_bundle_is_written_first_and_public_can_be_restored(
    tmp_path: Path,
) -> None:
    private = _private()
    public = _public()
    attempt = tmp_path / "attempt-001"

    persisted = persist_question_bundle(
        attempt,
        public_artifact=public,
        private_artifact=private,
    )

    assert persisted == public
    bundle = json.loads((attempt / "private_bundle.json").read_text())
    assert bundle["private_artifact"] == private
    assert json.loads((attempt / "public.json").read_text()) == public

    (attempt / "public.json").unlink()
    restored = load_or_restore_question_bundle(attempt)
    assert restored == public
    assert json.loads((attempt / "public.json").read_text()) == public


def test_bundle_rejects_mismatched_private_hash_or_existing_drift(
    tmp_path: Path,
) -> None:
    private = _private()
    public = _public()
    public["private_artifact_sha256"] = "d" * 64
    public["payload_sha256"] = payload_sha256(
        {key: value for key, value in public.items() if key != "payload_sha256"}
    )
    with pytest.raises(GraphQualitySuiteError, match="private"):
        persist_question_bundle(
            tmp_path / "attempt-001",
            public_artifact=public,
            private_artifact=private,
        )

    valid = _public()
    persist_question_bundle(
        tmp_path / "attempt-002",
        public_artifact=valid,
        private_artifact=private,
    )
    changed = dict(valid)
    changed["qa_accuracy"] = 0.0
    changed["payload_sha256"] = payload_sha256(
        {key: value for key, value in changed.items() if key != "payload_sha256"}
    )
    with pytest.raises(GraphQualitySuiteError, match="existing"):
        persist_question_bundle(
            tmp_path / "attempt-002",
            public_artifact=changed,
            private_artifact=private,
        )


def test_summary_excludes_invalid_judge_from_denominator_and_requires_identity() -> None:
    rows = []
    for method in METHODS:
        for index, history_id in enumerate(HISTORIES):
            rows.append(
                _public(
                    method,
                    history_id,
                    qa=None if index == 3 else (1.0 if index == 0 else 0.0),
                    denominator=0 if index == 3 else 1,
                )
            )

    summary = summarize_graph_quality_results(rows)

    assert summary["question_count"] == 12
    assert summary["valid_judge_count"] == 9
    assert summary["invalid_judge_count"] == 3
    assert summary["qa_accuracy_micro"] == pytest.approx(1 / 3)
    assert summary["edge_attributed_source_coverage_at_10_macro"] == 1.0
    assert summary["claim_label"] == (
        "PROTOCOL_RUBRIC_COMPATIBLE_LOCAL_JUDGE_DIAGNOSTIC"
    )

    rows[-1] = dict(rows[-1])
    rows[-1]["quality_identity"] = {
        **dict(rows[-1]["quality_identity"]),
        "reader_config_sha256": "f" * 64,
    }
    rows[-1]["payload_sha256"] = payload_sha256(
        {key: value for key, value in rows[-1].items() if key != "payload_sha256"}
    )
    with pytest.raises(GraphQualitySuiteError, match="identity"):
        summarize_graph_quality_results(rows)


@pytest.mark.asyncio
async def test_runner_checkpoints_each_unit_and_replay_does_not_resample(
    tmp_path: Path,
) -> None:
    targets = [_target(method, history) for method in METHODS for history in HISTORIES]
    calls: list[tuple[str, str]] = []

    async def evaluate(
        target: GraphQualityTarget, attempt_root: Path
    ) -> GraphQualityQuestionResult:
        calls.append((target.method, target.history_id))
        assert attempt_root.name == "attempt-001"
        return GraphQualityQuestionResult(
            public_artifact=_public(
                target.method,
                target.history_id,
                construction_result_sha256=target.construction_result_sha256,
            ),
            private_artifact=_private(),
        )

    report = await run_graph_quality_targets(
        overlay_run_id="gq-dev-001",
        targets=targets,
        run_root=tmp_path / "gq-dev-001",
        evaluate=evaluate,
        runtime_identity=RUNTIME_IDENTITY,
        quality_identity=QUALITY_IDENTITY,
    )

    assert report["status"] == "PASS"
    assert len(calls) == 12
    assert report["summary"]["question_count"] == 12
    assert json.loads((tmp_path / "gq-dev-001" / "progress.json").read_text())[
        "completed_unit_count"
    ] == 12

    async def forbidden_resample(
        _target: GraphQualityTarget, _attempt_root: Path
    ) -> GraphQualityQuestionResult:
        raise AssertionError("completed overlay unit was resampled")

    replay = await run_graph_quality_targets(
        overlay_run_id="gq-dev-001",
        targets=targets,
        run_root=tmp_path / "gq-dev-001",
        evaluate=forbidden_resample,
        runtime_identity=RUNTIME_IDENTITY,
        quality_identity=QUALITY_IDENTITY,
    )
    assert replay == report


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift",
    ["schema", "overlay", "namespace", "runtime", "quality"],
)
async def test_restored_bundle_requires_complete_run_and_runtime_binding(
    tmp_path: Path,
    drift: str,
) -> None:
    targets = [_target(method, history) for method in METHODS for history in HISTORIES]
    target = targets[0]
    public = _public(
        target.method,
        target.history_id,
        construction_result_sha256=target.construction_result_sha256,
    )
    if drift == "schema":
        public["schema_version"] = "stale.public.v0"
    elif drift == "overlay":
        public["overlay_run_id"] = "gq-copied-999"
    elif drift == "namespace":
        public["namespace_sha256"] = "9" * 64
    elif drift == "runtime":
        stale_runtime = {"implementation": "copied-runtime"}
        public["runtime_identity"] = stale_runtime
        public["runtime_identity_sha256"] = payload_sha256(stale_runtime)
    elif drift == "quality":
        public["quality_identity"] = {
            **QUALITY_IDENTITY,
            "reader_config_sha256": "9" * 64,
        }
    public["payload_sha256"] = payload_sha256(
        {key: value for key, value in public.items() if key != "payload_sha256"}
    )
    attempt = (
        tmp_path
        / "gq-dev-001"
        / "units"
        / "u0"
        / HISTORIES[0]
        / "attempt-001"
    )
    if drift == "schema":
        bundle: dict[str, object] = {
            "schema_version": "membind.paper-eval-v3.graph-quality-bundle.v1",
            "public_artifact": public,
            "private_artifact": _private(),
        }
        bundle["bundle_sha256"] = payload_sha256(bundle)
        attempt.mkdir(parents=True)
        (attempt / "private_bundle.json").write_text(
            json.dumps(bundle), encoding="utf-8"
        )
    else:
        persist_question_bundle(
            attempt,
            public_artifact=public,
            private_artifact=_private(),
        )

    async def forbidden(
        _target: GraphQualityTarget, _attempt_root: Path
    ) -> GraphQualityQuestionResult:
        raise AssertionError("stale bundle reached live evaluation")

    with pytest.raises(GraphQualitySuiteError, match="identity|schema"):
        await run_graph_quality_targets(
            overlay_run_id="gq-dev-001",
            targets=targets,
            run_root=tmp_path / "gq-dev-001",
            evaluate=forbidden,
            runtime_identity=RUNTIME_IDENTITY,
            quality_identity=QUALITY_IDENTITY,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_index", "failure_kind"),
    [(5, "stale_identity"), (11, "corrupt_bundle")],
)
async def test_all_existing_attempts_fail_closed_before_any_live_evaluation(
    tmp_path: Path,
    target_index: int,
    failure_kind: str,
) -> None:
    targets = [_target(method, history) for method in METHODS for history in HISTORIES]
    target = targets[target_index]
    method_slug = {"U0": "u0", "A0": "a0", "P(C=2)": "pc2"}[target.method]
    run_root = tmp_path / "gq-preflight-001"
    attempt_root = (
        run_root
        / "units"
        / method_slug
        / target.history_id
        / "attempt-001"
    )
    if failure_kind == "stale_identity":
        stale_runtime = {"implementation": "stale-runtime"}
        persist_question_bundle(
            attempt_root,
            public_artifact=_public(
                target.method,
                target.history_id,
                construction_result_sha256=target.construction_result_sha256,
                overlay_run_id="gq-preflight-001",
                runtime_identity=stale_runtime,
            ),
            private_artifact=_private(),
        )
    else:
        attempt_root.mkdir(parents=True)
        (attempt_root / "private_bundle.json").write_text(
            "{corrupt-bundle",
            encoding="utf-8",
        )

    calls: list[tuple[str, str]] = []

    async def evaluate(
        current: GraphQualityTarget,
        _attempt_root: Path,
    ) -> GraphQualityQuestionResult:
        calls.append((current.method, current.history_id))
        return GraphQualityQuestionResult(
            public_artifact=_public(
                current.method,
                current.history_id,
                construction_result_sha256=current.construction_result_sha256,
                overlay_run_id="gq-preflight-001",
            ),
            private_artifact=_private(),
        )

    with pytest.raises(GraphQualitySuiteError, match="identity|unreadable"):
        await run_graph_quality_targets(
            overlay_run_id="gq-preflight-001",
            targets=targets,
            run_root=run_root,
            evaluate=evaluate,
            runtime_identity=RUNTIME_IDENTITY,
            quality_identity=QUALITY_IDENTITY,
        )

    assert calls == []
    first_target = targets[0]
    assert not (
        run_root
        / "units"
        / "u0"
        / first_target.history_id
        / "attempt-001"
        / "target.json"
    ).exists()


@pytest.mark.asyncio
async def test_all_recoverable_public_artifacts_are_restored_before_live_evaluation(
    tmp_path: Path,
) -> None:
    targets = [_target(method, history) for method in METHODS for history in HISTORIES]
    recovered_target = targets[-1]
    run_root = tmp_path / "gq-preflight-002"
    recovered_attempt = (
        run_root
        / "units"
        / "pc2"
        / recovered_target.history_id
        / "attempt-001"
    )
    persist_question_bundle(
        recovered_attempt,
        public_artifact=_public(
            recovered_target.method,
            recovered_target.history_id,
            construction_result_sha256=(
                recovered_target.construction_result_sha256
            ),
            overlay_run_id="gq-preflight-002",
        ),
        private_artifact=_private(),
    )
    (recovered_attempt / "public.json").unlink()
    calls: list[tuple[str, str]] = []

    async def evaluate(
        target: GraphQualityTarget,
        _attempt_root: Path,
    ) -> GraphQualityQuestionResult:
        assert (recovered_attempt / "public.json").is_file()
        calls.append((target.method, target.history_id))
        return GraphQualityQuestionResult(
            public_artifact=_public(
                target.method,
                target.history_id,
                construction_result_sha256=target.construction_result_sha256,
                overlay_run_id="gq-preflight-002",
            ),
            private_artifact=_private(),
        )

    report = await run_graph_quality_targets(
        overlay_run_id="gq-preflight-002",
        targets=targets,
        run_root=run_root,
        evaluate=evaluate,
        runtime_identity=RUNTIME_IDENTITY,
        quality_identity=QUALITY_IDENTITY,
    )

    assert report["status"] == "PASS"
    assert len(calls) == 11
    assert report["units"][-1]["attempt_ordinal"] == 1


@pytest.mark.asyncio
async def test_failed_unit_is_nonmergeable_and_retry_uses_new_attempt(
    tmp_path: Path,
) -> None:
    targets = [_target(method, history) for method in METHODS for history in HISTORIES]

    async def fail(
        _target: GraphQualityTarget, _attempt_root: Path
    ) -> GraphQualityQuestionResult:
        raise ConnectionError("PRIVATE endpoint and secret must not persist")

    with pytest.raises(ConnectionError):
        await run_graph_quality_targets(
            overlay_run_id="gq-dev-002",
            targets=targets,
            run_root=tmp_path / "gq-dev-002",
            evaluate=fail,
            runtime_identity=RUNTIME_IDENTITY,
            quality_identity=QUALITY_IDENTITY,
        )
    first = (
        tmp_path
        / "gq-dev-002"
        / "units"
        / "u0"
        / HISTORIES[0]
        / "attempt-001"
    )
    failure = json.loads((first / "failure.json").read_text())
    assert failure["status"] == "incomplete_non_mergeable"
    assert failure["error_class"] == "builtins.ConnectionError"
    assert "PRIVATE" not in json.dumps(failure)

    async def succeed(
        target: GraphQualityTarget, attempt_root: Path
    ) -> GraphQualityQuestionResult:
        assert attempt_root.name in {"attempt-001", "attempt-002"}
        return GraphQualityQuestionResult(
            public_artifact=_public(
                target.method,
                target.history_id,
                construction_result_sha256=target.construction_result_sha256,
                overlay_run_id="gq-dev-002",
            ),
            private_artifact=_private(),
        )

    report = await run_graph_quality_targets(
        overlay_run_id="gq-dev-002",
        targets=targets,
        run_root=tmp_path / "gq-dev-002",
        evaluate=succeed,
        runtime_identity=RUNTIME_IDENTITY,
        quality_identity=QUALITY_IDENTITY,
    )
    assert report["status"] == "PASS"
    assert (first.parent / "attempt-002" / "public.json").is_file()


@pytest.mark.asyncio
async def test_same_overlay_run_cannot_execute_concurrently(tmp_path: Path) -> None:
    targets = [_target(method, history) for method in METHODS for history in HISTORIES]
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow(
        target: GraphQualityTarget, _attempt_root: Path
    ) -> GraphQualityQuestionResult:
        entered.set()
        await release.wait()
        return GraphQualityQuestionResult(
            public_artifact=_public(
                target.method,
                target.history_id,
                construction_result_sha256=target.construction_result_sha256,
                overlay_run_id="gq-lock-001",
            ),
            private_artifact=_private(),
        )

    first = asyncio.create_task(
        run_graph_quality_targets(
            overlay_run_id="gq-lock-001",
            targets=targets,
            run_root=tmp_path / "gq-lock-001",
            evaluate=slow,
            runtime_identity=RUNTIME_IDENTITY,
            quality_identity=QUALITY_IDENTITY,
        )
    )
    await entered.wait()
    with pytest.raises(GraphQualitySuiteError, match="already running"):
        await run_graph_quality_targets(
            overlay_run_id="gq-lock-001",
            targets=targets,
            run_root=tmp_path / "gq-lock-001",
            evaluate=slow,
            runtime_identity=RUNTIME_IDENTITY,
            quality_identity=QUALITY_IDENTITY,
        )
    release.set()
    assert (await first)["status"] == "PASS"
