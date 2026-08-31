"""DVSR-scoped C0/C1 read validation and fallback accounting."""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Mapping, Sequence

from .certificates import CertificateResult, CertificateStatus, Witness
from .dvsr_certificates import certify_dvsr_exact_topk
from .graphiti_observer import canonical_digest
from .state_delta import StateDelta


READ_ACCOUNTING_SCHEMA = "membind.dvsr.c0-c1-read-accounting.v1"
_READ_IDENTITY_FIELDS = (
    "operator",
    "query",
    "filter_fingerprint",
    "group_ids",
    "limit",
    "min_score",
    "query_epoch",
    "index_epoch",
    "config_epoch",
)
_PROMPT_VISIBLE_NODE_FIELDS = (
    "name",
    "group_id",
    "labels",
    "summary",
    "attributes",
)


class DvsrReadAccountingError(ValueError):
    pass


def _read_key(value: Mapping[str, Any]) -> tuple[str, int]:
    operator = value.get("operator")
    occurrence = value.get("occurrence")
    if (
        not isinstance(operator, str)
        or not operator
        or isinstance(occurrence, bool)
        or not isinstance(occurrence, int)
        or occurrence < 0
    ):
        raise DvsrReadAccountingError("read key is invalid")
    return operator, occurrence


def _read_map(capture: Mapping[str, Any]) -> dict[tuple[str, int], Mapping[str, Any]]:
    values = capture.get("reads")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise DvsrReadAccountingError("capture reads are invalid")
    result: dict[tuple[str, int], Mapping[str, Any]] = {}
    for value in values:
        if not isinstance(value, Mapping):
            raise DvsrReadAccountingError("read record is invalid")
        key = _read_key(value)
        if key in result:
            raise DvsrReadAccountingError("read key is duplicated")
        result[key] = value
    return result


def _ordered_projection_digest(
    read: Mapping[str, Any],
    nodes: Mapping[str, Mapping[str, Any]],
) -> str | None:
    result = read.get("actual_result")
    if not isinstance(result, Sequence) or isinstance(result, (str, bytes, bytearray)):
        return None
    projection: list[dict[str, Any]] = []
    for raw_key in result:
        key = str(raw_key)
        node = nodes.get(key)
        if not isinstance(node, Mapping):
            return None
        projection.append(
            {
                "uuid": key,
                **{field: node.get(field) for field in _PROMPT_VISIBLE_NODE_FIELDS},
            }
        )
    return canonical_digest(projection)


def _complete_read(read: Mapping[str, Any]) -> bool:
    if read.get("completeness_status") != "COMPLETE":
        return False
    if any(field not in read for field in _READ_IDENTITY_FIELDS):
        return False
    result = read.get("actual_result")
    domain = read.get("complete_domain")
    return (
        isinstance(result, Sequence)
        and not isinstance(result, (str, bytes, bytearray))
        and isinstance(domain, Sequence)
        and not isinstance(domain, (str, bytes, bytearray))
    )


def _c0_status(
    old: Mapping[str, Any] | None,
    fresh: Mapping[str, Any] | None,
    *,
    old_nodes: Mapping[str, Mapping[str, Any]],
    fresh_nodes: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str | None, str | None, str]:
    if old is None or fresh is None or not _complete_read(old) or not _complete_read(fresh):
        return "UNKNOWN_INCOMPLETE_EVIDENCE", None, None, "read alignment or completeness evidence is missing"
    old_projection = _ordered_projection_digest(old, old_nodes)
    fresh_projection = _ordered_projection_digest(fresh, fresh_nodes)
    if old_projection is None or fresh_projection is None:
        return "UNKNOWN_INCOMPLETE_EVIDENCE", old_projection, fresh_projection, "prompt-visible projection is incomplete"
    exact = all(
        canonical_digest(old.get(field)) == canonical_digest(fresh.get(field))
        for field in (*_READ_IDENTITY_FIELDS, "actual_result")
    ) and old_projection == fresh_projection
    return (
        ("VALID", old_projection, fresh_projection, "fresh requery is exact")
        if exact
        else ("INVALID_CHANGED", old_projection, fresh_projection, "fresh requery observed a complete semantic change")
    )


