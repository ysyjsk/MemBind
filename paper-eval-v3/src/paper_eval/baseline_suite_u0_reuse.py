"""Read-only verification for reusing a completed Native U0 result corpus.

Reuse means importing immutable result evidence into the baseline-suite report.
It never means reconnecting to, mutating, or continuing the source namespaces.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256
from .native_baseline_runner import (
    DEVELOPMENT_HISTORIES,
    HistoryPlan,
    build_native_baseline_plan,
    verify_checkpoint,
    verify_history_result,
)


U0_REUSE_SCHEMA = "membind.paper-eval-v3.baseline-suite-u0-reuse.v1"
REQUIRED_U0_FILES = (
    "spans.jsonl",
    "events.jsonl",
    "llm.jsonl",
    "embedding.jsonl",
    "db.jsonl",
    "graph_work.jsonl",
    "queue.jsonl",
    "quality.jsonl",
    "per_episode_metrics.jsonl",
    "history_result.json",
    "checkpoint.json",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_QUALITY_IDENTITY_FIELDS = {
    "baseline_id",
    "reader_config_sha256",
    "judge_config_sha256",
}


class U0ReuseError(ValueError):
    """The source U0 run cannot be reused as immutable result evidence."""


def _object_pairs_without_duplicates(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise U0ReuseError("JSON object contains a duplicate field")
        result[key] = value
    return result


def _json_object_from_bytes(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_object_pairs_without_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise U0ReuseError(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise U0ReuseError(f"{label} must be a JSON object")
    return value


def _read_required_files(history_root: Path) -> tuple[dict[str, bytes], dict[str, str]]:
    if not history_root.is_dir() or history_root.is_symlink():
        raise U0ReuseError("required U0 history directory is missing or indirect")
    content: dict[str, bytes] = {}
    hashes: dict[str, str] = {}
    for filename in REQUIRED_U0_FILES:
        path = history_root / filename
        if not path.is_file() or path.is_symlink():
            raise U0ReuseError(
                f"required U0 artifact is missing or indirect: {filename}"
            )
        try:
            data = path.read_bytes()
        except OSError as error:
            raise U0ReuseError(f"required U0 artifact is unreadable: {filename}") from error
        content[filename] = data
        hashes[filename] = hashlib.sha256(data).hexdigest()
    return content, hashes


def _verify_history(
    *,
    history_root: Path,
    expected: HistoryPlan,
) -> dict[str, Any]:
    content, file_hashes = _read_required_files(history_root)
    checkpoint_raw = _json_object_from_bytes(
        content["checkpoint.json"], label="checkpoint"
    )
    result_raw = _json_object_from_bytes(
        content["history_result.json"], label="history result"
    )
    try:
        checkpoint = verify_checkpoint(checkpoint_raw)
    except (TypeError, ValueError) as error:
        raise U0ReuseError(f"checkpoint invalid: {expected.history_id}") from error
    for field, expected_value in (
        ("run_id", expected.run_id),
        ("history_id", expected.history_id),
        ("namespace", expected.namespace),
    ):
        if checkpoint.get(field) != expected_value:
            raise U0ReuseError(
                f"checkpoint does not match Native plan: {expected.history_id}"
            )
    complete_prefix = (
        checkpoint["completed_sequences"] == checkpoint["expected_sequences"]
    )
    if checkpoint["status"] != "completed" or not complete_prefix:
        raise U0ReuseError(
            f"checkpoint is not a completed full prefix: {expected.history_id}"
        )
    if not checkpoint["expected_sequences"]:
        raise U0ReuseError(f"completed history is empty: {expected.history_id}")

    try:
        result = verify_history_result(result_raw, expected_plan=expected)
    except (TypeError, ValueError) as error:
        raise U0ReuseError(f"history result invalid: {expected.history_id}") from error
    episode_count = len(checkpoint["expected_sequences"])
    aggregate = result.get("aggregate")
    if (
        not isinstance(aggregate, Mapping)
        or aggregate.get("episode_count") != episode_count
    ):
        raise U0ReuseError(
            f"history result episode count differs from checkpoint: {expected.history_id}"
        )
    final_observation = result.get("final_namespace_observation")
    if (
        not isinstance(final_observation, Mapping)
        or final_observation.get("episode_count") != episode_count
        or final_observation.get("episode_names_match_expected") is not True
    ):
        raise U0ReuseError(
            f"final graph observation episode count or identity mismatch: {expected.history_id}"
        )
    quality = result.get("quality")
    if not isinstance(quality, Mapping) or quality.get("status") != "SUCCESS":
        raise U0ReuseError(f"history quality is not successful: {expected.history_id}")
    quality_identity = result.get("quality_identity")
    if (
        not isinstance(quality_identity, Mapping)
        or set(quality_identity) != _QUALITY_IDENTITY_FIELDS
        or not isinstance(quality_identity.get("baseline_id"), str)
        or not quality_identity.get("baseline_id")
        or any(
            not isinstance(quality_identity.get(field), str)
            or _SHA256.fullmatch(str(quality_identity.get(field))) is None
            for field in ("reader_config_sha256", "judge_config_sha256")
        )
    ):
        raise U0ReuseError(
            f"history quality identity invalid: {expected.history_id}"
        )
    reader = quality.get("reader")
    if (
        not isinstance(reader, Mapping)
        or reader.get("config_sha256")
        != quality_identity["reader_config_sha256"]
    ):
        raise U0ReuseError(
            f"history Reader identity mismatch: {expected.history_id}"
        )
    metrics = aggregate.get("metrics")
    if not isinstance(metrics, Mapping):
        raise U0ReuseError(
            f"history quality metrics missing: {expected.history_id}"
        )
    quality_metrics: dict[str, float] = {}
    for field in ("qa_accuracy", "evidence_recall_at_10"):
        value = metrics.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0.0 <= float(value) <= 1.0
        ):
            raise U0ReuseError(
                f"history quality metric invalid: {expected.history_id}"
            )
        quality_metrics[field] = float(value)

    # Deliberately exclude the source namespace. A suite consumes only these
    # immutable result hashes and must allocate fresh namespaces for live work.
    return {
        "history_id": expected.history_id,
        "source_order": expected.source_order,
        "episode_count": episode_count,
        "checkpoint_payload_sha256": checkpoint["payload_sha256"],
        "history_result_payload_sha256": result["payload_sha256"],
        "quality_identity": dict(quality_identity),
        "quality_metrics": quality_metrics,
        "file_sha256": file_hashes,
    }


def verify_reusable_u0_run(run_root: Path, run_id: str) -> dict[str, Any]:
    """Verify a fixed-four Native run and return a sealed, sanitized artifact.

    ``run_root`` is the parent ``.../native_baseline/runs`` directory. The
    function performs reads only and rejects symlinked run/history artifacts.
    """

    root = Path(run_root)
    try:
        plan = build_native_baseline_plan(run_id)
    except (TypeError, ValueError) as error:
        raise U0ReuseError("Native U0 run id or plan is invalid") from error
    source_run_root = root / run_id
    if not source_run_root.is_dir() or source_run_root.is_symlink():
        raise U0ReuseError("Native U0 run directory is missing or indirect")
    histories = [
        _verify_history(
            history_root=source_run_root / expected.history_id,
            expected=expected,
        )
        for expected in plan.histories
    ]
    if tuple(row["history_id"] for row in histories) != DEVELOPMENT_HISTORIES:
        raise U0ReuseError("Native U0 source history order drift")
    quality_identity = dict(histories[0]["quality_identity"])
    if any(row["quality_identity"] != quality_identity for row in histories):
        raise U0ReuseError("Native U0 cross-history quality identity drift")

    artifact: dict[str, Any] = {
        "schema_version": U0_REUSE_SCHEMA,
        "status": "VERIFIED_RESULT_ARTIFACTS_ONLY",
        "source_method": "U0",
        "source_run_id": run_id,
        "source_history_order": list(DEVELOPMENT_HISTORIES),
        "required_files": list(REQUIRED_U0_FILES),
        "namespace_reuse": False,
        "target_must_use_fresh_namespaces": True,
        "quality_identity": quality_identity,
        "histories": histories,
        "source_manifest_sha256": payload_sha256(histories),
    }
    artifact["payload_sha256"] = payload_sha256(artifact)
    return artifact


def build_verified_u0_reuse_artifact(
    *,
    native_runs_root: Path,
    native_run_id: str,
) -> dict[str, Any]:
    """Keyword-only compatibility wrapper around :func:`verify_reusable_u0_run`."""

    return verify_reusable_u0_run(native_runs_root, native_run_id)


def verify_u0_reuse_artifact(
    value: Mapping[str, Any],
    *,
    native_runs_root: Path,
) -> dict[str, Any]:
    """Revalidate a stored reuse artifact and detect subsequent source drift."""

    if not isinstance(value, Mapping):
        raise U0ReuseError("U0 reuse artifact must be an object")
    candidate = dict(value)
    observed_hash = candidate.pop("payload_sha256", None)
    if observed_hash != payload_sha256(candidate):
        raise U0ReuseError("U0 reuse artifact payload hash mismatch")
    if candidate.get("schema_version") != U0_REUSE_SCHEMA:
        raise U0ReuseError("U0 reuse artifact schema mismatch")
    if (
        candidate.get("status") != "VERIFIED_RESULT_ARTIFACTS_ONLY"
        or candidate.get("source_method") != "U0"
        or candidate.get("namespace_reuse") is not False
        or candidate.get("target_must_use_fresh_namespaces") is not True
    ):
        raise U0ReuseError("U0 reuse artifact policy mismatch")
    source_run_id = candidate.get("source_run_id")
    if not isinstance(source_run_id, str):
        raise U0ReuseError("U0 reuse artifact source run id is invalid")
    current = verify_reusable_u0_run(native_runs_root, source_run_id)
    if dict(value) != current:
        raise U0ReuseError("U0 source artifact hash drift")
    return current


__all__ = [
    "REQUIRED_U0_FILES",
    "U0_REUSE_SCHEMA",
    "U0ReuseError",
    "build_verified_u0_reuse_artifact",
    "verify_reusable_u0_run",
    "verify_u0_reuse_artifact",
]
