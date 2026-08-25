"""Read-only QA overlay that runs only after a construction seal."""

from __future__ import annotations

import json
import inspect
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any


class QALaneError(ValueError):
    """QA cannot safely attach to the sealed construction artifact."""


_FAILURE_CLASSES = {
    "DATASET_MAPPING_INVALID",
    "CONSTRUCTION_FAILED",
    "NAMESPACE_NOT_SEALED",
    "RETRIEVAL_FAILED",
    "CONTEXT_PACK_INVALID",
    "READER_FAILED",
    "READER_INVALID_FINISH",
    "JUDGE_FAILED",
    "JUDGE_INVALID",
    "QA_PHASE_WRITE_VIOLATION",
    "UNKNOWN_INFRA_FAILURE",
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise QALaneError(f"invalid QA JSONL at line {line_number}") from exc
        if not isinstance(value, dict):
            raise QALaneError("QA result row is not an object")
        rows.append(value)
    return rows


def _append(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def _identity(row: Mapping[str, Any]) -> tuple[str, str, str]:
    values = (row.get("context_id"), row.get("qa_pair_id"), row.get("qa_identity_sha256"))
    if any(not isinstance(value, str) or not value for value in values):
        raise QALaneError("QA identity is invalid")
    return values  # type: ignore[return-value]


def run_mab_qa_on_sealed_namespace(
    *,
    construction_seal: Mapping[str, Any],
    qa_manifest: Sequence[Mapping[str, Any]],
    output_root: str | Path,
    state_reader: Callable[[], Any],
    answer_fn: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    """Run/resume QA while asserting the construction namespace stays read-only.

    ``answer_fn`` is the provider/evaluator boundary.  It receives one frozen
    QA manifest row and must not receive or mutate construction inputs.
    """

    if construction_seal.get("status") != "CONSTRUCTION_SEALED":
        raise QALaneError("QA requires a construction namespace sealed first")
    for field in ("context_id", "method", "namespace", "workload_hash"):
        if not isinstance(construction_seal.get(field), str) or not construction_seal[field]:
            raise QALaneError(f"construction seal field is missing: {field}")
    if isinstance(qa_manifest, (str, bytes)) or not isinstance(qa_manifest, Sequence) or not qa_manifest:
        raise QALaneError("QA manifest is empty")
    manifest_rows = [dict(row) for row in qa_manifest]
    manifest_keys = {_identity(row) for row in manifest_rows}
    if len(manifest_keys) != len(manifest_rows):
        raise QALaneError("QA manifest contains duplicate identity")
    if any(row.get("context_id") != construction_seal.get("context_id") for row in manifest_rows):
        raise QALaneError("QA context does not match construction seal")
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    result_path = root / "qa_results.jsonl"
    existing = _read_jsonl(result_path)
    existing_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in existing:
        key = _identity(row)
        if key in existing_by_key:
            raise QALaneError("QA result contains duplicate identity")
        existing_by_key[key] = row
    before = state_reader()
    for row in manifest_rows:
        key = _identity(row)
        if key in existing_by_key:
            if existing_by_key[key].get("qa_identity_sha256") != row.get("qa_identity_sha256"):
                raise QALaneError("resume QA identity mismatch")
            continue
        try:
            outcome = dict(answer_fn(dict(row)))
        except Exception as exc:  # provider/evaluator errors become null QA
            outcome = {"judge_valid": False, "correct": None, "failure_class": type(exc).__name__}
        judge_valid = outcome.get("judge_valid") is True
        failure = outcome.get("failure_class")
        if not judge_valid:
            if not isinstance(failure, str) or failure not in _FAILURE_CLASSES:
                failure = "UNKNOWN_INFRA_FAILURE"
            correct = None
            status = "INVALID"
        else:
            if not isinstance(outcome.get("correct"), bool):
                raise QALaneError("valid QA outcome must contain boolean correctness")
            correct = bool(outcome["correct"])
            failure = None
            status = "COMPLETE"
        artifact = {
            **row,
            "status": status,
            "judge_valid": judge_valid,
            "correct": correct,
            "failure_class": failure,
        }
        _append(result_path, artifact)
        existing_by_key[key] = artifact
    after = state_reader()
    state_unchanged = before == after
    all_rows = list(existing_by_key.values())
    invalid_count = sum(row.get("judge_valid") is not True for row in all_rows)
    quality_status = "PASS" if state_unchanged and not invalid_count else ("INVALID" if not state_unchanged else "PASS_WITH_INVALID_ROWS")
    if not state_unchanged:
        quality_status = "INVALID"
    summary = {
        "schema_version": "membind.v1.3.qa-summary.v1",
        "quality_status": quality_status,
        "context_id": construction_seal["context_id"],
        "method": construction_seal["method"],
        "namespace": construction_seal["namespace"],
        "workload_hash": construction_seal["workload_hash"],
        "expected_count": len(manifest_rows),
        "completed_count": len(all_rows),
        "invalid_count": invalid_count,
        "invalid_reason": "QA_PHASE_WRITE_VIOLATION" if not state_unchanged else None,
        "graph_state_before": before,
        "graph_state_after": after,
        "question_type_counts": dict(Counter(str(row.get("question_type", "unknown")) for row in all_rows)),
    }
    (root / "quality_summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    qa_seal = {"status": "QA_SEALED" if state_unchanged else "QA_INVALID", "parent_construction_seal": dict(construction_seal), "summary": summary}
    (root / "qa_seal.json").write_text(json.dumps(qa_seal, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return summary


__all__ = ["QALaneError", "run_mab_qa_on_sealed_namespace"]


async def run_mab_qa_on_sealed_namespace_async(
    *,
    construction_seal: Mapping[str, Any],
    qa_manifest: Sequence[Mapping[str, Any]],
    output_root: str | Path,
    state_reader: Callable[[], Any],
    answer_fn: Callable[[Mapping[str, Any]], Any],
) -> dict[str, Any]:
    """Async counterpart used by the real Graphiti/HTTP QA boundary."""

    if construction_seal.get("status") != "CONSTRUCTION_SEALED":
        raise QALaneError("QA requires a construction namespace sealed first")
    for field in ("context_id", "method", "namespace", "workload_hash"):
        if not isinstance(construction_seal.get(field), str) or not construction_seal[field]:
            raise QALaneError(f"construction seal field is missing: {field}")
    if isinstance(qa_manifest, (str, bytes)) or not isinstance(qa_manifest, Sequence) or not qa_manifest:
        raise QALaneError("QA manifest is empty")
    manifest_rows = [dict(row) for row in qa_manifest]
    manifest_keys = {_identity(row) for row in manifest_rows}
    if len(manifest_keys) != len(manifest_rows):
        raise QALaneError("QA manifest contains duplicate identity")
    if any(row.get("context_id") != construction_seal.get("context_id") for row in manifest_rows):
        raise QALaneError("QA context does not match construction seal")
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    result_path = root / "qa_results.jsonl"
    existing = _read_jsonl(result_path)
    existing_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in existing:
        key = _identity(row)
        if key in existing_by_key:
            raise QALaneError("QA result contains duplicate identity")
        existing_by_key[key] = row
    before = state_reader()
    if inspect.isawaitable(before):
        before = await before
    for row in manifest_rows:
        key = _identity(row)
        if key in existing_by_key:
            continue
        try:
            outcome = answer_fn(dict(row))
            if inspect.isawaitable(outcome):
                outcome = await outcome
            outcome = dict(outcome)
        except Exception as exc:  # provider/evaluator errors become null QA
            outcome = {"judge_valid": False, "correct": None, "failure_class": type(exc).__name__}
        judge_valid = outcome.get("judge_valid") is True
        failure = outcome.get("failure_class")
        if not judge_valid:
            if not isinstance(failure, str) or failure not in _FAILURE_CLASSES:
                failure = "UNKNOWN_INFRA_FAILURE"
            correct = None
            status = "INVALID"
        else:
            if not isinstance(outcome.get("correct"), bool):
                raise QALaneError("valid QA outcome must contain boolean correctness")
            correct = bool(outcome["correct"])
            failure = None
            status = "COMPLETE"
        artifact = {**row, **{key: value for key, value in outcome.items() if key not in {"judge_valid", "correct", "failure_class"}}, "status": status, "judge_valid": judge_valid, "correct": correct, "failure_class": failure}
        _append(result_path, artifact)
        existing_by_key[key] = artifact
    after = state_reader()
    if inspect.isawaitable(after):
        after = await after
    state_unchanged = before == after
    all_rows = list(existing_by_key.values())
    invalid_count = sum(row.get("judge_valid") is not True for row in all_rows)
    quality_status = "PASS" if state_unchanged and not invalid_count else ("INVALID" if not state_unchanged else "PASS_WITH_INVALID_ROWS")
    if not state_unchanged:
        quality_status = "INVALID"
    summary = {
        "schema_version": "membind.v1.3.qa-summary.v1",
        "quality_status": quality_status,
        "context_id": construction_seal["context_id"],
        "method": construction_seal["method"],
        "namespace": construction_seal["namespace"],
        "workload_hash": construction_seal["workload_hash"],
        "expected_count": len(manifest_rows),
        "completed_count": len(all_rows),
        "invalid_count": invalid_count,
        "invalid_reason": "QA_PHASE_WRITE_VIOLATION" if not state_unchanged else None,
        "graph_state_before": before,
        "graph_state_after": after,
        "question_type_counts": dict(Counter(str(row.get("question_type", "unknown")) for row in all_rows)),
    }
    (root / "quality_summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (root / "qa_seal.json").write_text(json.dumps({"status": "QA_SEALED" if state_unchanged else "QA_INVALID", "parent_construction_seal": dict(construction_seal), "summary": summary}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return summary


__all__ = ["QALaneError", "run_mab_qa_on_sealed_namespace", "run_mab_qa_on_sealed_namespace_async"]