def _cosine(query: Sequence[Any], embedding: Sequence[Any]) -> float | None:
    if isinstance(query, (str, bytes, bytearray)) or isinstance(embedding, (str, bytes, bytearray)):
        return None
    try:
        left = tuple(float(value) for value in query)
        right = tuple(float(value) for value in embedding)
    except (TypeError, ValueError):
        return None
    if not left or len(left) != len(right) or not all(math.isfinite(value) for value in (*left, *right)):
        return None
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return None
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _c1_certificate(old: Mapping[str, Any] | None, delta: StateDelta) -> CertificateResult:
    if old is None or not _complete_read(old):
        return CertificateResult(CertificateStatus.UNKNOWN, "old witness is incomplete")
    query = old.get("query")
    actual = old.get("actual_result")
    domain_rows = old.get("complete_domain")
    ties = old.get("boundary_ties")
    if (
        not isinstance(query, Sequence)
        or isinstance(query, (str, bytes, bytearray))
        or not isinstance(actual, Sequence)
        or isinstance(actual, (str, bytes, bytearray))
        or not isinstance(domain_rows, Sequence)
        or isinstance(domain_rows, (str, bytes, bytearray))
        or not isinstance(ties, Sequence)
        or isinstance(ties, (str, bytes, bytearray))
    ):
        return CertificateResult(CertificateStatus.UNKNOWN, "old witness is incomplete")
    domain: list[str] = []
    for row in domain_rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("uuid"), str):
            return CertificateResult(CertificateStatus.UNKNOWN, "complete domain is malformed")
        domain.append(str(row["uuid"]))
    post_scores: dict[str, float] = {}
    for change in delta.changes:
        if change.kind != "node" or change.operation in {"delete", "remove"}:
            continue
        embedding = change.after.get("name_embedding") if isinstance(change.after, Mapping) else None
        if not isinstance(embedding, Sequence):
            continue
        score = _cosine(query, embedding)
        if score is not None:
            post_scores[change.key] = score
    result_ids = tuple(str(value) for value in actual)
    changed_nonmembers = tuple(
        change
        for change in delta.changes
        if change.kind == "node"
        and change.key not in result_ids
        and change.operation not in {"delete", "remove"}
    )
    min_score = old.get("min_score")
    no_new_eligible = (
        isinstance(min_score, (int, float))
        and not isinstance(min_score, bool)
        and all(
            change.key in post_scores and post_scores[change.key] < float(min_score)
            for change in changed_nonmembers
        )
    )
    try:
        witness = Witness(
            operator=str(old["operator"]),
            query=tuple(float(value) for value in query),
            result=result_ids,
            domain=tuple(domain),
            k=int(old["limit"]),
            cutoff=float(old["cutoff"]) if old.get("cutoff") is not None else None,
            ties=tuple(str(value) for value in ties),
            query_epoch=str(old.get("query_epoch") or ""),
            index_epoch=str(old.get("index_epoch") or ""),
            filter_fingerprint=str(old.get("filter_fingerprint") or ""),
            proof_data={
                "post_scores": post_scores,
                "tie_contract": "strict-score-separation" if not ties else None,
                "no_new_eligible": no_new_eligible,
                "min_score": min_score,
            },
        )
    except (KeyError, TypeError, ValueError):
        return CertificateResult(CertificateStatus.UNKNOWN, "old witness is malformed")
    return certify_dvsr_exact_topk(witness, delta)


def _interval(read: Mapping[str, Any] | None) -> tuple[int, int] | None:
    if read is None:
        return None
    start, end = read.get("native_start_ns"), read.get("native_end_ns")
    if (
        isinstance(start, bool)
        or not isinstance(start, int)
        or isinstance(end, bool)
        or not isinstance(end, int)
        or end < start
    ):
        return None
    return start, end


def _union_duration(intervals: Sequence[tuple[int, int]]) -> int:
    values = sorted((start, end) for start, end in intervals if end > start)
    if not values:
        return 0
    total = 0
    left, right = values[0]
    for next_left, next_right in values[1:]:
        if next_left <= right:
            right = max(right, next_right)
        else:
            total += right - left
            left, right = next_left, next_right
    return total + right - left


def _status(value: CertificateStatus) -> str:
    if value is CertificateStatus.STABLE:
        return "VALID"
    if value is CertificateStatus.INVALID:
        return "INVALID_CHANGED"
    return "UNKNOWN_INCOMPLETE_EVIDENCE"


