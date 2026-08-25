"""Observer-only block state machine and hash-bound artifact materializer."""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import os
import re
import time
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .graphiti_observer import BackendProjection, build_projection_delta
from .graphiti_observer import (
    GraphitiCaptureInstallation,
    build_semantic_cost_dag,
    build_to_seam_async,
    canonical_digest,
    load_backend_projection_async,
)
from .gates import evaluate_opportunity_gates
from .characterization import (
    audit_r1_assumptions,
    build_r2_causal_trace,
    characterize_r3_blocks,
)


class ObserverArtifactError(RuntimeError):
    pass


_NAMESPACE_SAFE = re.compile(r"[^a-z0-9-]+")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _error_chain(error: BaseException) -> tuple[BaseException, ...]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return tuple(chain)


def classify_observer_failure(error: BaseException) -> dict[str, Any]:
    """Classify an attempt failure without retaining exception text or payloads."""

    types = {
        f"{type(item).__module__}.{type(item).__qualname__}" for item in _error_chain(error)
    }
    names = {name.rsplit(".", 1)[-1] for name in types}
    if any(isinstance(item, TimeoutError) for item in _error_chain(error)) or names & {
        "APITimeoutError",
        "ReadTimeout",
        "ConnectTimeout",
        "PoolTimeout",
        "WriteTimeout",
    }:
        failure_class = "INFRASTRUCTURE_PROVIDER_TIMEOUT"
    elif names & {"APIConnectionError", "ConnectError", "NetworkError"}:
        failure_class = "INFRASTRUCTURE_PROVIDER_CONNECTION"
    elif names & {"RateLimitError"}:
        failure_class = "INFRASTRUCTURE_PROVIDER_RATE_LIMIT"
    elif names & {"InternalServerError", "BadGatewayError", "ServiceUnavailableError"}:
        failure_class = "INFRASTRUCTURE_PROVIDER_SERVICE"
    elif "JSONDecodeError" in names:
        failure_class = "INFRASTRUCTURE_PROVIDER_STRUCTURED_OUTPUT_INVALID"
    elif any(isinstance(item, ObserverArtifactError) for item in _error_chain(error)):
        failure_class = "OBSERVER_PROTOCOL_OR_ARTIFACT_FAILURE"
    else:
        failure_class = "OBSERVER_RUNTIME_FAILURE"
    return {
        "failure_class": failure_class,
        "attempt_validity": "INVALID_FOR_R1_R3_GATES",
        "replacement_eligible": True,
        "gate_outcome": "NOT_EVALUATED",
        "selected_method": None,
    }


def _journal_row(**fields: Any) -> bytes:
    return (
        json.dumps(fields, ensure_ascii=True, sort_keys=True, allow_nan=False).encode("ascii")
        + b"\n"
    )


