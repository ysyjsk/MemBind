"""Durable, content-free wrapper for the one-chain S2 live sanity run.

The scientific implementation remains in :mod:`paper_eval.s2_live`.  Small
proxies observe its retrieval, Reader, and Judge boundaries so durability
does not create a second execution path or gain construction/cleanup powers.
"""

from __future__ import annotations

import inspect
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import PROTOCOL_VERSION
from .artifacts import (
    append_jsonl_durable,
    atomic_write_json,
    finalize_envelope,
    payload_sha256,
    sha256_file,
)
from .s2_live import (
    Judge,
    Reader,
    S2LiveInputs,
    S2LiveQualification,
    finalize_s2_qualification,
    run_s2_numeric_sanity,
)


CHECKPOINT_SCHEMA = "membind.paper-eval-v3.s2-live-checkpoint.v1"
EVENT_SCHEMA = "membind.paper-eval-v3.s2-live-event.v1"
_STAGE_ORDER = ("retrieval", "reader", "judge")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

# Evidence is fail-closed. Unknown fields are not silently copied because a
# future backend could otherwise add raw prompt/output fields to artifacts.
_SAFE_EVIDENCE_KEYS = frozenset(
    {
        "status",
        "model",
        "config_sha256",
        "prompt_sha256",
        "output_sha256",
        "prompt_character_count",
        "prompt_byte_count",
        "output_character_count",
        "output_byte_count",
        "prompt_tokens",
        "completion_tokens",
        "parse_status",
        "retry_count",
        "label",
        "error_class",
        "retrieval_result_count",
        "result_sha256",
    }
)
_SAFE_STATUS = frozenset(
    {"SUCCESS", "INVALID_OUTPUT", "SERVICE_ERROR", "YES", "NO", "INVALID", "NOT_RUN"}
)


class S2DurabilityError(RuntimeError):
    """A sanitized S2 live failure with durable evidence already written."""


class S2RunAlreadyStarted(S2DurabilityError):
    """The run ID has a start marker and must never issue a second chain."""


@dataclass(frozen=True)
class S2DurableResult:
    qualification: S2LiveQualification
    artifact: Mapping[str, Any]

    @property
    def payload(self) -> Mapping[str, Any]:
        return self.artifact["payload"]


def _identifier(label: str, value: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"invalid {label}")
    return value


def _nonnegative_int(label: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"unsafe evidence value: {label}")
    return value


