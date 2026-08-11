"""Seal one interrupted C2 run and revoke its consumed live grant.

The transition is deliberately evidence-bound and offline. It does not contact
model services or the database; cleanup is a later, separately gated action.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RUN_ID = "c2-2fe3711c62933407"
GRAPH_NAMESPACE = "nc-e1e2-400b9b78c2c218df"
ERROR_CODE = "openai.APIConnectionError"
METADATA_KEY = "native_characterization_c2_interruption"
REPORT_RELATIVE_PATH = (
    f"artifacts/diagnostics/native_characterization_{RUN_ID}_interruption.json"
)
_RUN_ID_RE = re.compile(r"c2-[0-9a-f]{16}")
_SHA_RE = re.compile(r"[0-9a-f]{64}")


class C2InterruptionError(RuntimeError):
    """Sanitized fail-closed interruption transition error."""


def _fail(reason: str) -> C2InterruptionError:
    return C2InterruptionError(f"C2 interruption finalization denied: {reason}")


@dataclass(frozen=True)
class ArtifactBinding:
    path: str
    sha256: str
    line_count: int


@dataclass(frozen=True)
class C2InterruptionBindings:
    run_id: str
    source_state_sha256: str
    checkpoint_path: str
    checkpoint_sha256: str
    block_checkpoint_path: str
    block_checkpoint_sha256: str
    artifacts: tuple[ArtifactBinding, ...]
    outer_log_path: str
    outer_log_sha256: str
    freeze_path: str
    freeze_sha256: str
    workplan_path: str
    workplan_sha256: str
    report_path: str
    graph_namespace: str


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, UnicodeError, ValueError):
        raise _fail("value_not_canonicalizable") from None


def _sha(encoded: bytes) -> str:
    return hashlib.sha256(encoded).hexdigest()


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise _fail(f"{label}_invalid")
    return value


def _json_object(encoded: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(encoded.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):
        raise _fail(f"{label}_invalid_json") from None
    if not isinstance(value, dict):
        raise _fail(f"{label}_not_object")
    return value


def _validate_payload(value: Mapping[str, Any], label: str) -> None:
    expected = value.get("payload_sha256")
    _require_digest(expected, f"{label}_payload_sha256")
    unhashed = dict(value)
    unhashed.pop("payload_sha256", None)
    if _sha(_canonical(unhashed)) != expected:
        raise _fail(f"{label}_payload_hash_mismatch")


def _read_bound(root: Path, relative: str, digest: str, label: str) -> bytes:
    _require_digest(digest, f"{label}_sha256")
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise _fail(f"{label}_path_invalid")
    cursor = root
    for part in path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _fail(f"{label}_path_symlink_forbidden")
    try:
        encoded = cursor.read_bytes()
    except OSError:
        raise _fail(f"{label}_unreadable") from None
    if _sha(encoded) != digest:
        raise _fail(f"{label}_hash_mismatch")
    return encoded


def _validate_source(source: Mapping[str, Any], bindings: C2InterruptionBindings) -> None:
    if _sha(_canonical(source)) != bindings.source_state_sha256:
        raise _fail("source_state_hash_mismatch")
    progress = source.get("stage_progress")
    alignment = source.get("native_characterization_reference_alignment")
    fresh = alignment.get("fresh_c2") if isinstance(alignment, Mapping) else None
    receipt = source.get("native_characterization_reference_c2_authorization")
    exact = (
        source.get("protocol_version") == "current-validation-v1.3"
        and source.get("current_stage") == "NATIVE_CHARACTERIZATION"
        and source.get("status") == "native_characterization_c2_live_only"
        and source.get("current_blocker") is None
        and source.get("current_action_scope")
        == "native_characterization_c2_live_only"
        and source.get("authorized_live_actions")
        == ["native_characterization_c2"]
        and source.get("native_characterization_live_authorized") is True
        and source.get("live_h0_candidate_authorized") is False
        and source.get("service_admin_authorized") is False
        and source.get("next_allowed_action") == "run_native_characterization_c2"
        and isinstance(progress, Mapping)
        and progress.get("native_characterization")
        == "c0_c1_pass_reference_aligned_c2_authorized_from_episode_0"
        and isinstance(alignment, Mapping)
        and alignment.get("status") == "c2_live_authorized"
        and alignment.get("reference_freeze_path") == bindings.freeze_path
        and alignment.get("reference_freeze_sha256") == bindings.freeze_sha256
        and isinstance(fresh, Mapping)
        and fresh.get("live_authorized") is True
        and fresh.get("semantic_attempts_remaining") == 1
        and fresh.get("resume_allowed") is False
        and fresh.get("prefix_merge_allowed") is False
        and fresh.get("start_source_sequence") == 0
        and isinstance(receipt, Mapping)
        and receipt.get("live_authorized") is True
        and receipt.get("replacement_resume_allowed") is False
        and receipt.get("replacement_start_source_sequence") == 0
        and receipt.get("semantic_attempts_authorized") == 1
        and METADATA_KEY not in source
    )
    if not exact:
        raise _fail("source_state_not_exact_c2_live_grant")


def _expected_history() -> list[dict[str, Any]]:
    return [
        {
            "block_index": 0,
            "episode_id": f"07741c45:{index}",
            "event_type": "episode_completed",
            "history_id": "07741c45",
            "source_sequence": index,
            "status": "completed",
        }
        for index in range(9)
    ]


def _validate_checkpoint(
    encoded: bytes, *, bindings: C2InterruptionBindings, block: bool
) -> dict[str, Any]:
    label = "block_checkpoint" if block else "checkpoint"
    value = _json_object(encoded, label)
    _validate_payload(value, label)
    expected_status = "episode_completed" if block else "error"
    expected_error = None if block else ERROR_CODE
    exact = (
        value.get("schema_version")
        == "membind.native-characterization-c2-checkpoint.v1"
        and value.get("run_id") == bindings.run_id
        and value.get("stage") == "C2"
        and value.get("status") == expected_status
        and value.get("error_code") == expected_error
        and value.get("planned_block_indices") == [0, 1, 2, 3]
        and value.get("completed_block_indices") == []
        and value.get("completed_episode_ids")
        == [f"07741c45:{index}" for index in range(9)]
        and value.get("checkpoint_history") == _expected_history()
    )
    if not exact:
        raise _fail(f"{label}_contract_mismatch")
    return value


def _validate_jsonl_artifacts(
    validation_root: Path, bindings: C2InterruptionBindings
) -> list[dict[str, Any]]:
    expected_names = {"events", "spans", "llm", "embedding", "db", "errors", "trace"}
    observed_names: set[str] = set()
    inventory: list[dict[str, Any]] = []
    for binding in bindings.artifacts:
        if not isinstance(binding, ArtifactBinding) or binding.line_count != 10:
            raise _fail("artifact_binding_invalid")
        stem = Path(binding.path).stem
        name = "trace" if stem == "trace" else stem
        if name not in expected_names or name in observed_names:
            raise _fail("artifact_inventory_mismatch")
        observed_names.add(name)
        encoded = _read_bound(
            validation_root, binding.path, binding.sha256, f"artifact_{name}"
        )
        lines = encoded.splitlines()
        if len(lines) != binding.line_count:
            raise _fail(f"artifact_{name}_line_count_mismatch")
        values = [_json_object(line, f"artifact_{name}_line") for line in lines]
        for index, value in enumerate(values):
            if (
                value.get("run_id") != bindings.run_id
                or value.get("episode_id") != f"07741c45:{index}"
                or value.get("source_sequence") != index
            ):
                raise _fail(f"artifact_{name}_trajectory_mismatch")
        if name == "errors":
            if any(values[index].get("spans") for index in range(9)):
                raise _fail("errors_completed_prefix_not_clean")
            failure_spans = values[9].get("spans")
            if not isinstance(failure_spans, list) or not any(
                isinstance(span, Mapping)
                and span.get("status") == "error"
                and span.get("error_code") == ERROR_CODE
                for span in failure_spans
            ):
                raise _fail("errors_failure_envelope_missing")
        inventory.append(
            {
                "path": binding.path,
                "sha256": binding.sha256,
                "line_count": binding.line_count,
            }
        )
    if observed_names != expected_names:
        raise _fail("artifact_inventory_mismatch")
    return sorted(inventory, key=lambda item: item["path"])


def _validate_outer_log(encoded: bytes) -> None:
    try:
        text = encoded.decode("utf-8")
    except UnicodeError:
        raise _fail("outer_log_not_utf8") from None
    lowered = text.casefold()
    if "bearer " in lowered or "authorization:" in lowered:
        raise _fail("outer_log_unsafe")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or lines[-2:] != [
        "Error in generating LLM response: Connection error.",
        '{"error_code":"openai.APIConnectionError","status":"error"}',
    ]:
        raise _fail("outer_log_terminal_error_mismatch")


def _validate_bindings(bindings: C2InterruptionBindings) -> None:
    if not isinstance(bindings, C2InterruptionBindings):
        raise _fail("bindings_invalid")
    if bindings.run_id != RUN_ID or _RUN_ID_RE.fullmatch(bindings.run_id) is None:
        raise _fail("run_id_invalid")
    if bindings.graph_namespace != GRAPH_NAMESPACE:
        raise _fail("graph_namespace_invalid")
    for name in (
        "source_state_sha256",
        "checkpoint_sha256",
        "block_checkpoint_sha256",
        "outer_log_sha256",
        "freeze_sha256",
        "workplan_sha256",
    ):
        _require_digest(getattr(bindings, name), name)
    if bindings.report_path != REPORT_RELATIVE_PATH:
        raise _fail("report_path_invalid")


def build_interrupted_state_and_report(
    source_state: Mapping[str, Any],
    *,
    validation_root: Path,
    repo_root: Path,
    bindings: C2InterruptionBindings,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate immutable evidence and derive cleanup-only state plus report."""

    _validate_bindings(bindings)
    source = deepcopy(dict(source_state))
    _validate_source(source, bindings)
    checkpoint_encoded = _read_bound(
        validation_root,
        bindings.checkpoint_path,
        bindings.checkpoint_sha256,
        "checkpoint",
    )
    checkpoint = _validate_checkpoint(
        checkpoint_encoded, bindings=bindings, block=False
    )
    block_encoded = _read_bound(
        validation_root,
        bindings.block_checkpoint_path,
        bindings.block_checkpoint_sha256,
        "block_checkpoint",
    )
    _validate_checkpoint(block_encoded, bindings=bindings, block=True)
    inventory = _validate_jsonl_artifacts(validation_root, bindings)
    outer_log = _read_bound(
        validation_root,
        bindings.outer_log_path,
        bindings.outer_log_sha256,
        "outer_log",
    )
    _validate_outer_log(outer_log)
    _read_bound(validation_root, bindings.freeze_path, bindings.freeze_sha256, "freeze")
    _read_bound(repo_root, bindings.workplan_path, bindings.workplan_sha256, "workplan")

    report: dict[str, Any] = {
        "schema_version": "membind.native-characterization-c2-interruption.v1",
        "classification": "infrastructure_interruption",
        "run_id": bindings.run_id,
        "status": "infrastructure_interrupted_invalid_non_mergeable",
        "error_code": ERROR_CODE,
        "completed_episode_count": len(checkpoint["completed_episode_ids"]),
        "failed_source_sequence": 9,
        "completed_block_count": 0,
        "attempt_valid": False,
        "attempt_mergeable": False,
        "resume_allowed": False,
        "prefix_merge_allowed": False,
        "semantic_attempt_consumed": False,
        "semantic_attempts_remaining": 1,
        "failure_envelope_persisted": True,
        "outer_exit_status": "unavailable",
        "checkpoint": {
            "path": bindings.checkpoint_path,
            "sha256": bindings.checkpoint_sha256,
        },
        "block_checkpoint": {
            "path": bindings.block_checkpoint_path,
            "sha256": bindings.block_checkpoint_sha256,
        },
        "run_artifacts": inventory,
        "outer_log": {
            "path": bindings.outer_log_path,
            "sha256": bindings.outer_log_sha256,
        },
        "frozen_inputs": {
            "freeze_path": bindings.freeze_path,
            "freeze_sha256": bindings.freeze_sha256,
            "workplan_path": bindings.workplan_path,
            "workplan_sha256": bindings.workplan_sha256,
        },
        "cleanup": {
            "authorized": True,
            "target_group_id": bindings.graph_namespace,
            "planned_evidence_path": (
                f"artifacts/native_characterization/c2_cleanup/{bindings.run_id}.json"
            ),
            "required_post_node_count": 0,
            "required_post_relationship_count": 0,
        },
        "decision_boundary": {
            "structured_correctness_failure": False,
            "automatic_resume_allowed": False,
            "prefix_merge_allowed": False,
            "next_action": "execute_exact_block_zero_cleanup",
        },
        "secrets_persisted": False,
        "source_state_sha256": bindings.source_state_sha256,
    }
    report["payload_sha256"] = _sha(_canonical(report))
    report_file_sha256 = _sha(_canonical(report) + b"\n")

    target = deepcopy(source)
    target.update(
        {
            "status": "native_characterization_cleanup_only",
            "current_blocker": "c2_infrastructure_interruption_cleanup_pending",
            "current_action_scope": "native_characterization_c2_cleanup_only",
            "authorized_live_actions": [],
            "native_characterization_live_authorized": False,
            "next_allowed_action": (
                "execute_scoped_c2_cleanup_after_infrastructure_interruption"
            ),
        }
    )
    progress = deepcopy(dict(target["stage_progress"]))
    progress["native_characterization"] = (
        "c0_c1_pass_reference_c2_infrastructure_interrupted_cleanup_pending"
    )
    target["stage_progress"] = progress
    alignment = deepcopy(dict(target["native_characterization_reference_alignment"]))
    alignment["status"] = "c2_infrastructure_interrupted_cleanup_pending"
    fresh = deepcopy(dict(alignment["fresh_c2"]))
    fresh["live_authorized"] = False
    alignment["fresh_c2"] = fresh
    alignment["cleanup"] = {
        "operator_authorized": True,
        "execution_status": "pending",
        "failed_attempt_id": bindings.run_id,
        "failed_attempt_valid": False,
        "failed_attempt_mergeable": False,
        "replacement_resume_allowed": False,
        "target_group_id": bindings.graph_namespace,
        "source_freeze_path": bindings.freeze_path,
        "source_freeze_sha256": bindings.freeze_sha256,
        "planned_evidence_path": (
            f"artifacts/native_characterization/c2_cleanup/{bindings.run_id}.json"
        ),
        "required_post_node_count": 0,
        "required_post_relationship_count": 0,
    }
    target["native_characterization_reference_alignment"] = alignment
    receipt = deepcopy(dict(target["native_characterization_reference_c2_authorization"]))
    receipt["live_authorized"] = False
    receipt["consumed_by_run_id"] = bindings.run_id
    target["native_characterization_reference_c2_authorization"] = receipt
    target[METADATA_KEY] = {
        "schema_version": "membind.native-characterization-c2-interruption-state.v1",
        "source_state_sha256": bindings.source_state_sha256,
        "run_id": bindings.run_id,
        "error_code": ERROR_CODE,
        "completed_episode_count": 9,
        "failed_source_sequence": 9,
        "attempt_valid": False,
        "attempt_mergeable": False,
        "resume_allowed": False,
        "prefix_merge_allowed": False,
        "semantic_attempt_consumed": False,
        "semantic_attempts_remaining": 1,
        "live_authorized": False,
        "cleanup_authorized": True,
        "report_path": bindings.report_path,
        "report_payload_sha256": report["payload_sha256"],
        "report_sha256": report_file_sha256,
        "checkpoint_sha256": bindings.checkpoint_sha256,
        "outer_log_sha256": bindings.outer_log_sha256,
    }
    return target, report