class ObserverAttemptJournal:
    """Exclusive append-only attempt evidence; rows use a fixed sanitized schema."""

    def __init__(
        self,
        path: Path,
        descriptor: int,
        *,
        run_id: str,
        protocol_sha256: str,
        output_root_name: str,
        replacement_of: str | None,
    ) -> None:
        self.path = path
        self._descriptor: int | None = descriptor
        self.run_id = run_id
        self.protocol_sha256 = protocol_sha256
        self.output_root_name = output_root_name
        self.replacement_of = replacement_of

    @classmethod
    def create(
        cls,
        path: str | Path,
        *,
        run_id: str,
        protocol_sha256: str,
        output_root_name: str,
        replacement_of: str | None = None,
    ) -> "ObserverAttemptJournal":
        target = Path(path)
        if (
            not run_id
            or not output_root_name
            or _SHA256_RE.fullmatch(protocol_sha256) is None
            or replacement_of == run_id
        ):
            raise ObserverArtifactError("observer attempt identity is invalid")
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            target,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_APPEND,
            0o600,
        )
        journal = cls(
            target,
            descriptor,
            run_id=run_id,
            protocol_sha256=protocol_sha256,
            output_root_name=output_root_name,
            replacement_of=replacement_of,
        )
        try:
            journal._append(
                event="ATTEMPT_START",
                completed_block_count=0,
                treatment_calls=0,
                response_replay_calls=0,
            )
        except BaseException:
            journal.close()
            raise
        return journal

    def _append(self, **fields: Any) -> None:
        if self._descriptor is None:
            raise ObserverArtifactError("observer attempt journal is closed")
        payload = _journal_row(
            schema_version="membind.v7.observer-attempt-event.v1",
            run_id=self.run_id,
            protocol_sha256=self.protocol_sha256,
            output_root_name=self.output_root_name,
            replacement_of=self.replacement_of,
            monotonic_ns=time.monotonic_ns(),
            **fields,
        )
        view = memoryview(payload)
        while view:
            written = os.write(self._descriptor, view)
            if written <= 0:
                raise OSError("observer attempt journal write made no progress")
            view = view[written:]
        os.fsync(self._descriptor)

    def record_progress(
        self,
        *,
        event: str,
        block_id: str,
        completed_block_count: int,
    ) -> None:
        if event not in {"BLOCK_START", "BLOCK_COMPLETE"}:
            raise ObserverArtifactError("observer progress event is invalid")
        if not block_id or completed_block_count not in {0, 1, 2, 3}:
            raise ObserverArtifactError("observer progress value is invalid")
        self._append(
            event=event,
            block_id=block_id,
            completed_block_count=completed_block_count,
            treatment_calls=0,
            response_replay_calls=0,
        )

    def record_failure(
        self,
        *,
        failure: Mapping[str, Any],
        error_type: str,
        error_message_sha256: str,
        completed_block_count: int,
    ) -> None:
        expected = {
            "failure_class",
            "attempt_validity",
            "replacement_eligible",
            "gate_outcome",
            "selected_method",
        }
        if set(failure) != expected:
            raise ObserverArtifactError("observer failure classification is invalid")
        if (
            not error_type
            or _SHA256_RE.fullmatch(error_message_sha256) is None
            or completed_block_count not in {0, 1, 2, 3}
        ):
            raise ObserverArtifactError("observer failure evidence is invalid")
        self._append(
            event="ATTEMPT_FAILURE",
            error_type=error_type,
            error_message_sha256=error_message_sha256,
            completed_block_count=completed_block_count,
            treatment_calls=0,
            response_replay_calls=0,
            **dict(failure),
        )

    def record_provider_response(
        self,
        *,
        lane: str,
        finish_reason: str | None,
        prompt_tokens: int,
        completion_tokens: int,
        content_bytes: int,
        content_sha256: str,
        phase: str | None = None,
        source_sequence: int | None = None,
        request_ordinal: int | None = None,
        prompt_name: str | None = None,
    ) -> None:
        integers = (prompt_tokens, completion_tokens, content_bytes)
        if (
            not lane
            or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in integers)
            or _SHA256_RE.fullmatch(content_sha256) is None
            or (finish_reason is not None and (not isinstance(finish_reason, str) or len(finish_reason) > 64))
            or (phase is not None and phase not in {"OLD", "FRESH_NATIVE", "R1_PROBE"})
            or (source_sequence is not None and (isinstance(source_sequence, bool) or not isinstance(source_sequence, int) or source_sequence < 0))
            or (request_ordinal is not None and (isinstance(request_ordinal, bool) or not isinstance(request_ordinal, int) or request_ordinal < 0))
            or (prompt_name is not None and (not isinstance(prompt_name, str) or not prompt_name or len(prompt_name) > 160))
        ):
            raise ObserverArtifactError("provider response observation is invalid")
        self._append(
            event="PROVIDER_RESPONSE",
            lane=lane,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            content_bytes=content_bytes,
            content_sha256=content_sha256,
            phase=phase,
            source_sequence=source_sequence,
            request_ordinal=request_ordinal,
            prompt_name=prompt_name,
            hard_attempt_count=1,
            treatment_calls=0,
            response_replay_calls=0,
        )

    def record_success(self, *, manifest_sha256: str) -> None:
        if _SHA256_RE.fullmatch(manifest_sha256) is None:
            raise ObserverArtifactError("observer success manifest digest is invalid")
        self._append(
            event="ATTEMPT_SUCCESS",
            completed_block_count=3,
            manifest_sha256=manifest_sha256,
            treatment_calls=0,
            response_replay_calls=0,
        )

    def close(self) -> None:
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None

    @property
    def sha256(self) -> str:
        if self._descriptor is not None:
            os.fsync(self._descriptor)
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _publication_calls(value: Mapping[str, Any], *, label: str) -> int:
    calls = value.get("publication_calls")
    if isinstance(calls, bool) or not isinstance(calls, int) or calls < 0:
        raise ObserverArtifactError(f"{label} publication accounting is invalid")
    return calls


async def run_observer_block_async(
    *,
    source_count: int,
    prepare: Callable[[int, int], Awaitable[Mapping[str, Any]] | Mapping[str, Any]],
    publish: Callable[[int, int], Awaitable[Mapping[str, Any]] | Mapping[str, Any]],
    project: Callable[[int], Awaitable[BackendProjection] | BackendProjection],
) -> dict[str, Any]:
    """Run ``prepare(i+1) -> native publish(i)`` in one fresh namespace."""

    if isinstance(source_count, bool) or not isinstance(source_count, int) or source_count < 2:
        raise ObserverArtifactError("observer block requires at least two sources")
    old_builds: dict[int, Mapping[str, Any]] = {}
    transitions: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    shadow_calls = 0
    native_calls = 0
    previous_after: BackendProjection | None = None
    for sequence in range(source_count):
        state_version = sequence
        if sequence + 1 < source_count:
            old = dict(await _maybe_await(prepare(sequence + 1, state_version)))
            calls = _publication_calls(old, label="shadow")
            if calls != 0:
                raise ObserverArtifactError("shadow build attempted publication")
            shadow_calls += calls
            old_builds[sequence + 1] = old

        before = await _maybe_await(project(state_version))
        if before.version != state_version:
            raise ObserverArtifactError("pre-publication projection version mismatch")
        if sequence == 0 and (before.nodes or before.edges or before.episodes):
            raise ObserverArtifactError("observer namespace is not fresh")
        if previous_after is not None and previous_after.digest != before.digest:
            raise ObserverArtifactError("state changed outside native publication")
        fresh = dict(await _maybe_await(publish(sequence, state_version)))
        calls = _publication_calls(fresh, label="native")
        if calls != 1:
            raise ObserverArtifactError("native source must publish exactly once")
        native_calls += calls
        after = await _maybe_await(project(state_version + 1))
        if after.version != state_version + 1:
            raise ObserverArtifactError("post-publication projection version mismatch")
        delta = build_projection_delta(before, after)
        transitions.append(
            {
                "source_sequence": sequence,
                "before": before,
                "after": after,
                "delta": delta,
            }
        )
        if sequence > 0:
            if sequence not in old_builds:
                raise ObserverArtifactError("old observer build is missing")
            pairs.append(
                {
                    "source_sequence": sequence,
                    "old_build": old_builds[sequence],
                    "fresh_build": fresh,
                    "delta": transitions[sequence - 1]["delta"],
                    "semantic_dag": fresh.get("semantic_dag"),
                }
            )
        previous_after = after
    return {
        "schema_version": "membind.v7.observer-block.v1",
        "status": "OBSERVER_ONLY",
        "source_count": source_count,
        "transitions": transitions,
        "pairs": pairs,
        "shadow_publication_calls": shadow_calls,
        "native_publication_calls": native_calls,
        "treatment_calls": 0,
    }


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _reference_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            from graphiti_native import parse_datetime

            return parse_datetime(text)
        except (ImportError, TypeError, ValueError):
            raise ObserverArtifactError("episode reference time is invalid") from None