class S2DurableRun:
    """Persist one and only one S2 retrieval/Reader/Judge attempt."""

    def __init__(
        self,
        artifact_root: Path,
        inputs: S2LiveInputs,
        *,
        final_output: Path | None = None,
    ) -> None:
        _identifier("run_id", inputs.run_id)
        _identifier("history_id", inputs.history_id)
        _identifier("namespace", inputs.namespace)
        self.inputs = inputs
        self.run_dir = Path(artifact_root) / inputs.run_id
        self.events_path = self.run_dir / "events.jsonl"
        self.checkpoint_path = self.run_dir / "checkpoint.json"
        self.failure_path = self.run_dir / "failure.json"
        self.start_marker_path = self.run_dir / ".started"
        self.final_output = (
            Path(final_output) if final_output is not None else self.run_dir / "result.json"
        )
        self._checkpoint = self._base_checkpoint()
        self._active_stage = "preflight"
        self._started = False

    def _base_checkpoint(self) -> dict[str, Any]:
        return {
            "schema_version": CHECKPOINT_SCHEMA,
            "run_id": self.inputs.run_id,
            "history_id": self.inputs.history_id,
            "namespace": self.inputs.namespace,
            "status": "running",
            "completed_stages": [],
            "chain_counts": {stage: 0 for stage in _STAGE_ORDER},
            "retrieval_result_count": None,
            "reader_status": None,
            "judge_status": None,
            "error_class": None,
            "failure_stage": None,
            "result_sha256": None,
            "qualification_evidence_sha256": None,
            "adapter_identity_sha256": None,
        }

    @property
    def active_stage(self) -> str:
        return self._active_stage

    def set_active_stage(self, stage: str) -> None:
        if stage not in {*_STAGE_ORDER, "preflight", "close", "finalize"}:
            raise ValueError("invalid S2 active stage")
        self._active_stage = stage

    def bind_execution_evidence(
        self, *, qualification_evidence_sha256: str, adapter_identity_sha256: str
    ) -> None:
        """Bind immutable qualification and adapter evidence before live calls."""

        for label, value in (
            ("qualification evidence", qualification_evidence_sha256),
            ("adapter identity", adapter_identity_sha256),
        ):
            if _SHA_RE.fullmatch(value) is None:
                raise ValueError(f"{label} hash is invalid")
        if self._started:
            raise S2DurabilityError("S2 execution evidence bound after start")
        self._checkpoint["qualification_evidence_sha256"] = (
            qualification_evidence_sha256
        )
        self._checkpoint["adapter_identity_sha256"] = adapter_identity_sha256

    def sanitize_evidence(self, evidence: Mapping[str, Any]) -> dict[str, Any]:
        """Validate a no-content backend artifact instead of filtering it."""

        if not isinstance(evidence, Mapping):
            raise ValueError("unsafe evidence shape")
        unknown = sorted(str(key) for key in evidence if key not in _SAFE_EVIDENCE_KEYS)
        if unknown:
            raise ValueError("unsafe evidence fields: " + ",".join(unknown))
        sanitized: dict[str, Any] = {}
        for key, value in evidence.items():
            if key in {
                "config_sha256",
                "prompt_sha256",
                "output_sha256",
                "result_sha256",
            }:
                if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
                    raise ValueError(f"unsafe evidence value: {key}")
            elif key in {"status", "parse_status"}:
                if value not in _SAFE_STATUS:
                    raise ValueError(f"unsafe evidence value: {key}")
            elif key == "model":
                _identifier("evidence model", value)
            elif key in {
                "prompt_character_count",
                "prompt_byte_count",
                "output_character_count",
                "output_byte_count",
                "prompt_tokens",
                "completion_tokens",
                "retry_count",
                "retrieval_result_count",
            }:
                value = _nonnegative_int(key, value)
            elif key == "label":
                if type(value) is not bool:
                    raise ValueError("unsafe evidence value: label")
            elif key == "error_class":
                if value is not None:
                    _identifier("evidence error_class", value)
            sanitized[str(key)] = value
        return sanitized

    def _write_checkpoint(self) -> None:
        body = dict(self._checkpoint)
        body["completed_stages"] = list(self._checkpoint["completed_stages"])
        body["chain_counts"] = dict(self._checkpoint["chain_counts"])
        body.pop("payload_sha256", None)
        body["payload_sha256"] = payload_sha256(body)
        atomic_write_json(self.checkpoint_path, body)

    def _event(self, event_type: str, *, stage: str, evidence: Mapping[str, Any] | None = None) -> None:
        event: dict[str, Any] = {
            "schema_version": EVENT_SCHEMA,
            "run_id": self.inputs.run_id,
            "history_id": self.inputs.history_id,
            "namespace": self.inputs.namespace,
            "event_type": event_type,
            "stage": stage,
            "timestamp_ns": time.time_ns(),
        }
        if evidence:
            event["evidence"] = dict(evidence)
        event["payload_sha256"] = payload_sha256(event)
        append_jsonl_durable(self.events_path, event)

    async def start(self) -> None:
        """Acquire the exclusive run marker before any live call."""

        if (
            self._checkpoint["qualification_evidence_sha256"] is None
            or self._checkpoint["adapter_identity_sha256"] is None
        ):
            raise S2DurabilityError("S2 execution evidence is not bound")

        self.run_dir.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self.start_marker_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o664,
            )
        except FileExistsError:
            raise S2RunAlreadyStarted("S2 run ID already started") from None
        try:
            os.write(descriptor, b"one-chain-only\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(self.run_dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        self._started = True
        self._write_checkpoint()
        self._event("start", stage="preflight")

    def complete_stage(self, stage: str, evidence: Mapping[str, Any]) -> None:
        if stage not in _STAGE_ORDER:
            raise ValueError("invalid S2 stage")
        completed = list(self._checkpoint["completed_stages"])
        expected = _STAGE_ORDER[len(completed)] if len(completed) < len(_STAGE_ORDER) else None
        if stage != expected or self._checkpoint["chain_counts"][stage] != 0:
            raise S2DurabilityError("S2 chain order or cardinality violation")
        safe = self.sanitize_evidence(evidence)
        self._checkpoint["chain_counts"][stage] = 1
        completed.append(stage)
        self._checkpoint["completed_stages"] = completed
        if stage == "retrieval":
            self._checkpoint["retrieval_result_count"] = safe["retrieval_result_count"]
        elif stage == "reader":
            self._checkpoint["reader_status"] = safe["status"]
        else:
            self._checkpoint["judge_status"] = safe["status"]
        self._event(stage, stage=stage, evidence=safe)
        self._write_checkpoint()

    def complete(self, *, result_sha256: str) -> None:
        if self._checkpoint["completed_stages"] != list(_STAGE_ORDER):
            raise S2DurabilityError("S2 completed without all three stages")
        if _SHA_RE.fullmatch(result_sha256) is None:
            raise ValueError("invalid S2 result hash")
        self._checkpoint.update(
            status="completed",
            error_class=None,
            failure_stage=None,
            result_sha256=result_sha256,
        )
        self._event("completed", stage="finalize", evidence={"result_sha256": result_sha256})
        self._write_checkpoint()

    def fail(self, error: BaseException, *, git_commit: str) -> None:
        """Seal only stable failure metadata; exception text is never copied."""

        error_class = type(error).__name__
        _identifier("error class", error_class)
        stage = self._active_stage
        self._checkpoint.update(
            status="incomplete",
            error_class=error_class,
            failure_stage=stage,
            result_sha256=None,
        )
        self._event(
            "failure",
            stage=stage,
            evidence={"error_class": error_class},
        )
        self._write_checkpoint()
        payload = {
            "stage": "S2",
            "method": "U0",
            "history_id": self.inputs.history_id,
            "namespace": self.inputs.namespace,
            "status": "FAIL",
            "failure_stage": stage,
            "error_class": error_class,
            "completed_stages": list(self._checkpoint["completed_stages"]),
            "chain_counts": dict(self._checkpoint["chain_counts"]),
            "checkpoint_sha256": sha256_file(self.checkpoint_path),
            "events_sha256": sha256_file(self.events_path),
            "qualification_evidence_sha256": self._checkpoint[
                "qualification_evidence_sha256"
            ],
            "adapter_identity_sha256": self._checkpoint[
                "adapter_identity_sha256"
            ],
            "retry_authorized": False,
        }
        artifact = finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=git_commit,
            run_id=self.inputs.run_id,
        )
        atomic_write_json(self.failure_path, artifact)


