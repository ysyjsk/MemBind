"""Small, isolated planning/checkpoint primitives for the Native U0 screen.

The live composition is intentionally kept outside the old S1/C2 controllers.
These helpers own only the new ``native_baseline`` artifact namespace and are
safe to exercise with offline doubles before any Graphiti client is created.
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .artifacts import payload_sha256


NATIVE_BASELINE_SCHEMA = "membind.paper-eval-v3.native-baseline.v1"
DEVELOPMENT_HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
_RUN_ID_RE = re.compile(r"^nb-[a-z0-9][a-z0-9-]{2,63}$")
_NAMESPACE_RE = re.compile(r"^nc-e1e2-[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STATUSES = {"planned", "running", "completed", "incomplete_non_mergeable"}


@dataclass(frozen=True)
class HistoryPlan:
    run_id: str
    history_id: str
    source_order: int
    namespace: str
    method: str = "U0"
    repeat_id: int = 0


@dataclass(frozen=True)
class NativeBaselinePlan:
    run_id: str
    histories: tuple[HistoryPlan, ...]
    mode: str = "serial"


def _validate_run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError("run_id is invalid")


def _namespace(run_id: str, history_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}\0{history_id}".encode("ascii")).hexdigest()
    return f"nc-e1e2-{digest[:16]}"


def build_native_baseline_plan(
    run_id: str,
    *,
    history_ids: Sequence[str] = DEVELOPMENT_HISTORIES,
) -> NativeBaselinePlan:
    """Build the exact ordered-four serial plan; reject substitutions."""

    _validate_run_id(run_id)
    supplied = tuple(str(value) for value in history_ids)
    if supplied != DEVELOPMENT_HISTORIES:
        raise ValueError("fixed development histories are required")
    histories = tuple(
        HistoryPlan(
            run_id=run_id,
            history_id=history_id,
            source_order=index,
            namespace=_namespace(run_id, history_id),
        )
        for index, history_id in enumerate(supplied)
    )
    if len({item.namespace for item in histories}) != len(histories):
        raise ValueError("history namespace collision")
    return NativeBaselinePlan(run_id=run_id, histories=histories, mode="serial")


def make_checkpoint(
    *,
    run_id: str,
    history_id: str,
    namespace: str,
    expected_sequences: Sequence[int],
    completed_sequences: Sequence[int],
    status: str,
    error_class: str | None = None,
) -> dict[str, Any]:
    """Create an atomically writable checkpoint with a content hash."""

    _validate_run_id(run_id)
    if not isinstance(history_id, str) or history_id not in DEVELOPMENT_HISTORIES:
        raise ValueError("history_id is not in the fixed development set")
    if not isinstance(namespace, str) or _NAMESPACE_RE.fullmatch(namespace) is None:
        raise ValueError("namespace is invalid")
    expected = [int(value) for value in expected_sequences]
    completed = [int(value) for value in completed_sequences]
    if expected != list(range(len(expected))):
        raise ValueError("expected sequences are not contiguous")
    if completed != expected[: len(completed)]:
        raise ValueError("completed sequences are not a prefix")
    if status not in _STATUSES:
        raise ValueError("checkpoint status is invalid")
    body: dict[str, Any] = {
        "schema_version": NATIVE_BASELINE_SCHEMA,
        "run_id": run_id,
        "history_id": history_id,
        "namespace": namespace,
        "method": "U0",
        "repeat_id": 0,
        "status": status,
        "expected_sequences": expected,
        "completed_sequences": completed,
        "error_class": error_class,
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def verify_checkpoint(value: Mapping[str, Any]) -> dict[str, Any]:
    """Verify hash, identity, status, and exact prefix semantics."""

    if not isinstance(value, Mapping):
        raise ValueError("checkpoint must be an object")
    candidate = dict(value)
    observed_hash = candidate.pop("payload_sha256", None)
    if observed_hash != payload_sha256(candidate):
        raise ValueError("checkpoint payload hash mismatch")
    if candidate.get("schema_version") != NATIVE_BASELINE_SCHEMA:
        raise ValueError("checkpoint schema mismatch")
    _validate_run_id(candidate.get("run_id"))
    history_id = candidate.get("history_id")
    if history_id not in DEVELOPMENT_HISTORIES:
        raise ValueError("checkpoint history identity mismatch")
    namespace = candidate.get("namespace")
    if not isinstance(namespace, str) or _NAMESPACE_RE.fullmatch(namespace) is None:
        raise ValueError("checkpoint namespace invalid")
    expected = candidate.get("expected_sequences")
    completed = candidate.get("completed_sequences")
    if not isinstance(expected, list) or not isinstance(completed, list):
        raise ValueError("checkpoint sequences invalid")
    if expected != list(range(len(expected))):
        raise ValueError("checkpoint expected sequence contract mismatch")
    if completed != expected[: len(completed)]:
        raise ValueError("checkpoint completed sequence prefix mismatch")
    if candidate.get("method") != "U0" or candidate.get("repeat_id") != 0:
        raise ValueError("checkpoint method identity mismatch")
    if candidate.get("status") not in _STATUSES:
        raise ValueError("checkpoint status invalid")
    result = dict(candidate)
    result["payload_sha256"] = observed_hash
    return result


def decide_history_resume(
    checkpoint: Mapping[str, Any],
    *,
    result_exists: bool,
) -> str:
    """Separate construction progress from quality/result finalization."""

    verified = verify_checkpoint(checkpoint)
    status = verified["status"]
    if status == "incomplete_non_mergeable":
        raise ValueError("history attempt is incomplete and non-mergeable")
    full_prefix = verified["completed_sequences"] == verified["expected_sequences"]
    if result_exists and not full_prefix:
        raise ValueError("history result exists before complete source prefix")
    if status == "completed":
        if not full_prefix:
            raise ValueError("completed checkpoint does not cover every source")
        return "FINALIZED" if result_exists else "QUALITY_PENDING"
    if result_exists:
        return "FINALIZATION_PENDING"
    if full_prefix:
        return "QUALITY_PENDING"
    return "CONSTRUCTION_PENDING"


def should_pause_before_quality(
    checkpoint: Mapping[str, Any],
    *,
    quality_exists: bool,
    result_exists: bool,
) -> bool:
    """Identify the narrow full-prefix boundary before any quality request."""

    verified = verify_checkpoint(checkpoint)
    if verified["status"] == "incomplete_non_mergeable":
        return False
    full_prefix = verified["completed_sequences"] == verified["expected_sequences"]
    return full_prefix and not quality_exists and not result_exists


def upgrade_episode_phase_span_counts(
    episode_metrics: Sequence[Mapping[str, Any]],
    spans: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Upgrade legacy derived rows using only their immutable span stream."""

    counts: dict[int, dict[str, int]] = {}
    for span in spans:
        if not isinstance(span, Mapping):
            raise ValueError("span row must be an object")
        sequence = span.get("source_sequence")
        phase = span.get("phase")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("span source sequence is invalid")
        if not isinstance(phase, str) or not phase:
            raise ValueError("span phase is invalid")
        phase_counts = counts.setdefault(sequence, {})
        phase_counts[phase] = phase_counts.get(phase, 0) + 1

    upgraded: list[dict[str, Any]] = []
    observed: set[int] = set()
    for raw in episode_metrics:
        if not isinstance(raw, Mapping):
            raise ValueError("episode metric row must be an object")
        row = deepcopy(dict(raw))
        identity = row.get("identity")
        if not isinstance(identity, Mapping):
            raise ValueError("episode metric identity is missing")
        sequence = identity.get("source_sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("episode metric source sequence is invalid")
        if sequence in observed:
            raise ValueError("episode metric source sequence is duplicate")
        observed.add(sequence)
        phases = row.get("phase_metrics")
        if not isinstance(phases, Mapping):
            raise ValueError("episode phase metrics are missing")
        expected_counts = counts.get(sequence)
        if expected_counts is None or set(phases) != set(expected_counts):
            raise ValueError("episode phase coverage differs from span stream")
        for phase, value in phases.items():
            if not isinstance(value, Mapping):
                raise ValueError("episode phase metric is invalid")
            metric = dict(value)
            expected_count = expected_counts[str(phase)]
            if "span_count" in metric and metric["span_count"] != expected_count:
                raise ValueError("episode phase span_count mismatch")
            metric["span_count"] = expected_count
            phases[phase] = metric
        upgraded.append(row)
    if observed != set(counts):
        raise ValueError("episode/span source coverage mismatch")
    return upgraded


def seal_history_result(value: Mapping[str, Any]) -> dict[str, Any]:
    """Attach a deterministic hash after all history-level evidence is ready."""

    if not isinstance(value, Mapping):
        raise ValueError("history result must be an object")
    if "payload_sha256" in value:
        raise ValueError("history result is already sealed")
    body = dict(value)
    if body.get("status") != "completed":
        raise ValueError("history result is not completed")
    body["payload_sha256"] = payload_sha256(body)
    return body


def verify_history_result(
    value: Mapping[str, Any],
    *,
    expected_plan: HistoryPlan,
) -> dict[str, Any]:
    """Verify a sealed history result against its exact Native plan identity."""

    if not isinstance(value, Mapping):
        raise ValueError("history result must be an object")
    candidate = dict(value)
    observed_hash = candidate.pop("payload_sha256", None)
    if observed_hash != payload_sha256(candidate):
        raise ValueError("history result payload hash mismatch")
    if candidate.get("schema_version") != "membind.paper-eval-v3.native-baseline-history.v1":
        raise ValueError("history result schema mismatch")
    for field, expected in (
        ("run_id", expected_plan.run_id),
        ("history_id", expected_plan.history_id),
        ("namespace", expected_plan.namespace),
        ("method", "U0"),
        ("repeat_id", 0),
        ("status", "completed"),
    ):
        if candidate.get(field) != expected:
            raise ValueError(f"history result {field} mismatch")
    if not isinstance(candidate.get("quality"), Mapping):
        raise ValueError("history result quality is missing")
    if not isinstance(candidate.get("aggregate"), Mapping):
        raise ValueError("history result aggregate is missing")
    result = dict(candidate)
    result["payload_sha256"] = observed_hash
    return result


def build_n0_artifact(
    *,
    run_id: str,
    construction_models: Sequence[Mapping[str, Any]],
    embedding_models: Sequence[Mapping[str, Any]],
    neo4j_ready: bool,
    plan: NativeBaselinePlan,
    overlay_sha256: str,
) -> dict[str, Any]:
    """Project read-only service probes into a secret-free N0 artifact."""

    _validate_run_id(run_id)
    if plan.run_id != run_id or plan.mode != "serial":
        raise ValueError("N0 plan identity mismatch")
    if not isinstance(overlay_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", overlay_sha256
    ):
        raise ValueError("overlay hash is invalid")

    def model_row(
        rows: Sequence[Mapping[str, Any]], expected_id: str, expected_context: int
    ) -> dict[str, Any]:
        matches = [row for row in rows if isinstance(row, Mapping) and row.get("id") == expected_id]
        if len(matches) != 1:
            raise ValueError("service model identity mismatch")
        max_len = matches[0].get("max_model_len")
        if isinstance(max_len, bool) or not isinstance(max_len, int):
            raise ValueError("service context identity missing")
        return {"served_model_id": expected_id, "max_model_len": max_len, "expected_context": expected_context}

    construction = model_row(construction_models, "qwen3-32b-fp8", 65536)
    embedding = model_row(embedding_models, "qwen3-embedding-0.6b", 32768)
    status = "PASS" if neo4j_ready and construction["max_model_len"] >= 65536 else "BLOCKED"
    body: dict[str, Any] = {
        "schema_version": NATIVE_BASELINE_SCHEMA,
        "stage": "N0",
        "run_id": run_id,
        "status": status,
        "construction": construction,
        "embedding": embedding,
        "neo4j_ready": bool(neo4j_ready),
        "target_history_count": len(plan.histories),
        "target_histories": [item.history_id for item in plan.histories],
        "target_namespaces": [item.namespace for item in plan.histories],
        "overlay_sha256": overlay_sha256,
        "secrets_persisted": False,
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def verify_native_quality_bindings(
    *,
    frozen_baseline: Mapping[str, Any],
    reader_config_sha256: str,
    judge_config_sha256: str,
) -> dict[str, str]:
    """Fail closed unless the live quality adapters match Native-v2 freeze."""

    if not isinstance(frozen_baseline, Mapping):
        raise ValueError("Native-v2 baseline freeze is invalid")
    payload = frozen_baseline.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("Native-v2 baseline freeze payload is invalid")
    baseline_id = payload.get("baseline_id")
    if baseline_id != "native-graphiti-u0-reader-v2":
        raise ValueError("Native Reader-v2 baseline identity mismatch")
    policy = payload.get("common_evaluation_policy")
    if not isinstance(policy, Mapping):
        raise ValueError("Native-v2 evaluation policy is invalid")
    expected_reader = policy.get("reader_config_sha256")
    expected_judge = policy.get("judge_component_config_sha256")
    if not all(
        isinstance(value, str) and _SHA256_RE.fullmatch(value)
        for value in (
            expected_reader,
            expected_judge,
            reader_config_sha256,
            judge_config_sha256,
        )
    ):
        raise ValueError("Native-v2 quality binding hash is invalid")
    if reader_config_sha256 != expected_reader:
        raise ValueError("Native Reader-v2 configuration mismatch")
    if judge_config_sha256 != expected_judge:
        raise ValueError("Native Judge configuration mismatch")
    return {
        "baseline_id": str(baseline_id),
        "reader_config_sha256": reader_config_sha256,
        "judge_config_sha256": judge_config_sha256,
    }


def validate_read_only_quality_graph(
    *,
    construction_graph: Any,
    retrieval_graph: Any,
) -> Any:
    """Bind quality retrieval to a separate driver with schema init disabled.

    Native construction intentionally awaits Graphiti's automatically scheduled
    Neo4j initialization task.  S2-R0 retrieval has a stricter read-only
    boundary: its driver must never schedule that task at all.  Keeping the two
    Graphiti objects separate preserves both contracts without mutating the
    completed construction driver.
    """

    if construction_graph is None or retrieval_graph is None:
        raise ValueError("Native quality Graphiti binding is incomplete")
    construction_driver = getattr(construction_graph, "driver", None)
    retrieval_driver = getattr(retrieval_graph, "driver", None)
    if construction_driver is None or retrieval_driver is None:
        raise ValueError("Native quality Graphiti driver binding is incomplete")
    if construction_graph is retrieval_graph or construction_driver is retrieval_driver:
        raise ValueError("Native quality retrieval requires a separate read-only driver")

    construction_init = getattr(construction_driver, "_init_task", None)
    if construction_init is not None:
        done = getattr(construction_init, "done", None)
        if not callable(done) or done() is not True:
            raise ValueError("Native construction schema initialization is incomplete")
    if getattr(retrieval_driver, "_init_task", None) is not None:
        raise ValueError("Native quality retrieval driver scheduled schema initialization")
    if not callable(getattr(retrieval_driver, "execute_query", None)):
        raise ValueError("Native quality retrieval driver has no query API")
    if not callable(getattr(retrieval_graph, "search_", None)):
        raise ValueError("Native quality retrieval Graphiti has no search API")
    return retrieval_graph


__all__ = [
    "DEVELOPMENT_HISTORIES",
    "HistoryPlan",
    "NATIVE_BASELINE_SCHEMA",
    "NativeBaselinePlan",
    "build_native_baseline_plan",
    "build_n0_artifact",
    "decide_history_resume",
    "make_checkpoint",
    "seal_history_result",
    "should_pause_before_quality",
    "upgrade_episode_phase_span_counts",
    "validate_read_only_quality_graph",
    "verify_native_quality_bindings",
    "verify_checkpoint",
    "verify_history_result",
]