def native_episode_kwargs(episode: Any, namespace: str) -> dict[str, Any]:
    """Map workload identity to pinned Graphiti's fresh-episode public API."""

    required = ("context_id", "source_sequence", "episode_id", "reference_time", "body")
    if any(_field(episode, name) is None for name in required):
        raise ObserverArtifactError("observer episode input is incomplete")
    sequence = int(_field(episode, "source_sequence"))
    try:
        from graphiti_core.nodes import EpisodeType
    except ModuleNotFoundError as exc:
        raise ObserverArtifactError("pinned Graphiti is unavailable") from exc
    return {
        "name": f"{_field(episode, 'context_id')}::episode::{sequence:04d}",
        "episode_body": str(_field(episode, "body")),
        "source_description": "MemoryAgentBench LongMemEval session",
        "reference_time": _reference_time(_field(episode, "reference_time")),
        "source": EpisodeType.message,
        "group_id": namespace,
        # In Graphiti 0.29.3 a non-null uuid means "load this existing
        # episode", not "create a new episode with this identity".  Workload
        # episode_id remains bound by the frozen dataset and stable name.
        "uuid": None,
        "update_communities": False,
        "saga": None,
        "saga_previous_episode_uuid": None,
    }


def _runtime_epochs(runtime: Any, graphiti: Any) -> dict[str, str]:
    public = getattr(runtime, "public_identity", None)
    if not isinstance(public, Mapping):
        public = getattr(runtime, "shared_public_identity", None)
    if not isinstance(public, Mapping):
        public = {"runtime_class": f"{type(runtime).__module__}.{type(runtime).__qualname__}"}
    provider = getattr(getattr(graphiti.driver, "provider", None), "value", "unknown")
    database = getattr(graphiti.driver, "_database", "unknown")
    return {
        "model_epoch": canonical_digest({"construction": public.get("construction"), "runtime": public}),
        "query_epoch": canonical_digest({"embedding": public.get("embedding"), "runtime": public}),
        "index_epoch": canonical_digest({"provider": provider, "operator": "exact-node-cosine-full-scan-v0.29.3"}),
        "config_epoch": canonical_digest({"graphiti_pin": "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d", "runtime": public}),
        "backend_epoch": canonical_digest({"provider": provider, "database": database, "schema": "graphiti-v0.29.3"}),
    }


def _trace_rows(recorder: Any, run_id: str) -> list[dict[str, Any]]:
    if recorder is None:
        return []
    result: list[dict[str, Any]] = []
    for record in list(getattr(recorder, "records", ()) or ()):
        if str(getattr(record, "run_id", "")) == run_id:
            to_dict = getattr(record, "to_dict", None)
            result.append(dict(to_dict()) if callable(to_dict) else _jsonable(record))
    return result


