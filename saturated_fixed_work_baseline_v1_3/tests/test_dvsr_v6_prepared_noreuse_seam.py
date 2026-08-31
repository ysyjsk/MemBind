"""TDD contract for the Frozen-V6 prepared/no-reuse seam."""

from __future__ import annotations

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_seam import (
    FrozenV6PreparedNoReuseControl,
    SEAM_IDENTITY,
)


def test_prepare_materializes_once_and_returns_isolated_clones() -> None:
    calls: list[object] = []

    def extract(source: object) -> dict[str, object]:
        calls.append(source)
        return {"source": source, "nodes": ["n1"]}

    control = FrozenV6PreparedNoReuseControl(v6_identity="v6-sealed", previous_context_policy="v6-certified-strip")
    first = control.prepare(0, "body", extract)
    original_digest = first.artifact_digest
    first.payload["nodes"].append("local-only")  # type: ignore[union-attr]
    second = control.prepare(0, "body", extract)

    assert control.materialize_count == 1
    assert calls == ["body"]
    assert second.payload == {"source": "body", "nodes": ["n1"]}
    assert second.artifact_digest == original_digest


def test_no_reuse_resolves_fresh_on_each_authoritative_state() -> None:
    seen: list[dict[str, object]] = []
    control = FrozenV6PreparedNoReuseControl(v6_identity="v6-sealed", previous_context_policy="v6-certified-strip")
    artifact = control.prepare(1, "body", lambda _: {"nodes": ["n1"]})

    def resolve(payload: dict[str, object], state: dict[str, object]) -> dict[str, object]:
        seen.append(state)
        return {"payload": payload, "frontier": state["frontier"]}

    first = control.resolve_fresh(artifact, {"frontier": 0}, resolve, read_epoch="s0")
    second = control.resolve_fresh(artifact, {"frontier": 1}, resolve, read_epoch="s1")

    assert control.resolve_count == 2
    assert [row["frontier"] for row in seen] == [0, 1]
    assert first.output["frontier"] == 0
    assert second.output["frontier"] == 1
    assert first.provider_calls == second.provider_calls == 1
    assert first.database_writes == second.database_writes == 0


def test_resolve_rejects_foreign_v6_artifact() -> None:
    owner = FrozenV6PreparedNoReuseControl(v6_identity="v6-a", previous_context_policy="p")
    other = FrozenV6PreparedNoReuseControl(v6_identity="v6-b", previous_context_policy="p")
    artifact = owner.prepare(0, "body", lambda _: {"nodes": []})
    with pytest.raises(ValueError, match="different Frozen V6 identity"):
        other.resolve_fresh(artifact, {}, lambda _payload, _state: {}, read_epoch="s0")


def test_ordered_publication_is_contiguous_and_source_ordered() -> None:
    published: list[int] = []
    control = FrozenV6PreparedNoReuseControl(v6_identity="v6-sealed", previous_context_policy="p")
    artifacts = [control.prepare(index, f"body-{index}", lambda source: {"source": source}) for index in range(3)]
    resolutions = {
        index: control.resolve_fresh(artifact, {"frontier": index}, lambda _payload, state: state["frontier"], read_epoch=f"s{index}")
        for index, artifact in enumerate(reversed(artifacts))
    }
    assert control.ordered_publication(resolutions, lambda sequence, _output: published.append(sequence)) == (0, 1, 2)
    assert published == [0, 1, 2]

    with pytest.raises(ValueError, match="contiguous"):
        control.ordered_publication({0: resolutions[0], 2: resolutions[2]}, lambda _sequence, _output: None)


def test_no_reuse_identity_is_explicit_and_has_no_writes() -> None:
    assert SEAM_IDENTITY == "V6_PREPARED_NOREUSE_CONTROL"
    control = FrozenV6PreparedNoReuseControl(v6_identity="v6-sealed", previous_context_policy="p")
    artifact = control.prepare(0, "body", lambda _: {"nodes": []})
    result = control.resolve_fresh(artifact, {"frontier": 0}, lambda _payload, _state: {"ok": True}, read_epoch="s0")
    assert result.database_writes == 0
