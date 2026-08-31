"""Frozen development workload adapter for the Phase-3 DVSR observer."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from ..mab_live_runner import MABLiveEpisode


DEV_HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
DEV_COUNTS = {"07741c45": 49, "b6019101": 49, "6071bd76": 46, "a2f3aa27": 44}


class DvsrWorkloadError(ValueError):
    """The sealed development workload cannot be mapped without fabrication."""


def _load_builder(repository_root: Path):
    path = repository_root / "membind-validation/src/dataset.py"
    if not path.is_file() or path.is_symlink():
        raise DvsrWorkloadError("frozen development episode builder is unavailable")
    name = "membind_dvsr_frozen_dataset"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise DvsrWorkloadError("frozen development episode builder is unavailable")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise DvsrWorkloadError("frozen development episode builder failed") from exc
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    builder = getattr(module, "build_episodes", None)
    if not callable(builder):
        raise DvsrWorkloadError("frozen development episode builder is invalid")
    return builder


def load_development_history_episodes(
    *,
    repository_root: Path,
    history_id: str,
    source_count: int | None = None,
    development_input: Path | None = None,
) -> tuple[MABLiveEpisode, ...]:
    """Load one of the four hash-bound development histories only."""

    if history_id not in DEV_HISTORIES:
        raise DvsrWorkloadError("history is outside the development inventory")
    if source_count is not None and (isinstance(source_count, bool) or source_count < 2):
        raise DvsrWorkloadError("source_count must be at least two")
    from paper_eval.development_graph_quality_input import (
        DEVELOPMENT_GRAPH_QUALITY_INPUT_PATH,
        load_development_graph_quality_records,
    )

    records = load_development_graph_quality_records(
        Path(development_input) if development_input is not None else DEVELOPMENT_GRAPH_QUALITY_INPUT_PATH
    )
    record = records.get(history_id)
    expected_count = DEV_COUNTS[history_id]
    if not isinstance(record, dict):
        raise DvsrWorkloadError("history record is missing")
    builder = _load_builder(Path(repository_root).resolve())
    try:
        rendered = tuple(builder(dict(record)))
    except Exception as exc:
        raise DvsrWorkloadError("development episode rendering failed") from exc
    if len(rendered) != expected_count:
        raise DvsrWorkloadError("development episode count drift")
    episodes: list[MABLiveEpisode] = []
    for index, item in enumerate(rendered):
        if int(getattr(item, "source_sequence", -1)) != index:
            raise DvsrWorkloadError("development source sequence is not contiguous")
        episodes.append(
            MABLiveEpisode(
                context_id=history_id,
                source_sequence=index,
                episode_id=f"{history_id}::episode::{index:04d}",
                reference_time=str(getattr(item, "reference_time")),
                body=str(getattr(item, "body")),
                session_id=str(getattr(item, "session_id")),
                source_hash=str(getattr(item, "source_hash")),
            )
        )
    selected = tuple(episodes[:source_count]) if source_count is not None else tuple(episodes)
    if source_count is not None and len(selected) != source_count:
        raise DvsrWorkloadError("requested source prefix is longer than the history")
    return selected


__all__ = ["DEV_COUNTS", "DEV_HISTORIES", "DvsrWorkloadError", "load_development_history_episodes"]
