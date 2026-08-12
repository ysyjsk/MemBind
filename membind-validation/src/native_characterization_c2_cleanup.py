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
FAILED_C2_ATTEMPT_ID = "c2-c5e5463facb3bce7"
INTERRUPTED_C2_ATTEMPT_ID = "c2-2fe3711c62933407"
SERVING_ENVELOPE_C2_ATTEMPT_ID = "c2-4cc7d0599bbbbdac"
POLLUTED_C2_GROUP_ID = "nc-e1e2-400b9b78c2c218df"
CLEANUP_PRIMITIVE = "graphiti.clear_data(driver,group_ids=[target_group])"
SOURCE_FREEZE_RELATIVE_PATH = (
    "artifacts/native_characterization/freeze_json_object.json"
)
SOURCE_FREEZE_SHA256 = (
    "1952fb7cde2fed9b9ef22024a98642de83e7c29aade1144148e5b734953b4b28"
)
PLANNED_EVIDENCE_RELATIVE_PATH = (
    f"artifacts/native_characterization/c2_cleanup/{FAILED_C2_ATTEMPT_ID}.json"
)
INTERRUPTION_SOURCE_FREEZE_RELATIVE_PATH = (
    "artifacts/native_characterization/freeze_reference_aligned.json"
)
INTERRUPTION_SOURCE_FREEZE_SHA256 = (
    "cea700f73f7dc942deeb49195e0a3ca235c35ec51a1c06fdab0edd94738330a7"
)
INTERRUPTION_EVIDENCE_RELATIVE_PATH = (
    f"artifacts/native_characterization/c2_cleanup/{INTERRUPTED_C2_ATTEMPT_ID}.json"
)
SERVING_ENVELOPE_EVIDENCE_RELATIVE_PATH = (
    "artifacts/native_characterization/c2_cleanup/"
    f"{SERVING_ENVELOPE_C2_ATTEMPT_ID}.json"
)
_CLEANUP_STATUS = "native_characterization_cleanup_only"
_CLEANUP_SCOPE = "native_characterization_c2_cleanup_only"
_CLEANUP_BLOCKER = "c2_reference_aligned_cleanup_pending"
_CLEANUP_NEXT_ACTION = "execute_scoped_c2_cleanup_reference_aligned_precondition"

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


