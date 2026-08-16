"""Durable M* production-runner composition, gated by sealed FX0 parity."""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from pathlib import Path

from .artifacts import payload_sha256
from .s5_durable_attempt_store import S5AttemptStore, inspect_s5_attempt
from .s5_mstar_failure_envelope import (
    S5MStarFailureEnvelopeError,
    build_s5_mstar_failure_envelope,
    verify_s5_mstar_failure_envelope,
    write_s5_mstar_failure_envelope,
)
from .s5_mstar_pipeline import (
    SCHEMA as MSTAR_PIPELINE_SCHEMA,
    MStarSource,
    MStarSpec,
    run_mstar_pipeline,
)
from .s5_mstar_production_core_identity import (
    S5MStarProductionCoreIdentityError,
    verify_s5_mstar_production_core_identity,
)
from .s5_mstar_publication_journal import S5MStarPublicationJournal
from .s5_production_runner import (
    S5ProductionIdentityError,
    verify_s5_production_identity,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_FX0_ARTIFACT_FIELDS = {
    "git_commit",
    "payload",
    "payload_sha256",
    "protocol_version",
    "run_id",
    "status",
}
_FX0_PAYLOAD_FIELDS = {
    "schema_version",
    "verdict",
    "fixture_count",
    "run_id",
    "runtime_config_sha256",
    "production_core_identity_sha256",
    "fx0_artifact_payload_sha256",
    "fx0_fixture_manifest_sha256",
    "current_stage_pointer_sha256",
    "full_regression_junit_sha256",
    "full_regression_summary",
    "legacy_status_artifact_preserved",
    "authority",
}
_FX0_AUTHORITY = {
    "model_call_authorized": False,
    "neo4j_read_authorized": False,
    "neo4j_mutation_authorized": False,
    "s5_live_execution_authorized": False,
    "current_stage_pointer_update_authorized": False,
}
_RUNNER_FAILURE_SCHEMA = "membind.paper-eval-v3.s5-mstar-runner-failure.v2"
_RUNNER_TERMINAL_FAILURE_SCHEMA = (
    "membind.paper-eval-v3.s5-mstar-runner-terminal-failure.v1"
)
_KNOWN_INNER_FAILURE_SCHEMAS = {
    MSTAR_PIPELINE_SCHEMA,
    _RUNNER_TERMINAL_FAILURE_SCHEMA,
}
_FAILURE_CLASSIFICATIONS = {
    "CAP_EXHAUSTED",
    "STRUCTURED_INVALID",
    "UNCLASSIFIED",
}


class S5MStarProductionRunnerError(ValueError):
    """M* runner composition or identity failure."""


class UnclassifiedFailure(RuntimeError):
    """Stable content-free marker used only for fail-closed evidence fallback."""


def _fail(code: str) -> S5MStarProductionRunnerError:
    return S5MStarProductionRunnerError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _verify_fx0_qualification(value: Mapping[str, object]) -> dict[str, object]:
    """Verify the sealed public FX0 qualification needed by this runner."""

    if not isinstance(value, Mapping):
        raise _fail("fx0_qualification_not_mapping")
    artifact = deepcopy(dict(value))
    if set(artifact) != _FX0_ARTIFACT_FIELDS:
        raise _fail("fx0_qualification_shape_invalid")
    payload = artifact.get("payload")
    if not isinstance(payload, Mapping):
        raise _fail("fx0_qualification_payload_invalid")
    payload = deepcopy(dict(payload))
    if artifact.get("payload_sha256") != payload_sha256(payload):
        raise _fail("fx0_qualification_hash_invalid")
    if (
        artifact.get("protocol_version") != "paper-eval-v3"
        or artifact.get("status") != "finalized"
        or not isinstance(artifact.get("run_id"), str)
        or not artifact.get("run_id")
        or not isinstance(artifact.get("git_commit"), str)
        or _GIT_COMMIT.fullmatch(str(artifact.get("git_commit"))) is None
        or set(payload) != _FX0_PAYLOAD_FIELDS
        or payload.get("schema_version")
        != "membind.paper-eval-v3.s5-graphiti-fx0-production-qualification.v1"
        or payload.get("verdict") != "PRODUCTION_PATH_EXACT_PARITY_PASS"
        or payload.get("fixture_count") != 11
        or not isinstance(payload.get("run_id"), str)
        or not payload.get("run_id")
        or payload.get("legacy_status_artifact_preserved") is not True
        or payload.get("authority") != _FX0_AUTHORITY
    ):
        raise _fail("fx0_qualification_binding_invalid")
    for field in (
        "runtime_config_sha256",
        "production_core_identity_sha256",
        "fx0_artifact_payload_sha256",
        "fx0_fixture_manifest_sha256",
        "current_stage_pointer_sha256",
        "full_regression_junit_sha256",
    ):
        _sha(payload.get(field), "fx0_qualification_hash_field_invalid")
    summary = payload.get("full_regression_summary")
    if (
        not isinstance(summary, Mapping)
        or set(summary) != {"tests", "failures", "errors", "skipped"}
        or isinstance(summary.get("tests"), bool)
        or not isinstance(summary.get("tests"), int)
        or int(summary["tests"]) < 1
        or summary.get("failures") != 0
        or summary.get("errors") != 0
        or summary.get("skipped") != 0
    ):
        raise _fail("fx0_qualification_summary_invalid")
    return payload


SemanticPrepare = Callable[[object, int], Awaitable[object]]
LatestStateBind = Callable[[object, int, int, tuple[int, ...]], Awaitable[object]]
CommitEvidence = Callable[
    [object, int, int, tuple[int, ...]], Awaitable[str] | str
]
ClockNs = Callable[[], int]
FailureTelemetrySnapshot = Callable[[], Sequence[Mapping[str, object]]]


def _empty_failure_telemetry_snapshot() -> tuple[()]:
    return ()


def _failure_terminal_fields(
    evidence: Mapping[str, object],
) -> tuple[int | None, str, str | None, str | None]:
    events = evidence.get("events")
    terminal: Mapping[str, object] | None = None
    if isinstance(events, Sequence) and not isinstance(events, (str, bytes)):
        for event in reversed(events):
            if isinstance(event, Mapping) and event.get("event_type") == "terminal_failure":
                terminal = event
                break
    source = terminal.get("failed_source_sequence") if terminal else None
    if isinstance(source, bool) or not isinstance(source, int) or source < 0:
        source = None
    error_class = terminal.get("error_class") if terminal else evidence.get("error_class")
    if not isinstance(error_class, str):
        error_class = "paper_eval.s5_mstar_production_runner.UnclassifiedFailure"
    semantic_code = evidence.get("semantic_error_code")
    if not isinstance(semantic_code, str):
        semantic_code = None
    upstream_class = evidence.get("upstream_error_class")
    if not isinstance(upstream_class, str):
        upstream_class = None
    return source, error_class, semantic_code, upstream_class


def verify_s5_mstar_runner_failure_evidence(
    value: Mapping[str, object],
    *,
    failure_envelope_path: Path | None = None,
) -> dict[str, object]:
    """Verify the runner wrapper and, when available, its persisted sidecar."""

    if not isinstance(value, Mapping):
        raise _fail("runner_failure_not_mapping")
    evidence = deepcopy(dict(value))
    required = {
        "schema_version",
        "inner_evidence_schema_version",
        "run_id",
        "method",
        "production_core_identity_sha256",
        "status",
        "mergeable",
        "failure_code",
        "error_class",
        "failed_source_sequence",
        "semantic_error_code",
        "upstream_error_class",
        "events",
        "summary",
        "failure_envelope_payload_sha256",
        "failure_envelope_file_sha256",
        "failure_classification",
    }
    if set(evidence) != required:
        raise _fail("runner_failure_shape_invalid")
    if (
        evidence.get("schema_version") != _RUNNER_FAILURE_SCHEMA
        or evidence.get("inner_evidence_schema_version")
        not in _KNOWN_INNER_FAILURE_SCHEMAS
        or evidence.get("method") != "M*"
        or evidence.get("status") != "FAIL_CLOSED"
        or evidence.get("mergeable") is not False
        or not isinstance(evidence.get("failure_code"), str)
        or not isinstance(evidence.get("error_class"), str)
        or evidence.get("failure_classification") not in _FAILURE_CLASSIFICATIONS
    ):
        raise _fail("runner_failure_binding_invalid")
    for field in (
        "production_core_identity_sha256",
        "failure_envelope_payload_sha256",
        "failure_envelope_file_sha256",
    ):
        _sha(evidence.get(field), "runner_failure_hash_invalid")
    if failure_envelope_path is not None:
        path = Path(failure_envelope_path)
        try:
            import json

            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise _fail("failure_envelope_unreadable") from None
        try:
            envelope = verify_s5_mstar_failure_envelope(raw)
        except S5MStarFailureEnvelopeError as error:
            raise _fail(str(error)) from None
        from .artifacts import sha256_file

        if (
            envelope.get("run_id") != evidence.get("run_id")
            or envelope.get("method") != evidence.get("method")
            or envelope.get("production_core_identity_sha256")
            != evidence.get("production_core_identity_sha256")
            or envelope.get("failed_source_sequence")
            != evidence.get("failed_source_sequence")
            or envelope.get("pipeline_failure_code")
            != evidence.get("failure_code")
            or envelope.get("pipeline_error_class")
            != evidence.get("error_class")
            or envelope.get("semantic_error_code")
            != evidence.get("semantic_error_code")
            or envelope.get("upstream_error_class")
            != evidence.get("upstream_error_class")
            or envelope.get("failure_envelope_sha256")
            != evidence.get("failure_envelope_payload_sha256")
            or sha256_file(path) != evidence.get("failure_envelope_file_sha256")
            or envelope.get("classification")
            != evidence.get("failure_classification")
        ):
            raise _fail("failure_envelope_binding_invalid")
    return evidence


def verify_s5_mstar_production_bindings(
    *,
    spec: MStarSpec,
    identity: Mapping[str, object],
    production_core_identity: Mapping[str, object],
    fx0_qualification: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    """Cross-bind the generic identity, pre-FX0 core, and FX0 qualification."""

    if not isinstance(spec, MStarSpec):
        raise _fail("mstar_spec_invalid")
    try:
        checked_identity = verify_s5_production_identity(identity)
    except S5ProductionIdentityError as error:
        raise _fail(str(error)) from None
    try:
        checked_core = verify_s5_mstar_production_core_identity(
            production_core_identity
        )
    except S5MStarProductionCoreIdentityError as error:
        raise _fail(str(error)) from None
    checked_fx0 = _verify_fx0_qualification(fx0_qualification)

    if checked_identity["method"] != "M*":
        raise _fail("identity_method_mismatch")
    if spec.production_core_identity_sha256 != checked_core["identity_sha256"]:
        raise _fail("production_core_identity_mismatch")
    source_bindings = (
        ("graphiti_version", "graphiti_version"),
        ("graphiti_commit", "graphiti_commit"),
        ("graphiti_semantic_api_sha256", "graphiti_semantic_api_sha256"),
        ("runtime_factory_entrypoint", "runtime_factory_entrypoint"),
        ("runtime_factory_source_sha256", "runtime_factory_source_sha256"),
        ("scheduler_source_sha256", "pipeline_source_sha256"),
        ("scheduler_test_source_sha256", "pipeline_test_source_sha256"),
        ("durable_store_source_sha256", "durable_store_source_sha256"),
        (
            "durable_store_test_source_sha256",
            "durable_store_test_source_sha256",
        ),
        ("runtime_config_sha256", "runtime_config_sha256"),
    )
    if any(
        checked_identity[production_field] != checked_core[core_field]
        for production_field, core_field in source_bindings
    ):
        raise _fail("production_core_source_binding_mismatch")
    if (
        checked_fx0["production_core_identity_sha256"]
        != checked_core["identity_sha256"]
    ):
        raise _fail("fx0_core_identity_mismatch")
    if (
        checked_fx0["fx0_artifact_payload_sha256"]
        != checked_identity["fx0_parity_artifact_sha256"]
    ):
        raise _fail("fx0_artifact_identity_mismatch")
    if (
        checked_fx0["runtime_config_sha256"]
        != checked_core["runtime_config_sha256"]
    ):
        raise _fail("fx0_runtime_config_mismatch")
    return {
        "identity": checked_identity,
        "production_core_identity": checked_core,
        "fx0_qualification": checked_fx0,
    }


class S5MStarProductionRunner:
    """Compose pinned M* callbacks, commit journal, and durable evidence.

    The generic production identity names the full runnable composition.  The
    distinct core identity is the pre-FX0 code/configuration identity sealed by
    the qualification artifact; both are required and cross-bound here.
    """

    def __init__(
        self,
        *,
        attempt_root: Path,
        spec: MStarSpec,
        identity: Mapping[str, object],
        production_core_identity: Mapping[str, object],
        fx0_qualification: Mapping[str, object],
        sources: Sequence[MStarSource],
        semantic_prepare: SemanticPrepare,
        latest_state_bind: LatestStateBind,
        commit_evidence: CommitEvidence,
        clock_ns: ClockNs,
        failure_telemetry_snapshot: FailureTelemetrySnapshot = (
            _empty_failure_telemetry_snapshot
        ),
    ) -> None:
        bindings = verify_s5_mstar_production_bindings(
            spec=spec,
            identity=identity,
            production_core_identity=production_core_identity,
            fx0_qualification=fx0_qualification,
        )
        checked_identity = bindings["identity"]
        checked_core = bindings["production_core_identity"]

        selected = tuple(sources)
        if (
            len(selected) < 2
            or any(not isinstance(item, MStarSource) for item in selected)
            or [item.source_sequence for item in selected]
            != list(range(len(selected)))
        ):
            raise _fail("sources_invalid")
        if not callable(semantic_prepare) or not callable(latest_state_bind):
            raise _fail("semantic_callback_invalid")
        if not callable(commit_evidence):
            raise _fail("commit_evidence_callback_invalid")
        if not callable(clock_ns):
            raise _fail("clock_invalid")
        if not callable(failure_telemetry_snapshot):
            raise _fail("failure_telemetry_snapshot_invalid")
        root = Path(attempt_root)
        if root.exists():
            raise _fail("attempt_exists")

        self.attempt_root = root
        self.spec = spec
        self.identity = checked_identity
        self.production_core_identity = checked_core
        self.sources = selected
        self.semantic_prepare = semantic_prepare
        self.latest_state_bind = latest_state_bind
        self.commit_evidence = commit_evidence
        self.clock_ns = clock_ns
        self.failure_telemetry_snapshot = failure_telemetry_snapshot
        self._operation_ids = {
            source.source_sequence: payload_sha256(
                {
                    "run_id": spec.run_id,
                    "source_sequence": source.source_sequence,
                    "source_sha256": source.source_sha256,
                }
            )
            for source in selected
        }

    def _terminal_evidence(self, error: Exception) -> dict[str, object]:
        inspected = inspect_s5_attempt(self.attempt_root)
        events = inspected["events"]
        published = inspected["checkpoint"]["published_source_sequences"]
        error_type = type(error)
        return {
            "schema_version": _RUNNER_TERMINAL_FAILURE_SCHEMA,
            "run_id": self.spec.run_id,
            "method": "M*",
            "production_core_identity_sha256": self.production_core_identity[
                "identity_sha256"
            ],
            "status": "FAIL_CLOSED",
            "mergeable": False,
            "failure_code": "PIPELINE_OR_JOURNAL_EXCEPTION",
            "error_class": f"{error_type.__module__}.{error_type.__qualname__}",
            "semantic_error_code": None,
            "upstream_error_class": None,
            "events": events,
            "summary": {
                "event_count": len(events),
                "published_source_sequences": published,
            },
        }

    def _seal_failure_evidence(
        self, evidence: Mapping[str, object]
    ) -> tuple[dict[str, object], dict[str, str]]:
        source, error_class, semantic_code, upstream_class = (
            _failure_terminal_fields(evidence)
        )
        failure_code = evidence.get("failure_code")
        if not isinstance(failure_code, str):
            raise _fail("failure_code_missing")
        telemetry_status: str | None = None
        try:
            snapshot = self.failure_telemetry_snapshot()
            if isinstance(snapshot, (str, bytes)) or not isinstance(
                snapshot, Sequence
            ):
                raise S5MStarFailureEnvelopeError("snapshot_shape_invalid")
            selected_snapshot = tuple(
                event
                for event in snapshot
                if not isinstance(event, Mapping)
                or source is None
                or event.get("source_sequence") == source
            )
        except S5MStarFailureEnvelopeError:
            selected_snapshot = ()
            telemetry_status = "REJECTED_PRIVATE_FIELDS"
        except Exception:
            selected_snapshot = ()
            telemetry_status = "SNAPSHOT_ERROR"

        envelope_arguments = {
            "run_id": self.spec.run_id,
            "production_core_identity_sha256": self.production_core_identity[
                "identity_sha256"
            ],
            "failed_source_sequence": source,
            "pipeline_failure_code": failure_code,
            "pipeline_error_class": error_class,
            "semantic_error_code": semantic_code,
            "upstream_error_class": upstream_class,
        }
        try:
            envelope = build_s5_mstar_failure_envelope(
                **envelope_arguments,
                transport_events=selected_snapshot,
                telemetry_status=telemetry_status,
            )
        except S5MStarFailureEnvelopeError:
            try:
                envelope = build_s5_mstar_failure_envelope(
                    **envelope_arguments,
                    transport_events=(),
                    telemetry_status="REJECTED_PRIVATE_FIELDS",
                )
            except S5MStarFailureEnvelopeError:
                fallback_type = UnclassifiedFailure
                envelope = build_s5_mstar_failure_envelope(
                    run_id=self.spec.run_id,
                    production_core_identity_sha256=self.production_core_identity[
                        "identity_sha256"
                    ],
                    failed_source_sequence=source,
                    pipeline_failure_code="PIPELINE_OR_JOURNAL_EXCEPTION",
                    pipeline_error_class=(
                        f"{fallback_type.__module__}.{fallback_type.__qualname__}"
                    ),
                    semantic_error_code=None,
                    upstream_error_class=None,
                    transport_events=(),
                    telemetry_status="SANITIZED_FALLBACK",
                )
        binding = write_s5_mstar_failure_envelope(
            self.attempt_root / "failure_envelope.json",
            envelope,
        )
        wrapped = {
            "schema_version": _RUNNER_FAILURE_SCHEMA,
            "inner_evidence_schema_version": evidence.get("schema_version"),
            "run_id": self.spec.run_id,
            "method": "M*",
            "production_core_identity_sha256": self.production_core_identity[
                "identity_sha256"
            ],
            "status": "FAIL_CLOSED",
            "mergeable": False,
            "failure_code": envelope["pipeline_failure_code"],
            "error_class": envelope["pipeline_error_class"],
            "failed_source_sequence": envelope["failed_source_sequence"],
            "semantic_error_code": envelope["semantic_error_code"],
            "upstream_error_class": envelope["upstream_error_class"],
            "events": deepcopy(evidence.get("events")),
            "summary": deepcopy(evidence.get("summary")),
            **binding,
        }
        return (
            verify_s5_mstar_runner_failure_evidence(
                wrapped,
                failure_envelope_path=self.attempt_root / "failure_envelope.json",
            ),
            binding,
        )

    async def run(self) -> dict[str, object]:
        core_sha = str(self.production_core_identity["identity_sha256"])
        store = S5AttemptStore.create(
            self.attempt_root,
            run_id=self.spec.run_id,
            method="M*",
            production_core_identity_sha256=core_sha,
            source_sha256s=tuple(item.source_sha256 for item in self.sources),
        )
        try:
            journal = S5MStarPublicationJournal.create(
                self.attempt_root / "publication_journal.jsonl"
            )

            async def persist_event(event: Mapping[str, object]) -> None:
                store.append_event(event)
                event_type = event.get("event_type")
                source_sequence = event.get("source_sequence")
                if event_type == "intent":
                    journal.record_intent(
                        self._operation_ids[int(source_sequence)],
                        str(event["source_sha256"]),
                    )
                elif event_type == "publication":
                    journal.record_publication(
                        self._operation_ids[int(source_sequence)]
                    )

            async def bind_and_record_commit(
                prepared: object,
                logical_time: int,
                source_sequence: int,
                visible_prefix: tuple[int, ...],
            ) -> object:
                bound = self.latest_state_bind(
                    prepared, logical_time, source_sequence, visible_prefix
                )
                if not inspect.isawaitable(bound):
                    raise TypeError("latest_state_bind must be async")
                bind_result = await bound
                commit = self.commit_evidence(
                    bind_result,
                    logical_time,
                    source_sequence,
                    visible_prefix,
                )
                if inspect.isawaitable(commit):
                    commit = await commit
                commit_sha256 = _sha(commit, "commit_evidence_invalid")
                journal.record_commit(
                    self._operation_ids[source_sequence], commit_sha256
                )
                return bind_result

            evidence = await run_mstar_pipeline(
                spec=self.spec,
                sources=self.sources,
                semantic_prepare=self.semantic_prepare,
                latest_state_bind=bind_and_record_commit,
                persist_event=persist_event,
                clock_ns=self.clock_ns,
            )
        except Exception as error:
            evidence = self._terminal_evidence(error)

        failure_binding: dict[str, str] | None = None
        if evidence.get("status") == "FAIL_CLOSED":
            evidence, failure_binding = self._seal_failure_evidence(evidence)
        finalized = store.finalize(evidence)
        result = {
            **finalized,
            "payload": evidence,
            "production_identity_sha256": self.identity["identity_sha256"],
            "production_core_identity_sha256": core_sha,
        }
        if failure_binding is not None:
            result.update(failure_binding)
        return result


__all__ = [
    "S5MStarProductionRunner",
    "S5MStarProductionRunnerError",
    "verify_s5_mstar_runner_failure_evidence",
    "verify_s5_mstar_production_bindings",
]
