"""Durable orchestration of the required multi-round external recovery loop."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .external_diagnosis import (
    ExternalDiagnosisError,
    build_stop_diagnosis,
    write_stop_diagnosis,
)
from .recovery_probe import collect_recovery_round


class RecoveryRunnerError(ValueError):
    """Recovery materialization is invalid or would overwrite evidence."""


Collector = Callable[..., Mapping[str, Any]]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    body = dict(value)
    body.setdefault("payload_sha256", hashlib.sha256(_canonical_bytes(body)).hexdigest())
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        body, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RecoveryRunnerError("RECOVERY_ARTIFACT_ALREADY_EXISTS") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_round(path: Path, expected_ordinal: int) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RecoveryRunnerError("RECOVERY_ARTIFACT_INVALID") from None
    if not isinstance(value, dict):
        raise RecoveryRunnerError("RECOVERY_ARTIFACT_INVALID")
    body = dict(value)
    observed = body.pop("payload_sha256", None)
    if (
        observed != hashlib.sha256(_canonical_bytes(body)).hexdigest()
        or body.get("round") != expected_ordinal
    ):
        raise RecoveryRunnerError("RECOVERY_ARTIFACT_INVALID")
    return body


def run_external_recovery(
    *,
    run_root: Path,
    ssh_alias: str,
    rounds: int = 3,
    interval_s: float = 2.0,
    collector: Collector = collect_recovery_round,
    sleeper: Callable[[float], Any] = time.sleep,
) -> dict[str, Any]:
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 3:
        raise RecoveryRunnerError("RECOVERY_ROUNDS_INCOMPLETE")
    if isinstance(interval_s, bool) or not isinstance(interval_s, (int, float)) or interval_s < 0:
        raise RecoveryRunnerError("RECOVERY_INTERVAL_INVALID")
    root = run_root.resolve()
    round_paths = [
        root / f"service_evidence/recovery_round_{ordinal:03d}.json"
        for ordinal in range(1, rounds + 1)
    ]
    if (root / "STOP_WITH_EXTERNAL_DIAGNOSIS.json").exists():
        raise RecoveryRunnerError("RECOVERY_ARTIFACT_ALREADY_EXISTS")
    first_missing = next(
        (index for index, path in enumerate(round_paths) if not path.exists()),
        len(round_paths),
    )
    if any(path.exists() for path in round_paths[first_missing + 1 :]):
        raise RecoveryRunnerError("RECOVERY_DURABLE_PREFIX_INVALID")
    recovered: list[dict[str, Any]] = []
    for ordinal in range(1, rounds + 1):
        path = round_paths[ordinal - 1]
        if path.exists():
            row = _read_round(path, ordinal)
        else:
            row = dict(collector(ordinal=ordinal, ssh_alias=ssh_alias))
            if row.get("round") != ordinal:
                raise RecoveryRunnerError("RECOVERY_ROUND_IDENTITY_INVALID")
            _write_new_json(path, row)
        recovered.append(row)
        if ordinal != rounds:
            sleeper(float(interval_s))
    diagnosis = build_stop_diagnosis(recovered)
    try:
        write_stop_diagnosis(root, diagnosis)
    except ExternalDiagnosisError as error:
        raise RecoveryRunnerError(str(error)) from None
    return diagnosis


__all__ = [
    "RecoveryRunnerError",
    "run_external_recovery",
]
