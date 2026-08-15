"""Offline RED/GREEN contracts for the bilateral S4 candidate sidecar."""

from __future__ import annotations

import asyncio
import copy
import hashlib
from pathlib import Path

import pytest

from paper_eval.s4_candidate_sidecar import (
    CandidateSidecarError,
    CaptureSidecarStore,
    ReplaySidecarBinder,
    activate_replay_binding,
    build_candidate_call_record,
    current_replay_binding,
    load_capture_sidecar,
    remap_edge_response,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


IDENTITY = {
    "attempt_id": "006",
    "cache_id": "s4-d0-sidecar-07741c45-20260815-006",
    "history_id": "07741c45",
    "episode_manifest_sha256": _sha("manifest"),
    "projection_schema_sha256": _sha("projection"),
}


def _candidate(
    candidate_id: int,
    *,
    fact: str,
    logical_identity: str,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "fact_sha256": _sha(fact),
        "logical_identity_sha256": _sha(logical_identity),
    }


def _record(
    *,
    source_sequence: int = 7,
    call: str = "call-1",
    prompt: str = "prompt-1",
    related: list[dict] | None = None,
    invalidation: list[dict] | None = None,
) -> dict:
    related = related or []
    invalidation = invalidation or []
    return build_candidate_call_record(
        source_sequence=source_sequence,
        source_hash=_sha(f"source-{source_sequence}"),
        logical_call_sha256=_sha(call),
        prompt_sha256=_sha(prompt),
        related=related,
        invalidation=invalidation,
    )


def test_same_fact_different_directed_endpoints_remaps_by_logical_identity() -> None:
    capture = _record(
        invalidation=[
            _candidate(0, fact="shared", logical_identity="alice->google"),
            _candidate(1, fact="shared", logical_identity="bob->google"),
        ]
    )
    binder = ReplaySidecarBinder(identity=IDENTITY, records=[capture])
    binding = binder.bind(
        source_sequence=7,
        source_hash=_sha("source-7"),
        logical_call_sha256=_sha("call-1"),
        related=[],
        invalidation=[
            _candidate(0, fact="shared", logical_identity="bob->google"),
            _candidate(1, fact="shared", logical_identity="alice->google"),
        ],
    )

    assert binding["invalidation_id_map"] == {0: 1, 1: 0}
    assert binding["related_id_map"] == {}
    assert binder.consumed_count == 1


def test_same_fact_and_endpoints_different_provenance_remaps() -> None:
    capture = _record(
        invalidation=[
            _candidate(0, fact="shared", logical_identity="edge@source-3"),
            _candidate(1, fact="shared", logical_identity="edge@source-5"),
        ]
    )
    binder = ReplaySidecarBinder(identity=IDENTITY, records=[capture])

    binding = binder.bind(
        source_sequence=7,
        source_hash=_sha("source-7"),
        logical_call_sha256=_sha("call-1"),
        related=[],
        invalidation=[
            _candidate(0, fact="shared", logical_identity="edge@source-5"),
            _candidate(1, fact="shared", logical_identity="edge@source-3"),
        ],
    )

    assert binding["invalidation_id_map"] == {0: 1, 1: 0}


def test_related_and_invalidation_permutations_translate_response_independently() -> None:
    capture = _record(
        related=[
            _candidate(0, fact="r1", logical_identity="r1"),
            _candidate(1, fact="r2", logical_identity="r2"),
        ],
        invalidation=[
            _candidate(2, fact="i1", logical_identity="i1"),
            _candidate(3, fact="i2", logical_identity="i2"),
        ],
    )
    binder = ReplaySidecarBinder(identity=IDENTITY, records=[capture])
    binding = binder.bind(
        source_sequence=7,
        source_hash=_sha("source-7"),
        logical_call_sha256=_sha("call-1"),
        related=[
            _candidate(0, fact="r2", logical_identity="r2"),
            _candidate(1, fact="r1", logical_identity="r1"),
        ],
        invalidation=[
            _candidate(2, fact="i2", logical_identity="i2"),
            _candidate(3, fact="i1", logical_identity="i1"),
        ],
    )

    assert remap_edge_response(
        {"duplicate_facts": [0], "contradicted_facts": [1, 2, 3]},
        binding,
    ) == {"duplicate_facts": [1], "contradicted_facts": [0, 3, 2]}


def test_fully_identical_logical_identities_fail_closed() -> None:
    with pytest.raises(CandidateSidecarError, match="AMBIGUOUS"):
        _record(
            invalidation=[
                _candidate(0, fact="shared", logical_identity="same"),
                _candidate(1, fact="shared", logical_identity="same"),
            ]
        )


def test_membership_and_partition_drift_fail_closed() -> None:
    capture = _record(
        related=[_candidate(0, fact="one", logical_identity="one")],
        invalidation=[_candidate(1, fact="two", logical_identity="two")],
    )

    with pytest.raises(CandidateSidecarError, match="MEMBERSHIP"):
        ReplaySidecarBinder(identity=IDENTITY, records=[capture]).bind(
            source_sequence=7,
            source_hash=_sha("source-7"),
            logical_call_sha256=_sha("call-1"),
            related=[_candidate(0, fact="other", logical_identity="other")],
            invalidation=[_candidate(1, fact="two", logical_identity="two")],
        )

    with pytest.raises(CandidateSidecarError, match="PARTITION"):
        ReplaySidecarBinder(identity=IDENTITY, records=[capture]).bind(
            source_sequence=7,
            source_hash=_sha("source-7"),
            logical_call_sha256=_sha("call-1"),
            related=[_candidate(0, fact="two", logical_identity="two")],
            invalidation=[_candidate(1, fact="one", logical_identity="one")],
        )


def test_call_correlation_collision_and_second_consumption_fail() -> None:
    record = _record()
    with pytest.raises(CandidateSidecarError, match="COLLISION"):
        ReplaySidecarBinder(identity=IDENTITY, records=[record, copy.deepcopy(record)])

    binder = ReplaySidecarBinder(identity=IDENTITY, records=[record])
    kwargs = {
        "source_sequence": 7,
        "source_hash": _sha("source-7"),
        "logical_call_sha256": _sha("call-1"),
        "related": [],
        "invalidation": [],
    }
    binder.bind(**kwargs)
    with pytest.raises(CandidateSidecarError, match="CONSUMED"):
        binder.bind(**kwargs)


def test_replay_binding_prepare_commits_only_after_oracle_success() -> None:
    record = _record()
    binder = ReplaySidecarBinder(identity=IDENTITY, records=[record])
    kwargs = {
        "source_sequence": 7,
        "source_hash": _sha("source-7"),
        "logical_call_sha256": _sha("call-1"),
        "related": [],
        "invalidation": [],
    }

    lease = binder.prepare(**kwargs)
    assert binder.consumed_count == 0
    with pytest.raises(CandidateSidecarError, match="PREPARED"):
        binder.prepare(**kwargs)
    lease.rollback()
    assert binder.consumed_count == 0

    committed = binder.prepare(**kwargs)
    committed.commit()
    assert binder.consumed_count == 1
    with pytest.raises(CandidateSidecarError, match="finalized"):
        committed.rollback()


def test_capture_store_is_durable_resumable_and_sealed_for_replay(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate-sidecar.jsonl"
    first = _record(source_sequence=0, call="call-0", prompt="prompt-0")
    second = _record(source_sequence=1, call="call-1", prompt="prompt-1")

    store = CaptureSidecarStore.create(path, identity=IDENTITY)
    store.append(first)
    resumed = CaptureSidecarStore.resume(path, identity=IDENTITY)
    resumed.append(second)
    seal = resumed.seal()

    assert seal["record_count"] == 2
    assert len(seal["records_sha256"]) == 64
    loaded = load_capture_sidecar(path, expected_identity=IDENTITY)
    assert loaded["identity"] == IDENTITY
    assert loaded["records"] == [first, second]
    assert loaded["seal"] == seal
    with pytest.raises(CandidateSidecarError, match="sealed"):
        CaptureSidecarStore.resume(path, identity=IDENTITY)


def test_sidecar_seal_binds_cache_hashes_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "candidate-sidecar.jsonl"
    store = CaptureSidecarStore.create(path, identity=IDENTITY)
    store.append(_record())
    evidence = {
        "prompt_cache_sha256": _sha("prompt-cache"),
        "embedding_cache_sha256": _sha("embedding-cache"),
    }

    first = store.seal(cache_evidence=evidence)
    second = store.seal(cache_evidence=copy.deepcopy(evidence))

    assert first == second
    assert first["cache_evidence"] == evidence
    assert len(first["episode_call_counts_sha256"]) == 64
    assert load_capture_sidecar(path, expected_identity=IDENTITY)["seal"] == first
    with pytest.raises(CandidateSidecarError, match="sealed evidence"):
        store.seal(
            cache_evidence={
                **evidence,
                "prompt_cache_sha256": _sha("different"),
            }
        )


def test_sidecar_core_accepts_authority_bound_fixed_history_identity() -> None:
    identity = {
        "attempt_id": "qualification-001",
        "cache_id": "s4q-d0-sidecar-b6019101-001",
        "history_id": "b6019101",
        "episode_manifest_sha256": _sha("other-manifest"),
        "projection_schema_sha256": _sha("projection"),
    }
    binder = ReplaySidecarBinder(
        identity=identity,
        records=[_record(source_sequence=50)],
    )

    assert binder.identity == identity


def test_replay_binder_restores_checkpoint_consumption_prefix() -> None:
    records = [
        _record(source_sequence=0, call="call-0"),
        _record(source_sequence=1, call="call-1"),
    ]
    binder = ReplaySidecarBinder(
        identity=IDENTITY,
        records=records,
        consumed_source_sequences=[0],
    )

    assert binder.consumed_count == 1
    assert binder.resumed_consumed_count == 1
    assert binder.remaining_for_source(0) == 0
    assert binder.remaining_for_source(1) == 1


def test_capture_store_rejects_duplicate_resume_record_and_file_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate-sidecar.jsonl"
    record = _record()
    store = CaptureSidecarStore.create(path, identity=IDENTITY)
    store.append(record)
    with pytest.raises(CandidateSidecarError, match="duplicate"):
        store.append(record)
    store.seal()

    path.write_text(path.read_text() + "{}\n", encoding="ascii")
    with pytest.raises(CandidateSidecarError):
        load_capture_sidecar(path, expected_identity=IDENTITY)


def test_capture_store_ensure_is_idempotent_but_rejects_key_conflicts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate-sidecar.jsonl"
    record = _record()
    store = CaptureSidecarStore.create(path, identity=IDENTITY)

    assert store.ensure(record) is True
    assert store.ensure(copy.deepcopy(record)) is False
    conflicting = _record(prompt="different-prompt")
    with pytest.raises(CandidateSidecarError, match="conflicting"):
        store.ensure(conflicting)


@pytest.mark.parametrize(
    "forbidden",
    ["raw_fact", "raw_prompt", "raw_response", "uuid", "created_at", "group_id"],
)
def test_sidecar_rejects_raw_or_volatile_public_fields(forbidden: str) -> None:
    record = _record()
    record[forbidden] = "private"

    with pytest.raises(CandidateSidecarError):
        ReplaySidecarBinder(identity=IDENTITY, records=[record])


def test_sidecar_file_hash_is_immutable_across_read_only_binding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "candidate-sidecar.jsonl"
    store = CaptureSidecarStore.create(path, identity=IDENTITY)
    store.append(_record())
    store.seal()
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    loaded = load_capture_sidecar(path, expected_identity=IDENTITY)
    binder = ReplaySidecarBinder(
        identity=loaded["identity"], records=loaded["records"]
    )
    binder.bind(
        source_sequence=7,
        source_hash=_sha("source-7"),
        logical_call_sha256=_sha("call-1"),
        related=[],
        invalidation=[],
    )

    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_replay_binding_context_copies_input_and_restores_nested_state() -> None:
    outer = {"name": "outer", "nested": {"value": 1}}
    inner = {"name": "inner"}

    assert current_replay_binding() is None
    with activate_replay_binding(outer):
        outer["nested"]["value"] = 2
        assert current_replay_binding() == {
            "name": "outer",
            "nested": {"value": 1},
        }
        with activate_replay_binding(inner):
            assert current_replay_binding() == inner
        assert current_replay_binding()["name"] == "outer"
    assert current_replay_binding() is None


def test_replay_binding_context_is_task_local_and_restored_after_error() -> None:
    async def observe(name: str) -> str:
        with activate_replay_binding({"name": name}):
            await asyncio.sleep(0)
            return str(current_replay_binding()["name"])

    async def run() -> list[str]:
        return list(await asyncio.gather(observe("one"), observe("two")))

    assert asyncio.run(run()) == ["one", "two"]
    with pytest.raises(RuntimeError, match="stop"):
        with activate_replay_binding({"name": "error"}):
            raise RuntimeError("stop")
    assert current_replay_binding() is None
