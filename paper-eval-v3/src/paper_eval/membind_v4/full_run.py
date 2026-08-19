"""Immutable four-history orchestration for the formal MemBind v4 run.

The orchestrator owns only between-history durability.  A production history
runner is injected and must allocate the supplied fresh namespace, execute the
frozen method, and return a content-safe aggregate.  Only a sealed PASS result
may be resumed; a partial history is never retried in the same namespace.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file
from paper_eval.membind_v4.freeze import FORMAL_HISTORY_IDS, verify_frozen_method


FULL_RUN_MANIFEST_SCHEMA = "membind.paper-eval-v4.full-run-manifest.v1"
FULL_RUN_CHECKPOINT_SCHEMA = "membind.paper-eval-v4.full-run-checkpoint.v1"
FULL_HISTORY_RESULT_SCHEMA = "membind.paper-eval-v4.full-history-result.v1"
FULL_RESULT_SCHEMA = "membind.paper-eval-v4.full-result.v1"
FULL_FAILURE_SCHEMA = "membind.paper-eval-v4.full-run-failure.v1"

FORMAL_HISTORY_SOURCE_COUNTS: dict[str, int] = {
    "07741c45": 49,
    "6071bd76": 46,
    "a2f3aa27": 44,
    "b6019101": 49,
}

_RUN_ID = re.compile(r"^v4-full-[a-z0-9][a-z0-9-]{2,63}$")
_PUBLIC_RESULT_FIELDS = {
    "performance",
    "telemetry",
    "admission_observation",
    "quality",
    "final_graph",
    "work_volume",
    "output_artifacts",
    "frontier_p95_service_ratio",
    "freshness_p95_ratio",
    "publication_source_sequences",
}


class V4FullRunError(ValueError):
    """The formal run cannot be initialized, resumed, or sealed."""


def _fail(code: str) -> V4FullRunError:
    return V4FullRunError(code)


def _seal(body: Mapping[str, object]) -> dict[str, object]:
    value = dict(body)
    if "payload_sha256" in value:
        raise _fail("artifact_already_sealed")
    value["payload_sha256"] = payload_sha256(value)
    return value


def _read(path: Path, *, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _fail(code) from error
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _verify_sealed(
    value: Mapping[str, Any],
    *,
    schema: str,
    code: str,
) -> dict[str, Any]:
    candidate = dict(value)
    digest = candidate.pop("payload_sha256", None)
    if not isinstance(digest, str) or digest != payload_sha256(candidate):
        raise _fail(code)
    if candidate.get("schema_version") != schema:
        raise _fail(f"{code}:schema")
    candidate["payload_sha256"] = digest
    return candidate


def _normalize_preflight(value: Mapping[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    body = dict(value)
    digest = body.pop("payload_sha256", None)
    if digest is not None and (not isinstance(digest, str) or digest != payload_sha256(body)):
        raise _fail("preflight_payload_hash_mismatch")
    body["payload_sha256"] = payload_sha256(body)
    return body


def _history_identity(run_id: str, history_id: str, index: int, frozen_sha: str) -> dict[str, object]:
    digest = hashlib.sha256(
        f"{run_id}\0{history_id}\0{index}\0{frozen_sha}".encode("ascii")
    ).hexdigest()
    return {
        "history_index": index,
        "history_id": history_id,
        "run_id": f"{run_id}-h{index:02d}-{history_id}",
        "namespace": f"membind-v4-{digest[:20]}",
        "source_count": FORMAL_HISTORY_SOURCE_COUNTS[history_id],
        "fresh_namespace": True,
    }


def _manifest(
    *,
    run_id: str,
    frozen_method_path: Path,
    frozen_method: Mapping[str, object],
    mode: str,
    preflight: Mapping[str, object] | None,
) -> dict[str, object]:
    frozen_file_sha = sha256_file(frozen_method_path)
    histories = [
        _history_identity(run_id, history_id, index, frozen_file_sha)
        for index, history_id in enumerate(FORMAL_HISTORY_IDS)
    ]
    return _seal(
        {
            "schema_version": FULL_RUN_MANIFEST_SCHEMA,
            "status": "FROZEN_PLAN",
            "run_id": run_id,
            "runner_mode": mode,
            "formal_main_table_eligible": mode == "live" and _preflight_is_ready(preflight),
            "frozen_method": {
                "absolute_path": str(frozen_method_path.resolve()),
                "file_sha256": frozen_file_sha,
                "payload_sha256": frozen_method["payload_sha256"],
                "candidate_id": frozen_method.get("candidate_id"),
                "policy": frozen_method.get("policy"),
            },
            "history_ids": list(FORMAL_HISTORY_IDS),
            "histories": histories,
            "source_count": sum(FORMAL_HISTORY_SOURCE_COUNTS.values()),
            "fresh_namespaces_required": True,
            "preflight_payload_sha256": preflight.get("payload_sha256") if preflight else None,
        }
    )


def _checkpoint(
    *,
    manifest_sha: str,
    status: str,
    completed_hashes: Sequence[str],
    formal_eligible: bool,
) -> dict[str, object]:
    return _seal(
        {
            "schema_version": FULL_RUN_CHECKPOINT_SCHEMA,
            "status": status,
            "manifest_payload_sha256": manifest_sha,
            "completed_history_result_payload_sha256s": list(completed_hashes),
            "next_history_index": len(completed_hashes),
            "formal_main_table_eligible": formal_eligible and status == "PASS",
        }
    )


def _verify_checkpoint(
    value: Mapping[str, Any], *, manifest: Mapping[str, Any]
) -> tuple[list[str], str]:
    checked = _verify_sealed(
        value,
        schema=FULL_RUN_CHECKPOINT_SCHEMA,
        code="full_run_checkpoint_payload_hash_mismatch",
    )
    completed = checked.get("completed_history_result_payload_sha256s")
    index = checked.get("next_history_index")
    if (
        checked.get("manifest_payload_sha256") != manifest["payload_sha256"]
        or not isinstance(completed, list)
        or any(not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None for item in completed)
        or isinstance(index, bool)
        or not isinstance(index, int)
        or index != len(completed)
        or not 0 <= index <= len(FORMAL_HISTORY_IDS)
    ):
        raise _fail("full_run_checkpoint_identity_drift")
    return list(completed), str(checked.get("status"))


def _history_result(
    result: Mapping[str, object],
    *,
    identity: Mapping[str, object],
    manifest: Mapping[str, object],
    mode: str,
) -> dict[str, object]:
    for field in ("history_id", "run_id", "namespace", "source_count"):
        if result.get(field) != identity[field]:
            raise _fail(f"history_result_{field}_mismatch")
    if result.get("status") != "PASS":
        raise _fail("history_result_not_pass")
    direct_violations = result.get("direct_violation_count", 0)
    if isinstance(direct_violations, bool) or not isinstance(direct_violations, int):
        raise _fail("history_result_direct_violation_invalid")
    if direct_violations != 0:
        raise _fail("history_result_direct_violation")
    projection = {
        key: result[key]
        for key in sorted(_PUBLIC_RESULT_FIELDS)
        if key in result
    }
    return _seal(
        {
            "schema_version": FULL_HISTORY_RESULT_SCHEMA,
            "status": "PASS",
            **dict(identity),
            "runner_mode": mode,
            "formal_main_table_eligible": mode == "live",
            "manifest_payload_sha256": manifest["payload_sha256"],
            "direct_violation_count": direct_violations,
            "result": projection,
        }
    )


def _verify_history_result(
    value: Mapping[str, Any],
    *,
    identity: Mapping[str, object],
    manifest: Mapping[str, object],
    mode: str,
) -> dict[str, Any]:
    checked = _verify_sealed(
        value,
        schema=FULL_HISTORY_RESULT_SCHEMA,
        code="history_result_payload_hash_mismatch",
    )
    for field, expected in (
        ("status", "PASS"),
        ("runner_mode", mode),
        ("formal_main_table_eligible", mode == "live"),
        ("manifest_payload_sha256", manifest["payload_sha256"]),
        ("direct_violation_count", 0),
        *tuple((key, identity[key]) for key in identity),
    ):
        if checked.get(field) != expected:
            raise _fail(f"history_result_identity_drift:{field}")
    return checked


def _verify_full_result(
    value: Mapping[str, Any],
    *,
    manifest: Mapping[str, object],
    frozen: Mapping[str, object],
    mode: str,
    history_results: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    checked = _verify_sealed(
        value,
        schema=FULL_RESULT_SCHEMA,
        code="full_result_payload_hash_mismatch",
    )
    for field, expected in (
        ("status", "PASS"),
        ("run_id", manifest["run_id"]),
        ("runner_mode", mode),
        ("formal_main_table_eligible", mode == "live"),
        ("manifest_payload_sha256", manifest["payload_sha256"]),
        ("frozen_method_payload_sha256", frozen["payload_sha256"]),
        ("history_ids", list(FORMAL_HISTORY_IDS)),
        ("history_count", 4),
        ("source_count", 188),
        ("direct_violation_count", 0),
    ):
        if checked.get(field) != expected:
            raise _fail(f"full_result_identity_drift:{field}")
    expected_histories = [
        {
            "history_id": row["history_id"],
            "run_id": row["run_id"],
            "namespace": row["namespace"],
            "source_count": row["source_count"],
            "result_payload_sha256": row["payload_sha256"],
            "result": row["result"],
        }
        for row in history_results
    ]
    if checked.get("histories") != expected_histories:
        raise _fail("full_result_history_binding_drift")
    return checked


def _fixture_runner(**kwargs: object) -> dict[str, object]:
    source_count = int(kwargs["source_count"])
    return {
        "status": "PASS",
        "history_id": kwargs["history_id"],
        "run_id": kwargs["run_id"],
        "namespace": kwargs["namespace"],
        "source_count": source_count,
        "direct_violation_count": 0,
        "performance": {"makespan_ns": source_count, "p95_freshness_ns": source_count},
        "telemetry": {"fixture": True},
    }


def _invoke_runner(
    runner: Callable[..., Mapping[str, object] | Awaitable[Mapping[str, object]]],
    **kwargs: object,
) -> Mapping[str, object]:
    result = runner(**kwargs)
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    if not isinstance(result, Mapping):
        raise _fail("history_runner_result_invalid")
    return result


def _preflight_is_ready(value: Mapping[str, object] | None) -> bool:
    return bool(
        value is not None
        and value.get("status") == "READY"
        and value.get("classification") == "READY"
        and value.get("mutations_performed") is False
        and value.get("credentials_recorded") is False
    )


def _failure(
    *,
    manifest: Mapping[str, object],
    classification: str,
    error: BaseException | None,
    history_id: str | None,
    completed_hashes: Sequence[str],
) -> dict[str, object]:
    return _seal(
        {
            "schema_version": FULL_FAILURE_SCHEMA,
            "status": "FAILED_NON_MERGEABLE",
            "formal_main_table_eligible": False,
            "manifest_payload_sha256": manifest["payload_sha256"],
            "classification": classification,
            "history_id": history_id,
            "error_class": (
                f"{type(error).__module__}.{type(error).__qualname__}" if error else None
            ),
            "error_code": str(error) if error else classification,
            "completed_history_result_payload_sha256s": list(completed_hashes),
        }
    )


def _prepare_root(
    root: Path,
    *,
    expected_manifest: Mapping[str, object],
) -> tuple[list[str], str]:
    manifest_path = root / "FULL_RUN_MANIFEST.json"
    checkpoint_path = root / "FULL_RUN_CHECKPOINT.json"
    if root.exists():
        if not manifest_path.is_file() or not checkpoint_path.is_file():
            if any(root.iterdir()):
                raise _fail("full_run_root_unrecognized")
        else:
            observed = _verify_sealed(
                _read(manifest_path, code="full_run_manifest_unreadable"),
                schema=FULL_RUN_MANIFEST_SCHEMA,
                code="full_run_manifest_payload_hash_mismatch",
            )
            if observed != expected_manifest:
                raise _fail("full_run_manifest_identity_drift")
            return _verify_checkpoint(
                _read(checkpoint_path, code="full_run_checkpoint_unreadable"),
                manifest=observed,
            )
    else:
        root.mkdir(parents=True, exist_ok=False)
    atomic_write_json(manifest_path, expected_manifest)
    initial = _checkpoint(
        manifest_sha=str(expected_manifest["payload_sha256"]),
        status="RUNNING",
        completed_hashes=[],
        formal_eligible=False,
    )
    atomic_write_json(checkpoint_path, initial)
    return [], "RUNNING"


def run_v4_full(
    *,
    frozen_method_path: Path,
    output_root: Path,
    run_id: str,
    histories: Sequence[str] = FORMAL_HISTORY_IDS,
    mode: str = "live",
    preflight: Mapping[str, object] | None = None,
    history_runner: Callable[..., Mapping[str, object] | Awaitable[Mapping[str, object]]] | None = None,
) -> dict[str, object]:
    """Run or safely resume the exact frozen 4-history / 188-episode plan."""

    supplied = tuple(histories)
    if supplied != FORMAL_HISTORY_IDS:
        raise _fail("formal_history_order_drift")
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise _fail("full_run_id_invalid")
    if mode not in {"live", "fixture", "blocked"}:
        raise _fail("full_runner_mode_invalid")

    frozen_path = Path(frozen_method_path).resolve()
    frozen = verify_frozen_method(frozen_path)
    normalized_preflight = _normalize_preflight(preflight)
    if mode == "live" and normalized_preflight is None:
        normalized_preflight = _normalize_preflight(
            {"status": "BLOCKED_SERVICE_PREFLIGHT", "classification": "SERVICE_PREFLIGHT_MISSING"}
        )
    if mode == "blocked" and normalized_preflight is None:
        normalized_preflight = _normalize_preflight(
            {"status": "BLOCKED_SERVICE_PREFLIGHT", "classification": "SERVICE_PREFLIGHT_BLOCKED"}
        )

    manifest = _manifest(
        run_id=run_id,
        frozen_method_path=frozen_path,
        frozen_method=frozen,
        mode=mode,
        preflight=normalized_preflight,
    )
    root = Path(output_root).resolve()
    completed_hashes, checkpoint_status = _prepare_root(root, expected_manifest=manifest)
    if normalized_preflight is not None:
        preflight_path = root / "PREFLIGHT.json"
        if preflight_path.exists():
            if _read(preflight_path, code="preflight_unreadable") != normalized_preflight:
                raise _fail("preflight_identity_drift")
        else:
            atomic_write_json(preflight_path, normalized_preflight)

    identities = manifest["histories"]
    if not isinstance(identities, list):
        raise _fail("full_run_manifest_histories_invalid")
    checked_results: list[dict[str, Any]] = []
    for index, expected_hash in enumerate(completed_hashes):
        identity = identities[index]
        if not isinstance(identity, Mapping):
            raise _fail("full_run_manifest_history_invalid")
        result_path = root / "histories" / str(identity["history_id"]) / "result.json"
        checked = _verify_history_result(
            _read(result_path, code="completed_history_result_missing"),
            identity=identity,
            manifest=manifest,
            mode=mode,
        )
        if checked["payload_sha256"] != expected_hash:
            raise _fail("completed_history_result_hash_drift")
        checked_results.append(checked)

    failure_path = root / "FAILURE.json"
    if failure_path.exists():
        if (root / "FULL_RUN_RESULT.json").exists():
            raise _fail("failure_and_full_result_both_exist")
        failure = _verify_sealed(
            _read(failure_path, code="full_run_failure_unreadable"),
            schema=FULL_FAILURE_SCHEMA,
            code="full_run_failure_payload_hash_mismatch",
        )
        if failure.get("manifest_payload_sha256") != manifest["payload_sha256"]:
            raise _fail("full_run_failure_identity_drift")
        if failure.get("completed_history_result_payload_sha256s") != completed_hashes:
            raise _fail("full_run_failure_checkpoint_drift")
        if checkpoint_status == "RUNNING":
            atomic_write_json(
                root / "FULL_RUN_CHECKPOINT.json",
                _checkpoint(
                    manifest_sha=str(manifest["payload_sha256"]),
                    status="FAILED_NON_MERGEABLE",
                    completed_hashes=completed_hashes,
                    formal_eligible=False,
                ),
            )
        elif checkpoint_status != "FAILED_NON_MERGEABLE":
            raise _fail("full_run_failure_checkpoint_drift")
        return failure
    if checkpoint_status == "FAILED_NON_MERGEABLE":
        raise _fail("full_run_failure_missing")
    if checkpoint_status == "PASS" and len(completed_hashes) != len(identities):
        raise _fail("pass_checkpoint_before_history_completion")
    if checkpoint_status not in {"RUNNING", "PASS"}:
        raise _fail("full_run_checkpoint_status_invalid")

    # Recover the narrow crash window after result sealing but before the
    # checkpoint update.  Any non-PASS or non-contiguous artifact fails closed.
    while len(completed_hashes) < len(identities):
        identity = identities[len(completed_hashes)]
        if not isinstance(identity, Mapping):
            raise _fail("full_run_manifest_history_invalid")
        result_path = root / "histories" / str(identity["history_id"]) / "result.json"
        if not result_path.exists():
            break
        checked = _verify_history_result(
            _read(result_path, code="history_result_unreadable"),
            identity=identity,
            manifest=manifest,
            mode=mode,
        )
        completed_hashes.append(str(checked["payload_sha256"]))
        checked_results.append(checked)
        atomic_write_json(
            root / "FULL_RUN_CHECKPOINT.json",
            _checkpoint(
                manifest_sha=str(manifest["payload_sha256"]),
                status="RUNNING",
                completed_hashes=completed_hashes,
                formal_eligible=False,
            ),
        )

    preflight_ready = _preflight_is_ready(normalized_preflight)
    if mode == "blocked" or (mode == "live" and not preflight_ready):
        classification = (
            "SERVICE_PREFLIGHT_BLOCKED"
            if mode == "blocked"
            else str((normalized_preflight or {}).get("classification", "SERVICE_PREFLIGHT_BLOCKED"))
        )
        failure = _failure(
            manifest=manifest,
            classification=classification,
            error=None,
            history_id=None,
            completed_hashes=completed_hashes,
        )
        atomic_write_json(failure_path, failure)
        atomic_write_json(
            root / "FULL_RUN_CHECKPOINT.json",
            _checkpoint(
                manifest_sha=str(manifest["payload_sha256"]),
                status="FAILED_NON_MERGEABLE",
                completed_hashes=completed_hashes,
                formal_eligible=False,
            ),
        )
        return failure

    full_result_path = root / "FULL_RUN_RESULT.json"
    if len(completed_hashes) == len(identities):
        if full_result_path.is_file():
            result = _verify_full_result(
                _read(full_result_path, code="full_result_unreadable"),
                manifest=manifest,
                frozen=frozen,
                mode=mode,
                history_results=checked_results,
            )
            if checkpoint_status == "RUNNING":
                atomic_write_json(
                    root / "FULL_RUN_CHECKPOINT.json",
                    _checkpoint(
                        manifest_sha=str(manifest["payload_sha256"]),
                        status="PASS",
                        completed_hashes=completed_hashes,
                        formal_eligible=mode == "live",
                    ),
                )
            return result
        if checkpoint_status == "PASS":
            raise _fail("full_result_missing_after_pass_checkpoint")
    if full_result_path.exists():
        raise _fail("full_result_exists_before_history_completion")

    runner = _fixture_runner if mode == "fixture" and history_runner is None else history_runner
    if runner is None:
        error = _fail("live_history_runner_not_configured")
        failure = _failure(
            manifest=manifest,
            classification="LIVE_HISTORY_RUNNER_NOT_CONFIGURED",
            error=error,
            history_id=str(identities[len(completed_hashes)]["history_id"]),
            completed_hashes=completed_hashes,
        )
        atomic_write_json(failure_path, failure)
        atomic_write_json(
            root / "FULL_RUN_CHECKPOINT.json",
            _checkpoint(
                manifest_sha=str(manifest["payload_sha256"]),
                status="FAILED_NON_MERGEABLE",
                completed_hashes=completed_hashes,
                formal_eligible=False,
            ),
        )
        return failure

    for index in range(len(completed_hashes), len(identities)):
        identity = identities[index]
        if not isinstance(identity, Mapping):
            raise _fail("full_run_manifest_history_invalid")
        history_root = root / "histories" / str(identity["history_id"])
        if history_root.exists():
            raise _fail("incomplete_history_not_resumable")
        try:
            returned = _invoke_runner(
                runner,
                **dict(identity),
                history_root=history_root,
                frozen_method=dict(frozen),
                frozen_method_path=frozen_path,
                preflight=dict(normalized_preflight) if normalized_preflight else None,
                runner_mode=mode,
            )
            sealed = _history_result(
                returned,
                identity=identity,
                manifest=manifest,
                mode=mode,
            )
            history_root.mkdir(parents=True, exist_ok=True)
            result_path = history_root / "result.json"
            if result_path.exists():
                if _read(result_path, code="history_result_unreadable") != sealed:
                    raise _fail("history_result_path_already_occupied")
            else:
                atomic_write_json(result_path, sealed)
        except Exception as error:
            failure = _failure(
                manifest=manifest,
                classification="HISTORY_FAILED",
                error=error,
                history_id=str(identity["history_id"]),
                completed_hashes=completed_hashes,
            )
            atomic_write_json(failure_path, failure)
            atomic_write_json(
                root / "FULL_RUN_CHECKPOINT.json",
                _checkpoint(
                    manifest_sha=str(manifest["payload_sha256"]),
                    status="FAILED_NON_MERGEABLE",
                    completed_hashes=completed_hashes,
                    formal_eligible=False,
                ),
            )
            return failure
        completed_hashes.append(str(sealed["payload_sha256"]))
        checked_results.append(sealed)
        atomic_write_json(
            root / "FULL_RUN_CHECKPOINT.json",
            _checkpoint(
                manifest_sha=str(manifest["payload_sha256"]),
                status="RUNNING",
                completed_hashes=completed_hashes,
                formal_eligible=False,
            ),
        )

    history_rows = [
        {
            "history_id": row["history_id"],
            "run_id": row["run_id"],
            "namespace": row["namespace"],
            "source_count": row["source_count"],
            "result_payload_sha256": row["payload_sha256"],
            "result": row["result"],
        }
        for row in checked_results
    ]
    final = _seal(
        {
            "schema_version": FULL_RESULT_SCHEMA,
            "status": "PASS",
            "run_id": run_id,
            "runner_mode": mode,
            "formal_main_table_eligible": mode == "live",
            "manifest_payload_sha256": manifest["payload_sha256"],
            "frozen_method_payload_sha256": frozen["payload_sha256"],
            "history_ids": list(FORMAL_HISTORY_IDS),
            "history_count": 4,
            "source_count": sum(FORMAL_HISTORY_SOURCE_COUNTS.values()),
            "direct_violation_count": 0,
            "histories": history_rows,
        }
    )
    atomic_write_json(full_result_path, final)
    atomic_write_json(
        root / "FULL_RUN_CHECKPOINT.json",
        _checkpoint(
            manifest_sha=str(manifest["payload_sha256"]),
            status="PASS",
            completed_hashes=completed_hashes,
            formal_eligible=mode == "live",
        ),
    )
    return final


__all__ = [
    "FORMAL_HISTORY_SOURCE_COUNTS",
    "FULL_FAILURE_SCHEMA",
    "FULL_HISTORY_RESULT_SCHEMA",
    "FULL_RESULT_SCHEMA",
    "FULL_RUN_CHECKPOINT_SCHEMA",
    "FULL_RUN_MANIFEST_SCHEMA",
    "V4FullRunError",
    "run_v4_full",
]
