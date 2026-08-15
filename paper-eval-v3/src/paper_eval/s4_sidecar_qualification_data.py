"""Frozen dataset projection for the remaining S4 qualification histories."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256, sha256_file


SMOKE_HISTORY_ID = "07741c45"
LIVE_HISTORY_IDS = ("b6019101", "6071bd76", "a2f3aa27")
EXPECTED_EPISODE_COUNTS = {
    "b6019101": 49,
    "6071bd76": 46,
    "a2f3aa27": 44,
}
FIXED_CALIBRATION_IDS = (SMOKE_HISTORY_ID, *LIVE_HISTORY_IDS)


@dataclass(frozen=True)
class S4QualificationBlockData:
    """In-memory source binding; raw record and episodes are never serialized."""

    history_id: str
    episode_count: int
    record: dict[str, Any]
    episodes: tuple[Any, ...]
    episode_manifest: dict[str, dict[str, Any]]
    episode_manifest_sha256: str
    dataset_file_sha256: str
    split_file_sha256: str


def _load_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is unreadable: {type(error).__name__}") from None


def _value(value: Any, field: str) -> Any:
    return value.get(field) if isinstance(value, Mapping) else getattr(value, field, None)


def _sha(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} is not a lowercase SHA256")
    return value


def build_s4_qualification_episode_manifest(
    episodes: Sequence[Any],
) -> tuple[dict[str, dict[str, Any]], str]:
    """Build the same hash-only manifest shape consumed by the sidecar projector."""

    if isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence):
        raise ValueError("S4 qualification episodes are malformed")
    manifest: dict[str, dict[str, Any]] = {}
    for expected_sequence, episode in enumerate(episodes):
        sequence = _value(episode, "source_sequence")
        source_hash = _value(episode, "source_hash")
        name = _value(episode, "name")
        body = _value(episode, "body")
        if (
            sequence != expected_sequence
            or not isinstance(name, str)
            or not name
            or name in manifest
            or not isinstance(body, str)
        ):
            raise ValueError("S4 qualification episode projection is not contiguous")
        manifest[name] = {
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "source_hash": _sha(source_hash, field="episode source hash"),
            "source_sequence": expected_sequence,
        }
    if not manifest:
        raise ValueError("S4 qualification episode manifest is empty")
    return manifest, payload_sha256(manifest)


def load_s4_qualification_block(
    *,
    dataset_path: Path,
    split_path: Path,
    history_id: str,
    episode_builder: Callable[[dict[str, Any]], Sequence[Any]],
) -> S4QualificationBlockData:
    """Load exactly one preregistered live block and reject every identity drift."""

    if history_id not in LIVE_HISTORY_IDS:
        raise ValueError("history is outside the S4 fixed-three qualification")
    dataset_file_sha = sha256_file(Path(dataset_path))
    split_file_sha = sha256_file(Path(split_path))
    if dataset_file_sha == "missing" or split_file_sha == "missing":
        raise ValueError("S4 qualification dataset or split is missing")

    split = _load_json(Path(split_path), label="S4 qualification split")
    if not isinstance(split, Mapping):
        raise ValueError("S4 qualification split is not a mapping")
    calibration_ids = split.get("calibration_question_ids")
    if (
        not isinstance(calibration_ids, list)
        or tuple(str(item) for item in calibration_ids) != FIXED_CALIBRATION_IDS
    ):
        raise ValueError("S4 qualification calibration order drift")
    if split.get("source_sha256") != dataset_file_sha:
        raise ValueError("S4 qualification dataset SHA256 drift")

    records = _load_json(Path(dataset_path), label="S4 qualification dataset")
    if not isinstance(records, list):
        raise ValueError("S4 qualification dataset must be a JSON list")
    matches = [
        deepcopy(dict(record))
        for record in records
        if isinstance(record, Mapping)
        and str(record.get("question_id", "")) == history_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"S4 qualification history must occur exactly once; found {len(matches)}"
        )
    record = matches[0]
    if record.get("question_type") != "knowledge-update":
        raise ValueError("S4 qualification history type drift")
    episodes = tuple(episode_builder(deepcopy(record)))
    expected_count = EXPECTED_EPISODE_COUNTS[history_id]
    if len(episodes) != expected_count:
        raise ValueError("S4 qualification episode count drift")
    manifest, manifest_sha = build_s4_qualification_episode_manifest(episodes)
    return S4QualificationBlockData(
        history_id=history_id,
        episode_count=expected_count,
        record=record,
        episodes=episodes,
        episode_manifest=manifest,
        episode_manifest_sha256=manifest_sha,
        dataset_file_sha256=dataset_file_sha,
        split_file_sha256=split_file_sha,
    )
