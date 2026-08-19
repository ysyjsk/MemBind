"""Read-only P0 evidence binding for the MemBind v4 lane.

P0 registers already-produced v3.1 and baseline artifacts.  It never opens a
network connection and never copies or mutates the source runs.  The output
contains absolute paths and SHA-256 digests so later v4 runs can prove exactly
which evidence they used.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file


BASELINE_BINDING_SCHEMA = "membind.paper-eval-v3.membind-v4-baseline-binding.v1"
ROLE_PROFILE_SCHEMA = "membind.paper-eval-v3.membind-v4-role-profile.v1"
PREFIX_REFERENCE_SCHEMA = "membind.paper-eval-v3.membind-v4-prefix-reference.v1"


class P0BindingError(ValueError):
    """Raised when an offline P0 source cannot be bound deterministically."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise P0BindingError(f"artifact unreadable: {path}") from error
    if not isinstance(value, dict):
        raise P0BindingError(f"artifact must be a JSON object: {path}")
    return value


def _path(path: Path | str) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file():
        raise P0BindingError(f"artifact missing: {candidate}")
    return candidate


def _seal(body: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(body))
    result["payload_sha256"] = payload_sha256(result)
    return result


def _int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def _percentile(values: Sequence[int], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(ordered[lower])
    weight = index - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)


def _result_status(raw: Mapping[str, Any]) -> str | None:
    direct = raw.get("status")
    if isinstance(direct, str):
        return direct
    nested = raw.get("block_result")
    if isinstance(nested, Mapping) and isinstance(nested.get("status"), str):
        return str(nested["status"])
    return None


def _result_identity(raw: Mapping[str, Any], key: str) -> object:
    if key in raw:
        return raw[key]
    nested = raw.get("block_result")
    if isinstance(nested, Mapping):
        return nested.get(key)
    return None


