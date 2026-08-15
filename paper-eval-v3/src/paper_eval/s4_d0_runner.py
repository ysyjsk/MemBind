"""Namespace-safe, checkpointed execution core for the S4 D0 smoke."""

from __future__ import annotations

import inspect
import json
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import (
    append_jsonl_durable,
    atomic_write_json,
    finalize_envelope,
    payload_sha256,
    sha256_file,
)


CHECKPOINT_SCHEMA = "membind.paper-eval-v3.s4-phase-checkpoint.v1"
EVENT_SCHEMA = "membind.paper-eval-v3.s4-phase-event.v1"
RESULT_SCHEMA = "membind.paper-eval-v3.s4-phase-result.v1"
NAMESPACE_PLACEHOLDER = "__S4_ISOLATED_NAMESPACE__"
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,95}$")


class S4NamespaceMismatch(RuntimeError):
    """A durable S4 prefix cannot be reconciled with its live namespace."""


class S4PhaseFailed(RuntimeError):
    """A phase stopped after persisting a sanitized, non-mergeable result."""

    def __init__(self, result: Mapping[str, Any]):
        self.result = deepcopy(dict(result))
        payload = self.result.get("payload", {})
        super().__init__(
            f"S4 phase {payload.get('phase')} failed: {payload.get('error_class')}"
        )


def _sequence(item: Any) -> int:
    if isinstance(item, Mapping):
        return int(item["source_sequence"])
    return int(getattr(item, "source_sequence"))


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _validate_spec(value: Mapping[str, Any]) -> dict[str, str]:
    spec = _mapping(value, label="S4 phase spec")
    expected_fields = {
        "phase",
        "run_id",
        "history_id",
        "namespace",
        "method",
        "mode",
        "cache_id",
    }
    if set(spec) != expected_fields:
        raise ValueError("S4 phase spec shape drift")
    for field in expected_fields:
        selected = spec[field]
        if not isinstance(selected, str) or not selected or any(
            token in selected for token in ("/", "\\", "..")
        ):
            raise ValueError(f"invalid S4 phase {field}")
    expected = {
        "capture": ("U0_CAPTURE", "U0"),
        "replay": ("D0_READ_ONLY_REPLAY", "D0"),
    }
    if spec["mode"] not in expected or (
        spec["phase"], spec["method"]
    ) != expected[spec["mode"]]:
        raise ValueError("S4 phase method/mode drift")
    if not spec["namespace"].startswith("pev3-s4-"):
        raise ValueError("S4 namespace is outside the isolated prefix")
    return {key: str(value) for key, value in spec.items()}


