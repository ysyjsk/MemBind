"""Gold-only LongMemEval-S operationalization of selective forgetting.

This module is intentionally separate from the v1.3 construction runner.  It
freezes the *operation* to be evaluated later; it does not call Graphiti,
construct a graph, invoke an LLM, or inspect a B0/B1 result.  The protocol is
grounded in MemoryAgentBench (ICLR 2026), whose Selective Forgetting and
FactConsolidation tasks require a memory system to resolve an old fact against
a newer contradictory fact after incremental injection.

LongMemEval-S does not provide structured ``old_value``/``new_value`` fields.
Consequently this adapter uses the two official answer sessions as an
old-session/new-session *provenance pair*, while keeping the values opaque and
retaining the official final answer verbatim.  It is therefore an explicit
LongMemEval operationalization, not a claim of exact FactConsolidation data
reproduction.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from saturated_fixed_work_baseline_v1_2.contracts import EpisodeInput


RAW_LONGMEMEVAL_PATH = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
EXPECTED_RAW_SHA256 = (
    "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
)
EXPECTED_RECORD_COUNT = 500
EXPECTED_KNOWLEDGE_UPDATE_COUNT = 78
EXPECTED_ABSTENTION_COUNT = 6
EXPECTED_NON_ABSTENTION_COUNT = 72

REFERENCE_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)
REFERENCE_TIME_POLICY = {
    "name": "LONGMEMEVAL_SOURCE_ORDER_MONOTONIC_V1",
    "epoch": "2000-01-01T00:00:00Z",
    "step_seconds": 60,
    "basis": "source_sequence_only",
    "gold_blind": True,
    "question_date_used_for_construction": False,
}

LITERATURE_PROVENANCE = {
    "benchmark": "MemoryAgentBench",
    "venue": "ICLR 2026",
    "title": "Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions",
    "authors": "Yuanzhe Hu, Yu Wang, Julian McAuley",
    "arxiv": "2507.05257v4",
    "reference_url": "https://arxiv.org/abs/2507.05257",
    "paper_sections": ["§3.3", "Appendix B.4", "Appendix K"],
    "operation": "Selective Forgetting / FactConsolidation",
    "paper_operation": [
        "inject facts incrementally",
        "assign larger serial numbers to newer facts",
        "resolve contradictory old/new facts in favor of the newest fact",
        "query after all injection",
        "score final answer with substring exact match (SubEM)-compatible semantics",
    ],
    "source_dataset": "LongMemEval-S",
    "adaptation_status": "LONGMEMEVAL_OPERATIONALIZATION",
    "exact_memoryagentbench_reproduction": False,
    "adaptation_boundary": (
        "LongMemEval-S official knowledge-update records supply two answer-session "
        "provenance anchors and a final gold answer, but no structured old_value/new_value. "
        "The adapter therefore freezes old/new provenance while keeping values opaque."
    ),
    "selection_is_gold_only": True,
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class LongMemEvalOperationError(ValueError):
    """A source, operation, or artifact contract failed closed."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _nonempty_text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LongMemEvalOperationError(code)
    return value


def _role(message: Any) -> str:
    if isinstance(message, Mapping):
        value = str(
            message.get("role")
            or message.get("speaker")
            or message.get("from")
            or "user"
        )
    elif isinstance(message, (list, tuple)) and message:
        value = str(message[0])
    else:
        value = "user"
    value = value.strip().upper()
    return {
        "HUMAN": "USER",
        "CUSTOMER": "USER",
        "AI": "ASSISTANT",
        "BOT": "ASSISTANT",
    }.get(value, value or "USER")


def _content(message: Any) -> str:
    if isinstance(message, Mapping):
        for key in ("content", "text", "message", "value"):
            if message.get(key) is not None:
                return str(message[key])
        return json.dumps(message, ensure_ascii=False, sort_keys=True)
    if isinstance(message, (list, tuple)) and len(message) >= 2:
        return str(message[1])
    return str(message)


def session_body(session: Any) -> str:
    """Render a session with the same role/content convention as v1.2."""

    if isinstance(session, Mapping):
        messages = next(
            (
                session[key]
                for key in ("messages", "turns", "conversation")
                if isinstance(session.get(key), list)
            ),
            [session],
        )
    elif isinstance(session, list):
        messages = session
    else:
        messages = [session]
    if not messages:
        raise LongMemEvalOperationError("LONGMEMEVAL_SESSION_EMPTY")
    return "\n".join(
        f"[{_role(row)}] {_content(row).strip()}" for row in messages
    )