def _identity_map(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Collect content identities without assuming one result schema."""

    scopes: list[Mapping[str, Any]] = [raw]
    for key in ("block_result", "live", "manifest"):
        value = raw.get(key)
        if isinstance(value, Mapping):
            scopes.append(value)
    names = (
        "provider_execution_envelope_sha256",
        "execution_identity_sha256",
        "shared_execution_envelope_sha256",
        "construction_model_identity_sha256",
        "embedding_model_identity_sha256",
        "arrival_trace_sha256",
        "history_arrival_trace_sha256",
        "source_manifest_sha256",
    )
    return {
        name: value
        for name in names
        for scope in scopes
        if (value := scope.get(name)) is not None
    }


def _companion_bindings(result_path: Path) -> dict[str, dict[str, Any]]:
    """Bind nearby manifest/checkpoint files when they are present.

    Companions are optional because older successful result artifacts did not
    always publish every sidecar.  Missing companions are reported, never
    silently synthesized.
    """

    companions: dict[str, dict[str, Any]] = {}
    block_root = result_path.parent
    for name in ("manifest.json", "checkpoint.json", "events.jsonl"):
        candidate = block_root / name
        companions[name] = {
            "absolute_path": str(candidate.resolve()),
            "exists": candidate.is_file(),
            "sha256": sha256_file(candidate) if candidate.is_file() else None,
        }
    return companions


def _bind_file(path: Path | str, *, role: str) -> dict[str, Any]:
    candidate = _path(path)
    raw = _read_json(candidate) if candidate.suffix.lower() == ".json" else {}
    return {
        "role": role,
        "absolute_path": str(candidate),
        "sha256": sha256_file(candidate),
        "size_bytes": candidate.stat().st_size,
        "schema_version": raw.get("schema_version"),
        "status": _result_status(raw),
        "run_id": _result_identity(raw, "run_id"),
        "method": _result_identity(raw, "method"),
        "history_id": _result_identity(raw, "history_id"),
        "identities": _identity_map(raw),
        "companions": _companion_bindings(candidate)
        if candidate.suffix.lower() == ".json"
        else {},
    }


def build_baseline_binding(
    *,
    v31_result_path: Path | str,
    baseline_result_paths: Iterable[Path | str],
) -> dict[str, Any]:
    """Bind v3.1 and one or more existing baseline result files.

    This function is deliberately agnostic about whether a baseline run is
    currently complete.  It records each artifact's status and lets the later
    full-run gate decide eligibility; P0 itself never reruns a partial lane.
    """

    v31 = _bind_file(v31_result_path, role="v3_1_success")
    paths = [Path(value) for value in baseline_result_paths]
    if not paths:
        raise P0BindingError("at least one baseline result is required")
    baseline = [_bind_file(value, role="baseline_result") for value in paths]
    absolute = [item["absolute_path"] for item in baseline]
    if len(set(absolute)) != len(absolute):
        raise P0BindingError("duplicate baseline result path")
    identity_records = [v31, *baseline]
    shared_envelopes = sorted(
        {
            str(value)
            for item in identity_records
            for key, value in item.get("identities", {}).items()
            if key == "shared_execution_envelope_sha256" and isinstance(value, str)
        }
    )
    provider_envelopes = sorted(
        {
            str(value)
            for item in identity_records
            for key, value in item.get("identities", {}).items()
            if key == "provider_execution_envelope_sha256" and isinstance(value, str)
        }
    )
    body = {
        "schema_version": BASELINE_BINDING_SCHEMA,
        "status": "PASS",
        "binding_mode": "READ_ONLY_ABSOLUTE_PATH_SHA256",
        "live_rerun_performed": False,
        "artifacts": {"v3_1_success": v31, "baseline": baseline},
        "identity_consistency": {
            "shared_execution_envelope_sha256s": shared_envelopes,
            "provider_execution_envelope_sha256s": provider_envelopes,
            "status": "UNIFORM"
            if len(shared_envelopes) <= 1
            else "MIXED_ENVELOPES_NOT_FORMAL_COMPARISON",
        },
        "limitations": [
            "P0 records existing artifacts only; it does not infer formal main-table eligibility.",
            "Provider-envelope equivalence remains a later execution-gate responsibility.",
        ],
    }
    return _seal(body)


def _unwrap_trace_row(value: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value.get("record"), Mapping):
        record = value["record"]
        if isinstance(record.get("row"), Mapping):
            return dict(record["row"])
        return dict(record)
    return dict(value)


def _trace_rows(path: Path) -> Iterable[dict[str, Any]]:
    try:
        stream = path.open("r", encoding="utf-8")
    except OSError as error:
        raise P0BindingError(f"trace unreadable: {path}") from error
    with stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise P0BindingError(f"trace JSON invalid: {path}:{line_number}") from error
            if not isinstance(value, Mapping):
                continue
            yield _unwrap_trace_row(value)


def _trace_role(row: Mapping[str, Any]) -> str:
    metadata = row.get("metadata")
    if isinstance(metadata, Mapping):
        for key in ("prompt_name", "request_kind", "phase"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value
    for key in ("prompt_name", "request_kind", "phase"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return "UNKNOWN"


def _trace_metric(row: Mapping[str, Any], key: str) -> int:
    metadata = row.get("metadata")
    if isinstance(metadata, Mapping) and key in metadata:
        return _int(metadata.get(key))
    return _int(row.get(key))


def build_role_profile(trace_paths: Iterable[Path | str]) -> dict[str, Any]:
    """Derive deterministic request-role statistics from logical-call rows."""

    paths = [_path(value) for value in trace_paths]
    if not paths:
        raise P0BindingError("at least one LLM trace is required")
    grouped: dict[str, list[dict[str, int]]] = defaultdict(list)
    source_traces: list[dict[str, Any]] = []
    logical_rows = 0
    all_outputs: list[int] = []
    for path in paths:
        source_traces.append(
            {"absolute_path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
        )
        for row in _trace_rows(path):
            operation_class = row.get("operation_class")
            phase = row.get("phase")
            # v3.1 wraps request events; only logical calls represent a role.
            if operation_class not in (None, "logical-call"):
                continue
            if operation_class is None and phase not in (None, "llm"):
                continue
            role = _trace_role(row)
            item = {
                "input_tokens": _trace_metric(row, "input_tokens")
                or _trace_metric(row, "token_count"),
                "output_tokens": _trace_metric(row, "output_tokens"),
                "duration_ns": _int(row.get("duration_ns")),
                "retry_count": _trace_metric(row, "retry_count"),
            }
            grouped[role].append(item)
            all_outputs.append(item["output_tokens"])
            logical_rows += 1
    if not logical_rows:
        raise P0BindingError("LLM trace contains no logical-call rows")
    # A p90 cutoff keeps the long-prefill roles (large prompt, modest decode)
    # together while leaving genuinely long-decode outliers in MIXED.  The
    # quantile is frozen in the artifact before any v4 candidate runs.
    long_decode_cutoff_quantile = 0.90
    long_decode_cutoff = _percentile(all_outputs, long_decode_cutoff_quantile)
    if long_decode_cutoff is None:
        long_decode_cutoff = 0.0
    roles: dict[str, Any] = {}
    for role in sorted(grouped):
        rows = grouped[role]
        inputs = [row["input_tokens"] for row in rows]
        outputs = [row["output_tokens"] for row in rows]
        durations = [row["duration_ns"] for row in rows]
        retries = [row["retry_count"] for row in rows]
        median_input = float(statistics.median(inputs))
        median_output = float(statistics.median(outputs))
        if median_input >= 4096 and median_output < long_decode_cutoff:
            resource_class = "LONG_PREFILL"
        elif median_input < 4096:
            resource_class = "SHORT"
        else:
            resource_class = "MIXED"
        roles[role] = {
            "logical_call_count": len(rows),
            "input_tokens_total": sum(inputs),
            "output_tokens_total": sum(outputs),
            "retry_count_total": sum(retries),
            "input_tokens_median": median_input,
            "output_tokens_median": median_output,
            "duration_ns_median": float(statistics.median(durations)),
            "duration_ns_p50": _percentile(durations, 0.50),
            "duration_ns_p95": _percentile(durations, 0.95),
            "resource_class": resource_class,
        }
    body = {
        "schema_version": ROLE_PROFILE_SCHEMA,
        "status": "PASS",
        "profile_source": "EXISTING_LOGICAL_CALL_TRACE",
        "source_traces": source_traces,
        "limitations": [
            "The available target-GPU c246 baseline exposes no logical-call trace; this profile uses the existing native_baseline trace set.",
            "The profile is an initialization reference, not a post-result tuning license.",
        ],
        "logical_call_rows": logical_rows,
        "resource_classification": {
            "rule": "input_tokens>=4096 and median_output_tokens<long_decode_cutoff => LONG_PREFILL; input_tokens<4096 => SHORT; otherwise MIXED",
            "long_decode_cutoff_quantile": long_decode_cutoff_quantile,
            "long_decode_cutoff_output_tokens": long_decode_cutoff,
        },
        "roles": roles,
    }
    return _seal(body)


def _performance_rows(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[object] = []
    performance = raw.get("performance")
    if isinstance(performance, Mapping):
        candidates.append(performance.get("per_source"))
    nested = raw.get("block_result")
    if isinstance(nested, Mapping):
        nested_perf = nested.get("performance")
        if isinstance(nested_perf, Mapping):
            candidates.append(nested_perf.get("per_source"))
    for candidate in candidates:
        if isinstance(candidate, list) and all(isinstance(row, Mapping) for row in candidate):
            return [dict(row) for row in candidate]
    raise P0BindingError("result has no per_source performance rows")


def _event_rows(events_path: Path) -> list[dict[str, Any]]:
    """Derive minimal per-source timing rows from an append-only event trace.

    The successful v3.1 wrapper intentionally stores only aggregate performance
    in its outer RESULT; its sibling events.jsonl remains the source of truth
    for this small prefix reference.
    """

    states: dict[int, dict[str, int]] = defaultdict(dict)
    try:
        lines = events_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise P0BindingError(f"events unreadable: {events_path}") from error
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise P0BindingError(f"events JSON invalid: {events_path}:{line_number}") from error
        event = value.get("event") if isinstance(value, Mapping) else None
        if not isinstance(event, Mapping):
            event = value if isinstance(value, Mapping) else {}
        sequence = event.get("source_sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            continue
        event_type = event.get("event_type")
        timestamp = _int(event.get("timestamp_ns"))
        if isinstance(event_type, str):
            states[sequence][event_type] = timestamp
    rows: list[dict[str, Any]] = []
    for sequence in sorted(states):
        state = states[sequence]
        if "ARRIVAL" not in state or "PUBLICATION_DURABLE" not in state:
            continue
        arrival = state["ARRIVAL"]
        publication = state["PUBLICATION_DURABLE"]
        start = state.get("BIND_STARTED", state.get("SERVICE_STARTED", arrival))
        rows.append(
            {
                "source_sequence": sequence,
                "arrival_timestamp_ns": arrival,
                "publication_timestamp_ns": publication,
                "freshness_ns": max(0, publication - arrival),
                "service_latency_ns": max(0, publication - start),
            }
        )
    if not rows:
        raise P0BindingError(f"events contain no complete source rows: {events_path}")
    return rows


def _performance_rows_from_path(raw: Mapping[str, Any], result_path: Path) -> list[dict[str, Any]]:
    try:
        return _performance_rows(raw)
    except P0BindingError:
        event_candidates = [result_path.parent / "events.jsonl"]
        # The v3.1 outer RESULT is one level above block-00; preserve that
        # layout without hard-coding a run identifier.
        event_candidates.extend(sorted(result_path.parent.glob("block-*/events.jsonl")))
        for events_path in event_candidates:
            if events_path.is_file():
                return _event_rows(events_path)
        raise


def _method_name(raw: Mapping[str, Any], path: Path) -> str:
    method = _result_identity(raw, "method")
    if isinstance(method, str) and method:
        return method
    return path.stem


def _prefix_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: _int(row.get("source_sequence")))
    freshness = [_int(row.get("freshness_ns")) for row in ordered]
    arrival = [_int(row.get("arrival_timestamp_ns")) for row in ordered]
    publication = [_int(row.get("publication_timestamp_ns")) for row in ordered]
    horizon = max(publication) - min(arrival) if ordered else 0
    return {
        "count": len(ordered),
        "source_sequences": [_int(row.get("source_sequence")) for row in ordered],
        "makespan_ns": horizon,
        "goodput_episodes_per_second": (len(ordered) / (horizon / 1e9)) if horizon else None,
        "freshness_ns_mean": float(statistics.mean(freshness)) if freshness else None,
        "freshness_ns_p50": _percentile(freshness, 0.50),
        "freshness_ns_p95": _percentile(freshness, 0.95),
        "freshness_ns_max": max(freshness) if freshness else None,
        "source_rows": [
            {
                key: row[key]
                for key in (
                    "source_sequence",
                    "arrival_timestamp_ns",
                    "publication_timestamp_ns",
                    "freshness_ns",
                    "service_latency_ns",
                )
                if key in row
            }
            for row in ordered
        ],
    }


def build_prefix_reference(
    *,
    v31_result_path: Path | str,
    baseline_result_paths: Iterable[Path | str],
    history_id: str = "07741c45",
) -> dict[str, Any]:
    """Build the registered six- and twelve-source offline references."""

    all_paths = [Path(v31_result_path), *[Path(value) for value in baseline_result_paths]]
    if not all_paths:
        raise P0BindingError("prefix reference requires result artifacts")
    methods: dict[str, dict[str, Any]] = {}
    for path_value in all_paths:
        path = _path(path_value)
        raw = _read_json(path)
        raw_history = _result_identity(raw, "history_id")
        if raw_history not in (None, history_id):
            continue
        rows = _performance_rows_from_path(raw, path)
        selected = [row for row in rows if _int(row.get("source_sequence")) >= 0]
        name = _method_name(raw, path)
        if name in methods:
            name = f"{name}@{path.name}"
        methods[name] = {
            "source_artifact": {
                "absolute_path": str(path),
                "sha256": sha256_file(path),
            },
            "status": _result_status(raw),
            "source_count_available": len(selected),
            "prefixes": {},
        }
        for prefix in (6, 12):
            prefix_rows = [
                row for row in selected if _int(row.get("source_sequence")) < prefix
            ]
            if len(prefix_rows) != prefix:
                raise P0BindingError(
                    f"{name} is missing source prefix 0..{prefix - 1}: {path}"
                )
            methods[name]["prefixes"][f"sources_0_{prefix - 1}"] = _prefix_metrics(prefix_rows)
    if not methods:
        raise P0BindingError(f"no result artifact matches history {history_id}")
    prefix_view: dict[str, Any] = {}
    for key in ("sources_0_5", "sources_0_11"):
        count = int(key.rsplit("_", 1)[-1]) + 1
        prefix_view[key] = {
            "source_count": count,
            "methods": {
                name: value["prefixes"][key] for name, value in methods.items()
            },
        }
    body = {
        "schema_version": PREFIX_REFERENCE_SCHEMA,
        "status": "PASS",
        "history_id": history_id,
        "methods": sorted(methods),
        "method_sources": methods,
        "prefixes": prefix_view,
        "source_of_truth": "existing_result_per_source_rows",
    }
    return _seal(body)


def write_p0_artifacts(
    output_dir: Path | str,
    *,
    v31_result_path: Path | str,
    baseline_result_paths: Iterable[Path | str],
    role_trace_paths: Iterable[Path | str],
    history_id: str = "07741c45",
) -> dict[str, Path]:
    """Write the three P0 documents atomically and return their paths."""

    target = Path(output_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    baseline = build_baseline_binding(
        v31_result_path=v31_result_path,
        baseline_result_paths=baseline_result_paths,
    )
    profile = build_role_profile(role_trace_paths)
    reference = build_prefix_reference(
        v31_result_path=v31_result_path,
        baseline_result_paths=baseline_result_paths,
        history_id=history_id,
    )
    outputs = {
        "baseline_binding": target / "BASELINE_BINDING.json",
        "role_profile": target / "ROLE_PROFILE.json",
        "prefix_reference": target / "PREFIX_REFERENCE.json",
    }
    atomic_write_json(outputs["baseline_binding"], baseline)
    atomic_write_json(outputs["role_profile"], profile)
    atomic_write_json(outputs["prefix_reference"], reference)
    return outputs


__all__ = [
    "BASELINE_BINDING_SCHEMA",
    "PREFIX_REFERENCE_SCHEMA",
    "ROLE_PROFILE_SCHEMA",
    "P0BindingError",
    "build_baseline_binding",
    "build_prefix_reference",
    "build_role_profile",
    "write_p0_artifacts",
]