def _hash_record(value: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body.pop("payload_sha256", None)
    body["payload_sha256"] = payload_sha256(body)
    return body


def _valid_hash(value: Mapping[str, Any]) -> bool:
    body = dict(value)
    stored = body.pop("payload_sha256", None)
    return isinstance(stored, str) and stored == payload_sha256(body)


def _namespace_nonempty(state: Mapping[str, Any]) -> bool:
    return int(state.get("node_count") or 0) != 0 or int(
        state.get("relationship_count") or 0
    ) != 0


def _event(
    *,
    path: Path,
    spec: Mapping[str, str],
    event_type: str,
    source_sequence: int | None,
    event_sink: Callable[[Mapping[str, Any]], Any] | None,
    **extra: Any,
) -> None:
    value = _hash_record(
        {
            "schema_version": EVENT_SCHEMA,
            "run_id": spec["run_id"],
            "phase": spec["phase"],
            "history_id": spec["history_id"],
            "namespace": spec["namespace"],
            "event_type": event_type,
            "source_sequence": source_sequence,
            "timestamp_ns": time.time_ns(),
            **extra,
        }
    )
    append_jsonl_durable(path, value)
    if event_sink is not None:
        event_sink(dict(value))


def _failure_fields(error: BaseException, *, stage: str) -> dict[str, str]:
    fields = {
        "error_class": type(error).__name__,
        "failure_stage": stage,
    }
    code = getattr(error, "code", None)
    if isinstance(code, str) and _ERROR_CODE.fullmatch(code) is not None:
        fields["error_code"] = code
    return fields


def _load_events(path: Path, spec: Mapping[str, str]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise S4NamespaceMismatch("event log JSON corruption") from error
        if (
            not isinstance(value, dict)
            or not _valid_hash(value)
            or value.get("schema_version") != EVENT_SCHEMA
            or value.get("run_id") != spec["run_id"]
            or value.get("phase") != spec["phase"]
            or value.get("history_id") != spec["history_id"]
            or value.get("namespace") != spec["namespace"]
        ):
            raise S4NamespaceMismatch("event log identity or hash drift")
        records.append(value)
    return records


def _base_checkpoint(spec: Mapping[str, str], state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": CHECKPOINT_SCHEMA,
        "run_id": spec["run_id"],
        "phase": spec["phase"],
        "history_id": spec["history_id"],
        "namespace": spec["namespace"],
        "method": spec["method"],
        "mode": spec["mode"],
        "cache_id": spec["cache_id"],
        "status": "running",
        "completed_source_sequences": [],
        "namespace_state": dict(state),
        "runtime_evidence_cumulative": {},
        "error_class": None,
        "canonical_graph_sha256": None,
    }


def _load_checkpoint(
    path: Path,
    *,
    spec: Mapping[str, str],
    current_state: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        if events:
            raise S4NamespaceMismatch("event log exists without checkpoint")
        if _namespace_nonempty(current_state):
            raise S4NamespaceMismatch("namespace is nonempty without checkpoint")
        return _base_checkpoint(spec, current_state), False
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise S4NamespaceMismatch("checkpoint is unreadable") from error
    if not isinstance(checkpoint, dict) or not _valid_hash(checkpoint):
        raise S4NamespaceMismatch("checkpoint payload hash mismatch")
    for field in (
        "run_id",
        "phase",
        "history_id",
        "namespace",
        "method",
        "mode",
        "cache_id",
    ):
        if checkpoint.get(field) != spec[field]:
            raise S4NamespaceMismatch(f"checkpoint {field} mismatch")
    if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA:
        raise S4NamespaceMismatch("checkpoint schema mismatch")
    if checkpoint.get("namespace_state") != dict(current_state):
        raise S4NamespaceMismatch("namespace state differs from checkpoint")
    completed = checkpoint.get("completed_source_sequences")
    if not isinstance(completed, list) or completed != list(range(len(completed))):
        raise S4NamespaceMismatch("checkpoint is not a contiguous durable prefix")
    publications = [
        event.get("source_sequence")
        for event in events
        if event.get("event_type") == "publication"
    ]
    if publications != completed:
        raise S4NamespaceMismatch("event publications differ from checkpoint")
    if not isinstance(checkpoint.get("runtime_evidence_cumulative"), dict):
        raise S4NamespaceMismatch("checkpoint runtime evidence is invalid")
    return checkpoint, True


def _merge_runtime_evidence(
    prefix: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, int]:
    """Add process-local counters to the durable pre-resume prefix."""

    base_fields = {
        "live_llm_calls",
        "live_embedding_calls",
        "resolved_prompt_count",
        "resolved_embedding_count",
        "unexpected_prompt_count",
        "unexpected_embedding_count",
        "live_fallback_count",
        "cross_encoder_call_count",
    }
    candidate_remap_fields = {
        "exact_prompt_hit_count",
        "candidate_remap_hit_count",
        "candidate_remap_node_hit_count",
        "candidate_remap_edge_hit_count",
        "candidate_remap_rejection_count",
    }
    valid_shapes = (base_fields, base_fields | candidate_remap_fields)
    if not prefix and not current:
        return {}
    current_fields = set(current)
    if current_fields not in valid_shapes or (
        prefix and set(prefix) != current_fields
    ):
        raise ValueError("S4 runtime evidence shape drift")
    result: dict[str, int] = {}
    for field in sorted(current_fields):
        prior = int(prefix.get(field, 0))
        observed = int(current[field])
        if prior < 0 or observed < 0:
            raise ValueError("S4 runtime evidence contains a negative counter")
        result[field] = prior + observed
    return result


def normalize_isolated_namespace_graph(value: Mapping[str, Any]) -> dict[str, Any]:
    """Alpha-rename only Entity group IDs in an already canonical export."""

    graph = _mapping(value, label="canonical graph")
    graph.pop("canonical_graph_hash", None)
    entities = graph.get("entities", [])
    if not isinstance(entities, list):
        raise ValueError("canonical graph entities must be a list")
    for entity in entities:
        if not isinstance(entity, dict):
            raise ValueError("canonical graph entity must be a mapping")
        entity["group_id"] = NAMESPACE_PLACEHOLDER
    return graph


async def _call(value: Callable[..., Any], *args: Any) -> Any:
    result = value(*args)
    if inspect.isawaitable(result):
        return await result
    return result


async def _close_graph(graph: Any) -> None:
    close = getattr(graph, "close", None)
    if callable(close):
        await _call(close)


def _phase_result(
    *,
    spec: Mapping[str, str],
    status: str,
    expected_episode_count: int,
    completed: Sequence[int],
    canonical_graph_sha256: str | None,
    runtime_evidence: Mapping[str, Any],
    cache_evidence: Mapping[str, Any],
    cleanup: Mapping[str, Any] | None,
    error_class: str | None,
    checkpoint_path: Path,
    events_path: Path,
    git_commit: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": RESULT_SCHEMA,
        "stage": "S4",
        "phase": spec["phase"],
        "run_id": spec["run_id"],
        "history_id": spec["history_id"],
        "namespace": spec["namespace"],
        "method": spec["method"],
        "mode": spec["mode"],
        "cache_id": spec["cache_id"],
        "status": status,
        "mergeable": status == "PASS",
        "expected_episode_count": expected_episode_count,
        "completed_source_sequences": list(completed),
        "episode_coverage": (
            len(completed) / expected_episode_count
            if expected_episode_count
            else 0.0
        ),
        "canonical_graph_sha256": canonical_graph_sha256,
        "runtime_evidence": dict(runtime_evidence),
        "cache_evidence": dict(cache_evidence),
        "cleanup": dict(cleanup) if cleanup is not None else None,
        "error_class": error_class,
        "checkpoint_sha256": sha256_file(checkpoint_path),
        "events_sha256": sha256_file(events_path),
    }
    return finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=str(git_commit),
        run_id=spec["run_id"],
    )


async def run_s4_phase(
    *,
    spec: Mapping[str, Any],
    episodes: Sequence[Any],
    graph: Any,
    episode_kwargs: Callable[[Any], Mapping[str, Any]],
    namespace_probe: Callable[[], Awaitable[Mapping[str, Any]]],
    graph_exporter: Callable[[Any, Sequence[Any], str], Awaitable[Mapping[str, Any]]],
    runtime_evidence: Callable[[], Mapping[str, Any]],
    cache_evidence: Callable[[], Mapping[str, Any]],
    cleanup_namespace: Callable[[str], Awaitable[Any]],
    artifact_root: Path,
    expected_episode_count: int,
    git_commit: str,
    event_sink: Callable[[Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Run or resume one phase, preserving a durable prefix on failure."""

    selected = _validate_spec(spec)
    sequence = [_sequence(item) for item in episodes]
    if sequence != list(range(expected_episode_count)):
        raise ValueError("S4 episodes are not the exact contiguous source sequence")
    run_dir = Path(artifact_root) / selected["run_id"]
    checkpoint_path = run_dir / "checkpoint.json"
    events_path = run_dir / "events.jsonl"
    graph_path = run_dir / "canonical_graph.json"
    result_path = run_dir / "phase_result.json"
    run_dir.mkdir(parents=True, exist_ok=True)

    checkpoint: dict[str, Any] | None = None
    completed: list[int] = []
    canonical_sha: str | None = None
    runtime: dict[str, Any] = {}
    cache: dict[str, Any] = {}
    runtime_prefix: dict[str, Any] = {}
    try:
        current_state = _mapping(
            await _call(namespace_probe), label="namespace state"
        )
        events = _load_events(events_path, selected)
        checkpoint, existed = _load_checkpoint(
            checkpoint_path,
            spec=selected,
            current_state=current_state,
            events=events,
        )
        if checkpoint.get("status") == "completed":
            if not result_path.is_file():
                raise S4NamespaceMismatch(
                    "completed checkpoint is missing its phase result"
                )
            return json.loads(result_path.read_text(encoding="utf-8"))
        completed = [int(value) for value in checkpoint["completed_source_sequences"]]
        runtime_prefix = _mapping(
            checkpoint.get("runtime_evidence_cumulative", {}),
            label="checkpoint runtime evidence",
        )
        if not existed:
            atomic_write_json(checkpoint_path, _hash_record(checkpoint))
        else:
            _event(
                path=events_path,
                spec=selected,
                event_type="resume",
                source_sequence=None,
                event_sink=event_sink,
                completed_count=len(completed),
            )

        for item in episodes[len(completed) :]:
            source_sequence = _sequence(item)
            _event(
                path=events_path,
                spec=selected,
                event_type="intent",
                source_sequence=source_sequence,
                event_sink=event_sink,
            )
            try:
                await graph.add_episode(**dict(episode_kwargs(item)))
            except Exception as error:
                _event(
                    path=events_path,
                    spec=selected,
                    event_type="failure",
                    source_sequence=source_sequence,
                    event_sink=event_sink,
                    **_failure_fields(error, stage="add_episode"),
                )
                runtime = _merge_runtime_evidence(
                    runtime_prefix,
                    _mapping(runtime_evidence(), label="runtime evidence"),
                )
                checkpoint.update(
                    status="incomplete",
                    error_class=type(error).__name__,
                    runtime_evidence_cumulative=runtime,
                )
                atomic_write_json(checkpoint_path, _hash_record(checkpoint))
                result = _phase_result(
                    spec=selected,
                    status="INCOMPLETE",
                    expected_episode_count=expected_episode_count,
                    completed=completed,
                    canonical_graph_sha256=None,
                    runtime_evidence=runtime,
                    cache_evidence=_mapping(cache_evidence(), label="cache evidence"),
                    cleanup=None,
                    error_class=type(error).__name__,
                    checkpoint_path=checkpoint_path,
                    events_path=events_path,
                    git_commit=git_commit,
                )
                atomic_write_json(result_path, result)
                raise S4PhaseFailed(result) from error

            completed.append(source_sequence)
            _event(
                path=events_path,
                spec=selected,
                event_type="publication",
                source_sequence=source_sequence,
                event_sink=event_sink,
            )
            checkpoint.update(
                status="running",
                completed_source_sequences=list(completed),
                namespace_state=_mapping(
                    await _call(namespace_probe), label="namespace state"
                ),
                runtime_evidence_cumulative=_merge_runtime_evidence(
                    runtime_prefix,
                    _mapping(runtime_evidence(), label="runtime evidence"),
                ),
                error_class=None,
            )
            atomic_write_json(checkpoint_path, _hash_record(checkpoint))

        exported = _mapping(
            await _call(graph_exporter, graph, episodes, selected["namespace"]),
            label="canonical graph export",
        )
        normalized = normalize_isolated_namespace_graph(exported)
        canonical_sha = payload_sha256(normalized)
        atomic_write_json(graph_path, normalized)
        runtime = _merge_runtime_evidence(
            runtime_prefix,
            _mapping(runtime_evidence(), label="runtime evidence"),
        )
        cache = _mapping(cache_evidence(), label="cache evidence")

        await _call(cleanup_namespace, selected["namespace"])
        post_cleanup = _mapping(
            await _call(namespace_probe), label="post-cleanup namespace state"
        )
        if _namespace_nonempty(post_cleanup):
            raise RuntimeError("namespace cleanup left live state")
        cleanup = {
            "scope": "EXACT_GROUP_ID_ONLY",
            "namespace": selected["namespace"],
            "global_cleanup_used": False,
            "post_cleanup_node_count": int(post_cleanup.get("node_count") or 0),
            "post_cleanup_relationship_count": int(
                post_cleanup.get("relationship_count") or 0
            ),
        }
        _event(
            path=events_path,
            spec=selected,
            event_type="terminal",
            source_sequence=None,
            event_sink=event_sink,
            status="PASS",
        )
        checkpoint.update(
            status="completed",
            completed_source_sequences=list(completed),
            namespace_state=post_cleanup,
            runtime_evidence_cumulative=runtime,
            error_class=None,
            canonical_graph_sha256=canonical_sha,
        )
        atomic_write_json(checkpoint_path, _hash_record(checkpoint))
        result = _phase_result(
            spec=selected,
            status="PASS",
            expected_episode_count=expected_episode_count,
            completed=completed,
            canonical_graph_sha256=canonical_sha,
            runtime_evidence=runtime,
            cache_evidence=cache,
            cleanup=cleanup,
            error_class=None,
            checkpoint_path=checkpoint_path,
            events_path=events_path,
            git_commit=git_commit,
        )
        atomic_write_json(result_path, result)
        return result
    except (S4NamespaceMismatch, S4PhaseFailed):
        raise
    except Exception as error:
        if checkpoint is None:
            raise
        _event(
            path=events_path,
            spec=selected,
            event_type="failure",
            source_sequence=None,
            event_sink=event_sink,
            **_failure_fields(error, stage="finalization"),
        )
        checkpoint.update(status="incomplete", error_class=type(error).__name__)
        if not runtime:
            try:
                runtime = _merge_runtime_evidence(
                    runtime_prefix,
                    _mapping(runtime_evidence(), label="runtime evidence"),
                )
            except Exception:
                runtime = {}
        checkpoint.update(runtime_evidence_cumulative=runtime)
        atomic_write_json(checkpoint_path, _hash_record(checkpoint))
        if not cache:
            try:
                cache = _mapping(cache_evidence(), label="cache evidence")
            except Exception:
                cache = {}
        result = _phase_result(
            spec=selected,
            status="INCOMPLETE",
            expected_episode_count=expected_episode_count,
            completed=completed,
            canonical_graph_sha256=canonical_sha,
            runtime_evidence=runtime,
            cache_evidence=cache,
            cleanup=None,
            error_class=type(error).__name__,
            checkpoint_path=checkpoint_path,
            events_path=events_path,
            git_commit=git_commit,
        )
        atomic_write_json(result_path, result)
        raise S4PhaseFailed(result) from error
    finally:
        await _close_graph(graph)


def evaluate_s4_smoke(
    *,
    capture_result: Mapping[str, Any],
    replay_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate only the frozen one-history hard gates."""

    capture = _mapping(capture_result.get("payload"), label="capture result")
    replay = _mapping(replay_result.get("payload"), label="replay result")
    failures: list[str] = []
    expected = 49
    complete_sequence = list(range(expected))
    if (
        capture.get("status") != "PASS"
        or capture.get("completed_source_sequences") != complete_sequence
        or capture.get("expected_episode_count") != expected
    ):
        failures.append("capture_episode_coverage")
    if (
        replay.get("status") != "PASS"
        or replay.get("completed_source_sequences") != complete_sequence
        or replay.get("expected_episode_count") != expected
    ):
        failures.append("replay_episode_coverage")
    graph_parity = (
        isinstance(capture.get("canonical_graph_sha256"), str)
        and capture.get("canonical_graph_sha256")
        == replay.get("canonical_graph_sha256")
    )
    if not graph_parity:
        failures.append("canonical_graph_parity")

    capture_runtime = _mapping(
        capture.get("runtime_evidence"), label="capture runtime evidence"
    )
    replay_runtime = _mapping(
        replay.get("runtime_evidence"), label="replay runtime evidence"
    )
    if int(capture_runtime.get("live_llm_calls", 0)) <= 0 or int(
        capture_runtime.get("live_embedding_calls", 0)
    ) <= 0:
        failures.append("capture_live_model_call")
    if int(replay_runtime.get("live_llm_calls", -1)) != 0 or int(
        replay_runtime.get("live_embedding_calls", -1)
    ) != 0:
        failures.append("replay_live_model_call")
    if int(replay_runtime.get("unexpected_prompt_count", -1)) != 0 or int(
        replay_runtime.get("unexpected_embedding_count", -1)
    ) != 0:
        failures.append("replay_oracle_miss")
    if int(replay_runtime.get("live_fallback_count", -1)) != 0:
        failures.append("replay_live_fallback")
    if int(replay_runtime.get("cross_encoder_call_count", -1)) != 0:
        failures.append("replay_cross_encoder_call")
    for field in ("resolved_prompt_count", "resolved_embedding_count"):
        if capture_runtime.get(field) != replay_runtime.get(field):
            failures.append(field)

    remap_fields = {
        "exact_prompt_hit_count",
        "candidate_remap_hit_count",
        "candidate_remap_node_hit_count",
        "candidate_remap_edge_hit_count",
        "candidate_remap_rejection_count",
    }
    remap_present = bool(remap_fields & set(replay_runtime))
    remap_count = 0
    remap_accounting: bool | None = None
    if remap_present:
        if not remap_fields.issubset(replay_runtime) or any(
            not isinstance(replay_runtime.get(field), int)
            or isinstance(replay_runtime.get(field), bool)
            or int(replay_runtime[field]) < 0
            for field in remap_fields
        ):
            failures.append("candidate_remap_evidence_shape")
        else:
            remap_count = int(replay_runtime["candidate_remap_hit_count"])
            if int(replay_runtime["candidate_remap_rejection_count"]) != 0:
                failures.append("candidate_remap_rejection")
            if remap_count != int(
                replay_runtime["candidate_remap_node_hit_count"]
            ) + int(replay_runtime["candidate_remap_edge_hit_count"]):
                failures.append("candidate_remap_breakdown")
            remap_accounting = (
                int(replay_runtime["exact_prompt_hit_count"]) + remap_count
                == int(replay_runtime.get("resolved_prompt_count", -1))
            )
            if not remap_accounting:
                failures.append("candidate_oracle_resolution_accounting")

    capture_cache = _mapping(
        capture.get("cache_evidence"), label="capture cache evidence"
    )
    replay_cache = _mapping(
        replay.get("cache_evidence"), label="replay cache evidence"
    )
    cache_stable = all(
        capture_cache.get(field) == replay_cache.get(field)
        for field in ("prompt_cache_sha256", "embedding_cache_sha256")
    )
    if not cache_stable:
        failures.append("cache_mutation")
    failures = list(dict.fromkeys(failures))
    passed = not failures
    return {
        "schema_version": "membind.paper-eval-v3.s4-d0-smoke-evaluation.v1",
        "verdict": "PASS" if passed else "FAIL",
        "failures": failures,
        "canonical_graph_parity": graph_parity,
        "cache_mutation_during_replay": not cache_stable,
        "candidate_remap_used": remap_count > 0,
        "candidate_remap_hit_count": remap_count,
        "candidate_oracle_resolution_accounting": remap_accounting,
        "s4_four_history_qualification_authorized": passed,
        "s5_authorized": False,
    }