def _parse_dataset(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise LongMemEvalOperationError("LONGMEMEVAL_DATASET_UNREADABLE") from None
    if not isinstance(value, list):
        raise LongMemEvalOperationError("LONGMEMEVAL_DATASET_LIST_REQUIRED")
    records: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, Mapping):
            raise LongMemEvalOperationError("LONGMEMEVAL_RECORD_INVALID")
        records.append(dict(row))
    return records


def load_longmemeval_records(
    path: Path = RAW_LONGMEMEVAL_PATH,
    *,
    enforce_pinned_sha256: bool = True,
) -> tuple[dict[str, Any], ...]:
    """Load the raw file, pinning its bytes by default."""

    path = Path(path)
    try:
        raw_sha256 = _sha256_bytes(path.read_bytes())
    except OSError:
        raise LongMemEvalOperationError("LONGMEMEVAL_DATASET_UNREADABLE") from None
    if enforce_pinned_sha256 and raw_sha256 != EXPECTED_RAW_SHA256:
        raise LongMemEvalOperationError("LONGMEMEVAL_DATASET_SHA256_MISMATCH")
    records = _parse_dataset(path)
    validate_longmemeval_inventory(records)
    return tuple(records)


def validate_longmemeval_inventory(records: Sequence[Mapping[str, Any]]) -> None:
    """Validate structural inventory without selecting on any execution result."""

    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise LongMemEvalOperationError("LONGMEMEVAL_DATASET_LIST_REQUIRED")
    if len(records) != EXPECTED_RECORD_COUNT:
        raise LongMemEvalOperationError("LONGMEMEVAL_RECORD_COUNT_MISMATCH")
    ids: set[str] = set()
    knowledge_update = 0
    abstention = 0
    for row in records:
        if not isinstance(row, Mapping):
            raise LongMemEvalOperationError("LONGMEMEVAL_RECORD_INVALID")
        question_id = _nonempty_text(row.get("question_id"), "LONGMEMEVAL_QUESTION_ID_INVALID")
        if question_id in ids:
            raise LongMemEvalOperationError("LONGMEMEVAL_DUPLICATE_QUESTION_ID")
        ids.add(question_id)
        question_type = _nonempty_text(
            row.get("question_type"), "LONGMEMEVAL_QUESTION_TYPE_INVALID"
        )
        if question_type != "knowledge-update":
            continue
        knowledge_update += 1
        if question_id.endswith("_abs"):
            abstention += 1
    if knowledge_update != EXPECTED_KNOWLEDGE_UPDATE_COUNT:
        raise LongMemEvalOperationError("LONGMEMEVAL_KNOWLEDGE_UPDATE_COUNT_MISMATCH")
    if abstention != EXPECTED_ABSTENTION_COUNT:
        raise LongMemEvalOperationError("LONGMEMEVAL_ABSTENTION_COUNT_MISMATCH")


def _scalar_answer(value: Any) -> str | int | float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise LongMemEvalOperationError("LONGMEMEVAL_ANSWER_NOT_SCALAR")
    if isinstance(value, str) and not value.strip():
        raise LongMemEvalOperationError("LONGMEMEVAL_ANSWER_EMPTY")
    return value


@dataclass(frozen=True, slots=True)
class LongMemEvalSegment:
    source_sequence: int
    segment_index: int
    session_id: str
    original_date: str
    reference_time: str
    raw_session: Any
    raw_session_sha256: str
    body_sha256: str

    @property
    def body(self) -> str:
        return session_body(self.raw_session)


