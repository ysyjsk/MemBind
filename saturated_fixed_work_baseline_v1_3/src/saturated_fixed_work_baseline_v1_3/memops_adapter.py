"""Thin, gold-derived MemOps adapter for the v1.3 construction contract.

The adapter owns only MemOps parsing, eligibility, and projection into the
already-qualified v1.3 ``EpisodeInput``/QA shapes.  It does not implement a
construction policy or a Graphiti client.
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


DEFAULT_MEMOPS_ROOT = Path("/data/predator/ly/third_party/MemOps")
EVIDENCE_RELATIVE_ROOT = Path("generated_result/2-evidence_conversation")
QUALIFYING_QA_TYPES = frozenset(
    {"StateTransition", "CandidateDisambiguation", "StateTrajectory"}
)
REFERENCE_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class MemOpsAdapterError(ValueError):
    """MemOps source or frozen workload contract failed closed."""


@dataclass(frozen=True, slots=True)
class ConfirmedTransition:
    target_id: str
    target_name: str
    old_value: str
    new_value: str
    old_operation_id: str
    new_operation_id: str
    old_segment_index: int
    new_segment_index: int


@dataclass(frozen=True, slots=True)
class MemOpsQA:
    question_id: str
    question_pair_id: str
    evaluation_setting: str
    evaluation_type: str
    question: str
    candidate_options: tuple[str, ...]
    expected_answer: str
    gold_memory_state: str
    judge_rubric: Mapping[str, Any]
    gold_provenance: tuple[Mapping[str, Any], ...]

    @property
    def public_question(self) -> str:
        if not self.candidate_options:
            return self.question
        options = "\n".join(f"- {option}" for option in self.candidate_options)
        return f"{self.question}\nOptions:\n{options}"


@dataclass(frozen=True, slots=True)
class MemOpsSample:
    sample_id: str
    operation_type: str
    source_file: str
    source_sha256: str
    history_id: str
    target_id: str
    target_name: str
    transitions: tuple[ConfirmedTransition, ...]
    latest_confirmed_value: str
    stale_confirmed_values: tuple[str, ...]
    questions: tuple[MemOpsQA, ...]
    raw: Mapping[str, Any]

    @property
    def special_semantics_present(self) -> bool:
        operations = self.raw.get("operations", [])
        return any(
            isinstance(operation, Mapping)
            and (
                operation.get("validity") in {"tentative", "retracted"}
                or operation.get("type") in {"forget", "reflect"}
            )
            for operation in operations
        )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise MemOpsAdapterError(f"MEMOPS_JSON_UNREADABLE:{path.name}") from None
    if not isinstance(value, dict):
        raise MemOpsAdapterError("MEMOPS_JSON_OBJECT_REQUIRED")
    return value


def _sample_id(path: Path) -> str:
    name = path.name
    for suffix in ("_trajectory_ops.json", "_update.json"):
        if name.endswith(suffix):
            value = name[: -len(suffix)]
            break
    else:
        raise MemOpsAdapterError("MEMOPS_SOURCE_FILENAME_INVALID")
    if _SAFE_ID.fullmatch(value) is None:
        raise MemOpsAdapterError("MEMOPS_SAMPLE_ID_INVALID")
    return value


def _segment_index(operation: Mapping[str, Any]) -> int:
    trigger = operation.get("trigger_span")
    if not isinstance(trigger, Mapping):
        raise MemOpsAdapterError("MEMOPS_TRIGGER_SPAN_MISSING")
    value = trigger.get("segment_index")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MemOpsAdapterError("MEMOPS_SEGMENT_INDEX_INVALID")
    return value


def _operation_target(operation: Mapping[str, Any]) -> tuple[str, str]:
    target = operation.get("target")
    if not isinstance(target, Mapping):
        raise MemOpsAdapterError("MEMOPS_TARGET_MISSING")
    target_id = str(target.get("target_id") or "")
    target_name = str(target.get("target_name") or target_id)
    if not target_id or not target_name:
        raise MemOpsAdapterError("MEMOPS_TARGET_INVALID")
    return target_id, target_name


def _confirmed_update_pairs(
    operations: Sequence[Mapping[str, Any]],
) -> tuple[ConfirmedTransition, ...]:
    pairs: list[ConfirmedTransition] = []
    for index, old_operation in enumerate(operations):
        if (
            old_operation.get("type") != "update"
            or old_operation.get("validity") != "confirmed"
            or old_operation.get("old_value") is None
            or old_operation.get("new_value") is None
            or old_operation.get("old_value") == old_operation.get("new_value")
        ):
            continue
        old_target_id, old_target_name = _operation_target(old_operation)
        old_segment = _segment_index(old_operation)
        for new_operation in operations[index + 1 :]:
            if (
                new_operation.get("type") != "update"
                or new_operation.get("validity") != "confirmed"
                or new_operation.get("old_value") is None
                or new_operation.get("new_value") is None
                or new_operation.get("old_value") == new_operation.get("new_value")
            ):
                continue
            new_target_id, new_target_name = _operation_target(new_operation)
            new_segment = _segment_index(new_operation)
            if (
                old_target_id == new_target_id
                and old_operation.get("new_value") == new_operation.get("old_value")
                and str(old_operation.get("old_value"))
                != str(new_operation.get("new_value"))
                and old_segment != new_segment
            ):
                pairs.append(
                    ConfirmedTransition(
                        target_id=old_target_id,
                        target_name=old_target_name or new_target_name,
                        old_value=str(old_operation["old_value"]),
                        new_value=str(new_operation["new_value"]),
                        old_operation_id=str(old_operation.get("operation_id") or index),
                        new_operation_id=str(
                            new_operation.get("operation_id") or index + 1
                        ),
                        old_segment_index=old_segment,
                        new_segment_index=new_segment,
                    )
                )
    return tuple(pairs)


def _qa_rows(
    raw: Mapping[str, Any],
    *,
    sample_id: str,
    transitions: Sequence[ConfirmedTransition],
) -> tuple[MemOpsQA, ...]:
    rows: list[MemOpsQA] = []
    transition_segments = {
        segment
        for transition in transitions
        for segment in (transition.old_segment_index, transition.new_segment_index)
    }
    answers = raw.get("answer")
    if not isinstance(answers, list):
        raise MemOpsAdapterError("MEMOPS_ANSWER_LIST_MISSING")
    for answer in answers:
        if not isinstance(answer, Mapping):
            raise MemOpsAdapterError("MEMOPS_ANSWER_ROW_INVALID")
        evaluation_type = str(answer.get("evaluation_type") or "")
        if evaluation_type not in QUALIFYING_QA_TYPES:
            continue
        provenance = answer.get("gold_provenance")
        if not isinstance(provenance, list) or not provenance:
            raise MemOpsAdapterError("MEMOPS_GOLD_PROVENANCE_MISSING")
        provenance_rows = tuple(
            dict(row)
            for row in provenance
            if isinstance(row, Mapping)
        )
        if not provenance_rows:
            raise MemOpsAdapterError("MEMOPS_GOLD_PROVENANCE_INVALID")
        provenance_segments = {
            row.get("segment_index")
            for row in provenance_rows
            if isinstance(row.get("segment_index"), int)
        }
        if not provenance_segments & transition_segments:
            continue
        pair_id = str(answer.get("question_pair_id") or "")
        setting = str(answer.get("evaluation_setting") or "")
        question = str(answer.get("question") or "")
        expected = str(answer.get("expected_answer") or "")
        gold_state = str(answer.get("gold_memory_state") or "")
        if not pair_id or not setting or not question or not expected or not gold_state:
            raise MemOpsAdapterError("MEMOPS_QA_REQUIRED_FIELD_MISSING")
        options = answer.get("candidate_options")
        if options is None:
            options_tuple: tuple[str, ...] = ()
        elif isinstance(options, list) and all(isinstance(value, str) and value for value in options):
            options_tuple = tuple(options)
        else:
            raise MemOpsAdapterError("MEMOPS_CANDIDATE_OPTIONS_INVALID")
        rows.append(
            MemOpsQA(
                question_id=f"{sample_id}:{pair_id}:{setting}",
                question_pair_id=pair_id,
                evaluation_setting=setting,
                evaluation_type=evaluation_type,
                question=question,
                candidate_options=options_tuple,
                expected_answer=expected,
                gold_memory_state=gold_state,
                judge_rubric=dict(answer.get("judge_rubric") or {}),
                gold_provenance=provenance_rows,
            )
        )
    if not rows:
        raise MemOpsAdapterError("MEMOPS_NO_MECHANISM_QA")
    return tuple(rows)


def parse_memops_sample(path: Path) -> MemOpsSample:
    raw = _read_json(path)
    sample_id = _sample_id(path)
    operation_type = str(raw.get("operation_type") or "")
    if operation_type not in {"Update", "TrajectoryOps"}:
        raise MemOpsAdapterError("MEMOPS_OPERATION_TYPE_UNSUPPORTED")
    conversations = raw.get("conversations")
    if not isinstance(conversations, list) or not conversations:
        raise MemOpsAdapterError("MEMOPS_CONVERSATIONS_MISSING")
    indices = [
        row.get("segment_index")
        for row in conversations
        if isinstance(row, Mapping)
    ]
    if len(indices) != len(conversations) or any(
        isinstance(value, bool) or not isinstance(value, int) for value in indices
    ):
        raise MemOpsAdapterError("MEMOPS_CONVERSATION_INDEX_INVALID")
    if sorted(indices) != indices or len(set(indices)) != len(indices):
        raise MemOpsAdapterError("MEMOPS_CONVERSATION_ORDER_INVALID")
    operations = raw.get("operations")
    if not isinstance(operations, list):
        raise MemOpsAdapterError("MEMOPS_OPERATIONS_MISSING")
    operation_rows = tuple(
        row for row in operations if isinstance(row, Mapping)
    )
    if len(operation_rows) != len(operations):
        raise MemOpsAdapterError("MEMOPS_OPERATION_ROW_INVALID")
    transitions = _confirmed_update_pairs(operation_rows)
    if not transitions:
        raise MemOpsAdapterError("MEMOPS_NO_CROSS_SEGMENT_CONFIRMED_UPDATE")
    target_ids = {transition.target_id for transition in transitions}
    if len(target_ids) != 1:
        raise MemOpsAdapterError("MEMOPS_MULTIPLE_TARGETS_IN_SAMPLE")
    target_id = next(iter(target_ids))
    target_name = next(
        transition.target_name
        for transition in transitions
        if transition.target_id == target_id
    )
    target_operations = [
        row
        for row in operation_rows
        if _operation_target(row)[0] == target_id
        and row.get("validity") == "confirmed"
        and row.get("new_value") is not None
    ]
    target_operations.sort(key=lambda row: (_segment_index(row), str(row.get("operation_id") or "")))
    if not target_operations:
        raise MemOpsAdapterError("MEMOPS_CURRENT_CONFIRMED_STATE_MISSING")
    latest_confirmed_value = str(target_operations[-1]["new_value"])
    stale_values = tuple(
        dict.fromkeys(
            str(row["new_value"])
            for row in target_operations[:-1]
            if str(row["new_value"]) != latest_confirmed_value
        )
    )
    questions = _qa_rows(raw, sample_id=sample_id, transitions=transitions)
    return MemOpsSample(
        sample_id=sample_id,
        operation_type=operation_type,
        source_file=str(path),
        source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        history_id=f"memops-{sample_id.lower()}-{operation_type.lower()}",
        target_id=target_id,
        target_name=target_name,
        transitions=transitions,
        latest_confirmed_value=latest_confirmed_value,
        stale_confirmed_values=stale_values,
        questions=questions,
        raw=raw,
    )


def discover_memops_samples(memops_root: Path = DEFAULT_MEMOPS_ROOT) -> tuple[Path, ...]:
    root = Path(memops_root) / EVIDENCE_RELATIVE_ROOT
    if not root.is_dir():
        raise MemOpsAdapterError("MEMOPS_EVIDENCE_ROOT_MISSING")
    paths = tuple(sorted(root.glob("*_update.json"))) + tuple(
        sorted(root.glob("*_trajectory_ops.json"))
    )
    if not paths:
        raise MemOpsAdapterError("MEMOPS_EVIDENCE_EMPTY")
    return paths


def select_memops_samples(
    memops_root: Path = DEFAULT_MEMOPS_ROOT,
    *,
    limit: int = 5,
) -> tuple[MemOpsSample, ...]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5:
        raise MemOpsAdapterError("MEMOPS_LIMIT_INVALID")
    candidates: list[MemOpsSample] = []
    for path in discover_memops_samples(memops_root):
        try:
            sample = parse_memops_sample(path)
        except MemOpsAdapterError:
            continue
        # Update files are the least dependent on TrajectoryOps-specific
        # semantics. The tie-break remains fully deterministic and gold-only.
        priority = 0 if sample.operation_type == "Update" else 1
        candidates.append((priority, sample.sample_id, sample))  # type: ignore[arg-type]
    candidates.sort(key=lambda row: (row[0], row[1], row[2].source_file))
    selected = tuple(row[2] for row in candidates[:limit])
    if len(selected) < limit:
        raise MemOpsAdapterError("MEMOPS_ELIGIBLE_SAMPLE_COUNT_INSUFFICIENT")
    return selected


def _dialogue_body(dialogue: Any) -> str:
    if not isinstance(dialogue, list) or not dialogue:
        raise MemOpsAdapterError("MEMOPS_DIALOGUE_INVALID")
    rows: list[str] = []
    for message in dialogue:
        if not isinstance(message, Mapping):
            raise MemOpsAdapterError("MEMOPS_MESSAGE_INVALID")
        role = str(message.get("role") or "").strip().upper()
        content = message.get("content")
        if role not in {"USER", "ASSISTANT"} or not isinstance(content, str):
            raise MemOpsAdapterError("MEMOPS_MESSAGE_FIELDS_INVALID")
        rows.append(f"[{role}] {content}")
    return "\n".join(rows)


def build_episode_inputs(sample: MemOpsSample, namespace: str) -> tuple[EpisodeInput, ...]:
    if not isinstance(sample, MemOpsSample) or not isinstance(namespace, str) or not namespace:
        raise MemOpsAdapterError("MEMOPS_EPISODE_INPUT_INVALID")
    conversations = sample.raw.get("conversations")
    assert isinstance(conversations, list)
    episodes: list[EpisodeInput] = []
    for source_sequence, conversation in enumerate(conversations):
        assert isinstance(conversation, Mapping)
        segment_index = conversation["segment_index"]
        body = _dialogue_body(conversation.get("dialogue"))
        reference_time = (
            REFERENCE_EPOCH + timedelta(minutes=source_sequence)
        ).isoformat().replace("+00:00", "Z")
        source_hash = _sha256(
            {
                "adapter": "sfwb-v1.3-memops",
                "sample_id": sample.sample_id,
                "operation_type": sample.operation_type,
                "segment_index": segment_index,
                "source_sequence": source_sequence,
                "reference_time": reference_time,
                "body": body,
            }
        )
        episodes.append(
            EpisodeInput(
                history_id=sample.history_id,
                session_id=f"{sample.sample_id}:segment:{int(segment_index):03d}",
                source_sequence=source_sequence,
                source_hash=source_hash,
                reference_time=reference_time,
                body=body,
                namespace=namespace,
            )
        )
    return tuple(episodes)


def build_workload_identity(sample: MemOpsSample, episodes: Sequence[EpisodeInput]) -> str:
    if not episodes or any(not isinstance(episode, EpisodeInput) for episode in episodes):
        raise MemOpsAdapterError("MEMOPS_WORKLOAD_EPISODES_INVALID")
    return _sha256(
        {
            "sample_id": sample.sample_id,
            "source_file_sha256": sample.source_sha256,
            "episodes": [
                {
                    "source_sequence": episode.source_sequence,
                    "session_id": episode.session_id,
                    "source_hash": episode.source_hash,
                    "reference_time": episode.reference_time,
                }
                for episode in episodes
            ],
        }
    )


def build_memops_source_record(sample: MemOpsSample, episodes: Sequence[EpisodeInput]) -> dict[str, Any]:
    conversations = sample.raw.get("conversations")
    if not isinstance(conversations, list) or len(conversations) != len(episodes):
        raise MemOpsAdapterError("MEMOPS_QA_SOURCE_INVENTORY_INVALID")
    sessions: list[list[dict[str, str]]] = []
    for conversation in conversations:
        if not isinstance(conversation, Mapping):
            raise MemOpsAdapterError("MEMOPS_QA_SOURCE_CONVERSATION_INVALID")
        dialogue = conversation.get("dialogue")
        if not isinstance(dialogue, list) or not dialogue:
            raise MemOpsAdapterError("MEMOPS_QA_SOURCE_DIALOGUE_INVALID")
        turns: list[dict[str, str]] = []
        for message in dialogue:
            if not isinstance(message, Mapping):
                raise MemOpsAdapterError("MEMOPS_QA_SOURCE_MESSAGE_INVALID")
            role = message.get("role")
            content = message.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str) or not content:
                raise MemOpsAdapterError("MEMOPS_QA_SOURCE_MESSAGE_FIELDS_INVALID")
            turns.append({"role": role, "content": content})
        sessions.append(turns)
    return {
        "haystack_session_ids": [episode.session_id for episode in episodes],
        "haystack_dates": [episode.reference_time for episode in episodes],
        "haystack_sessions": sessions,
        "question_id": sample.history_id,
    }


def build_memops_qa_projection(sample: MemOpsSample, qa: MemOpsQA) -> tuple[dict[str, Any], dict[str, Any]]:
    gold_session_ids = tuple(
        f"{sample.sample_id}:segment:{int(row['segment_index']):03d}"
        for row in qa.gold_provenance
        if isinstance(row.get("segment_index"), int)
    )
    if not gold_session_ids:
        raise MemOpsAdapterError("MEMOPS_QA_GOLD_SESSION_MAPPING_EMPTY")
    public = {
        "question_id": qa.question_id,
        "qa_pair_id": qa.question_pair_id,
        "history_id": sample.history_id,
        "question_type": "MemOps-" + qa.evaluation_type,
        "question_date": (
            REFERENCE_EPOCH + timedelta(minutes=10)
        ).isoformat().replace("+00:00", "Z"),
        "question": qa.public_question,
    }
    private = {
        "reference_answer": qa.expected_answer,
        "gold_session_ids": gold_session_ids,
        "expected_answer": qa.expected_answer,
        "gold_memory_state": qa.gold_memory_state,
        "judge_rubric": dict(qa.judge_rubric),
        "evaluation_type": qa.evaluation_type,
        "evaluation_setting": qa.evaluation_setting,
        "gold_provenance": qa.gold_provenance,
    }
    return public, private


def inspect_current_state(
    sample: MemOpsSample,
    graph: Mapping[str, Any],
) -> dict[str, Any]:
    """Conservative text/effect inspection; never treats UUIDs as semantics."""

    expected = sample.latest_confirmed_value.casefold()
    stale = [value.casefold() for value in sample.stale_confirmed_values]
    observation_time = REFERENCE_EPOCH + timedelta(minutes=10)
    active_edges: list[tuple[Mapping[str, Any], str]] = []
    current_groups: set[tuple[str, str]] = set()
    active_current_mentions: list[str] = []
    active_stale_conflicts: list[dict[str, str]] = []
    active_transition_mentions: list[str] = []
    inactive_mentions: list[str] = []
    entity_text: list[str] = []
    for entity in graph.get("entities", []) if isinstance(graph.get("entities"), list) else []:
        if isinstance(entity, Mapping):
            entity_text.extend(
                str(entity.get(field) or "")
                for field in ("name", "summary", "attributes")
            )
    def edge_time(value: Any) -> datetime | None:
        if value is None or value == "":
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return (
            parsed.replace(tzinfo=timezone.utc)
            if parsed.tzinfo is None
            else parsed.astimezone(timezone.utc)
        )

    for edge in graph.get("edges", []) if isinstance(graph.get("edges"), list) else []:
        if not isinstance(edge, Mapping):
            continue
        text = " ".join(
            str(edge.get(field) or "")
            for field in ("fact", "source_entity_key", "target_entity_key", "attributes")
        )
        valid_at = edge_time(edge.get("valid_at"))
        invalid_at = edge_time(edge.get("invalid_at"))
        expired_at = edge_time(edge.get("expired_at"))
        is_active = (
            (valid_at is None or valid_at <= observation_time)
            and (invalid_at is None or observation_time < invalid_at)
            and (expired_at is None or observation_time < expired_at)
        )
        folded_text = text.casefold()
        current_in_edge = bool(expected and expected in folded_text)
        if current_in_edge:
            if is_active:
                active_current_mentions.append(expected)
                current_groups.add(
                    (
                        str(edge.get("source_entity_key") or "").casefold(),
                        str(edge.get("relation_type") or "").casefold(),
                    )
                )
            else:
                inactive_mentions.append(expected)
        if is_active:
            active_edges.append((edge, folded_text))

    # Only a stale active fact in the same dynamically discovered subject and
    # relation group as a current fact is a current-state conflict. Historical
    # entities and unrelated planned/tentative relations remain inspectable but
    # do not become false current-state failures.
    for edge, folded_text in active_edges:
        group = (
            str(edge.get("source_entity_key") or "").casefold(),
            str(edge.get("relation_type") or "").casefold(),
        )
        if group not in current_groups:
            continue
        current_in_edge = bool(expected and expected in folded_text)
        stale_text = folded_text.replace(expected, " ") if expected else folded_text
        for value in sorted(set(stale), key=lambda item: (-len(item), item)):
            if not value or value not in stale_text:
                continue
            if current_in_edge:
                active_transition_mentions.append(value)
            else:
                active_stale_conflicts.append(
                    {
                        "value": value,
                        "source_entity_key": str(edge.get("source_entity_key") or ""),
                        "relation_type": str(edge.get("relation_type") or ""),
                        "fact": str(edge.get("fact") or ""),
                    }
                )
            stale_text = stale_text.replace(value, " ")
    current_active = bool(active_current_mentions)
    stale_active = sorted({row["value"] for row in active_stale_conflicts})
    status = "PASS" if current_active and not stale_active else "AMBIGUOUS"
    if not current_active:
        status = "FAIL"
    return {
        "status": status,
        "target_id": sample.target_id,
        "target_name": sample.target_name,
        "expected_current_value": sample.latest_confirmed_value,
        "observation_time": observation_time.isoformat().replace("+00:00", "Z"),
        "stale_confirmed_values": list(sample.stale_confirmed_values),
        "current_value_active": current_active,
        "current_value_in_entity_summary": bool(
            expected and expected in "\n".join(entity_text).casefold()
        ),
        "active_current_mentions": len(active_current_mentions),
        "stale_active_mentions": stale_active,
        "active_stale_conflicts": active_stale_conflicts,
        "active_transition_mentions": sorted(set(active_transition_mentions)),
        "inactive_mentions": sorted(set(inactive_mentions)),
        "canonical_graph_used_for_uuid_equality": False,
    }


def sample_manifest_row(sample: MemOpsSample) -> dict[str, Any]:
    return {
        "sample_id": sample.sample_id,
        "operation_type": sample.operation_type,
        "source_file": sample.source_file,
        "source_sha256": sample.source_sha256,
        "history_id": sample.history_id,
        "target_id": sample.target_id,
        "target_name": sample.target_name,
        "transitions": [
            {
                "target_id": transition.target_id,
                "target_name": transition.target_name,
                "old_value": transition.old_value,
                "new_value": transition.new_value,
                "old_operation_id": transition.old_operation_id,
                "new_operation_id": transition.new_operation_id,
                "old_segment_index": transition.old_segment_index,
                "new_segment_index": transition.new_segment_index,
            }
            for transition in sample.transitions
        ],
        "latest_confirmed_value": sample.latest_confirmed_value,
        "stale_confirmed_values": list(sample.stale_confirmed_values),
        "qa": [
            {
                "question_id": qa.question_id,
                "question_pair_id": qa.question_pair_id,
                "evaluation_setting": qa.evaluation_setting,
                "evaluation_type": qa.evaluation_type,
                "expected_answer": qa.expected_answer,
                "gold_memory_state": qa.gold_memory_state,
                "judge_rubric": dict(qa.judge_rubric),
                "gold_provenance": list(qa.gold_provenance),
            }
            for qa in sample.questions
        ],
        "special_semantics_present": sample.special_semantics_present,
    }


__all__ = [
    "DEFAULT_MEMOPS_ROOT",
    "EVIDENCE_RELATIVE_ROOT",
    "MemOpsAdapterError",
    "MemOpsQA",
    "MemOpsSample",
    "build_episode_inputs",
    "build_memops_qa_projection",
    "build_memops_source_record",
    "build_workload_identity",
    "discover_memops_samples",
    "inspect_current_state",
    "parse_memops_sample",
    "sample_manifest_row",
    "select_memops_samples",
]
