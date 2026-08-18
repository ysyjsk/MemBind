"""Offline control-plane orchestration for smoke then six v3.1 blocks.

Execution is supplied through explicit hooks.  This module validates the
source-bound live plan, enforces exact stage order, and seals only
content-safe orchestration artifacts.  It has no Graphiti, provider, or graph
database dependency.
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.membind_v31.materialization import (
    METHOD_PLAN_NAME,
    inspect_materialized_control,
)
from paper_eval.membind_v31.method_plan import (
    LIVE_AUTHORIZATION_SCOPE,
    verify_membind_v31_method_plan,
)


MANIFEST_SCHEMA = "membind.paper-eval-v3.membind-v31-orchestration-manifest.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.membind-v31-orchestration-checkpoint.v1"
SMOKE_RESULT_SCHEMA = "membind.paper-eval-v3.membind-v31-smoke-result.v1"
SMOKE_GATE_SCHEMA = "membind.paper-eval-v3.membind-v31-smoke-gate.v1"
SMOKE_ONLY_RESULT_SCHEMA = (
    "membind.paper-eval-v3.membind-v31-smoke-only-result.v1"
)
RESULT_SCHEMA = "membind.paper-eval-v3.membind-v31-orchestration-result.v1"
MAIN_METHOD_RESULT_SCHEMA = (
    "membind.paper-eval-v3.membind-v31-main-method-result.v1"
)
FAILURE_SCHEMA = "membind.paper-eval-v3.membind-v31-orchestration-failure.v1"
METHOD_RESULT_SCHEMA = "membind.paper-eval-v3.membind-v31-live-block-result.v1"
REPRESENTATIVE_HISTORY = "07741c45"
SMOKE_BLOCK_INDEX = 0
SMOKE_SOURCE_SEQUENCES = (0, 1, 2)
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENTRYPOINT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")


class OrchestrationError(ValueError):
    """A control transaction, stage order, or returned result is invalid."""


def _fail(code: str) -> OrchestrationError:
    return OrchestrationError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _seal(body: Mapping[str, object]) -> dict[str, Any]:
    selected = deepcopy(dict(body))
    selected["payload_sha256"] = payload_sha256(selected)
    return selected


def _sealed(
    value: Mapping[str, object], *, label: str, field: str = "payload_sha256"
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(f"{label} invalid")
    selected = deepcopy(dict(value))
    stored = _sha(selected.get(field), f"{label} hash invalid")
    body = {key: child for key, child in selected.items() if key != field}
    if payload_sha256(body) != stored:
        raise _fail(f"{label} hash mismatch")
    return selected


def _read_sealed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(f"{label} unreadable") from None
    if not isinstance(value, dict):
        raise _fail(f"{label} invalid")
    return _sealed(value, label=label)


@dataclass(frozen=True, slots=True)
class SmokeSpec:
    """Exact content-safe identity of the three-episode live gate."""

    attempt_id: str
    plan_payload_sha256: str
    control_commit_payload_sha256: str | None
    block_index: int
    method: str
    history_id: str
    namespace: str
    source_sequences: tuple[int, ...]
    global_llm_admission_k: int


@dataclass(frozen=True, slots=True)
class OrchestrationHooks:
    """Explicit execution boundary; hook implementations may own live access."""

    executor_identity_sha256: str
    run_smoke: Callable[[SmokeSpec, Path], Mapping[str, object]]
    run_block: Callable[[Mapping[str, object], int, Path], Mapping[str, object]]

    def __post_init__(self) -> None:
        _sha(self.executor_identity_sha256, "executor identity invalid")
        if not callable(self.run_smoke) or not callable(self.run_block):
            raise _fail("executor hook invalid")


def _control(root: Path) -> tuple[dict[str, Any], bool, str | None]:
    try:
        control = inspect_materialized_control(Path(root))
    except ValueError as error:
        plan_path = Path(root) / METHOD_PLAN_NAME
        if not plan_path.is_file():
            raise _fail(f"live plan or materialized control invalid: {error}") from None
        plan = _read_sealed(plan_path, label="method plan")
        try:
            plan = verify_membind_v31_method_plan(plan)
        except ValueError as plan_error:
            raise _fail(f"live plan invalid: {plan_error}") from None
        merge_authorized = False
        commit_sha256 = None
    else:
        if not isinstance(control, Mapping):
            raise _fail("materialized control invalid")
        acceptance = _sealed(control.get("acceptance"), label="baseline acceptance")
        plan = _sealed(control.get("method_plan"), label="method plan")
        commit = _sealed(control.get("commit"), label="control commit")
        if (
            acceptance.get("status") != "PASS"
            or commit.get("status") != "COMMITTED"
            or commit.get("method_plan_payload_sha256") != plan["payload_sha256"]
            or commit.get("baseline_acceptance_payload_sha256")
            != acceptance["payload_sha256"]
        ):
            raise _fail("materialized control binding invalid")
        merge_authorized = True
        commit_sha256 = commit["payload_sha256"]
    blocks = plan.get("blocks")
    if (
        plan.get("authorization_scope") != LIVE_AUTHORIZATION_SCOPE
        or not isinstance(blocks, list)
        or len(blocks) != 6
        or [block.get("block_index") for block in blocks] != list(range(6))
        or plan.get("global_llm_admission_k") != 2
    ):
        raise _fail("live plan binding invalid")
    expected = (
        ("MemBind", "07741c45"),
        ("MemBind", "b6019101"),
        ("MemBind", "6071bd76"),
        ("MemBind", "a2f3aa27"),
        ("MemBind-Barrier", "07741c45"),
        ("MemBind-FIFO", "07741c45"),
    )
    if [(block.get("method"), block.get("history_id")) for block in blocks] != list(expected):
        raise _fail("materialized six-block order invalid")
    return plan, merge_authorized, commit_sha256


def _smoke_spec(
    *, attempt_id: str, plan: Mapping[str, Any], commit_sha256: str | None
) -> SmokeSpec:
    block = plan["blocks"][SMOKE_BLOCK_INDEX]
    namespace = f"{block['namespace']}-smoke-{attempt_id}"
    if not _IDENTITY.fullmatch(namespace):
        raise _fail("smoke namespace invalid")
    return SmokeSpec(
        attempt_id=attempt_id,
        plan_payload_sha256=plan["payload_sha256"],
        control_commit_payload_sha256=commit_sha256,
        block_index=SMOKE_BLOCK_INDEX,
        method="MemBind",
        history_id=REPRESENTATIVE_HISTORY,
        namespace=namespace,
        source_sequences=SMOKE_SOURCE_SEQUENCES,
        global_llm_admission_k=2,
    )


def _manifest(
    *,
    attempt_id: str,
    plan: Mapping[str, Any],
    hooks: OrchestrationHooks,
    smoke: SmokeSpec,
) -> dict[str, Any]:
    return _seal(
        {
            "schema_version": MANIFEST_SCHEMA,
            "status": "ACTIVE",
            "attempt_id": attempt_id,
            "method_run_id": plan["run_id"],
            "method_plan_payload_sha256": plan["payload_sha256"],
            "authorization_scope": LIVE_AUTHORIZATION_SCOPE,
            "executor_identity_sha256": hooks.executor_identity_sha256,
            "smoke_block_index": smoke.block_index,
            "smoke_history_id": smoke.history_id,
            "smoke_namespace": smoke.namespace,
            "smoke_source_sequences": list(smoke.source_sequences),
            "formal_block_indices": list(range(6)),
        }
    )


def _checkpoint(
    *,
    manifest_sha256: str,
    status: str,
    smoke_result_sha256: str | None,
    completed_hashes: list[str],
    next_block_index: int,
) -> dict[str, Any]:
    return _seal(
        {
            "schema_version": CHECKPOINT_SCHEMA,
            "status": status,
            "manifest_payload_sha256": manifest_sha256,
            "smoke_result_payload_sha256": smoke_result_sha256,
            "completed_block_indices": list(range(len(completed_hashes))),
            "completed_block_result_payload_sha256s": list(completed_hashes),
            "next_block_index": next_block_index,
        }
    )


def _verify_smoke(value: Mapping[str, object], *, spec: SmokeSpec) -> dict[str, Any]:
    result = _sealed(value, label="smoke result")
    expected = {
        "schema_version": SMOKE_RESULT_SCHEMA,
        "status": "PASS",
        "attempt_id": spec.attempt_id,
        "plan_payload_sha256": spec.plan_payload_sha256,
        "method": spec.method,
        "history_id": spec.history_id,
        "namespace": spec.namespace,
        "source_sequences": list(spec.source_sequences),
        "source_count": 3,
        "global_llm_admission_k": 2,
        "verified_prepared_artifact_count": 3,
        "publication_source_sequences": list(spec.source_sequences),
        "visibility_confirmed_count": 3,
        "direct_violation_count": 0,
    }
    if any(result.get(key) != wanted for key, wanted in expected.items()):
        raise _fail("smoke result contract invalid")
    observed = result.get("observed_max_inflight")
    if isinstance(observed, bool) or not isinstance(observed, int) or not 0 <= observed <= 2:
        raise _fail("smoke admission bound invalid")
    return result


def _verify_block(
    value: Mapping[str, object], *, plan: Mapping[str, Any], index: int
) -> dict[str, Any]:
    result = _sealed(value, label=f"block {index} result")
    block = plan["blocks"][index]
    expected = {
        "schema_version": METHOD_RESULT_SCHEMA,
        "status": "PASS",
        "run_id": plan["run_id"],
        "block_index": index,
        "method": block["method"],
        "policy": block["policy"],
        "history_id": block["history_id"],
        "namespace": block["namespace"],
        "source_count": block["source_count"],
        "plan_payload_sha256": plan["payload_sha256"],
        "global_llm_admission_k": 2,
        "direct_violation_count": 0,
    }
    if any(result.get(key) != wanted for key, wanted in expected.items()):
        raise _fail("formal block result contract invalid")
    checkpoint = result.get("checkpoint")
    if not isinstance(checkpoint, Mapping):
        raise _fail("formal block checkpoint missing")
    verified_checkpoint = _sealed(
        checkpoint, label="formal block checkpoint", field="checkpoint_sha256"
    )
    if (
        verified_checkpoint.get("terminal_status") != "COMPLETED"
        or verified_checkpoint.get("complete_coverage") is not True
        or verified_checkpoint.get("completed_source_prefix") != block["source_count"] - 1
    ):
        raise _fail("formal block checkpoint incomplete")
    return result


def _persist_returned_result(path: Path, result: Mapping[str, Any], *, label: str) -> None:
    if path.is_file():
        existing = _read_sealed(path, label=label)
        if existing != result:
            raise _fail(f"{label} conflicts with executor result")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, result)


def _failure(
    *,
    manifest: Mapping[str, Any],
    stage: str,
    block_index: int | None,
    error: BaseException,
    completed_hashes: list[str],
) -> dict[str, Any]:
    return _seal(
        {
            "schema_version": FAILURE_SCHEMA,
            "status": "FAILED_NON_REUSABLE",
            "attempt_id": manifest["attempt_id"],
            "manifest_payload_sha256": manifest["payload_sha256"],
            "failure_stage": stage,
            "block_index": block_index,
            "error_class": f"{type(error).__module__}.{type(error).__qualname__}",
            "completed_block_indices": list(range(len(completed_hashes))),
            "completed_block_result_payload_sha256s": list(completed_hashes),
        }
    )


def _restore(
    root: Path,
    *,
    expected_manifest: Mapping[str, Any],
    plan: Mapping[str, Any],
    smoke: SmokeSpec,
) -> tuple[str | None, list[str], int, dict[str, Any] | None]:
    manifest = _read_sealed(root / "ORCHESTRATION_MANIFEST.json", label="orchestration manifest")
    if manifest != expected_manifest:
        raise _fail("orchestration manifest conflict")
    checkpoint = _read_sealed(
        root / "ORCHESTRATION_CHECKPOINT.json", label="orchestration checkpoint"
    )
    if checkpoint.get("manifest_payload_sha256") != manifest["payload_sha256"]:
        raise _fail("orchestration checkpoint binding invalid")
    status = checkpoint.get("status")
    if status == "FAILED_NON_REUSABLE" or (root / "FAILURE.json").exists():
        raise _fail("attempt terminal")
    if status == "COMPLETED":
        result = _read_sealed(root / "ORCHESTRATION_RESULT.json", label="orchestration result")
        return checkpoint.get("smoke_result_payload_sha256"), list(
            checkpoint.get("completed_block_result_payload_sha256s", [])
        ), 6, result
    smoke_sha = checkpoint.get("smoke_result_payload_sha256")
    if smoke_sha is not None:
        smoke_result = _verify_smoke(
            _read_sealed(root / "smoke/result.json", label="smoke result"), spec=smoke
        )
        gate = _read_sealed(root / "SMOKE_GATE.json", label="smoke gate")
        if gate.get("smoke_result_payload_sha256") != smoke_result["payload_sha256"] or smoke_sha != smoke_result["payload_sha256"]:
            raise _fail("smoke checkpoint binding invalid")
    completed = checkpoint.get("completed_block_result_payload_sha256s")
    next_index = checkpoint.get("next_block_index")
    if (
        not isinstance(completed, list)
        or any(not isinstance(value, str) or _SHA256.fullmatch(value) is None for value in completed)
        or isinstance(next_index, bool)
        or not isinstance(next_index, int)
        or next_index != len(completed)
        or not 0 <= next_index <= 6
    ):
        raise _fail("orchestration completed-prefix invalid")
    for index, expected_sha in enumerate(completed):
        result = _verify_block(
            _read_sealed(
                root / "blocks" / f"block-{index:02d}" / "result.json",
                label=f"block {index} result",
            ),
            plan=plan,
            index=index,
        )
        if result["payload_sha256"] != expected_sha:
            raise _fail("orchestration completed-prefix hash mismatch")
    return smoke_sha, list(completed), next_index, None


def run_v31_orchestration(
    *,
    control_root: Path,
    attempt_root: Path,
    attempt_id: str,
    hooks: OrchestrationHooks,
    formal_block_limit: int = 6,
) -> dict[str, Any]:
    """Run or safely resume at a sealed between-block checkpoint."""

    if not isinstance(attempt_id, str) or _IDENTITY.fullmatch(attempt_id) is None:
        raise _fail("attempt id invalid")
    if not isinstance(hooks, OrchestrationHooks):
        raise _fail("orchestration hooks invalid")
    if isinstance(formal_block_limit, bool) or formal_block_limit not in {0, 4, 6}:
        raise _fail("formal block limit invalid")
    plan, merge_authorized, commit_sha256 = _control(control_root)
    if formal_block_limit == 6 and not merge_authorized:
        raise _fail("baseline merge authority required for ablation blocks")
    smoke = _smoke_spec(
        attempt_id=attempt_id,
        plan=plan,
        commit_sha256=commit_sha256,
    )
    manifest = _manifest(
        attempt_id=attempt_id,
        plan=plan,
        hooks=hooks,
        smoke=smoke,
    )
    root = Path(attempt_root)
    if root.exists():
        smoke_sha, completed_hashes, next_index, terminal = _restore(
            root, expected_manifest=manifest, plan=plan, smoke=smoke
        )
        if terminal is not None:
            return terminal
    else:
        root.mkdir(parents=True, exist_ok=False)
        atomic_write_json(root / "ORCHESTRATION_MANIFEST.json", manifest)
        smoke_sha = None
        completed_hashes = []
        next_index = 0
        atomic_write_json(
            root / "ORCHESTRATION_CHECKPOINT.json",
            _checkpoint(
                manifest_sha256=manifest["payload_sha256"],
                status="SMOKE_PENDING",
                smoke_result_sha256=None,
                completed_hashes=[],
                next_block_index=0,
            ),
        )

    if formal_block_limit == 0 and smoke_sha is not None:
        return _read_sealed(
            root / "SMOKE_ONLY_RESULT.json", label="smoke-only result"
        )

    if next_index >= 4 and formal_block_limit == 4:
        return _read_sealed(
            root / "MAIN_METHOD_RESULT.json", label="main method result"
        )

    if smoke_sha is None:
        smoke_root = root / "smoke"
        try:
            smoke_root.mkdir(parents=True, exist_ok=False)
            smoke_result = _verify_smoke(hooks.run_smoke(smoke, smoke_root), spec=smoke)
            _persist_returned_result(
                smoke_root / "result.json", smoke_result, label="smoke result"
            )
            gate = _seal(
                {
                    "schema_version": SMOKE_GATE_SCHEMA,
                    "status": "PASS",
                    "attempt_id": attempt_id,
                    "manifest_payload_sha256": manifest["payload_sha256"],
                    "plan_payload_sha256": plan["payload_sha256"],
                    "smoke_result_payload_sha256": smoke_result["payload_sha256"],
                    "formal_blocks_authorized": True,
                }
            )
            atomic_write_json(root / "SMOKE_GATE.json", gate)
            smoke_sha = smoke_result["payload_sha256"]
            atomic_write_json(
                root / "ORCHESTRATION_CHECKPOINT.json",
                _checkpoint(
                    manifest_sha256=manifest["payload_sha256"],
                    status="BLOCKS_RUNNING",
                    smoke_result_sha256=smoke_sha,
                    completed_hashes=completed_hashes,
                    next_block_index=next_index,
                ),
            )
        except Exception as error:
            failure = _failure(
                manifest=manifest,
                stage="SMOKE",
                block_index=None,
                error=error,
                completed_hashes=completed_hashes,
            )
            atomic_write_json(root / "FAILURE.json", failure)
            atomic_write_json(
                root / "ORCHESTRATION_CHECKPOINT.json",
                _checkpoint(
                    manifest_sha256=manifest["payload_sha256"],
                    status="FAILED_NON_REUSABLE",
                    smoke_result_sha256=None,
                    completed_hashes=completed_hashes,
                    next_block_index=next_index,
                ),
            )
            raise _fail("smoke execution failed") from None

    if formal_block_limit == 0:
        atomic_write_json(
            root / "ORCHESTRATION_CHECKPOINT.json",
            _checkpoint(
                manifest_sha256=manifest["payload_sha256"],
                status="SMOKE_COMPLETED_PROBE_REQUIRED",
                smoke_result_sha256=smoke_sha,
                completed_hashes=completed_hashes,
                next_block_index=next_index,
            ),
        )
        smoke_only_result = _seal(
            {
                "schema_version": SMOKE_ONLY_RESULT_SCHEMA,
                "status": "SMOKE_PASS_PROBE_REQUIRED",
                "attempt_id": attempt_id,
                "manifest_payload_sha256": manifest["payload_sha256"],
                "smoke_result_payload_sha256": smoke_sha,
                "completed_block_indices": [],
                "next_stage": "BOUNDED_AUTORESEARCH_PROBE",
                "heldout_data_accessed": False,
            }
        )
        atomic_write_json(root / "SMOKE_ONLY_RESULT.json", smoke_only_result)
        return smoke_only_result

    (root / "blocks").mkdir(exist_ok=True)
    for index in range(next_index, formal_block_limit):
        block_root = root / "blocks" / f"block-{index:02d}"
        if block_root.exists():
            error = _fail("partial formal block requires new attempt")
            failure = _failure(
                manifest=manifest,
                stage="FORMAL_BLOCK",
                block_index=index,
                error=error,
                completed_hashes=completed_hashes,
            )
            atomic_write_json(root / "FAILURE.json", failure)
            atomic_write_json(
                root / "ORCHESTRATION_CHECKPOINT.json",
                _checkpoint(
                    manifest_sha256=manifest["payload_sha256"],
                    status="FAILED_NON_REUSABLE",
                    smoke_result_sha256=smoke_sha,
                    completed_hashes=completed_hashes,
                    next_block_index=index,
                ),
            )
            raise error
        try:
            result = _verify_block(hooks.run_block(plan, index, block_root), plan=plan, index=index)
            _persist_returned_result(
                block_root / "result.json", result, label=f"block {index} result"
            )
        except Exception as error:
            failure = _failure(
                manifest=manifest,
                stage="FORMAL_BLOCK",
                block_index=index,
                error=error,
                completed_hashes=completed_hashes,
            )
            atomic_write_json(root / "FAILURE.json", failure)
            atomic_write_json(
                root / "ORCHESTRATION_CHECKPOINT.json",
                _checkpoint(
                    manifest_sha256=manifest["payload_sha256"],
                    status="FAILED_NON_REUSABLE",
                    smoke_result_sha256=smoke_sha,
                    completed_hashes=completed_hashes,
                    next_block_index=index,
                ),
            )
            raise _fail("block execution failed") from None
        completed_hashes.append(result["payload_sha256"])
        next_index = index + 1
        atomic_write_json(
            root / "ORCHESTRATION_CHECKPOINT.json",
            _checkpoint(
                manifest_sha256=manifest["payload_sha256"],
                status=(
                    "COMPLETED"
                    if next_index == 6
                    else "MAIN_METHOD_COMPLETED_BASELINE_RESUME_REQUIRED"
                    if next_index == 4 and formal_block_limit == 4
                    else "BLOCKS_RUNNING"
                ),
                smoke_result_sha256=smoke_sha,
                completed_hashes=completed_hashes,
                next_block_index=next_index,
            ),
        )
    if formal_block_limit == 4:
        main_result = _seal(
            {
                "schema_version": MAIN_METHOD_RESULT_SCHEMA,
                "status": "MAIN_METHOD_PASS_BASELINE_RESUME_REQUIRED",
                "attempt_id": attempt_id,
                "manifest_payload_sha256": manifest["payload_sha256"],
                "smoke_result_payload_sha256": smoke_sha,
                "completed_block_indices": list(range(4)),
                "completed_block_result_payload_sha256s": completed_hashes,
                "next_stage": "RESUME_EXACT_APC_BASELINE_PROCESS",
                "heldout_data_accessed": False,
            }
        )
        atomic_write_json(root / "MAIN_METHOD_RESULT.json", main_result)
        return main_result

    terminal_result = _seal(
        {
            "schema_version": RESULT_SCHEMA,
            "status": "PASS",
            "attempt_id": attempt_id,
            "manifest_payload_sha256": manifest["payload_sha256"],
            "smoke_result_payload_sha256": smoke_sha,
            "completed_block_indices": list(range(6)),
            "completed_block_result_payload_sha256s": completed_hashes,
            "next_stage": "QUALITY_V1_MEMBIND_EXTENSION",
            "heldout_data_accessed": False,
        }
    )
    atomic_write_json(root / "ORCHESTRATION_RESULT.json", terminal_result)
    return terminal_result


def load_executor_hooks(entrypoint: str) -> OrchestrationHooks:
    """Load one explicitly named hook factory; there is no implicit live default."""

    if not isinstance(entrypoint, str) or _ENTRYPOINT.fullmatch(entrypoint) is None:
        raise _fail("executor factory entrypoint invalid")
    module_name, attribute = entrypoint.split(":", 1)
    try:
        factory = getattr(importlib.import_module(module_name), attribute)
        hooks = factory()
    except Exception:
        raise _fail("executor factory load failed") from None
    if not isinstance(hooks, OrchestrationHooks):
        raise _fail("executor factory result invalid")
    return hooks


__all__ = [
    "OrchestrationError",
    "OrchestrationHooks",
    "SmokeSpec",
    "load_executor_hooks",
    "run_v31_orchestration",
]
