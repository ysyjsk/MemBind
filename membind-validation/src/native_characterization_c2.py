"""C2/E1 Native Graphiti construction-breakdown runner.

The runner executes the frozen E1/E2 calibration blocks and persists only
sanitized timing/count artifacts.  Raw episode bodies are loaded only for live
Graphiti calls and are never written to trace, checkpoint, or summary files.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import tempfile
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence

from current_state_gate import LiveAction, require_live_action
from dataset import Episode, build_episodes, load_json_records
from graphiti_native import graphiti_episode_kwargs
from native_characterization_instrumentation import (
    install_native_characterization_instrumentation,
)
from native_characterization_c2_measurement import (
    collect_graph_prefix_size,
    install_c2_measurement_adapter,
)
from native_characterization_runtime import build_u0_graphiti_from_env
from native_characterization_tracing import (
    DurableJsonlEnvelopeWriter,
    SpanRecord,
    critical_path_ns,
    exclusive_duration_ns,
    TraceRecorder,
    interval_union_ns,
)


DEFAULT_SOURCE_PATH = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/"
    "longmemeval_s_cleaned.json"
)
SCHEMA_VERSION = "membind.native-characterization-c2-result.v1"
BREAKDOWN_SCHEMA_VERSION = "membind.native-characterization-e1-breakdown.v1"
CHECKPOINT_SCHEMA_VERSION = "membind.native-characterization-c2-checkpoint.v1"
_GRAPH_NAMESPACE_RE = re.compile(r"^nc-e1e2-[0-9a-f]{16}$")
_RUN_ID_RE = re.compile(r"^c2-[0-9a-f]{16}$")
_FORBIDDEN_FIELDS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "cypher",
    "messages",
    "parameters",
    "prompt",
    "raw_prompt",
    "raw_response",
    "response",
    "secret",
    "session_id",
    "token",
}
_COUNTER_PHASES = (
    "add-episode",
    "previous-context",
    "node-extraction",
    "node-resolution",
    "edge-extraction",
    "edge-resolution",
    "candidate-embedding",
    "candidate-search",
    "invalidation-update",
    "attributes-summary",
    "publication",
    "llm",
    "llm-transport",
    "embedding",
    "database",
    "database-transaction",
)
_ROOT_JSONL_VIEWS = (
    "spans.jsonl",
    "llm.jsonl",
    "embedding.jsonl",
    "db.jsonl",
    "events.jsonl",
    "errors.jsonl",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class E1E2Block:
    block_index: int
    history_id: str
    graph_namespace: str
    episode_count: int
    episodes: tuple[dict[str, Any], ...]


class NativeCharacterizationC2Error(RuntimeError):
    """Sanitized C2 failure that avoids source content and secrets."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: str | Path, value: Mapping[str, Any]) -> str:
    _assert_sanitized(value)
    encoded = _canonical_bytes(value) + b"\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return _sha256_bytes(encoded)


