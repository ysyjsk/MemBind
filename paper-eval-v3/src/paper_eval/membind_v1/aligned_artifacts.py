"""Durable, content-safe artifacts for one fresh aligned live benchmark block.

This module deliberately has no Graphiti, scheduler, or network dependency.
It protects the shared identity needed to compare fresh U0-aligned,
P(C=2)-aligned, and MemBind-v1 rows: a verified plan block creates one fresh
root; lifecycle telemetry is append-only; and only complete, hash-bound block
coverage can become a public main-table row.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from paper_eval.artifacts import append_jsonl_durable, atomic_write_json, payload_sha256
from paper_eval.membind_v1.aligned_plan import verify_aligned_development_plan
from paper_eval.apc_aligned_baseline import (
    SCHEMA as APC_BASELINE_PLAN_SCHEMA,
    verify_apc_aligned_baseline_plan,
)


MANIFEST_SCHEMA = "membind.paper-eval-v3.membind-v1-aligned-block-manifest.v1"
EVENT_SCHEMA = "membind.paper-eval-v3.membind-v1-aligned-block-event.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.membind-v1-aligned-block-checkpoint.v1"
PUBLIC_ROW_SCHEMA = "membind.paper-eval-v3.membind-v1-aligned-public-row.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EVENT_TYPES = {
    "ARRIVAL",
    "ENQUEUED",
    "SERVICE_STARTED",
    "PUBLICATION_DURABLE",
    "TERMINAL_FAILURE",
    "AMBIGUOUS_COMMIT",
}
_TERMINAL_STATES = {
    "PUBLICATION_DURABLE",
    "TERMINAL_FAILURE",
    "AMBIGUOUS_COMMIT",
}
_PRIVATE_FIELDS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "credential",
    "episode",
    "message",
    "messages",
    "password",
    "prompt",
    "raw_output",
    "raw_response",
    "request",
    "response",
    "secret",
    "token",
}
_QUALITY_STATUSES = {
    "NUMERICALLY_COMPARABLE",
    "NQ_GRAPH_NATIVE_PROTOCOL_DEGENERATE",
}


class AlignedArtifactsError(ValueError):
    """An aligned artifact's identity, safety, or durability check failed."""


