"""Dataset freezing and episode rendering for the MemBind validation pilot."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


SCRIPT_VERSION = "membind-validation.dataset.v1"


@dataclass(frozen=True)
class FrozenSplit:
    source_path: str
    source_sha256: str
    filter_script_version: str
    filter_script_sha256: str
    calibration_question_ids: list[str]
    evaluation_question_ids: list[str]


@dataclass(frozen=True)
class Episode:
    question_id: str
    group_id: str
    session_id: str
    source_sequence: int
    source_hash: str
    reference_time: str
    body: str

    @property
    def name(self) -> str:
        return f"{self.question_id}::episode::{self.source_sequence:04d}"

    def to_graphiti_kwargs(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "episode_body": self.body,
            "source_description": "LongMemEval-S haystack session",
            "reference_time": self.reference_time,
            "group_id": self.group_id,
        }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json_records(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if not stripped:
        raise ValueError(f"Dataset is empty: {path}")
    if stripped[0] == "[":
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("Expected a JSON list dataset")
        return data
    records = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError(f"Line {line_no} is not a JSON object")
            records.append(obj)
    return records


def eligible_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = [
        x
        for x in records
        if x.get("question_type") == "knowledge-update"
        and not str(x.get("question_id", "")).endswith("_abs")
    ]
    eligible.sort(key=lambda x: hashlib.sha256(str(x["question_id"]).encode()).hexdigest())
    return eligible


def freeze_split(data_path: str | Path, output_dir: str | Path) -> FrozenSplit:
    data_path = Path(data_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    records = load_json_records(data_path)
    eligible = eligible_records(records)
    source_sha = sha256_file(data_path)
    script_sha = sha256_file(Path(__file__))
    split = FrozenSplit(
        source_path=str(data_path),
        source_sha256=source_sha,
        filter_script_version=SCRIPT_VERSION,
        filter_script_sha256=script_sha,
        calibration_question_ids=[x["question_id"] for x in eligible[:4]],
        evaluation_question_ids=[x["question_id"] for x in eligible[4:12]],
    )

    (output_dir / "source_sha256.txt").write_text(source_sha + "\n", encoding="utf-8")
    (output_dir / "frozen_split.json").write_text(
        json.dumps(asdict(split), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return split


def _message_role(message: Any) -> str:
    if isinstance(message, dict):
        role = str(message.get("role") or message.get("speaker") or message.get("from") or "user")
    elif isinstance(message, (list, tuple)) and message:
        role = str(message[0])
    else:
        role = "user"
    role = role.strip().upper()
    if role in {"HUMAN", "CUSTOMER"}:
        return "USER"
    if role in {"AI", "BOT"}:
        return "ASSISTANT"
    if role not in {"USER", "ASSISTANT", "SYSTEM", "TOOL"}:
        return role or "USER"
    return role


def _message_content(message: Any) -> str:
    if isinstance(message, dict):
        for key in ("content", "text", "message", "value"):
            if key in message and message[key] is not None:
                return str(message[key])
        return json.dumps(message, ensure_ascii=False, sort_keys=True)
    if isinstance(message, (list, tuple)) and len(message) >= 2:
        return str(message[1])
    return str(message)


def _iter_messages(session: Any) -> list[Any]:
    if isinstance(session, dict):
        for key in ("messages", "turns", "conversation"):
            if key in session and isinstance(session[key], list):
                return session[key]
        return [session]
    if isinstance(session, list):
        return session
    return [session]


def render_episode_body(session: Any) -> str:
    lines = []
    for message in _iter_messages(session):
        role = _message_role(message)
        content = _message_content(message).strip()
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


def build_episodes(instance: dict[str, Any]) -> list[Episode]:
    question_id = str(instance["question_id"])
    group_id = str(instance.get("group_id") or question_id)
    sessions = instance.get("haystack_sessions")
    dates = instance.get("haystack_dates")
    if not isinstance(sessions, list) or not isinstance(dates, list):
        raise ValueError(f"{question_id}: missing haystack_sessions or haystack_dates")
    if len(sessions) != len(dates):
        raise ValueError(f"{question_id}: session/date count mismatch")

    session_ids = instance.get("haystack_session_ids") or instance.get("session_ids")
    episodes: list[Episode] = []
    for idx, (session, date) in enumerate(zip(sessions, dates, strict=True)):
        body = render_episode_body(session)
        session_id = str(session_ids[idx]) if isinstance(session_ids, list) and idx < len(session_ids) else str(idx)
        source_hash = sha256_bytes(
            json.dumps(
                {
                    "question_id": question_id,
                    "session_id": session_id,
                    "source_sequence": idx,
                    "reference_time": str(date),
                    "body": body,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        episodes.append(
            Episode(
                question_id=question_id,
                group_id=group_id,
                session_id=session_id,
                source_sequence=idx,
                source_hash=source_hash,
                reference_time=str(date),
                body=body,
            )
        )
    return episodes


def records_by_question_id(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(record["question_id"]): record for record in records}