async def run_graphiti_observer_block_async(
    *,
    run_id: str,
    block_id: str,
    namespace: str,
    episodes: list[Any] | tuple[Any, ...],
    runtime_builder: Callable[[], Any],
    recorder_factory: Callable[[], Any] | None = None,
    instrumentation_installer: Callable[[Any, Any], Any] | None = None,
) -> dict[str, Any]:
    """Bind the pure block state machine to pinned Graphiti ``add_episode``."""

    selected = tuple(episodes)
    if len(selected) < 2:
        raise ObserverArtifactError("real Graphiti block requires at least two episodes")
    sequences = [int(_field(item, "source_sequence")) for item in selected]
    if sequences != list(range(len(selected))):
        raise ObserverArtifactError("real Graphiti block episode sequence is invalid")
    if not run_id or not block_id or not namespace:
        raise ObserverArtifactError("real Graphiti block identity is incomplete")
    runtime = await _maybe_await(runtime_builder())
    graphiti = getattr(runtime, "graphiti", None)
    if graphiti is None:
        raise ObserverArtifactError("real Graphiti runtime is missing")
    init_task = getattr(graphiti.driver, "_init_task", None)
    if init_task is not None:
        await _maybe_await(init_task)
    epochs = _runtime_epochs(runtime, graphiti)
    capture = GraphitiCaptureInstallation(graphiti, **epochs)
    recorder = recorder_factory() if recorder_factory is not None else None
    native_instrumentation: Any = None
    capture.install()
    try:
        if instrumentation_installer is not None:
            if recorder is None:
                raise ObserverArtifactError("instrumentation requires a recorder")
            native_instrumentation = instrumentation_installer(graphiti, recorder)

        async def prepare(sequence: int, state_version: int) -> Mapping[str, Any]:
            kwargs = native_episode_kwargs(selected[sequence], namespace)
            trace_run_id = f"{run_id}:{block_id}:OLD:{sequence}"
            recorder_scope = (
                recorder.episode_scope(trace_run_id, kwargs["name"], sequence)
                if recorder is not None
                else nullcontext()
            )
            with capture.scope(
                phase="OLD",
                source_sequence=sequence,
                state_version=state_version,
                episode_kwargs=kwargs,
            ) as observed:
                with recorder_scope:
                    root_scope = (
                        recorder.span("build-to-seam", operation_class="semantic-root")
                        if recorder is not None
                        else nullcontext()
                    )
                    with root_scope:
                        stage = await build_to_seam_async(
                            graphiti,
                            kwargs,
                            publication_frontier=state_version,
                            backend_epoch=epochs["backend_epoch"],
                        )
                capture.attach_shadow_result(observed, stage)
            value = observed.to_record()
            value["trace"] = _trace_rows(recorder, trace_run_id)
            value["semantic_dag"] = build_semantic_cost_dag(value)
            return value

        async def publish(sequence: int, state_version: int) -> Mapping[str, Any]:
            kwargs = native_episode_kwargs(selected[sequence], namespace)
            trace_run_id = f"{run_id}:{block_id}:FRESH_NATIVE:{sequence}"
            recorder_scope = (
                recorder.episode_scope(trace_run_id, kwargs["name"], sequence)
                if recorder is not None
                else nullcontext()
            )
            with capture.scope(
                phase="FRESH_NATIVE",
                source_sequence=sequence,
                state_version=state_version,
                episode_kwargs=kwargs,
            ) as observed:
                with recorder_scope:
                    await graphiti.add_episode(**kwargs)
            value = observed.to_record()
            value["trace"] = _trace_rows(recorder, trace_run_id)
            value["semantic_dag"] = build_semantic_cost_dag(value)
            return value

        async def project(version: int) -> BackendProjection:
            return await load_backend_projection_async(
                graphiti.driver,
                namespace=namespace,
                version=version,
                backend_epoch=epochs["backend_epoch"],
            )

        block = await run_observer_block_async(
            source_count=len(selected),
            prepare=prepare,
            publish=publish,
            project=project,
        )
        block.update(
            {
                "run_id": run_id,
                "block_id": block_id,
                "namespace": namespace,
                "real_graphiti_evidence": True,
                "epochs": epochs,
                "provider_identity": _jsonable(getattr(runtime, "public_identity", {})),
            }
        )
        return block
    finally:
        if native_instrumentation is not None:
            native_instrumentation.restore()
        capture.restore()
        close = getattr(graphiti, "close", None)
        if callable(close):
            await _maybe_await(close())


