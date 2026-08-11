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
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from current_state_gate import LiveAction, require_live_action
from dataset import Episode, build_episodes, load_json_records
from graphiti_native import graphiti_episode_kwargs
from native_characterization_instrumentation import (
    install_native_characterization_instrumentation,
)
from native_characterization_runtime import build_u0_graphiti_from_env
from native_characterization_tracing import (
    DurableJsonlEnvelopeWriter,
    SpanRecord,
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
    "attributes-summary",
    "publication",
    "llm",
    "llm-transport",
    "embedding",
    "database",
    "database-transaction",
)


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
        if union_ns or phase in {"llm", "embedding", "database"}:
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
    return {
        "llm_logical_call_count": len(llm_records),
        "llm_transport_attempt_count": sum(
            1 for record in records if record.phase == "llm-transport"
        ),
        "llm_input_tokens": sum(
            int(record.metadata.get("input_tokens", 0)) for record in llm_records
        ),
        "llm_output_tokens": sum(
            int(record.metadata.get("output_tokens", 0)) for record in llm_records
        ),
        "embedding_call_count": len(embedding_records),
        "embedding_text_count": sum(
            int(record.metadata.get("text_count", 0)) for record in embedding_records
        ),
        "embedding_dimension_observed": max(
            [int(record.metadata.get("dimension", 0)) for record in embedding_records]
            or [0]
        ),
        "db_query_count": sum(
            1 for record in database_records if record.operation_class == "query"
        ),
        "db_write_count": sum(
            1 for record in database_records if record.operation_class == "write"
        ),
        "db_transaction_count": len(transaction_records),
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
        "llm_input_tokens": 0,
        "llm_output_tokens": 0,
        "embedding_call_count": 0,
        "embedding_text_count": 0,
        "embedding_dimension_observed": 0,
        "db_query_count": 0,
        "db_write_count": 0,
        "db_transaction_count": 0,
    }
    aggregate_phase_union: dict[str, int] = {}

    for block, records in blocks:
        ordered = sorted(records, key=lambda record: record.sequence)
        phases = _safe_phase_summary(ordered)
        counters = _counter_summary(ordered)
        total_ns = int(phases["total_add_episode_union_ns"])
        block_summaries.append(
            {
                "block_index": block.block_index,
                "history_id": block.history_id,
                "graph_namespace": block.graph_namespace,
                "episode_count": block.episode_count,
                "span_count": len(ordered),
                "total_add_episode_union_ns": total_ns,
                "phase_occupancy": phases["phase_occupancy"],
                "counters": counters,
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
    payload = {
        "schema_version": BREAKDOWN_SCHEMA_VERSION,
        "run_id": run_id,
        "freeze_sha256": freeze_sha256,
        "interpretation": "bounded_screening_not_significance_claim",
        "blocks": block_summaries,
        "aggregate": aggregate,
        "aggregate_phase_occupancy": aggregate_phase_occupancy,
    }
    return _payload_with_hash(payload)


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
) -> dict[str, Any]:
    """Run the frozen E1 blocks and checkpoint every completed block."""

    authorization_checker(LiveAction.NATIVE_CHARACTERIZATION_C2)
    validation = Path(validation_root).resolve()
    freeze = Path(freeze_path).resolve()
    freeze_sha256 = _sha256_file(freeze)
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

    try:
        await _ensure_driver_ready(runtime.graphiti)
        phase_module = getattr(runtime, "phase_module", None)
        handle = install_native_characterization_instrumentation(
            runtime.graphiti,
            recorder,
            phase_module=phase_module,
        )
        try:
            for block in blocks:
                block_dir = run_root / "blocks" / f"{block.block_index:03d}_{block.history_id}"
                trace_writer = DurableJsonlEnvelopeWriter(block_dir / "trace.jsonl")
                before = len(recorder.records)
                for episode_meta in block.episodes:
                    episode_id = str(episode_meta["episode_id"])
                    source_sequence = int(episode_meta["source_sequence"])
                    runtime_episode = runtime_episodes.get(
                        (block.history_id, source_sequence)
                    )
                    with recorder.episode_scope(run_id, episode_id, source_sequence):
                        await _add_episode(runtime.graphiti, episode_meta, runtime_episode)
                    trace_writer.write(
                        recorder.episode_envelope(
                            run_id,
                            episode_id,
                            source_sequence,
                        )
                    )
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
                records = _records_since(recorder.records, before)
                block_records.append((block, records))
                summary_payload = _payload_with_hash(
                    {
                        "schema_version": "membind.native-characterization-c2-block-summary.v1",
                        "run_id": run_id,
                        "block_index": block.block_index,
                        "history_id": block.history_id,
                        "graph_namespace": block.graph_namespace,
                        "episode_count": block.episode_count,
                        "span_count": len(records),
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

            breakdown = analyze_e1_breakdown(
                run_id=run_id,
                blocks=block_records,
                freeze_sha256=freeze_sha256,
            )
            breakdown_sha = _atomic_json(run_root / "e1_breakdown.json", breakdown)
            final_checkpoint = _checkpoint_payload(
                run_id=run_id,
                status="completed",
                blocks=blocks,
                completed=completed,
                completed_episode_ids=completed_episode_ids,
                checkpoint_history=checkpoint_history,
            )
            checkpoint_sha = _atomic_json(run_root / "checkpoint.json", final_checkpoint)
            manifest = _payload_with_hash(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "stage": "C2",
                    "status": "completed",
                    "freeze_sha256": freeze_sha256,
                    "e1_breakdown_sha256": breakdown_sha,
                    "checkpoint_sha256": checkpoint_sha,
                    "block_count": len(blocks),
                    "episode_count": sum(block.episode_count for block in blocks),
                    "interpretation": "bounded_screening_not_significance_claim",
                }
            )
            manifest_sha = _atomic_json(run_root / "manifest.json", manifest)
            return {
                "status": "completed",
                "run_id": run_id,
                "manifest_sha256": manifest_sha,
                "checkpoint_sha256": checkpoint_sha,
                "e1_breakdown_sha256": breakdown_sha,
            }
        finally:
            handle.restore()
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
        default=validation / "artifacts/native_characterization/freeze.json",
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    args = parser.parse_args()
    if not args.live:
        blocks = load_e1_e2_blocks(args.freeze)
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