def _atomic_write(path: Path, encoded: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise _fail("atomic_write_failed") from None


@contextmanager
def _locked(state_path: Path):
    lock_path = state_path.with_name(".native-characterization-c2-interruption.lock")
    try:
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
    except OSError:
        raise _fail("lock_failed") from None


def _already_applied(
    state: Mapping[str, Any], report_path: Path, bindings: C2InterruptionBindings
) -> bool:
    metadata = state.get(METADATA_KEY)
    if not isinstance(metadata, Mapping):
        return False
    exact = (
        state.get("authorized_live_actions") == []
        and state.get("native_characterization_live_authorized") is False
        and state.get("status") == "native_characterization_cleanup_only"
        and metadata.get("run_id") == bindings.run_id
        and metadata.get("attempt_valid") is False
        and metadata.get("attempt_mergeable") is False
        and metadata.get("resume_allowed") is False
        and metadata.get("live_authorized") is False
        and metadata.get("cleanup_authorized") is True
    )
    if not exact or not report_path.is_file():
        raise _fail("partial_or_drifted_target_state")
    encoded = report_path.read_bytes()
    report = _json_object(encoded, "report")
    _validate_payload(report, "report")
    if (
        _sha(encoded) != metadata.get("report_sha256")
        or report.get("payload_sha256") != metadata.get("report_payload_sha256")
    ):
        raise _fail("report_binding_mismatch")
    return True


def finalize_c2_interruption(
    *,
    state_path: Path,
    validation_root: Path,
    repo_root: Path,
    bindings: C2InterruptionBindings,
    apply: bool = False,
) -> dict[str, Any]:
    """Dry-run or atomically persist the report and fail-closed state."""

    _validate_bindings(bindings)
    if not isinstance(apply, bool):
        raise _fail("apply_not_boolean")
    report_path = validation_root / bindings.report_path
    with _locked(state_path):
        try:
            source = _json_object(state_path.read_bytes(), "state")
        except OSError:
            raise _fail("state_unreadable") from None
        if _already_applied(source, report_path, bindings):
            return {
                "status": "already_applied",
                "run_id": bindings.run_id,
                "target_state_sha256": _sha(_canonical(source)),
            }
        target, report = build_interrupted_state_and_report(
            source,
            validation_root=validation_root,
            repo_root=repo_root,
            bindings=bindings,
        )
        report_encoded = _canonical(report) + b"\n"
        target_encoded = _canonical(target) + b"\n"
        result = {
            "status": "validated_not_applied",
            "run_id": bindings.run_id,
            "target_state_sha256": _sha(_canonical(target)),
            "report_sha256": _sha(report_encoded),
            "report_payload_sha256": report["payload_sha256"],
        }
        if not apply:
            return result
        if report_path.exists() and report_path.read_bytes() != report_encoded:
            raise _fail("report_already_exists_with_different_content")
        _atomic_write(report_path, report_encoded)
        _atomic_write(state_path, target_encoded)
        result["status"] = "applied"
        return result


def _default_bindings() -> C2InterruptionBindings:
    run_root = f"artifacts/native_characterization/runs/{RUN_ID}"
    return C2InterruptionBindings(
        run_id=RUN_ID,
        source_state_sha256=(
            "a5189f4e70fd96328de920fac6d42a8504975cc1fe7aeeba7fcb2f3ccd29d292"
        ),
        checkpoint_path=f"{run_root}/checkpoint.json",
        checkpoint_sha256=(
            "2010f6eecf82d1cab8706cd5136445c08175b3ddf9e1e1d11b8ec5f16a3735b8"
        ),
        block_checkpoint_path=f"{run_root}/blocks/000_07741c45/checkpoint.json",
        block_checkpoint_sha256=(
            "5129e802c03a2b3aa4ec30cae7e1350c7856c64c8e0375a5e528213820dd1b65"
        ),
        artifacts=(
            ArtifactBinding(f"{run_root}/db.jsonl", "981ed8e303f4c8005c4917b5b74312e7e3a942618364b3791b9a1cde46611905", 10),
            ArtifactBinding(f"{run_root}/embedding.jsonl", "cb7e49f028befc5aa3a0aa4f34d7dca7dec57bb63ca17477e3e6e9d36a8c0a62", 10),
            ArtifactBinding(f"{run_root}/errors.jsonl", "43ace349eda5c25d8fc29a57e26e4e853e13e4c59aad721f95ce7c7336411f37", 10),
            ArtifactBinding(f"{run_root}/events.jsonl", "cfe414969654c28451e8e5c59e376ec610d8a4db750440d9e64ad34dd71251d0", 10),
            ArtifactBinding(f"{run_root}/llm.jsonl", "cc3d8732908d616c45eee90bf1263d24b69d5a36a04eb2d737d3a38deb4333a9", 10),
            ArtifactBinding(f"{run_root}/spans.jsonl", "1f4582c93561656835547c66e234fd85e90ee3e56164316d491fa3e4a6698ddd", 10),
            ArtifactBinding(f"{run_root}/blocks/000_07741c45/trace.jsonl", "1d7a18902e6eb39da370ec3c641df223fb27213b13d30d882291a0f910daa988", 10),
        ),
        outer_log_path=(
            "artifacts/tdd/native_characterization_c2-2fe3711c62933407_live_20260811.log"
        ),
        outer_log_sha256=(
            "3a453f968c6cb5b30a3ae198ac4ec79a569f8993d5a2b5e2e9ab5c32f6f646e1"
        ),
        freeze_path="artifacts/native_characterization/freeze_reference_aligned.json",
        freeze_sha256=(
            "cea700f73f7dc942deeb49195e0a3ca235c35ec51a1c06fdab0edd94738330a7"
        ),
        workplan_path="MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md",
        workplan_sha256=(
            "be3112cc2da4080ce98f9c94f1ab510ba5cc8350dca108a15e304da04c996b5b"
        ),
        report_path=REPORT_RELATIVE_PATH,
        graph_namespace=GRAPH_NAMESPACE,
    )


def main() -> int:
    validation = Path(__file__).resolve().parents[1]
    repo = validation.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--state", type=Path, default=validation / "CURRENT_STATE.json")
    args = parser.parse_args()
    try:
        result = finalize_c2_interruption(
            state_path=args.state,
            validation_root=validation,
            repo_root=repo,
            bindings=_default_bindings(),
            apply=args.apply,
        )
    except C2InterruptionError as exc:
        print(json.dumps({"status": "denied", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