def _fail(code: str) -> AlignedArtifactsError:
    return AlignedArtifactsError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _content_safe(value: object) -> None:
    """Reject raw model/episode content before it reaches a durable trace."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("telemetry content safe violation")
            _content_safe(child)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _content_safe(child)
        return
    if value is None or isinstance(value, (str, int, bool)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise _fail("telemetry content safe violation")


def _sealed(value: Mapping[str, object], field: str) -> dict[str, object]:
    body = deepcopy(dict(value))
    body[field] = payload_sha256(body)
    return body


def _read_json(path: Path, code: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _plan_block(
    verified_plan: Mapping[str, object], block_index: object
) -> tuple[dict[str, object], dict[str, object], list[str]]:
    try:
        plan = (
            verify_apc_aligned_baseline_plan(verified_plan)
            if verified_plan.get("schema_version") == APC_BASELINE_PLAN_SCHEMA
            else verify_aligned_development_plan(verified_plan)
        )
    except ValueError:
        raise _fail("verified plan invalid") from None
    index = _nonnegative_int(block_index, "block index invalid")
    blocks = plan.get("blocks")
    if not isinstance(blocks, list) or index >= len(blocks):
        raise _fail("plan block binding invalid")
    raw_block = blocks[index]
    if not isinstance(raw_block, Mapping) or raw_block.get("block_index") != index:
        raise _fail("plan block binding invalid")
    block = deepcopy(dict(raw_block))
    history_id = block.get("history_id")
    histories = plan.get("history_source_sha256s")
    if not isinstance(history_id, str) or not isinstance(histories, Mapping):
        raise _fail("plan block binding invalid")
    raw_sources = histories.get(history_id)
    if not isinstance(raw_sources, list) or not raw_sources:
        raise _fail("plan block binding invalid")
    sources = [_sha(item, "plan block binding invalid") for item in raw_sources]
    if block.get("source_count") != len(sources):
        raise _fail("plan block binding invalid")
    return deepcopy(plan), block, sources


def _manifest_from_plan(
    *,
    plan: Mapping[str, object],
    block: Mapping[str, object],
    source_sha256s: Sequence[str],
    execution_identity_sha256: object,
) -> dict[str, object]:
    execution_identity = _sha(execution_identity_sha256, "execution identity invalid")
    body = {
        "schema_version": MANIFEST_SCHEMA,
        "aligned_run_id": block["aligned_run_id"],
        "block_index": block["block_index"],
        "method": block["method"],
        "history_id": block["history_id"],
        "namespace": block["namespace"],
        "source_sha256s": list(source_sha256s),
        "source_manifest_sha256": block["source_manifest_sha256"],
        "arrival_trace_sha256": block["arrival_trace_sha256"],
        "history_arrival_trace_sha256": block["history_arrival_trace_sha256"],
        "shared_execution_envelope_sha256": block[
            "shared_execution_envelope_sha256"
        ],
        "global_llm_admission_k": block["global_llm_admission_k"],
        "plan_payload_sha256": plan["payload_sha256"],
        "plan_block_sha256": payload_sha256(block),
        "execution_identity_sha256": execution_identity,
    }
    _content_safe(body)
    return _sealed(body, "manifest_sha256")


def _validate_manifest(value: Mapping[str, object]) -> dict[str, object]:
    manifest = deepcopy(dict(value))
    expected = {
        "schema_version",
        "aligned_run_id",
        "block_index",
        "method",
        "history_id",
        "namespace",
        "source_sha256s",
        "source_manifest_sha256",
        "arrival_trace_sha256",
        "history_arrival_trace_sha256",
        "shared_execution_envelope_sha256",
        "global_llm_admission_k",
        "plan_payload_sha256",
        "plan_block_sha256",
        "execution_identity_sha256",
        "manifest_sha256",
    }
    if set(manifest) != expected or manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise _fail("manifest invalid")
    if not isinstance(manifest.get("aligned_run_id"), str) or not manifest["aligned_run_id"]:
        raise _fail("manifest invalid")
    _nonnegative_int(manifest.get("block_index"), "manifest invalid")
    for field in ("method", "history_id", "namespace"):
        if not isinstance(manifest.get(field), str) or not manifest[field]:
            raise _fail("manifest invalid")
    hashes = manifest.get("source_sha256s")
    if (
        not isinstance(hashes, list)
        or not hashes
        or any(_SHA256.fullmatch(item or "") is None for item in hashes)
        or len(set(hashes)) != len(hashes)
    ):
        raise _fail("manifest invalid")
    for field in (
        "source_manifest_sha256",
        "arrival_trace_sha256",
        "history_arrival_trace_sha256",
        "shared_execution_envelope_sha256",
        "plan_payload_sha256",
        "plan_block_sha256",
        "execution_identity_sha256",
    ):
        _sha(manifest.get(field), "manifest invalid")
    if manifest.get("global_llm_admission_k") != 2:
        raise _fail("global LLM admission invalid")
    stored = _sha(manifest.get("manifest_sha256"), "manifest hash invalid")
    body = {key: item for key, item in manifest.items() if key != "manifest_sha256"}
    if stored != payload_sha256(body):
        raise _fail("manifest hash mismatch")
    _content_safe(manifest)
    return manifest


def _transition(states: list[str], event: Mapping[str, object]) -> None:
    sequence = _nonnegative_int(event.get("source_sequence"), "event source invalid")
    if sequence >= len(states):
        raise _fail("event source invalid")
    event_type = event.get("event_type")
    state = states[sequence]
    allowed = {
        "ARRIVAL": {"NEW"},
        "ENQUEUED": {"ARRIVAL"},
        "SERVICE_STARTED": {"ENQUEUED"},
        "PUBLICATION_DURABLE": {"SERVICE_STARTED"},
        "TERMINAL_FAILURE": {"ARRIVAL", "ENQUEUED", "SERVICE_STARTED"},
        "AMBIGUOUS_COMMIT": {"SERVICE_STARTED"},
    }
    if event_type not in allowed or state not in allowed[event_type]:
        raise _fail("lifecycle transition invalid")
    states[sequence] = str(event_type)


def _read_events(path: Path, manifest: Mapping[str, object]) -> list[dict[str, object]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise _fail("events unreadable") from None
    sources = list(manifest["source_sha256s"])
    events: list[dict[str, object]] = []
    last_timestamps = [-1] * len(sources)
    states = ["NEW"] * len(sources)
    for sequence, line in enumerate(lines):
        try:
            wrapped = json.loads(line)
        except (UnicodeError, json.JSONDecodeError):
            raise _fail("event invalid") from None
        if not isinstance(wrapped, dict) or set(wrapped) != {"event", "event_sha256"}:
            raise _fail("event invalid")
        event = wrapped.get("event")
        if not isinstance(event, dict) or wrapped.get("event_sha256") != payload_sha256(event):
            raise _fail("event hash mismatch")
        expected = {
            "schema_version",
            "event_sequence",
            "source_sequence",
            "source_sha256",
            "event_type",
            "timestamp_ns",
            "telemetry",
        }
        if set(event) != expected or event.get("schema_version") != EVENT_SCHEMA:
            raise _fail("event invalid")
        if event.get("event_sequence") != sequence:
            raise _fail("event sequence invalid")
        source_sequence = _nonnegative_int(event.get("source_sequence"), "event source invalid")
        if source_sequence >= len(sources) or event.get("source_sha256") != sources[source_sequence]:
            raise _fail("event source invalid")
        timestamp = _nonnegative_int(event.get("timestamp_ns"), "event timestamp invalid")
        if timestamp < last_timestamps[source_sequence]:
            raise _fail("event timestamp invalid")
        last_timestamps[source_sequence] = timestamp
        if not isinstance(event.get("telemetry"), dict):
            raise _fail("telemetry content safe violation")
        _content_safe(event)
        _transition(states, event)
        events.append({**event, "event_sha256": wrapped["event_sha256"]})
    return events


def _checkpoint(
    *, manifest: Mapping[str, object], events: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    states = ["NEW"] * len(manifest["source_sha256s"])
    for event in events:
        _transition(states, event)
    prefix = -1
    for index, state in enumerate(states):
        if state != "PUBLICATION_DURABLE":
            break
        prefix = index
    complete_coverage = all(state == "PUBLICATION_DURABLE" for state in states)
    if complete_coverage:
        terminal_status = "COMPLETED"
        resume_status = "NOT_NEEDED_COMPLETE"
    elif "AMBIGUOUS_COMMIT" in states:
        terminal_status = "INCOMPLETE_NON_MERGEABLE"
        resume_status = "AMBIGUOUS_COMMIT_POISONED"
    elif "TERMINAL_FAILURE" in states:
        terminal_status = "INCOMPLETE_NON_MERGEABLE"
        resume_status = "TERMINAL_FAILURE_NON_MERGEABLE"
    else:
        terminal_status = "RUNNING"
        resume_status = "SAFE_TO_RESUME"
    return _sealed(
        {
            "schema_version": CHECKPOINT_SCHEMA,
            "manifest_sha256": manifest["manifest_sha256"],
            "event_count": len(events),
            "terminal_status": terminal_status,
            "completed_source_prefix": prefix,
            "complete_coverage": complete_coverage,
            "source_states": states,
            "resume_status": resume_status,
        },
        "checkpoint_sha256",
    )


def _validate_checkpoint(
    value: Mapping[str, object], *, manifest: Mapping[str, object], events: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    checkpoint = deepcopy(dict(value))
    expected_keys = {
        "schema_version",
        "manifest_sha256",
        "event_count",
        "terminal_status",
        "completed_source_prefix",
        "complete_coverage",
        "source_states",
        "resume_status",
        "checkpoint_sha256",
    }
    if set(checkpoint) != expected_keys or checkpoint.get("schema_version") != CHECKPOINT_SCHEMA:
        raise _fail("checkpoint invalid")
    stored = _sha(checkpoint.get("checkpoint_sha256"), "checkpoint hash invalid")
    body = {key: item for key, item in checkpoint.items() if key != "checkpoint_sha256"}
    if stored != payload_sha256(body):
        raise _fail("checkpoint hash mismatch")
    expected = _checkpoint(manifest=manifest, events=events)
    if checkpoint != expected:
        raise _fail("checkpoint state mismatch")
    return checkpoint


def _binding_matches(
    manifest: Mapping[str, object], *, plan: Mapping[str, object], block: Mapping[str, object], source_sha256s: Sequence[str]
) -> None:
    expected = _manifest_from_plan(
        plan=plan,
        block=block,
        source_sha256s=source_sha256s,
        execution_identity_sha256=manifest.get("execution_identity_sha256"),
    )
    # The execution identity is external to the plan, but all other manifest
    # fields must match exactly.  Rebuild then retain the stored correct seal.
    if manifest != expected:
        raise _fail("plan block binding invalid")


class AlignedBlockArtifactStore:
    """Durable per-block lifecycle writer with no live-runtime dependency."""

    def __init__(self, root: Path, *, manifest: Mapping[str, object], events: Sequence[Mapping[str, object]]) -> None:
        self.root = Path(root)
        self.manifest = deepcopy(dict(manifest))
        self.events_path = self.root / "events.jsonl"
        self.checkpoint_path = self.root / "checkpoint.json"
        self.manifest_path = self.root / "manifest.json"
        self._events = [deepcopy(dict(event)) for event in events]

    @property
    def source_count(self) -> int:
        return len(self.manifest["source_sha256s"])

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        verified_plan: Mapping[str, object],
        block_index: int,
        execution_identity_sha256: str,
    ) -> "AlignedBlockArtifactStore":
        plan, block, sources = _plan_block(verified_plan, block_index)
        # Validate every identity before reserving a fresh root so a malformed
        # caller cannot leave a partially initialized namespace behind.
        manifest = _manifest_from_plan(
            plan=plan,
            block=block,
            source_sha256s=sources,
            execution_identity_sha256=execution_identity_sha256,
        )
        target = Path(root)
        try:
            target.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise _fail("aligned block root already exists") from None
        atomic_write_json(target / "manifest.json", manifest)
        descriptor = os.open(target / "events.jsonl", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        store = cls(target, manifest=manifest, events=())
        store._write_checkpoint()
        return store

    @classmethod
    def open_existing(cls, root: Path) -> "AlignedBlockArtifactStore":
        checked = inspect_aligned_block_artifacts(root)
        resume_status = checked["checkpoint"]["resume_status"]
        if resume_status == "AMBIGUOUS_COMMIT_POISONED":
            raise _fail("ambiguous commit cannot resume")
        if resume_status != "SAFE_TO_RESUME":
            raise _fail("aligned block cannot resume")
        return cls(Path(root), manifest=checked["manifest"], events=checked["events"])

    def _write_checkpoint(self) -> None:
        atomic_write_json(
            self.checkpoint_path,
            _checkpoint(manifest=self.manifest, events=self._events),
        )

    def append_lifecycle(
        self,
        source_sequence: int,
        *,
        event_type: str,
        timestamp_ns: int,
        telemetry: Mapping[str, object] | None = None,
    ) -> None:
        sequence = _nonnegative_int(source_sequence, "event source invalid")
        if sequence >= self.source_count:
            raise _fail("event source invalid")
        if event_type not in _EVENT_TYPES:
            raise _fail("lifecycle transition invalid")
        timestamp = _nonnegative_int(timestamp_ns, "event timestamp invalid")
        if telemetry is not None and not isinstance(telemetry, Mapping):
            raise _fail("telemetry content safe violation")
        telemetry_data = {} if telemetry is None else deepcopy(dict(telemetry))
        _content_safe(telemetry_data)
        event = {
            "schema_version": EVENT_SCHEMA,
            "event_sequence": len(self._events),
            "source_sequence": sequence,
            "source_sha256": self.manifest["source_sha256s"][sequence],
            "event_type": event_type,
            "timestamp_ns": timestamp,
            "telemetry": telemetry_data,
        }
        # Replaying the prospective append checks both state and per-source
        # timestamp order before any durable side effect is made.
        _read_like_events(self.manifest, [*self._events, {**event, "event_sha256": payload_sha256(event)}])
        append_jsonl_durable(
            self.events_path,
            {"event": event, "event_sha256": payload_sha256(event)},
        )
        self._events.append({**event, "event_sha256": payload_sha256(event)})
        self._write_checkpoint()


def _read_like_events(manifest: Mapping[str, object], events: Sequence[Mapping[str, object]]) -> None:
    """Validate in-memory event documents with the same rules as JSONL readback."""

    sources = list(manifest["source_sha256s"])
    states = ["NEW"] * len(sources)
    last_timestamps = [-1] * len(sources)
    for expected_sequence, item in enumerate(events):
        if item.get("event_sequence") != expected_sequence:
            raise _fail("event sequence invalid")
        source = _nonnegative_int(item.get("source_sequence"), "event source invalid")
        if source >= len(sources) or item.get("source_sha256") != sources[source]:
            raise _fail("event source invalid")
        timestamp = _nonnegative_int(item.get("timestamp_ns"), "event timestamp invalid")
        if timestamp < last_timestamps[source]:
            raise _fail("event timestamp invalid")
        last_timestamps[source] = timestamp
        if item.get("event_type") not in _EVENT_TYPES or not isinstance(item.get("telemetry"), dict):
            raise _fail("lifecycle transition invalid")
        _content_safe(item)
        _transition(states, item)


def inspect_aligned_block_artifacts(root: Path) -> dict[str, Any]:
    """Fail-closed inspection of a durable aligned block without live services."""

    target = Path(root)
    manifest = _validate_manifest(_read_json(target / "manifest.json", "manifest unreadable"))
    events = _read_events(target / "events.jsonl", manifest)
    checkpoint = _validate_checkpoint(
        _read_json(target / "checkpoint.json", "checkpoint unreadable"),
        manifest=manifest,
        events=events,
    )
    return {"manifest": manifest, "events": events, "checkpoint": checkpoint}


def _validate_public_metrics(value: Mapping[str, object]) -> dict[str, object]:
    metrics = deepcopy(dict(value))
    _content_safe(metrics)
    required = {
        "qa_accuracy",
        "evidence_recall_at_10",
        "direct_violations",
        "p95_arrival_to_publication_ns",
        "p99_arrival_to_publication_ns",
        "successful_goodput_episodes_per_second",
        "makespan_ns",
        "max_backlog",
    }
    if set(metrics) != required:
        raise _fail("public metrics invalid")
    return metrics


def _public_row_body(
    *, inspected: Mapping[str, object], plan: Mapping[str, object], block: Mapping[str, object], source_sha256s: Sequence[str], metrics: Mapping[str, object], quality_status: str
) -> dict[str, object]:
    manifest = inspected["manifest"]
    checkpoint = inspected["checkpoint"]
    if not isinstance(manifest, Mapping) or not isinstance(checkpoint, Mapping):
        raise _fail("aligned artifact invalid")
    _binding_matches(manifest, plan=plan, block=block, source_sha256s=source_sha256s)
    if (
        checkpoint.get("terminal_status") != "COMPLETED"
        or checkpoint.get("complete_coverage") is not True
        or checkpoint.get("completed_source_prefix") != len(source_sha256s) - 1
    ):
        raise _fail("complete coverage required")
    if quality_status not in _QUALITY_STATUSES:
        raise _fail("quality status invalid")
    return {
        "schema_version": PUBLIC_ROW_SCHEMA,
        "aligned_run_id": block["aligned_run_id"],
        "block_index": block["block_index"],
        "method": block["method"],
        "history_id": block["history_id"],
        "source_count": len(source_sha256s),
        "source_manifest_sha256": block["source_manifest_sha256"],
        "arrival_trace_sha256": block["arrival_trace_sha256"],
        "history_arrival_trace_sha256": block["history_arrival_trace_sha256"],
        "shared_execution_envelope_sha256": block[
            "shared_execution_envelope_sha256"
        ],
        "global_llm_admission_k": block["global_llm_admission_k"],
        "plan_payload_sha256": plan["payload_sha256"],
        "plan_block_sha256": payload_sha256(block),
        "manifest_sha256": manifest["manifest_sha256"],
        "execution_identity_sha256": manifest["execution_identity_sha256"],
        "checkpoint_sha256": checkpoint["checkpoint_sha256"],
        "execution_status": "COMPLETED",
        "validity_status": "VALID",
        "quality_status": quality_status,
        "metrics": _validate_public_metrics(metrics),
    }


def build_public_aligned_row(
    root: Path,
    *,
    verified_plan: Mapping[str, object],
    block_index: int,
    metrics: Mapping[str, object],
    quality_status: str,
) -> dict[str, object]:
    """Emit one hash-bound main-table-compatible row after complete coverage."""

    plan, block, source_sha256s = _plan_block(verified_plan, block_index)
    inspected = inspect_aligned_block_artifacts(root)
    body = _public_row_body(
        inspected=inspected,
        plan=plan,
        block=block,
        source_sha256s=source_sha256s,
        metrics=metrics,
        quality_status=quality_status,
    )
    _content_safe(body)
    return _sealed(body, "row_sha256")


def verify_public_aligned_row(
    value: Mapping[str, object], *, verified_plan: Mapping[str, object], block_index: int
) -> dict[str, object]:
    """Verify the public seal and all plan-block identities before table use."""

    row = deepcopy(dict(value))
    stored = _sha(row.get("row_sha256"), "public row hash invalid")
    body = {key: item for key, item in row.items() if key != "row_sha256"}
    if stored != payload_sha256(body):
        raise _fail("public row hash mismatch")
    plan, block, sources = _plan_block(verified_plan, block_index)
    expected_keys = {
        "schema_version",
        "aligned_run_id",
        "block_index",
        "method",
        "history_id",
        "source_count",
        "source_manifest_sha256",
        "arrival_trace_sha256",
        "history_arrival_trace_sha256",
        "shared_execution_envelope_sha256",
        "global_llm_admission_k",
        "plan_payload_sha256",
        "plan_block_sha256",
        "manifest_sha256",
        "execution_identity_sha256",
        "checkpoint_sha256",
        "execution_status",
        "validity_status",
        "quality_status",
        "metrics",
        "row_sha256",
    }
    if set(row) != expected_keys or row.get("schema_version") != PUBLIC_ROW_SCHEMA:
        raise _fail("public row invalid")
    _content_safe(row)
    expected_identities = {
        "aligned_run_id": block["aligned_run_id"],
        "block_index": block["block_index"],
        "method": block["method"],
        "history_id": block["history_id"],
        "source_count": len(sources),
        "source_manifest_sha256": block["source_manifest_sha256"],
        "arrival_trace_sha256": block["arrival_trace_sha256"],
        "history_arrival_trace_sha256": block["history_arrival_trace_sha256"],
        "shared_execution_envelope_sha256": block[
            "shared_execution_envelope_sha256"
        ],
        "global_llm_admission_k": 2,
        "plan_payload_sha256": plan["payload_sha256"],
        "plan_block_sha256": payload_sha256(block),
    }
    if any(row.get(key) != expected for key, expected in expected_identities.items()):
        raise _fail("plan block binding invalid")
    expected_manifest = _manifest_from_plan(
        plan=plan,
        block=block,
        source_sha256s=sources,
        execution_identity_sha256=row.get("execution_identity_sha256"),
    )
    if row.get("manifest_sha256") != expected_manifest["manifest_sha256"]:
        raise _fail("plan block binding invalid")
    if row.get("execution_status") != "COMPLETED" or row.get("validity_status") != "VALID":
        raise _fail("public row invalid")
    _sha(row.get("manifest_sha256"), "public row invalid")
    _sha(row.get("execution_identity_sha256"), "public row invalid")
    _sha(row.get("checkpoint_sha256"), "public row invalid")
    _validate_public_metrics(_metrics_from_row(row))
    if row.get("quality_status") not in _QUALITY_STATUSES:
        raise _fail("quality status invalid")
    return row


def _metrics_from_row(row: Mapping[str, object]) -> Mapping[str, object]:
    metrics = row.get("metrics")
    if not isinstance(metrics, Mapping):
        raise _fail("public metrics invalid")
    return metrics


__all__ = [
    "AlignedArtifactsError",
    "AlignedBlockArtifactStore",
    "build_public_aligned_row",
    "inspect_aligned_block_artifacts",
    "verify_public_aligned_row",
]
