from __future__ import annotations

from paper_eval.membind_v4.mseg.conflict import (
    ConflictClass,
    MemoryScope,
    classify_operator_conflict,
)


def test_known_disjoint_scopes_are_certified_non_conflicting() -> None:
    left = MemoryScope.known(
        namespace="history-a",
        read_items={"entity:a"},
        effect_items={"summary:a"},
    )
    right = MemoryScope.known(
        namespace="history-a",
        read_items={"entity:b"},
        effect_items={"summary:b"},
    )

    assert classify_operator_conflict(left, right) is ConflictClass.CERTIFIED_NON_CONFLICTING


def test_read_write_and_write_write_overlap_are_conflicting() -> None:
    reader = MemoryScope.known(
        namespace="history-a",
        read_items={"entity:a"},
        effect_items=set(),
    )
    writer = MemoryScope.known(
        namespace="history-a",
        read_items=set(),
        effect_items={"entity:a"},
    )
    second_writer = MemoryScope.known(
        namespace="history-a",
        read_items=set(),
        effect_items={"entity:a"},
    )

    assert classify_operator_conflict(reader, writer) is ConflictClass.CONFLICTING
    assert classify_operator_conflict(writer, second_writer) is ConflictClass.CONFLICTING


def test_unknown_scope_is_never_treated_as_non_conflicting() -> None:
    unknown = MemoryScope.unknown(namespace="history-a", reason="candidate_uuid_unresolved")
    known = MemoryScope.known(
        namespace="history-a",
        read_items={"entity:b"},
        effect_items={"summary:b"},
    )

    assert classify_operator_conflict(unknown, known) is ConflictClass.UNKNOWN


def test_known_overlap_wins_even_when_other_scope_information_is_incomplete() -> None:
    partial = MemoryScope.unknown(
        namespace="history-a",
        reason="additional_effects_unresolved",
        read_items={"entity:a"},
    )
    writer = MemoryScope.known(
        namespace="history-a",
        read_items=set(),
        effect_items={"entity:a"},
    )

    assert classify_operator_conflict(partial, writer) is ConflictClass.CONFLICTING


def test_known_namespace_isolation_certifies_non_conflict() -> None:
    left = MemoryScope.known(
        namespace="history-a",
        read_items={"entity:a"},
        effect_items={"entity:a"},
    )
    right = MemoryScope.known(
        namespace="history-b",
        read_items={"entity:a"},
        effect_items={"entity:a"},
    )

    assert classify_operator_conflict(left, right) is ConflictClass.CERTIFIED_NON_CONFLICTING