def evaluate_c0_c1_read_accounting(
    *,
    old_capture: Mapping[str, Any],
    fresh_capture: Mapping[str, Any],
    delta: StateDelta,
    old_nodes: Mapping[str, Mapping[str, Any]],
    fresh_nodes: Mapping[str, Mapping[str, Any]],
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> dict[str, Any]:
    """Evaluate C1 against C0 truth and charge C0 only on C1 fallback."""

    if not isinstance(delta, StateDelta):
        raise DvsrReadAccountingError("state delta is invalid")
    old_reads = _read_map(old_capture)
    fresh_reads = _read_map(fresh_capture)
    rows: list[dict[str, Any]] = []
    c0_valid: list[tuple[str, int]] = []
    c1_valid: list[tuple[str, int]] = []
    reusable: list[tuple[str, int]] = []
    all_intervals: list[tuple[int, int]] = []
    fallback_intervals: list[tuple[int, int]] = []
    certificate_cost = 0
    incomplete_interval = False
    for key in sorted(set(old_reads) | set(fresh_reads)):
        old = old_reads.get(key)
        fresh = fresh_reads.get(key)
        c0_status, old_digest, fresh_digest, c0_reason = _c0_status(
            old,
            fresh,
            old_nodes=old_nodes,
            fresh_nodes=fresh_nodes,
        )
        started = clock_ns()
        certificate = _c1_certificate(old, delta)
        ended = clock_ns()
        if ended < started:
            raise DvsrReadAccountingError("C1 clock moved backwards")
        certificate_cost += ended - started
        c1_status = _status(certificate.status)
        if c0_status == "VALID":
            c0_valid.append(key)
        if c1_status == "VALID":
            c1_valid.append(key)
        can_reuse = c0_status == "VALID" and c1_status == "VALID"
        if can_reuse:
            reusable.append(key)
        interval = _interval(fresh)
        if interval is None:
            incomplete_interval = True
        else:
            all_intervals.append(interval)
            if not can_reuse:
                fallback_intervals.append(interval)
        rows.append(
            {
                "operator": key[0],
                "occurrence": key[1],
                "c0_status": c0_status,
                "c0_reason": c0_reason,
                "c0_old_prompt_projection_digest": old_digest,
                "c0_fresh_prompt_projection_digest": fresh_digest,
                "c1_status": c1_status,
                "c1_reason": certificate.reason,
                "selected_path": (
                    "C1_VALID_SKIP_C0"
                    if can_reuse
                    else "C0_VALID_FALLBACK"
                    if c0_status == "VALID"
                    else "FRESH_REQUIRED_CHANGED"
                    if c0_status == "INVALID_CHANGED"
                    else "UNKNOWN_INCOMPLETE_EVIDENCE"
                ),
                "reusable": can_reuse,
            }
        )
    c0_set, c1_set = set(c0_valid), set(c1_valid)
    false_valid = sorted(c1_set - c0_set)
    c0_cost = _union_duration(all_intervals)
    fallback_cost = _union_duration(fallback_intervals)
    c0_unknown = sum(row["c0_status"] == "UNKNOWN_INCOMPLETE_EVIDENCE" for row in rows)
    c1_unknown = sum(row["c1_status"] == "UNKNOWN_INCOMPLETE_EVIDENCE" for row in rows)
    status = (
        "UNSOUND_FALSE_VALID"
        if false_valid
        else "UNKNOWN_INCOMPLETE_EVIDENCE"
        if c0_unknown or incomplete_interval
        else "COMPLETE"
    )
    return {
        "schema_version": READ_ACCOUNTING_SCHEMA,
        "status": status,
        "rows": rows,
        "c0_valid_keys": [[operator, occurrence] for operator, occurrence in c0_valid],
        "c1_valid_keys": [[operator, occurrence] for operator, occurrence in c1_valid],
        "reusable_read_keys": [[operator, occurrence] for operator, occurrence in reusable],
        "c1_valid_subset_of_c0": not false_valid,
        "false_valid_count": len(false_valid),
        "false_valid_keys": [[operator, occurrence] for operator, occurrence in false_valid],
        "c0_invalid_count": sum(row["c0_status"] == "INVALID_CHANGED" for row in rows),
        "c0_unknown_count": c0_unknown,
        "c1_invalid_count": sum(row["c1_status"] == "INVALID_CHANGED" for row in rows),
        "c1_unknown_count": c1_unknown,
        "unknown_count": sum(
            row["c0_status"] == "UNKNOWN_INCOMPLETE_EVIDENCE"
            or row["c1_status"] == "UNKNOWN_INCOMPLETE_EVIDENCE"
            for row in rows
        ),
        "c0_fresh_requery_cost_ns": c0_cost,
        "c1_certificate_cost_ns": certificate_cost,
        "c0_fallback_cost_ns": fallback_cost,
        "selected_validation_cost_ns": certificate_cost + fallback_cost,
        "cost_accounting_status": "MISSING_FIELD" if incomplete_interval else "COMPLETE",
    }


__all__ = [
    "DvsrReadAccountingError",
    "READ_ACCOUNTING_SCHEMA",
    "evaluate_c0_c1_read_accounting",
]