@dataclass(frozen=True, slots=True)
class LongMemEvalFactConsolidationCase:
    question_id: str
    history_id: str
    question_type: str
    question: str
    question_date: str
    gold_current_answer: str | int | float
    answer_session_ids: tuple[str, str]
    old_session_id: str
    new_session_id: str
    old_segment_index: int
    new_segment_index: int
    old_session_body_sha256: str
    new_session_body_sha256: str
    source_record_sha256: str
    source_manifest_sha256: str
    segments: tuple[LongMemEvalSegment, ...]
    old_value_status: str = "OPAQUE_UNLESS_PROVABLE"
    old_value: None = None
    new_value: None = None
    query_after_all_injection: bool = True
    subem_compatible: bool = True
    selection_reason: str = "KNOWLEDGE_UPDATE_TWO_SESSION_NON_ABSTENTION"

    def __post_init__(self) -> None:
        if self.old_value is not None or self.new_value is not None:
            raise LongMemEvalOperationError("LONGMEMEVAL_OLD_NEW_VALUES_MUST_REMAIN_OPAQUE")
        if len(self.answer_session_ids) != 2:
            raise LongMemEvalOperationError("LONGMEMEVAL_ANSWER_SESSION_PAIR_INVALID")
        if self.old_segment_index >= self.new_segment_index:
            raise LongMemEvalOperationError("LONGMEMEVAL_SOURCE_ORDER_INVALID")
        if not self.query_after_all_injection or not self.subem_compatible:
            raise LongMemEvalOperationError("LONGMEMEVAL_OPERATION_FLAGS_INVALID")

    @property
    def source_count(self) -> int:
        return len(self.segments)

    @property
    def raw_date_order_status(self) -> str:
        old = self.segments[self.old_segment_index].original_date
        new = self.segments[self.new_segment_index].original_date
        try:
            old_dt = datetime.strptime(old, "%Y/%m/%d (%a) %H:%M")
            new_dt = datetime.strptime(new, "%Y/%m/%d (%a) %H:%M")
        except (TypeError, ValueError):
            return "NOT_PARSEABLE"
        return "MONOTONIC" if old_dt < new_dt else "NON_MONOTONIC_OR_EQUAL"

    def to_manifest_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "history_id": self.history_id,
            "question_type": self.question_type,
            "question": self.question,
            "question_date": self.question_date,
            "gold_current_answer": self.gold_current_answer,
            "answer_session_ids": list(self.answer_session_ids),
            "old_session_id": self.old_session_id,
            "new_session_id": self.new_session_id,
            "old_segment_index": self.old_segment_index,
            "new_segment_index": self.new_segment_index,
            "old_session_body_sha256": self.old_session_body_sha256,
            "new_session_body_sha256": self.new_session_body_sha256,
            "old_value_status": self.old_value_status,
            "old_value": None,
            "new_value": None,
            "source_record_sha256": self.source_record_sha256,
            "source_manifest_sha256": self.source_manifest_sha256,
            "source_count": self.source_count,
            "source_order": [
                {
                    "source_sequence": segment.source_sequence,
                    "segment_index": segment.segment_index,
                    "session_id": segment.session_id,
                    "original_date": segment.original_date,
                    "reference_time": segment.reference_time,
                    "raw_session_sha256": segment.raw_session_sha256,
                    "body_sha256": segment.body_sha256,
                }
                for segment in self.segments
            ],
            "raw_date_order_status": self.raw_date_order_status,
            "query_after_all_injection": self.query_after_all_injection,
            "subem_compatible": self.subem_compatible,
            "selection_reason": self.selection_reason,
        }


