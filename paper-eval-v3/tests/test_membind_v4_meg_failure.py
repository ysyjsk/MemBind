from __future__ import annotations

import json

from paper_eval.membind_v4.mseg.failure import OPAQUE, SemanticFailureRecord
from paper_eval.membind_v4.mseg.runtime_instrumentation import (
    InstrumentationMode,
    MEGRuntimeRecorder,
)
from paper_eval.membind_v4.mseg.version_token import VersionTokenFactory


def _token():
    return VersionTokenFactory(backend_id="neo4j", epoch="offline").commit(
        namespace="failure-fixture",
        transaction_id="tx-1",
        evidence_hash="a" * 64,
    )


def test_failure_record_retains_explicit_cause_chain_and_root() -> None:
    try:
        try:
            raise LookupError("driver root")
        except LookupError as root:
            raise RuntimeError("adapter wrapper") from root
    except RuntimeError as error:
        record = SemanticFailureRecord.from_exception(
            error,
            run_id="offline-run",
            source_sequence=2,
            phase="BIND",
            semantic_operator_id="op-1",
            semantic_operator_type="PERSIST_AND_PUBLISH",
            semantic_subrequest_role="transaction.commit",
            request_id="production-request-1",
            parent_semantic_operator_id="op-parent",
            last_completed_semantic_predecessors=("op-parent",),
            memory_version_token=_token(),
            mutation_epoch=3,
            transaction_started=True,
            transaction_committed=False,
            persistent_effect_started=True,
            publication_started=False,
            implementation_seam_hash="b" * 64,
            top_level_classification="bind_failed",
        )

    assert record.exception_type.endswith("RuntimeError")
    assert record.root_exception_type.endswith("LookupError")
    assert [item["exception_message"] for item in record.exception_chain] == [
        "adapter wrapper",
        "driver root",
    ]
    assert record.causality_observable is True
    assert record.traceback_hash != OPAQUE
    payload = json.loads(json.dumps(record.to_dict(), default=str))
    assert payload["semantic_operator_id"] == "op-1"


def test_failure_record_is_explicitly_opaque_when_no_exception_is_available() -> None:
    record = SemanticFailureRecord.from_exception(
        None,
        run_id="historical-run",
        phase="BIND",
        top_level_classification="bind_failed",
    )
    assert record.root_exception_type == OPAQUE
    assert record.root_exception_message == OPAQUE
    assert record.traceback_hash == OPAQUE
    assert record.semantic_operator_id == OPAQUE
    assert record.causality_observable is False


def test_recorder_keeps_failure_records_outside_runtime_event_order() -> None:
    recorder = MEGRuntimeRecorder(mode=InstrumentationMode.OBSERVE_ONLY)
    record = SemanticFailureRecord.from_exception(
        ValueError("parse failed"), run_id="run", phase="LLM_PARSE"
    )
    recorder.record_failure(record)
    assert recorder.failure_records == (record,)
    assert recorder.events == ()
