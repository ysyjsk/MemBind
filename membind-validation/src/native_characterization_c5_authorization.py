"""One-way, fail-closed authorization of the frozen C5 live screening."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


FREEZE_RELATIVE_PATH = "artifacts/native_characterization/freeze_reference_aligned_64k.json"
WORKPLAN_NAME = "MemBind_NATIVE_GRAPHITI_CHARACTERIZATION_WORKPLAN_v1.1.md"
FROZEN_HISTORY_ID = "07741c45"
FROZEN_EPISODE_COUNT = 49
FROZEN_CONCURRENCY_GRID = [1, 2, 4, 8]
FROZEN_NAMESPACES = [
    "nc-e4-1434fcb947df5c3d",
    "nc-e4-b352061ffa0d4b21",
    "nc-e4-c15538d1fe2801cb",
    "nc-e4-2a427029b1a8b2ac",
]
JUDGE_RUN_ID = "jq-b00a9689796c1e67"
JUDGE_SUMMARY_RELATIVE_PATH = (
    f"artifacts/judge_qualification/runs/{JUDGE_RUN_ID}/qualification_summary.json"
)
JUDGE_RUNTIME_RELATIVE_PATH = (
    f"artifacts/judge_qualification/runs/{JUDGE_RUN_ID}/runtime_identity.json"
)
C4_RUN_ID = "c4-8e76fba0288047f9"
C4_RUN_RELATIVE_PATH = f"artifacts/native_characterization/runs/{C4_RUN_ID}"
C4_SUMMARY_RELATIVE_PATH = f"{C4_RUN_RELATIVE_PATH}/e3_sync_async.json"
C4_CHECKPOINT_RELATIVE_PATH = f"{C4_RUN_RELATIVE_PATH}/checkpoint.json"
C4_EVENTS_RELATIVE_PATH = f"{C4_RUN_RELATIVE_PATH}/events.jsonl"
JUDGE_MANIFEST_RELATIVE_PATH = (
    f"artifacts/judge_qualification/runs/{JUDGE_RUN_ID}/manifest.json"
)
JUDGE_FREEZE_RELATIVE_PATH = (
    f"artifacts/judge_qualification/runs/{JUDGE_RUN_ID}/fixture_freeze.json"
)
JUDGE_CHECKPOINT_RELATIVE_PATH = (
    f"artifacts/judge_qualification/runs/{JUDGE_RUN_ID}/checkpoint.json"
)
JUDGE_EVENTS_RELATIVE_PATH = (
    f"artifacts/judge_qualification/runs/{JUDGE_RUN_ID}/events.jsonl"
)
C5_FOCUSED_REGRESSION_RELATIVE_PATH = (
    "artifacts/tdd/native_characterization_c5_focused_green_20260813.log"
)
C5_FULL_REGRESSION_RELATIVE_PATH = (
    "artifacts/tdd/native_characterization_c5_full_offline_regression_20260813.log"
)
C5_STALE_REGRESSION_RELATIVE_PATH = (
    "artifacts/tdd/"
    "native_characterization_c5_stale_state_fixture_focused_green_20260813.log"
)
C4_BOUNDED_USE = (
    "c4_summary_sufficient_for_c5_progression_without_reclassifying_"
    "c4_attempt_mergeable"
)
C5_LIVE_TCB_PATHS = {
    "c5_core_source": "src/native_characterization_c5.py",
    "c5_authorization_source": "src/native_characterization_c5_authorization.py",
    "c5_live_source": "src/native_characterization_c5_live.py",
    "c5_live_artifacts_source": "src/native_characterization_c5_live_artifacts.py",
    "c5_live_core_source": "src/native_characterization_c5_live_core.py",
    "c5_qa_source": "src/native_characterization_c5_qa.py",
    "c5_core_tests": "tests/test_native_characterization_c5.py",
    "c5_authorization_tests": "tests/test_native_characterization_c5_authorization.py",
    "c5_live_tests": "tests/test_native_characterization_c5_live.py",
    "c5_live_artifacts_tests": "tests/test_native_characterization_c5_live_artifacts.py",
    "c5_live_core_tests": "tests/test_native_characterization_c5_live_core.py",
    "c5_live_integration_tests": "tests/test_native_characterization_c5_live_integration.py",
    "c5_qa_tests": "tests/test_native_characterization_c5_qa.py",
}
C5_GREEN_RECEIPT_PATHS = {
    "c5_focused_regression": C5_FOCUSED_REGRESSION_RELATIVE_PATH,
    "c5_stale_state_regression": C5_STALE_REGRESSION_RELATIVE_PATH,
    "c5_full_offline_regression": C5_FULL_REGRESSION_RELATIVE_PATH,
}
SOURCE_PROGRESS = "c2_c3_complete_c4_offline_tdd_pending"
TARGET_PROGRESS = (
    "c2_c3_complete_c4_summary_retained_nonmergeable_c5_live_authorized"
)
_GREEN_TEST_RE = re.compile(r"^Ran ([1-9][0-9]*) tests? in ", re.MULTILINE)


class C5AuthorizationError(RuntimeError):
    """Sanitized C5 authority-transition failure."""


def _fail(code: str) -> None:
    raise C5AuthorizationError(code)


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        _fail("evidence_unreadable")


def _load(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(value, dict):
        _fail(code)
    return value


def _payload_sha256(value: Mapping[str, Any]) -> str:
    candidate = deepcopy(dict(value))
    candidate.pop("payload_sha256", None)
    raw = json.dumps(
        candidate, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _exact_path(validation: Path, relative: str, code: str) -> Path:
    candidate = validation / relative
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(validation)
    except (OSError, RuntimeError, ValueError):
        _fail(code)
    if candidate.is_symlink() or not resolved.is_file():
        _fail(code)
    return resolved


def _sealed_file(validation: Path, relative: str, code: str) -> tuple[Path, dict[str, Any]]:
    path = _exact_path(validation, relative, f"{code}_path_invalid")
    value = _load(path, f"{code}_unreadable")
    if value.get("payload_sha256") != _payload_sha256(value):
        _fail(f"{code}_payload_mismatch")
    return path, value


def _jsonl_events(validation: Path, relative: str, code: str) -> tuple[Path, list[dict[str, Any]]]:
    path = _exact_path(validation, relative, f"{code}_path_invalid")
    try:
        lines = path.read_text("ascii").splitlines()
        events = [json.loads(line) for line in lines]
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(f"{code}_unreadable")
    if (
        not events
        or not all(isinstance(event, dict) for event in events)
        or [event.get("event_sequence") for event in events]
        != list(range(len(events)))
        or any(event.get("payload_sha256") != _payload_sha256(event) for event in events)
    ):
        _fail(f"{code}_invalid")
    return path, events


def _green_log(validation: Path, relative: str, code: str) -> dict[str, Any]:
    path = _exact_path(validation, relative, f"{code}_path_invalid")
    try:
        text = path.read_text("utf-8")
    except (OSError, UnicodeError):
        _fail(f"{code}_unreadable")
    matches = _GREEN_TEST_RE.findall(text)
    if len(matches) != 1 or "FAILED" in text or "ERRORS=" in text:
        _fail(f"{code}_not_green")
    lines = text.splitlines()
    ran_index = next(
        (index for index, line in enumerate(lines) if _GREEN_TEST_RE.match(line)),
        None,
    )
    if (
        ran_index is None
        or not any(line.strip() == "OK" for line in lines[ran_index + 1 :])
    ):
        _fail(f"{code}_not_green")
    return {
        "path": relative,
        "sha256": _sha(path),
        "status": "green",
        "test_count": int(matches[0]),
    }


def _c4_disposition(
    validation: Path,
    c4_path: Path,
    c4: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        c4_path != validation / C4_SUMMARY_RELATIVE_PATH
        or c4.get("schema_version")
        != "membind.native-characterization-e3-sync-async.v1"
        or c4.get("run_id") != C4_RUN_ID
        or c4.get("status") != "complete"
        or c4.get("mergeable") is not False
        or c4.get("block_count") != 10
        or c4.get("episode_count") != 490
    ):
        _fail("c4_summary_not_exact_bounded_complete")
    checkpoint_path, checkpoint = _sealed_file(
        validation, C4_CHECKPOINT_RELATIVE_PATH, "c4_checkpoint"
    )
    progress = checkpoint.get("progress")
    failure = checkpoint.get("failure")
    if (
        checkpoint.get("schema_version")
        != "membind.native-characterization-c4-checkpoint.v1"
        or checkpoint.get("run_id") != C4_RUN_ID
        or checkpoint.get("stage") != "C4/E3"
        or checkpoint.get("checkpoint_level") != "root"
        or checkpoint.get("status") != "incomplete_invalid_non_mergeable"
        or not isinstance(progress, Mapping)
        or progress.get("completed_block_indices") != list(range(10))
        or progress.get("completed_episode_count") != 490
        or progress.get("failure_stage") != "verification"
        or not isinstance(failure, Mapping)
        or failure.get("error_class") != "builtins.TypeError"
    ):
        _fail("c4_checkpoint_disposition_mismatch")
    events_path, events = _jsonl_events(
        validation, C4_EVENTS_RELATIVE_PATH, "c4_events"
    )
    failures = [event for event in events if event.get("event_type") == "failure"]
    last = events[-1]
    if (
        len(events) != 736
        or sum(event.get("event_type") == "enqueue" for event in events) != 245
        or sum(event.get("event_type") == "publication" for event in events) != 490
        or len(failures) != 1
        or failures[0] != last
        or last.get("run_id") != C4_RUN_ID
        or last.get("event_sequence") != 735
        or last.get("status") != "incomplete_invalid_non_mergeable"
        or last.get("failure_scope") != "stage"
        or last.get("failure_stage") != "verification"
        or last.get("error_class") != "builtins.TypeError"
        or last.get("block_index") is not None
        or last.get("source_sequence") is not None
        or last.get("completed_block_count") != 10
        or last.get("completed_episode_count") != 490
    ):
        _fail("c4_retained_failure_mismatch")
    return {
        "run_id": C4_RUN_ID,
        "summary_path": C4_SUMMARY_RELATIVE_PATH,
        "summary_sha256": _sha(c4_path),
        "summary_payload_sha256": c4["payload_sha256"],
        "summary_status": "complete",
        "summary_mergeable": False,
        "checkpoint_path": C4_CHECKPOINT_RELATIVE_PATH,
        "checkpoint_sha256": _sha(checkpoint_path),
        "checkpoint_payload_sha256": checkpoint["payload_sha256"],
        "events_path": C4_EVENTS_RELATIVE_PATH,
        "events_sha256": _sha(events_path),
        "event_count": len(events),
        "failure_event_count": len(failures),
        "last_failure_event_sequence": last["event_sequence"],
        "last_failure_event_payload_sha256": last["payload_sha256"],
        "retained_failure": {
            "attempt_status": last["status"],
            "failure_stage": last["failure_stage"],
            "error_class": last["error_class"],
        },
        "bounded_use": C4_BOUNDED_USE,
    }


def _judge_contract(validation: Path) -> dict[str, Any]:
    summary_path, summary = _sealed_file(
        validation, JUDGE_SUMMARY_RELATIVE_PATH, "judge_summary"
    )
    runtime_path, runtime = _sealed_file(
        validation, JUDGE_RUNTIME_RELATIVE_PATH, "judge_runtime"
    )
    manifest_path, manifest = _sealed_file(
        validation, JUDGE_MANIFEST_RELATIVE_PATH, "judge_manifest"
    )
    freeze_path, freeze = _sealed_file(
        validation, JUDGE_FREEZE_RELATIVE_PATH, "judge_freeze"
    )
    checkpoint_path, checkpoint = _sealed_file(
        validation, JUDGE_CHECKPOINT_RELATIVE_PATH, "judge_checkpoint"
    )
    events_path, events = _jsonl_events(
        validation, JUDGE_EVENTS_RELATIVE_PATH, "judge_events"
    )
    identity = runtime.get("identity")
    public = identity.get("backend_public_config") if isinstance(identity, Mapping) else None
    confusion = summary.get("confusion_matrix")
    strict_gate = freeze.get("strict_pass_gate")
    previous: str | None = None
    event_chain_valid = True
    for event in events:
        if event.get("previous_event_sha256") != previous:
            event_chain_valid = False
            break
        previous = event.get("payload_sha256")
    if (
        manifest.get("schema_version") != "membind.judge-qualification-run.v1"
        or manifest.get("protocol_id") != "judge-qualification-v1.0"
        or manifest.get("scientific_surface") != "JUDGE_QUALIFICATION_ONLY"
        or manifest.get("run_id") != JUDGE_RUN_ID
        or manifest.get("freeze_file_sha256") != _sha(freeze_path)
        or manifest.get("freeze_payload_sha256") != freeze.get("payload_sha256")
        or manifest.get("runtime_identity_file_sha256") != _sha(runtime_path)
        or manifest.get("runtime_identity_payload_sha256")
        != runtime.get("payload_sha256")
        or freeze.get("schema_version")
        != "membind.judge-qualification-freeze.v1"
        or freeze.get("protocol_id") != "judge-qualification-v1.0"
        or freeze.get("scientific_surface") != "JUDGE_QUALIFICATION_ONLY"
        or not isinstance(freeze.get("items"), list)
        or len(freeze["items"]) != 14
        or [item.get("item_index") for item in freeze["items"]]
        != list(range(14))
        or sum(item.get("human_label") is True for item in freeze["items"]) != 7
        or sum(item.get("human_label") is False for item in freeze["items"]) != 7
        or not isinstance(strict_gate, Mapping)
        or strict_gate.get("planned_item_count") != 14
        or summary.get("schema_version")
        != "membind.judge-qualification-summary.v1"
        or summary.get("protocol_id") != "judge-qualification-v1.0"
        or summary.get("scientific_surface") != "JUDGE_QUALIFICATION_ONLY"
        or summary.get("run_id") != JUDGE_RUN_ID
        or summary.get("attempt_status") != "complete"
        or summary.get("qualification_status") != "PASS"
        or summary.get("mergeable") is not True
        or [
            summary.get("planned_item_count"),
            summary.get("terminal_item_count"),
            summary.get("eligible_item_count"),
            summary.get("agreement_count"),
        ]
        != [14, 14, 14, 14]
        or summary.get("observed_agreement") != 1.0
        or summary.get("cohens_kappa") != 1.0
        or any(
            summary.get(name) != 0
            for name in (
                "invalid_output_count",
                "service_error_count",
                "retry_count_total",
            )
        )
        or confusion
        != {
            "true_positive": 7,
            "true_negative": 7,
            "false_positive": 0,
            "false_negative": 0,
        }
        or runtime.get("schema_version") != "membind.judge-runtime-identity.v1"
        or runtime.get("run_id") != JUDGE_RUN_ID
        or summary.get("freeze_payload_sha256") != freeze.get("payload_sha256")
        or summary.get("runtime_identity_payload_sha256")
        != runtime.get("payload_sha256")
        or checkpoint.get("schema_version")
        != "membind.judge-qualification-checkpoint.v1"
        or checkpoint.get("run_id") != JUDGE_RUN_ID
        or checkpoint.get("status") != "complete"
        or checkpoint.get("phase") != "finalized"
        or checkpoint.get("next_item_index") != 14
        or checkpoint.get("terminal_item_count") != 14
        or checkpoint.get("event_count") != 28
        or checkpoint.get("last_event_payload_sha256")
        != events[-1].get("payload_sha256")
        or checkpoint.get("freeze_payload_sha256") != freeze.get("payload_sha256")
        or checkpoint.get("runtime_identity_payload_sha256")
        != runtime.get("payload_sha256")
        or len(events) != 28
        or [event.get("event_type") for event in events]
        != ["dispatch_intent_durable", "terminal_success"] * 14
        or [event.get("item_index") for event in events]
        != [index for index in range(14) for _ in range(2)]
        or any(event.get("run_id") != JUDGE_RUN_ID for event in events)
        or not event_chain_valid
        or not isinstance(identity, Mapping)
        or identity.get("served_model_name") != "qwen3-32b-fp8"
        or identity.get("vllm_version") != "0.26.0"
        or identity.get("max_model_len") != 65536
        or identity.get("effective_enable_thinking") is not False
        or not isinstance(public, Mapping)
        or public.get("backend") != "openai_compatible_chat_completions"
        or public.get("served_model_name") != "qwen3-32b-fp8"
        or public.get("temperature") != 0
        or public.get("max_tokens") != 10
        or public.get("n") != 1
        or public.get("max_attempts") != 1
        or public.get("sdk_hidden_retries") != 0
        or public.get("effective_enable_thinking") is not False
    ):
        _fail("judge_qualification_not_exact_pass")
    return {
        "judge_qualification_summary_path": JUDGE_SUMMARY_RELATIVE_PATH,
        "judge_qualification_summary_sha256": _sha(summary_path),
        "judge_qualification_summary_payload_sha256": str(
            summary["payload_sha256"]
        ),
        "judge_runtime_identity_path": JUDGE_RUNTIME_RELATIVE_PATH,
        "judge_runtime_identity_sha256": _sha(runtime_path),
        "judge_runtime_identity_payload_sha256": str(runtime["payload_sha256"]),
        "judge_closure": {
            "manifest_path": JUDGE_MANIFEST_RELATIVE_PATH,
            "manifest_sha256": _sha(manifest_path),
            "manifest_payload_sha256": manifest["payload_sha256"],
            "fixture_freeze_path": JUDGE_FREEZE_RELATIVE_PATH,
            "fixture_freeze_sha256": _sha(freeze_path),
            "fixture_freeze_payload_sha256": freeze["payload_sha256"],
            "checkpoint_path": JUDGE_CHECKPOINT_RELATIVE_PATH,
            "checkpoint_sha256": _sha(checkpoint_path),
            "checkpoint_payload_sha256": checkpoint["payload_sha256"],
            "events_path": JUDGE_EVENTS_RELATIVE_PATH,
            "events_sha256": _sha(events_path),
            "event_count": len(events),
            "terminal_item_count": checkpoint["terminal_item_count"],
            "last_event_payload_sha256": events[-1]["payload_sha256"],
        },
    }


def _c5_tcb_contract(validation: Path) -> dict[str, Any]:
    hashes = {
        label: _sha(_exact_path(validation, relative, f"{label}_path_invalid"))
        for label, relative in C5_LIVE_TCB_PATHS.items()
    }
    focused = _green_log(
        validation,
        C5_FOCUSED_REGRESSION_RELATIVE_PATH,
        "c5_focused_regression",
    )
    full = _green_log(
        validation,
        C5_FULL_REGRESSION_RELATIVE_PATH,
        "c5_full_offline_regression",
    )
    stale = _green_log(
        validation,
        C5_STALE_REGRESSION_RELATIVE_PATH,
        "c5_stale_state_regression",
    )
    return {
        "c5_live_tcb_paths": dict(C5_LIVE_TCB_PATHS),
        "c5_live_tcb_sha256": hashes,
        "c5_focused_regression": focused,
        "c5_full_offline_regression": full,
        "c5_stale_state_regression": stale,
    }


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    raw = (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _freeze_contract(freeze: Mapping[str, Any]) -> dict[str, Any]:
    protocol = freeze.get("protocol")
    screening = freeze.get("screening")
    e4 = screening.get("e4") if isinstance(screening, Mapping) else None
    dataset = freeze.get("dataset")
    histories = dataset.get("calibration_histories") if isinstance(dataset, Mapping) else None
    history = next(
        (
            item
            for item in histories or []
            if isinstance(item, Mapping) and item.get("history_id") == FROZEN_HISTORY_ID
        ),
        None,
    )
    blocks = e4.get("block_order") if isinstance(e4, Mapping) else None
    namespaces = (
        [item.get("graph_namespace") for item in blocks]
        if isinstance(blocks, list) and all(isinstance(item, Mapping) for item in blocks)
        else None
    )
    if (
        not isinstance(protocol, Mapping)
        or protocol.get("id") != "native-characterization-v1.1"
        or protocol.get("freeze_marker") is not True
        or not isinstance(e4, Mapping)
        or e4.get("history_id") != FROZEN_HISTORY_ID
        or e4.get("concurrency_order") != FROZEN_CONCURRENCY_GRID
        or namespaces != FROZEN_NAMESPACES
        or not isinstance(history, Mapping)
        or history.get("episode_count") != FROZEN_EPISODE_COUNT
        or len(history.get("episodes", [])) != FROZEN_EPISODE_COUNT
    ):
        _fail("freeze_contract_mismatch")
    return {
        "history_id": FROZEN_HISTORY_ID,
        "episode_count": FROZEN_EPISODE_COUNT,
        "episode_source_hashes": [
            item.get("episode_source_sha256") for item in history["episodes"]
        ],
        "concurrency_grid": FROZEN_CONCURRENCY_GRID,
        "graph_namespaces": FROZEN_NAMESPACES,
        "screening_pass_count": 1,
        "workplan_sha256": protocol.get("workplan_sha256"),
    }


def _is_exact_source(state: Mapping[str, Any]) -> bool:
    return (
        state.get("protocol_version") == "current-validation-v1.3"
        and state.get("current_stage") == "NATIVE_CHARACTERIZATION"
        and state.get("current_action_scope") == "native_characterization_c4_live_only"
        and state.get("status") == "native_characterization_c4_live_only"
        and state.get("authorized_live_actions") == ["native_characterization_c4"]
        and state.get("next_allowed_action") == "run_native_characterization_c4"
        and state.get("native_characterization_live_authorized") is True
        and state.get("current_blocker") is None
        and state.get("live_h0_candidate_authorized") is False
        and state.get("authorized_h0_candidate_id") is None
        and state.get("service_admin_authorized") is False
        and state.get("v3_smoke_003_authorized") is False
        and state.get("stage_progress", {}).get("native_characterization")
        == SOURCE_PROGRESS
        and "native_characterization_c5_authorization" not in state
    )


def _is_exact_target(state: Mapping[str, Any]) -> bool:
    return (
        state.get("protocol_version") == "current-validation-v1.3"
        and state.get("current_stage") == "NATIVE_CHARACTERIZATION"
        and state.get("current_action_scope") == "native_characterization_c5_live_only"
        and state.get("status") == "native_characterization_c5_live_only"
        and state.get("authorized_live_actions") == ["native_characterization_c5"]
        and state.get("next_allowed_action") == "run_native_characterization_c5"
        and state.get("native_characterization_live_authorized") is True
        and state.get("current_blocker") is None
        and state.get("live_h0_candidate_authorized") is False
        and state.get("authorized_h0_candidate_id") is None
        and state.get("service_admin_authorized") is False
        and state.get("v3_smoke_003_authorized") is False
        and state.get("stage_progress", {}).get("native_characterization")
        == TARGET_PROGRESS
        and isinstance(state.get("native_characterization_c5_authorization"), Mapping)
    )


def authorize_c5(
    *,
    validation_root: str | Path,
    state_path: str | Path,
    c4_summary_path: str | Path,
    c4_summary_sha256: str,
) -> dict[str, Any]:
    """Atomically replace exact C4-only authority with exact C5-only authority."""

    validation = Path(validation_root).resolve(strict=True)
    state_file = Path(state_path).resolve(strict=True)
    c4_path = Path(c4_summary_path).resolve(strict=True)
    if state_file != validation / "CURRENT_STATE.json":
        _fail("path_not_exact")
    try:
        c4_relative = c4_path.relative_to(validation).as_posix()
    except ValueError:
        _fail("path_not_exact")
    if c4_relative != C4_SUMMARY_RELATIVE_PATH:
        _fail("path_not_exact")
    if not isinstance(c4_summary_sha256, str) or _sha(c4_path) != c4_summary_sha256:
        _fail("c4_summary_hash_mismatch")

    freeze_path = validation / FREEZE_RELATIVE_PATH
    workplan_path = validation.parent / WORKPLAN_NAME
    freeze = _load(freeze_path, "freeze_unreadable")
    if freeze.get("payload_sha256") != _payload_sha256(freeze):
        _fail("freeze_payload_mismatch")
    contract = _freeze_contract(freeze)
    if _sha(workplan_path) != contract["workplan_sha256"]:
        _fail("workplan_hash_mismatch")
    c4 = _load(c4_path, "c4_summary_unreadable")
    if c4.get("payload_sha256") != _payload_sha256(c4):
        _fail("c4_payload_mismatch")
    if (
        not isinstance(c4.get("payload_sha256"), str)
    ):
        _fail("c4_summary_not_complete")
    c4_disposition = _c4_disposition(validation, c4_path, c4)
    judge_evidence = _judge_contract(validation)
    tcb_evidence = _c5_tcb_contract(validation)

    state = _load(state_file, "state_unreadable")
    if _is_exact_target(state):
        evidence = state["native_characterization_c5_authorization"]
        expected = {
            "schema_version": "membind.native-characterization-c5-authorization.v1",
            **contract,
            "freeze_path": FREEZE_RELATIVE_PATH,
            "freeze_sha256": _sha(freeze_path),
            "freeze_payload_sha256": freeze["payload_sha256"],
            "c4_summary_path": c4_relative,
            "c4_summary_sha256": c4_summary_sha256,
            "c4_summary_payload_sha256": c4["payload_sha256"],
            "c4_disposition": c4_disposition,
            **judge_evidence,
            **tcb_evidence,
            "live_authorized": True,
        }
        if evidence != expected:
            _fail("source_state_not_exact")
        return {"status": "authorized"}
    if not _is_exact_source(state):
        _fail("source_state_not_exact")

    evidence = {
        "schema_version": "membind.native-characterization-c5-authorization.v1",
        **contract,
        "freeze_path": FREEZE_RELATIVE_PATH,
        "freeze_sha256": _sha(freeze_path),
        "freeze_payload_sha256": freeze["payload_sha256"],
        "c4_summary_path": c4_relative,
        "c4_summary_sha256": c4_summary_sha256,
        "c4_summary_payload_sha256": c4["payload_sha256"],
        "c4_disposition": c4_disposition,
        **judge_evidence,
        **tcb_evidence,
        "live_authorized": True,
    }
    target = deepcopy(state)
    target.update(
        {
            "current_action_scope": "native_characterization_c5_live_only",
            "status": "native_characterization_c5_live_only",
            "authorized_live_actions": ["native_characterization_c5"],
            "next_allowed_action": "run_native_characterization_c5",
            "native_characterization_live_authorized": True,
            "service_admin_authorized": False,
            "native_characterization_c5_authorization": evidence,
            "stage_progress": {
                **target.get("stage_progress", {}),
                "native_characterization": TARGET_PROGRESS,
            },
        }
    )
    _atomic_write(state_file, target)
    return {"status": "authorized"}
