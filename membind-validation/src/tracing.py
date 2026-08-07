"""Trace records for run and episode metrics."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def now_ns() -> int:
    return time.monotonic_ns()


def ns_to_ms(ns: int) -> float:
    return ns / 1_000_000


@dataclass
class EpisodeTrace:
    run_id: str
    question_id: str
    method: str
    repeat: int
    source_sequence: int
    arrival_time: int
    queue_enter_time: int | None = None
    compile_start_time: int | None = None
    compile_end_time: int | None = None
    bind_start_time: int | None = None
    bind_end_time: int | None = None
    commit_start_time: int | None = None
    commit_end_time: int | None = None
    publish_time: int | None = None
    add_episode_start: int | None = None
    add_episode_end: int | None = None
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_call_count: int = 0
    embedding_call_count: int = 0
    db_query_count: int = 0
    db_write_count: int = 0
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def derived(self) -> dict[str, float | None]:
        first_work = self.compile_start_time or self.add_episode_start or self.bind_start_time
        return {
            "arrival_to_publish_ms": ns_to_ms(self.publish_time - self.arrival_time) if self.publish_time else None,
            "queue_wait_ms": ns_to_ms(first_work - self.arrival_time) if first_work else None,
            "compile_ms": ns_to_ms(self.compile_end_time - self.compile_start_time)
            if self.compile_start_time and self.compile_end_time
            else None,
            "bind_commit_ms": ns_to_ms(self.publish_time - self.bind_start_time)
            if self.publish_time and self.bind_start_time
            else None,
        }

    def to_json(self) -> dict[str, Any]:
        obj = asdict(self)
        obj.update(self.derived())
        for key in (
            "arrival_time",
            "queue_enter_time",
            "compile_start_time",
            "compile_end_time",
            "bind_start_time",
            "bind_end_time",
            "commit_start_time",
            "commit_end_time",
            "publish_time",
            "add_episode_start",
            "add_episode_end",
        ):
            if obj.get(key) is not None:
                obj[f"{key}_ms"] = ns_to_ms(int(obj[key]))
        return obj


class JsonlTraceWriter:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any] | EpisodeTrace) -> None:
        obj = record.to_json() if isinstance(record, EpisodeTrace) else record
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str) + "\n")