class _DurableGraph:
    def __init__(self, graph: Any, run: S2DurableRun) -> None:
        self._graph = graph
        self._run = run
        self.driver = graph.driver

    async def search(self, *args: Any, **kwargs: Any) -> list[Any]:
        self._run.set_active_stage("retrieval")
        value = await self._graph.search(*args, **kwargs)
        results = list(value)
        # Retrieval content stays in memory; only its bounded cardinality is durable.
        self._run.complete_stage(
            "retrieval",
            {"retrieval_result_count": min(len(results), 10)},
        )
        return results

    async def close(self) -> None:
        previous_stage = self._run.active_stage
        close = getattr(self._graph, "close", None)
        if callable(close):
            try:
                value = close()
                if inspect.isawaitable(value):
                    await value
            except Exception:
                self._run.set_active_stage("close")
                raise
        self._run.set_active_stage(previous_stage)


class _DurableReader:
    def __init__(self, reader: Reader, run: S2DurableRun) -> None:
        self._reader = reader
        self._run = run

    async def answer(self, *args: Any, **kwargs: Any) -> Any:
        self._run.set_active_stage("reader")
        result = await self._reader.answer(*args, **kwargs)
        artifact = getattr(result, "to_artifact", None)
        if not callable(artifact):
            raise ValueError("Reader result lacks sanitized artifact")
        self._run.complete_stage("reader", artifact())
        return result


