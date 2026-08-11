"""One-shot, fail-closed cleanup for the polluted frozen C2 block-0 namespace.

This module intentionally exposes no general database cleanup surface.  Its only
accepted target is the failed C2 attempt's block-0 namespace, cross-checked
against the frozen E1/E2 selection before the driver is touched.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from graphiti_core.utils.maintenance.graph_data_operations import clear_data

from native_characterization_c2 import load_e1_e2_blocks


SCHEMA_VERSION = "membind.native-characterization-c2-cleanup.v1"
FAILED_C2_ATTEMPT_ID = "c2-723261287e32e182"
POLLUTED_C2_GROUP_ID = "nc-e1e2-400b9b78c2c218df"
CLEANUP_PRIMITIVE = "graphiti.clear_data(driver,group_ids=[target_group])"

NODE_COUNT_QUERY = """
MATCH (n)
WHERE n.group_id = $group_id
RETURN count(n) AS node_count
"""

RELATIONSHIP_COUNT_QUERY = """
MATCH ()-[r]->()
WHERE r.group_id = $group_id
RETURN count(r) AS relationship_count
"""

ClearData = Callable[..., Awaitable[None]]


class ScopedC2CleanupError(RuntimeError):
    """Sanitized denial or verification failure for the exact C2 cleanup."""

    def __init__(self, reason: str, *, evidence: Mapping[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.evidence = None if evidence is None else dict(evidence)


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _with_payload_hash(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["payload_sha256"] = hashlib.sha256(_canonical_bytes(result)).hexdigest()
    return result


def _freeze_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise ScopedC2CleanupError("freeze_unreadable") from None
    return digest.hexdigest()


def _query_records(result: Any) -> Sequence[Any]:
    records = getattr(result, "records", None)
    if isinstance(records, Sequence) and not isinstance(records, (str, bytes)):
        return records
    raise ScopedC2CleanupError("count_query_result_invalid")


async def _count_group_objects(
    driver: Any,
    *,
    target_group: str,
    query: str,
    result_key: str,
    phase: str,
) -> int:
    execute_query = getattr(driver, "execute_query", None)
    if not callable(execute_query):
        raise ScopedC2CleanupError("driver_execute_query_missing")
    try:
        result = await execute_query(query, params={"group_id": target_group})
        records = _query_records(result)
    except ScopedC2CleanupError:
        raise
    except Exception:
        raise ScopedC2CleanupError(f"{phase}_query_failed") from None
    if len(records) != 1:
        raise ScopedC2CleanupError(f"{phase}_count_invalid")
    try:
        value = records[0][result_key]
    except (KeyError, TypeError):
        raise ScopedC2CleanupError(f"{phase}_count_invalid") from None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ScopedC2CleanupError(f"{phase}_count_invalid")
    return value


async def _counts(driver: Any, target_group: str, prefix: str) -> dict[str, int]:
    node_count = await _count_group_objects(
        driver,
        target_group=target_group,
        query=NODE_COUNT_QUERY,
        result_key="node_count",
        phase=f"{prefix}_node",
    )
    relationship_count = await _count_group_objects(
        driver,
        target_group=target_group,
        query=RELATIONSHIP_COUNT_QUERY,
        result_key="relationship_count",
        phase=f"{prefix}_relationship",
    )
    return {
        "node_count": node_count,
        "relationship_count": relationship_count,
    }


def _evidence(
    *,
    status: str,
    target_group: str,
    freeze_sha256: str,
    pre_cleanup: Mapping[str, int],
    post_cleanup: Mapping[str, int | None],
) -> dict[str, Any]:
    return _with_payload_hash(
        {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "failed_attempt_id": FAILED_C2_ATTEMPT_ID,
            "failed_attempt_valid": False,
            "failed_attempt_mergeable": False,
            "replacement_resume_allowed": False,
            "target_group_id": target_group,
            "freeze_sha256": freeze_sha256,
            "cleanup_primitive": CLEANUP_PRIMITIVE,
            "pre_cleanup": dict(pre_cleanup),
            "post_cleanup": dict(post_cleanup),
            "preexisting_empty": (
                pre_cleanup["node_count"] == 0
                and pre_cleanup["relationship_count"] == 0
            ),
        }
    )


async def cleanup_scoped_c2_namespace(
    *,
    driver: Any,
    freeze_path: str | Path,
    target_group: object,
    operator_authorized: object,
    clear_data_impl: ClearData = clear_data,
) -> dict[str, Any]:
    """Delete and verify only the polluted frozen C2 block-0 namespace."""

    if operator_authorized is not True:
        raise ScopedC2CleanupError("operator_authorization_required")
    if type(target_group) is not str or not target_group.strip():
        raise ScopedC2CleanupError("target_group_invalid")
    if target_group != POLLUTED_C2_GROUP_ID:
        raise ScopedC2CleanupError("target_group_not_allowlisted")
    if not callable(clear_data_impl):
        raise ScopedC2CleanupError("clear_data_impl_invalid")

    try:
        blocks = load_e1_e2_blocks(freeze_path)
    except Exception:
        raise ScopedC2CleanupError("freeze_invalid") from None
    if not blocks or blocks[0].graph_namespace != POLLUTED_C2_GROUP_ID:
        raise ScopedC2CleanupError("freeze_block_zero_binding_mismatch")
    freeze_sha256 = _freeze_sha256(freeze_path)

    pre_cleanup = await _counts(driver, target_group, "pre")
    try:
        await clear_data_impl(driver, group_ids=[target_group])
    except Exception:
        evidence = _evidence(
            status="upstream_clear_failed",
            target_group=target_group,
            freeze_sha256=freeze_sha256,
            pre_cleanup=pre_cleanup,
            post_cleanup={"node_count": None, "relationship_count": None},
        )
        raise ScopedC2CleanupError(
            "upstream_clear_failed", evidence=evidence
        ) from None

    try:
        post_cleanup = await _counts(driver, target_group, "post")
    except ScopedC2CleanupError as exc:
        evidence = _evidence(
            status="post_cleanup_verification_failed",
            target_group=target_group,
            freeze_sha256=freeze_sha256,
            pre_cleanup=pre_cleanup,
            post_cleanup={"node_count": None, "relationship_count": None},
        )
        raise ScopedC2CleanupError(exc.reason, evidence=evidence) from None

    status = (
        "verified_empty"
        if post_cleanup["node_count"] == 0
        and post_cleanup["relationship_count"] == 0
        else "residual_detected"
    )
    evidence = _evidence(
        status=status,
        target_group=target_group,
        freeze_sha256=freeze_sha256,
        pre_cleanup=pre_cleanup,
        post_cleanup=post_cleanup,
    )
    if status != "verified_empty":
        raise ScopedC2CleanupError(
            "post_cleanup_residual_detected", evidence=evidence
        )
    return evidence
