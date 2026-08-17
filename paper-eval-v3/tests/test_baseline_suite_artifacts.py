"""TDD contract for the isolated baseline-suite durable block store."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.baseline_suite import build_baseline_suite_plan
from paper_eval.baseline_suite_artifacts import (
    BaselineBlockArtifactError,
    BaselineBlockStore,
    inspect_baseline_block,
)


SOURCES = tuple(f"{index + 1:064x}" for index in range(4))


def _block(method: str = "U0") -> dict[str, object]:
    plan = build_baseline_suite_plan("bs-artifact-test-001", mode="development")
    return next(deepcopy(block) for block in plan["blocks"] if block["method"] == method)


def _create(tmp_path: Path, method: str = "U0", count: int = 4) -> BaselineBlockStore:
    block = _block(method)
    return BaselineBlockStore.create(
        tmp_path / "block",
        block=block,
        expected_sequences=list(range(count)),
        source_sha256s=SOURCES[:count],
    )


def _event(
    store: BaselineBlockStore,
    event_sequence: int,
    event_type: str,
    source_sequence: int | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {
        "event_sequence": event_sequence,
        "event_type": event_type,
        "run_id": store.block["namespace"],
        "method": store.block["method"],
        "timestamp_ns": 1000 + event_sequence,
    }
    if source_sequence is not None:
        event["source_sequence"] = source_sequence
        event["source_sha256"] = store.source_sha256s[source_sequence]
    return event


def _publish(store: BaselineBlockStore, event_sequence: int, source: int) -> None:
    store.append_event(_event(store, event_sequence, "publication", source))


def _result(store: BaselineBlockStore) -> dict[str, object]:
    return {
        "schema_version": "membind.paper-eval-v3.baseline-block-output.v1",
        "run_id": store.block["namespace"],
        "method": store.block["method"],
        "status": "PASS",
        "metrics_sha256": "f" * 64,
    }


def _reseal(value: dict[str, object], field: str) -> None:
    value[field] = payload_sha256(
        {key: item for key, item in value.items() if key != field}
    )


def test_create_is_manifest_first_hash_bound_and_exclusive(tmp_path: Path) -> None:
    store = _create(tmp_path)

    assert store.manifest_path.is_file()
    assert store.events_path.is_file()
    assert store.checkpoint_path.is_file()
    observed = inspect_baseline_block(store.root, _block())
    assert observed["status"] == "incomplete_non_mergeable"
    assert observed["artifacts_verified"] is False
    assert observed["resume_authorized"] is False
    assert observed["completed_sequences"] == []

    with pytest.raises(BaselineBlockArtifactError, match="block_exists"):
        _create(tmp_path)


def test_serial_publications_must_be_a_unique_source_prefix(tmp_path: Path) -> None:
    store = _create(tmp_path, "U0")

    with pytest.raises(BaselineBlockArtifactError, match="progress|prefix"):
        _publish(store, 0, 1)
    _publish(store, 0, 0)
    with pytest.raises(BaselineBlockArtifactError, match="duplicate_publication"):
        _publish(store, 1, 0)

    inspected = inspect_baseline_block(store.root, _block("U0"))
    assert inspected["completed_sequences"] == [0]
    assert inspected["event_count"] == 1


def test_parallel_publications_may_be_unordered_but_not_duplicate(tmp_path: Path) -> None:
    store = _create(tmp_path, "P(C=2)")
    _publish(store, 0, 1)
    _publish(store, 1, 0)
    _publish(store, 2, 3)

    inspected = inspect_baseline_block(store.root, _block("P(C=2)"))
    assert inspected["completed_sequences"] == [1, 0, 3]
    with pytest.raises(BaselineBlockArtifactError, match="duplicate_publication"):
        _publish(store, 3, 1)


def test_quality_pending_requires_full_publication_set_and_remains_nonmergeable(
    tmp_path: Path,
) -> None:
    store = _create(tmp_path, count=2)
    _publish(store, 0, 0)
    with pytest.raises(BaselineBlockArtifactError, match="full_publication"):
        store.mark_quality_pending()
    _publish(store, 1, 1)

    pending = store.mark_quality_pending()
    assert pending["phase"] == "quality_pending"
    assert pending["status"] == "incomplete_non_mergeable"
    assert pending["artifacts_verified"] is False
    assert pending["resume_authorized"] is False
    assert not store.result_path.exists()


def test_complete_requires_quality_pending_full_set_and_seals_result(
    tmp_path: Path,
) -> None:
    store = _create(tmp_path, count=2)
    _publish(store, 0, 0)
    _publish(store, 1, 1)
    with pytest.raises(BaselineBlockArtifactError, match="quality_pending"):
        store.complete(_result(store))
    store.mark_quality_pending()

    completed = store.complete(_result(store))
    assert completed["status"] == "completed"
    assert completed["artifacts_verified"] is True
    assert completed["resume_authorized"] is False
    inspected = inspect_baseline_block(store.root, _block())
    assert inspected["status"] == "completed"
    assert inspected["artifacts_verified"] is True
    assert inspected["result"]["payload"] == _result(store)


def test_failure_is_sealed_incomplete_nonmergeable_and_never_resumable(
    tmp_path: Path,
) -> None:
    store = _create(tmp_path)
    _publish(store, 0, 0)

    failed = store.fail("builtins.ConnectionError", "construction")
    assert failed["status"] == "incomplete_non_mergeable"
    assert failed["artifacts_verified"] is False
    assert failed["resume_authorized"] is False
    inspected = inspect_baseline_block(store.root, _block())
    assert inspected["phase"] == "failed"
    assert inspected["result"]["payload"] == {
        "error_class": "builtins.ConnectionError",
        "failure_stage": "construction",
    }
    with pytest.raises(BaselineBlockArtifactError, match="terminal"):
        store.append_event(_event(store, 1, "intent", 1))


def test_event_identity_sequence_hash_and_private_fields_fail_closed(
    tmp_path: Path,
) -> None:
    store = _create(tmp_path)
    wrong = _event(store, 0, "intent", 0)
    wrong["method"] = "A0"
    with pytest.raises(BaselineBlockArtifactError, match="event_identity"):
        store.append_event(wrong)
    wrong = _event(store, 0, "intent", 0)
    wrong["source_sha256"] = "e" * 64
    with pytest.raises(BaselineBlockArtifactError, match="event_source"):
        store.append_event(wrong)
    private = _event(store, 0, "intent", 0)
    private["diagnostic"] = {"api_key": "forbidden"}
    with pytest.raises(BaselineBlockArtifactError, match="private"):
        store.append_event(private)


def test_inspection_rejects_expected_identity_drift_and_rehashed_tampering(
    tmp_path: Path,
) -> None:
    store = _create(tmp_path)
    _publish(store, 0, 0)
    foreign = _block()
    foreign["namespace"] = "foreign"
    with pytest.raises(BaselineBlockArtifactError, match="identity"):
        inspect_baseline_block(store.root, foreign)

    record = json.loads(store.events_path.read_text(encoding="utf-8"))
    record["event"]["source_sequence"] = 1
    record["event"]["source_sha256"] = store.source_sha256s[1]
    record["event_sha256"] = payload_sha256(record["event"])
    store.events_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(BaselineBlockArtifactError, match="checkpoint|progress"):
        inspect_baseline_block(store.root, _block())


def test_rehashed_manifest_or_result_private_field_still_fails_closed(
    tmp_path: Path,
) -> None:
    store = _create(tmp_path, count=1)
    _publish(store, 0, 0)
    store.mark_quality_pending()
    store.complete(_result(store))

    result = json.loads(store.result_path.read_text(encoding="utf-8"))
    result["payload"]["raw_response"] = "forbidden"
    _reseal(result, "result_sha256")
    store.result_path.write_text(json.dumps(result) + "\n", encoding="utf-8")
    with pytest.raises(BaselineBlockArtifactError, match="private"):
        inspect_baseline_block(store.root, _block())


def test_rehashed_compound_secret_field_and_impossible_phase_fail_closed(
    tmp_path: Path,
) -> None:
    store = _create(tmp_path)
    _publish(store, 0, 0)
    checkpoint = json.loads(store.checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["phase"] = "planned"
    _reseal(checkpoint, "checkpoint_sha256")
    store.checkpoint_path.write_text(json.dumps(checkpoint) + "\n", encoding="utf-8")
    with pytest.raises(BaselineBlockArtifactError, match="phase|checkpoint"):
        inspect_baseline_block(store.root, _block())

    checkpoint["phase"] = "running"
    _reseal(checkpoint, "checkpoint_sha256")
    store.checkpoint_path.write_text(json.dumps(checkpoint) + "\n", encoding="utf-8")
    record = json.loads(store.events_path.read_text(encoding="utf-8"))
    record["event"]["diagnostic"] = {"client_secret": "forbidden"}
    record["event_sha256"] = payload_sha256(record["event"])
    store.events_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(BaselineBlockArtifactError, match="private"):
        inspect_baseline_block(store.root, _block())


def test_result_identity_and_private_content_are_rejected(tmp_path: Path) -> None:
    store = _create(tmp_path, count=1)
    _publish(store, 0, 0)
    store.mark_quality_pending()
    result = _result(store)
    result["run_id"] = "foreign"
    with pytest.raises(BaselineBlockArtifactError, match="result_identity"):
        store.complete(result)
    result = _result(store)
    result["content"] = "raw benchmark material"
    with pytest.raises(BaselineBlockArtifactError, match="private"):
        store.complete(result)


def test_create_rejects_noncanonical_or_unbounded_source_inventory(
    tmp_path: Path,
) -> None:
    block = _block()
    with pytest.raises(BaselineBlockArtifactError, match="expected_sequences"):
        BaselineBlockStore.create(
            tmp_path / "bad-sequences",
            block=block,
            expected_sequences=[1, 0],
            source_sha256s=SOURCES[:2],
        )
    with pytest.raises(BaselineBlockArtifactError, match="source_identity"):
        BaselineBlockStore.create(
            tmp_path / "invalid-source",
            block=block,
            expected_sequences=[0, 1],
            source_sha256s=[SOURCES[0], "not-a-sha256"],
        )
    with pytest.raises(BaselineBlockArtifactError, match="source_inventory_too_large"):
        BaselineBlockStore.create(
            tmp_path / "too-large",
            block=block,
            expected_sequences=list(range(10_001)),
            source_sha256s=["a" * 64] * 10_001,
        )