def _assert_sanitized(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_FIELDS:
                raise ValueError(f"forbidden C2 artifact field: {key}")
            _assert_sanitized(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _assert_sanitized(child)
        return
    if value is None or isinstance(value, (str, int, bool)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise ValueError("C2 artifact contains a non-JSON scalar")


def _payload_with_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(value))
    _assert_sanitized(payload)
    payload["payload_sha256"] = _sha256_bytes(_canonical_bytes(payload))
    return payload


def _load_json_object(path: str | Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise NativeCharacterizationC2Error(f"{label}_unreadable") from None
    if not isinstance(value, dict):
        raise NativeCharacterizationC2Error(f"{label}_not_object")
    return value


def _resolve_safe_validation_relative_file(
    validation_root: Path,
    supplied: str | Path,
    *,
    error_code: str,
) -> tuple[Path, str]:
    """Resolve a canonical relative file without following symlink components."""

    try:
        raw = os.fspath(supplied)
    except TypeError:
        raise NativeCharacterizationC2Error(error_code) from None
    if not isinstance(raw, str) or not raw or "\\" in raw:
        raise NativeCharacterizationC2Error(error_code)
    pure = PurePosixPath(raw)
    if (
        pure.is_absolute()
        or raw != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise NativeCharacterizationC2Error(error_code)

    candidate = validation_root
    try:
        for part in pure.parts:
            candidate = candidate / part
            if candidate.is_symlink():
                raise NativeCharacterizationC2Error(error_code)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(validation_root)
        if not resolved.is_file():
            raise NativeCharacterizationC2Error(error_code)
    except NativeCharacterizationC2Error:
        raise
    except (OSError, RuntimeError, ValueError):
        raise NativeCharacterizationC2Error(error_code) from None
    return resolved, pure.as_posix()


def _history_map(freeze: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    histories = freeze.get("dataset", {}).get("calibration_histories")
    if not isinstance(histories, list):
        raise NativeCharacterizationC2Error("calibration_histories_missing")
    result: dict[str, Mapping[str, Any]] = {}
    for history in histories:
        if not isinstance(history, Mapping):
            raise NativeCharacterizationC2Error("calibration_history_not_object")
        history_id = str(history.get("history_id", ""))
        if not history_id or history_id in result:
            raise NativeCharacterizationC2Error("calibration_history_id_invalid")
        result[history_id] = history
    return result


def load_e1_e2_blocks(freeze_path: str | Path) -> list[E1E2Block]:
    """Load exactly the four frozen E1/E2 blocks without source episode bodies."""

    freeze = _load_json_object(freeze_path, "freeze")
    e1_e2 = freeze.get("screening", {}).get("e1_e2")
    if not isinstance(e1_e2, Mapping):
        raise NativeCharacterizationC2Error("e1_e2_selection_missing")
    block_order = e1_e2.get("block_order")
    if not isinstance(block_order, list) or len(block_order) != 4:
        raise NativeCharacterizationC2Error("e1_e2_block_order_invalid")
    histories = _history_map(freeze)

    blocks: list[E1E2Block] = []
    seen_indices: set[int] = set()
    seen_namespaces: set[str] = set()
    for expected_index, item in enumerate(block_order):
        if not isinstance(item, Mapping):
            raise NativeCharacterizationC2Error("e1_e2_block_not_object")
        block_index = item.get("block_index")
        history_id = str(item.get("history_id", ""))
        graph_namespace = str(item.get("graph_namespace", ""))
        if block_index != expected_index or block_index in seen_indices:
            raise NativeCharacterizationC2Error("e1_e2_block_index_invalid")
        if history_id not in histories:
            raise NativeCharacterizationC2Error("e1_e2_history_missing")
        if (
            _GRAPH_NAMESPACE_RE.fullmatch(graph_namespace) is None
            or graph_namespace in seen_namespaces
        ):
            raise NativeCharacterizationC2Error("e1_e2_graph_namespace_invalid")
        seen_indices.add(int(block_index))
        seen_namespaces.add(graph_namespace)

        history = histories[history_id]
        raw_episodes = history.get("episodes")
        episode_count = history.get("episode_count")
        if (
            not isinstance(raw_episodes, list)
            or not isinstance(episode_count, int)
            or episode_count != len(raw_episodes)
        ):
            raise NativeCharacterizationC2Error("e1_e2_episode_count_invalid")
        episodes: list[dict[str, Any]] = []
        for episode in raw_episodes:
            if not isinstance(episode, Mapping):
                raise NativeCharacterizationC2Error("e1_e2_episode_not_object")
            source_sequence = episode.get("source_sequence")
            if not isinstance(source_sequence, int):
                raise NativeCharacterizationC2Error("e1_e2_source_sequence_invalid")
            episodes.append(
                {
                    "episode_id": f"{history_id}:{source_sequence}",
                    "history_id": history_id,
                    "source_sequence": source_sequence,
                    "episode_source_sha256": str(
                        episode.get("episode_source_sha256", "")
                    ),
                    "prefix_sha256": str(episode.get("prefix_sha256", "")),
                }
            )
        blocks.append(
            E1E2Block(
                block_index=int(block_index),
                history_id=history_id,
                graph_namespace=graph_namespace,
                episode_count=episode_count,
                episodes=tuple(episodes),
            )
        )
    return blocks


def _runtime_episode_map(
    blocks: Sequence[E1E2Block], source_path: str | Path
) -> dict[tuple[str, int], Episode]:
    wanted = {block.history_id for block in blocks}
    records = {
        str(record.get("question_id")): record
        for record in load_json_records(source_path)
        if isinstance(record, dict) and str(record.get("question_id")) in wanted
    }
    if set(records) != wanted:
        raise NativeCharacterizationC2Error("source_histories_missing")

    expected_hashes = {
        (block.history_id, int(episode["source_sequence"])): str(
            episode["episode_source_sha256"]
        )
        for block in blocks
        for episode in block.episodes
    }
    hydrated: dict[tuple[str, int], Episode] = {}
    for block in blocks:
        episodes = build_episodes(records[block.history_id])
        if len(episodes) != block.episode_count:
            raise NativeCharacterizationC2Error("source_episode_count_mismatch")
        for episode in episodes:
            key = (block.history_id, episode.source_sequence)
            if key not in expected_hashes:
                continue
            if episode.source_hash != expected_hashes[key]:
                raise NativeCharacterizationC2Error("source_episode_hash_mismatch")
            hydrated[key] = replace(episode, group_id=block.graph_namespace)
    if set(hydrated) != set(expected_hashes):
        raise NativeCharacterizationC2Error("source_episode_missing")
    return hydrated


async def _ensure_driver_ready(graphiti: Any) -> None:
    driver = graphiti.driver
    init_task = getattr(driver, "_init_task", None)
    if init_task is not None:
        await init_task
        return
    readiness = getattr(driver, "build_indices_and_constraints", None)
    if callable(readiness):
        await readiness()


async def _add_episode(
    graphiti: Any,
    episode_meta: Mapping[str, Any],
    runtime_episode: Episode | None,
) -> Any:
    if runtime_episode is not None:
        return await graphiti.add_episode(**graphiti_episode_kwargs(runtime_episode))
    return await graphiti.add_episode(dict(episode_meta))


def _records_since(records: Sequence[SpanRecord], offset: int) -> list[SpanRecord]:
    return list(records[offset:])


def _sealed_episode_envelope(
    recorder: TraceRecorder,
    *,
    run_id: str,
    episode_id: str,
    source_sequence: int,
    episode_meta: Mapping[str, Any],
) -> dict[str, Any]:
    envelope = recorder.episode_envelope(run_id, episode_id, source_sequence)
    envelope["episode_source_sha256"] = str(
        episode_meta.get("episode_source_sha256", "")
    )
    envelope["prefix_sha256"] = str(episode_meta.get("prefix_sha256", ""))
    envelope["payload_sha256"] = _sha256_bytes(_canonical_bytes(envelope))
    return envelope


def _view_envelope(envelope: Mapping[str, Any], name: str) -> dict[str, Any]:
    spans = envelope.get("spans")
    if not isinstance(spans, list):
        raise NativeCharacterizationC2Error("trace_spans_invalid")
    if name == "spans.jsonl":
        selected = list(spans)
    elif name == "llm.jsonl":
        selected = [span for span in spans if span.get("phase") in {"llm", "llm-transport"}]
    elif name == "embedding.jsonl":
        selected = [
            span
            for span in spans
            if span.get("phase") in {"embedding", "candidate-embedding"}
        ]
    elif name == "db.jsonl":
        selected = [
            span
            for span in spans
            if span.get("phase") in {"database", "database-transaction"}
        ]
    elif name == "errors.jsonl":
        selected = [span for span in spans if span.get("status") != "ok"]
    elif name == "events.jsonl":
        excluded = {
            "llm",
            "llm-transport",
            "embedding",
            "candidate-embedding",
            "database",
            "database-transaction",
        }
        selected = [span for span in spans if span.get("phase") not in excluded]
    else:
        raise NativeCharacterizationC2Error("artifact_view_invalid")
    result = {
        key: deepcopy(value)
        for key, value in envelope.items()
        if key not in {"spans", "payload_sha256", "schema_version"}
    }
    view = name.removesuffix(".jsonl")
    result["schema_version"] = f"membind.native-characterization-c2-{view}.v1"
    result["spans"] = selected
    result["payload_sha256"] = _sha256_bytes(_canonical_bytes(result))
    return result


def _persist_episode_envelope(
    *,
    block_writer: DurableJsonlEnvelopeWriter,
    root_writers: Mapping[str, DurableJsonlEnvelopeWriter],
    envelope: Mapping[str, Any],
) -> None:
    block_writer.write(envelope)
    for name in _ROOT_JSONL_VIEWS:
        root_writers[name].write(_view_envelope(envelope, name))


def _artifact_inventory(run_root: Path) -> tuple[dict[str, str], dict[str, Any]]:
    hashes: dict[str, str] = {}
    inventory: dict[str, Any] = {}
    for path in sorted(
        item
        for item in run_root.rglob("*")
        if item.is_file() and item.name != "manifest.json"
    ):
        relative = path.relative_to(run_root).as_posix()
        raw = path.read_bytes()
        digest = _sha256_bytes(raw)
        hashes[relative] = digest
        inventory[relative] = {
            "sha256": digest,
            "byte_count": len(raw),
            "line_count": raw.count(b"\n") if path.suffix == ".jsonl" else None,
        }
    return hashes, inventory


def _run_provenance(
    *,
    validation_root: Path,
    freeze_path: str,
    freeze_sha256: str,
    freeze_payload: Mapping[str, Any],
    run_id: str,
) -> dict[str, Any]:
    source_root = Path(__file__).resolve().parent
    phase_map = validation_root / "artifacts/native_characterization/phase_map.json"
    return {
        "creation_command": (
            ".venv/bin/python src/native_characterization_c2.py --live "
            f"--run-id {run_id}"
        ),
        "freeze_path": freeze_path,
        "freeze_sha256": freeze_sha256,
        "phase_map_sha256": _sha256_file(phase_map) if phase_map.is_file() else None,
        "c2_runner_source_sha256": _sha256_file(Path(__file__)),
        "measurement_adapter_source_sha256": _sha256_file(
            source_root / "native_characterization_c2_measurement.py"
        ),
        "base_instrumentation_source_sha256": _sha256_file(
            source_root / "native_characterization_instrumentation.py"
        ),
        "tracing_source_sha256": _sha256_file(
            source_root / "native_characterization_tracing.py"
        ),
        "frozen_input_hashes": deepcopy(dict(freeze_payload.get("input_hashes", {}))),
        "dataset_source_sha256": freeze_payload.get("dataset", {}).get(
            "source_sha256"
        ),
        "sanitized_runtime_identity": deepcopy(
            dict(freeze_payload.get("runtime_identities", {}))
        ),
    }


def _record_from_dict(value: Mapping[str, Any]) -> SpanRecord:
    return SpanRecord(
        int(value["sequence"]),
        str(value["span_id"]),
        value.get("parent_span_id"),
        str(value["run_id"]),
        str(value["episode_id"]),
        int(value["source_sequence"]),
        str(value["phase"]),
        (
            None
            if value.get("operation_class") is None
            else str(value.get("operation_class"))
        ),
        int(value["start_ns"]),
        int(value["end_ns"]),
        str(value["status"]),
        None if value.get("error_code") is None else str(value.get("error_code")),
        dict(value.get("metadata", {})),
    )


def _load_block_records(trace_path: str | Path) -> list[SpanRecord]:
    records: list[SpanRecord] = []
    try:
        lines = Path(trace_path).read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError):
        raise NativeCharacterizationC2Error("trace_unreadable") from None
    for line in lines:
        if not line.strip():
            continue
        envelope = json.loads(line)
        spans = envelope.get("spans")
        if not isinstance(spans, list):
            raise NativeCharacterizationC2Error("trace_spans_invalid")
        records.extend(_record_from_dict(span) for span in spans)
    return records


def _safe_phase_summary(records: Sequence[SpanRecord]) -> dict[str, Any]:
    total_root_ns = interval_union_ns(
        (record.start_ns, record.end_ns)
        for record in records
        if record.phase == "add-episode"
    )
    if total_root_ns <= 0:
        total_root_ns = interval_union_ns(
            (record.start_ns, record.end_ns) for record in records
        )
    occupancy: dict[str, Any] = {}
    for phase in _COUNTER_PHASES:
        union_ns = interval_union_ns(
            (record.start_ns, record.end_ns)
            for record in records
            if record.phase == phase
        )
        if union_ns or phase in {
            "llm",
            "embedding",
            "database",
            "candidate-embedding",
            "candidate-search",
            "invalidation-update",
        }:
            occupancy[phase] = {
                "union_ns": union_ns,
                "span_count": sum(1 for record in records if record.phase == phase),
                "occupancy_fraction": (
                    union_ns / total_root_ns if total_root_ns > 0 else 0.0
                ),
            }
    return {
        "total_add_episode_union_ns": total_root_ns,
        "phase_occupancy": occupancy,
    }


def _counter_summary(records: Sequence[SpanRecord]) -> dict[str, int]:
    llm_records = [
        record
        for record in records
        if record.phase == "llm" and record.operation_class == "logical-call"
    ]
    embedding_records = [record for record in records if record.phase == "embedding"]
    database_records = [record for record in records if record.phase == "database"]
    transaction_records = [
        record for record in records if record.phase == "database-transaction"
    ]
    candidate_embedding_records = [
        record for record in records if record.phase == "candidate-embedding"
    ]
    candidate_search_records = [
        record for record in records if record.phase == "candidate-search"
    ]
    invalidation_records = [
        record for record in records if record.phase == "invalidation-update"
    ]
    return {
        "llm_logical_call_count": len(llm_records),
        "llm_transport_attempt_count": sum(
            1 for record in records if record.phase == "llm-transport"
        ),
        "llm_transport_error_count": sum(
            record.status == "error"
            for record in records
            if record.phase == "llm-transport"
        ),
        "llm_input_tokens": sum(
            int(record.metadata.get("input_tokens", 0)) for record in llm_records
        ),
        "llm_output_tokens": sum(
            int(record.metadata.get("output_tokens", 0)) for record in llm_records
        ),
        "llm_retry_count": sum(
            int(record.metadata.get("retry_count", 0) or 0) for record in llm_records
        ),
        "llm_error_count": sum(record.status == "error" for record in llm_records),
        "embedding_call_count": len(embedding_records),
        "embedding_text_count": sum(
            int(record.metadata.get("text_count", 0)) for record in embedding_records
        ),
        "embedding_dimension_observed": max(
            [int(record.metadata.get("dimension", 0)) for record in embedding_records]
            or [0]
        ),
        "embedding_error_count": sum(
            record.status == "error" for record in embedding_records
        ),
        "db_query_count": sum(
            1 for record in database_records if record.operation_class == "query"
        ),
        "db_write_count": sum(
            1 for record in database_records if record.operation_class == "write"
        ),
        "db_transaction_count": len(transaction_records),
        "db_error_count": sum(record.status == "error" for record in database_records),
        "candidate_embedding_call_count": len(candidate_embedding_records),
        "candidate_embedding_text_count": sum(
            int(record.metadata.get("text_count", 0) or 0)
            for record in candidate_embedding_records
        ),
        "candidate_search_call_count": len(candidate_search_records),
        "candidate_query_count": sum(
            int(record.metadata.get("candidate_query_count", 0) or 0)
            for record in candidate_search_records
        ),
        "candidate_count": sum(
            int(record.metadata.get("candidate_count", 0) or 0)
            for record in candidate_search_records
        ),
        "invalidation_span_count": len(invalidation_records),
        "invalidated_existing_edge_count": sum(
            int(record.metadata.get("invalidated_count", 0) or 0)
            for record in invalidation_records
            if record.operation_class == "existing-edge-mutation"
        ),
        "new_edge_expired_count": sum(
            int(record.metadata.get("new_edge_expired_count", 0) or 0)
            for record in invalidation_records
        ),
    }


_HARD_TELEMETRY_FIELDS = (
    "phase_boundaries",
    "llm_telemetry",
    "llm_transport",
    "embedding_telemetry",
    "database_telemetry",
    "database_transaction",
    "candidate_counts",
    "candidate_embedding",
    "candidate_search",
    "invalidation_update",
    "graph_prefix_size",
)

_REQUIRED_BASE_PHASES = frozenset(
    {
        "previous-context",
        "node-extraction",
        "node-resolution",
        "edge-extraction",
        "edge-resolution",
        "attributes-summary",
        "publication",
    }
)
_DATABASE_OPERATION_CLASSES = frozenset({"query", "write"})
_TRANSACTION_OPERATION_CLASSES = frozenset({"query", "write", "transaction"})


def _percentile(values: Sequence[int], probability: float) -> float | int:
    """Deterministic descriptive percentile without treating episodes as replicas."""

    ordered = sorted(int(value) for value in values)
    if not ordered:
        return 0
    if probability <= 0:
        return ordered[0]
    if probability >= 1:
        return ordered[-1]
    # Nearest-rank keeps small bounded screening groups on an observed value
    # instead of inventing an interpolated latency between episodes.
    rank = max(1, math.ceil(probability * len(ordered)))
    return ordered[rank - 1]


def _distribution(values: Sequence[int]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "median": 0, "p95": 0}
    ordered = sorted(int(value) for value in values)
    middle = len(ordered) // 2
    median: float | int
    if len(ordered) % 2:
        median = ordered[middle]
    else:
        median = (ordered[middle - 1] + ordered[middle]) / 2
    return {
        "count": len(ordered),
        "median": median,
        "p95": _percentile(ordered, 0.95),
    }


def _aggregate_episode_work_volume(
    episode_metrics: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    numeric_fields = (
        "llm_logical_call_count",
        "llm_transport_attempt_count",
        "llm_transport_error_count",
        "llm_retry_count",
        "llm_error_count",
        "llm_input_tokens",
        "llm_output_tokens",
        "embedding_call_count",
        "embedding_text_count",
        "embedding_error_count",
        "db_query_count",
        "db_write_count",
        "db_error_count",
        "db_transaction_count",
        "candidate_embedding_call_count",
        "candidate_embedding_text_count",
        "candidate_search_call_count",
        "candidate_query_count",
        "candidate_count",
        "invalidation_span_count",
        "invalidation_candidate_count",
        "invalidated_existing_edge_count",
        "new_edge_expired_count",
    )
    result: dict[str, Any] = {
        name: sum(
            int(item["work_volume"].get(name, 0) or 0)
            for item in episode_metrics
        )
        for name in numeric_fields
    }
    result["embedding_dimension_observed"] = max(
        [
            int(item["work_volume"].get("embedding_dimension_observed", 0) or 0)
            for item in episode_metrics
        ]
        or [0]
    )
    for name in (
        "prompt_names",
        "llm_statuses",
        "llm_transport_statuses",
        "embedding_statuses",
        "db_statuses",
    ):
        labels = {
            label
            for item in episode_metrics
            for label in item["work_volume"].get(name, {})
        }
        result[name] = {
            label: sum(
                int(item["work_volume"].get(name, {}).get(label, 0) or 0)
                for item in episode_metrics
            )
            for label in sorted(labels)
        }
    return result


def _span_children(records: Sequence[SpanRecord]) -> dict[str, list[SpanRecord]]:
    children: dict[str, list[SpanRecord]] = {}
    for record in records:
        if record.parent_span_id is not None:
            children.setdefault(record.parent_span_id, []).append(record)
    return children


def _metadata_is_nonnegative_count(record: SpanRecord, name: str) -> bool:
    value = record.metadata.get(name)
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _episode_analysis(
    records: Sequence[SpanRecord],
    episode_meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    roots = [record for record in records if record.phase == "add-episode"]
    if len(roots) != 1:
        raise NativeCharacterizationC2Error("measurement_contract_root_missing")
    root = roots[0]
    children = _span_children(records)
    by_phase: dict[str, list[SpanRecord]] = {}
    for record in records:
        by_phase.setdefault(record.phase, []).append(record)

    inclusive: dict[str, int] = {}
    exclusive: dict[str, int] = {}
    interval_union: dict[str, int] = {}
    for phase, phase_records in by_phase.items():
        inclusive[phase] = sum(
            max(0, int(record.end_ns) - int(record.start_ns))
            for record in phase_records
        )
        exclusive[phase] = sum(
            exclusive_duration_ns(
                (record.start_ns, record.end_ns),
                [
                    (child.start_ns, child.end_ns)
                    for child in children.get(record.span_id, [])
                ],
            )
            for record in phase_records
        )
        interval_union[phase] = interval_union_ns(
            (record.start_ns, record.end_ns) for record in phase_records
        )

    llm_records = by_phase.get("llm", [])
    transport_records = by_phase.get("llm-transport", [])
    embedding_records = by_phase.get("embedding", [])
    db_records = by_phase.get("database", [])
    transaction_records = by_phase.get("database-transaction", [])
    candidate_embedding_records = by_phase.get("candidate-embedding", [])
    candidate_search_records = by_phase.get("candidate-search", [])
    invalidation_records = by_phase.get("invalidation-update", [])
    graph_prefix_records = by_phase.get("graph-prefix-snapshot", [])
    prompt_names: dict[str, int] = {}
    for record in llm_records:
        prompt_name = record.metadata.get("prompt_name")
        if isinstance(prompt_name, str) and prompt_name:
            prompt_names[prompt_name] = prompt_names.get(prompt_name, 0) + 1
    llm_statuses: dict[str, int] = {}
    for record in llm_records:
        llm_statuses[record.status] = llm_statuses.get(record.status, 0) + 1
    llm_transport_statuses: dict[str, int] = {}
    for record in transport_records:
        llm_transport_statuses[record.status] = (
            llm_transport_statuses.get(record.status, 0) + 1
        )
    embedding_statuses: dict[str, int] = {}
    for record in embedding_records:
        embedding_statuses[record.status] = embedding_statuses.get(record.status, 0) + 1
    db_statuses: dict[str, int] = {}
    for record in db_records:
        db_statuses[record.status] = db_statuses.get(record.status, 0) + 1

    missing: list[str] = []
    phase_names = set(by_phase)
    if not _REQUIRED_BASE_PHASES.issubset(phase_names):
        missing.append("phase_boundaries")
    if not llm_records or not all(
        record.operation_class == "logical-call"
        and isinstance(record.metadata.get("prompt_name"), str)
        and bool(record.metadata.get("prompt_name"))
        and _metadata_is_nonnegative_count(record, "retry_count")
        and _metadata_is_nonnegative_count(record, "input_tokens")
        and _metadata_is_nonnegative_count(record, "output_tokens")
        for record in llm_records
    ):
        missing.append("llm_telemetry")
    if not transport_records or not all(
        record.operation_class == "request-attempt"
        and _metadata_is_nonnegative_count(record, "attempt_index")
        for record in transport_records
    ):
        missing.append("llm_transport")
    if not embedding_records or not all(
        _metadata_is_nonnegative_count(record, "text_count")
        and (
            record.status != "ok"
            or _metadata_is_nonnegative_count(record, "dimension")
        )
        for record in embedding_records
    ):
        missing.append("embedding_telemetry")
    if not db_records or not all(
        record.operation_class in _DATABASE_OPERATION_CLASSES
        for record in db_records
    ):
        missing.append("database_telemetry")
    if not transaction_records or not all(
        record.operation_class in _TRANSACTION_OPERATION_CLASSES
        and isinstance(record.metadata.get("transaction_id"), str)
        and bool(record.metadata.get("transaction_id"))
        for record in transaction_records
    ):
        missing.append("database_transaction")
    if not candidate_search_records or not all(
        _metadata_is_nonnegative_count(record, "candidate_count")
        and _metadata_is_nonnegative_count(record, "candidate_query_count")
        for record in candidate_search_records
    ):
        missing.append("candidate_counts")
    if not candidate_embedding_records or not all(
        _metadata_is_nonnegative_count(record, "text_count")
        for record in candidate_embedding_records
    ):
        missing.append("candidate_embedding")
    if not candidate_search_records:
        missing.append("candidate_search")
    if not invalidation_records or not all(
        _metadata_is_nonnegative_count(record, "invalidation_candidate_count")
        and (
            _metadata_is_nonnegative_count(record, "invalidated_count")
            or _metadata_is_nonnegative_count(record, "invalidated_result_count")
        )
        and _metadata_is_nonnegative_count(record, "new_edge_expired_count")
        for record in invalidation_records
    ):
        missing.append("invalidation_update")
    if len(graph_prefix_records) != 1 or not all(
        _metadata_is_nonnegative_count(record, "graph_prefix_node_count")
        and _metadata_is_nonnegative_count(
            record, "graph_prefix_relationship_count"
        )
        for record in graph_prefix_records
    ):
        missing.append("graph_prefix_size")
    source_sha = (
        episode_meta.get("episode_source_sha256")
        if isinstance(episode_meta, Mapping)
        else None
    )
    prefix_sha = (
        episode_meta.get("prefix_sha256")
        if isinstance(episode_meta, Mapping)
        else None
    )
    if not _valid_sha256(source_sha) or not _valid_sha256(prefix_sha):
        missing.append("source_attribution")
    if not by_phase.get("publication"):
        missing.append("publication_boundary")
    if root.status != "ok":
        missing.append("root_status")
    if isinstance(episode_meta, Mapping) and (
        root.episode_id != str(episode_meta.get("episode_id", ""))
        or root.source_sequence != episode_meta.get("source_sequence")
    ):
        missing.append("source_identity")
    span_ids = {record.span_id for record in records}
    if len(span_ids) != len(records) or any(
        record.parent_span_id is not None and record.parent_span_id not in span_ids
        for record in records
    ):
        missing.append("span_parentage")

    graph_prefix = graph_prefix_records[0].metadata if len(graph_prefix_records) == 1 else {}
    invalidated_existing_count = sum(
        int(record.metadata.get("invalidated_count", 0) or 0)
        for record in invalidation_records
        if record.operation_class == "existing-edge-mutation"
    )

    return {
        "episode_id": root.episode_id,
        "source_sequence": root.source_sequence,
        "episode_source_sha256": (
            str(episode_meta.get("episode_source_sha256"))
            if isinstance(episode_meta, Mapping)
            else None
        ),
        "prefix_sha256": (
            str(episode_meta.get("prefix_sha256"))
            if isinstance(episode_meta, Mapping)
            else None
        ),
        "service_latency_ns": max(0, root.end_ns - root.start_ns),
        "publication_latency_ns": interval_union.get("publication", 0),
        "graph_prefix_size": {
            "node_count": int(graph_prefix.get("graph_prefix_node_count", 0) or 0),
            "relationship_count": int(
                graph_prefix.get("graph_prefix_relationship_count", 0) or 0
            ),
        },
        "accounting": {
            "inclusive_ns": inclusive.get("add-episode", 0),
            "exclusive_ns": exclusive.get("add-episode", 0),
            "interval_union_ns": interval_union.get("add-episode", 0),
            "sum_of_work_ns": sum(
                duration
                for phase, duration in inclusive.items()
                if phase != "graph-prefix-snapshot"
            ),
            "critical_path_ns": critical_path_ns(root.span_id, records),
            "inclusive_by_phase_ns": dict(sorted(inclusive.items())),
            "exclusive_by_phase_ns": dict(sorted(exclusive.items())),
            "interval_union_by_phase_ns": dict(sorted(interval_union.items())),
        },
        "work_volume": {
            "llm_logical_call_count": len(llm_records),
            "llm_transport_attempt_count": len(transport_records),
            "llm_transport_error_count": sum(
                1 for record in transport_records if record.status == "error"
            ),
            "llm_retry_count": sum(
                int(record.metadata.get("retry_count", 0) or 0)
                for record in llm_records
            ),
            "llm_error_count": sum(1 for record in llm_records if record.status == "error"),
            "llm_input_tokens": sum(
                int(record.metadata.get("input_tokens", 0) or 0)
                for record in llm_records
            ),
            "llm_output_tokens": sum(
                int(record.metadata.get("output_tokens", 0) or 0)
                for record in llm_records
            ),
            "prompt_names": dict(sorted(prompt_names.items())),
            "llm_statuses": dict(sorted(llm_statuses.items())),
            "llm_transport_statuses": dict(
                sorted(llm_transport_statuses.items())
            ),
            "embedding_call_count": len(embedding_records),
            "embedding_text_count": sum(
                int(record.metadata.get("text_count", 0) or 0)
                for record in embedding_records
            ),
            "embedding_error_count": sum(
                1 for record in embedding_records if record.status == "error"
            ),
            "embedding_statuses": dict(sorted(embedding_statuses.items())),
            "embedding_dimension_observed": max(
                [int(record.metadata.get("dimension", 0) or 0) for record in embedding_records]
                or [0]
            ),
            "db_query_count": sum(
                1 for record in db_records if record.operation_class == "query"
            ),
            "db_write_count": sum(
                1 for record in db_records if record.operation_class == "write"
            ),
            "db_error_count": sum(1 for record in db_records if record.status == "error"),
            "db_statuses": dict(sorted(db_statuses.items())),
            "db_transaction_count": len(transaction_records),
            "candidate_embedding_call_count": len(candidate_embedding_records),
            "candidate_embedding_text_count": sum(
                int(record.metadata.get("text_count", 0) or 0)
                for record in candidate_embedding_records
            ),
            "candidate_search_call_count": len(candidate_search_records),
            "candidate_query_count": sum(
                int(record.metadata.get("candidate_query_count", 0) or 0)
                for record in candidate_search_records
            ),
            "candidate_count": sum(
                int(record.metadata.get("candidate_count", 0) or 0)
                for record in candidate_search_records
            ),
            "invalidation_span_count": len(invalidation_records),
            "invalidation_candidate_count": sum(
                int(record.metadata.get("invalidation_candidate_count", 0) or 0)
                for record in invalidation_records
                if record.operation_class == "existing-edge-mutation"
            ),
            "invalidated_existing_edge_count": invalidated_existing_count,
            "new_edge_expired_count": sum(
                int(record.metadata.get("new_edge_expired_count", 0) or 0)
                for record in invalidation_records
            ),
        },
        "telemetry_completeness": {
            "status": (
                "complete" if not missing else "incomplete_missing_required_fields"
            ),
            "missing_required_fields": sorted(set(missing)),
            "captured_phase_names": sorted(phase_names),
        },
    }


def analyze_e1_breakdown(
    *,
    run_id: str,
    blocks: Sequence[tuple[E1E2Block, Sequence[SpanRecord]]],
    freeze_sha256: str,
) -> dict[str, Any]:
    """Summarize C2 traces using interval-union phase occupancy."""

    block_summaries: list[dict[str, Any]] = []
    aggregate = {
        "episode_count": 0,
        "span_count": 0,
        "total_add_episode_union_ns": 0,
        "llm_logical_call_count": 0,
        "llm_transport_attempt_count": 0,
        "llm_transport_error_count": 0,
        "llm_input_tokens": 0,
        "llm_output_tokens": 0,
        "llm_retry_count": 0,
        "llm_error_count": 0,
        "embedding_call_count": 0,
        "embedding_text_count": 0,
        "embedding_dimension_observed": 0,
        "embedding_error_count": 0,
        "db_query_count": 0,
        "db_write_count": 0,
        "db_transaction_count": 0,
        "db_error_count": 0,
        "candidate_embedding_call_count": 0,
        "candidate_embedding_text_count": 0,
        "candidate_search_call_count": 0,
        "candidate_query_count": 0,
        "candidate_count": 0,
        "invalidation_span_count": 0,
        "invalidated_existing_edge_count": 0,
        "new_edge_expired_count": 0,
    }
    aggregate_phase_union: dict[str, int] = {}

    for block, records in blocks:
        ordered = sorted(records, key=lambda record: record.sequence)
        phases = _safe_phase_summary(ordered)
        counters = _counter_summary(ordered)
        total_ns = int(phases["total_add_episode_union_ns"])
        by_episode: dict[str, list[SpanRecord]] = {}
        for record in ordered:
            by_episode.setdefault(record.episode_id, []).append(record)
        episode_metadata = {
            str(episode.get("episode_id")): episode for episode in block.episodes
        }
        observed_episode_ids = set(by_episode)
        planned_episode_ids = set(episode_metadata)
        episode_metrics = [
            _episode_analysis(by_episode[episode_id], episode_metadata.get(episode_id))
            for episode_id in sorted(by_episode, key=lambda value: (
                episode_metadata.get(value, {}).get("source_sequence", 0), value
            ))
        ]
        service_latencies = [
            int(item["service_latency_ns"]) for item in episode_metrics
        ]
        publication_latencies = [
            int(item["publication_latency_ns"]) for item in episode_metrics
        ]
        accounting = {
            "inclusive_ns": sum(
                int(item["accounting"]["inclusive_ns"]) for item in episode_metrics
            ),
            "exclusive_ns": sum(
                int(item["accounting"]["exclusive_ns"]) for item in episode_metrics
            ),
            "interval_union_ns": sum(
                int(item["accounting"]["interval_union_ns"])
                for item in episode_metrics
            ),
            "sum_of_work_ns": sum(
                int(item["accounting"]["sum_of_work_ns"]) for item in episode_metrics
            ),
            "critical_path_ns": sum(
                int(item["accounting"]["critical_path_ns"])
                for item in episode_metrics
            ),
        }
        missing_fields = sorted(
            {
                field
                for item in episode_metrics
                for field in item["telemetry_completeness"][
                    "missing_required_fields"
                ]
            }
        )
        if observed_episode_ids != planned_episode_ids:
            missing_fields = sorted(set(missing_fields) | {"episode_set"})
        block_summaries.append(
            {
                "block_index": block.block_index,
                "history_id": block.history_id,
                "graph_namespace": block.graph_namespace,
                "episode_count": block.episode_count,
                "span_count": len(ordered),
                "observed_episode_count": len(episode_metrics),
                "total_add_episode_union_ns": total_ns,
                "phase_occupancy": phases["phase_occupancy"],
                "counters": counters,
                "episode_metrics": episode_metrics,
                "accounting": accounting,
                "distributions": {
                    "service_latency_ns": _distribution(service_latencies),
                    "publication_latency_ns": _distribution(publication_latencies),
                },
                "work_volume": _aggregate_episode_work_volume(episode_metrics),
                "telemetry_completeness": {
                    "status": (
                        "complete"
                        if not missing_fields
                        else "incomplete_missing_required_fields"
                    ),
                    "missing_required_fields": missing_fields,
                },
            }
        )
        aggregate["episode_count"] += block.episode_count
        aggregate["span_count"] += len(ordered)
        aggregate["total_add_episode_union_ns"] += total_ns
        for name, value in counters.items():
            if name == "embedding_dimension_observed":
                aggregate[name] = max(int(aggregate[name]), int(value))
            else:
                aggregate[name] += int(value)
        for phase, phase_summary in phases["phase_occupancy"].items():
            aggregate_phase_union[phase] = aggregate_phase_union.get(phase, 0) + int(
                phase_summary["union_ns"]
            )

    total = int(aggregate["total_add_episode_union_ns"])
    aggregate_phase_occupancy = {
        phase: {
            "union_ns": union_ns,
            "occupancy_fraction": union_ns / total if total > 0 else 0.0,
        }
        for phase, union_ns in sorted(aggregate_phase_union.items())
    }
    all_episode_metrics = [
        episode
        for block_summary in block_summaries
        for episode in block_summary["episode_metrics"]
    ]
    aggregate_missing_fields = sorted(
        {
            field
            for block_summary in block_summaries
            for field in block_summary["telemetry_completeness"][
                "missing_required_fields"
            ]
        }
    )
    aggregate["observed_episode_count"] = len(all_episode_metrics)
    aggregate["accounting"] = {
        "inclusive_ns": sum(
            int(item["accounting"]["inclusive_ns"]) for item in all_episode_metrics
        ),
        "exclusive_ns": sum(
            int(item["accounting"]["exclusive_ns"]) for item in all_episode_metrics
        ),
        "interval_union_ns": sum(
            int(item["accounting"]["interval_union_ns"])
            for item in all_episode_metrics
        ),
        "sum_of_work_ns": sum(
            int(item["accounting"]["sum_of_work_ns"]) for item in all_episode_metrics
        ),
        "critical_path_ns": sum(
            int(item["accounting"]["critical_path_ns"])
            for item in all_episode_metrics
        ),
    }
    aggregate["distributions"] = {
        "service_latency_ns": _distribution(
            [int(item["service_latency_ns"]) for item in all_episode_metrics]
        ),
        "publication_latency_ns": _distribution(
            [int(item["publication_latency_ns"]) for item in all_episode_metrics]
        ),
    }
    aggregate["work_volume"] = _aggregate_episode_work_volume(all_episode_metrics)
    payload = {
        "schema_version": BREAKDOWN_SCHEMA_VERSION,
        "run_id": run_id,
        "freeze_sha256": freeze_sha256,
        "interpretation": "bounded_screening_not_significance_claim",
        "blocks": block_summaries,
        "aggregate": aggregate,
        "aggregate_phase_occupancy": aggregate_phase_occupancy,
        "telemetry_completeness": {
            "status": (
                "complete"
                if not aggregate_missing_fields
                else "incomplete_missing_required_fields"
            ),
            "missing_required_fields": aggregate_missing_fields,
        },
    }
    return _payload_with_hash(payload)


def require_complete_e1_breakdown(
    breakdown: Mapping[str, Any], blocks: Sequence[E1E2Block]
) -> None:
    """Reject any C2 completion whose dynamic trace differs from the freeze."""

    telemetry = breakdown.get("telemetry_completeness")
    summaries = breakdown.get("blocks")
    if (
        not isinstance(telemetry, Mapping)
        or telemetry.get("status") != "complete"
        or not isinstance(summaries, list)
        or len(summaries) != len(blocks)
    ):
        raise NativeCharacterizationC2Error("measurement_contract_incomplete")
    by_index = {
        summary.get("block_index"): summary
        for summary in summaries
        if isinstance(summary, Mapping)
    }
    if set(by_index) != {block.block_index for block in blocks}:
        raise NativeCharacterizationC2Error("measurement_contract_incomplete")
    for block in blocks:
        summary = by_index[block.block_index]
        observed = summary.get("episode_metrics")
        if not isinstance(observed, list):
            raise NativeCharacterizationC2Error("measurement_contract_incomplete")
        observed_identities = [
            (item.get("episode_id"), item.get("source_sequence"))
            for item in observed
            if isinstance(item, Mapping)
        ]
        planned_identities = [
            (episode["episode_id"], episode["source_sequence"])
            for episode in block.episodes
        ]
        if (
            observed_identities != planned_identities
            or summary.get("observed_episode_count") != block.episode_count
            or summary.get("telemetry_completeness", {}).get("status")
            != "complete"
        ):
            raise NativeCharacterizationC2Error("measurement_contract_incomplete")


def _require_complete_episode_measurement(episode: Mapping[str, Any]) -> None:
    telemetry = episode.get("telemetry_completeness")
    if not isinstance(telemetry, Mapping) or telemetry.get("status") != "complete":
        raise NativeCharacterizationC2Error("measurement_contract_incomplete")


def _checkpoint_payload(
    *,
    run_id: str,
    status: str,
    blocks: Sequence[E1E2Block],
    completed: Sequence[int],
    completed_episode_ids: Sequence[str],
    checkpoint_history: Sequence[Mapping[str, Any]],
    error: BaseException | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "run_id": run_id,
        "stage": "C2",
        "status": status,
        "completed_block_indices": list(completed),
        "completed_episode_ids": list(completed_episode_ids),
        "planned_block_indices": [block.block_index for block in blocks],
        "checkpoint_history": [dict(item) for item in checkpoint_history],
        "error_code": (
            None
            if error is None
            else f"{type(error).__module__}.{type(error).__qualname__}"
        ),
    }
    return _payload_with_hash(payload)


async def _close_runtime(runtime: Any) -> None:
    close = getattr(getattr(runtime, "graphiti", None), "close", None)
    if callable(close):
        await close()


async def execute_c2(
    *,
    validation_root: str | Path,
    freeze_path: str | Path,
    run_id: str,
    authorization_checker: Callable[[LiveAction], Any] = require_live_action,
    runtime_factory: Callable[..., Any] | None = None,
    source_path: str | Path | None = None,
    measurement_installer: Callable[..., Any] = install_c2_measurement_adapter,
    graph_prefix_collector: Callable[..., Any] = collect_graph_prefix_size,
    progress_sink: Callable[[Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Run the frozen E1 blocks and checkpoint every completed block."""

    authorization_checker(LiveAction.NATIVE_CHARACTERIZATION_C2)
    validation = Path(validation_root).resolve()
    freeze, freeze_relative = _resolve_safe_validation_relative_file(
        validation,
        freeze_path,
        error_code="freeze_path_invalid",
    )
    freeze_sha256 = _sha256_file(freeze)
    freeze_payload = _load_json_object(freeze, "freeze")
    blocks = load_e1_e2_blocks(freeze)
    if _RUN_ID_RE.fullmatch(run_id) is None and not run_id.startswith("c2-offline-"):
        raise NativeCharacterizationC2Error("run_id_invalid")
    run_root = validation / "artifacts" / "native_characterization" / "runs" / run_id
    if run_root.exists() and any(run_root.iterdir()):
        raise FileExistsError("C2 run namespace already contains evidence")

    if runtime_factory is None:
        def runtime_factory() -> Any:
            return build_u0_graphiti_from_env(
                authorization_checker=authorization_checker,
                live_action=LiveAction.NATIVE_CHARACTERIZATION_C2,
            )

    runtime_episodes = (
        _runtime_episode_map(blocks, source_path)
        if source_path is not None
        else {}
    )
    runtime = runtime_factory()
    recorder = TraceRecorder()
    completed: list[int] = []
    completed_episode_ids: list[str] = []
    checkpoint_history: list[dict[str, Any]] = []
    block_records: list[tuple[E1E2Block, list[SpanRecord]]] = []
    root_writers = {
        name: DurableJsonlEnvelopeWriter(run_root / name)
        for name in _ROOT_JSONL_VIEWS
    }

    try:
        await _ensure_driver_ready(runtime.graphiti)
        phase_module = getattr(runtime, "phase_module", None)
        base_handle = install_native_characterization_instrumentation(
            runtime.graphiti,
            recorder,
            phase_module=phase_module,
        )
        try:
            measurement_handle = measurement_installer(
                runtime.graphiti,
                recorder,
                phase_module=phase_module,
            )
            try:
                for block in blocks:
                    block_dir = (
                        run_root
                        / "blocks"
                        / f"{block.block_index:03d}_{block.history_id}"
                    )
                    trace_writer = DurableJsonlEnvelopeWriter(block_dir / "trace.jsonl")
                    before = len(recorder.records)
                    for episode_meta in block.episodes:
                        episode_id = str(episode_meta["episode_id"])
                        source_sequence = int(episode_meta["source_sequence"])
                        runtime_episode = runtime_episodes.get(
                            (block.history_id, source_sequence)
                        )
                        prefix_size = await graph_prefix_collector(
                            runtime.graphiti.driver,
                            block.graph_namespace,
                        )
                        with recorder.episode_scope(
                            run_id, episode_id, source_sequence
                        ):
                            with recorder.span(
                                "graph-prefix-snapshot",
                                operation_class="group-count-before-add-episode",
                                metadata=dict(prefix_size),
                            ):
                                pass
                            try:
                                await _add_episode(
                                    runtime.graphiti,
                                    episode_meta,
                                    runtime_episode,
                                )
                            except BaseException:
                                # Close and fsync the failed episode before the
                                # outer handler publishes an error checkpoint.
                                envelope = _sealed_episode_envelope(
                                    recorder,
                                    run_id=run_id,
                                    episode_id=episode_id,
                                    source_sequence=source_sequence,
                                    episode_meta=episode_meta,
                                )
                                _persist_episode_envelope(
                                    block_writer=trace_writer,
                                    root_writers=root_writers,
                                    envelope=envelope,
                                )
                                raise
                        envelope = _sealed_episode_envelope(
                            recorder,
                            run_id=run_id,
                            episode_id=episode_id,
                            source_sequence=source_sequence,
                            episode_meta=episode_meta,
                        )
                        _persist_episode_envelope(
                            block_writer=trace_writer,
                            root_writers=root_writers,
                            envelope=envelope,
                        )
                        episode_records = [
                            _record_from_dict(span) for span in envelope["spans"]
                        ]
                        episode_analysis = _episode_analysis(
                            episode_records, episode_meta
                        )
                        _require_complete_episode_measurement(episode_analysis)
                        completed_episode_ids.append(episode_id)
                        checkpoint_history.append(
                            {
                                "event_type": "episode_completed",
                                "block_index": block.block_index,
                                "history_id": block.history_id,
                                "episode_id": episode_id,
                                "source_sequence": source_sequence,
                                "status": "completed",
                            }
                        )
                        episode_checkpoint = _checkpoint_payload(
                            run_id=run_id,
                            status="episode_completed",
                            blocks=blocks,
                            completed=completed,
                            completed_episode_ids=completed_episode_ids,
                            checkpoint_history=checkpoint_history,
                        )
                        _atomic_json(block_dir / "checkpoint.json", episode_checkpoint)
                        _atomic_json(run_root / "checkpoint.json", episode_checkpoint)
                        if progress_sink is not None:
                            progress_sink(
                                {
                                    "event_type": "episode_completed",
                                    "run_id": run_id,
                                    "block_index": block.block_index,
                                    "history_id": block.history_id,
                                    "episode_id": episode_id,
                                    "source_sequence": source_sequence,
                                    "completed_episode_count": len(
                                        completed_episode_ids
                                    ),
                                    "planned_episode_count": sum(
                                        item.episode_count for item in blocks
                                    ),
                                    "service_latency_ns": episode_analysis[
                                        "service_latency_ns"
                                    ],
                                    "publication_latency_ns": episode_analysis[
                                        "publication_latency_ns"
                                    ],
                                    "graph_prefix_size": deepcopy(
                                        episode_analysis["graph_prefix_size"]
                                    ),
                                    "phase_interval_union_ns": deepcopy(
                                        episode_analysis["accounting"][
                                            "interval_union_by_phase_ns"
                                        ]
                                    ),
                                    "work_volume": deepcopy(
                                        episode_analysis["work_volume"]
                                    ),
                                    "telemetry_completeness": (
                                        episode_analysis[
                                            "telemetry_completeness"
                                        ]["status"]
                                    ),
                                    "status": "completed",
                                }
                            )
                    records = _records_since(recorder.records, before)
                    block_records.append((block, records))
                    summary_payload = _payload_with_hash(
                        {
                            "schema_version": (
                                "membind.native-characterization-c2-block-summary.v1"
                            ),
                            "run_id": run_id,
                            "block_index": block.block_index,
                            "history_id": block.history_id,
                            "graph_namespace": block.graph_namespace,
                            "episode_count": block.episode_count,
                            "span_count": len(records),
                            "freeze_sha256": freeze_sha256,
                            "counters": _counter_summary(records),
                        }
                    )
                    block_summary_sha = _atomic_json(
                        block_dir / "block_summary.json",
                        summary_payload,
                    )
                    completed.append(block.block_index)
                    checkpoint_event = {
                        "event_type": "block_completed",
                        "block_index": block.block_index,
                        "history_id": block.history_id,
                        "block_summary_sha256": block_summary_sha,
                        "trace_line_count": block.episode_count,
                        "status": "completed",
                    }
                    checkpoint_history.append(checkpoint_event)
                    block_checkpoint = _checkpoint_payload(
                        run_id=run_id,
                        status="block_completed",
                        blocks=blocks,
                        completed=completed,
                        completed_episode_ids=completed_episode_ids,
                        checkpoint_history=checkpoint_history,
                    )
                    _atomic_json(block_dir / "checkpoint.json", block_checkpoint)
                    _atomic_json(run_root / "checkpoint.json", block_checkpoint)
                    if progress_sink is not None:
                        progress_sink(
                            {
                                "event_type": "block_completed",
                                "run_id": run_id,
                                "block_index": block.block_index,
                                "history_id": block.history_id,
                                "completed_block_count": len(completed),
                                "planned_block_count": len(blocks),
                                "status": "completed",
                            }
                        )

                breakdown = analyze_e1_breakdown(
                    run_id=run_id,
                    blocks=block_records,
                    freeze_sha256=freeze_sha256,
                )
                breakdown_sha = _atomic_json(
                    run_root / "e1_breakdown.json", breakdown
                )
                require_complete_e1_breakdown(breakdown, blocks)
                final_checkpoint = _checkpoint_payload(
                    run_id=run_id,
                    status="completed",
                    blocks=blocks,
                    completed=completed,
                    completed_episode_ids=completed_episode_ids,
                    checkpoint_history=checkpoint_history,
                )
                checkpoint_sha = _atomic_json(
                    run_root / "checkpoint.json", final_checkpoint
                )
                top_level_breakdown_sha = _atomic_json(
                    validation
                    / "artifacts/native_characterization/e1_breakdown.json",
                    breakdown,
                )
                artifact_hashes, artifact_inventory = _artifact_inventory(run_root)
                manifest = _payload_with_hash(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "run_id": run_id,
                        "stage": "C2",
                        "status": "completed",
                        "freeze_sha256": freeze_sha256,
                        "e1_breakdown_sha256": breakdown_sha,
                        "top_level_e1_breakdown_sha256": top_level_breakdown_sha,
                        "checkpoint_sha256": checkpoint_sha,
                        "block_count": len(blocks),
                        "episode_count": sum(
                            block.episode_count for block in blocks
                        ),
                        "telemetry_completeness": deepcopy(
                            breakdown["telemetry_completeness"]
                        ),
                        "provenance": _run_provenance(
                            validation_root=validation,
                            freeze_path=freeze_relative,
                            freeze_sha256=freeze_sha256,
                            freeze_payload=freeze_payload,
                            run_id=run_id,
                        ),
                        "artifact_sha256": artifact_hashes,
                        "artifact_inventory": artifact_inventory,
                        "interpretation": (
                            "bounded_screening_not_significance_claim"
                        ),
                    }
                )
                manifest_sha = _atomic_json(run_root / "manifest.json", manifest)
                if progress_sink is not None:
                    progress_sink(
                        {
                            "event_type": "run_completed",
                            "run_id": run_id,
                            "completed_episode_count": len(completed_episode_ids),
                            "planned_episode_count": sum(
                                item.episode_count for item in blocks
                            ),
                            "completed_block_count": len(completed),
                            "planned_block_count": len(blocks),
                            "manifest_sha256": manifest_sha,
                            "status": "completed",
                        }
                    )
                return {
                    "status": "completed",
                    "run_id": run_id,
                    "manifest_sha256": manifest_sha,
                    "checkpoint_sha256": checkpoint_sha,
                    "e1_breakdown_sha256": breakdown_sha,
                    "top_level_e1_breakdown_sha256": top_level_breakdown_sha,
                }
            finally:
                measurement_handle.restore()
        finally:
            base_handle.restore()
    except BaseException as exc:
        failure_checkpoint = _checkpoint_payload(
            run_id=run_id,
            status="error",
            blocks=blocks,
            completed=completed,
            completed_episode_ids=completed_episode_ids,
            checkpoint_history=checkpoint_history,
            error=exc,
        )
        _atomic_json(run_root / "checkpoint.json", failure_checkpoint)
        raise
    finally:
        await _close_runtime(runtime)


def _main() -> int:
    validation = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--run-id", default="c2-0000000000000000")
    parser.add_argument(
        "--freeze",
        type=Path,
        default=Path("artifacts/native_characterization/freeze.json"),
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    args = parser.parse_args()
    if not args.live:
        freeze, _freeze_relative = _resolve_safe_validation_relative_file(
            validation,
            args.freeze,
            error_code="freeze_path_invalid",
        )
        blocks = load_e1_e2_blocks(freeze)
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "block_count": len(blocks),
                    "episode_count": sum(block.episode_count for block in blocks),
                    "block_indices": [block.block_index for block in blocks],
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    try:
        result = asyncio.run(
            execute_c2(
                validation_root=validation,
                freeze_path=args.freeze,
                run_id=args.run_id,
                source_path=args.source,
                progress_sink=lambda event: print(
                    json.dumps(
                        event,
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    flush=True,
                ),
            )
        )
    except BaseException as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_code": f"{type(exc).__module__}.{type(exc).__qualname__}",
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
