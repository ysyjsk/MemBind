"""Contracts and reducers for authored QA on the four frozen baseline states.

This module deliberately does not construct memory or call a model.  It binds
the extension inventory to the byte-hashed baseline input, exposes a
gold-blind projection for retrieval/Reader, and reduces sealed live rows.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CLAIM_SCOPE = "BASELINE_REUSE_4_HISTORY_AUTHORED_EXTENSION"
SCHEMA_VERSION = "membind.baseline-reuse-expanded-qa.v1"
METHODS = ("U0", "P(C=2)")
METRICS = ("recall_at_1", "recall_at_3", "recall_at_5", "recall_at_10", "mrr", "ndcg_at_10")
EXPECTED_HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
EXPECTED_SOURCE_SHA256 = "a1e3088193eaf6b866fceb62343ebe09beddc8ad0ed57bc70176232f16b3454b"


class ExpandedAnalysisError(ValueError):
    """The extension inventory or sealed row contract is invalid."""


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _nonempty(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExpandedAnalysisError(f"{field}_INVALID")
    return value.strip()


def _history_sessions(source: Mapping[str, Any]) -> dict[str, list[str]]:
    records = source.get("records")
    if not isinstance(records, list):
        raise ExpandedAnalysisError("SOURCE_RECORDS_INVALID")
    result: dict[str, list[str]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise ExpandedAnalysisError("SOURCE_RECORD_INVALID")
        history = _nonempty(record.get("question_id"), "SOURCE_HISTORY_ID")
        session_ids = record.get("haystack_session_ids")
        if (
            not isinstance(session_ids, list)
            or not session_ids
            or any(not isinstance(value, str) or not value for value in session_ids)
            or len(set(session_ids)) != len(session_ids)
        ):
            raise ExpandedAnalysisError("SOURCE_SESSION_INVENTORY_INVALID")
        result[history] = list(session_ids)
    if tuple(result) != EXPECTED_HISTORIES:
        raise ExpandedAnalysisError("SOURCE_HISTORY_INVENTORY_DRIFT")
    return result


def _datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y/%m/%d (%a) %H:%M")
        except ValueError:
            raise ExpandedAnalysisError("TIMESTAMP_INVALID") from None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _history_details(source: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records = source.get("records")
    if not isinstance(records, list):
        raise ExpandedAnalysisError("SOURCE_RECORDS_INVALID")
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise ExpandedAnalysisError("SOURCE_RECORD_INVALID")
        history = _nonempty(record.get("question_id"), "SOURCE_HISTORY_ID")
        session_ids = record.get("haystack_session_ids")
        dates = record.get("haystack_dates")
        sessions = record.get("haystack_sessions")
        if (
            not isinstance(session_ids, list)
            or not isinstance(dates, list)
            or not isinstance(sessions, list)
            or len(session_ids) != len(dates)
            or len(session_ids) != len(sessions)
            or not session_ids
        ):
            raise ExpandedAnalysisError("SOURCE_SESSION_DETAILS_INVALID")
        by_id: dict[str, dict[str, Any]] = {}
        for session_id, date, turns in zip(session_ids, dates, sessions, strict=True):
            if (
                not isinstance(session_id, str)
                or not session_id
                or session_id in by_id
                or not isinstance(date, str)
                or not date
                or not isinstance(turns, list)
                or not turns
            ):
                raise ExpandedAnalysisError("SOURCE_SESSION_DETAILS_INVALID")
            contents: list[str] = []
            for turn in turns:
                if not isinstance(turn, Mapping) or not isinstance(turn.get("content"), str):
                    raise ExpandedAnalysisError("SOURCE_SESSION_TURN_INVALID")
                contents.append(str(turn["content"]))
            by_id[session_id] = {"date": date, "contents": contents}
        result[history] = by_id
    if tuple(result) != EXPECTED_HISTORIES:
        raise ExpandedAnalysisError("SOURCE_HISTORY_INVENTORY_DRIFT")
    return result


def _validate_question(row: Mapping[str, Any], allowed: set[str]) -> dict[str, Any]:
    required = {
        "question_id", "qa_pair_id", "history_id", "question_type",
        "question_date", "question", "reference_answer", "gold_session_ids",
        "gold_evidence_quotes",
    }
    if set(row) != required:
        raise ExpandedAnalysisError("QUESTION_FIELD_INVENTORY_INVALID")
    selected = dict(row)
    for field in ("question_id", "qa_pair_id", "history_id", "question_type", "question_date", "question", "reference_answer"):
        _nonempty(selected.get(field), field.upper())
    if selected["question_id"] != selected["qa_pair_id"]:
        raise ExpandedAnalysisError("QUESTION_IDENTITY_INVALID")
    gold = selected.get("gold_session_ids")
    if (
        not isinstance(gold, list)
        or not gold
        or any(not isinstance(value, str) or not value for value in gold)
        or len(set(gold)) != len(gold)
    ):
        raise ExpandedAnalysisError("GOLD_SESSION_INVENTORY_INVALID")
    if not set(gold).issubset(allowed):
        raise ExpandedAnalysisError("GOLD_SESSION_NOT_IN_HISTORY")
    quotes = selected.get("gold_evidence_quotes")
    if (
        not isinstance(quotes, list)
        or not quotes
        or any(not isinstance(value, str) or not value for value in quotes)
        or len(set(quotes)) != len(quotes)
    ):
        raise ExpandedAnalysisError("GOLD_QUOTE_INVENTORY_INVALID")
    return selected


def validate_gold_provenance(row: Mapping[str, Any], source: Mapping[str, Any]) -> None:
    """Validate exact quote/session provenance and the question-time boundary."""

    details = _history_details(source)
    history = _nonempty(row.get("history_id"), "HISTORY_ID")
    if history not in details:
        raise ExpandedAnalysisError("QUESTION_HISTORY_UNKNOWN")
    selected = _validate_question(row, set(details[history]))
    question_time = _datetime(selected["question_date"])
    sessions = details[history]
    for session_id in selected["gold_session_ids"]:
        session = sessions[session_id]
        if _datetime(session["date"]) > question_time:
            raise ExpandedAnalysisError("GOLD_SESSION_AFTER_QUESTION")
        haystack = "\n".join(session["contents"])
        for quote in selected["gold_evidence_quotes"]:
            if quote not in haystack:
                raise ExpandedAnalysisError("GOLD_QUOTE_NOT_IN_SESSION")


def load_expanded_inventory(path: Path, source_path: Path) -> dict[str, Any]:
    """Load the authored extension and bind it to the frozen source bytes."""

    try:
        inventory = json.loads(path.read_text(encoding="utf-8"))
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ExpandedAnalysisError("INVENTORY_SOURCE_UNREADABLE") from error
    if not isinstance(inventory, Mapping) or not isinstance(source, Mapping):
        raise ExpandedAnalysisError("INVENTORY_SOURCE_SCHEMA_INVALID")
    if inventory.get("schema_version") != SCHEMA_VERSION or inventory.get("claim_scope") != CLAIM_SCOPE:
        raise ExpandedAnalysisError("INVENTORY_IDENTITY_INVALID")
    observed_source_hash = file_sha256(source_path)
    if inventory.get("source_sha256") != EXPECTED_SOURCE_SHA256 or observed_source_hash != EXPECTED_SOURCE_SHA256:
        raise ExpandedAnalysisError("SOURCE_HASH_MISMATCH")
    if tuple(inventory.get("history_order", ())) != EXPECTED_HISTORIES:
        raise ExpandedAnalysisError("INVENTORY_HISTORY_ORDER_INVALID")
    sessions = _history_sessions(source)
    details = _history_details(source)
    raw_questions = inventory.get("questions")
    if not isinstance(raw_questions, list) or len(raw_questions) != 16:
        raise ExpandedAnalysisError("QUESTION_COUNT_INVALID")
    questions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_questions:
        if not isinstance(raw, Mapping):
            raise ExpandedAnalysisError("QUESTION_SCHEMA_INVALID")
        history = _nonempty(raw.get("history_id"), "HISTORY_ID")
        if history not in sessions:
            raise ExpandedAnalysisError("QUESTION_HISTORY_UNKNOWN")
        row = _validate_question(raw, set(sessions[history]))
        validate_gold_provenance(row, source)
        if row["question_id"] in seen:
            raise ExpandedAnalysisError("QUESTION_IDENTITY_DUPLICATE")
        seen.add(row["question_id"])
        questions.append(row)
    counts = {history: sum(row["history_id"] == history for row in questions) for history in EXPECTED_HISTORIES}
    if counts != {history: 4 for history in EXPECTED_HISTORIES}:
        raise ExpandedAnalysisError("QUESTION_BALANCE_INVALID")
    return {
        "schema_version": SCHEMA_VERSION,
        "claim_scope": CLAIM_SCOPE,
        "source_path": str(source_path.resolve()),
        "source_sha256": observed_source_hash,
        "history_order": list(EXPECTED_HISTORIES),
        "history_sessions": sessions,
        "questions": questions,
        "question_count": len(questions),
        "questions_per_history": counts,
        "inventory_sha256": canonical_sha256(questions),
    }


def build_gold_blind_projection(
    row: Mapping[str, Any], *, allowed_session_ids: set[str] | None = None
) -> dict[str, Any]:
    """Return the only question fields permitted to retrieval/Reader code."""

    if allowed_session_ids is not None:
        _validate_question(row, allowed_session_ids)
    else:
        for field in ("question_id", "qa_pair_id", "history_id", "question_type", "question_date", "question"):
            _nonempty(row.get(field), field.upper())
    return {
        "question_id": str(row["question_id"]),
        "qa_pair_id": str(row["qa_pair_id"]),
        "history_id": str(row["history_id"]),
        "question_type": str(row["question_type"]),
        "question_date": str(row["question_date"]),
        "question": str(row["question"]),
    }


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0 or not 0 <= successes <= total:
        raise ExpandedAnalysisError("WILSON_INPUT_INVALID")
    p = successes / total
    denominator = 1.0 + z * z / total
    centre = (p + z * z / (2.0 * total)) / denominator
    margin = z * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total)) / denominator
    return centre - margin, centre + margin


def _mean(rows: Sequence[Mapping[str, Any]], metric: str) -> float:
    values = []
    for row in rows:
        metrics = row.get("retrieval_metrics")
        if not isinstance(metrics, Mapping) or not isinstance(metrics.get(metric), (int, float)):
            raise ExpandedAnalysisError("RETRIEVAL_METRICS_INVALID")
        values.append(float(metrics[metric]))
    return sum(values) / len(values)


def reduce_expanded_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Reduce exactly paired U0/P(C=2) extension rows without dropping invalids."""

    if not isinstance(rows, Sequence) or not rows:
        raise ExpandedAnalysisError("ROWS_EMPTY")
    row_map: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or row.get("method") not in METHODS:
            raise ExpandedAnalysisError("ROW_SCHEMA_INVALID")
        key = (str(row.get("method")), str(row.get("question_id")))
        if not key[1] or key in row_map:
            raise ExpandedAnalysisError("ROW_IDENTITY_DUPLICATE")
        row_map[key] = row
    u0 = {key[1] for key in row_map if key[0] == "U0"}
    pc2 = {key[1] for key in row_map if key[0] == "P(C=2)"}
    if not u0 or u0 != pc2:
        raise ExpandedAnalysisError("PAIRED_QUESTION_INVENTORY_MISMATCH")
    results: dict[str, Any] = {"schema_version": "membind.baseline-reuse-expanded-analysis.v1", "claim_scope": CLAIM_SCOPE, "status": "PASS", "question_count": len(u0), "methods": {}, "paired": {}}
    for method in METHODS:
        selected = [row_map[(method, question)] for question in sorted(u0)]
        valid = [row for row in selected if row.get("judge_valid") is True and type(row.get("correct")) is bool]
        correct = sum(row.get("correct") is True for row in valid)
        reader_invalid_count = sum(row.get("reader_valid") is not True for row in selected)
        judge_invalid_count = sum(
            row.get("reader_valid") is True
            and not (row.get("judge_valid") is True and type(row.get("correct")) is bool)
            for row in selected
        )
        low, high = wilson_interval(correct, len(selected)) if selected else (None, None)
        failure_categories: dict[str, int] = defaultdict(int)
        for row in selected:
            failure_categories[str(row.get("failure_category", "UNCLASSIFIED"))] += 1
        results["methods"][method] = {
            "question_count": len(selected),
            "valid_count": len(valid),
            "invalid_count": len(selected) - len(valid),
            "reader_invalid_count": reader_invalid_count,
            "judge_invalid_count": judge_invalid_count,
            "correct_count": correct,
            # Primary QA accuracy treats invalid model outputs as incorrect.
            "accuracy": correct / len(selected) if selected else None,
            "valid_only_accuracy": correct / len(valid) if valid else None,
            "accuracy_wilson_95": {"low": low, "high": high},
            "reader_valid_count": sum(row.get("reader_valid") is True for row in selected),
            "failure_categories": dict(sorted(failure_categories.items())),
            "retrieval": {metric: _mean(selected, metric) for metric in METRICS},
        }
    paired_rows = []
    for question_id in sorted(u0):
        u0_row, pc2_row = row_map[("U0", question_id)], row_map[("P(C=2)", question_id)]
        u0_label, pc2_label = u0_row.get("correct"), pc2_row.get("correct")
        jointly_valid = type(u0_label) is bool and type(pc2_label) is bool
        paired_rows.append({"question_id": question_id, "U0": u0_label, "P(C=2)": pc2_label, "agreement": (u0_label == pc2_label) if jointly_valid else None, "jointly_valid": jointly_valid})
    agreement = sum(item["agreement"] is True for item in paired_rows)
    jointly_valid_count = sum(item["jointly_valid"] is True for item in paired_rows)
    invalid_pair_count = len(paired_rows) - jointly_valid_count
    results["paired"] = {
        "pair_count": len(paired_rows),
        "agreement_count": agreement,
        "jointly_valid_pair_count": jointly_valid_count,
        "invalid_pair_count": invalid_pair_count,
        "agreement_rate": agreement / jointly_valid_count if jointly_valid_count else None,
        "agreement_rate_all_pairs": agreement / len(paired_rows),
        "discordant_count": jointly_valid_count - agreement,
        "accuracy_delta_pc2_minus_u0": results["methods"]["P(C=2)"]["accuracy"] - results["methods"]["U0"]["accuracy"],
        "items": paired_rows,
    }
    results["payload_sha256"] = canonical_sha256(results)
    return results


__all__ = [
    "CLAIM_SCOPE",
    "EXPECTED_HISTORIES",
    "ExpandedAnalysisError",
    "build_gold_blind_projection",
    "canonical_sha256",
    "file_sha256",
    "load_expanded_inventory",
    "reduce_expanded_rows",
    "validate_gold_provenance",
    "wilson_interval",
]