def _build_case(row: Mapping[str, Any]) -> LongMemEvalFactConsolidationCase:
    question_id = _nonempty_text(row.get("question_id"), "LONGMEMEVAL_QUESTION_ID_INVALID")
    if row.get("question_type") != "knowledge-update":
        raise LongMemEvalOperationError("LONGMEMEVAL_OPERATION_TYPE_INVALID")
    if question_id.endswith("_abs"):
        raise LongMemEvalOperationError("LONGMEMEVAL_ABSTENTION_EXCLUDED")
    question = _nonempty_text(row.get("question"), "LONGMEMEVAL_QUESTION_INVALID")
    question_date = _nonempty_text(row.get("question_date"), "LONGMEMEVAL_QUESTION_DATE_INVALID")
    answer = _scalar_answer(row.get("answer"))
    session_ids = row.get("haystack_session_ids")
    dates = row.get("haystack_dates")
    sessions = row.get("haystack_sessions")
    answer_ids = row.get("answer_session_ids")
    if not all(isinstance(value, list) for value in (session_ids, dates, sessions, answer_ids)):
        raise LongMemEvalOperationError("LONGMEMEVAL_SESSION_INVENTORY_INVALID")
    if not session_ids or len(session_ids) != len(dates) or len(session_ids) != len(sessions):
        raise LongMemEvalOperationError("LONGMEMEVAL_SESSION_INVENTORY_INVALID")
    if len(answer_ids) != 2 or any(not isinstance(value, str) or not value for value in answer_ids):
        raise LongMemEvalOperationError("LONGMEMEVAL_ANSWER_SESSION_PAIR_INVALID")
    if answer_ids[0] == answer_ids[1] or any(value not in session_ids for value in answer_ids):
        raise LongMemEvalOperationError("LONGMEMEVAL_ANSWER_SESSION_REFERENCE_INVALID")
    positions = tuple(session_ids.index(value) for value in answer_ids)
    if positions[0] >= positions[1]:
        raise LongMemEvalOperationError("LONGMEMEVAL_ANSWER_SESSION_SOURCE_ORDER_INVALID")

    segments: list[LongMemEvalSegment] = []
    for source_sequence, (sid, original_date, raw_session) in enumerate(
        zip(session_ids, dates, sessions, strict=True)
    ):
        if not isinstance(sid, str) or not sid:
            raise LongMemEvalOperationError("LONGMEMEVAL_SESSION_ID_INVALID")
        if not isinstance(original_date, str) or not original_date:
            raise LongMemEvalOperationError("LONGMEMEVAL_SESSION_DATE_INVALID")
        body = session_body(raw_session)
        reference_time = (
            REFERENCE_EPOCH + timedelta(seconds=REFERENCE_TIME_POLICY["step_seconds"] * source_sequence)
        ).isoformat().replace("+00:00", "Z")
        segments.append(
            LongMemEvalSegment(
                source_sequence=source_sequence,
                segment_index=source_sequence,
                session_id=sid,
                original_date=original_date,
                reference_time=reference_time,
                raw_session=raw_session,
                raw_session_sha256=_sha256(raw_session),
                body_sha256=_sha256_bytes(body.encode("utf-8")),
            )
        )
    source_manifest = [
        {
            "source_sequence": segment.source_sequence,
            "segment_index": segment.segment_index,
            "session_id": segment.session_id,
            "original_date": segment.original_date,
            "reference_time": segment.reference_time,
            "raw_session_sha256": segment.raw_session_sha256,
            "body_sha256": segment.body_sha256,
        }
        for segment in segments
    ]
    old_segment, new_segment = (segments[positions[0]], segments[positions[1]])
    return LongMemEvalFactConsolidationCase(
        question_id=question_id,
        history_id=question_id,
        question_type="knowledge-update",
        question=question,
        question_date=question_date,
        gold_current_answer=answer,
        answer_session_ids=(str(answer_ids[0]), str(answer_ids[1])),
        old_session_id=str(answer_ids[0]),
        new_session_id=str(answer_ids[1]),
        old_segment_index=positions[0],
        new_segment_index=positions[1],
        old_session_body_sha256=old_segment.body_sha256,
        new_session_body_sha256=new_segment.body_sha256,
        source_record_sha256=_sha256(dict(row)),
        source_manifest_sha256=_sha256(source_manifest),
        segments=tuple(segments),
    )


def select_longmemeval_cases(
    records: Sequence[Mapping[str, Any]], *, limit: int | None = None
) -> tuple[LongMemEvalFactConsolidationCase, ...]:
    """Select the fixed operation cohort from official gold structure only."""

    validate_longmemeval_inventory(records)
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
    ):
        raise LongMemEvalOperationError("LONGMEMEVAL_LIMIT_INVALID")
    selected: list[LongMemEvalFactConsolidationCase] = []
    for row in records:
        if row.get("question_type") != "knowledge-update":
            continue
        question_id = str(row.get("question_id"))
        if question_id.endswith("_abs"):
            continue
        selected.append(_build_case(row))
        if limit is not None and len(selected) == limit:
            break
    if len(selected) != (EXPECTED_NON_ABSTENTION_COUNT if limit is None else limit):
        raise LongMemEvalOperationError("LONGMEMEVAL_OPERATION_COHORT_COUNT_INVALID")
    return tuple(selected)


