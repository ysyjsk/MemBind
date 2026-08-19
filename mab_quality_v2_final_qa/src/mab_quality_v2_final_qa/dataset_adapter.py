"""MemoryAgentBench/LongMemEval-shaped source adapter.

The official MAB release stores LongMemEval contexts as a Python-literal string
containing alternating ``Chat Time`` and turn-list values.  Its private
``metadata.haystack_sessions`` is per-question and contains ``has_answer``.
This adapter resolves those sessions to the common context session inventory,
then drops ``has_answer`` before producing the public projection.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .contracts import MABQA, MABContext, MABSession, canonical_sha256


class DatasetMappingError(ValueError):
    """The source cannot be mapped without inventing identity or chronology."""


_CHAT_TIME = re.compile(r"^\s*Chat\s+Time\s*:\s*(.+?)\s*$", re.IGNORECASE)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DatasetMappingError(f"{field} must be an object")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise DatasetMappingError(f"{field} must be a list")
    return list(value)


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DatasetMappingError(f"{field} must be a non-empty string")
    return value.strip()


def _metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    raw = record.get("metadata", {})
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as error:
            raise DatasetMappingError("metadata JSON is invalid") from error
    return dict(_mapping(raw, "metadata"))


def _as_answers(value: Any, index: int) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, str)):
        result: list[str] = []
        for item in value:
            if isinstance(item, Sequence) and not isinstance(item, (bytes, str)):
                result.extend(
                    str(child).strip() for child in item if str(child).strip()
                )
            elif isinstance(item, str) and item.strip():
                result.append(item.strip())
        return tuple(dict.fromkeys(result))
    raise DatasetMappingError(f"answers[{index}] is invalid")


def _turns(value: Any, field: str) -> tuple[dict[str, str], ...]:
    values = _list(value, field)
    if not values:
        raise DatasetMappingError(f"{field} is empty")
    result: list[dict[str, str]] = []
    for turn in values:
        item = _mapping(turn, field)
        role = _nonempty(item.get("role"), f"{field}.role").casefold()
        content = _nonempty(item.get("content"), f"{field}.content")
        if role not in {"user", "assistant"}:
            raise DatasetMappingError(f"{field} contains unsupported role {role!r}")
        result.append({"role": role, "content": content})
    return tuple(result)


def _turn_digest(turns: Sequence[Mapping[str, Any]]) -> str:
    clean = [
        {
            "role": str(turn.get("role", "")).casefold(),
            "content": str(turn.get("content", "")),
        }
        for turn in turns
    ]
    return canonical_sha256(clean)


def _timestamp(value: Any) -> str:
    text = _nonempty(value, "session timestamp")
    match = _CHAT_TIME.match(text)
    return match.group(1).strip() if match else text


def _literal_context(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        raise DatasetMappingError("context is empty")
    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError):
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise DatasetMappingError(
                "context is not a structured MAB session list; chronology cannot be recovered"
            ) from error


def _context_sessions(
    raw_context: Any, *, context_id: str
) -> list[tuple[str, tuple[dict[str, str], ...]]]:
    value = _literal_context(raw_context)
    pairs: list[tuple[Any, Any]] = []
    if isinstance(value, Mapping):
        value = value.get("sessions", value.get("haystack_sessions"))
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise DatasetMappingError("context must contain an ordered session sequence")
    values = list(value)
    if values and all(isinstance(item, Mapping) for item in values):
        for item in values:
            pairs.append(
                (
                    item.get("timestamp", item.get("date", item.get("session_date"))),
                    item.get("turns", item.get("messages", item.get("dialogue"))),
                )
            )
    elif len(values) % 2 == 0 and all(
        isinstance(values[index], str) and isinstance(values[index + 1], Sequence)
        for index in range(0, len(values), 2)
    ):
        pairs = [
            (values[index], values[index + 1]) for index in range(0, len(values), 2)
        ]
    else:
        for item in values:
            if not isinstance(item, Sequence) or len(item) != 2:
                raise DatasetMappingError("context session pairs are malformed")
            pairs.append((item[0], item[1]))
    if not pairs:
        raise DatasetMappingError("context has no sessions")
    result: list[tuple[str, tuple[dict[str, str], ...]]] = []
    for index, (date, turns) in enumerate(pairs):
        if date is None:
            raise DatasetMappingError(
                f"context {context_id} session {index} has no timestamp; refusing fabrication"
            )
        result.append(
            (_timestamp(date), _turns(turns, f"context.sessions[{index}].turns"))
        )
    return result


def _private_session_groups(value: Any) -> list[list[tuple[dict[str, str], ...]]]:
    """Normalize MAB metadata.haystack_sessions (one group per QA)."""

    if value is None:
        return []
    groups = _list(value, "metadata.haystack_sessions")
    normalized: list[list[tuple[dict[str, str], ...]]] = []
    for q_index, group in enumerate(groups):
        sessions = _list(group, f"haystack_sessions[{q_index}]")
        normalized.append([])
        for s_index, session in enumerate(sessions):
            normalized[-1].append(
                _turns(session, f"haystack_sessions[{q_index}][{s_index}]")
            )
    return normalized


def _field_list(
    metadata: Mapping[str, Any], record: Mapping[str, Any], name: str
) -> list[Any]:
    value = record.get(name, metadata.get(name, []))
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return _list(value, name)


class MABDatasetAdapter:
    """Load and qualify MAB records into immutable :class:`MABContext` values."""

    def __init__(self, *, dataset_revision: str = "UNPINNED") -> None:
        self.dataset_revision = _nonempty(dataset_revision, "dataset_revision")
        self._last_manifest: dict[str, Any] | None = None
        self._contexts: tuple[MABContext, ...] = ()

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        source: str | None = None,
        dataset_revision: str = "UNPINNED",
    ) -> MABDatasetAdapter:
        file_path = Path(path)
        try:
            raw = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise DatasetMappingError(
                f"dataset file is unreadable: {file_path}"
            ) from error
        try:
            parsed: Any = json.loads(raw)
        except json.JSONDecodeError:
            records = []
            for line_number, line in enumerate(raw.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise DatasetMappingError(
                        f"invalid JSONL at line {line_number}"
                    ) from error
            parsed = records
        if isinstance(parsed, Mapping) and "data" in parsed:
            parsed = parsed["data"]
        if isinstance(parsed, Mapping):
            parsed = [parsed]
        adapter = cls(dataset_revision=dataset_revision)
        adapter._load_records(parsed, source=source)
        if adapter._last_manifest is not None:
            adapter._last_manifest["dataset_file"] = str(file_path.resolve())
            adapter._last_manifest["dataset_file_sha256"] = hashlib.sha256(
                file_path.read_bytes()
            ).hexdigest()
            adapter._last_manifest.pop("dataset_manifest_sha256", None)
            adapter._last_manifest["dataset_manifest_sha256"] = canonical_sha256(
                adapter._last_manifest
            )
        return adapter

    @classmethod
    def from_records(
        cls,
        records: Sequence[Mapping[str, Any]],
        *,
        source: str | None = None,
        dataset_revision: str = "UNPINNED",
    ) -> MABDatasetAdapter:
        adapter = cls(dataset_revision=dataset_revision)
        adapter._load_records(records, source=source)
        return adapter

    def _load_records(
        self, records: Sequence[Mapping[str, Any]], *, source: str | None = None
    ) -> tuple[MABContext, ...]:
        values = _list(records, "records")
        contexts: list[MABContext] = []
        occurrence: Counter[str] = Counter()
        for row_index, raw in enumerate(values):
            record = _mapping(raw, f"records[{row_index}]")
            metadata = _metadata(record)
            record_source = (
                str(record.get("source", metadata.get("source", "UNKNOWN"))).strip()
                or "UNKNOWN"
            )
            if source and not fnmatch.fnmatch(record_source, source):
                continue
            context_id = str(record.get("context_id", "")).strip()
            if not context_id:
                context_value = record.get("context")
                context_digest = hashlib.sha256(
                    json.dumps(
                        context_value, ensure_ascii=False, sort_keys=True, default=str
                    ).encode("utf-8")
                ).hexdigest()[:16]
                context_id = f"{record_source}:{context_digest}"
            occurrence[context_id] += 1
            if occurrence[context_id] > 1:
                context_id = f"{context_id}:dup{occurrence[context_id] - 1}"
            contexts.append(
                self._adapt_record(record, metadata, context_id, record_source)
            )
        if not contexts:
            raise DatasetMappingError("source filter selected no contexts")
        self._contexts = tuple(contexts)
        self._last_manifest = self._manifest(contexts, source=source)
        return tuple(contexts)

    def _adapt_record(
        self,
        record: Mapping[str, Any],
        metadata: Mapping[str, Any],
        context_id: str,
        record_source: str,
    ) -> MABContext:
        raw_sessions = record.get("sessions", metadata.get("sessions"))
        if raw_sessions is None:
            raw_sessions = record.get("context")
        parsed = _context_sessions(raw_sessions, context_id=context_id)
        sessions: list[MABSession] = []
        for sequence, (timestamp, turns) in enumerate(parsed):
            sessions.append(
                MABSession(
                    session_id=f"{context_id}:s{sequence:04d}",
                    source_sequence=sequence,
                    timestamp=timestamp,
                    turns=turns,
                    source_sha256=_turn_digest(turns),
                )
            )

        questions = _field_list(metadata, record, "questions")
        answers = _field_list(metadata, record, "answers")
        if not answers:
            answers = _field_list(metadata, record, "reference_answers")
        if len(questions) != len(answers) or not questions:
            raise DatasetMappingError("questions/answers inventory is inconsistent")
        q_ids = _field_list(metadata, record, "question_ids")
        pair_ids = _field_list(metadata, record, "qa_pair_ids")
        dates = _field_list(metadata, record, "question_dates")
        types = _field_list(metadata, record, "question_types")
        if not dates:
            dates = _field_list(metadata, record, "question_date")
        if not types:
            types = ["unknown"] * len(questions)
        if len(dates) != len(questions) or len(types) != len(questions):
            raise DatasetMappingError("question date/type inventory is inconsistent")
        if not q_ids:
            q_ids = [f"{context_id}:q{i:04d}" for i in range(len(questions))]
        if not pair_ids:
            pair_ids = list(q_ids)
        if len(q_ids) != len(questions) or len(pair_ids) != len(questions):
            raise DatasetMappingError("question ID inventory is inconsistent")
        groups = _private_session_groups(metadata.get("haystack_sessions"))
        explicit_gold = _field_list(metadata, record, "gold_session_ids")
        qa_items: list[MABQA] = []
        digest_to_sequences: dict[str, list[int]] = {}
        for sequence, session in enumerate(sessions):
            digest_to_sequences.setdefault(session.source_sha256, []).append(sequence)
        for index, question in enumerate(questions):
            if not isinstance(question, str) or not question.strip():
                raise DatasetMappingError(f"questions[{index}] is empty")
            if groups:
                if len(groups) != len(questions):
                    raise DatasetMappingError(
                        "haystack_sessions must align one-to-one with questions"
                    )
                gold_ids: list[str] = []
                used: set[int] = set()
                for private_turns in groups[index]:
                    digest = _turn_digest(private_turns)
                    choices = [
                        seq
                        for seq in digest_to_sequences.get(digest, [])
                        if seq not in used
                    ]
                    if not choices:
                        raise DatasetMappingError(
                            f"question {index} gold session is absent from common context"
                        )
                    sequence = choices[0]
                    used.add(sequence)
                    gold_ids.append(sessions[sequence].session_id)
            elif explicit_gold:
                raw_gold = (
                    explicit_gold[index]
                    if len(explicit_gold) == len(questions)
                    else explicit_gold
                )
                if isinstance(raw_gold, str):
                    gold_ids = [raw_gold]
                else:
                    gold_ids = [
                        str(item)
                        for item in _list(raw_gold, f"gold_session_ids[{index}]")
                    ]
            else:
                raise DatasetMappingError(
                    "no gold session mapping is available; refusing to infer labels from answers"
                )
            answers_for_question = _as_answers(answers[index], index)
            if not answers_for_question:
                raise DatasetMappingError(f"answers[{index}] is empty")
            qa_items.append(
                MABQA(
                    qa_pair_id=_nonempty(pair_ids[index], f"qa_pair_ids[{index}]"),
                    question_id=_nonempty(q_ids[index], f"question_ids[{index}]"),
                    question=question,
                    reference_answers=answers_for_question,
                    question_date=_timestamp(dates[index]),
                    question_type=_nonempty(types[index], f"question_types[{index}]"),
                    gold_session_ids=tuple(gold_ids),
                )
            )
        try:
            return MABContext.create(context_id, sessions, qa_items)
        except ValueError as error:
            raise DatasetMappingError(
                f"context {context_id} contract invalid"
            ) from error

    def _manifest(
        self, contexts: Sequence[MABContext], *, source: str | None
    ) -> dict[str, Any]:
        types = Counter(
            item.question_type for context in contexts for item in context.qa_items
        )
        dates = [
            item.question_date for context in contexts for item in context.qa_items
        ]
        body = {
            "schema_version": "mab-quality-v2-final-qa.dataset-manifest.v1",
            "dataset_revision": self.dataset_revision,
            "source_filter": source,
            "context_count": len(contexts),
            "qa_count": sum(len(context.qa_items) for context in contexts),
            "session_count": sum(len(context.sessions) for context in contexts),
            "qa_count_by_context": {
                context.context_id: len(context.qa_items) for context in contexts
            },
            "session_count_by_context": {
                context.context_id: len(context.sessions) for context in contexts
            },
            "question_type_counts": dict(sorted(types.items())),
            "question_date_available": bool(dates)
            and all(bool(value) for value in dates),
            "session_chronology_available": all(
                bool(session.timestamp) for c in contexts for session in c.sessions
            ),
            "context_ids": [context.context_id for context in contexts],
            "question_inventory_sha256": canonical_sha256(
                [
                    [context.context_id, item.qa_pair_id, item.question_id]
                    for context in contexts
                    for item in context.qa_items
                ]
            ),
            "private_label_inventory_sha256": canonical_sha256(
                [
                    [
                        context.context_id,
                        item.qa_pair_id,
                        item.question_type,
                        list(item.reference_answers),
                        list(item.gold_session_ids),
                    ]
                    for context in contexts
                    for item in context.qa_items
                ]
            ),
        }
        body["dataset_manifest_sha256"] = canonical_sha256(body)
        return body

    @property
    def manifest(self) -> dict[str, Any]:
        if self._last_manifest is None:
            raise DatasetMappingError(
                "from_records must run before manifest is requested"
            )
        return dict(self._last_manifest)

    @property
    def contexts(self) -> tuple[MABContext, ...]:
        if not self._contexts:
            raise DatasetMappingError(
                "from_records must run before contexts are requested"
            )
        return self._contexts


__all__ = ["DatasetMappingError", "MABDatasetAdapter"]
