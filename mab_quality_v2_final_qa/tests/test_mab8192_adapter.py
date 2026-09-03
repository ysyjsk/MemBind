from __future__ import annotations

import hashlib

import pytest

from mab_quality_v2_final_qa.mab_main_dataset import DATASET_REVISION, load_main_contexts
from mab_quality_v2_final_qa.mab8192_adapter import (
    MAB8192_ADAPTER_VERSION,
    MAB8192_CHUNK_SIZE,
    MAB8192Manifest,
    adapter_identity,
    split_lossless_body,
)
from mab_quality_v2_final_qa.workload_contract import canonical_episode_body


DATASET = "mab_quality_v2_final_qa/data/official_5_contexts.json"


def test_split_lossless_body_prefers_turn_boundaries_and_is_bounded() -> None:
    body = "[USER]\n" + ("alpha " * 2000) + "\n[ASSISTANT]\nanswer"
    chunks = split_lossless_body(body)
    assert chunks
    assert all(len(chunk) <= MAB8192_CHUNK_SIZE for chunk in chunks)
    assert "".join(chunks) == body
    assert chunks[0].startswith("[USER]\n")
    assert chunks[-1].endswith("answer")


def test_split_lossless_body_rejects_invalid_chunk_size() -> None:
    with pytest.raises(ValueError):
        split_lossless_body("[USER]\nx", chunk_size=0)


def test_all_official_sessions_round_trip_without_loss() -> None:
    contexts = load_main_contexts(DATASET)
    assert len(contexts) == 5
    assert sum(len(context.sessions) for context in contexts) == 555
    for context in contexts:
        manifest = MAB8192Manifest.from_context(
            context, dataset_revision=DATASET_REVISION
        )
        assert manifest.manifest_sha256
        assert manifest.to_dict()["adapter_version"] == MAB8192_ADAPTER_VERSION
        for session in context.sessions:
            body = canonical_episode_body(session)
            chunks = manifest.session_chunks(session.session_id)
            assert chunks
            assert all(len(chunk.body) <= MAB8192_CHUNK_SIZE for chunk in chunks)
            assert "".join(chunk.body for chunk in chunks) == body
            assert manifest.reconstruct_session(session.session_id) == body
            assert all(
                chunk.chunk_sha256 == hashlib.sha256(chunk.body.encode("utf-8")).hexdigest()
                for chunk in chunks
            )
            assert [chunk.chunk_ordinal for chunk in chunks] == list(range(len(chunks)))


def test_manifest_is_deterministic_and_session_chains_are_not_interleaved() -> None:
    context = load_main_contexts(DATASET)[0]
    first = MAB8192Manifest.from_context(context, dataset_revision=DATASET_REVISION)
    second = MAB8192Manifest.from_context(context, dataset_revision=DATASET_REVISION)
    assert first.manifest_sha256 == second.manifest_sha256
    assert first.jsonl() == second.jsonl()
    sequence = [(chunk.source_sequence, chunk.chunk_ordinal) for chunk in first.chunks]
    assert sequence == sorted(sequence)
    assert [chunk.global_sequence for chunk in first.chunks] == list(range(len(first.chunks)))
    for index, chunk in enumerate(first.chunks):
        if chunk.chunk_ordinal == 0:
            assert chunk.previous_chunk_id is None
        else:
            assert chunk.previous_chunk_id == first.chunks[index - 1].chunk_id
    assert all(
        chunk.chunk_count == len(first.session_chunks(chunk.session_id))
        for chunk in first.chunks
    )


def test_adapter_identity_is_stable_and_separate_from_body() -> None:
    identity = adapter_identity()
    assert identity["adapter_version"] == MAB8192_ADAPTER_VERSION
    assert identity["chunk_size_characters"] == MAB8192_CHUNK_SIZE
    assert identity["lossless"] is True
    assert len(identity["adapter_sha256"]) == 64
    assert "adapter_sha256" not in split_lossless_body("[USER]\nhello")[0]