def build_episode_inputs(
    case: LongMemEvalFactConsolidationCase, namespace: str
) -> tuple[EpisodeInput, ...]:
    """Project all source sessions into the already-qualified v1.3 shape."""

    if not isinstance(case, LongMemEvalFactConsolidationCase) or not isinstance(namespace, str) or not namespace:
        raise LongMemEvalOperationError("LONGMEMEVAL_EPISODE_INPUT_INVALID")
    episodes: list[EpisodeInput] = []
    for segment in case.segments:
        body = segment.body
        source_hash = _sha256(
            {
                "adapter": "sfwb-v1.3-longmemeval-fact-consolidation",
                "question_id": case.question_id,
                "source_sequence": segment.source_sequence,
                "segment_index": segment.segment_index,
                "session_id": segment.session_id,
                "reference_time": segment.reference_time,
                "body": body,
            }
        )
        episodes.append(
            EpisodeInput(
                history_id=case.history_id,
                session_id=segment.session_id,
                source_sequence=segment.source_sequence,
                source_hash=source_hash,
                reference_time=segment.reference_time,
                body=body,
                namespace=namespace,
            )
        )
    if tuple(item.source_sequence for item in episodes) != tuple(range(len(episodes))):
        raise LongMemEvalOperationError("LONGMEMEVAL_EPISODE_SOURCE_ORDER_INVALID")
    return tuple(episodes)


def build_workload_identity(
    case: LongMemEvalFactConsolidationCase, episodes: Sequence[EpisodeInput]
) -> str:
    if not episodes or any(not isinstance(item, EpisodeInput) for item in episodes):
        raise LongMemEvalOperationError("LONGMEMEVAL_WORKLOAD_EPISODES_INVALID")
    if len(episodes) != case.source_count:
        raise LongMemEvalOperationError("LONGMEMEVAL_WORKLOAD_SOURCE_COUNT_INVALID")
    return _sha256(
        {
            "protocol": "sfwb-v1.3-longmemeval-fact-consolidation-v1",
            "question_id": case.question_id,
            "source_record_sha256": case.source_record_sha256,
            "episodes": [
                {
                    "source_sequence": item.source_sequence,
                    "session_id": item.session_id,
                    "source_hash": item.source_hash,
                    "reference_time": item.reference_time,
                    "body": item.body,
                }
                for item in episodes
            ],
        }
    )


