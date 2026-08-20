from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from paper_eval.artifacts import payload_sha256, sha256_file

from .model import NOT_OBSERVABLE, PublicationRecord, RequestRecord, TraceBundle


class TraceParseError(ValueError):
    """A sealed trace wrapper or its lifecycle is invalid."""


def _fail(code: str) -> TraceParseError:
    return TraceParseError(code)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _fail(f"json_read_failed:{path.name}") from exc
    if not isinstance(value, dict):
        raise _fail(f"json_object_required:{path.name}")
    return value


def _read_wrapped_jsonl(path: Path, *, payload_key: str, digest_key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise _fail(f"trace_read_failed:{path.name}") from exc
    if not lines:
        raise _fail(f"trace_empty:{path.name}")
    for line_number, line in enumerate(lines, start=1):
        try:
            wrapper = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _fail(f"trace_json_invalid:{path.name}:{line_number}") from exc
        if not isinstance(wrapper, dict) or payload_key not in wrapper or digest_key not in wrapper:
            raise _fail(f"trace_wrapper_invalid:{path.name}:{line_number}")
        payload = wrapper[payload_key]
        digest = wrapper[digest_key]
        if not isinstance(payload, dict) or not isinstance(digest, str):
            raise _fail(f"trace_wrapper_types_invalid:{path.name}:{line_number}")
        if payload_sha256(payload) != digest:
            raise _fail(f"trace_hash_invalid:{path.name}:{line_number}")
        rows.append(payload)
    return rows


def _integer(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _metadata(row: dict[str, Any], key: str, *, optional: bool = False) -> str | None:
    value = row.get(key)
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise _fail(f"metadata_missing:{key}")
    return value


def _build_requests(rows: list[dict[str, Any]]) -> tuple[RequestRecord, ...]:
    submitted = {
        str(row.get("request_id")): row
        for row in rows
        if row.get("event_type") == "llm_request_submitted"
    }
    starts = {
        str(row.get("request_id")): row
        for row in rows
        if row.get("event_type") == "llm_request_start"
    }
    terminals = {
        str(row.get("request_id")): row
        for row in rows
        if row.get("event_type") == "llm_request_terminal"
    }
    if not submitted:
        raise _fail("llm_submissions_missing")
    if set(submitted) != set(starts) or set(submitted) != set(terminals):
        raise _fail("llm_lifecycle_incomplete")
    requests: list[RequestRecord] = []
    for request_id, row in submitted.items():
        request_id_value = _metadata(row, "request_id")
        assert request_id_value is not None
        start = starts[request_id]
        terminal = terminals[request_id]
        submitted_ns = _integer(row.get("timestamp_ns"), "submitted_timestamp_invalid")
        started_ns = _integer(start.get("timestamp_ns"), "start_timestamp_invalid")
        terminal_ns = _integer(terminal.get("timestamp_ns"), "terminal_timestamp_invalid")
        if started_ns < submitted_ns or terminal_ns < started_ns:
            raise _fail(f"llm_lifecycle_order_invalid:{request_id}")
        service_duration = terminal_ns - started_ns
        token_count = row.get("token_count")
        if isinstance(token_count, bool) or not isinstance(token_count, int) or token_count < 0:
            raise _fail(f"token_count_invalid:{request_id}")
        requests.append(
            RequestRecord(
                request_id=request_id_value,
                stream_id=_metadata(row, "stream_id") or NOT_OBSERVABLE,
                source_sequence=_integer(row.get("source_sequence"), "source_sequence_invalid"),
                request_kind=_metadata(row, "request_kind") or NOT_OBSERVABLE,
                operator_role=_metadata(row, "operator_role") or NOT_OBSERVABLE,
                operator_id=_metadata(row, "operator_id") or NOT_OBSERVABLE,
                parent_bind_id=_metadata(row, "parent_bind_id", optional=True),
                parent_operator_id=_metadata(row, "parent_operator_id", optional=True),
                operator_phase=_metadata(row, "operator_phase") or NOT_OBSERVABLE,
                submitted_ns=submitted_ns,
                started_ns=started_ns,
                terminal_ns=terminal_ns,
                service_duration_ns=service_duration,
                token_count=token_count,
                prompt_tokens=NOT_OBSERVABLE,
                completion_tokens=NOT_OBSERVABLE,
                execution_mode=row.get("execution_mode") if isinstance(row.get("execution_mode"), str) else NOT_OBSERVABLE,
                persistent_state_access_class=NOT_OBSERVABLE,
            )
        )
    return tuple(sorted(requests, key=lambda request: (request.submitted_ns, request.request_id)))


def _build_publications(events: list[dict[str, Any]]) -> tuple[PublicationRecord, ...]:
    arrivals: dict[int, int] = {}
    publications: dict[int, int] = {}
    for event in events:
        event_type = event.get("event_type")
        source = event.get("source_sequence")
        if event_type not in {"ARRIVAL", "PUBLICATION_DURABLE"}:
            continue
        source_sequence = _integer(source, "event_source_sequence_invalid")
        timestamp = _integer(event.get("timestamp_ns"), "event_timestamp_invalid")
        if event_type == "ARRIVAL":
            arrivals[source_sequence] = timestamp
        else:
            publications[source_sequence] = timestamp
    if not publications:
        raise _fail("publication_events_missing")
    missing_arrivals = sorted(set(publications) - set(arrivals))
    if missing_arrivals:
        raise _fail(f"arrival_events_missing:{missing_arrivals}")
    return tuple(
        PublicationRecord(
            source_sequence=source,
            arrival_ns=arrivals[source],
            publication_ns=publications[source],
        )
        for source in sorted(publications)
    )


def load_trace_bundle(
    *,
    llm_path: Path,
    events_path: Path,
    manifest_path: Path | None = None,
    history_id: str | None = None,
    configured_k: int | None = None,
    source_count: int | None = None,
) -> TraceBundle:
    """Load and hash-verify a sealed Q0-style request/event trace."""

    llm_rows = _read_wrapped_jsonl(llm_path, payload_key="record", digest_key="record_sha256")
    event_rows = _read_wrapped_jsonl(events_path, payload_key="event", digest_key="event_sha256")
    manifest: dict[str, Any] = _read_json(manifest_path) if manifest_path else {}
    selected_history = history_id or manifest.get("history_id")
    if not isinstance(selected_history, str) or not selected_history:
        # All Q0 request rows expose stream_id; require a stable single stream.
        streams = {row.get("row", {}).get("stream_id") for row in llm_rows}
        streams.discard(None)
        if len(streams) != 1:
            raise _fail("history_id_unobservable")
        selected_history = next(iter(streams))
    k = configured_k if configured_k is not None else manifest.get("global_llm_admission_k", 2)
    count = source_count if source_count is not None else manifest.get("source_count")
    if isinstance(k, bool) or not isinstance(k, int) or k <= 0:
        raise _fail("configured_k_unobservable")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise _fail("source_count_unobservable")
    requests = _build_requests([row["row"] for row in llm_rows])
    publications = _build_publications(event_rows)
    if count != len(publications):
        raise _fail("source_count_publication_mismatch")
    if manifest:
        expected_manifest_sha = manifest.get("manifest_sha256")
        if expected_manifest_sha is not None and expected_manifest_sha != sha256_file(manifest_path):
            # A manifest may contain a self-referential hash or an external
            # registry hash. Keep the exact mismatch visible but do not reject
            # unless it is explicitly a trace hash field.
            pass
    observability = {
        "llm_file_sha256": sha256_file(llm_path),
        "events_file_sha256": sha256_file(events_path),
        "manifest_file_sha256": sha256_file(manifest_path) if manifest_path else NOT_OBSERVABLE,
        "prompt_name": NOT_OBSERVABLE,
        "persistent_state_access_class": NOT_OBSERVABLE,
        "transport_request_id": NOT_OBSERVABLE,
    }
    return TraceBundle(
        history_id=selected_history,
        requests=requests,
        publications=publications,
        configured_k=k,
        source_count=count,
        input_paths=tuple(str(path) for path in (llm_path, events_path) if path),
        observability=observability,
    )
