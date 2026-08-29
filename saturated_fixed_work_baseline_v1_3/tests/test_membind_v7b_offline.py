from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.v7b import (
    FallbackPolicy,
    StableSemanticIR,
    ViewDefinition,
    V7FreshEngine,
    V7IncrementalEngine,
    apply_ordered_publication,
    extract_source_ir,
    materialize_offline_artifacts,
    stable_mention_id,
    stable_ir_contract,
    view_contracts,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.state_delta import (
    DeltaChange,
    StateDelta,
)


def _definitions() -> tuple[ViewDefinition, ...]:
    return (
        ViewDefinition(
            "source_ir",
            kind="source_local",
            state_dependencies=frozenset(),
            cost=2.0,
            compute=lambda ir, _state, _views: ir.digest,
        ),
        ViewDefinition(
            "entity_resolution",
            kind="stateful",
            predecessors=("source_ir",),
            state_dependencies=frozenset({"entities"}),
            cost=8.0,
            compute=lambda ir, state, views: {
                "source": ir.source_id,
                "known": tuple(sorted(state.get("entities", ()) or ())),
                "ir": views["source_ir"],
            },
        ),
        ViewDefinition(
            "publication_plan",
            kind="stateful",
            predecessors=("entity_resolution",),
            state_dependencies=frozenset({"entities"}),
            cost=5.0,
            compute=lambda _ir, state, views: {
                "entity": views["entity_resolution"],
                "frontier": state.get("frontier", 0),
            },
        ),
        ViewDefinition(
            "unrelated_view",
            kind="stateful",
            state_dependencies=frozenset({"unrelated"}),
            cost=20.0,
            compute=lambda _ir, state, _views: state.get("unrelated", "stable"),
        ),
        ViewDefinition(
            "stable_projection",
            kind="stateful",
            state_dependencies=frozenset({"entities"}),
            cost=3.0,
            compute=lambda _ir, _state, _views: "constant",
        ),
    )


def test_source_ir_is_stable_and_does_not_read_mutable_state() -> None:
    left = extract_source_ir("s0", "Alice met Bob on 2025-01-02.")
    right = extract_source_ir("s0", "Alice met Bob on 2025-01-02.")
    assert left == right
    assert left.digest == right.digest
    assert left.grounded is True
    assert all(item.source_id == "s0" for item in left.mentions)


def test_source_local_identity_is_state_independent_and_source_scoped() -> None:
    text = "Alice met Bob on 2025-01-02."
    same_source = extract_source_ir("s0", text)
    same_source_after_state_change = extract_source_ir("s0", text)
    other_source = extract_source_ir("s1", text)

    # A cache key may be reused only for the same source identity and content;
    # changing the authoritative frontier must not change source-local IR.
    assert same_source.digest == same_source_after_state_change.digest
    assert same_source.digest != other_source.digest
    assert stable_mention_id(same_source, same_source.mentions[0]) == stable_mention_id(
        same_source_after_state_change, same_source_after_state_change.mentions[0]
    )
    assert stable_mention_id(same_source, same_source.mentions[0]) != stable_mention_id(
        other_source, other_source.mentions[0]
    )


def test_source_local_view_is_reused_across_state_versions_only() -> None:
    definitions = _definitions()
    engine = V7FreshEngine(definitions)
    ir = extract_source_ir("s0", "Alice met Bob.")
    before = engine.build(ir, {"frontier": 0, "entities": ("alice",)})
    after = engine.build(
        ir,
        {"frontier": 1, "entities": ("alice", "bob"), "unrelated": "changed"},
    )
    assert before.views["source_ir"].digest == after.views["source_ir"].digest
    assert before.views["source_ir"].value == after.views["source_ir"].value


def test_fresh_and_incremental_match_with_localized_repair_and_reconvergence() -> None:
    definitions = _definitions()
    fresh_engine = V7FreshEngine(definitions)
    incremental_engine = V7IncrementalEngine(definitions)
    ir = extract_source_ir("s0", "Alice met Bob on 2025-01-02.")
    old_state = {"frontier": 0, "entities": ("alice",), "unrelated": "stable"}
    old = fresh_engine.build(ir, old_state)
    delta = StateDelta(
        source_version=0,
        target_version=1,
        changes=(
            DeltaChange(
                "memory",
                "entities",
                changed_fields=frozenset({"entities"}),
                before={"entities": ("alice",)},
                after={"entities": ("alice", "bob")},
            ),
        ),
    )
    new_state = {"frontier": 1, "entities": ("alice", "bob"), "unrelated": "stable"}
    fresh = fresh_engine.build(ir, new_state)
    maintained = incremental_engine.maintain(old, ir, new_state, delta)

    assert maintained.canonical_views == fresh.canonical_views
    assert maintained.fallback is False
    assert maintained.reused_view_ids == ("source_ir", "unrelated_view")
    assert set(maintained.repaired_view_ids) == {
        "entity_resolution",
        "publication_plan",
        "stable_projection",
    }
    assert maintained.reconverged_view_ids == ("stable_projection",)
    assert maintained.work_cost < fresh.work_cost


def test_unknown_environment_or_large_delta_falls_back_to_fresh() -> None:
    definitions = _definitions()
    fresh_engine = V7FreshEngine(definitions)
    incremental_engine = V7IncrementalEngine(
        definitions,
        fallback_policy=FallbackPolicy(max_repair_cost=1.0, headroom=0.0),
    )
    ir = extract_source_ir("s0", "Alice met Bob.")
    old = fresh_engine.build(ir, {"frontier": 0, "entities": ("alice",)})
    delta = StateDelta(
        source_version=0,
        target_version=1,
        changes=(),
        environment_changes=frozenset({"model_epoch"}),
    )
    result = incremental_engine.maintain(
        old, ir, {"frontier": 1, "entities": ("alice",)}, delta
    )
    assert result.fallback is True
    assert result.fallback_reason == "delta_or_environment_unknown"
    assert result.canonical_views == fresh_engine.build(
        ir, {"frontier": 1, "entities": ("alice",)}
    ).canonical_views


def test_ordered_publication_rejects_stale_frontier_and_preserves_source_order() -> None:
    state = {"frontier": 0, "events": []}
    result = apply_ordered_publication(
        state,
        (
            {"source_sequence": 0, "payload": "a"},
            {"source_sequence": 1, "payload": "b"},
        ),
    )
    assert result["frontier"] == 2
    assert result["events"] == ["a", "b"]
    with pytest.raises(ValueError, match="frontier"):
        apply_ordered_publication(
            {"frontier": 1, "events": []},
            ({"source_sequence": 0, "payload": "a"},),
        )


def test_offline_artifact_materializer_writes_auditable_contract(tmp_path: Path) -> None:
    definitions = _definitions()
    engine = V7FreshEngine(definitions)
    ir = extract_source_ir("s0", "Alice met Bob.")
    state = {"frontier": 0, "entities": ("alice",)}
    fresh = engine.build(ir, state)
    delta = StateDelta(0, 1, changes=())
    maintained = V7IncrementalEngine(definitions).maintain(
        fresh, ir, {"frontier": 1, "entities": ("alice",)}, delta
    )
    root = materialize_offline_artifacts(
        tmp_path / "run", fresh=fresh, incremental=maintained, delta=delta
    )
    assert (root / "manifest.json").exists()
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["treatment_calls"] == 0
    assert manifest["publication_order"] == "source_order"
    assert json.loads((root / "canonical_views.json").read_text())


def test_view_graph_rejects_cycles_and_unknown_predecessors() -> None:
    cyclic = (
        ViewDefinition("a", kind="stateful", predecessors=("b",)),
        ViewDefinition("b", kind="stateful", predecessors=("a",)),
    )
    with pytest.raises(ValueError, match="acyclic"):
        V7FreshEngine(cyclic)
    with pytest.raises(ValueError, match="unknown predecessor"):
        V7FreshEngine((ViewDefinition("a", kind="stateful", predecessors=("missing",)),))


def test_unsupported_view_is_fail_closed_for_fresh_and_contracts() -> None:
    definitions = (
        ViewDefinition("unsupported", kind="stateful", supported=False),
    )
    ir = extract_source_ir("s0", "Alice")
    with pytest.raises(ValueError, match="unsupported view"):
        V7FreshEngine(definitions).build(ir, {"frontier": 0})
    contracts = view_contracts(definitions)
    assert contracts["unsupported"]["status"] == "UNSUPPORTED"


def test_source_ir_change_falls_back_and_never_reuses_old_artifacts() -> None:
    definitions = _definitions()
    fresh_engine = V7FreshEngine(definitions)
    incremental_engine = V7IncrementalEngine(definitions)
    old_ir = extract_source_ir("s0", "Alice met Bob.")
    new_ir = extract_source_ir("s0", "Alice met Carol.")
    old = fresh_engine.build(old_ir, {"frontier": 0, "entities": ("alice",)})
    delta = StateDelta(0, 1, changes=())
    result = incremental_engine.maintain(
        old, new_ir, {"frontier": 1, "entities": ("alice",)}, delta
    )
    assert result.fallback is True
    assert result.fallback_reason == "source_ir_changed"
    assert result.reused_view_ids == ()


def test_undeclared_state_dependency_is_conservatively_repaired() -> None:
    definitions = (
        ViewDefinition(
            "opaque", kind="stateful", state_dependencies=frozenset(),
            compute=lambda _ir, state, _views: state.get("opaque"),
        ),
    )
    ir = extract_source_ir("s0", "Alice")
    old = V7FreshEngine(definitions).build(ir, {"frontier": 0, "opaque": "a"})
    delta = StateDelta(
        0, 1,
        changes=(DeltaChange("memory", "opaque", changed_fields=frozenset({"opaque"}), after={"opaque": "b"}),),
    )
    result = V7IncrementalEngine(definitions).maintain(
        old, ir, {"frontier": 1, "opaque": "b"}, delta
    )
    assert result.repaired_view_ids == ("opaque",)
    assert result.reused_view_ids == ()


def test_contracts_are_explicitly_source_local_and_state_guarded() -> None:
    contract = stable_ir_contract()
    assert contract["status"] == "PASS"
    assert "mutable_memory_state" in contract["forbidden_mutable_state_reads"]
    definitions = _definitions()
    contracts = view_contracts(definitions)
    assert contracts["source_ir"]["kind"] == "source_local"
    assert contracts["entity_resolution"]["kind"] == "stateful"
    assert contracts["entity_resolution"]["state_dependencies"] == ["entities"]


def test_guarded_dynamic_repair_stops_propagation_after_exact_reconvergence() -> None:
    """C1 must not repair a successor whose dirty predecessor reconverges."""

    definitions = (
        ViewDefinition(
            "source_ir", kind="source_local", cost=1.0,
            compute=lambda ir, _state, _views: ir.digest,
        ),
        ViewDefinition(
            "temporal_view", kind="stateful", predecessors=("source_ir",),
            state_dependencies=frozenset({"temporal"}), cost=4.0,
            compute=lambda _ir, state, _views: tuple(sorted(str(x) for x in state.get("temporal", ()))),
        ),
        ViewDefinition(
            "year_bucket", kind="stateful", predecessors=("temporal_view",),
            state_dependencies=frozenset({"temporal"}), cost=2.0,
            compute=lambda _ir, state, _views: tuple(sorted({str(x)[:4] for x in state.get("temporal", ())})),
        ),
        ViewDefinition(
            "year_only_consumer", kind="stateful", predecessors=("year_bucket",),
            state_dependencies=frozenset({"unrelated"}), cost=9.0,
            compute=lambda _ir, _state, views: {"year": views["year_bucket"]},
        ),
    )
    ir = extract_source_ir("s0", "Alice met Bob")
    engine = V7FreshEngine(definitions)
    old_state = {"frontier": 0, "temporal": ("2025-01-01",)}
    new_state = {"frontier": 1, "temporal": ("2025-01-01", "2025-01-02")}
    old = engine.build(ir, old_state)
    fresh = engine.build(ir, new_state)
    delta = StateDelta(
        0, 1,
        changes=(DeltaChange("memory", "temporal", changed_fields=frozenset({"temporal"})),),
    )
    c0 = V7IncrementalEngine(definitions).maintain(old, ir, new_state, delta)
    c1 = V7IncrementalEngine(definitions).maintain_guarded(old, ir, new_state, delta)
    assert c0.canonical_views == fresh.canonical_views
    assert c1.canonical_views == fresh.canonical_views
    assert "year_only_consumer" in c0.repaired_view_ids
    assert "year_only_consumer" in c1.reused_view_ids
    assert "year_bucket" in c1.reconverged_view_ids
    assert c1.work_cost < c0.work_cost
