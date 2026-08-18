#!/usr/bin/env python3
"""Run one frozen MemBind v3.1 history as a feasibility/performance gate.

This deliberately sits outside the four-history orchestration entrypoint.  It
reuses the sealed block-0 plan and the already-qualified three-source smoke
gate, then invokes the production block executor exactly once for all 49
sources in history ``07741c45``.  The wrapper owns only a new feasibility
artifact root; it never edits the method plan, baseline artifacts, or the
historical smoke attempt.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
SOURCE = PROJECT / "src"
LEGACY = ROOT / "membind-validation"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
if str(LEGACY / "src") not in sys.path:
    sys.path.insert(0, str(LEGACY / "src"))

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file  # noqa: E402
from paper_eval.membind_v31.method_plan import verify_membind_v31_method_plan  # noqa: E402
from paper_eval.membind_v31.production_executor import (  # noqa: E402
    build_production_executor_hooks,
)


SCHEMA = "membind.paper-eval-v3.membind-v31-single-history-feasibility.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.membind-v31-single-history-checkpoint.v1"
FAILURE_SCHEMA = "membind.paper-eval-v3.membind-v31-single-history-failure.v1"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
HISTORY_ID = "07741c45"
BLOCK_INDEX = 0


class SingleHistoryError(ValueError):
    """The single-history gate cannot be admitted or safely sealed."""


def _fail(code: str) -> SingleHistoryError:
    return SingleHistoryError(code)


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _sealed(value: Mapping[str, object], *, field: str, code: str) -> dict[str, Any]:
    selected = deepcopy(dict(value))
    stored = selected.pop(field, None)
    if not isinstance(stored, str) or stored != payload_sha256(selected):
        raise _fail(code)
    selected[field] = stored
    return selected


def load_and_verify_plan(path: Path) -> dict[str, Any]:
    """Load the immutable method plan and select exactly block 0."""

    plan = _read_json(path, "method_plan_unreadable")
    try:
        verified = verify_membind_v31_method_plan(plan)
    except ValueError:
        raise _fail("method_plan_invalid") from None
    block = verified["blocks"][BLOCK_INDEX]
    if (
        block.get("method") != "MemBind"
        or block.get("history_id") != HISTORY_ID
        or block.get("source_count") != 49
        or block.get("compile_workers") != 2
        or block.get("lookahead") != 2
        or block.get("global_llm_admission_k") != 2
    ):
        raise _fail("block_zero_identity_invalid")
    return verified


def verify_smoke_gate(path: Path, *, plan_payload_sha256: str) -> dict[str, Any]:
    """Require the independent, three-source smoke gate for this plan."""

    gate = _sealed(_read_json(path, "smoke_gate_unreadable"), field="payload_sha256", code="smoke_gate_invalid")
    if (
        gate.get("schema_version") != "membind.paper-eval-v3.membind-v31-smoke-gate.v1"
        or gate.get("status") != "PASS"
        or gate.get("formal_blocks_authorized") is not True
        or gate.get("plan_payload_sha256") != plan_payload_sha256
    ):
        raise _fail("smoke_gate_binding_invalid")
    return gate


def verify_cleanup_evidence(path: Path, *, namespace: str) -> dict[str, Any]:
    """Accept only an exact namespace-scoped cleanup with a zero post-state."""

    evidence = _sealed(
        _read_json(path, "cleanup_evidence_unreadable"),
        field="payload_sha256",
        code="cleanup_evidence_invalid",
    )
    if (
        evidence.get("namespace") != namespace
        or evidence.get("scope") != "EXACT_GROUP_ID_ONLY"
        or evidence.get("global_cleanup_used") is not False
        or evidence.get("post_cleanup_node_count") != 0
        or evidence.get("post_cleanup_relationship_count") != 0
    ):
        raise _fail("cleanup_evidence_not_fresh")
    return evidence


def build_manifest(*, attempt_id: str, plan: Mapping[str, Any], gate: Mapping[str, Any], cleanup: Mapping[str, Any]) -> dict[str, Any]:
    """Build a public, sealed wrapper manifest before opening live services."""

    if not isinstance(attempt_id, str) or _ID.fullmatch(attempt_id) is None:
        raise _fail("attempt_id_invalid")
    block = plan["blocks"][BLOCK_INDEX]
    body: dict[str, Any] = {
        "schema_version": SCHEMA,
        "status": "RUNNING",
        "attempt_id": attempt_id,
        "plan_payload_sha256": plan["payload_sha256"],
        "smoke_gate_payload_sha256": gate["payload_sha256"],
        "block_index": BLOCK_INDEX,
        "method": block["method"],
        "history_id": block["history_id"],
        "source_count": block["source_count"],
        "namespace": block["namespace"],
        "source_manifest_sha256": block["source_manifest_sha256"],
        "history_arrival_trace_sha256": block["history_arrival_trace_sha256"],
        "shared_execution_envelope_sha256": block["shared_execution_envelope_sha256"],
        "compile_workers": block["compile_workers"],
        "lookahead": block["lookahead"],
        "global_llm_admission_k": block["global_llm_admission_k"],
        "policy": block["policy"],
        "cleanup_evidence_payload_sha256": cleanup["payload_sha256"],
        "formal_main_table_eligible": False,
        "result_role": "SINGLE_HISTORY_FEASIBILITY_GATE_NOT_FINAL_TABLE",
    }
    return {**body, "payload_sha256": payload_sha256(body)}


def _checkpoint(*, manifest: Mapping[str, Any], status: str, block_checkpoint: Mapping[str, Any] | None = None, error_class: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": CHECKPOINT_SCHEMA,
        "attempt_id": manifest["attempt_id"],
        "manifest_payload_sha256": manifest["payload_sha256"],
        "status": status,
        "block_index": BLOCK_INDEX,
        "history_id": HISTORY_ID,
        "error_class": error_class,
        "block_checkpoint": deepcopy(dict(block_checkpoint)) if block_checkpoint is not None else None,
    }
    return {**body, "payload_sha256": payload_sha256(body)}


def _block_checkpoint(root: Path) -> dict[str, Any] | None:
    path = Path(root) / "checkpoint.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def run_single_history(
    *,
    plan_path: Path,
    smoke_gate_path: Path,
    cleanup_evidence_path: Path,
    attempt_root: Path,
    attempt_id: str,
    hooks: object | None = None,
) -> dict[str, Any]:
    """Execute block 0 once and seal a feasibility result or failure."""

    plan = load_and_verify_plan(plan_path)
    block = plan["blocks"][BLOCK_INDEX]
    gate = verify_smoke_gate(smoke_gate_path, plan_payload_sha256=plan["payload_sha256"])
    cleanup = verify_cleanup_evidence(cleanup_evidence_path, namespace=block["namespace"])
    root = Path(attempt_root)
    if root.exists():
        raise _fail("attempt_root_exists")
    root.mkdir(parents=True, exist_ok=False)
    block_root = root / "block-00"
    manifest = build_manifest(attempt_id=attempt_id, plan=plan, gate=gate, cleanup=cleanup)
    atomic_write_json(root / "MANIFEST.json", manifest)
    atomic_write_json(root / "CHECKPOINT.json", _checkpoint(manifest=manifest, status="RUNNING"))
    selected_hooks = build_production_executor_hooks() if hooks is None else hooks
    try:
        run_block = getattr(selected_hooks, "run_block", None)
        if not callable(run_block):
            raise _fail("executor_hook_invalid")
        result = run_block(plan, BLOCK_INDEX, block_root)
        if not isinstance(result, Mapping) or result.get("status") != "PASS":
            raise _fail("block_result_invalid")
        if result.get("source_count") != 49 or result.get("history_id") != HISTORY_ID:
            raise _fail("block_result_identity_invalid")
        result_copy = deepcopy(dict(result))
        body = {
            "schema_version": SCHEMA,
            "status": "PASS",
            "attempt_id": attempt_id,
            "plan_payload_sha256": plan["payload_sha256"],
            "block_result_payload_sha256": result_copy.get("payload_sha256"),
            "block_result": result_copy,
            "result_role": "SINGLE_HISTORY_FEASIBILITY_GATE_NOT_FINAL_TABLE",
            "formal_main_table_eligible": False,
        }
        sealed = {**body, "payload_sha256": payload_sha256(body)}
        atomic_write_json(root / "RESULT.json", sealed)
        atomic_write_json(
            root / "CHECKPOINT.json",
            _checkpoint(
                manifest=manifest,
                status="COMPLETED",
                block_checkpoint=_block_checkpoint(block_root),
            ),
        )
        return sealed
    except BaseException as error:
        failure_body = {
            "schema_version": FAILURE_SCHEMA,
            "status": "FAILED_NON_REUSABLE",
            "attempt_id": attempt_id,
            "plan_payload_sha256": plan["payload_sha256"],
            "failure_stage": "FORMAL_BLOCK",
            "error_class": f"{type(error).__module__}.{type(error).__qualname__}",
            "error_code": str(error),
            "block_checkpoint": _block_checkpoint(block_root),
            "formal_main_table_eligible": False,
        }
        atomic_write_json(root / "FAILURE.json", {**failure_body, "payload_sha256": payload_sha256(failure_body)})
        atomic_write_json(
            root / "CHECKPOINT.json",
            _checkpoint(
                manifest=manifest,
                status="FAILED_NON_REUSABLE",
                block_checkpoint=_block_checkpoint(block_root),
                error_class=f"{type(error).__module__}.{type(error).__qualname__}",
            ),
        )
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=PROJECT / "artifacts/paper_eval/membind_v31/V31_METHOD_PLAN.json")
    parser.add_argument("--smoke-gate", type=Path, required=True)
    parser.add_argument("--cleanup-evidence", type=Path, required=True)
    parser.add_argument("--attempt-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = run_single_history(
            plan_path=args.plan,
            smoke_gate_path=args.smoke_gate,
            cleanup_evidence_path=args.cleanup_evidence,
            attempt_root=args.attempt_root,
            attempt_id=args.attempt_id,
        )
    except BaseException as error:
        print(json.dumps({"status": "FAILED_NON_REUSABLE", "error_class": f"{type(error).__module__}.{type(error).__qualname__}", "error_code": str(error)}, sort_keys=True), flush=True)
        return 1
    print(json.dumps({"status": result["status"], "attempt_id": result["attempt_id"], "payload_sha256": result["payload_sha256"]}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