def load_protocol_freeze(path: str | Path) -> dict[str, Any]:
    value = _object(Path(path))
    schema_version = value.get("schema_version")
    if schema_version not in {
        "membind.v7.r1-r3-protocol-freeze.v1",
        "membind.v7.r1-r3-protocol-freeze.v2",
        "membind.v7.r1-r3-protocol-freeze.v3",
        "membind.v7.r1-r3-protocol-freeze.v4",
        "membind.v7.r1-r3-protocol-freeze.v5",
    }:
        raise ObserverArtifactError("V7 observer protocol schema is invalid")
    if value.get("status") != "FROZEN_BEFORE_REAL_GRAPHITI_CAMPAIGN":
        raise ObserverArtifactError("V7 observer protocol is not frozen")
    for field in (
        "treatment_authorized",
        "old_read_return_allowed",
        "native_demand_skip_allowed",
        "repair_apply_allowed",
    ):
        if value.get(field) is not False:
            raise ObserverArtifactError("V7 observer protocol authorizes forbidden treatment")
    workload = value.get("workload")
    thresholds = value.get("thresholds")
    if not isinstance(workload, Mapping) or not isinstance(thresholds, Mapping):
        raise ObserverArtifactError("V7 observer protocol is incomplete")
    r12 = workload.get("r1_r2")
    r3 = workload.get("r3_blocks")
    if not isinstance(r12, Mapping) or r12.get("source_count") != 2:
        raise ObserverArtifactError("V7 R1/R2 workload is invalid")
    if not isinstance(r3, list) or len(r3) != 2 or any(not isinstance(row, Mapping) or row.get("source_count") != 6 for row in r3):
        raise ObserverArtifactError("V7 R3 workload is invalid")
    for name in (
        "csp_min",
        "sca_work_max",
        "reconvergence_min",
        "required_headroom_floor_ns",
        "required_headroom_ratio",
    ):
        raw = thresholds.get(name)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
            raise ObserverArtifactError(f"V7 observer threshold is invalid: {name}")
    if schema_version in {
        "membind.v7.r1-r3-protocol-freeze.v2",
        "membind.v7.r1-r3-protocol-freeze.v3",
        "membind.v7.r1-r3-protocol-freeze.v4",
        "membind.v7.r1-r3-protocol-freeze.v5",
    }:
        provider = value.get("provider")
        if not isinstance(provider, Mapping):
            raise ObserverArtifactError("V7 observer transport is incomplete")
        requested_max_tokens = provider.get("requested_max_tokens")
        timeout_seconds = provider.get("http_timeout_seconds")
        if (
            isinstance(requested_max_tokens, bool)
            or not isinstance(requested_max_tokens, int)
            or requested_max_tokens <= 0
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
            or provider.get("sdk_max_retries") != 0
            or provider.get("hard_attempt_limit_per_request") != 1
            or provider.get("structured_output_mode") != "json_schema"
        ):
            raise ObserverArtifactError("V7 observer transport is invalid")
        reauthorization = value.get("infrastructure_reauthorization")
        if not isinstance(reauthorization, Mapping):
            raise ObserverArtifactError(
                "V7 observer infrastructure reauthorization is incomplete"
            )
        probes = reauthorization.get("diagnostic_probe_sha256")
        statuses = reauthorization.get("diagnostic_probe_status")
        digests = (
            reauthorization.get("previous_protocol_sha256"),
            reauthorization.get("previous_terminal_manifest_sha256"),
            *(probes if isinstance(probes, list) else ()),
        )
        changed_fields = reauthorization.get("changed_fields")
        if schema_version == "membind.v7.r1-r3-protocol-freeze.v2":
            changed_fields_valid = changed_fields == ["provider.requested_max_tokens"]
        elif schema_version == "membind.v7.r1-r3-protocol-freeze.v3":
            changed_fields_valid = (
                isinstance(changed_fields, list)
                and bool(changed_fields)
                and all(isinstance(field, str) and field for field in changed_fields)
                and "provider.requested_max_tokens" not in changed_fields
            )
        elif schema_version == "membind.v7.r1-r3-protocol-freeze.v4":
            changed_fields_valid = changed_fields == [
                "provider.requested_max_tokens",
                "observer_harness.source_sha256",
            ]
        else:
            changed_fields_valid = changed_fields == [
                "provider.requested_max_tokens",
                "observer_harness.source_sha256",
            ]
        if (
            reauthorization.get("previous_terminal_state")
            != "V7_THEORY_OR_SYSTEM_BLOCKED"
            or not changed_fields_valid
            or not isinstance(probes, list)
            or len(probes) != 2
            or statuses != ["PASS", "PASS"]
            or any(
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                for digest in digests
            )
        ):
            raise ObserverArtifactError(
                "V7 observer infrastructure reauthorization is invalid"
            )
        if schema_version in {
            "membind.v7.r1-r3-protocol-freeze.v4",
            "membind.v7.r1-r3-protocol-freeze.v5",
        }:
            invalid_attempt = reauthorization.get("invalid_attempt")
            previous_limit = (
                invalid_attempt.get("previous_requested_max_tokens")
                if isinstance(invalid_attempt, Mapping)
                else None
            )
            if schema_version == "membind.v7.r1-r3-protocol-freeze.v4":
                expected_length_evidence = (
                    previous_limit == 8_192
                    and invalid_attempt.get("completion_tokens") == previous_limit
                    and requested_max_tokens == 2 * previous_limit
                )
            else:
                expected_length_evidence = (
                    previous_limit == 16_384
                    and invalid_attempt.get("completion_tokens") == 8_192
                    and invalid_attempt.get("observed_provider_cap_tokens") == 8_192
                    and requested_max_tokens == 2 * previous_limit
                )
            length_evidence_valid = (
                isinstance(invalid_attempt, Mapping)
                and isinstance(invalid_attempt.get("run_id"), str)
                and bool(invalid_attempt.get("run_id"))
                and _SHA256_RE.fullmatch(
                    str(invalid_attempt.get("attempt_journal_sha256", ""))
                )
                is not None
                and _SHA256_RE.fullmatch(
                    str(invalid_attempt.get("failure_artifact_sha256", ""))
                )
                is not None
                and invalid_attempt.get("completed_block_count") in {0, 1, 2}
                and invalid_attempt.get("failure_class")
                == "INFRASTRUCTURE_PROVIDER_STRUCTURED_OUTPUT_INVALID"
                and invalid_attempt.get("error_type") == "json.decoder.JSONDecodeError"
                and invalid_attempt.get("finish_reason") == "length"
                and expected_length_evidence
                and invalid_attempt.get("gate_outcome") == "NOT_EVALUATED"
                and invalid_attempt.get("treatment_calls") == 0
                and invalid_attempt.get("response_replay_calls") == 0
            )
            if not length_evidence_valid:
                raise ObserverArtifactError(
                    "V7 observer token-limit length evidence is invalid"
                )
    if schema_version in {
        "membind.v7.r1-r3-protocol-freeze.v3",
        "membind.v7.r1-r3-protocol-freeze.v4",
        "membind.v7.r1-r3-protocol-freeze.v5",
    }:
        harness = value.get("observer_harness")
        sources = harness.get("source_sha256") if isinstance(harness, Mapping) else None
        if (
            not isinstance(harness, Mapping)
            or harness.get("schema_version") != "membind.v7.observer-harness-freeze.v1"
            or not isinstance(sources, Mapping)
            or not sources
            or any(
                not isinstance(name, str)
                or not name
                or Path(name).is_absolute()
                or ".." in Path(name).parts
                or Path(name).as_posix() != name
                or not isinstance(digest, str)
                or _SHA256_RE.fullmatch(digest) is None
                for name, digest in sources.items()
            )
        ):
            raise ObserverArtifactError("V7 observer harness freeze is invalid")
    return value


