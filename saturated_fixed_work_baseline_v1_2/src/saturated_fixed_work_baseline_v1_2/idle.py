"""Consecutive process-idle evidence for both vLLM services and Neo4j."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from .services import direct_get_text
from .telemetry import parse_vllm_026_metrics


class IdleEvidenceError(ValueError):
    """Service idle evidence is malformed, incomplete, or not append-only."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _default_getter(url: str, timeout_s: float) -> Mapping[str, Any]:
    return direct_get_text(url, timeout_s=timeout_s)


def _vllm_idle(
    *,
    repository_root: Path,
    port: int,
    http_getter: Callable[[str, float], Mapping[str, Any]],
    timeout_s: float,
) -> dict[str, Any]:
    try:
        text = http_getter(
            f"http://10.87.5.247:{port}/metrics", timeout_s
        )["text"]
    except (KeyError, TypeError, OSError, ValueError):
        raise IdleEvidenceError("VLLM_IDLE_METRICS_UNAVAILABLE") from None
    observation = parse_vllm_026_metrics(
        str(text),
        timestamp_ns=time.monotonic_ns(),
        repository_root=repository_root,
    )
    snapshot = observation.value
    if snapshot is None:
        raise IdleEvidenceError("VLLM_IDLE_METRICS_INVALID")
    running = float(snapshot.values["running_requests"])
    waiting = float(snapshot.values["waiting_requests"])
    return {
        "port": port,
        "running_requests": running,
        "waiting_requests": waiting,
        "idle": running == 0.0 and waiting == 0.0,
    }


def _neo4j_idle(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        result = {"idle": value, "active_transactions": 0 if value else None}
    elif isinstance(value, Mapping):
        result = dict(value)
    else:
        raise IdleEvidenceError("NEO4J_IDLE_EVIDENCE_INVALID")
    active = result.get("active_transactions")
    idle = result.get("idle")
    if (
        type(idle) is not bool
        or isinstance(active, bool)
        or not isinstance(active, int)
        or active < 0
        or idle != (active == 0)
    ):
        raise IdleEvidenceError("NEO4J_IDLE_EVIDENCE_INVALID")
    return {"idle": idle, "active_transactions": active}


def collect_idle_evidence(
    *,
    repository_root: Path,
    neo4j_idle_probe: Callable[[], Any],
    sample_count: int = 2,
    interval_s: float = 1.0,
    timeout_s: float = 10.0,
    http_getter: Callable[[str, float], Mapping[str, Any]] = _default_getter,
    sleep: Callable[[float], Any] = time.sleep,
) -> dict[str, Any]:
    if (
        not repository_root.is_dir()
        or not callable(neo4j_idle_probe)
        or not callable(http_getter)
        or not callable(sleep)
        or isinstance(sample_count, bool)
        or not isinstance(sample_count, int)
        or sample_count < 2
        or isinstance(interval_s, bool)
        or not isinstance(interval_s, (int, float))
        or not math.isfinite(interval_s)
        or interval_s < 0
        or isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or timeout_s <= 0
    ):
        raise IdleEvidenceError("IDLE_PROBE_CONFIGURATION_INVALID")
    samples: list[dict[str, Any]] = []
    for index in range(sample_count):
        construction = _vllm_idle(
            repository_root=repository_root,
            port=8000,
            http_getter=http_getter,
            timeout_s=float(timeout_s),
        )
        embedding = _vllm_idle(
            repository_root=repository_root,
            port=8001,
            http_getter=http_getter,
            timeout_s=float(timeout_s),
        )
        try:
            neo4j = _neo4j_idle(neo4j_idle_probe())
        except IdleEvidenceError:
            raise
        except Exception:
            raise IdleEvidenceError("NEO4J_IDLE_PROBE_FAILED") from None
        samples.append(
            {
                "ordinal": index + 1,
                "monotonic_ns": time.monotonic_ns(),
                "wall_time": datetime.now().astimezone().isoformat(),
                "construction": construction,
                "embedding": embedding,
                "neo4j": neo4j,
                "idle": construction["idle"]
                and embedding["idle"]
                and neo4j["idle"],
            }
        )
        if index + 1 < sample_count:
            sleep(float(interval_s))
    all_idle = all(sample["idle"] is True for sample in samples)
    result = {
        "schema_version": "membind.saturated-fixed-work.idle-evidence.v1",
        "status": "PASS" if all_idle else "INVALID",
        "all_services_idle": all_idle,
        "required_consecutive_samples": sample_count,
        "interval_s": float(interval_s),
        "samples": samples,
    }
    result["payload_sha256"] = _hash(result)
    return result


def write_idle_evidence(path: Path, evidence: Mapping[str, Any]) -> dict[str, Any]:
    selected = dict(evidence)
    candidate = dict(selected)
    observed = candidate.pop("payload_sha256", None)
    if (
        selected.get("schema_version")
        != "membind.saturated-fixed-work.idle-evidence.v1"
        or observed != _hash(candidate)
    ):
        raise IdleEvidenceError("IDLE_EVIDENCE_INVALID")
    payload = json.dumps(
        selected, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise IdleEvidenceError("IDLE_EVIDENCE_ALREADY_EXISTS") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return selected


__all__ = [
    "IdleEvidenceError",
    "collect_idle_evidence",
    "write_idle_evidence",
]
