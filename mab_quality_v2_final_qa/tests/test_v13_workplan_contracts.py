from __future__ import annotations

import copy
from pathlib import Path

import pytest

from mab_quality_v2_final_qa.mab_main_dataset import (
    DATASET_REVISION,
    KNOWN_PARTIAL_GOLD_QUESTION_ID,
    build_authority,
    build_episode_inputs,
    build_qa_manifest,
)
from mab_quality_v2_final_qa.workload_contract import (
    EpisodeInput,
    WorkloadManifest,
    WorkloadContractError,
)


DATASET = Path(__file__).parents[1] / "data" / "official_5_contexts.json"


def test_main_authority_is_full_five_context_component() -> None:
    authority = build_authority(DATASET)
    assert authority["authority_status"] == "FULL_OFFICIAL_COMPONENT"
    assert authority["context_count"] == 5
    assert authority["session_counts"] == [111, 107, 116, 111, 110]
    assert authority["total_sessions"] == 555
    assert authority["qa_per_context"] == 60
    assert authority["total_qa"] == 300
    assert authority["question_type_counts"] == {
        "knowledge-update": 45,
        "multi-session": 75,
        "single-session-assistant": 30,
        "single-session-preference": 30,
        "single-session-user": 45,
        "temporal-reasoning": 75,
    }
    assert authority["partial_gold_mapping_question_ids"] == [
        KNOWN_PARTIAL_GOLD_QUESTION_ID
    ]


def test_known_partial_gold_mapping_is_retained_for_answer_qa() -> None:
    authority = build_authority(DATASET)
    context = authority["contexts"][4]
    item = next(
        item
        for item in context.qa_items
        if item.question_id == KNOWN_PARTIAL_GOLD_QUESTION_ID
    )
    assert item.gold_mapping_status == "PARTIAL_GOLD_MAPPING"
    assert item.gold_session_ids
    assert len(context.qa_items) == 60


def test_construction_projection_is_gold_blind_and_hash_stable() -> None:
    authority = build_authority(DATASET)
    context = authority["contexts"][0]
    episodes = build_episode_inputs(context)
    assert len(episodes) == 111
    assert [item.source_sequence for item in episodes] == list(range(111))
    body = "\n".join(item.body for item in episodes)
    assert "has_answer" not in body
    assert "reference_answer" not in body
    assert "question_type" not in body

    manifest = WorkloadManifest.from_episodes(
        context_id=context.context_id,
        episodes=episodes,
        dataset_revision=DATASET_REVISION,
        dataset_file_sha256=authority["local_file_sha256"],
    )
    assert manifest.scope == "FORMAL"
    private_mutation = copy.deepcopy(context.qa_items[0].private_labels().as_dict())
    private_mutation["reference_answers"] = ["changed"]
    assert manifest.manifest_sha256 == WorkloadManifest.from_episodes(
        context_id=context.context_id,
        episodes=episodes,
        dataset_revision=DATASET_REVISION,
        dataset_file_sha256=authority["local_file_sha256"],
    ).manifest_sha256


def test_manifest_identity_changes_for_input_changes_and_rejects_prefix() -> None:
    authority = build_authority(DATASET)
    context = authority["contexts"][0]
    episodes = build_episode_inputs(context)
    full = WorkloadManifest.from_episodes(
        context_id=context.context_id,
        episodes=episodes,
        dataset_revision=DATASET_REVISION,
        dataset_file_sha256=authority["local_file_sha256"],
    )
    changed = list(episodes)
    changed[0] = EpisodeInput(
        **{**changed[0].to_dict(), "body": changed[0].body + " changed"}
    )
    changed_manifest = WorkloadManifest.from_episodes(
        context_id=context.context_id,
        episodes=changed,
        dataset_revision=DATASET_REVISION,
        dataset_file_sha256=authority["local_file_sha256"],
    )
    assert changed_manifest.manifest_sha256 != full.manifest_sha256
    prefix = WorkloadManifest.from_episodes(
        context_id=context.context_id,
        episodes=episodes[:8],
        dataset_revision=DATASET_REVISION,
        dataset_file_sha256=authority["local_file_sha256"],
        scope="ENGINEERING_DIAGNOSTIC",
    )
    assert prefix.scope == "ENGINEERING_DIAGNOSTIC"
    with pytest.raises(WorkloadContractError, match="prefix"):
        prefix.require_formal()


def test_qa_manifest_has_six_type_balanced_smoke_and_full_inventory() -> None:
    authority = build_authority(DATASET)
    context = authority["contexts"][0]
    smoke = build_qa_manifest(context, scope="SMOKE")
    full = build_qa_manifest(context, scope="FULL")
    assert len(smoke) == 6
    assert len({row["question_type"] for row in smoke}) == 6
    assert len(full) == 60
    assert {row["qa_pair_id"] for row in smoke}.issubset(
        {row["qa_pair_id"] for row in full}
    )
