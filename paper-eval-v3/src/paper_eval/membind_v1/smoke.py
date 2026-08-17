"""Isolated 3-5 episode fresh-namespace live smoke for MemBind-v1.

The smoke stage deterministically truncates a verified formal aligned plan,
creates a new smoke-only aligned run/namespace, and delegates the actual
node-only execution to :func:`execute_aligned_live_block`.  Its outer attempt
root is intentionally non-resumable: a failed or interrupted smoke is evidence
to inspect, never a namespace that may be reused in place.

This module does not launch model, embedding, or Neo4j services.  Production
service access occurs only through the existing aligned-live composition when
the caller invokes this async stage.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.membind_v1.aligned_live import (
    AlignedLiveHooks,
    execute_aligned_live_block,
)
from paper_eval.membind_v1.aligned_plan import (
    ALIGNED_DEVELOPMENT_HISTORIES,
    build_aligned_development_plan,
    verify_aligned_development_plan,
)
from paper_eval.membind_v1.graphiti_adapter import NodeArtifactIdentity


MANIFEST_SCHEMA = "membind.paper-eval-v3.membind-v1-smoke-manifest.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.membind-v1-smoke-checkpoint.v1"
RESULT_SCHEMA = "membind.paper-eval-v3.membind-v1-smoke-result.v1"
_METHOD = "MemBind-v1 node-only"
_RUN_ID = re.compile(r"^smoke-[a-z0-9][a-z0-9-]{2,48}$")


class MemBindV1SmokeError(RuntimeError):
    """A smoke attempt input, artifact, or live execution failed closed."""


def _fail(code: str) -> MemBindV1SmokeError:
    return MemBindV1SmokeError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise _fail(code)
    try:
        int(value, 16)
    except ValueError:
        raise _fail(code) from None
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _read_json(path: Path, code: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _seal(body: Mapping[str, object], field: str) -> dict[str, object]:
    value = deepcopy(dict(body))
    value[field] = payload_sha256(value)
    return value


def _verify_seal(value: Mapping[str, object], field: str, code: str) -> str:
    stored = _sha(value.get(field), code)
    body = {key: item for key, item in value.items() if key != field}
    if stored != payload_sha256(body):
        raise _fail(code)
    return stored


def _build_smoke_plan(
    *,
    smoke_run_id: str,
    formal_plan: Mapping[str, object],
    sample_count: int,
) -> dict[str, Any]:
    sources = formal_plan.get("history_source_sha256s")
    if not isinstance(sources, Mapping):
        raise _fail("formal source inventory invalid")
    selected: dict[str, list[str]] = {}
    for history_id in ALIGNED_DEVELOPMENT_HISTORIES:
        raw = sources.get(history_id)
        if not isinstance(raw, list) or len(raw) < sample_count:
            raise _fail("formal plan has insufficient smoke sources")
        selected[history_id] = [
            _sha(item, "formal source identity invalid")
            for item in raw[:sample_count]
        ]
    try:
        return verify_aligned_development_plan(
            build_aligned_development_plan(
                aligned_run_id=f"aligned-{smoke_run_id}",
                history_source_sha256s=selected,
                interarrival_ns=formal_plan.get("interarrival_ns"),
                shared_execution_envelope_sha256=formal_plan.get(
                    "shared_execution_envelope_sha256"
                ),
            )
        )
    except ValueError:
        raise _fail("smoke aligned plan invalid") from None


def _membind_block(
    plan: Mapping[str, object], *, history_id: str
) -> dict[str, object]:
    blocks = plan.get("blocks")
    if not isinstance(blocks, list):
        raise _fail("smoke block inventory invalid")
    matches = [
        block
        for block in blocks
        if isinstance(block, Mapping)
        and block.get("history_id") == history_id
        and block.get("method") == _METHOD
    ]
    if len(matches) != 1:
        raise _fail("smoke block inventory invalid")
    return deepcopy(dict(matches[0]))


def _artifact_identity(value: NodeArtifactIdentity) -> dict[str, str]:
    if not isinstance(value, NodeArtifactIdentity):
        raise _fail("membind artifact identity invalid")
    result = asdict(value)
    for field, item in result.items():
        _sha(item, f"membind artifact {field} invalid")
    return result


def _checkpoint(
    *,
    manifest_sha256: str,
    status: str,
    error_class: str | None,
    result_payload_sha256: str | None,
) -> dict[str, object]:
    resume = {
        "RUNNING": "DO_NOT_REUSE_ATTEMPT_IN_PROGRESS",
        "COMPLETED": "NOT_NEEDED_COMPLETE",
        "FAILED_NON_REUSABLE": "DO_NOT_REUSE_CREATE_NEW_ATTEMPT",
    }
    if status not in resume:
        raise _fail("smoke checkpoint status invalid")
    if status == "COMPLETED":
        _sha(result_payload_sha256, "smoke result identity invalid")
        if error_class is not None:
            raise _fail("smoke checkpoint invalid")
    elif result_payload_sha256 is not None:
        raise _fail("smoke checkpoint invalid")
    if error_class is not None and (
        not isinstance(error_class, str) or not error_class
    ):
        raise _fail("smoke checkpoint invalid")
    return _seal(
        {
            "schema_version": CHECKPOINT_SCHEMA,
            "manifest_sha256": manifest_sha256,
            "status": status,
            "resume_status": resume[status],
            "error_class": error_class,
            "result_payload_sha256": result_payload_sha256,
        },
        "checkpoint_sha256",
    )


def _manifest(
    *,
    smoke_run_id: str,
    formal_plan: Mapping[str, object],
    smoke_plan: Mapping[str, object],
    block: Mapping[str, object],
    sample_count: int,
    execution_identity_sha256: str,
    artifact_identity: Mapping[str, str],
) -> dict[str, object]:
    return _seal(
        {
            "schema_version": MANIFEST_SCHEMA,
            "smoke_run_id": smoke_run_id,
            "formal_plan_payload_sha256": formal_plan["payload_sha256"],
            "smoke_plan_payload_sha256": smoke_plan["payload_sha256"],
            "aligned_run_id": block["aligned_run_id"],
            "block_index": block["block_index"],
            "method": block["method"],
            "history_id": block["history_id"],
            "namespace": block["namespace"],
            "source_count": sample_count,
            "source_sha256s": smoke_plan["history_source_sha256s"][
                block["history_id"]
            ],
            "source_manifest_sha256": block["source_manifest_sha256"],
            "arrival_trace_sha256": block["arrival_trace_sha256"],
            "shared_execution_envelope_sha256": block[
                "shared_execution_envelope_sha256"
            ],
            "global_llm_admission_k": 2,
            "execution_identity_sha256": execution_identity_sha256,
            "membind_artifact_identity": dict(artifact_identity),
            "membind_artifact_identity_sha256": payload_sha256(artifact_identity),
            "resume_policy": "NEVER_REUSE_ATTEMPT_ROOT",
        },
        "manifest_sha256",
    )


def _ordered_plan_projection(value: Mapping[str, object]) -> dict[str, object]:
    """Restore frozen history map order after canonical JSON persistence.

    ``atomic_write_json`` writes sorted object keys while the existing aligned
    plan verifier intentionally checks its two history-keyed maps in frozen
    order.  JSON object order is not part of a plan's payload hash, so this is
    a read-only representation normalization before delegation to that
    verifier, not a plan rewrite or reseal.
    """

    candidate = deepcopy(dict(value))
    for field in ("history_source_sha256s", "arrival_traces"):
        raw = candidate.get(field)
        if isinstance(raw, Mapping) and set(raw) == set(ALIGNED_DEVELOPMENT_HISTORIES):
            candidate[field] = {
                history_id: raw[history_id]
                for history_id in ALIGNED_DEVELOPMENT_HISTORIES
            }
    return candidate


def inspect_membind_v1_smoke(root: Path) -> dict[str, object]:
    """Verify an outer smoke attempt without opening any live service."""

    target = Path(root)
    manifest = _read_json(target / "manifest.json", "smoke manifest unreadable")
    expected_manifest_keys = {
        "schema_version",
        "smoke_run_id",
        "formal_plan_payload_sha256",
        "smoke_plan_payload_sha256",
        "aligned_run_id",
        "block_index",
        "method",
        "history_id",
        "namespace",
        "source_count",
        "source_sha256s",
        "source_manifest_sha256",
        "arrival_trace_sha256",
        "shared_execution_envelope_sha256",
        "global_llm_admission_k",
        "execution_identity_sha256",
        "membind_artifact_identity",
        "membind_artifact_identity_sha256",
        "resume_policy",
        "manifest_sha256",
    }
    if set(manifest) != expected_manifest_keys or manifest.get(
        "schema_version"
    ) != MANIFEST_SCHEMA:
        raise _fail("smoke manifest invalid")
    manifest_sha = _verify_seal(
        manifest, "manifest_sha256", "smoke manifest hash invalid"
    )
    if (
        manifest.get("method") != _METHOD
        or manifest.get("global_llm_admission_k") != 2
        or manifest.get("resume_policy") != "NEVER_REUSE_ATTEMPT_ROOT"
        or manifest.get("source_count") not in {3, 4, 5}
    ):
        raise _fail("smoke manifest invalid")
    for field in (
        "formal_plan_payload_sha256",
        "smoke_plan_payload_sha256",
        "source_manifest_sha256",
        "arrival_trace_sha256",
        "shared_execution_envelope_sha256",
        "execution_identity_sha256",
        "membind_artifact_identity_sha256",
    ):
        _sha(manifest.get(field), "smoke manifest invalid")
    identity = manifest.get("membind_artifact_identity")
    if not isinstance(identity, Mapping) or payload_sha256(identity) != manifest.get(
        "membind_artifact_identity_sha256"
    ):
        raise _fail("smoke manifest invalid")

    smoke_plan_raw = _read_json(
        target / "SMOKE_PLAN.json", "smoke plan unreadable"
    )
    try:
        smoke_plan = verify_aligned_development_plan(
            _ordered_plan_projection(smoke_plan_raw)
        )
    except ValueError:
        raise _fail("smoke plan invalid") from None
    if smoke_plan.get("payload_sha256") != manifest.get(
        "smoke_plan_payload_sha256"
    ):
        raise _fail("smoke plan binding invalid")
    block_index = _nonnegative_int(manifest.get("block_index"), "smoke block invalid")
    blocks = smoke_plan.get("blocks")
    if not isinstance(blocks, list) or block_index >= len(blocks):
        raise _fail("smoke block invalid")
    block = blocks[block_index]
    if not isinstance(block, Mapping):
        raise _fail("smoke block invalid")
    bindings = {
        "aligned_run_id": block["aligned_run_id"],
        "block_index": block["block_index"],
        "method": block["method"],
        "history_id": block["history_id"],
        "namespace": block["namespace"],
        "source_count": block["source_count"],
        "source_manifest_sha256": block["source_manifest_sha256"],
        "arrival_trace_sha256": block["arrival_trace_sha256"],
        "shared_execution_envelope_sha256": block[
            "shared_execution_envelope_sha256"
        ],
    }
    if any(manifest.get(key) != value for key, value in bindings.items()):
        raise _fail("smoke block binding invalid")

    checkpoint = _read_json(
        target / "checkpoint.json", "smoke checkpoint unreadable"
    )
    expected_checkpoint_keys = {
        "schema_version",
        "manifest_sha256",
        "status",
        "resume_status",
        "error_class",
        "result_payload_sha256",
        "checkpoint_sha256",
    }
    if set(checkpoint) != expected_checkpoint_keys or checkpoint.get(
        "schema_version"
    ) != CHECKPOINT_SCHEMA:
        raise _fail("smoke checkpoint invalid")
    _verify_seal(
        checkpoint, "checkpoint_sha256", "smoke checkpoint hash invalid"
    )
    expected_checkpoint = _checkpoint(
        manifest_sha256=manifest_sha,
        status=str(checkpoint.get("status")),
        error_class=checkpoint.get("error_class"),
        result_payload_sha256=checkpoint.get("result_payload_sha256"),
    )
    if checkpoint != expected_checkpoint:
        raise _fail("smoke checkpoint invalid")

    result: dict[str, object] | None = None
    result_path = target / "SMOKE_RESULT.json"
    if checkpoint["status"] == "COMPLETED":
        result = _read_json(result_path, "smoke result unreadable")
        if result.get("schema_version") != RESULT_SCHEMA or result.get(
            "status"
        ) != "PASS":
            raise _fail("smoke result invalid")
        result_sha = _verify_seal(
            result, "payload_sha256", "smoke result hash invalid"
        )
        if (
            result_sha != checkpoint["result_payload_sha256"]
            or result.get("manifest_sha256") != manifest_sha
        ):
            raise _fail("smoke result binding invalid")
    elif result_path.exists():
        raise _fail("smoke result exists for incomplete attempt")
    return {
        "manifest": manifest,
        "smoke_plan": smoke_plan,
        "checkpoint": checkpoint,
        "result": result,
    }


async def run_membind_v1_smoke(
    root: Path,
    *,
    smoke_run_id: str,
    formal_verified_plan: Mapping[str, object],
    history_id: str,
    episodes: Sequence[object],
    sample_count: int,
    env: Mapping[str, str],
    execution_identity_sha256: str,
    membind_artifact_identity: NodeArtifactIdentity,
    hooks: AlignedLiveHooks | None = None,
) -> dict[str, object]:
    """Run one non-resumable, smoke-only MemBind-v1 fresh namespace."""

    target = Path(root)
    if target.exists():
        raise _fail("smoke attempt root already exists")
    if not isinstance(smoke_run_id, str) or _RUN_ID.fullmatch(smoke_run_id) is None:
        raise _fail("smoke run id invalid")
    count = _nonnegative_int(sample_count, "smoke sample count invalid")
    if count not in {3, 4, 5}:
        raise _fail("smoke sample count must be 3-5")
    if history_id not in ALIGNED_DEVELOPMENT_HISTORIES:
        raise _fail("smoke history invalid")
    if isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence):
        raise _fail("smoke episodes invalid")
    selected_episodes = tuple(episodes[:count])
    if len(selected_episodes) != count or any(item is None for item in selected_episodes):
        raise _fail("smoke episodes invalid")
    if not isinstance(env, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in env.items()
    ):
        raise _fail("smoke environment invalid")
    execution_identity = _sha(
        execution_identity_sha256, "smoke execution identity invalid"
    )
    artifact_identity = _artifact_identity(membind_artifact_identity)
    try:
        formal_plan = verify_aligned_development_plan(formal_verified_plan)
    except ValueError:
        raise _fail("formal verified plan invalid") from None
    smoke_plan = _build_smoke_plan(
        smoke_run_id=smoke_run_id,
        formal_plan=formal_plan,
        sample_count=count,
    )
    block = _membind_block(smoke_plan, history_id=history_id)
    manifest = _manifest(
        smoke_run_id=smoke_run_id,
        formal_plan=formal_plan,
        smoke_plan=smoke_plan,
        block=block,
        sample_count=count,
        execution_identity_sha256=execution_identity,
        artifact_identity=artifact_identity,
    )

    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise _fail("smoke attempt root already exists") from None
    atomic_write_json(target / "manifest.json", manifest)
    atomic_write_json(target / "SMOKE_PLAN.json", smoke_plan)
    atomic_write_json(
        target / "checkpoint.json",
        _checkpoint(
            manifest_sha256=str(manifest["manifest_sha256"]),
            status="RUNNING",
            error_class=None,
            result_payload_sha256=None,
        ),
    )
    try:
        live_result = await execute_aligned_live_block(
            verified_plan=smoke_plan,
            block_index=int(block["block_index"]),
            episodes=selected_episodes,
            env=dict(env),
            block_root=target / "aligned-block",
            execution_identity_sha256=execution_identity,
            hooks=hooks,
            membind_artifact_identity=membind_artifact_identity,
        )
        body = {
            "schema_version": RESULT_SCHEMA,
            "status": "PASS",
            "smoke_run_id": smoke_run_id,
            "manifest_sha256": manifest["manifest_sha256"],
            "formal_plan_payload_sha256": formal_plan["payload_sha256"],
            "smoke_plan_payload_sha256": smoke_plan["payload_sha256"],
            "aligned_run_id": block["aligned_run_id"],
            "block_index": block["block_index"],
            "method": block["method"],
            "history_id": block["history_id"],
            "namespace": block["namespace"],
            "source_count": count,
            "source_manifest_sha256": block["source_manifest_sha256"],
            "arrival_trace_sha256": block["arrival_trace_sha256"],
            "shared_execution_envelope_sha256": block[
                "shared_execution_envelope_sha256"
            ],
            "global_llm_admission_k": 2,
            "live_result": live_result,
        }
        result = _seal(body, "payload_sha256")
        atomic_write_json(target / "SMOKE_RESULT.json", result)
        atomic_write_json(
            target / "checkpoint.json",
            _checkpoint(
                manifest_sha256=str(manifest["manifest_sha256"]),
                status="COMPLETED",
                error_class=None,
                result_payload_sha256=str(result["payload_sha256"]),
            ),
        )
        inspect_membind_v1_smoke(target)
        return result
    except asyncio.CancelledError as error:
        atomic_write_json(
            target / "checkpoint.json",
            _checkpoint(
                manifest_sha256=str(manifest["manifest_sha256"]),
                status="FAILED_NON_REUSABLE",
                error_class=type(error).__name__,
                result_payload_sha256=None,
            ),
        )
        raise
    except BaseException as error:
        atomic_write_json(
            target / "checkpoint.json",
            _checkpoint(
                manifest_sha256=str(manifest["manifest_sha256"]),
                status="FAILED_NON_REUSABLE",
                error_class=type(error).__name__,
                result_payload_sha256=None,
            ),
        )
        raise _fail("smoke live execution failed") from None


__all__ = [
    "CHECKPOINT_SCHEMA",
    "MANIFEST_SCHEMA",
    "MemBindV1SmokeError",
    "RESULT_SCHEMA",
    "inspect_membind_v1_smoke",
    "run_membind_v1_smoke",
]
