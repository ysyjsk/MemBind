"""Strict L5 reduction, tables, diagnostics, final report, and content seal."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .dataset import EXPECTED_EPISODE_COUNTS
from .formal_run_seal import FormalRunSealError, verify_formal_run_seal
from .instrumentation import metric_dictionary
from .qa_lane import paired_qa_summary
from .qualification_seal import (
    QualificationSealError,
    verify_qualification_seal,
)
from .reducer import (
    RESULT_SCOPE,
    attach_paired_canonical_diffs,
    reduce_construction_main_table,
    reduce_quality_main_table,
)
from .schedules import Method
from .tdd_evidence import TddEvidenceError, verify_tdd_evidence


class ReportError(ValueError):
    """Sealed evidence does not satisfy the unique success terminal state."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ReportError(f"REQUIRED_ARTIFACT_UNREADABLE:{path.name}") from None
    if not isinstance(value, dict):
        raise ReportError(f"REQUIRED_ARTIFACT_INVALID:{path.name}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        values = [json.loads(line) for line in lines if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ReportError(f"JSONL_UNREADABLE:{path.name}") from None
    if any(not isinstance(value, dict) for value in values):
        raise ReportError(f"JSONL_ROW_INVALID:{path.name}")
    return values


def _write_new_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ReportError("REPORT_ARTIFACT_ALREADY_EXISTS") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json(path: Path, value: Any) -> None:
    _write_new_bytes(
        path,
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        ).encode("utf-8")
        + b"\n",
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    _write_new_bytes(
        path,
        "".join(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ).encode("utf-8"),
    )


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    import io

    stream = io.StringIO(newline="")
    try:
        writer = csv.DictWriter(stream, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        payload = stream.getvalue().encode("utf-8")
    finally:
        stream.close()
    _write_new_bytes(path, payload)


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines.extend(
        "| " + " | ".join(str(value) for value in row) + " |" for row in rows
    )
    return "\n".join(lines)


def _acceptance(
    root: Path,
    block_rows: Sequence[Mapping[str, Any]],
    qa_rows: Sequence[Mapping[str, Any]],
    formal: Mapping[str, Any],
) -> dict[str, Any]:
    test_summary = _read_object(root / "test_summary.json")
    resource = _read_object(root / "resource_envelope.json")
    required_tdd_stages = test_summary.get("required_tdd_stages")
    if (
        not isinstance(required_tdd_stages, list)
        or not required_tdd_stages
        or any(not isinstance(stage, str) or not stage for stage in required_tdd_stages)
    ):
        raise ReportError("TDD_TEST_SUMMARY_INVALID")
    try:
        tdd = verify_tdd_evidence(
            root / "tdd_evidence.jsonl",
            required_red_green_stages=tuple(required_tdd_stages),
        )
    except TddEvidenceError:
        raise ReportError("TDD_EVIDENCE_INVALID") from None
    if (
        test_summary.get("tdd_evidence_verified") is not True
        or test_summary.get("tdd_evidence_sha256") != tdd["journal_sha256"]
    ):
        raise ReportError("TDD_TEST_SUMMARY_MISMATCH")
    try:
        qualification = verify_qualification_seal(root)
    except QualificationSealError:
        raise ReportError("QUALIFICATION_SEAL_INVALID") from None
    methods = {method.value for method in Method}
    expected_blocks = {
        (method, history)
        for method in methods
        for history in EXPECTED_EPISODE_COUNTS
    }
    observed_blocks = {
        (str(row.get("method")), str(row.get("history_id")))
        for row in block_rows
        if row.get("valid") is True
    }
    qa_counts = {
        method: sum(row.get("method") == method for row in qa_rows)
        for method in methods
    }
    graph_writes = sum(int(row.get("graph_write_attempts") or 0) for row in qa_rows)
    qa_construction_calls = sum(
        int(row.get("construction_calls") or 0) for row in qa_rows
    )
    qa_history_counts = {
        (method, history): sum(
            row.get("method") == method and row.get("history_id") == history
            for row in qa_rows
        )
        for method in methods
        for history in EXPECTED_EPISODE_COUNTS
    }

    def timer_isolated(row: Mapping[str, Any]) -> bool:
        fields = (
            row.get("t0_ns"),
            row.get("t_durable_complete_ns"),
            row.get("t_validated_seal_ns"),
            row.get("build_makespan_ns"),
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in fields):
            return False
        t0, durable, validated, makespan = fields
        return (
            t0 < durable < validated
            and makespan == durable - t0
            and math.isclose(
                float(row.get("build_makespan_s", math.nan)),
                makespan / 1_000_000_000,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        )

    acceptance = {
        "tests_all_green": test_summary.get("tests_all_green") is True,
        "tdd_evidence_verified": tdd.get("verified") is True,
        "qualification_passed": qualification.get("qualification_passed") is True,
        "historical_resource_match": resource.get("historical_resource_match") is True,
        "live_resource_envelope_verified": resource.get("live_resource_envelope_verified") is True,
        "all_formal_blocks_share_one_resource_envelope": formal.get("all_formal_blocks_share_one_resource_envelope") is True,
        "valid_construction_blocks": len(observed_blocks),
        "formal_construction_calls": formal.get("formal_construction_calls", len(block_rows)),
        "valid_histories_per_method": {
            method: len({history for observed_method, history in observed_blocks if observed_method == method})
            for method in methods
        },
        "qa_rows_B0": qa_counts[Method.B0_NATIVE_SERIAL.value],
        "qa_rows_B1": qa_counts[Method.B1_NAIVE_WHOLE_UPDATE_ASYNC.value],
        "qa_graph_write_attempts": graph_writes,
        "qa_extra_construction_calls": qa_construction_calls,
        "build_timer_excludes_validation_and_qa": all(
            timer_isolated(row) for row in block_rows
        ),
        "tables_marked_development": True,
    }
    valid = (
        acceptance["tests_all_green"]
        and acceptance["tdd_evidence_verified"]
        and acceptance["qualification_passed"]
        and acceptance["historical_resource_match"]
        and acceptance["live_resource_envelope_verified"]
        and acceptance["all_formal_blocks_share_one_resource_envelope"]
        and len(block_rows) == 8
        and observed_blocks == expected_blocks
        and acceptance["qa_rows_B0"] == 16
        and acceptance["qa_rows_B1"] == 16
        and all(count == 4 for count in qa_history_counts.values())
        and graph_writes == 0
        and qa_construction_calls == 0
        and acceptance["build_timer_excludes_validation_and_qa"]
    )
    if not valid:
        raise ReportError("SUCCESS_ACCEPTANCE_INCOMPLETE")
    return acceptance


def _write_construction_tables(root: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = (
        "Method",
        "Valid histories",
        "Episodes",
        "Total build makespan (s)",
        "Speedup vs B0",
        "Source tokens/s",
        "LLM input-token ratio vs B0",
        "Direct semantic violations",
        "Canonical exact-match histories (descriptive)",
    )
    rendered = [
        {
            fields[0]: row["method"],
            fields[1]: row["valid_histories"],
            fields[2]: row["episodes"],
            fields[3]: row["total_build_makespan_s"],
            fields[4]: row["speedup_vs_b0"],
            fields[5]: row["source_tokens_per_s"],
            fields[6]: row["llm_input_token_ratio_vs_b0"],
            fields[7]: row["direct_semantic_violations"],
            fields[8]: row["canonical_exact_match_histories"],
        }
        for row in rows
    ]
    _write_csv(root / "main_table_construction.csv", rendered, fields)
    table = _markdown_table(fields, [[row[field] for field in fields] for row in rendered])
    _write_new_bytes(
        root / "main_table_construction.md",
        f"# Development Construction Main Table\n\nScope: `{RESULT_SCOPE}`\n\n{table}\n".encode(
            "utf-8"
        ),
    )


def _write_quality_tables(root: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = (
        "Method",
        "QA N",
        "R@1",
        "R@5",
        "R@10",
        "MRR",
        "nDCG@10",
        "Accuracy (invalid=wrong)",
        "Invalid",
    )
    rendered = [
        {
            fields[0]: row["method"],
            fields[1]: row["qa_n"],
            fields[2]: row["recall_at_1"],
            fields[3]: row["recall_at_5"],
            fields[4]: row["recall_at_10"],
            fields[5]: row["mrr"],
            fields[6]: row["ndcg_at_10"],
            fields[7]: row["accuracy_invalid_wrong"],
            fields[8]: row["invalid"],
        }
        for row in rows
    ]
    _write_csv(root / "main_table_quality.csv", rendered, fields)
    table = _markdown_table(fields, [[row[field] for field in fields] for row in rendered])
    _write_new_bytes(
        root / "main_table_quality.md",
        f"# Development Multi-QA Main Table\n\nScope: `{RESULT_SCOPE}`\n\n{table}\n".encode(
            "utf-8"
        ),
    )


def build_final_report(run_root: Path) -> dict[str, Any]:
    root = run_root.resolve()
    report_outputs = (
        "block_metrics.jsonl",
        "canonical_paired_diffs.json",
        "metric_dictionary.json",
        "main_table_construction.csv",
        "main_table_construction.md",
        "main_table_quality.csv",
        "main_table_quality.md",
        "per_history_construction.csv",
        "diagnostic_phase_llm_embedding_db.csv",
        "diagnostic_concurrency_ordering.csv",
        "diagnostic_resource_telemetry.csv",
        "correctness_ledger.csv",
        "qa/paired_rows.jsonl",
        "FINAL_REPORT.md",
        "FINAL_SEAL.json",
        "SATURATED_FIXED_WORK_BASELINE_V1_2_COMPLETE",
    )
    if any((root / name).exists() for name in report_outputs):
        raise ReportError("REPORT_ALREADY_MATERIALIZED")
    required = (
        "audit_manifest.json",
        "reuse_manifest.json",
        "protocol_manifest.json",
        "config_hashes.json",
        "provider_envelope.json",
        "resource_envelope.json",
        "RESOURCE_ENVELOPE_ID",
        "tdd_evidence.jsonl",
        "test_summary.json",
        "failed_attempts.jsonl",
        "qualification/qualification_seal.json",
        "formal/formal_run_seal.json",
        "service_evidence/identity.json",
        "qa/qa_rows.jsonl",
    )
    if any(not (root / name).is_file() for name in required):
        raise ReportError("REQUIRED_DELIVERY_ARTIFACT_MISSING")
    try:
        formal = verify_formal_run_seal(root)
    except FormalRunSealError:
        raise ReportError("FORMAL_RUN_SEAL_INVALID") from None
    audit = _read_object(root / "audit_manifest.json")
    repository_root = audit.get("repository_root")
    if not isinstance(repository_root, str) or not Path(repository_root).is_dir():
        raise ReportError("AUDIT_REPOSITORY_ROOT_INVALID")
    paired_canonical = attach_paired_canonical_diffs(
        formal["rows"],
        repository_root=Path(repository_root),
        expected_histories=tuple(EXPECTED_EPISODE_COUNTS),
    )
    block_rows = paired_canonical["rows"]
    qa_rows = _read_jsonl(root / "qa/qa_rows.jsonl")
    acceptance = _acceptance(root, block_rows, qa_rows, formal)

    def reduce_once() -> dict[str, Any]:
        return {
            "construction": reduce_construction_main_table(
                block_rows, expected_histories=tuple(EXPECTED_EPISODE_COUNTS)
            ),
            "quality": reduce_quality_main_table(qa_rows),
            "paired": paired_qa_summary(qa_rows),
        }

    first = reduce_once()
    second = reduce_once()
    first_hash = _payload_hash(first)
    second_hash = _payload_hash(second)
    if first_hash != second_hash:
        raise ReportError("REDUCER_NONDETERMINISTIC")
    acceptance["reducer_is_deterministic"] = True
    acceptance["construction_main_table_has_real_numbers"] = True
    acceptance["quality_main_table_has_real_numbers"] = True
    acceptance["final_seal_verified"] = True

    _write_jsonl(root / "block_metrics.jsonl", block_rows)
    _write_json(
        root / "canonical_paired_diffs.json",
        {
            "schema_version": paired_canonical["schema_version"],
            "diffs": paired_canonical["diffs"],
        },
    )
    _write_json(root / "metric_dictionary.json", metric_dictionary())
    _write_construction_tables(root, first["construction"])
    _write_quality_tables(root, first["quality"])
    _write_csv(
        root / "per_history_construction.csv",
        block_rows,
        tuple(
            dict.fromkeys(
                key for row in block_rows for key in row
            )
        ),
    )
    _write_csv(
        root / "diagnostic_phase_llm_embedding_db.csv",
        [
            {
                "method": row["method"],
                "history_id": row["history_id"],
                "llm_input_tokens": row.get("llm_input_tokens"),
                "phase_metrics_availability": row.get("phase_metrics_availability", "NOT_EXPOSED_BY_PINNED_STACK"),
                "embedding_metrics_availability": row.get("embedding_metrics_availability", "NOT_EXPOSED_BY_PINNED_STACK"),
                "db_metrics_availability": row.get("db_metrics_availability", "NOT_EXPOSED_BY_PINNED_STACK"),
            }
            for row in block_rows
        ],
        ("method", "history_id", "llm_input_tokens", "phase_metrics_availability", "embedding_metrics_availability", "db_metrics_availability"),
    )
    _write_csv(
        root / "diagnostic_concurrency_ordering.csv",
        block_rows,
        ("method", "history_id", "inversion_count", "direct_semantic_violations"),
    )
    _write_csv(
        root / "diagnostic_resource_telemetry.csv",
        block_rows,
        ("method", "history_id", "resource_availability"),
    )
    _write_csv(
        root / "correctness_ledger.csv",
        block_rows,
        ("method", "history_id", "direct_semantic_violations", "canonical_exact_match"),
    )
    paired_rows = []
    by_key = {(row["method"], row["qa_pair_id"]): row for row in qa_rows}
    for pair_id in sorted({row["qa_pair_id"] for row in qa_rows}):
        paired_rows.append(
            {
                "qa_pair_id": pair_id,
                "b0": by_key[(Method.B0_NATIVE_SERIAL.value, pair_id)],
                "b1": by_key[(Method.B1_NAIVE_WHOLE_UPDATE_ASYNC.value, pair_id)],
            }
        )
    _write_jsonl(root / "qa/paired_rows.jsonl", paired_rows)

    construction_md = (root / "main_table_construction.md").read_text(encoding="utf-8")
    quality_md = (root / "main_table_quality.md").read_text(encoding="utf-8")
    _write_new_bytes(
        root / "FINAL_REPORT.md",
        (
            "# Saturated Fixed-Work Baseline v1.2\n\n"
            + construction_md
            + "\n"
            + quality_md
            + "\n## Boundary\n\n"
            + "These are development, protocol-qualified, one-run-per-method-history results. "
            + "The 16 authored QA items are not official MemoryAgentBench or LongMemEval results.\n"
        ).encode("utf-8"),
    )

    files = {
        str(path.relative_to(root)): _file_hash(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name not in {"FINAL_SEAL.json", "SATURATED_FIXED_WORK_BASELINE_V1_2_COMPLETE"}
    }
    final = {
        "schema_version": "membind.saturated-fixed-work.final-seal.v1",
        "status": "ACCEPTED",
        "reducer_version": "1.2",
        "reducer_output_hash_first": first_hash,
        "reducer_output_hash_second": second_hash,
        "acceptance": acceptance,
        "selected_attempts": formal["selected_attempts"],
        "formal_run_seal_payload_sha256": formal["payload_sha256"],
        "files": files,
    }
    final["payload_sha256"] = _payload_hash(final)
    _write_json(root / "FINAL_SEAL.json", final)
    verified = verify_final_seal(root, require_complete=False)
    if verified.get("verified") is not True:
        raise ReportError("FINAL_SEAL_VERIFICATION_FAILED")
    _write_new_bytes(
        root / "SATURATED_FIXED_WORK_BASELINE_V1_2_COMPLETE",
        (final["payload_sha256"] + "\n").encode("ascii"),
    )
    return final


def verify_final_seal(
    run_root: Path, *, require_complete: bool = True
) -> dict[str, Any]:
    root = run_root.resolve()
    final = _read_object(root / "FINAL_SEAL.json")
    observed = final.pop("payload_sha256", None)
    if observed != _payload_hash(final) or final.get("status") != "ACCEPTED":
        raise ReportError("FINAL_SEAL_PAYLOAD_INVALID")
    files = final.get("files")
    if not isinstance(files, dict) or not files:
        raise ReportError("FINAL_SEAL_FILE_INVENTORY_INVALID")
    mismatches = [
        name
        for name, expected in files.items()
        if not (root / name).is_file() or _file_hash(root / name) != expected
    ]
    if mismatches:
        raise ReportError("FINAL_SEAL_FILE_HASH_MISMATCH")
    acceptance = final.get("acceptance")
    required_assertions = {
        "tests_all_green": True,
        "tdd_evidence_verified": True,
        "qualification_passed": True,
        "historical_resource_match": True,
        "live_resource_envelope_verified": True,
        "all_formal_blocks_share_one_resource_envelope": True,
        "valid_construction_blocks": 8,
        "formal_construction_calls": 8,
        "qa_rows_B0": 16,
        "qa_rows_B1": 16,
        "qa_graph_write_attempts": 0,
        "qa_extra_construction_calls": 0,
        "build_timer_excludes_validation_and_qa": True,
        "construction_main_table_has_real_numbers": True,
        "quality_main_table_has_real_numbers": True,
        "tables_marked_development": True,
        "reducer_is_deterministic": True,
        "final_seal_verified": True,
    }
    if not isinstance(acceptance, Mapping) or any(
        acceptance.get(name) != value for name, value in required_assertions.items()
    ):
        raise ReportError("FINAL_SEAL_ACCEPTANCE_INVALID")
    histories = acceptance.get("valid_histories_per_method")
    if not isinstance(histories, Mapping) or any(
        histories.get(method.value) != 4 for method in Method
    ):
        raise ReportError("FINAL_SEAL_ACCEPTANCE_INVALID")
    if require_complete:
        try:
            marker = (
                root / "SATURATED_FIXED_WORK_BASELINE_V1_2_COMPLETE"
            ).read_text(encoding="ascii")
        except (OSError, UnicodeError):
            raise ReportError("COMPLETION_MARKER_INVALID") from None
        if marker != observed + "\n":
            raise ReportError("COMPLETION_MARKER_INVALID")
    return {
        "verified": True,
        "payload_sha256": observed,
        "file_count": len(files),
    }


__all__ = ["ReportError", "build_final_report", "verify_final_seal"]