def verify_observer_harness_sources(
    repository_root: str | Path, protocol: Mapping[str, Any]
) -> dict[str, Any]:
    harness = protocol.get("observer_harness")
    sources = harness.get("source_sha256") if isinstance(harness, Mapping) else None
    if not isinstance(sources, Mapping) or not sources:
        raise ObserverArtifactError("V7 observer harness freeze is missing")
    root = Path(repository_root).resolve()
    actual: dict[str, str] = {}
    for name, expected in sorted(sources.items()):
        if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ObserverArtifactError("V7 observer harness source path is invalid")
        target = (root / name).resolve()
        if root not in target.parents or not target.is_file():
            raise ObserverArtifactError("V7 observer harness source is missing")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != expected:
            raise ObserverArtifactError("V7 observer harness source hash differs from freeze")
        actual[name] = digest
    return {
        "schema_version": "membind.v7.observer-harness-verification.v1",
        "status": "PASS",
        "source_sha256": actual,
    }


def _namespace(run_id: str, lane: str) -> str:
    value = _NAMESPACE_SAFE.sub("-", f"membind-v7-{run_id}-{lane}".casefold()).strip("-")
    if not value or len(value) > 120:
        raise ObserverArtifactError("V7 observer namespace is invalid")
    return value


async def run_real_observer_campaign_async(
    *,
    protocol: Mapping[str, Any],
    contexts: Sequence[Any],
    episode_builder: Callable[[Any], Sequence[Any]],
    runtime_builder_factory: Callable[[str], Callable[[], Any]],
    output_root: str | Path,
    run_id: str,
    recorder_factory: Callable[[], Any] | None = None,
    instrumentation_installer: Callable[[Any, Any], Any] | None = None,
    block_runner: Callable[..., Any] = run_graphiti_observer_block_async,
    progress_observer: Callable[[Mapping[str, Any]], Any] | None = None,
    observer_harness_verification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the preregistered real R1/R2 then two R3 blocks."""

    workload = protocol.get("workload")
    if not isinstance(workload, Mapping):
        raise ObserverArtifactError("V7 campaign workload is missing")
    if Path(output_root).exists():
        raise ObserverArtifactError("V7 campaign output root must be fresh")

    async def progress(event: str, block_id: str, completed_block_count: int) -> None:
        if progress_observer is not None:
            await _maybe_await(
                progress_observer(
                    {
                        "event": event,
                        "block_id": block_id,
                        "completed_block_count": completed_block_count,
                    }
                )
            )

    def selected_episodes(spec: Mapping[str, Any]) -> tuple[Any, ...]:
        index = int(spec["context_index"])
        start = int(spec.get("source_start", 0))
        count = int(spec["source_count"])
        if index < 0 or index >= len(contexts):
            raise ObserverArtifactError("V7 campaign context index is out of range")
        episodes = tuple(episode_builder(contexts[index]))
        selected = episodes[start : start + count]
        if len(selected) != count:
            raise ObserverArtifactError("V7 campaign workload slice is incomplete")
        # Rebase only the selected prefix contract.  The preregistered protocol
        # currently starts at zero; nonzero starts require a new delta profile.
        if start != 0 or [int(_field(item, "source_sequence")) for item in selected] != list(range(count)):
            raise ObserverArtifactError("V7 campaign workload is not a zero-based prefix")
        return selected

    r12_spec = workload["r1_r2"]
    r12_episodes = selected_episodes(r12_spec)
    await progress("BLOCK_START", "R1-R2", 0)
    r12_block = await _maybe_await(
        block_runner(
            run_id=run_id,
            block_id="R1-R2",
            namespace=_namespace(run_id, "r1-r2"),
            episodes=r12_episodes,
            runtime_builder=runtime_builder_factory("r1-r2"),
            recorder_factory=recorder_factory,
            instrumentation_installer=instrumentation_installer,
        )
    )
    await progress("BLOCK_COMPLETE", "R1-R2", 1)
    r1 = audit_r1_assumptions(r12_block)
    r2 = build_r2_causal_trace(r12_block)
    if r1.get("dependency_edge_kinds_complete") is not True:
        raise ObserverArtifactError("R1 instrumentation contract is incomplete")

    r3_blocks: list[Mapping[str, Any]] = []
    for block_index, spec in enumerate(workload["r3_blocks"], start=1):
        block_id = str(spec["block_id"])
        lane = block_id.casefold()
        await progress("BLOCK_START", block_id, block_index)
        block = await _maybe_await(
            block_runner(
                run_id=run_id,
                block_id=block_id,
                namespace=_namespace(run_id, lane),
                episodes=selected_episodes(spec),
                runtime_builder=runtime_builder_factory(lane),
                recorder_factory=recorder_factory,
                instrumentation_installer=instrumentation_installer,
            )
        )
        r3_blocks.append(block)
        await progress("BLOCK_COMPLETE", block_id, block_index + 1)
    characterization = characterize_r3_blocks(
        r3_blocks,
        thresholds=protocol["thresholds"],
    )
    characterization["decision_input"]["core_assumptions_supported"] = r1[
        "core_assumptions_supported"
    ]
    harness_bound = (
        isinstance(observer_harness_verification, Mapping)
        and observer_harness_verification.get("status") == "PASS"
        and isinstance(observer_harness_verification.get("source_sha256"), Mapping)
        and bool(observer_harness_verification.get("source_sha256"))
    )
    characterization["decision_input"]["observer_harness_bound"] = harness_bound
    campaign_identity = {
        "schema_version": "membind.v7.real-observer-campaign-identity.v1",
        "run_id": run_id,
        "provider": protocol.get("provider"),
        "backend": protocol.get("backend"),
        "workload": workload,
        "selected_characterization_region": protocol.get("selected_characterization_region"),
        "protocol_sha256": canonical_digest(protocol),
        "treatment_calls": 0,
        "response_replay_calls": 0,
        "observer_harness": dict(observer_harness_verification or {}),
    }
    return materialize_r3_artifacts(
        output_root,
        r1=r1,
        r2=r2,
        blocks=r3_blocks,
        characterization=characterization,
        campaign_identity=campaign_identity,
    )


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(child) for child in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(child) for child in value), key=repr)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _encoded(value: Any) -> bytes:
    return (
        json.dumps(
            _jsonable(value),
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("ascii")
        + b"\n"
    )


def _write_exclusive(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("artifact write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence_manifest(artifacts: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    members = [
        {"path": name, "sha256": hashlib.sha256(_encoded(value)).hexdigest()}
        for name, value in sorted(artifacts.items())
    ]
    value = {
        "schema_version": "membind.v7.r3-evidence-manifest.v1",
        "status": "SEALED_INPUTS",
        "files": members,
        "treatment_calls": 0,
    }
    return value, hashlib.sha256(_encoded(value)).hexdigest()


def materialize_r3_artifacts(
    root: str | Path,
    *,
    r1: Mapping[str, Any],
    r2: Mapping[str, Any],
    blocks: Sequence[Mapping[str, Any]],
    characterization: Mapping[str, Any],
    campaign_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Materialize the complete R1-R3 evidence and re-evaluate sealed gates."""

    if len(blocks) != 2:
        raise ObserverArtifactError("R3 materializer requires exactly two blocks")
    base = {
        "R1_ASSUMPTION_AUDIT.json": dict(r1),
        "R2_TWO_SOURCE_CAUSAL_TRACE.json": dict(r2),
        "R3_BLOCKS.json": list(blocks),
        "PROPAGATION_MATRIX.json": {
            "schema_version": "membind.v7.propagation-matrix.v1",
            "rows": list(characterization.get("pair_analyses") or ()),
        },
        "CERTIFICATE_CONFUSION.json": {
            "schema_version": "membind.v7.certificate-confusion.v1",
            "matrix": dict(characterization.get("certificate_confusion") or {}),
            "false_unaffected_count": characterization.get("false_unaffected_count"),
        },
        "AFFECTED_SET_ORACLE.json": {
            "schema_version": "membind.v7.affected-set-oracle.v1",
            "pair_analyses": list(characterization.get("pair_analyses") or ()),
        },
        "CSP_SCA.json": {
            "schema_version": "membind.v7.csp-sca.v1",
            "csp": characterization.get("csp"),
            "semantic_change_amplification": dict(
                characterization.get("semantic_change_amplification") or {}
            ),
            "reconvergence": dict(characterization.get("reconvergence") or {}),
        },
        "CRITICAL_OPPORTUNITY.json": {
            "schema_version": "membind.v7.critical-opportunity.v1",
            **dict(characterization.get("critical_opportunity") or {}),
        },
        "WORK_AMPLIFICATION.json": {
            "schema_version": "membind.v7.work-amplification.v1",
            **dict(characterization.get("semantic_change_amplification") or {}),
        },
    }
    evidence_manifest, evidence_digest = _evidence_manifest(base)
    decision = dict(characterization.get("decision_input") or {})
    decision["sealed_manifest_sha256"] = evidence_digest
    method = evaluate_opportunity_gates(decision)
    artifacts = {
        **base,
        "EVIDENCE_MANIFEST.json": evidence_manifest,
        "R3_DECISION_INPUT.json": decision,
        "METHOD_SELECTION.json": method,
    }
    seal = write_observer_artifacts(
        root,
        artifacts,
        campaign_identity=campaign_identity,
    )
    verification = verify_observer_manifest(root)
    return {
        **seal,
        "verification": verification,
        "decision_input": decision,
        "method_selection": method,
    }


def write_observer_artifacts(
    root: str | Path,
    artifacts: Mapping[str, Any],
    *,
    campaign_identity: Mapping[str, Any],
) -> dict[str, Any]:
    target = Path(root)
    if target.exists():
        raise ObserverArtifactError("observer artifact root must be fresh")
    if not artifacts:
        raise ObserverArtifactError("observer artifact set is empty")
    target.mkdir(parents=True, exist_ok=False)
    files: list[dict[str, str]] = []
    try:
        for name, value in sorted(artifacts.items()):
            if Path(name).name != name or not name.endswith(".json") or name in {"MANIFEST.json", "SEAL.json"}:
                raise ObserverArtifactError("observer artifact name is invalid")
            path = target / name
            _write_exclusive(path, _encoded(value))
            files.append({"path": name, "sha256": _sha256(path)})
        manifest = {
            "schema_version": "membind.v7.observer-manifest.v2",
            "status": "SEALED",
            "campaign_identity": _jsonable(campaign_identity),
            "files": files,
        }
        manifest_path = target / "MANIFEST.json"
        _write_exclusive(manifest_path, _encoded(manifest))
        manifest_sha256 = _sha256(manifest_path)
        seal = {
            "schema_version": "membind.v7.observer-seal.v2",
            "status": "SEALED",
            "manifest_sha256": manifest_sha256,
            "treatment_calls": 0,
        }
        _write_exclusive(target / "SEAL.json", _encoded(seal))
    except BaseException:
        # Do not hide a partial durable attempt.  A rerun must choose a fresh
        # root, preserving autoresearch failure evidence.
        raise
    return {"status": "SEALED", "root": str(target.resolve()), "manifest_sha256": manifest_sha256}


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ObserverArtifactError(f"observer artifact is unreadable: {path.name}") from None
    if not isinstance(value, dict):
        raise ObserverArtifactError(f"observer artifact is not an object: {path.name}")
    return value


def verify_observer_manifest(root: str | Path) -> dict[str, Any]:
    target = Path(root)
    manifest_path = target / "MANIFEST.json"
    seal = _object(target / "SEAL.json")
    manifest = _object(manifest_path)
    if manifest.get("schema_version") != "membind.v7.observer-manifest.v2":
        raise ObserverArtifactError("observer manifest schema is invalid")
    if seal.get("manifest_sha256") != _sha256(manifest_path):
        raise ObserverArtifactError("observer manifest digest mismatch")
    members = manifest.get("files")
    if not isinstance(members, list) or not members:
        raise ObserverArtifactError("observer manifest file list is invalid")
    expected = {"MANIFEST.json", "SEAL.json"}
    for member in members:
        if not isinstance(member, Mapping):
            raise ObserverArtifactError("observer manifest member is invalid")
        name = member.get("path")
        digest = member.get("sha256")
        if not isinstance(name, str) or Path(name).name != name or not isinstance(digest, str):
            raise ObserverArtifactError("observer manifest member identity is invalid")
        expected.add(name)
        path = target / name
        if not path.is_file() or _sha256(path) != digest:
            raise ObserverArtifactError(f"observer artifact digest mismatch: {name}")
    actual = {path.name for path in target.glob("*.json")}
    if actual != expected:
        raise ObserverArtifactError("observer artifact inventory mismatch")
    evidence_sha256: str | None = None
    evidence_path = target / "EVIDENCE_MANIFEST.json"
    if evidence_path.is_file():
        evidence_sha256 = _sha256(evidence_path)
        evidence = _object(evidence_path)
        evidence_members = evidence.get("files")
        if not isinstance(evidence_members, list) or not evidence_members:
            raise ObserverArtifactError("R3 evidence manifest is invalid")
        for member in evidence_members:
            if not isinstance(member, Mapping):
                raise ObserverArtifactError("R3 evidence member is invalid")
            name = member.get("path")
            digest = member.get("sha256")
            if not isinstance(name, str) or Path(name).name != name or not isinstance(digest, str):
                raise ObserverArtifactError("R3 evidence member identity is invalid")
            path = target / name
            if not path.is_file() or _sha256(path) != digest:
                raise ObserverArtifactError(f"R3 evidence digest mismatch: {name}")
        decision_path = target / "R3_DECISION_INPUT.json"
        if decision_path.is_file():
            decision = _object(decision_path)
            if decision.get("sealed_manifest_sha256") != evidence_sha256:
                raise ObserverArtifactError("R3 decision evidence-manifest digest mismatch")
    return {
        "status": "PASS",
        "manifest_sha256": _sha256(manifest_path),
        "file_count": len(members),
        "campaign_identity": manifest.get("campaign_identity"),
        "evidence_manifest_sha256": evidence_sha256,
    }


__all__ = [
    "ObserverAttemptJournal",
    "ObserverArtifactError",
    "classify_observer_failure",
    "materialize_r3_artifacts",
    "native_episode_kwargs",
    "load_protocol_freeze",
    "run_real_observer_campaign_async",
    "run_observer_block_async",
    "run_graphiti_observer_block_async",
    "verify_observer_manifest",
    "verify_observer_harness_sources",
    "write_observer_artifacts",
]