def build_graph_only_qa_projection(
    case: LongMemEvalFactConsolidationCase,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return public/private manifests for a later graph-only QA lane.

    This function does not retrieve or answer anything.  The future reader's
    public query receives only the question and graph-evidence contract;
    source-local sessions, answer-session IDs, and the gold answer are private
    evaluator metadata.
    """

    query_time = (
        REFERENCE_EPOCH
        + timedelta(seconds=REFERENCE_TIME_POLICY["step_seconds"] * case.source_count)
    ).isoformat().replace("+00:00", "Z")
    public = {
        "question_id": case.question_id,
        "history_id": case.history_id,
        "question_type": "LongMemEval-SelectiveForgetting",
        "operation": "FactConsolidation-SH-adapted",
        "question": case.question,
        "query_after_all_injection": True,
        "query_reference_time": query_time,
        "evidence_surface": "GRAPH_ONLY",
        "source_local_context_included": False,
        "full_gold_conversation_included": False,
        "old_value_status": case.old_value_status,
        "subem_compatible": case.subem_compatible,
    }
    private = {
        "question_id": case.question_id,
        "gold_current_answer": case.gold_current_answer,
        "gold_answer_session_ids": list(case.answer_session_ids),
        "old_value_status": case.old_value_status,
        "old_value": case.old_value,
        "new_value": case.new_value,
    }
    return public, private


def discover_completed_graph_coverage(artifact_root: Path) -> dict[str, Any]:
    """Inventory existing canonical graph files without reading their payloads."""

    artifact_root = Path(artifact_root)
    pattern = re.compile(
        r"^formal-(?P<ordinal>\d+)-(?P<history>[A-Za-z0-9_.-]+)-(?P<method>B0_NATIVE_SERIAL|B1_NAIVE_WHOLE_UPDATE_ASYNC)$"
    )
    rows: list[dict[str, Any]] = []
    for graph_path in sorted(artifact_root.glob("blocks/*/attempt-*/canonical_graph.json")):
        block_name = graph_path.parent.parent.name
        match = pattern.fullmatch(block_name)
        if match is None:
            continue
        rows.append(
            {
                "history_id": match.group("history"),
                "method": match.group("method"),
                "block_id": block_name,
                "canonical_graph_path": str(graph_path),
                "payload_read": False,
            }
        )
    histories = sorted({row["history_id"] for row in rows})
    methods = sorted({row["method"] for row in rows})
    paired = sorted(
        history
        for history in histories
        if {row["method"] for row in rows if row["history_id"] == history}
        == {"B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC"}
    )
    return {
        "artifact_root": str(artifact_root),
        "payloads_read": False,
        "rows": rows,
        "history_count": len(histories),
        "method_count": len(methods),
        "paired_history_ids": paired,
        "completed_graph_coverage": len(paired),
    }


def build_operation_manifest(
    cases: Sequence[LongMemEvalFactConsolidationCase],
    *,
    raw_file_sha256: str = EXPECTED_RAW_SHA256,
    completed_graph_coverage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not cases:
        raise LongMemEvalOperationError("LONGMEMEVAL_OPERATION_COHORT_EMPTY")
    question_ids = [case.question_id for case in cases]
    if len(set(question_ids)) != len(question_ids):
        raise LongMemEvalOperationError("LONGMEMEVAL_OPERATION_COHORT_DUPLICATE")
    payload: dict[str, Any] = {
        "schema_version": "sfwb.v1.3.longmemeval-fact-consolidation-operation.v1",
        "status": "OFFLINE_OPERATION_FROZEN",
        "selection_basis": "OFFICIAL_LONGMEMEVAL_GOLD_STRUCTURE_ONLY",
        "selection_reads_b0_b1_results": False,
        "selection_reads_execution_outcomes": False,
        "source_dataset": {
            "path": str(RAW_LONGMEMEVAL_PATH),
            "sha256": raw_file_sha256,
            "record_count": EXPECTED_RECORD_COUNT,
            "knowledge_update_count": EXPECTED_KNOWLEDGE_UPDATE_COUNT,
            "abstention_excluded_count": EXPECTED_ABSTENTION_COUNT,
            "non_abstention_cohort_count": len(cases),
        },
        "literature_provenance": dict(LITERATURE_PROVENANCE),
        "reference_time_policy": dict(REFERENCE_TIME_POLICY),
        "operation_contract": {
            "source_order": "haystack_sessions order; answer_session_ids[0] is old anchor and [1] is new anchor",
            "incremental_injection": True,
            "query_after_all_injection": True,
            "conflict_resolution_target": "newest/current state",
            "old_new_values": "OPAQUE_UNLESS_PROVABLE",
            "confirmed_counterfactual_value_pair": "NOT_EXPOSED_BY_LONGMEMEVAL",
            "eligibility_level": "STRUCTURAL_TWO_SESSION_UPDATE_PAIR_ONLY",
            "subem_compatible": True,
            "graph_only_qa_later": True,
        },
        "cases": [case.to_manifest_dict() for case in cases],
        "completed_graph_coverage": dict(completed_graph_coverage or {
            "completed_graph_coverage": 0,
            "paired_history_ids": [],
            "payloads_read": False,
        }),
    }
    payload["payload_sha256"] = _sha256(payload)
    return payload


def write_operation_artifact(path: Path, payload: Mapping[str, Any]) -> None:
    """Create one JSON artifact exactly once; never overwrite a freeze."""

    path = Path(path)
    if path.exists():
        raise LongMemEvalOperationError(f"ARTIFACT_ALREADY_EXISTS:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    descriptor = path.open("xb")
    try:
        descriptor.write(data)
        descriptor.flush()
    finally:
        descriptor.close()


__all__ = [
    "EXPECTED_RAW_SHA256",
    "EXPECTED_RECORD_COUNT",
    "EXPECTED_KNOWLEDGE_UPDATE_COUNT",
    "EXPECTED_ABSTENTION_COUNT",
    "EXPECTED_NON_ABSTENTION_COUNT",
    "LITERATURE_PROVENANCE",
    "RAW_LONGMEMEVAL_PATH",
    "REFERENCE_TIME_POLICY",
    "LongMemEvalOperationError",
    "LongMemEvalSegment",
    "LongMemEvalFactConsolidationCase",
    "session_body",
    "load_longmemeval_records",
    "validate_longmemeval_inventory",
    "select_longmemeval_cases",
    "build_episode_inputs",
    "build_workload_identity",
    "build_graph_only_qa_projection",
    "discover_completed_graph_coverage",
    "build_operation_manifest",
    "write_operation_artifact",
]
