"""TDD tests for durable M* runner identity and publication composition."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s5_durable_attempt_store import inspect_s5_attempt
from paper_eval.s5_mstar_pipeline import MStarSource, MStarSpec
from paper_eval.s5_mstar_production_core_identity import (
    build_s5_mstar_production_core_identity,
)
from paper_eval.s5_mstar_production_runner import (
    S5MStarProductionRunner,
    S5MStarProductionRunnerError,
)
from paper_eval.s5_mstar_publication_journal import S5MStarPublicationJournal
from paper_eval.s5_production_runner import (
    GRAPHITI_COMMIT,
    GRAPHITI_VERSION,
    S5ProductionIdentityError,
    build_s5_production_identity,
)


def _core_identity(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "graphiti_version": GRAPHITI_VERSION,
        "graphiti_commit": GRAPHITI_COMMIT,
        "graphiti_semantic_api_sha256": "b" * 64,
        "graphiti_semantic_identity_artifact_sha256": "4" * 64,
        "runtime_factory_entrypoint": (
            "native_characterization_runtime.build_u0_graphiti_from_env"
        ),
        "runtime_factory_source_sha256": "c" * 64,
        "pipeline_source_sha256": "d" * 64,
        "pipeline_test_source_sha256": "e" * 64,
        "adapter_source_sha256": "5" * 64,
        "adapter_test_source_sha256": "6" * 64,
        "semantic_runtime_source_sha256": "7" * 64,
        "semantic_runtime_test_source_sha256": "8" * 64,
        "semantic_binding_source_sha256": "9" * 64,
        "semantic_binding_test_source_sha256": "0" * 64,
        "durable_store_source_sha256": "f" * 64,
        "durable_store_test_source_sha256": "1" * 64,
        "runtime_config_sha256": "2" * 64,
    }
    fields.update(overrides)
    return build_s5_mstar_production_core_identity(**fields)


def _identity(
    core: dict[str, object], **overrides: object
) -> dict[str, object]:
    fields: dict[str, object] = {
        "method": "M*",
        "graphiti_version": GRAPHITI_VERSION,
        "graphiti_commit": GRAPHITI_COMMIT,
        "graphiti_native_source_sha256": "a" * 64,
        "graphiti_semantic_api_sha256": core["graphiti_semantic_api_sha256"],
        "runtime_factory_entrypoint": core["runtime_factory_entrypoint"],
        "runtime_factory_source_sha256": core["runtime_factory_source_sha256"],
        "scheduler_source_sha256": core["pipeline_source_sha256"],
        "scheduler_test_source_sha256": core["pipeline_test_source_sha256"],
        "durable_store_source_sha256": core["durable_store_source_sha256"],
        "durable_store_test_source_sha256": core[
            "durable_store_test_source_sha256"
        ],
        "runtime_config_sha256": core["runtime_config_sha256"],
        "fx0_parity_artifact_sha256": "3" * 64,
    }
    fields.update(overrides)
    return build_s5_production_identity(**fields)


def _fx0_qualification(
    identity: dict[str, object],
    core: dict[str, object],
    **payload_overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": (
            "membind.paper-eval-v3.s5-graphiti-fx0-production-qualification.v1"
        ),
        "verdict": "PRODUCTION_PATH_EXACT_PARITY_PASS",
        "fixture_count": 11,
        "run_id": "s5-mstar-fx0-production-parity-test-001",
        "runtime_config_sha256": core["runtime_config_sha256"],
        "production_core_identity_sha256": core["identity_sha256"],
        "fx0_artifact_payload_sha256": identity["fx0_parity_artifact_sha256"],
        "fx0_fixture_manifest_sha256": "a" * 64,
        "current_stage_pointer_sha256": "b" * 64,
        "full_regression_junit_sha256": "c" * 64,
        "full_regression_summary": {
            "tests": 100,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
        },
        "legacy_status_artifact_preserved": True,
        "authority": {
            "model_call_authorized": False,
            "neo4j_read_authorized": False,
            "neo4j_mutation_authorized": False,
            "s5_live_execution_authorized": False,
            "current_stage_pointer_update_authorized": False,
        },
    }
    payload.update(payload_overrides)
    return {
        "git_commit": "a" * 40,
        "payload": payload,
        "payload_sha256": payload_sha256(payload),
        "protocol_version": "paper-eval-v3",
        "run_id": "s5-mstar-fx0-production-parity-test-001-qualification",
        "status": "finalized",
    }


class StepClock:
    def __init__(self) -> None:
        self.value = 100

    def __call__(self) -> int:
        self.value += 1
        return self.value


def _sources(count: int = 3) -> tuple[MStarSource, ...]:
    return tuple(
        MStarSource(i, f"{i + 10:064x}", {"source": i})
        for i in range(count)
    )


def _spec(
    core: dict[str, object], *, run_id: str = "s5-mstar-production-test"
) -> MStarSpec:
    return MStarSpec(
        run_id=run_id,
        production_core_identity_sha256=str(core["identity_sha256"]),
        prepare_concurrency=2,
    )


async def _prepare(source: object, logical_time: int) -> object:
    await asyncio.sleep(0)
    return {"source": source["source"], "logical_time": logical_time}


async def _commit_evidence(
    _bind_result: object,
    _logical_time: int,
    source_sequence: int,
    _visible_prefix: tuple[int, ...],
) -> str:
    return f"{source_sequence + 30:064x}"


def _runner(
    *,
    attempt_root: Path,
    core: dict[str, object],
    identity: dict[str, object],
    fx0: dict[str, object],
    latest_state_bind: object,
    commit_evidence: object = _commit_evidence,
    run_id: str = "s5-mstar-production-test",
) -> S5MStarProductionRunner:
    return S5MStarProductionRunner(
        attempt_root=attempt_root,
        spec=_spec(core, run_id=run_id),
        identity=identity,
        production_core_identity=core,
        fx0_qualification=fx0,
        sources=_sources(),
        semantic_prepare=_prepare,
        latest_state_bind=latest_state_bind,
        commit_evidence=commit_evidence,
        clock_ns=StepClock(),
    )


def test_mstar_runner_cross_binds_distinct_identities_and_journals_commit_order(
    tmp_path: Path,
) -> None:
    core = _core_identity()
    identity = _identity(core)
    fx0 = _fx0_qualification(identity, core)
    callback_observations: list[tuple[object, int, int, tuple[int, ...]]] = []

    async def bind(
        prepared: object,
        logical_time: int,
        source_sequence: int,
        visible_prefix: tuple[int, ...],
    ) -> object:
        assert prepared["source"] == source_sequence
        assert prepared["logical_time"] == logical_time
        assert visible_prefix == tuple(range(source_sequence))
        return {"bound_source": source_sequence}

    async def commit_evidence(
        bind_result: object,
        logical_time: int,
        source_sequence: int,
        visible_prefix: tuple[int, ...],
    ) -> str:
        callback_observations.append(
            (bind_result, logical_time, source_sequence, visible_prefix)
        )
        return f"{source_sequence + 30:064x}"

    root = tmp_path / "mstar"
    result = asyncio.run(
        _runner(
            attempt_root=root,
            core=core,
            identity=identity,
            fx0=fx0,
            latest_state_bind=bind,
            commit_evidence=commit_evidence,
        ).run()
    )

    assert identity["identity_sha256"] != core["identity_sha256"]
    assert result["status"] == "complete"
    assert result["payload"]["status"] == "PASS"
    assert result["production_identity_sha256"] == identity["identity_sha256"]
    assert result["production_core_identity_sha256"] == core["identity_sha256"]
    assert [item[2:] for item in callback_observations] == [
        (0, ()),
        (1, (0,)),
        (2, (0, 1)),
    ]
    assert [item[0] for item in callback_observations] == [
        {"bound_source": 0},
        {"bound_source": 1},
        {"bound_source": 2},
    ]

    journal = S5MStarPublicationJournal.load(root / "publication_journal.jsonl")
    journal_types = [event["event_type"] for event in journal.events]
    assert journal_types == [
        "intent",
        "intent",
        "intent",
        "commit",
        "publication",
        "commit",
        "publication",
        "commit",
        "publication",
    ]
    for source_sequence in range(3):
        operation_events = [
            event
            for event in journal.events
            if event["source_sha256"] == _sources()[source_sequence].source_sha256
        ]
        assert [event["event_type"] for event in operation_events] == [
            "intent",
            "commit",
            "publication",
        ]
        assert operation_events[1]["commit_sha256"] == (
            f"{source_sequence + 30:064x}"
        )

    inspected = inspect_s5_attempt(root)
    assert inspected["manifest"]["production_core_identity_sha256"] == (
        core["identity_sha256"]
    )
    assert inspected["result"]["status"] == "complete"
    assert inspected["resume_authorized"] is False


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("spec_core", "production_core_identity_mismatch"),
        ("fx0_core", "fx0_core_identity_mismatch"),
        ("fx0_artifact", "fx0_artifact_identity_mismatch"),
        ("fx0_runtime", "fx0_runtime_config_mismatch"),
        ("generic_source", "production_core_source_binding_mismatch"),
    ],
)
def test_mstar_runner_rejects_identity_cross_binding_drift(
    tmp_path: Path, case: str, expected_code: str
) -> None:
    core = _core_identity()
    identity = _identity(core)
    fx0 = _fx0_qualification(identity, core)
    spec = _spec(core)
    if case == "spec_core":
        spec = MStarSpec(
            run_id=spec.run_id,
            production_core_identity_sha256="f" * 64,
            prepare_concurrency=2,
        )
    elif case == "fx0_core":
        fx0 = _fx0_qualification(
            identity, core, production_core_identity_sha256="f" * 64
        )
    elif case == "fx0_artifact":
        fx0 = _fx0_qualification(
            identity, core, fx0_artifact_payload_sha256="f" * 64
        )
    elif case == "fx0_runtime":
        fx0 = _fx0_qualification(identity, core, runtime_config_sha256="f" * 64)
    elif case == "generic_source":
        identity = _identity(core, scheduler_source_sha256="f" * 64)

    async def bind(*_args: object) -> None:
        return None

    with pytest.raises(S5MStarProductionRunnerError, match=expected_code):
        S5MStarProductionRunner(
            attempt_root=tmp_path / case,
            spec=spec,
            identity=identity,
            production_core_identity=core,
            fx0_qualification=fx0,
            sources=_sources(),
            semantic_prepare=_prepare,
            latest_state_bind=bind,
            commit_evidence=_commit_evidence,
            clock_ns=StepClock(),
        )


def test_mstar_runner_rejects_tampered_fx0_qualification_seal(
    tmp_path: Path,
) -> None:
    core = _core_identity()
    identity = _identity(core)
    fx0 = _fx0_qualification(identity, core)
    fx0["payload"]["verdict"] = "PRODUCTION_PATH_EXACT_PARITY_FAIL"

    async def bind(*_args: object) -> None:
        return None

    with pytest.raises(S5MStarProductionRunnerError, match="fx0_qualification_hash_invalid"):
        _runner(
            attempt_root=tmp_path / "tampered",
            core=core,
            identity=identity,
            fx0=fx0,
            latest_state_bind=bind,
        )


def test_mstar_bind_failure_is_incomplete_and_keeps_published_prefix(tmp_path: Path) -> None:
    core = _core_identity()
    identity = _identity(core)
    fx0 = _fx0_qualification(identity, core)

    async def bind(
        prepared: object,
        _logical_time: int,
        _source_sequence: int,
        _visible_prefix: tuple[int, ...],
    ) -> None:
        if prepared["source"] == 1:
            raise RuntimeError("simulated private bind failure")

    root = tmp_path / "failed"
    result = asyncio.run(
        _runner(
            attempt_root=root,
            core=core,
            identity=identity,
            fx0=fx0,
            latest_state_bind=bind,
        ).run()
    )
    assert result["status"] == "incomplete_non_mergeable"
    assert result["payload"]["failure_code"] == "LATEST_STATE_BIND_FAILED"
    assert result["payload"]["summary"]["published_source_sequences"] == [0]
    assert result["resume_authorized"] is False
    assert "simulated private bind failure" not in (root / "result.json").read_text()


def test_invalid_commit_evidence_fails_closed_without_publication(tmp_path: Path) -> None:
    core = _core_identity()
    identity = _identity(core)
    fx0 = _fx0_qualification(identity, core)

    async def bind(*_args: object) -> object:
        return {"private": "bind return"}

    async def invalid_commit(*_args: object) -> str:
        return "not-a-commit-digest"

    root = tmp_path / "invalid-commit"
    result = asyncio.run(
        _runner(
            attempt_root=root,
            core=core,
            identity=identity,
            fx0=fx0,
            latest_state_bind=bind,
            commit_evidence=invalid_commit,
            run_id="s5-mstar-invalid-commit-test",
        ).run()
    )
    assert result["status"] == "incomplete_non_mergeable"
    assert result["payload"]["failure_code"] == "LATEST_STATE_BIND_FAILED"
    assert result["payload"]["summary"]["published_source_sequences"] == []
    assert not any(
        event["event_type"] == "commit"
        for event in S5MStarPublicationJournal.load(
            root / "publication_journal.jsonl"
        ).events
    )
    assert "not-a-commit-digest" not in (root / "result.json").read_text()


def test_journal_exception_terminalizes_and_preserves_durable_published_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = _core_identity()
    identity = _identity(core)
    fx0 = _fx0_qualification(identity, core)

    async def bind(*_args: object) -> object:
        return {"committed": True}

    def fail_publication(
        _self: S5MStarPublicationJournal,
        _operation_id: str,
        *,
        recovered: bool = False,
    ) -> str:
        del recovered
        raise OSError("raw journal failure with private material")

    monkeypatch.setattr(
        S5MStarPublicationJournal, "record_publication", fail_publication
    )
    root = tmp_path / "journal-failed"
    result = asyncio.run(
        _runner(
            attempt_root=root,
            core=core,
            identity=identity,
            fx0=fx0,
            latest_state_bind=bind,
            run_id="s5-mstar-journal-failure-test",
        ).run()
    )

    assert result["status"] == "incomplete_non_mergeable"
    assert result["payload"]["status"] == "FAIL_CLOSED"
    assert result["payload"]["failure_code"] == "PIPELINE_OR_JOURNAL_EXCEPTION"
    inspected = inspect_s5_attempt(root)
    assert inspected["checkpoint"]["status"] == "incomplete_non_mergeable"
    assert inspected["checkpoint"]["published_source_sequences"] == [0]
    assert inspected["result"]["payload"]["events"] == inspected["events"]
    assert inspected["resume_authorized"] is False
    persisted = (root / "result.json").read_text(encoding="utf-8")
    assert "raw journal failure" not in persisted
    assert "private material" not in persisted


def test_pipeline_exception_terminalizes_empty_attempt(tmp_path: Path) -> None:
    core = _core_identity()
    identity = _identity(core)
    fx0 = _fx0_qualification(identity, core)

    async def bind(*_args: object) -> None:
        return None

    root = tmp_path / "pipeline-failed"
    runner = S5MStarProductionRunner(
        attempt_root=root,
        spec=_spec(core, run_id="s5-mstar-pipeline-failure-test"),
        identity=identity,
        production_core_identity=core,
        fx0_qualification=fx0,
        sources=_sources(),
        semantic_prepare=_prepare,
        latest_state_bind=bind,
        commit_evidence=_commit_evidence,
        clock_ns=lambda: -1,
    )
    result = asyncio.run(runner.run())
    assert result["status"] == "incomplete_non_mergeable"
    assert result["payload"]["failure_code"] == "PIPELINE_OR_JOURNAL_EXCEPTION"
    inspected = inspect_s5_attempt(root)
    assert inspected["events"] == []
    assert inspected["checkpoint"]["published_source_sequences"] == []
    assert inspected["result"]["payload"]["events"] == []


def test_mstar_runner_rejects_missing_fx0_identity_or_single_source(tmp_path: Path) -> None:
    core = _core_identity()
    with pytest.raises(S5ProductionIdentityError, match="fx0"):
        identity = build_s5_production_identity(
            method="M*",
            graphiti_version=GRAPHITI_VERSION,
            graphiti_commit=GRAPHITI_COMMIT,
            graphiti_native_source_sha256="a" * 64,
            graphiti_semantic_api_sha256="b" * 64,
            runtime_factory_entrypoint=(
                "native_characterization_runtime.build_u0_graphiti_from_env"
            ),
            runtime_factory_source_sha256="c" * 64,
            scheduler_source_sha256="d" * 64,
            scheduler_test_source_sha256="e" * 64,
            durable_store_source_sha256="f" * 64,
            durable_store_test_source_sha256="1" * 64,
            runtime_config_sha256="2" * 64,
        )
        del identity  # build itself must fail closed before runner construction

    identity = _identity(core)
    fx0 = _fx0_qualification(identity, core)
    with pytest.raises(S5MStarProductionRunnerError, match="sources"):
        S5MStarProductionRunner(
            attempt_root=tmp_path / "one",
            spec=_spec(core),
            identity=identity,
            production_core_identity=core,
            fx0_qualification=fx0,
            sources=_sources(1),
            semantic_prepare=_prepare,
            latest_state_bind=_prepare,
            commit_evidence=_commit_evidence,
            clock_ns=StepClock(),
        )
