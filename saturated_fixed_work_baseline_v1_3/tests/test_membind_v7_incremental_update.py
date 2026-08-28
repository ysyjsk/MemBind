from __future__ import annotations

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.incremental_update import (
    ArtifactKey,
    ArtifactRecord,
    IncrementalUpdateContractError,
    affected_closure,
    build_incremental_plan,
)


def _record(object_id: str, *, source_hash: str = "src") -> ArtifactRecord:
    key = ArtifactKey(object_id, source_hash, "schema", "model", "config")
    return ArtifactRecord(key, frontier_version=0, semantics_hash=f"sem-{object_id}")


def test_affected_closure_is_transitive_and_deterministic() -> None:
    assert affected_closure(
        ["episode-1"],
        [("episode-1", "entity-a"), ("entity-a", "edge-ab"), ("episode-2", "entity-z")],
    ) == frozenset({"episode-1", "entity-a", "edge-ab"})


def test_incremental_plan_reuses_only_unaffected_matching_artifacts() -> None:
    records = (_record("entity-a"), _record("entity-z"), _record("entity-b", source_hash="old"))
    plan = build_incremental_plan(
        source_version=0,
        target_version=1,
        changed_objects=["episode-1"],
        dependency_edges=[("episode-1", "entity-a")],
        artifacts=records,
        source_hash="src",
        schema_hash="schema",
        model_hash="model",
        config_hash="config",
    )
    assert plan.status == "READY"
    assert plan.recompute_objects == ("entity-a", "episode-1")
    assert plan.reusable_artifacts == (records[1].key.digest,)


def test_incremental_plan_rejects_multi_delta() -> None:
    with pytest.raises(IncrementalUpdateContractError, match="d=1"):
        build_incremental_plan(
            source_version=0,
            target_version=2,
            changed_objects=["new"],
            dependency_edges=[],
            artifacts=[],
            source_hash="src",
            schema_hash="schema",
            model_hash="model",
            config_hash="config",
        )


def test_incomplete_artifact_is_not_reusable() -> None:
    record = ArtifactRecord(_record("entity-z").key, 0, "sem-z", complete=False)
    plan = build_incremental_plan(
        source_version=0,
        target_version=1,
        changed_objects=[],
        dependency_edges=[],
        artifacts=[record],
        source_hash="src",
        schema_hash="schema",
        model_hash="model",
        config_hash="config",
    )
    assert plan.reusable_artifacts == ()


def test_artifact_from_another_frontier_is_not_reusable() -> None:
    record = ArtifactRecord(_record("entity-z").key, frontier_version=1, semantics_hash="sem-z")
    plan = build_incremental_plan(
        source_version=0,
        target_version=1,
        changed_objects=[],
        dependency_edges=[],
        artifacts=[record],
        source_hash="src",
        schema_hash="schema",
        model_hash="model",
        config_hash="config",
    )
    assert plan.reusable_artifacts == ()
