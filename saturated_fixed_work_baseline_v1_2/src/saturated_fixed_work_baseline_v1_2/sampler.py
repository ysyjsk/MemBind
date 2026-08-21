"""Durable 1 Hz sample rows and coverage/gap reduction."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any


class SamplerError(ValueError):
    """Sample identity, durability, or coverage input is invalid."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def collect_sample(
    probes: Mapping[str, Callable[[], Any]],
    *,
    monotonic_ns: int,
    wall_time: str,
) -> dict[str, Any]:
    if (
        isinstance(monotonic_ns, bool)
        or not isinstance(monotonic_ns, int)
        or monotonic_ns < 0
        or not isinstance(wall_time, str)
        or not wall_time
        or not probes
    ):
        raise SamplerError("SAMPLE_IDENTITY_INVALID")
    observations: dict[str, dict[str, Any]] = {}
    for name, probe in probes.items():
        if not isinstance(name, str) or not name or not callable(probe):
            raise SamplerError("SAMPLE_PROBE_INVALID")
        try:
            value = probe()
            _canonical_bytes(value)
        except BaseException as error:
            observations[name] = {
                "availability": "INVALID",
                "value": None,
                "reason": f"{type(error).__module__}.{type(error).__qualname__}",
            }
        else:
            observations[name] = {
                "availability": "MEASURED",
                "value": value,
                "reason": None,
            }
    body = {
        "schema_version": "membind.saturated-fixed-work.telemetry-sample.v1",
        "monotonic_ns": monotonic_ns,
        "wall_time": wall_time,
        "observations": observations,
    }
    body["payload_sha256"] = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    return body


def _percentile(values: Sequence[float], percentile: float) -> float:
    selected = sorted(values)
    index = max(0, math.ceil(percentile * len(selected)) - 1)
    return selected[index]


def summarize_samples(
    rows: Sequence[Mapping[str, Any]],
    *,
    window_start_ns: int,
    window_end_ns: int,
    target_period_s: float,
) -> dict[str, Any]:
    if (
        isinstance(window_start_ns, bool)
        or isinstance(window_end_ns, bool)
        or not isinstance(window_start_ns, int)
        or not isinstance(window_end_ns, int)
        or window_end_ns <= window_start_ns
        or isinstance(target_period_s, bool)
        or not isinstance(target_period_s, (int, float))
        or not math.isfinite(target_period_s)
        or target_period_s <= 0
    ):
        raise SamplerError("SAMPLE_WINDOW_INVALID")
    selected = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and isinstance(row.get("monotonic_ns"), int)
        and not isinstance(row.get("monotonic_ns"), bool)
        and window_start_ns <= int(row["monotonic_ns"]) <= window_end_ns
    ]
    timestamps = [int(row["monotonic_ns"]) for row in selected]
    if not timestamps or timestamps != sorted(set(timestamps)):
        raise SamplerError("SAMPLE_TIMESTAMPS_INVALID")
    period_ns = int(float(target_period_s) * 1_000_000_000)
    expected = (window_end_ns - window_start_ns) // period_ns + 1
    gaps = [
        (right - left) / 1_000_000_000
        for left, right in zip(timestamps, timestamps[1:])
    ]
    source_names = sorted(
        {
            name
            for row in selected
            for name in (
                row.get("observations", {}).keys()
                if isinstance(row.get("observations"), Mapping)
                else ()
            )
        }
    )
    source_coverage = {
        name: sum(
            isinstance(row.get("observations"), Mapping)
            and isinstance(row["observations"].get(name), Mapping)
            and row["observations"][name].get("availability")
            in {"MEASURED", "DERIVED"}
            for row in selected
        )
        / len(selected)
        for name in source_names
    }
    return {
        "schema_version": "membind.saturated-fixed-work.sampler-summary.v1",
        "window_start_ns": window_start_ns,
        "window_end_ns": window_end_ns,
        "duration_s": (window_end_ns - window_start_ns) / 1_000_000_000,
        "target_period_s": float(target_period_s),
        "expected_samples": expected,
        "actual_samples": len(selected),
        "coverage": min(1.0, len(selected) / expected),
        "gap_p50_s": _percentile(gaps, 0.50) if gaps else None,
        "gap_p95_s": _percentile(gaps, 0.95) if gaps else None,
        "gap_max_s": max(gaps) if gaps else None,
        "source_coverage": source_coverage,
    }


class DurableSampleWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._last_timestamp: int | None = None
        if path.exists():
            try:
                rows = [
                    json.loads(line)
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except (OSError, UnicodeError, json.JSONDecodeError):
                raise SamplerError("SAMPLE_JOURNAL_INVALID") from None
            timestamps = [row.get("monotonic_ns") for row in rows]
            if timestamps and timestamps != sorted(set(timestamps)):
                raise SamplerError("SAMPLE_JOURNAL_INVALID")
            self._last_timestamp = timestamps[-1] if timestamps else None

    def append(self, row: Mapping[str, Any]) -> None:
        timestamp = row.get("monotonic_ns")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, int)
            or timestamp < 0
            or self._last_timestamp is not None
            and timestamp <= self._last_timestamp
        ):
            raise SamplerError("SAMPLE_TIMESTAMP_NOT_MONOTONIC")
        payload = _canonical_bytes(dict(row)) + b"\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600
        )
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._last_timestamp = timestamp


class PeriodicSampler:
    def __init__(
        self,
        *,
        probes: Mapping[str, Callable[[], Any]],
        output_path: Path,
        target_period_s: float = 1.0,
    ) -> None:
        if (
            not probes
            or isinstance(target_period_s, bool)
            or not isinstance(target_period_s, (int, float))
            or not math.isfinite(target_period_s)
            or target_period_s <= 0
        ):
            raise SamplerError("SAMPLER_CONFIGURATION_INVALID")
        self._probes = dict(probes)
        self._writer = DurableSampleWriter(output_path)
        self._target_period_s = float(target_period_s)
        self._period_ns = int(self._target_period_s * 1_000_000_000)
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._rows: list[dict[str, Any]] = []
        self._start_ns: int | None = None
        self._end_ns: int | None = None

    async def start(self) -> None:
        if self._task is not None:
            raise SamplerError("SAMPLER_ALREADY_STARTED")
        self._start_ns = time.monotonic_ns()
        self._task = asyncio.create_task(self._run(), name="sfwb-v1-2-telemetry")
        await asyncio.sleep(0)

    async def _run(self) -> None:
        assert self._start_ns is not None
        deadline = self._start_ns
        while not self._stop.is_set():
            now = time.monotonic_ns()
            if now < deadline:
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=(deadline - now) / 1_000_000_000
                    )
                except TimeoutError:
                    pass
                if self._stop.is_set():
                    break
            timestamp = time.monotonic_ns()
            wall_time = datetime.now().astimezone().isoformat()

            def capture() -> dict[str, Any]:
                row = collect_sample(
                    self._probes,
                    monotonic_ns=timestamp,
                    wall_time=wall_time,
                )
                self._writer.append(row)
                return row

            self._rows.append(await asyncio.to_thread(capture))
            deadline = max(deadline + self._period_ns, timestamp + self._period_ns)

    async def stop(self) -> dict[str, Any]:
        if self._task is None:
            raise SamplerError("SAMPLER_NOT_STARTED")
        if self._end_ns is not None:
            raise SamplerError("SAMPLER_ALREADY_STOPPED")
        self._stop.set()
        await self._task
        assert self._start_ns is not None
        self._end_ns = max(time.monotonic_ns(), self._start_ns + 1)
        return summarize_samples(
            self._rows,
            window_start_ns=self._start_ns,
            window_end_ns=self._end_ns,
            target_period_s=self._target_period_s,
        )


__all__ = [
    "DurableSampleWriter",
    "PeriodicSampler",
    "SamplerError",
    "collect_sample",
    "summarize_samples",
]
