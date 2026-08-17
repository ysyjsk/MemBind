"""Strict loader for the four-record development graph-quality input.

The live overlay must never open the combined LongMemEval container.  This
module accepts one byte-for-byte pinned artifact containing only the four
already exposed development histories and fails closed on every inventory or
episode-count drift.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256
from .baseline_suite import DEVELOPMENT_HISTORIES


SCHEMA = "membind.paper-eval-v3.development-graph-quality-input.v1"
SOURCE_DATASET_SHA256 = (
    "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
)
DEVELOPMENT_EPISODE_COUNTS = {
    "07741c45": 49,
    "b6019101": 49,
    "6071bd76": 46,
    "a2f3aa27": 44,
}
DEVELOPMENT_GRAPH_QUALITY_INPUT_PATH = (
    Path(__file__).resolve().parents[2]
    / "artifacts/paper_eval/development_inputs/"
    "LONGMEMEVAL_S_DEVELOPMENT_EXPOSED_4.json"
)
# Raw-file identity is intentionally independent from the canonical payload
# hash so whitespace, encoding, or out-of-band rewrites also fail closed.
DEVELOPMENT_GRAPH_QUALITY_INPUT_FILE_SHA256 = (
    "a1e3088193eaf6b866fceb62343ebe09beddc8ad0ed57bc70176232f16b3454b"
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TOP_LEVEL_FIELDS = {
    "schema_version",
    "data_role",
    "source_dataset_sha256",
    "selection_policy",
    "history_order",
    "episode_counts",
    "records",
    "payload_sha256",
}
_REQUIRED_RECORD_FIELDS = {
    "question_id",
    "question",
    "question_date",
    "question_type",
    "answer",
    "answer_session_ids",
    "haystack_session_ids",
    "haystack_dates",
    "haystack_sessions",
}


class DevelopmentGraphQualityInputError(ValueError):
    """The isolated development input is missing, stale, or over-broad."""


def _read_strict_json(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file() or path.is_symlink():
        raise DevelopmentGraphQualityInputError(
            "development input file is missing or unsafe"
        )
    try:
        raw = path.read_bytes()
    except OSError:
        raise DevelopmentGraphQualityInputError(
            "development input file is unreadable"
        ) from None

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DevelopmentGraphQualityInputError(
                    "development input contains a duplicate field"
                )
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeError, json.JSONDecodeError):
        raise DevelopmentGraphQualityInputError(
            "development input is not strict UTF-8 JSON"
        ) from None
    if not isinstance(value, dict):
        raise DevelopmentGraphQualityInputError("development input is invalid")
    return value, hashlib.sha256(raw).hexdigest()


def _nonempty_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise DevelopmentGraphQualityInputError(
            f"development record {field} is invalid"
        )
    return value


def _string_list(
    value: object,
    *,
    field: str,
    expected_count: int | None = None,
) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(child, str) or not child for child in value
    ):
        raise DevelopmentGraphQualityInputError(
            f"development record {field} is invalid"
        )
    if expected_count is not None and len(value) != expected_count:
        raise DevelopmentGraphQualityInputError(
            f"development record episode inventory drift: {field}"
        )
    return list(value)


def _validated_record(
    value: object,
    *,
    expected_history_id: str,
    expected_episode_count: int,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not _REQUIRED_RECORD_FIELDS.issubset(value):
        raise DevelopmentGraphQualityInputError(
            "development record inventory is incomplete"
        )
    record = dict(value)
    if _nonempty_text(record.get("question_id"), field="question_id") != (
        expected_history_id
    ):
        raise DevelopmentGraphQualityInputError(
            "development record history inventory drift"
        )
    for field in ("question", "question_date", "question_type"):
        _nonempty_text(record.get(field), field=field)
    answer = record.get("answer")
    if (
        isinstance(answer, bool)
        or not isinstance(answer, (str, int, float))
        or not str(answer)
    ):
        raise DevelopmentGraphQualityInputError(
            "development record answer is invalid"
        )
    answer_sessions = _string_list(
        record.get("answer_session_ids"), field="answer_session_ids"
    )
    if not answer_sessions or len(set(answer_sessions)) != len(answer_sessions):
        raise DevelopmentGraphQualityInputError(
            "development record answer-session inventory is invalid"
        )
    session_ids = _string_list(
        record.get("haystack_session_ids"),
        field="haystack_session_ids",
        expected_count=expected_episode_count,
    )
    _string_list(
        record.get("haystack_dates"),
        field="haystack_dates",
        expected_count=expected_episode_count,
    )
    sessions = record.get("haystack_sessions")
    if not isinstance(sessions, list) or len(sessions) != expected_episode_count:
        raise DevelopmentGraphQualityInputError(
            "development record episode inventory drift: haystack_sessions"
        )
    if len(set(session_ids)) != len(session_ids) or not set(answer_sessions).issubset(
        session_ids
    ):
        raise DevelopmentGraphQualityInputError(
            "development record session inventory is invalid"
        )
    return record


def load_development_graph_quality_records(
    path: Path = DEVELOPMENT_GRAPH_QUALITY_INPUT_PATH,
    *,
    expected_file_sha256: str = DEVELOPMENT_GRAPH_QUALITY_INPUT_FILE_SHA256,
) -> dict[str, dict[str, Any]]:
    """Load exactly four exposed records from one raw-file-hash-bound artifact."""

    if _SHA256.fullmatch(expected_file_sha256) is None:
        raise DevelopmentGraphQualityInputError(
            "expected development input file SHA256 is invalid"
        )
    artifact, observed_file_sha256 = _read_strict_json(Path(path))
    if observed_file_sha256 != expected_file_sha256:
        raise DevelopmentGraphQualityInputError(
            "development input file SHA256 mismatch"
        )
    if set(artifact) != _TOP_LEVEL_FIELDS or artifact.get("schema_version") != SCHEMA:
        raise DevelopmentGraphQualityInputError(
            "development input schema or field inventory drift"
        )
    observed_payload_sha256 = artifact.get("payload_sha256")
    if observed_payload_sha256 != payload_sha256(
        {key: value for key, value in artifact.items() if key != "payload_sha256"}
    ):
        raise DevelopmentGraphQualityInputError(
            "development input payload hash mismatch"
        )
    if (
        artifact.get("data_role") != "DEVELOPMENT_EXPOSED"
        or artifact.get("source_dataset_sha256") != SOURCE_DATASET_SHA256
        or artifact.get("selection_policy")
        != "EXACT_FROZEN_DEVELOPMENT_HISTORIES_ONLY"
    ):
        raise DevelopmentGraphQualityInputError(
            "development input role or source identity drift"
        )
    order = artifact.get("history_order")
    counts = artifact.get("episode_counts")
    records = artifact.get("records")
    if (
        order != list(DEVELOPMENT_HISTORIES)
        or counts != DEVELOPMENT_EPISODE_COUNTS
        or not isinstance(records, list)
        or len(records) != len(DEVELOPMENT_HISTORIES)
    ):
        raise DevelopmentGraphQualityInputError(
            "development input history or episode inventory drift"
        )
    return {
        history_id: _validated_record(
            record,
            expected_history_id=history_id,
            expected_episode_count=DEVELOPMENT_EPISODE_COUNTS[history_id],
        )
        for history_id, record in zip(DEVELOPMENT_HISTORIES, records, strict=True)
    }


__all__ = [
    "DEVELOPMENT_EPISODE_COUNTS",
    "DEVELOPMENT_GRAPH_QUALITY_INPUT_FILE_SHA256",
    "DEVELOPMENT_GRAPH_QUALITY_INPUT_PATH",
    "DevelopmentGraphQualityInputError",
    "load_development_graph_quality_records",
]
