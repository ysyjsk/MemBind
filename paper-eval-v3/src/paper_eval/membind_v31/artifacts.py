"""Durable, content-safe artifacts for one MemBind v3.1 live block."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from paper_eval.artifacts import append_jsonl_durable, atomic_write_json, payload_sha256
from paper_eval.membind_v31.method_plan import verify_membind_v31_method_plan
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact, PreparedArtifactError


MANIFEST_SCHEMA = "membind.paper-eval-v3.membind-v31-block-manifest.v1"
EVENT_SCHEMA = "membind.paper-eval-v3.membind-v31-block-event.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.membind-v31-block-checkpoint.v1"
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
_TRANSITIONS = {
    "ARRIVAL": {"NEW"},
    "COMPILE_STARTED": {"ARRIVAL"},
    "PREPARED_DURABLE": {"COMPILE_STARTED"},
    "BIND_STARTED": {"PREPARED_DURABLE"},
    "COMMIT_RETURNED": {"BIND_STARTED"},
    "PUBLICATION_DURABLE": {"COMMIT_RETURNED"},
    "TERMINAL_FAILURE": {
        "NEW",
        "ARRIVAL",
        "COMPILE_STARTED",
        "PREPARED_DURABLE",
        "BIND_STARTED",
        "COMMIT_RETURNED",
    },
}


class MemBindV31ArtifactsError(ValueError):
    """A v3.1 artifact identity, transition, or safety check failed."""


def _fail(code: str) -> MemBindV31ArtifactsError:
    return MemBindV31ArtifactsError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise _fail(code)
    return value


def _nonnegative(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _positive(value: object, code: str) -> int:
    result = _nonnegative(value, code)
    if result == 0:
        raise _fail(code)
    return result


def _content_safe(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("content_safe_violation")
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
    raise _fail("content_safe_violation")


def _sealed(body: Mapping[str, object], field: str) -> dict[str, object]:
    result = deepcopy(dict(body))
    result[field] = payload_sha256(result)
    return result


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _validate_manifest(value: Mapping[str, object]) -> dict[str, object]:
    manifest = deepcopy(dict(value))
    stored = _sha(manifest.get("manifest_sha256"), "manifest_hash_invalid")
    body = {key: child for key, child in manifest.items() if key != "manifest_sha256"}
    if stored != payload_sha256(body):
        raise _fail("manifest_hash_mismatch")
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise _fail("manifest_invalid")
    source_hashes = manifest.get("source_sha256s")
    compile_hashes = manifest.get("compile_source_sha256s")
    if (
        not isinstance(source_hashes, list)
        or not source_hashes
        or any(not isinstance(value, str) or len(value) != 64 for value in source_hashes)
        or manifest.get("source_count") != len(source_hashes)
        or not isinstance(compile_hashes, list)
        or len(compile_hashes) != len(source_hashes)
        or any(not isinstance(value, str) or len(value) != 64 for value in compile_hashes)
    ):
        raise _fail("manifest_invalid")
    for field in (
        "plan_payload_sha256",
        "plan_block_sha256",
        "execution_identity_sha256",
        "state_cut_certification_sha256",
        "source_manifest_sha256",
        "arrival_trace_sha256",
        "history_arrival_trace_sha256",
        "shared_execution_envelope_sha256",
    ):
        _sha(manifest.get(field), "manifest_invalid")
    _positive(manifest.get("compile_workers"), "manifest_invalid")
    _nonnegative(manifest.get("lookahead"), "manifest_invalid")
    if manifest.get("global_llm_admission_k") != 2:
        raise _fail("manifest_invalid")
    _content_safe(manifest)
    return manifest


def _transition(states: list[str], event_type: str, sequence: int) -> None:
    if sequence >= len(states):
        raise _fail("lifecycle_source_invalid")
    allowed = _TRANSITIONS.get(event_type)
    if allowed is None or states[sequence] not in allowed:
        raise _fail("lifecycle_transition_invalid")
    states[sequence] = event_type


def _checkpoint(manifest: Mapping[str, object], states: list[str], event_count: int) -> dict[str, object]:
    prefix = -1
    for index, state in enumerate(states):
        if state != "PUBLICATION_DURABLE":
            break
        prefix = index
    complete = all(state == "PUBLICATION_DURABLE" for state in states)
    failed = any(state == "TERMINAL_FAILURE" for state in states)
    ambiguous = any(state == "COMMIT_RETURNED" for state in states)
    body = {
        "schema_version": CHECKPOINT_SCHEMA,
        "manifest_sha256": manifest["manifest_sha256"],
        "event_count": event_count,
        "source_states": list(states),
        "completed_source_prefix": prefix,
        "complete_coverage": complete,
        "terminal_status": (
            "COMPLETED" if complete else "INCOMPLETE_NON_MERGEABLE" if failed else "RUNNING"
        ),
        "resume_status": (
            "NOT_NEEDED_COMPLETE"
            if complete
            else "AMBIGUOUS_COMMIT_POISONED"
            if ambiguous
            else "INCOMPLETE_NON_MERGEABLE"
            if failed
            else "PRE_COMMIT_INCOMPLETE"
        ),
    }
    return _sealed(body, "checkpoint_sha256")


class V31BlockStore:
    """Append-only lifecycle plus private durable Prepared Artifacts."""

    def __init__(self, root: Path, manifest: dict[str, object], states: list[str], event_count: int) -> None:
        self.root = root
        self.manifest = manifest
        self._states = states
        self._event_count = event_count

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        verified_plan: Mapping[str, object],
        block_index: int,
        execution_identity_sha256: str,
        state_cut_certification_sha256: str,
        compile_workers: int,
        lookahead: int,
        compile_source_sha256s: Sequence[str] | None = None,
    ) -> "V31BlockStore":
        try:
            plan = verify_membind_v31_method_plan(verified_plan)
        except ValueError:
            raise _fail("verified_plan_invalid") from None
        index = _nonnegative(block_index, "block_index_invalid")
        blocks = plan["blocks"]
        if index >= len(blocks) or blocks[index].get("block_index") != index:
            raise _fail("plan_block_invalid")
        block = blocks[index]
        sources = plan["history_source_sha256s"][block["history_id"]]
        compile_sources = list(sources) if compile_source_sha256s is None else list(compile_source_sha256s)
        if (
            len(compile_sources) != len(sources)
            or any(not isinstance(value, str) for value in compile_sources)
        ):
            raise _fail("compile_source_inventory_invalid")
        compile_sources = [
            _sha(value, "compile_source_inventory_invalid") for value in compile_sources
        ]
        target = Path(root)
        try:
            target.mkdir(parents=True, exist_ok=False)
            (target / "private" / "prepared").mkdir(parents=True)
        except FileExistsError:
            raise _fail("block_root_exists") from None
        body = {
            "schema_version": MANIFEST_SCHEMA,
            "run_id": plan["run_id"],
            "block_index": index,
            "method": block["method"],
            "policy": block["policy"],
            "history_id": block["history_id"],
            "namespace": block["namespace"],
            "source_count": len(sources),
            "source_sha256s": list(sources),
            "compile_source_sha256s": compile_sources,
            "source_manifest_sha256": block["source_manifest_sha256"],
            "arrival_trace_sha256": block["arrival_trace_sha256"],
            "history_arrival_trace_sha256": block["history_arrival_trace_sha256"],
            "shared_execution_envelope_sha256": block["shared_execution_envelope_sha256"],
            "global_llm_admission_k": block["global_llm_admission_k"],
            "compile_workers": _positive(compile_workers, "compile_workers_invalid"),
            "lookahead": _nonnegative(lookahead, "lookahead_invalid"),
            "plan_payload_sha256": plan["payload_sha256"],
            "plan_block_sha256": payload_sha256(block),
            "execution_identity_sha256": _sha(
                execution_identity_sha256, "execution_identity_invalid"
            ),
            "state_cut_certification_sha256": _sha(
                state_cut_certification_sha256, "state_cut_certification_invalid"
            ),
        }
        manifest = _sealed(body, "manifest_sha256")
        atomic_write_json(target / "manifest.json", manifest)
        states = ["NEW"] * len(sources)
        atomic_write_json(target / "checkpoint.json", _checkpoint(manifest, states, 0))
        return cls(target, manifest, states, 0)

    @property
    def checkpoint(self) -> dict[str, object]:
        return _checkpoint(self.manifest, self._states, self._event_count)

    def persist_prepared(self, artifact: PreparedArtifact) -> None:
        if not isinstance(artifact, PreparedArtifact):
            raise _fail("prepared_artifact_invalid")
        sequence = artifact.source_sequence
        if sequence >= len(self._states):
            raise _fail("prepared_artifact_source_invalid")
        try:
            artifact.verify(
                expected_source_sha256=self.manifest["compile_source_sha256s"][sequence],
                expected_certification_sha256=self.manifest[
                    "state_cut_certification_sha256"
                ],
            )
        except PreparedArtifactError:
            raise _fail("prepared_artifact_invalid") from None
        path = self.root / "private" / "prepared" / f"{sequence:08d}.json"
        if path.exists():
            raise _fail("prepared_artifact_exists")
        atomic_write_json(path, artifact.to_document())

    def append_lifecycle(
        self,
        source_sequence: int,
        event_type: str,
        timestamp_ns: int,
        telemetry: Mapping[str, object] | None = None,
    ) -> None:
        sequence = _nonnegative(source_sequence, "lifecycle_source_invalid")
        timestamp = _nonnegative(timestamp_ns, "lifecycle_timestamp_invalid")
        if not isinstance(event_type, str):
            raise _fail("lifecycle_event_invalid")
        selected_telemetry = {} if telemetry is None else deepcopy(dict(telemetry))
        _content_safe(selected_telemetry)
        _transition(self._states, event_type, sequence)
        if event_type == "PREPARED_DURABLE":
            prepared = self.root / "private" / "prepared" / f"{sequence:08d}.json"
            if not prepared.is_file():
                self._states[sequence] = "COMPILE_STARTED"
                raise _fail("prepared_artifact_missing")
        body = {
            "schema_version": EVENT_SCHEMA,
            "event_sequence": self._event_count,
            "source_sequence": sequence,
            "source_sha256": self.manifest["source_sha256s"][sequence],
            "event_type": event_type,
            "timestamp_ns": timestamp,
            "telemetry": selected_telemetry,
        }
        wrapper = {"event": body, "event_sha256": payload_sha256(body)}
        append_jsonl_durable(self.root / "events.jsonl", wrapper)
        self._event_count += 1
        atomic_write_json(self.root / "checkpoint.json", self.checkpoint)

    def append_telemetry(self, stream: str, row: Mapping[str, object]) -> None:
        if stream not in {"llm", "embedding", "db", "graph_work", "queue", "direct_violations"}:
            raise _fail("telemetry_stream_invalid")
        value = deepcopy(dict(row))
        _content_safe(value)
        body = {
            "schema_version": f"membind.paper-eval-v3.membind-v31-{stream}.v1",
            "row": value,
        }
        append_jsonl_durable(
            self.root / f"{stream}.jsonl",
            {"record": body, "record_sha256": payload_sha256(body)},
        )


def inspect_v31_block(root: Path) -> dict[str, object]:
    target = Path(root)
    manifest = _validate_manifest(_read_json(target / "manifest.json", "manifest_invalid"))
    states = ["NEW"] * int(manifest["source_count"])
    events: list[dict[str, object]] = []
    events_path = target / "events.jsonl"
    if events_path.is_file():
        try:
            lines = events_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            raise _fail("events_invalid") from None
        for expected_sequence, line in enumerate(lines):
            try:
                wrapper = json.loads(line)
            except json.JSONDecodeError:
                raise _fail("events_invalid") from None
            if not isinstance(wrapper, dict) or set(wrapper) != {"event", "event_sha256"}:
                raise _fail("events_invalid")
            event = wrapper["event"]
            if not isinstance(event, dict) or wrapper["event_sha256"] != payload_sha256(event):
                raise _fail("event_hash_mismatch")
            if event.get("event_sequence") != expected_sequence:
                raise _fail("event_sequence_invalid")
            sequence = _nonnegative(event.get("source_sequence"), "lifecycle_source_invalid")
            if event.get("source_sha256") != manifest["source_sha256s"][sequence]:
                raise _fail("event_source_identity_invalid")
            _content_safe(event.get("telemetry"))
            _transition(states, str(event.get("event_type")), sequence)
            if event.get("event_type") == "PREPARED_DURABLE":
                document = _read_json(
                    target / "private" / "prepared" / f"{sequence:08d}.json",
                    "prepared_artifact_invalid",
                )
                if document.get("artifact_sha256") != payload_sha256(
                    {key: value for key, value in document.items() if key != "artifact_sha256"}
                ):
                    raise _fail("prepared_artifact_hash_mismatch")
            events.append(event)
    expected_checkpoint = _checkpoint(manifest, states, len(events))
    checkpoint = _read_json(target / "checkpoint.json", "checkpoint_invalid")
    if checkpoint != expected_checkpoint:
        raise _fail("checkpoint_drift")
    return {
        "manifest": manifest,
        "events": events,
        "checkpoint": checkpoint,
        "source_states": states,
    }


__all__ = [
    "MemBindV31ArtifactsError",
    "V31BlockStore",
    "inspect_v31_block",
]
