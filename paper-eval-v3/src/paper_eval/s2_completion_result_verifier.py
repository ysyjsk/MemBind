"""Pure verification of the sealed bounded S2 completion terminal chain."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256, sha256_file
from .s2_completion_authority import verify_completion_authorization
from .s2_completion_controller import (
    CHECKPOINT_SCHEMA,
    EVENT_SCHEMA,
    RESULT_SCHEMA,
)


@dataclass(frozen=True)
class CompletionResultPaths:
    authorization: Path
    consumption: Path
    events: Path
    checkpoint: Path
    result: Path
    failure: Path


@dataclass(frozen=True)
class VerifiedCompletionResult:
    run_id: str
    status: str
    evidence_recall_at_10: float
    qa_accuracy: float
    gold_ranks: tuple[int | None, ...]
    reader_prompt_tokens: int
    reader_truncation_count: int
    judge_parse_status: str
    result_mergeable: bool
    s3_authorized: bool


class CompletionResultVerificationError(ValueError):
    """The completion result or one of its durable bindings drifted."""


def _json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise CompletionResultVerificationError(f"{label} is unreadable") from None
    if not isinstance(value, dict):
        raise CompletionResultVerificationError(f"{label} is invalid")
    return value


def _envelope(path: Path, *, label: str) -> dict[str, Any]:
    value = _json(path, label=label)
    payload = value.get("payload")
    if (
        value.get("status") != "finalized"
        or not isinstance(payload, Mapping)
        or value.get("payload_sha256") != payload_sha256(payload)
    ):
        raise CompletionResultVerificationError(f"{label} envelope seal is invalid")
    value["payload"] = dict(payload)
    return value


def _self_sealed(value: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    body = dict(value)
    stored = body.pop("payload_sha256", None)
    if stored != payload_sha256(body):
        raise CompletionResultVerificationError(f"{label} payload seal is invalid")
    return dict(value)


def _nonnegative(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CompletionResultVerificationError(f"{label} counter is invalid")
    return value


def _verify_consumption(
    paths: CompletionResultPaths, authorization: Mapping[str, Any]
) -> dict[str, Any]:
    consumption = _envelope(paths.consumption, label="consumption")
    payload = consumption["payload"]
    if (
        payload.get("schema_version")
        != "membind.paper-eval-v3.s2-completion-consumption.v1"
        or payload.get("status") != "CONSUMED_BEFORE_LIVE_IO"
        or payload.get("run_id") != authorization.get("run_id")
        or payload.get("authorization_sha256") != sha256_file(paths.authorization)
        or payload.get("authorization_payload_sha256")
        != authorization.get("payload_sha256")
        or payload.get("live_io_performed_at_consumption") is not False
        or payload.get("s3_authorized") is not False
    ):
        raise CompletionResultVerificationError("consumption hash or identity drift")
    return consumption


def _verify_events(paths: CompletionResultPaths, *, run_id: str) -> list[dict[str, Any]]:
    try:
        lines = paths.events.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise CompletionResultVerificationError("event log is unreadable") from None
    events: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            raise CompletionResultVerificationError("event log parse failure") from None
        if not isinstance(value, dict):
            raise CompletionResultVerificationError("event record is invalid")
        event = _self_sealed(value, label="event")
        if (
            event.get("schema_version") != EVENT_SCHEMA
            or event.get("run_id") != run_id
            or event.get("history_id") != "07741c45"
            or event.get("event_sequence") != index
            or not isinstance(event.get("evidence"), Mapping)
        ):
            raise CompletionResultVerificationError("event identity drift")
        events.append(event)
    expected_types = [
        "authorization_consumed",
        "retrieval_complete",
        "reader_complete",
        "judge_complete",
        "terminal_success",
    ]
    if [event.get("event_type") for event in events] != expected_types:
        raise CompletionResultVerificationError("event sequence drift")
    return events


def _verify_checkpoint(
    paths: CompletionResultPaths, *, events: list[dict[str, Any]], run_id: str
) -> dict[str, Any]:
    checkpoint = _self_sealed(_json(paths.checkpoint, label="checkpoint"), label="checkpoint")
    if (
        checkpoint.get("schema_version") != CHECKPOINT_SCHEMA
        or checkpoint.get("run_id") != run_id
        or checkpoint.get("history_id") != "07741c45"
        or checkpoint.get("status") != "completed"
        or checkpoint.get("completed_stages")
        != ["retrieval_complete", "reader_complete", "judge_complete"]
        or checkpoint.get("event_count") != len(events)
        or checkpoint.get("events_sha256") != sha256_file(paths.events)
        or checkpoint.get("error_class") is not None
    ):
        raise CompletionResultVerificationError("checkpoint identity or hash drift")
    return checkpoint


def _verify_metrics(result: Mapping[str, Any], *, outer: Mapping[str, Any]) -> tuple[float, float, tuple[int | None, ...]]:
    retrieved_count = _nonnegative(result.get("retrieved_session_count"), label="retrieved")
    gold_count = _nonnegative(result.get("gold_session_count"), label="gold")
    covered = _nonnegative(result.get("covered_gold_session_count"), label="covered")
    ranks_value = result.get("gold_ranks")
    if (
        retrieved_count != 10
        or gold_count < 1
        or covered > gold_count
        or not isinstance(ranks_value, list)
        or len(ranks_value) != gold_count
        or any(
            rank is not None
            and (isinstance(rank, bool) or not isinstance(rank, int) or not 1 <= rank <= 10)
            for rank in ranks_value
        )
        or sum(rank is not None for rank in ranks_value) != covered
    ):
        raise CompletionResultVerificationError("result metric shape drift")
    recall_any = 1.0 if covered else 0.0
    recall_all = 1.0 if covered == gold_count else 0.0
    fraction = covered / gold_count
    if (
        result.get("session_recall_any_at_10") != recall_any
        or result.get("session_recall_all_at_10") != recall_all
        or result.get("evidence_recall_at_10") != recall_all
        or result.get("session_gold_coverage_fraction_at_10") != fraction
        or result.get("coverage_fraction_is_official") is not False
    ):
        raise CompletionResultVerificationError("result metric consistency drift")
    judge = result.get("judge")
    if not isinstance(judge, Mapping):
        raise CompletionResultVerificationError("Judge result is missing")
    label = judge.get("label")
    parse = judge.get("parse_status")
    if (
        judge.get("status") != "SUCCESS"
        or type(label) is not bool
        or parse not in {"YES", "NO"}
        or (label and parse != "YES")
        or (not label and parse != "NO")
        or judge.get("retry_count") != 0
        or judge.get("error_class") is not None
    ):
        raise CompletionResultVerificationError("Judge status drift")
    qa = 1.0 if label else 0.0
    expected_status = "PASS" if recall_all == 1.0 and qa == 1.0 else "REVIEW_REQUIRED"
    if (
        result.get("qa_accuracy") != qa
        or result.get("reference_sanity_status") != expected_status
        or outer.get("status") != expected_status
        or outer.get("reference_sanity_status") != expected_status
        or outer.get("result_mergeable") is not (expected_status == "PASS")
    ):
        raise CompletionResultVerificationError("result metric/status drift")
    return recall_all, qa, tuple(ranks_value)


def verify_s2_completion_result(paths: CompletionResultPaths) -> VerifiedCompletionResult:
    """Verify the single terminal result and all durable hash links."""

    if not isinstance(paths, CompletionResultPaths):
        raise CompletionResultVerificationError("completion result paths are invalid")
    result_exists = paths.result.is_file()
    failure_exists = paths.failure.is_file()
    if result_exists == failure_exists:
        raise CompletionResultVerificationError("exactly one terminal artifact is required")
    if failure_exists:
        raise CompletionResultVerificationError("completion attempt has a failure terminal")

    authorization = verify_completion_authorization(
        _json(paths.authorization, label="authorization")
    )
    run_id = str(authorization["run_id"])
    consumption = _verify_consumption(paths, authorization)
    events = _verify_events(paths, run_id=run_id)
    _verify_checkpoint(paths, events=events, run_id=run_id)
    envelope = _envelope(paths.result, label="result")
    payload = envelope["payload"]
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise CompletionResultVerificationError("result payload is missing")
    if (
        envelope.get("run_id") != run_id
        or payload.get("schema_version") != RESULT_SCHEMA
        or payload.get("stage") != "S2"
        or payload.get("method") != "U0"
        or payload.get("completion_scope") != "BOUNDED_ONE_HISTORY"
        or payload.get("diagnostic_only") is not False
        or payload.get("retrieval_policy_selected") is not True
        or payload.get("reader_judge_executed") is not True
        or payload.get("reference_alignment_status")
        != "PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED"
        or payload.get("authorization_sha256") != sha256_file(paths.authorization)
        or payload.get("authorization_payload_sha256")
        != authorization.get("payload_sha256")
        or payload.get("consumption_sha256") != sha256_file(paths.consumption)
        or payload.get("events_sha256") != sha256_file(paths.events)
        or payload.get("checkpoint_sha256") != sha256_file(paths.checkpoint)
    ):
        raise CompletionResultVerificationError("result hash chain drift")
    if (
        payload.get("s3_authorized") is not False
        or payload.get("s3_ready") is not False
        or result.get("s3_authorized") is not False
        or result.get("s3_ready") is not False
    ):
        raise CompletionResultVerificationError("result must leave S3 unauthorized")
    recall, qa, ranks = _verify_metrics(result, outer=payload)
    reader = result.get("reader")
    if (
        not isinstance(reader, Mapping)
        or reader.get("status") != "SUCCESS"
        or _nonnegative(reader.get("prompt_tokens"), label="Reader prompt tokens") < 1
        or reader.get("truncation_count") != 0
    ):
        raise CompletionResultVerificationError("Reader status drift")
    counters = result.get("counters")
    expected_counters = {
        "graphiti_search_calls": 1,
        "reader_requests": 1,
        "judge_requests": 1,
        "construction_llm_requests": 0,
        "embedding_requests": 0,
        "cross_encoder_requests": 0,
        "database_mutation_attempts": 0,
        "database_mutations": 0,
        "cleanup_calls": 0,
        "retry_count": 0,
    }
    if (
        not isinstance(counters, Mapping)
        or any(counters.get(key) != value for key, value in expected_counters.items())
        or _nonnegative(counters.get("neo4j_read_requests"), label="Neo4j reads") < 1
    ):
        raise CompletionResultVerificationError("result counter drift")
    return VerifiedCompletionResult(
        run_id=run_id,
        status=str(payload["status"]),
        evidence_recall_at_10=recall,
        qa_accuracy=qa,
        gold_ranks=ranks,
        reader_prompt_tokens=int(reader["prompt_tokens"]),
        reader_truncation_count=int(reader["truncation_count"]),
        judge_parse_status=str(result["judge"]["parse_status"]),
        result_mergeable=bool(payload["result_mergeable"]),
        s3_authorized=False,
    )
