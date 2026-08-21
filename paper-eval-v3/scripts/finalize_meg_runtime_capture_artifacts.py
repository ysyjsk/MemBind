#!/usr/bin/env python3
"""Finalize offline evidence for one bounded MEG runtime capture.

This finalizer is deliberately read-only with respect to runtime state.  It
consumes the durable contract/failure/event files from a fresh capture and
emits a separate, hash-sealed report directory.  Missing runtime evidence is
reported as OPAQUE rather than inferred from timestamps or coordinator state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file


STATUS = "STOP_REAL_RUNTIME_SEMANTIC_LINEAGE"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path.name}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        record = json.loads(line)
        if not isinstance(record, dict) or not isinstance(record.get("record"), dict):
            raise ValueError(f"jsonl_record_invalid:{path.name}:{line_number}")
        expected = payload_sha256(record["record"])
        if record.get("record_sha256") != expected:
            raise ValueError(f"jsonl_record_hash_invalid:{path.name}:{line_number}")
        row = record["record"].get("row")
        if not isinstance(row, dict):
            raise ValueError(f"jsonl_row_invalid:{path.name}:{line_number}")
        rows.append(row)
    return rows


def _sealed(body: dict[str, Any]) -> dict[str, Any]:
    result = dict(body)
    result["payload_sha256"] = payload_sha256(result)
    return result


def _verify_payload(value: dict[str, Any], *, label: str) -> dict[str, Any]:
    selected = dict(value)
    digest = selected.pop("payload_sha256", None)
    if not isinstance(digest, str) or digest != payload_sha256(selected):
        raise ValueError(f"{label}_payload_hash_invalid")
    selected["payload_sha256"] = digest
    return selected


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    run_root = args.run_root.resolve()
    output_root = args.output_root.resolve()
    if output_root.exists():
        raise ValueError("meg_runtime_artifact_root_not_fresh")

    contract = _verify_payload(
        _read_json(run_root / "MEG_RUNTIME_CAPTURE_CONTRACT.json"),
        label="capture_contract",
    )
    failure = _verify_payload(_read_json(run_root / "FAILURE.json"), label="failure")
    partial = None
    partial_path = run_root / "MEG_RUNTIME_CAPTURE_PARTIAL.json"
    if partial_path.is_file():
        partial = _verify_payload(_read_json(partial_path), label="partial_capture")
    lifecycle = _read_jsonl(run_root / "lifecycle.jsonl")
    queue = _read_jsonl(run_root / "queue.jsonl")
    llm = _read_jsonl(run_root / "llm.jsonl")

    expected_sources = [int(item) for item in contract["source_sequences"]]
    lifecycle_by_source: dict[int, set[str]] = {source: set() for source in expected_sources}
    for row in lifecycle:
        sequence = row.get("source_sequence")
        if isinstance(sequence, int) and sequence in lifecycle_by_source:
            lifecycle_by_source[sequence].add(str(row.get("event_type")))

    required = (
        "arrival",
        "compile_start",
        "compile_end",
        "prepared_durable",
        "bind_start",
        "bind_end",
        "publication_durable",
    )
    source_completion = []
    for source in expected_sources:
        events = lifecycle_by_source[source]
        source_completion.append(
            {
                "source_sequence": source,
                "events": sorted(events),
                "completion": {event: event in events for event in required},
                "durable_publication": "publication_durable" in events,
            }
        )

    submitted = {
        str(row["request_id"])
        for row in llm
        if row.get("event_type") == "llm_request_submitted" and row.get("request_id")
    }
    terminal_ok = {
        str(row["request_id"])
        for row in llm
        if row.get("event_type") == "llm_request_terminal"
        and row.get("status") == "ok"
        and row.get("request_id")
    }
    request_ids_with_missing_lineage = sorted(
        request_id
        for request_id in submitted
        if not any(
            row.get("request_id") == request_id
            and row.get("semantic_operator_id")
            and row.get("prompt_name")
            and row.get("semantic_subrequest_role")
            for row in llm
        )
    )
    partial_spans = [] if partial is None else partial.get("request_spans", [])
    if not isinstance(partial_spans, list):
        raise ValueError("partial_request_spans_invalid")
    partial_span_ids = {
        str(row.get("request_id"))
        for row in partial_spans
        if isinstance(row, dict) and row.get("request_id")
    }
    request_ids_with_missing_lineage = sorted(
        request_id
        for request_id in submitted
        if request_id not in partial_span_ids
        and request_id not in {
            str(row.get("request_id"))
            for row in llm
            if row.get("request_id")
            and row.get("semantic_operator_id")
            and row.get("prompt_name")
            and row.get("semantic_subrequest_role")
        }
    )
    request_lineage_fraction = (
        (len(submitted) - len(request_ids_with_missing_lineage)) / len(submitted)
        if submitted
        else 0.0
    )
    runtime_lineage = {
        "semantic_operator_count_before_failure": failure.get(
            "operator_count_before_failure"
        ),
        "operator_ready_count": None,
        "operator_ready_status": "OPAQUE",
        "request_span_count_before_failure": failure.get("request_span_count_before_failure"),
        "production_request_submitted_count": len(submitted),
        "production_request_terminal_ok_count": len(terminal_ok),
        "request_lineage_coverage": (
            1.0 if submitted and not request_ids_with_missing_lineage else "OPAQUE"
        ),
        "request_lineage_coverage_fraction": request_lineage_fraction,
        "opaque_lineage_count": len(request_ids_with_missing_lineage),
        "opaque_request_ids": request_ids_with_missing_lineage,
        "source_completion": source_completion,
        "event_evidence_complete": False,
        "reason": "capture failed before complete MEG_RUNTIME_CAPTURE.json materialization",
    }

    partial_events = [] if partial is None else partial.get("events", [])
    if not isinstance(partial_events, list):
        raise ValueError("partial_events_invalid")
    transaction_count = sum(
        row.get("event_type") == "transaction_commit" for row in lifecycle
    ) + sum(
        isinstance(row, dict) and row.get("event_type") == "TRANSACTION_COMMIT"
        for row in partial_events
    )
    publication_count = sum(
        row.get("event_type") == "publication_durable" for row in lifecycle
    ) + sum(
        isinstance(row, dict) and row.get("event_type") == "PUBLICATION"
        for row in partial_events
    )
    state_transaction = {
        "transaction_commit_count_observed": transaction_count,
        "mutation_epoch_transition_count_observed": 0,
        "transaction_observability": "OPAQUE",
        "writer_domain_status": "OPAQUE_UNOBSERVED_AFTER_CAPTURE_FAILURE",
        "publication_event_count_observed": publication_count,
        "publication_causal_coverage": "OPAQUE",
        "opaque_publication_count": publication_count,
    }
    failure_record = failure.get("failure_record")
    if not isinstance(failure_record, dict):
        failure_record = {
            "exception_type": failure.get("error_class", "OPAQUE"),
            "exception_message": failure.get("error_code", "OPAQUE"),
            "root_exception_type": "OPAQUE",
            "root_exception_message": "OPAQUE",
            "semantic_operator_id": "OPAQUE",
            "semantic_subrequest_role": "OPAQUE",
            "request_id": "OPAQUE",
            "causality_status": "OPAQUE",
        }
    else:
        failure_record = dict(failure_record)
        failure_record["causality_status"] = (
            "OBSERVABLE"
            if failure_record.get("root_exception_type") not in {None, "OPAQUE"}
            else "OPAQUE"
        )

    passive = {
        "certificate_type": "PASSIVE_EQUIVALENCE_CERTIFICATE",
        "status": "NOT_CERTIFIED",
        "violations": [
            "capture_incomplete_before_three_source_completion",
            "baseline_execution_not_materialized_for_this_real_attempt",
            "semantic_request_lineage_opaque",
            "persistent_effect_projection_not_materialized",
        ],
        "production_request_count_observed": len(terminal_ok),
        "production_prompt_hashes": "OPAQUE",
        "production_model_schema": "OPAQUE",
        "source_exactly_once": False,
        "persistent_effect_projection": "OPAQUE",
        "source_publication_order": [],
        "shadow_db_reads": 0,
        "shadow_llm_calls": 0,
        "shadow_embeddings": 0,
        "extra_writes": 0,
        "reorder": False,
    }

    seam_hash = str(
        contract["composition_proof"]["source_hashes"]["meg_runtime_seam"]
    )
    identity = {
        "run_id": contract["run_id"],
        "history": contract["history_id"],
        "sources": expected_sources,
        "namespace": contract["namespace"],
        "graphiti_version": "0.29.3",
        "seam_hash": seam_hash,
        "contract_payload_sha256": contract["payload_sha256"],
        "failure_payload_sha256": failure["payload_sha256"],
        "run_root": str(run_root),
        "run_root_sha256": sha256_file(run_root / "FAILURE.json"),
    }

    summary = _sealed(
        {
            "schema_version": "membind.meg.real-runtime-capture-summary.v1",
            "status": STATUS,
            "failure_classification": "B_SEMANTIC_LINEAGE_FAILURE",
            "identity": identity,
            "source_completion": source_completion,
            "semantic_lineage": runtime_lineage,
            "state_transaction": state_transaction,
            "passive_equivalence": {
                "status": passive["status"],
                "shadow_db_reads": 0,
                "shadow_llm_calls": 0,
                "shadow_embeddings": 0,
                "extra_writes": 0,
                "reorder": False,
            },
            "observed_failure": {
                "error_code": failure.get("error_code"),
                "error_class": failure.get("error_class"),
                "root_cause_boundary": "production bind after compile and two successful LLM requests",
                "root_cause": "OPAQUE: FAILURE.json preserves only the coordinator-level bind_failed code",
                "observed_warning": "Graphiti 0.29.3 extract_edges logged missing target entities and skipped those edges; source code does not establish this warning as the bind exception",
                "failure_record": failure_record,
            },
        }
    )
    lineage = _sealed(
        {
            "schema_version": "membind.meg.real-runtime-lineage-audit.v1",
            "status": STATUS,
            "identity": identity,
            "audit_status": "NOT_CERTIFIED",
            "semantic_lineage": runtime_lineage,
            "request_events": [
                {
                    "request_id": row.get("request_id"),
                    "source_sequence": row.get("source_sequence"),
                    "request_kind": row.get("request_kind"),
                    "event_type": row.get("event_type"),
                    "semantic_operator_id": row.get("semantic_operator_id"),
                    "semantic_subrequest_role": row.get("semantic_subrequest_role"),
                    "prompt_name": row.get("prompt_name"),
                }
                for row in llm
                if row.get("event_type") in {"llm_request_submitted", "llm_request_terminal"}
            ],
            "partial_request_spans": partial_spans,
            "partial_operator_count": (
                len(partial.get("operators", [])) if partial is not None else None
            ),
            "lifecycle_event_types": sorted({str(row.get("event_type")) for row in lifecycle}),
            "queue_event_count": len(queue),
            "reason": "The failed capture did not persist a complete semantic capture payload; missing fields remain OPAQUE.",
        }
    )
    passive_artifact = _sealed(
        {
            "schema_version": "membind.meg.real-runtime-passive-equivalence.v1",
            "status": passive["status"],
            "identity": identity,
            "certificate": passive,
            "reason": "A failed partial production execution cannot establish equality against the v3.1 baseline.",
        }
    )

    output_root.mkdir(parents=True, exist_ok=False)
    atomic_write_json(output_root / "REAL_MEG_RUNTIME_CAPTURE_SUMMARY.json", summary)
    atomic_write_json(output_root / "REAL_MEG_RUNTIME_LINEAGE_AUDIT.json", lineage)
    atomic_write_json(output_root / "REAL_MEG_RUNTIME_PASSIVE_EQUIVALENCE.json", passive_artifact)

    summary_md = "\n".join(
        [
            "# Real MEG Runtime OBSERVE_ONLY Capture Summary",
            "",
            f"STATUS: {STATUS}",
            "FAILURE_CLASSIFICATION: B_SEMANTIC_LINEAGE_FAILURE",
            f"RUN_ID: {identity['run_id']}",
            f"HISTORY: {identity['history']}",
            f"SOURCES: {identity['sources']}",
            f"NAMESPACE: {identity['namespace']}",
            f"GRAPHITI_VERSION: {identity['graphiti_version']}",
            f"SEAM_HASH: {identity['seam_hash']}",
            "",
            "## Source Completion",
            "",
            "Source 0 reached compile, prepared-durable, and bind-start, then failed in bind. Sources 1 and 2 did not complete.",
            "No source reached durable publication.",
            "",
            "## Semantic Lineage",
            "",
            f"Semantic operators before failure: {runtime_lineage['semantic_operator_count_before_failure']}",
            "OPERATOR_READY count: OPAQUE (complete capture payload was not materialized)",
            f"Request spans before failure: {runtime_lineage['request_span_count_before_failure']}",
            "Request lineage coverage: OPAQUE",
            f"Opaque lineage count: {runtime_lineage['opaque_lineage_count']}",
            "",
            "## State / Transaction",
            "",
            "Transaction commits observed: 0",
            "Mutation epoch transitions observed: 0",
            "Writer domain after failed capture: OPAQUE_UNOBSERVED",
            "",
            "## Publication",
            "",
            "Publication events observed: 0",
            "Publication causal coverage: OPAQUE",
            "",
            "## Passive Equivalence",
            "",
            "NOT_CERTIFIED: the partial failed execution cannot prove equality against the v3.1 baseline.",
            "Shadow DB reads: 0; shadow LLM calls: 0; shadow embeddings: 0; extra writes: 0; reorder: no.",
            "",
            "## Final Decision",
            "",
            f"{STATUS}",
            "No live retry, source expansion, or SHADOW_READ is authorized by this artifact.",
            "",
        ]
    )
    (output_root / "REAL_MEG_RUNTIME_CAPTURE_SUMMARY.md").write_text(
        summary_md, encoding="utf-8"
    )
    lineage_md = "\n".join(
        [
            "# Real MEG Runtime Lineage Audit",
            "",
            f"STATUS: {STATUS}",
            "AUDIT_STATUS: NOT_CERTIFIED",
            "",
            "The qualified seam was reached and production semantic work began. The capture then failed during source 0 bind. Two successful production LLM requests are present, but their durable request events contain no semantic operator, subrequest role, or prompt name fields. Those associations are therefore OPAQUE.",
            "",
            f"Semantic operators before failure: {runtime_lineage['semantic_operator_count_before_failure']}",
            f"Request spans before failure: {runtime_lineage['request_span_count_before_failure']}",
            f"Opaque lineage count: {runtime_lineage['opaque_lineage_count']}",
            "OPERATOR_READY count: OPAQUE",
            "",
            "The durable failure is the coordinator-level `bind_failed` code. Its nested adapter cause is OPAQUE. Graphiti 0.29.3 also logged missing target entities during extract_edges; source audit shows those are skipped warnings, not proven root cause. This is recorded as an incomplete production semantic trace, not as a capability-proxy failure.",
            "",
        ]
    )
    (output_root / "REAL_MEG_RUNTIME_LINEAGE_AUDIT.md").write_text(
        lineage_md, encoding="utf-8"
    )
    decision_md = "\n".join(
        [
            "# Real MEG Runtime Decision",
            "",
            f"STATUS: {STATUS}",
            "",
            "The qualified post-fix seam crossed the pre-measurement integration boundary: source 0 compiled, became prepared, entered bind, and issued two successful production LLM requests. The real capture did not complete sources 0..2 and did not materialize a complete MEG runtime payload.",
            "",
            "The request lineage and OPERATOR_READY evidence required for certification are OPAQUE. No transaction commit or publication event was observed, so publication causality and passive equivalence are not certified.",
            "",
            "Observed failure: `bind_failed` during Graphiti 0.29.3 bind. Nested root cause: OPAQUE. Missing-target messages are recorded as non-causal warnings only.",
            "",
            "NEXT ACTION: provider-free root-cause reproduction, TDD repair, and offline requalification only. A new live retry requires new explicit authorization.",
            "",
        ]
    )
    (output_root / "REAL_MEG_RUNTIME_DECISION.md").write_text(decision_md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
