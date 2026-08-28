from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6_1.evidence import (
    extraction_work_inventory,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.summary_materialization import (
    GroundedSummaryCompatibilityError,
    install_grounded_summary_materialization,
    materialize_grounded_summaries,
)


def _node(name: str, uuid: str, *, summary: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        uuid=uuid,
        group_id="fixture",
        summary=summary,
    )


def _edge(fact: str) -> SimpleNamespace:
    return SimpleNamespace(fact=fact)


def _run_materialization(
    nodes,
    episode,
    edges_by_node,
    *,
    registry=None,
    should_summarize_node=None,
    max_summary_chars=1000,
):
    evidence = []
    selected_registry = {} if registry is None else registry
    asyncio.run(
        materialize_grounded_summaries(
            nodes,
            episode,
            should_summarize_node,
            edges_by_node,
            registry=selected_registry,
            evidence_sink=evidence.append,
            max_summary_chars=max_summary_chars,
            max_nodes_per_flight=30,
        )
    )
    return selected_registry, evidence


def test_current_edge_facts_and_episode_spans_are_exact_grounded_units() -> None:
    node = _node("USER", "user", summary="uncertified old prose")
    episode_text = "USER watched a game at Staples Center.\nA separate sentence."
    _, evidence = _run_materialization(
        [node],
        SimpleNamespace(content=episode_text),
        {"user": [_edge("USER attended an NBA game at Staples Center")]},
    )

    assert node.summary.splitlines() == [
        "USER attended an NBA game at Staples Center",
        "USER watched a game at Staples Center.",
    ]
    node_event = evidence[0]
    assert [value["origin"] for value in node_event["selected_units"]] == [
        "current_edge",
        "current_episode",
    ]
    assert all(value["source_sha256"] for value in node_event["selected_units"])
    assert "uncertified old prose" not in json.dumps(evidence)
    assert "USER attended" not in json.dumps(evidence)


def test_isolated_node_gets_nonempty_exact_episode_span() -> None:
    node = _node("Notion", "notion")
    source = "The USER moved project notes into Notion after the meeting."
    _, evidence = _run_materialization(
        [node], SimpleNamespace(content=source), {}
    )

    assert node.summary == source
    assert node.summary in source
    assert evidence[-1]["empty_grounding_node_count"] == 0
    assert evidence[-1]["episode_span_unit_count"] == 1


def test_mention_span_excludes_unrelated_sentences_on_the_same_line() -> None:
    notion = _node("Notion", "notion")
    source = "Notion stores the notes. Evernote stores a separate backup."
    _, _ = _run_materialization(
        [notion], SimpleNamespace(content=source), {}
    )

    assert notion.summary == "Notion stores the notes."


def test_long_mention_span_starts_and_ends_on_word_boundaries() -> None:
    node = _node("TBR lists", "tbr-lists")
    source = ("abcdefgh " * 60) + "TBR lists collect future books " + ("trailingword " * 30)
    _, _ = _run_materialization(
        [node], SimpleNamespace(content=source), {}
    )

    assert node.summary.startswith("abcdefgh ")
    assert node.summary.endswith("trailingword")
    assert "TBR lists" in node.summary
    assert len(node.summary) <= 320


def test_units_are_deduplicated_stably_and_bounded() -> None:
    node = _node("Alpha", "alpha")
    fact = "Alpha has a deliberately long grounded fact"
    _, evidence = _run_materialization(
        [node],
        SimpleNamespace(content="Alpha appears here."),
        {"alpha": [_edge(fact), _edge(fact)]},
        max_summary_chars=20,
    )

    assert node.summary == fact[:20]
    assert len(node.summary) == 20
    selected = evidence[0]["selected_units"]
    assert len(selected) == 1
    assert selected[0]["origin"] == "current_edge"
    assert selected[0]["truncated"] is True
    assert evidence[0]["dropped_unit_count"] == 2


def test_only_certified_prior_units_are_reused() -> None:
    registry = {}
    first = _node("Notion", "notion")
    source = "Notion stores the USER's project notes."
    registry, _ = _run_materialization(
        [first], SimpleNamespace(content=source), {}, registry=registry
    )
    second = _node("Notion", "notion", summary="uncertified replacement")
    _, evidence = _run_materialization(
        [second],
        SimpleNamespace(content="No matching canonical entity is present."),
        {},
        registry=registry,
    )

    assert second.summary == source
    assert evidence[0]["previous_summary_certified"] is True
    assert evidence[0]["selected_units"][0]["origin"] == "prior_certified"


def test_false_summary_filter_does_not_modify_node() -> None:
    node = _node("Notion", "notion", summary="keep exactly")

    async def reject(_node):
        return False

    _, evidence = _run_materialization(
        [node],
        SimpleNamespace(content="Notion appears here."),
        {"notion": [_edge("Notion stores notes")]},
        should_summarize_node=reject,
    )

    assert node.summary == "keep exactly"
    assert [row["event"] for row in evidence] == ["GROUNDED_SUMMARY_BATCH"]
    assert evidence[0]["skipped_by_filter_count"] == 1


def test_skip_fact_appending_falls_back_to_upstream_and_restore_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graphiti_core.utils.maintenance import node_operations

    calls = []

    async def upstream(
        llm_client,
        nodes,
        episode,
        previous_episodes,
        should_summarize_node,
        edges_by_node,
        *,
        skip_fact_appending=False,
        entity_types=None,
    ):
        calls.append(skip_fact_appending)
        nodes[0].summary = "upstream result"

    monkeypatch.setattr(node_operations, "_extract_entity_summaries_batch", upstream)
    restore, evidence = install_grounded_summary_materialization()
    patched = node_operations._extract_entity_summaries_batch
    node = _node("Notion", "notion")
    asyncio.run(
        patched(
            object(),
            [node],
            SimpleNamespace(content="Notion"),
            [],
            None,
            {},
            skip_fact_appending=True,
        )
    )

    assert calls == [True]
    assert node.summary == "upstream result"
    assert evidence[0]["fallback_to_upstream"] is True
    restore()
    restore()
    assert node_operations._extract_entity_summaries_batch is upstream


def test_installer_rejects_double_install_and_signature_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from graphiti_core.utils.maintenance import node_operations

    original = node_operations._extract_entity_summaries_batch
    restore, _ = install_grounded_summary_materialization()
    try:
        with pytest.raises(GroundedSummaryCompatibilityError, match="already installed"):
            install_grounded_summary_materialization()
    finally:
        restore()

    async def drifted(*args, **kwargs):
        return None

    monkeypatch.setattr(node_operations, "_extract_entity_summaries_batch", drifted)
    with pytest.raises(GroundedSummaryCompatibilityError, match="signature drifted"):
        install_grounded_summary_materialization()
    monkeypatch.setattr(node_operations, "_extract_entity_summaries_batch", original)


def test_grounded_summary_inventory_is_content_free_and_counts_bypasses() -> None:
    node = _node("Notion", "notion")
    _, diagnostics = _run_materialization(
        [node],
        SimpleNamespace(content="Notion stores notes."),
        {},
    )
    inventory = extraction_work_inventory(diagnostics)

    assert inventory["grounded_summary_materializations"] == 1
    assert inventory["grounded_summary_nodes"] == 1
    assert inventory["grounded_summary_episode_span_units"] == 1
    assert inventory["grounded_summary_node_evidence"] == 1
    assert inventory["summary_llm_bypasses"] == 1
    assert inventory["summary_upstream_fallbacks"] == 0
    assert "Notion stores notes" not in json.dumps(diagnostics)