def _read_cleanup_grant(current_state_path: str | Path) -> tuple[Path, str, str]:
    """Validate the exact one-shot cleanup grant before database I/O."""
    state_path = Path(current_state_path)
    try:
        state = json.loads(state_path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ScopedC2CleanupError("cleanup_state_invalid") from None
    if not isinstance(state, Mapping):
        raise ScopedC2CleanupError("cleanup_state_invalid")

    alignment = state.get("native_characterization_reference_alignment")
    cleanup = alignment.get("cleanup") if isinstance(alignment, Mapping) else None
    shared_state = (
        state.get("protocol_version") == "current-validation-v1.3"
        and state.get("current_stage") == "NATIVE_CHARACTERIZATION"
        and state.get("status") == _CLEANUP_STATUS
        and state.get("current_action_scope") == _CLEANUP_SCOPE
        and state.get("authorized_live_actions") == []
        and state.get("native_characterization_live_authorized") is False
        and state.get("live_h0_candidate_authorized") is False
        and state.get("service_admin_authorized") is False
        and isinstance(alignment, Mapping)
        and isinstance(cleanup, Mapping)
        and cleanup.get("operator_authorized") is True
        and cleanup.get("execution_status") == "pending"
        and cleanup.get("target_group_id") == POLLUTED_C2_GROUP_ID
        and cleanup.get("required_post_node_count") == 0
        and cleanup.get("required_post_relationship_count") == 0
    )
    historical = (
        shared_state
        and state.get("current_blocker") == _CLEANUP_BLOCKER
        and state.get("next_allowed_action") == _CLEANUP_NEXT_ACTION
        and alignment.get("status") == "offline_green_cleanup_pending"
        and cleanup.get("failed_attempt_id") == FAILED_C2_ATTEMPT_ID
        and cleanup.get("source_freeze_path") == SOURCE_FREEZE_RELATIVE_PATH
        and cleanup.get("source_freeze_sha256") == SOURCE_FREEZE_SHA256
        and cleanup.get("planned_evidence_path") == PLANNED_EVIDENCE_RELATIVE_PATH
    )
    interrupted = (
        shared_state
        and state.get("current_blocker")
        == "c2_infrastructure_interruption_cleanup_pending"
        and state.get("next_allowed_action")
        == "execute_scoped_c2_cleanup_after_infrastructure_interruption"
        and alignment.get("status")
        == "c2_infrastructure_interrupted_cleanup_pending"
        and cleanup.get("failed_attempt_id") == INTERRUPTED_C2_ATTEMPT_ID
        and cleanup.get("failed_attempt_valid") is False
        and cleanup.get("failed_attempt_mergeable") is False
        and cleanup.get("replacement_resume_allowed") is False
        and cleanup.get("source_freeze_path")
        == INTERRUPTION_SOURCE_FREEZE_RELATIVE_PATH
        and cleanup.get("source_freeze_sha256")
        == INTERRUPTION_SOURCE_FREEZE_SHA256
        and cleanup.get("planned_evidence_path")
        == INTERRUPTION_EVIDENCE_RELATIVE_PATH
    )
    failure = state.get("native_characterization_c2_serving_envelope_failure")
    envelope = state.get("native_characterization_64k_serving_envelope")
    prior_receipt = state.get("native_characterization_reference_c2_authorization")
    fresh = alignment.get("fresh_c2") if isinstance(alignment, Mapping) else None
    serving_envelope_failure = (
        shared_state
        and state.get("current_blocker")
        == "c2_serving_envelope_failure_cleanup_pending"
        and state.get("next_allowed_action")
        == "execute_scoped_c2_cleanup_after_serving_envelope_failure"
        and alignment.get("status")
        == "c2_serving_envelope_failed_cleanup_pending"
        and cleanup.get("failed_attempt_id") == SERVING_ENVELOPE_C2_ATTEMPT_ID
        and cleanup.get("failed_attempt_valid") is False
        and cleanup.get("failed_attempt_mergeable") is False
        and cleanup.get("replacement_resume_allowed") is False
        and cleanup.get("source_freeze_path")
        == INTERRUPTION_SOURCE_FREEZE_RELATIVE_PATH
        and cleanup.get("source_freeze_sha256")
        == INTERRUPTION_SOURCE_FREEZE_SHA256
        and cleanup.get("planned_evidence_path")
        == SERVING_ENVELOPE_EVIDENCE_RELATIVE_PATH
        and isinstance(fresh, Mapping)
        and fresh.get("live_authorized") is False
        and isinstance(prior_receipt, Mapping)
        and prior_receipt.get("live_authorized") is False
        and prior_receipt.get("consumed_by_run_id")
        == SERVING_ENVELOPE_C2_ATTEMPT_ID
        and isinstance(failure, Mapping)
        and failure.get("run_id") == SERVING_ENVELOPE_C2_ATTEMPT_ID
        and failure.get("error_code") == "openai.BadRequestError"
        and failure.get("attempt_valid") is False
        and failure.get("attempt_mergeable") is False
        and failure.get("resume_allowed") is False
        and failure.get("prefix_merge_allowed") is False
        and failure.get("cleanup_authorized") is True
        and failure.get("live_authorized") is False
        and isinstance(envelope, Mapping)
        and envelope.get("qualification_status") == "64K_ENVELOPE_PASS"
        and envelope.get("max_model_len") == 65536
        and envelope.get("requested_max_tokens") == 16384
    )
    if historical:
        freeze_relative = SOURCE_FREEZE_RELATIVE_PATH
        freeze_sha256 = SOURCE_FREEZE_SHA256
        attempt_id = FAILED_C2_ATTEMPT_ID
    elif interrupted:
        freeze_relative = INTERRUPTION_SOURCE_FREEZE_RELATIVE_PATH
        freeze_sha256 = INTERRUPTION_SOURCE_FREEZE_SHA256
        attempt_id = INTERRUPTED_C2_ATTEMPT_ID
    elif serving_envelope_failure:
        freeze_relative = INTERRUPTION_SOURCE_FREEZE_RELATIVE_PATH
        freeze_sha256 = INTERRUPTION_SOURCE_FREEZE_SHA256
        attempt_id = SERVING_ENVELOPE_C2_ATTEMPT_ID
    else:
        raise ScopedC2CleanupError("cleanup_state_grant_mismatch")

    freeze_path = state_path.parent / freeze_relative
    if _freeze_sha256(freeze_path) != freeze_sha256:
        raise ScopedC2CleanupError("cleanup_source_freeze_hash_mismatch")
    return freeze_path, freeze_sha256, attempt_id


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
    failed_attempt_id: str,
) -> dict[str, Any]:
    return _with_payload_hash(
        {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "failed_attempt_id": failed_attempt_id,
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
    failed_attempt_id: str = FAILED_C2_ATTEMPT_ID,
    clear_data_impl: ClearData = clear_data,
) -> dict[str, Any]:
    """Delete and verify only the polluted frozen C2 block-0 namespace."""

    if operator_authorized is not True:
        raise ScopedC2CleanupError("operator_authorization_required")
    if type(target_group) is not str or not target_group.strip():
        raise ScopedC2CleanupError("target_group_invalid")
    if target_group != POLLUTED_C2_GROUP_ID:
        raise ScopedC2CleanupError("target_group_not_allowlisted")
    if failed_attempt_id not in {
        FAILED_C2_ATTEMPT_ID,
        INTERRUPTED_C2_ATTEMPT_ID,
        SERVING_ENVELOPE_C2_ATTEMPT_ID,
    }:
        raise ScopedC2CleanupError("failed_attempt_not_allowlisted")
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
            failed_attempt_id=failed_attempt_id,
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
            failed_attempt_id=failed_attempt_id,
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
        failed_attempt_id=failed_attempt_id,
    )
    if status != "verified_empty":
        raise ScopedC2CleanupError(
            "post_cleanup_residual_detected", evidence=evidence
        )
    return evidence


async def cleanup_reference_aligned_c2_precondition(
    *,
    driver: Any,
    current_state_path: str | Path,
    clear_data_impl: ClearData = clear_data,
) -> dict[str, Any]:
    """Execute only the cleanup authorized by the active machine state.

    This is the production entrypoint. The lower-level function remains
    injectable for offline unit tests, while this wrapper binds the mutation to
    the exact failed attempt, source freeze identity, namespace, and state.
    """
    freeze_path, expected_sha256, failed_attempt_id = _read_cleanup_grant(
        current_state_path
    )
    evidence = await cleanup_scoped_c2_namespace(
        driver=driver,
        freeze_path=freeze_path,
        target_group=POLLUTED_C2_GROUP_ID,
        operator_authorized=True,
        failed_attempt_id=failed_attempt_id,
        clear_data_impl=clear_data_impl,
    )
    if evidence.get("freeze_sha256") != expected_sha256:
        raise ScopedC2CleanupError("cleanup_evidence_freeze_hash_mismatch")
    return evidence
