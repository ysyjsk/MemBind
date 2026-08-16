"""TDD tests for the isolated S5 durable attempt store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s5_durable_attempt_store import (
    S5AttemptStore,
    S5StoreError,
    inspect_s5_attempt,
)


IDENTITY = "a" * 64
SOURCES = tuple(f"{index + 1:064x}" for index in range(2))


def _event(sequence: int, source: int) -> dict[str, object]:
    return {
        "event_sequence": sequence,
        "event_type": "intent",
        "run_id": "s5-mstar-store-001",
        "method": "M*",
        "source_sequence": source,
        "source_sha256": SOURCES[source],
        "logical_time_ns": 100 + source,
    }


def _publication(sequence: int, source: int) -> dict[str, object]:
    return {
        "event_sequence": sequence,
        "event_type": "publication",
        "run_id": "s5-mstar-store-001",
        "method": "M*",
        "source_sequence": source,
        "source_sha256": SOURCES[source],
        "publication_timestamp_ns": 200 + source,
    }


def _evidence(events: list[dict[str, object]], *, status: str = "PASS") -> dict[str, object]:
    return {
        "schema_version": "membind.paper-eval-v3.s5-mstar-pipeline-evidence.v1",
        "run_id": "s5-mstar-store-001",
        "method": "M*",
        "production_core_identity_sha256": IDENTITY,
        "status": status,
        "mergeable": status == "PASS",
        "failure_code": None if status == "PASS" else "LATEST_STATE_BIND_FAILED",
        "events": events,
        "summary": {
            "configured_prepare_concurrency": 2,
            "observed_prepare_worker_ids": [0, 1],
            "max_active_prepare": 2,
            "prepare_overlap_observed": True,
            "max_active_bind": 1,
            "intent_count": 2,
            "prepared_count": 0,
            "publication_count": 0,
            "published_source_sequences": [],
            "fallback_count": 0,
        },
    }


def _create(tmp_path: Path) -> S5AttemptStore:
    return S5AttemptStore.create(
        tmp_path / "attempt",
        run_id="s5-mstar-store-001",
        method="M*",
        production_core_identity_sha256=IDENTITY,
        source_sha256s=SOURCES,
    )


def _reseal(value: dict[str, object], seal_field: str) -> None:
    value[seal_field] = payload_sha256(
        {key: item for key, item in value.items() if key != seal_field}
    )


def test_manifest_first_append_hashes_events_and_rejects_duplicate_attempt(tmp_path: Path) -> None:
    store = _create(tmp_path)
    assert store.manifest_path.is_file()
    assert store.events_path.is_file()
    assert store.checkpoint_path.is_file()
    store.append_event(_event(0, 0))
    store.append_event(_event(1, 1))
    inspected = inspect_s5_attempt(store.root)
    assert inspected["manifest"]["status"] == "planned"
    assert inspected["event_count"] == 2
    with pytest.raises(S5StoreError, match="attempt_exists"):
        _create(tmp_path)


def test_each_durable_event_advances_atomic_nonresumable_checkpoint(
    tmp_path: Path,
) -> None:
    store = _create(tmp_path)

    store.append_event(_event(0, 0))
    after_intent = inspect_s5_attempt(store.root)["checkpoint"]
    assert after_intent["status"] == "running"
    assert after_intent["event_count"] == 1
    assert after_intent["published_source_sequences"] == []
    assert after_intent["result_payload_sha256"] is None
    assert after_intent["resume_authorized"] is False

    store.append_event(_publication(1, 0))
    after_publication = inspect_s5_attempt(store.root)["checkpoint"]
    assert after_publication["status"] == "running"
    assert after_publication["event_count"] == 2
    assert after_publication["published_source_sequences"] == [0]
    assert after_publication["result_payload_sha256"] is None


def test_append_is_contiguous_and_sanitized(tmp_path: Path) -> None:
    store = _create(tmp_path)
    with pytest.raises(S5StoreError, match="event_sequence_invalid"):
        store.append_event(_event(1, 0))
    private = _event(0, 0)
    private["prompt"] = "forbidden"
    with pytest.raises(S5StoreError, match="private_or_legacy_field"):
        store.append_event(private)
    store.append_event(_event(0, 0))
    with pytest.raises(S5StoreError, match="event_sequence_invalid"):
        store.append_event(_event(0, 0))


def test_tampered_event_hash_fails_closed(tmp_path: Path) -> None:
    store = _create(tmp_path)
    store.append_event(_event(0, 0))
    raw = json.loads(store.events_path.read_text(encoding="utf-8"))
    raw["event"]["source_sequence"] = 1
    store.events_path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    with pytest.raises(S5StoreError, match="event_hash_invalid"):
        inspect_s5_attempt(store.root)


def test_rehashed_private_event_still_fails_closed(tmp_path: Path) -> None:
    store = _create(tmp_path)
    store.append_event(_event(0, 0))
    raw = json.loads(store.events_path.read_text(encoding="utf-8"))
    raw["event"]["password"] = "forbidden"
    from paper_eval.artifacts import payload_sha256

    raw["event_sha256"] = payload_sha256(raw["event"])
    store.events_path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    with pytest.raises(S5StoreError, match="private_or_legacy_field"):
        inspect_s5_attempt(store.root)


def test_finalize_binds_result_and_writes_atomic_checkpoint(tmp_path: Path) -> None:
    store = _create(tmp_path)
    events = [_event(0, 0), _event(1, 1)]
    for event in events:
        store.append_event(event)
    evidence = _evidence(events)
    result = store.finalize(evidence)
    assert result["status"] == "complete"
    assert store.result_path.is_file()
    inspected = inspect_s5_attempt(store.root)
    assert inspected["checkpoint"]["status"] == "complete"
    assert inspected["result"]["payload"] == evidence
    assert not list(store.root.glob(".*.tmp"))


def test_finalize_derives_published_sources_from_durable_events_not_summary(
    tmp_path: Path,
) -> None:
    store = _create(tmp_path)
    events = [_event(0, 0), _publication(1, 0), _event(2, 1)]
    for event in events:
        store.append_event(event)
    evidence = _evidence(events)
    # Deliberately omit the publication prefix from the producer summary.  The
    # durable store owns this accounting and must rederive it from JSONL.
    evidence["summary"].pop("published_source_sequences")

    store.finalize(evidence)

    checkpoint = inspect_s5_attempt(store.root)["checkpoint"]
    assert checkpoint["published_source_sequences"] == [0]


def test_inspection_rejects_checkpoint_event_count_or_publication_drift(
    tmp_path: Path,
) -> None:
    store = _create(tmp_path)
    store.append_event(_event(0, 0))
    checkpoint = json.loads(store.checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["event_count"] = 0
    from paper_eval.artifacts import payload_sha256

    checkpoint["checkpoint_sha256"] = payload_sha256(
        {
            key: value
            for key, value in checkpoint.items()
            if key != "checkpoint_sha256"
        }
    )
    store.checkpoint_path.write_text(json.dumps(checkpoint) + "\n", encoding="utf-8")

    with pytest.raises(S5StoreError, match="checkpoint_event_binding_invalid"):
        inspect_s5_attempt(store.root)


def test_finalize_rejects_event_or_identity_mismatch(tmp_path: Path) -> None:
    store = _create(tmp_path)
    store.append_event(_event(0, 0))
    with pytest.raises(S5StoreError, match="result_event_binding_invalid"):
        store.finalize(_evidence([_event(0, 0), _event(1, 1)]))
    private = _evidence([_event(0, 0)])
    private["api_key"] = "forbidden"
    with pytest.raises(S5StoreError, match="private_or_legacy_field"):
        store.finalize(private)


def test_failed_evidence_is_terminal_non_mergeable_and_not_resumable(tmp_path: Path) -> None:
    store = _create(tmp_path)
    events = [_event(0, 0), _event(1, 1)]
    for event in events:
        store.append_event(event)
    # The store records the sealed attempt; it does not authorize in-place resume.
    evidence = _evidence(events, status="FAIL_CLOSED")
    result = store.finalize(evidence)
    assert result["status"] == "incomplete_non_mergeable"
    inspected = inspect_s5_attempt(store.root)
    assert inspected["checkpoint"]["status"] == "incomplete_non_mergeable"
    assert inspected["resume_authorized"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "s5-mstar-different-001"),
        ("method", "A0"),
        ("source_sha256", "f" * 64),
    ],
)
def test_inspection_rejects_rehashed_event_manifest_binding_drift(
    tmp_path: Path, field: str, value: object
) -> None:
    store = _create(tmp_path)
    store.append_event(_event(0, 0))
    record = json.loads(store.events_path.read_text(encoding="utf-8"))
    record["event"][field] = value
    record["event_sha256"] = payload_sha256(record["event"])
    store.events_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(S5StoreError, match="event_manifest_binding_invalid"):
        inspect_s5_attempt(store.root)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(production_core_identity_sha256="f" * 64),
        lambda payload: payload["events"][0].update(source_sha256="f" * 64),
        lambda payload: payload["events"].append(_event(2, 0)),
    ],
)
def test_inspection_rejects_resealed_result_payload_cross_binding_drift(
    tmp_path: Path, mutation
) -> None:
    store = _create(tmp_path)
    events = [_event(0, 0), _event(1, 1)]
    for event in events:
        store.append_event(event)
    store.finalize(_evidence(events))

    result = json.loads(store.result_path.read_text(encoding="utf-8"))
    mutation(result["payload"])
    _reseal(result, "result_sha256")
    store.result_path.write_text(json.dumps(result) + "\n", encoding="utf-8")

    checkpoint = json.loads(store.checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["result_payload_sha256"] = payload_sha256(result["payload"])
    _reseal(checkpoint, "checkpoint_sha256")
    store.checkpoint_path.write_text(json.dumps(checkpoint) + "\n", encoding="utf-8")

    with pytest.raises(S5StoreError, match="result_manifest_binding_invalid"):
        inspect_s5_attempt(store.root)


def test_inspection_rejects_resealed_manifest_source_inventory_drift(
    tmp_path: Path,
) -> None:
    store = _create(tmp_path)
    store.append_event(_event(0, 0))
    manifest = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    manifest["source_sha256s"][0] = "f" * 64
    _reseal(manifest, "manifest_sha256")
    store.manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    with pytest.raises(S5StoreError, match="event_manifest_binding_invalid"):
        inspect_s5_attempt(store.root)
