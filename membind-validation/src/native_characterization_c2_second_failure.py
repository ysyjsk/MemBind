"""Finalize the repeated C2 JSON decode failure without any live action.

This is a one-event evidence transition, not a retry or recovery framework. It
binds sanitized immutable files, revokes the consumed C2 grant, and leaves the
structured-output choice unresolved for an explicit protocol decision.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


RUN_ID = "c2-723261287e32e182"
PRIOR_RUN_ID = "c2-efb58c477f12adf6"
SOURCE_STATE_SHA256 = "c6aae8cfeda8f2eeec74b218455a7b2a1dcfc89099bab0a69367ab50c79e5671"
CHECKPOINT_RELATIVE_PATH = (
    f"artifacts/native_characterization/runs/{RUN_ID}/checkpoint.json"
)
CHECKPOINT_SHA256 = "a0106363a3e226669fcf199b30f3de219766ede8e204e788c4f27f6ba8c4ae09"
TRACE_RELATIVE_PATH = (
    f"artifacts/native_characterization/runs/{RUN_ID}/blocks/"
    "000_07741c45/trace.jsonl"
)
TRACE_SHA256 = "de9f5091a1163ba2db9f2b1bc0e734bbb67d14a9bc002fe9e72c262ce5be9c83"
OUTER_LOG_RELATIVE_PATH = (
    f"artifacts/native_characterization/live_logs/{RUN_ID}.log"
)
OUTER_LOG_SHA256 = "1dba546986e034b3d7fdfb05f1831c48378efa148a87524f97e04b7048433cae"
PRIOR_CHECKPOINT_RELATIVE_PATH = (
    f"artifacts/native_characterization/runs/{PRIOR_RUN_ID}/checkpoint.json"
)
PRIOR_CHECKPOINT_SHA256 = (
    "912fc162e65339737c2cbb7a35622c37958b73e261e9062be5a32b48410df7f0"
)
FREEZE_RELATIVE_PATH = "artifacts/native_characterization/freeze.json"
FREEZE_SHA256 = "3bca97e1f531dbd23584dd02248a0cbed783f2153f3c756880826ea0c48e001c"
WORKPLAN_RELATIVE_PATH = "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"
WORKPLAN_SHA256 = "be3112cc2da4080ce98f9c94f1ab510ba5cc8350dca108a15e304da04c996b5b"
HISTORICAL_PROBE_RELATIVE_PATH = (
    "artifacts/environment/v3_actual_schema_compatibility_probe_20260809_005_fresh_restart.json"
)
HISTORICAL_PROBE_SHA256 = (
    "fd1b23026689008ce9a5976581b519c2a7d62fc5c2ea05eb0964f5387e10a041"
)
DEFAULT_REPORT_RELATIVE_PATH = (
    "artifacts/diagnostics/"
    "native_characterization_c2_second_structured_failure_20260811.json"
)
METADATA_KEY = "native_characterization_c2_second_failure"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_ERROR = '{"error_code":"json.decoder.JSONDecodeError","status":"error"}'
_FORBIDDEN_LOG_FRAGMENTS = (
    "api_key",
    "api-key",
    "authorization:",
    "bearer ",
    "password",
    ".env",
)


class C2SecondFailureError(RuntimeError):
    """Sanitized fail-closed evidence transition error."""


@dataclass(frozen=True)
class C2SecondFailureBindings:
    source_state_sha256: str
    checkpoint_path: str
    checkpoint_sha256: str
    trace_path: str
    trace_sha256: str
    outer_log_path: str
    outer_log_sha256: str
    prior_checkpoint_path: str
    prior_checkpoint_sha256: str
    freeze_sha256: str
    workplan_sha256: str


def _fail(reason: str) -> C2SecondFailureError:
    return C2SecondFailureError(
        f"native characterization C2 second failure finalization denied: {reason}"
    )


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
    except (TypeError, UnicodeError, ValueError):
        raise _fail("value_not_canonicalizable") from None


def _sha(encoded: bytes) -> str:
    return hashlib.sha256(encoded).hexdigest()


def _require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _fail(f"{label}_invalid")
    return value


def _resolve_file(root: Path, relative: str, label: str) -> Path:
    value = Path(relative)
    if value.is_absolute() or any(part in {"", ".", ".."} for part in value.parts):
        raise _fail(f"{label}_path_invalid")
    path = root / value
    try:
        path.relative_to(root)
    except ValueError:
        raise _fail(f"{label}_path_escape") from None
    cursor = root
    for part in value.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _fail(f"{label}_symlink")
    if not path.is_file():
        raise _fail(f"{label}_missing")
    return path


def _read_bound_file(root: Path, relative: str, digest: str, label: str) -> bytes:
    _require_digest(digest, f"{label}_sha256")
    path = _resolve_file(root, relative, label)
    try:
        encoded = path.read_bytes()
    except OSError:
        raise _fail(f"{label}_unreadable") from None
    if _sha(encoded) != digest:
        raise _fail(f"{label}_hash_mismatch")
    return encoded


def _json_object(encoded: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(encoded.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):
        raise _fail(f"{label}_invalid_json") from None
    if not isinstance(value, dict):
        raise _fail(f"{label}_not_object")
    return value


def _validate_payload(value: Mapping[str, Any], label: str) -> None:
    candidate = deepcopy(dict(value))
    observed = candidate.pop("payload_sha256", None)
    if observed != _sha(_canonical(candidate)):
        raise _fail(f"{label}_payload_hash_mismatch")


def _expected_episode_ids() -> list[str]:
    return [f"07741c45:{index}" for index in range(10)]


def _validate_checkpoint(value: Mapping[str, Any], run_id: str, label: str) -> None:
    _validate_payload(value, label)
    exact = (
        value.get("schema_version")
        == "membind.native-characterization-c2-checkpoint.v1"
        and value.get("stage") == "C2"
        and value.get("run_id") == run_id
        and value.get("status") == "error"
        and value.get("error_code") == "json.decoder.JSONDecodeError"
        and value.get("planned_block_indices") == [0, 1, 2, 3]
        and value.get("completed_block_indices") == []
        and value.get("completed_episode_ids") == _expected_episode_ids()
    )
    history = value.get("checkpoint_history")
    if not exact or not isinstance(history, list) or len(history) != 10:
        raise _fail(f"{label}_contract_mismatch")
    for index, event in enumerate(history):
        expected = {
            "block_index": 0,
            "episode_id": f"07741c45:{index}",
            "event_type": "episode_completed",
            "history_id": "07741c45",
            "source_sequence": index,
            "status": "completed",
        }
        if event != expected:
            raise _fail(f"{label}_history_mismatch")


def _validate_trace(encoded: bytes) -> None:
    lines = encoded.splitlines()
    if len(lines) != 10:
        raise _fail("trace_line_count_mismatch")
    for index, line in enumerate(lines):
        value = _json_object(line, "trace_line")
        if (
            value.get("schema_version")
            != "membind.native_characterization.trace.v1"
            or value.get("run_id") != RUN_ID
            or value.get("episode_id") != f"07741c45:{index}"
            or value.get("source_sequence") != index
            or not isinstance(value.get("spans"), list)
        ):
            raise _fail("trace_contract_mismatch")


def _validate_outer_log(encoded: bytes) -> None:
    try:
        text = encoded.decode("utf-8")
    except UnicodeError:
        raise _fail("outer_log_not_utf8") from None
    lowered = text.casefold()
    if any(fragment in lowered for fragment in _FORBIDDEN_LOG_FRAGMENTS):
        raise _fail("outer_log_unsafe")
    lines = [line.rstrip("\r") for line in text.splitlines() if line.strip()]
    if not lines or lines[-1] != _TERMINAL_ERROR:
        raise _fail("outer_log_terminal_error_mismatch")
    retries = [line for line in lines if "Retrying _generate_response_with_retry" in line]
    if retries != [
        "Retrying _generate_response_with_retry after 2 attempts...",
        "Retrying _generate_response_with_retry after 3 attempts...",
        "Retrying _generate_response_with_retry after 4 attempts...",
    ]:
        raise _fail("outer_log_retry_trajectory_mismatch")


def _validate_freeze(value: Mapping[str, Any]) -> None:
    policy = value.get("construction_compatibility_policy")
    if not isinstance(policy, Mapping):
        raise _fail("freeze_construction_policy_missing")
    if (
        policy.get("structured_output_mode") != "json_schema"
        or policy.get("requested_max_tokens") != 16_384
        or policy.get("episode_indices") != [0]
    ):
        raise _fail("freeze_construction_policy_mismatch")


def _validate_historical_probe(value: Mapping[str, Any]) -> None:
    events = value.get("observed_events")
    if not isinstance(events, list) or len(events) != 8:
        raise _fail("historical_probe_events_mismatch")
    expected_budgets = [2_048, 8_192] * 4
    if (
        value.get("classification") != "exact_historical_truncation_reproduced"
        or value.get("error_type") != "JSONDecodeError"
        or value.get("high_level_attempt_count") != 4
        or value.get("llm_call_count") != 8
        or value.get("response_bodies_persisted") is not False
        or [event.get("max_tokens") for event in events] != expected_budgets
        or any(event.get("finish_reason") != "length" for event in events)
    ):
        raise _fail("historical_probe_contract_mismatch")


def _validate_source(source: Mapping[str, Any], bindings: C2SecondFailureBindings) -> None:
    if _sha(_canonical(source)) != bindings.source_state_sha256:
        raise _fail("source_state_hash_mismatch")
    progress = source.get("stage_progress")
    exact = (
        source.get("protocol_version") == "current-validation-v1.3"
        and source.get("current_stage") == "NATIVE_CHARACTERIZATION"
        and source.get("status") == "native_characterization_offline_only"
        and source.get("current_blocker") is None
        and source.get("current_action_scope")
        == "native_characterization_c2_live_only"
        and source.get("authorized_live_actions")
        == ["native_characterization_c2"]
        and source.get("service_admin_authorized") is False
        and source.get("next_allowed_action") == "run_native_characterization_c2"
        and isinstance(progress, Mapping)
        and progress.get("native_characterization")
        == "c0_c1_pass_c2_replacement_authorized_from_episode_0"
        and METADATA_KEY not in source
    )
    if not exact:
        raise _fail("source_state_not_c2_replacement_authorized")


def _validate_bindings(bindings: C2SecondFailureBindings) -> None:
    if not isinstance(bindings, C2SecondFailureBindings):
        raise _fail("bindings_invalid")
    for field in (
        "source_state_sha256",
        "checkpoint_sha256",
        "trace_sha256",
        "outer_log_sha256",
        "prior_checkpoint_sha256",
        "freeze_sha256",
        "workplan_sha256",
    ):
        _require_digest(getattr(bindings, field), field)


def build_second_failure_state_and_report(
    source_state: Mapping[str, Any],
    *,
    validation_root: Path,
    repo_root: Path,
    bindings: C2SecondFailureBindings,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate immutable evidence and derive the offline state and report."""

    _validate_bindings(bindings)
    source = deepcopy(dict(source_state))
    _validate_source(source, bindings)

    checkpoint = _json_object(
        _read_bound_file(
            validation_root,
            bindings.checkpoint_path,
            bindings.checkpoint_sha256,
            "checkpoint",
        ),
        "checkpoint",
    )
    _validate_checkpoint(checkpoint, RUN_ID, "checkpoint")
    trace = _read_bound_file(
        validation_root, bindings.trace_path, bindings.trace_sha256, "trace"
    )
    _validate_trace(trace)
    outer_log = _read_bound_file(
        validation_root,
        bindings.outer_log_path,
        bindings.outer_log_sha256,
        "outer_log",
    )
    _validate_outer_log(outer_log)
    prior_checkpoint = _json_object(
        _read_bound_file(
            validation_root,
            bindings.prior_checkpoint_path,
            bindings.prior_checkpoint_sha256,
            "prior_checkpoint",
        ),
        "prior_checkpoint",
    )
    _validate_checkpoint(prior_checkpoint, PRIOR_RUN_ID, "prior_checkpoint")
    freeze = _json_object(
        _read_bound_file(
            validation_root,
            FREEZE_RELATIVE_PATH,
            bindings.freeze_sha256,
            "freeze",
        ),
        "freeze",
    )
    _validate_freeze(freeze)
    _read_bound_file(
        repo_root,
        WORKPLAN_RELATIVE_PATH,
        bindings.workplan_sha256,
        "workplan",
    )
    probe = _json_object(
        _read_bound_file(
            validation_root,
            HISTORICAL_PROBE_RELATIVE_PATH,
            HISTORICAL_PROBE_SHA256,
            "historical_probe",
        ),
        "historical_probe",
    )
    _validate_historical_probe(probe)

    report: dict[str, Any] = {
        "schema_version": "membind.native-characterization-c2-second-failure.v1",
        "classification": "repeated_same_boundary_json_schema_decode_failure",
        "run_id": RUN_ID,
        "prior_run_id": PRIOR_RUN_ID,
        "status": "failed_invalid_non_mergeable",
        "structured_output_mode": "json_schema",
        "error_code": "json.decoder.JSONDecodeError",
        "completed_episode_count": 10,
        "completed_block_count": 0,
        "failed_episode_evidence_persisted": False,
        "high_level_attempt_count_inferred_from_retry_log": 4,
        "transport_attempt_count_for_this_failure_proven": False,
        "same_completed_boundary_as_prior_attempt": True,
        "checkpoint": {
            "path": bindings.checkpoint_path,
            "sha256": bindings.checkpoint_sha256,
        },
        "trace": {
            "path": bindings.trace_path,
            "sha256": bindings.trace_sha256,
            "line_count": 10,
        },
        "outer_log": {
            "path": bindings.outer_log_path,
            "sha256": bindings.outer_log_sha256,
        },
        "prior_checkpoint": {
            "path": bindings.prior_checkpoint_path,
            "sha256": bindings.prior_checkpoint_sha256,
        },
        "frozen_inputs": {
            "freeze_sha256": bindings.freeze_sha256,
            "workplan_sha256": bindings.workplan_sha256,
        },
        "historical_truncation_context": {
            "path": HISTORICAL_PROBE_RELATIVE_PATH,
            "sha256": HISTORICAL_PROBE_SHA256,
            "classification": "exact_historical_truncation_reproduced",
            "finish_reason": "length",
            "budget_sequence": [2_048, 8_192] * 4,
            "relationship_to_current_failure": (
                "consistent_context_only_current_failed_response_not_persisted"
            ),
        },
        "decision_boundary": {
            "automatic_retry_allowed": False,
            "namespace_cleanup_performed": False,
            "json_object_selected": False,
            "freeze_modified": False,
            "workplan_modified": False,
            "next_action": "explicit_json_object_protocol_deviation_decision",
        },
        "secrets_persisted": False,
        "source_state_sha256": bindings.source_state_sha256,
    }
    report["payload_sha256"] = _sha(_canonical(report))
    report_file_sha256 = _sha(_canonical(report) + b"\n")

    target = deepcopy(source)
    target["current_blocker"] = (
        "c2_second_structured_output_failure_requires_protocol_decision"
    )
    target["current_action_scope"] = "native_characterization_offline_only"
    target["authorized_live_actions"] = []
    target["next_allowed_action"] = "assess_c2_json_object_protocol_deviation"
    target["stage_progress"]["native_characterization"] = (
        "c0_c1_pass_c2_second_json_schema_failure_stopped"
    )
    target[METADATA_KEY] = {
        "schema_version": "membind.native-characterization-c2-second-failure-state.v1",
        "source_state_sha256": bindings.source_state_sha256,
        "run_id": RUN_ID,
        "prior_run_id": PRIOR_RUN_ID,
        "error_code": "json.decoder.JSONDecodeError",
        "completed_episode_count": 10,
        "attempt_valid": False,
        "attempt_mergeable": False,
        "resume_allowed": False,
        "live_authorized": False,
        "cleanup_authorized": False,
        "json_object_authorized": False,
        "report_path": DEFAULT_REPORT_RELATIVE_PATH,
        "report_payload_sha256": report["payload_sha256"],
        "report_sha256": report_file_sha256,
        "checkpoint_sha256": bindings.checkpoint_sha256,
        "trace_sha256": bindings.trace_sha256,
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
            try:
                temporary.unlink()
            except OSError:
                pass
        raise _fail("atomic_write_failed") from None


@contextmanager
def _locked(state_path: Path):
    lock_path = state_path.with_name(".native-characterization-c2-second-failure.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
    except OSError:
        raise _fail("lock_failed") from None


def _already_applied(
    state: Mapping[str, Any], report_path: Path
) -> bool:
    metadata = state.get(METADATA_KEY)
    if not isinstance(metadata, Mapping):
        return False
    exact = (
        state.get("authorized_live_actions") == []
        and state.get("current_action_scope")
        == "native_characterization_offline_only"
        and state.get("current_blocker")
        == "c2_second_structured_output_failure_requires_protocol_decision"
        and state.get("next_allowed_action")
        == "assess_c2_json_object_protocol_deviation"
        and metadata.get("run_id") == RUN_ID
        and metadata.get("live_authorized") is False
        and metadata.get("cleanup_authorized") is False
        and metadata.get("json_object_authorized") is False
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


def finalize_second_failure(
    *,
    state_path: Path,
    report_path: Path,
    validation_root: Path,
    repo_root: Path,
    bindings: C2SecondFailureBindings,
    apply: bool,
) -> dict[str, Any]:
    """Validate and optionally persist the report plus revoked-authority state."""

    with _locked(state_path):
        try:
            source = _json_object(state_path.read_bytes(), "state")
        except OSError:
            raise _fail("state_unreadable") from None
        if _already_applied(source, report_path):
            return {
                "status": "already_applied",
                "run_id": RUN_ID,
                "state_sha256": _sha(_canonical(source)),
            }
        target, report = build_second_failure_state_and_report(
            source,
            validation_root=validation_root,
            repo_root=repo_root,
            bindings=bindings,
        )
        report_encoded = _canonical(report) + b"\n"
        target_encoded = _canonical(target) + b"\n"
        if not apply:
            return {
                "status": "validated_not_applied",
                "run_id": RUN_ID,
                "target_state_sha256": _sha(_canonical(target)),
                "report_sha256": _sha(report_encoded),
                "report_payload_sha256": report["payload_sha256"],
            }
        if report_path.exists() and report_path.read_bytes() != report_encoded:
            raise _fail("report_already_exists_with_different_content")
        _atomic_write(report_path, report_encoded)
        _atomic_write(state_path, target_encoded)
        return {
            "status": "applied",
            "run_id": RUN_ID,
            "target_state_sha256": _sha(_canonical(target)),
            "report_sha256": _sha(report_encoded),
            "report_payload_sha256": report["payload_sha256"],
        }


def _default_bindings() -> C2SecondFailureBindings:
    return C2SecondFailureBindings(
        source_state_sha256=SOURCE_STATE_SHA256,
        checkpoint_path=CHECKPOINT_RELATIVE_PATH,
        checkpoint_sha256=CHECKPOINT_SHA256,
        trace_path=TRACE_RELATIVE_PATH,
        trace_sha256=TRACE_SHA256,
        outer_log_path=OUTER_LOG_RELATIVE_PATH,
        outer_log_sha256=OUTER_LOG_SHA256,
        prior_checkpoint_path=PRIOR_CHECKPOINT_RELATIVE_PATH,
        prior_checkpoint_sha256=PRIOR_CHECKPOINT_SHA256,
        freeze_sha256=FREEZE_SHA256,
        workplan_sha256=WORKPLAN_SHA256,
    )


def main() -> int:
    validation = Path(__file__).resolve().parents[1]
    repo = validation.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--state", type=Path, default=validation / "CURRENT_STATE.json")
    parser.add_argument(
        "--report", type=Path, default=validation / DEFAULT_REPORT_RELATIVE_PATH
    )
    args = parser.parse_args()
    try:
        result = finalize_second_failure(
            state_path=args.state,
            report_path=args.report,
            validation_root=validation,
            repo_root=repo,
            bindings=_default_bindings(),
            apply=args.apply,
        )
    except C2SecondFailureError as exc:
        print(json.dumps({"status": "denied", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