class _DurableJudge:
    def __init__(self, judge: Judge, run: S2DurableRun) -> None:
        self._judge = judge
        self._run = run

    async def evaluate(self, *args: Any, **kwargs: Any) -> Mapping[str, Any]:
        self._run.set_active_stage("judge")
        result = dict(await self._judge.evaluate(*args, **kwargs))
        self._run.complete_stage("judge", result)
        return result


async def run_s2_durable(
    *,
    run: S2DurableRun,
    graph: Any,
    episodes: Sequence[Any],
    reader: Reader,
    judge: Judge,
    git_commit: str,
    qualification_evidence_sha256: str,
    adapter_identity_sha256: str,
) -> S2DurableResult:
    """Execute one chain, finalizing either sanitized success or failure evidence."""

    if not isinstance(git_commit, str) or not git_commit:
        raise ValueError("git_commit must be nonempty")
    if _SHA_RE.fullmatch(qualification_evidence_sha256) is None:
        raise ValueError("qualification evidence hash is invalid")
    if _SHA_RE.fullmatch(adapter_identity_sha256) is None:
        raise ValueError("adapter identity hash is invalid")
    run.bind_execution_evidence(
        qualification_evidence_sha256=qualification_evidence_sha256,
        adapter_identity_sha256=adapter_identity_sha256,
    )
    try:
        await run.start()
    except Exception:
        close = getattr(graph, "close", None)
        if callable(close):
            try:
                value = close()
                if inspect.isawaitable(value):
                    await value
            except Exception:
                # The marker/qualification error is the actionable result;
                # cleanup is best-effort and must not mask it.
                pass
        raise
    run.set_active_stage("retrieval")
    try:
        result = await run_s2_numeric_sanity(
            inputs=run.inputs,
            graph=_DurableGraph(graph, run),
            episodes=episodes,
            reader=_DurableReader(reader, run),
            judge=_DurableJudge(judge, run),
        )
        # Rebuild with the same numeric result but only strict evidence views.
        sanitized = S2LiveQualification(
            edge_attributed_source_session_coverage_at_10=(
                result.edge_attributed_source_session_coverage_at_10
            ),
            qa_accuracy=result.qa_accuracy,
            edge_result_count=result.edge_result_count,
            retrieved_source_session_ids=result.retrieved_source_session_ids,
            reader_status=result.reader_status,
            reader_evidence=run.sanitize_evidence(result.reader_evidence),
            judge_status=result.judge_status,
            judge_evidence=run.sanitize_evidence(result.judge_evidence),
        )
        run.set_active_stage("finalize")
        artifact = finalize_s2_qualification(
            run.final_output,
            result=sanitized,
            inputs=run.inputs,
            git_commit=git_commit,
            qualification_evidence_sha256=qualification_evidence_sha256,
            adapter_identity_sha256=adapter_identity_sha256,
        )
        run.complete(result_sha256=sha256_file(run.final_output))
        return S2DurableResult(qualification=sanitized, artifact=artifact)
    except Exception as error:
        try:
            run.fail(error, git_commit=git_commit)
        except Exception:
            # Preserve the original stable class even if the storage medium fails.
            pass
        raise S2DurabilityError(
            f"S2 failed at {run.active_stage}: {type(error).__name__}"
        ) from None
