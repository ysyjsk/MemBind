"""Production adapter for the frozen C5 whole-update parallel screening.

This module performs no protocol selection.  It binds the exact C5-only state,
64K freeze, completed C4 summary, qualified Judge, source dataset and four
frozen namespaces before delegating to the tested live core.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

import current_state_gate
import dataset
import native_characterization_c5 as c5
from native_characterization_c5_authorization import (
    C4_BOUNDED_USE,
    C5_GREEN_RECEIPT_PATHS,
    C5_LIVE_TCB_PATHS,
    TARGET_PROGRESS,
)
import native_characterization_c5_live_artifacts as artifacts
import native_characterization_c5_live_core as core
from evaluation.backends.openai_compatible import Qwen3JudgeBackend
from graphiti_core.utils.maintenance.graph_data_operations import clear_data
from live_outputs import evaluate_retrieval, export_canonical_graph
from native_characterization_c5_qa import C5EvidenceAnswerabilityEvaluator
from native_characterization_runtime import build_u0_graphiti_from_env


VALIDATION_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = VALIDATION_ROOT / "CURRENT_STATE.json"
FROZEN_DATASET_PATH = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json"
)
RUNS_ROOT = "artifacts/native_characterization/runs"
FROZEN_HISTORY_ID = "07741c45"
FROZEN_NAMESPACES = core.FROZEN_E4_NAMESPACES
C4_RUN_ID = "c4-8e76fba0288047f9"
JUDGE_RUN_ID = "jq-b00a9689796c1e67"
_RUN_ID_RE = re.compile(r"^c5-[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "body",
        "content",
        "episode_body",
        "messages",
        "password",
        "prompt",
        "raw_output",
        "raw_response",
        "reference_answer",
        "request",
        "response",
        "secret",
        "token",
    }
)
_SECRET_RE = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[-_]?key|authorization|password|secret)\s*[=:]\s*\S+)"
)


class C5LiveAdapterError(RuntimeError):
    """Sanitized production-boundary failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> C5LiveAdapterError:
    return C5LiveAdapterError(code)


class _C5RunLease:
    """Process-scoped exclusive lease for every mutation of one C5 run."""

    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor

    @classmethod
    def acquire(cls, runs_root: Path, run_id: str) -> "_C5RunLease":
        if _RUN_ID_RE.fullmatch(run_id) is None:
            raise _fail("run_id_invalid")
        root = Path(runs_root)
        lock_root = root / ".locks"
        try:
            root.mkdir(parents=True, exist_ok=True)
            if root.is_symlink():
                raise _fail("c5_run_lease_path_invalid")
            lock_root.mkdir(mode=0o700, exist_ok=True)
            if lock_root.is_symlink():
                raise _fail("c5_run_lease_path_invalid")
            flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(lock_root / f"{run_id}.lock", flags, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                os.close(descriptor)
                raise _fail("c5_run_lease_path_invalid")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(descriptor)
                raise _fail("c5_run_lease_locked") from None
            return cls(descriptor)
        except C5LiveAdapterError:
            raise
        except OSError:
            raise _fail("c5_run_lease_unavailable") from None

    def close(self) -> None:
        descriptor = self._descriptor
        if descriptor < 0:
            return
        self._descriptor = -1
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise _fail("evidence_unreadable") from None


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _sealed(value: Mapping[str, Any], expected: Any, code: str) -> None:
    if (
        not isinstance(expected, str)
        or _SHA256_RE.fullmatch(expected) is None
        or c5.payload_sha256(value) != expected
        or value.get("payload_sha256") != expected
    ):
        raise _fail(code)


def _exact_file(
    validation: Path,
    relative: Any,
    expected_sha: Any,
    expected_payload: Any,
    code: str,
) -> tuple[Path, dict[str, Any]]:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise _fail(f"{code}_path_invalid")
    candidate = validation / relative
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(validation)
    except (OSError, RuntimeError, ValueError):
        raise _fail(f"{code}_path_invalid") from None
    if candidate.is_symlink() or not isinstance(expected_sha, str) or _sha(resolved) != expected_sha:
        raise _fail(f"{code}_hash_mismatch")
    value = _read_json(resolved, f"{code}_invalid")
    _sealed(value, expected_payload, f"{code}_payload_mismatch")
    return resolved, value


def _exact_bytes_file(
    validation: Path,
    relative: Any,
    expected_sha: Any,
    code: str,
) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise _fail(f"{code}_path_invalid")
    candidate = validation / relative
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(validation)
    except (OSError, RuntimeError, ValueError):
        raise _fail(f"{code}_path_invalid") from None
    if (
        candidate.is_symlink()
        or not isinstance(expected_sha, str)
        or _SHA256_RE.fullmatch(expected_sha) is None
        or _sha(resolved) != expected_sha
    ):
        raise _fail(f"{code}_hash_mismatch")
    return resolved


def _read_sealed_events(
    path: Path,
    *,
    schema_version: str,
    run_id: str,
    chained: bool,
    code: str,
) -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError:
        raise _fail(f"{code}_unreadable") from None
    if not raw or not raw.endswith(b"\n"):
        raise _fail(f"{code}_invalid")
    events: list[dict[str, Any]] = []
    previous: str | None = None
    for sequence, line in enumerate(raw.splitlines()):
        try:
            event = json.loads(line.decode("ascii"))
        except (UnicodeError, json.JSONDecodeError):
            raise _fail(f"{code}_invalid") from None
        if not isinstance(event, dict):
            raise _fail(f"{code}_invalid")
        _sealed(event, event.get("payload_sha256"), f"{code}_seal_invalid")
        if (
            event.get("schema_version") != schema_version
            or event.get("run_id") != run_id
            or event.get("event_sequence") != sequence
            or (
                chained
                and event.get("previous_event_sha256") != previous
            )
        ):
            raise _fail(f"{code}_chain_invalid")
        previous = event["payload_sha256"]
        events.append(event)
    return events


def _validate_tcb(validation: Path, metadata: Mapping[str, Any]) -> None:
    paths = metadata.get("c5_live_tcb_paths")
    hashes = metadata.get("c5_live_tcb_sha256")
    if paths != C5_LIVE_TCB_PATHS or not isinstance(hashes, Mapping):
        raise _fail("c5_live_tcb_not_exact")
    if set(hashes) != set(C5_LIVE_TCB_PATHS):
        raise _fail("c5_live_tcb_not_exact")
    for name, relative in C5_LIVE_TCB_PATHS.items():
        _exact_bytes_file(
            validation,
            relative,
            hashes.get(name),
            "c5_live_tcb",
        )
    for name, expected_path in C5_GREEN_RECEIPT_PATHS.items():
        receipt = metadata.get(name)
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("status") != "green"
            or isinstance(receipt.get("test_count"), bool)
            or not isinstance(receipt.get("test_count"), int)
            or receipt["test_count"] <= 0
            or receipt.get("path") != expected_path
        ):
            raise _fail("c5_live_tdd_evidence_not_exact")
        path = _exact_bytes_file(
            validation,
            expected_path,
            receipt.get("sha256"),
            "c5_live_tdd_evidence",
        )
        try:
            text = path.read_text("utf-8")
        except (OSError, UnicodeError):
            raise _fail("c5_live_tdd_evidence_unreadable") from None
        matches = re.findall(r"^Ran ([1-9][0-9]*) tests? in ", text, re.MULTILINE)
        if (
            matches != [str(receipt["test_count"])]
            or "FAILED" in text
            or "ERRORS=" in text
            or re.search(r"^OK$", text, re.MULTILINE) is None
        ):
            raise _fail("c5_live_tdd_evidence_not_green")


def _validate_state(state: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = state.get("native_characterization_c5_authorization")
    if (
        state.get("protocol_version") != "current-validation-v1.3"
        or state.get("current_stage") != "NATIVE_CHARACTERIZATION"
        or state.get("status") != "native_characterization_c5_live_only"
        or state.get("current_action_scope") != "native_characterization_c5_live_only"
        or state.get("authorized_live_actions") != ["native_characterization_c5"]
        or state.get("next_allowed_action") != "run_native_characterization_c5"
        or state.get("native_characterization_live_authorized") is not True
        or state.get("current_blocker") is not None
        or state.get("live_h0_candidate_authorized") is not False
        or state.get("authorized_h0_candidate_id") is not None
        or state.get("service_admin_authorized") is not False
        or state.get("v3_smoke_003_authorized") is not False
        or not isinstance(state.get("stage_progress"), Mapping)
        or state["stage_progress"].get("native_characterization")
        != TARGET_PROGRESS
        or not isinstance(metadata, Mapping)
        or metadata.get("schema_version")
        != "membind.native-characterization-c5-authorization.v1"
        or metadata.get("live_authorized") is not True
    ):
        raise _fail("state_not_exact_c5_live")
    return metadata


def _validate_freeze(freeze: Mapping[str, Any], metadata: Mapping[str, Any]) -> list[str]:
    runtime = freeze.get("runtime_identities")
    construction = runtime.get("construction") if isinstance(runtime, Mapping) else None
    embedding = runtime.get("embedding") if isinstance(runtime, Mapping) else None
    screening = freeze.get("screening")
    e4 = screening.get("e4") if isinstance(screening, Mapping) else None
    dataset_spec = freeze.get("dataset")
    histories = dataset_spec.get("calibration_histories") if isinstance(dataset_spec, Mapping) else None
    history = next(
        (
            item
            for item in histories or []
            if isinstance(item, Mapping) and item.get("history_id") == FROZEN_HISTORY_ID
        ),
        None,
    )
    blocks = e4.get("block_order") if isinstance(e4, Mapping) else None
    expected_blocks = [
        {"block_index": index, "concurrency": concurrency, "graph_namespace": namespace}
        for index, (concurrency, namespace) in enumerate(
            zip(core.FROZEN_CONCURRENCY_GRID, FROZEN_NAMESPACES)
        )
    ]
    episodes = history.get("episodes") if isinstance(history, Mapping) else None
    if (
        freeze.get("schema_version") != "membind.native-characterization-freeze.v1"
        or freeze.get("artifact_id") != "native-characterization-freeze-reference-aligned-64k"
        or not isinstance(construction, Mapping)
        or construction.get("vllm_version") != "0.26.0"
        or construction.get("served_model_id") != "qwen3-32b-fp8"
        or construction.get("max_model_len") != 65536
        or construction.get("rope_type") != "yarn"
        or construction.get("yarn_factor") != 2.0
        or construction.get("original_max_position_embeddings") != 32768
        or construction.get("rope_theta") != 1000000
        or construction.get("enable_thinking") is not False
        or not isinstance(embedding, Mapping)
        or embedding.get("served_model_id") != "qwen3-embedding-0.6b"
        or embedding.get("dimension") != 1024
        or not isinstance(e4, Mapping)
        or e4.get("history_id") != FROZEN_HISTORY_ID
        or e4.get("concurrency_order") != list(core.FROZEN_CONCURRENCY_GRID)
        or blocks != expected_blocks
        or not isinstance(history, Mapping)
        or history.get("episode_count") != core.FROZEN_EPISODE_COUNT
        or not isinstance(episodes, list)
        or len(episodes) != core.FROZEN_EPISODE_COUNT
    ):
        raise _fail("freeze_contract_mismatch")
    hashes: list[str] = []
    for index, item in enumerate(episodes):
        if (
            not isinstance(item, Mapping)
            or item.get("source_sequence") != index
            or not isinstance(item.get("episode_source_sha256"), str)
            or _SHA256_RE.fullmatch(item["episode_source_sha256"]) is None
        ):
            raise _fail("freeze_episode_contract_mismatch")
        hashes.append(item["episode_source_sha256"])
    if (
        metadata.get("history_id") != FROZEN_HISTORY_ID
        or metadata.get("episode_count") != core.FROZEN_EPISODE_COUNT
        or metadata.get("episode_source_hashes") != hashes
        or metadata.get("concurrency_grid") != list(core.FROZEN_CONCURRENCY_GRID)
        or metadata.get("graph_namespaces") != list(FROZEN_NAMESPACES)
        or metadata.get("screening_pass_count") != 1
    ):
        raise _fail("authorization_freeze_mismatch")
    return hashes


def _validate_c4(c4_value: Mapping[str, Any]) -> None:
    if (
        c4_value.get("schema_version")
        != "membind.native-characterization-e3-sync-async.v1"
        or c4_value.get("status") != "complete"
        or c4_value.get("run_id") != C4_RUN_ID
        or c4_value.get("block_count") != 10
        or c4_value.get("episode_count") != 490
        or c4_value.get("mergeable") is not False
    ):
        raise _fail("c4_summary_not_complete")


def _validate_c4_closure(
    validation: Path,
    metadata: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> dict[str, str]:
    closure = metadata.get("c4_disposition")
    if not isinstance(closure, Mapping):
        raise _fail("c4_closure_missing")
    retained = closure.get("retained_failure")
    if (
        closure.get("run_id") != C4_RUN_ID
        or closure.get("summary_path") != metadata.get("c4_summary_path")
        or closure.get("summary_sha256") != metadata.get("c4_summary_sha256")
        or closure.get("summary_payload_sha256")
        != metadata.get("c4_summary_payload_sha256")
        or closure.get("summary_status") != "complete"
        or closure.get("summary_mergeable") is not False
        or summary.get("run_id") != closure.get("run_id")
        or summary.get("status") != closure.get("summary_status")
        or summary.get("mergeable") is not closure.get("summary_mergeable")
        or closure.get("event_count") != 736
        or closure.get("failure_event_count") != 1
        or closure.get("last_failure_event_sequence") != 735
        or closure.get("bounded_use") != C4_BOUNDED_USE
        or retained
        != {
            "attempt_status": "incomplete_invalid_non_mergeable",
            "failure_stage": "verification",
            "error_class": "builtins.TypeError",
        }
    ):
        raise _fail("c4_closure_not_exact")
    _checkpoint_path, checkpoint = _exact_file(
        validation,
        closure.get("checkpoint_path"),
        closure.get("checkpoint_sha256"),
        closure.get("checkpoint_payload_sha256"),
        "c4_closure_checkpoint",
    )
    progress = checkpoint.get("progress")
    failure = checkpoint.get("failure")
    if (
        checkpoint.get("schema_version")
        != "membind.native-characterization-c4-checkpoint.v1"
        or checkpoint.get("run_id") != C4_RUN_ID
        or checkpoint.get("stage") != "C4/E3"
        or checkpoint.get("checkpoint_level") != "root"
        or checkpoint.get("status") != retained["attempt_status"]
        or not isinstance(progress, Mapping)
        or progress.get("completed_block_indices") != list(range(10))
        or progress.get("completed_episode_count") != 490
        or progress.get("failure_stage") != retained["failure_stage"]
        or not isinstance(failure, Mapping)
        or failure.get("error_class") != retained["error_class"]
    ):
        raise _fail("c4_closure_checkpoint_shape_invalid")
    events_path = _exact_bytes_file(
        validation,
        closure.get("events_path"),
        closure.get("events_sha256"),
        "c4_closure_events",
    )
    events = _read_sealed_events(
        events_path,
        schema_version="membind.native-characterization-c4-event.v1",
        run_id=C4_RUN_ID,
        chained=False,
        code="c4_closure_events",
    )
    failures = [event for event in events if event.get("event_type") == "failure"]
    last = events[-1] if events else None
    if (
        len(events) != closure["event_count"]
        or sum(event.get("event_type") == "enqueue" for event in events) != 245
        or sum(event.get("event_type") == "publication" for event in events)
        != 490
        or len(failures) != closure["failure_event_count"]
        or last is None
        or last is not failures[-1]
        or last.get("event_sequence") != closure["last_failure_event_sequence"]
        or last.get("payload_sha256")
        != closure.get("last_failure_event_payload_sha256")
        or last.get("failure_scope") != "stage"
        or last.get("failure_stage") != retained["failure_stage"]
        or last.get("status") != retained["attempt_status"]
        or last.get("error_class") != retained["error_class"]
        or last.get("block_index") is not None
        or last.get("source_sequence") is not None
        or last.get("completed_block_count") != 10
        or last.get("completed_episode_count") != 490
    ):
        raise _fail("c4_closure_events_not_exact")
    return {
        "c4_checkpoint_sha256": str(closure["checkpoint_sha256"]),
        "c4_checkpoint_payload_sha256": str(
            closure["checkpoint_payload_sha256"]
        ),
        "c4_events_sha256": str(closure["events_sha256"]),
    }


def _validate_judge(summary: Mapping[str, Any], runtime: Mapping[str, Any]) -> None:
    identity = runtime.get("identity")
    public = identity.get("backend_public_config") if isinstance(identity, Mapping) else None
    confusion = summary.get("confusion_matrix")
    if (
        summary.get("schema_version") != "membind.judge-qualification-summary.v1"
        or summary.get("protocol_id") != "judge-qualification-v1.0"
        or summary.get("scientific_surface") != "JUDGE_QUALIFICATION_ONLY"
        or summary.get("run_id") != "jq-b00a9689796c1e67"
        or summary.get("attempt_status") != "complete"
        or summary.get("qualification_status") != "PASS"
        or summary.get("mergeable") is not True
        or [summary.get(name) for name in (
            "planned_item_count", "terminal_item_count", "eligible_item_count", "agreement_count"
        )] != [14, 14, 14, 14]
        or summary.get("observed_agreement") != 1.0
        or summary.get("cohens_kappa") != 1.0
        or any(summary.get(name) != 0 for name in (
            "invalid_output_count", "service_error_count", "retry_count_total"
        ))
        or confusion
        != {"true_positive": 7, "true_negative": 7, "false_positive": 0, "false_negative": 0}
        or runtime.get("schema_version") != "membind.judge-runtime-identity.v1"
        or runtime.get("run_id") != summary.get("run_id")
        or summary.get("runtime_identity_payload_sha256") != runtime.get("payload_sha256")
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
        raise _fail("judge_qualification_not_exact_pass")


def _validate_judge_closure(
    validation: Path,
    metadata: Mapping[str, Any],
    summary: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> dict[str, str]:
    closure = metadata.get("judge_closure")
    if not isinstance(closure, Mapping) or closure.get("event_count") != 28:
        raise _fail("judge_closure_missing")
    _manifest_path, manifest = _exact_file(
        validation,
        closure.get("manifest_path"),
        closure.get("manifest_sha256"),
        closure.get("manifest_payload_sha256"),
        "judge_closure_manifest",
    )
    freeze_path, freeze = _exact_file(
        validation,
        closure.get("fixture_freeze_path"),
        closure.get("fixture_freeze_sha256"),
        closure.get("fixture_freeze_payload_sha256"),
        "judge_closure_fixture_freeze",
    )
    _checkpoint_path, checkpoint = _exact_file(
        validation,
        closure.get("checkpoint_path"),
        closure.get("checkpoint_sha256"),
        closure.get("checkpoint_payload_sha256"),
        "judge_closure_checkpoint",
    )
    items = freeze.get("items")
    gate = freeze.get("strict_pass_gate")
    if (
        manifest.get("schema_version") != "membind.judge-qualification-run.v1"
        or manifest.get("protocol_id") != "judge-qualification-v1.0"
        or manifest.get("scientific_surface") != "JUDGE_QUALIFICATION_ONLY"
        or manifest.get("run_id") != JUDGE_RUN_ID
        or manifest.get("freeze_file_sha256") != _sha(freeze_path)
        or manifest.get("freeze_payload_sha256") != freeze.get("payload_sha256")
        or manifest.get("runtime_identity_file_sha256")
        != metadata.get("judge_runtime_identity_sha256")
        or manifest.get("runtime_identity_payload_sha256")
        != runtime.get("payload_sha256")
        or freeze.get("schema_version")
        != "membind.judge-qualification-freeze.v1"
        or freeze.get("protocol_id") != "judge-qualification-v1.0"
        or freeze.get("scientific_surface") != "JUDGE_QUALIFICATION_ONLY"
        or not isinstance(items, list)
        or len(items) != 14
        or not isinstance(gate, Mapping)
        or gate.get("planned_item_count") != 14
        or summary.get("freeze_payload_sha256") != freeze.get("payload_sha256")
        or summary.get("runtime_identity_payload_sha256")
        != manifest.get("runtime_identity_payload_sha256")
    ):
        raise _fail("judge_closure_binding_invalid")
    events_path = _exact_bytes_file(
        validation,
        closure.get("events_path"),
        closure.get("events_sha256"),
        "judge_closure_events",
    )
    events = _read_sealed_events(
        events_path,
        schema_version="membind.judge-qualification-event.v1",
        run_id=JUDGE_RUN_ID,
        chained=True,
        code="judge_closure_events",
    )
    last = events[-1] if events else None
    if (
        len(events) != closure["event_count"]
        or [event.get("event_type") for event in events]
        != ["dispatch_intent_durable", "terminal_success"] * 14
        or [event.get("item_index") for event in events]
        != [index for index in range(14) for _ in range(2)]
        or last is None
        or last.get("payload_sha256") != closure.get("last_event_payload_sha256")
        or checkpoint.get("schema_version")
        != "membind.judge-qualification-checkpoint.v1"
        or checkpoint.get("run_id") != JUDGE_RUN_ID
        or checkpoint.get("status") != "complete"
        or checkpoint.get("phase") != "finalized"
        or checkpoint.get("terminal_item_count") != 14
        or checkpoint.get("next_item_index") != 14
        or checkpoint.get("event_count") != len(events)
        or checkpoint.get("last_event_payload_sha256") != last["payload_sha256"]
        or checkpoint.get("freeze_payload_sha256") != freeze.get("payload_sha256")
        or checkpoint.get("runtime_identity_payload_sha256")
        != runtime.get("payload_sha256")
    ):
        raise _fail("judge_closure_checkpoint_invalid")
    return {
        "judge_manifest_sha256": str(closure["manifest_sha256"]),
        "judge_manifest_payload_sha256": str(closure["manifest_payload_sha256"]),
        "judge_fixture_freeze_sha256": str(closure["fixture_freeze_sha256"]),
        "judge_fixture_freeze_payload_sha256": str(
            closure["fixture_freeze_payload_sha256"]
        ),
        "judge_checkpoint_sha256": str(closure["checkpoint_sha256"]),
        "judge_checkpoint_payload_sha256": str(
            closure["checkpoint_payload_sha256"]
        ),
        "judge_events_sha256": str(closure["events_sha256"]),
    }


def _load_dataset(
    path: Path,
    source_sha: str,
    dependencies: "C5LiveDependencies",
    expected_hashes: Sequence[str],
) -> tuple[dict[str, Any], list[c5.Episode]]:
    if path.resolve(strict=True) != FROZEN_DATASET_PATH.resolve(strict=True) or _sha(path) != source_sha:
        raise _fail("dataset_source_hash_mismatch")
    records = dependencies.raw_dataset_loader(path)
    matches = [
        item
        for item in records
        if isinstance(item, dict) and str(item.get("question_id")) == FROZEN_HISTORY_ID
    ]
    if len(matches) != 1:
        raise _fail("frozen_history_missing")
    instance = matches[0]
    built = list(dependencies.episode_builder(instance))
    if (
        len(built) != core.FROZEN_EPISODE_COUNT
        or [item.source_sequence for item in built] != list(range(core.FROZEN_EPISODE_COUNT))
        or [item.source_hash for item in built] != list(expected_hashes)
        or any(item.question_id != FROZEN_HISTORY_ID for item in built)
    ):
        raise _fail("dataset_episode_contract_mismatch")
    return instance, [c5.Episode(item.source_sequence, item) for item in built]


async def _ensure_driver_ready(graphiti: Any) -> None:
    driver = getattr(graphiti, "driver", None)
    task = getattr(driver, "_init_task", None)
    if task is not None:
        await task
    else:
        ready = getattr(driver, "build_indices_and_constraints", None)
        if callable(ready):
            await ready()


async def _count_namespace(driver: Any, namespace: str) -> core.NamespaceCounts:
    query = """
CALL { MATCH (n) WHERE n.group_id = $group_id RETURN count(n) AS node_count }
CALL { MATCH ()-[r]->() WHERE r.group_id = $group_id RETURN count(r) AS relationship_count }
RETURN node_count, relationship_count
"""
    try:
        result = await driver.execute_query(query, params={"group_id": namespace})
        records = getattr(result, "records", None)
        if not isinstance(records, Sequence) or isinstance(records, (str, bytes)) or len(records) != 1:
            raise _fail("namespace_count_invalid")
        nodes = records[0]["node_count"]
        relationships = records[0]["relationship_count"]
    except C5LiveAdapterError:
        raise
    except Exception:
        raise _fail("namespace_count_failed") from None
    if (
        isinstance(nodes, bool)
        or not isinstance(nodes, int)
        or nodes < 0
        or isinstance(relationships, bool)
        or not isinstance(relationships, int)
        or relationships < 0
    ):
        raise _fail("namespace_count_invalid")
    return core.NamespaceCounts(nodes, relationships)


def _production_preflight(state_path: Path) -> Callable[[tuple[str, ...]], Awaitable[dict[str, core.NamespaceCounts]]]:
    async def preflight(namespaces: tuple[str, ...]) -> dict[str, core.NamespaceCounts]:
        def check(action: current_state_gate.LiveAction) -> current_state_gate.GateDecision:
            return current_state_gate.require_live_action(action, state_path=state_path)

        runtime = build_u0_graphiti_from_env(
            authorization_checker=check,
            live_action=current_state_gate.LiveAction.NATIVE_CHARACTERIZATION_C5,
            structured_output_mode="json_schema",
        )
        graphiti = runtime.graphiti
        try:
            await _ensure_driver_ready(graphiti)
            return {
                namespace: await _count_namespace(graphiti.driver, namespace)
                for namespace in namespaces
            }
        finally:
            close = getattr(graphiti, "close", None)
            if callable(close):
                result = close()
                if hasattr(result, "__await__"):
                    await result

    return preflight


def _production_runtime_factory_builder(
    *, state_path: Path, instance: Mapping[str, Any], episodes: Sequence[dataset.Episode], **_: Any
) -> Callable[[core.C5Block], Awaitable[core.GraphitiBlockRuntime]]:
    async def factory(block: core.C5Block) -> core.GraphitiBlockRuntime:
        def check(action: current_state_gate.LiveAction) -> current_state_gate.GateDecision:
            return current_state_gate.require_live_action(action, state_path=state_path)

        runtime = build_u0_graphiti_from_env(
            authorization_checker=check,
            live_action=current_state_gate.LiveAction.NATIVE_CHARACTERIZATION_C5,
            structured_output_mode="json_schema",
        )
        await _ensure_driver_ready(runtime.graphiti)
        adapter = core.GraphitiBlockRuntime(
            graphiti=runtime.graphiti,
            block=block,
            episodes=episodes,
            instance=instance,
            graph_exporter=export_canonical_graph,
            retrieval_evaluator=evaluate_retrieval,
        )

        async def scoped_clear() -> None:
            try:
                await clear_data(runtime.graphiti.driver, group_ids=[block.graph_namespace])
            except Exception:
                raise _fail("partial_namespace_clear_failed") from None

        adapter.clear_namespace = scoped_clear  # type: ignore[method-assign]
        return adapter

    return factory


def _load_env_once() -> None:
    from graphiti_native import load_env_file

    load_env_file()


class _ProductionQAEvaluator:
    """Callable supplemental Judge with an explicit transport lifecycle."""

    def __init__(
        self,
        backend: Qwen3JudgeBackend,
        evaluator: C5EvidenceAnswerabilityEvaluator,
    ) -> None:
        self._backend = backend
        self._evaluator = evaluator

    async def __call__(
        self, runtime: core.BlockRuntime, _block: core.C5Block
    ) -> Mapping[str, object]:
        if not isinstance(runtime, core.GraphitiBlockRuntime):
            raise _fail("qa_runtime_invalid")
        retrieval = runtime.cached_retrieval_result()
        results = retrieval.get("results")
        facts = [
            str(item.get("fact"))
            for item in results or []
            if isinstance(item, Mapping) and isinstance(item.get("fact"), str)
        ]
        instance = runtime.instance
        return await self._evaluator.evaluate(
            question_id=str(instance["question_id"]),
            question_type=str(instance["question_type"]),
            question=str(instance["question"]),
            reference_answer=str(instance["answer"]),
            retrieved_facts=facts,
            retrieval_payload_sha256=hashlib.sha256(
                json.dumps(
                    retrieval,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("ascii")
            ).hexdigest(),
        )

    async def aclose(self) -> None:
        await self._backend.aclose()


def _production_qa_evaluator() -> _ProductionQAEvaluator:
    _load_env_once()
    base_url = os.environ.get("CONSTRUCTION_LLM_BASE_URL")
    api_key = os.environ.get("CONSTRUCTION_LLM_API_KEY")
    if not base_url or not api_key:
        raise _fail("judge_private_config_missing")
    backend = Qwen3JudgeBackend(
        base_url=base_url,
        api_key=api_key,
        thinking_control="client_request",
        max_attempts=1,
    )
    evaluator = C5EvidenceAnswerabilityEvaluator(backend)
    return _ProductionQAEvaluator(backend, evaluator)


@dataclass(frozen=True)
class C5LiveDependencies:
    gate_checker: Callable[..., current_state_gate.GateDecision] = current_state_gate.require_live_action
    state_loader: Callable[[Path], Mapping[str, Any]] | None = None
    raw_dataset_loader: Callable[[Path], list[dict[str, Any]]] = dataset.load_json_records
    episode_builder: Callable[[dict[str, Any]], Sequence[dataset.Episode]] = dataset.build_episodes
    namespace_preflight: Callable[[tuple[str, ...]], Awaitable[dict[str, core.NamespaceCounts]]] | None = None
    runtime_factory_builder: Callable[..., Any] = _production_runtime_factory_builder
    store_factory: Callable[..., Any] = artifacts.C5LiveArtifactStore.create
    run_c5: Callable[..., Any] = core.run_c5_live_core
    qa_evaluator: Callable[..., Any] | None = None


def _safe(value: Any) -> bool:
    if isinstance(value, Mapping):
        return all(
            isinstance(key, str)
            and key.casefold() not in _FORBIDDEN_KEYS
            and _safe(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return all(_safe(item) for item in value)
    if value is None or isinstance(value, (bool, int, float)):
        return True
    return isinstance(value, str) and not _SECRET_RE.search(value) and "episode body" not in value.casefold()


def _progress_sink(stream: Any | None) -> Callable[[Mapping[str, Any]], None]:
    def sink(value: Mapping[str, Any]) -> None:
        if stream is None or not _safe(value):
            return
        try:
            stream.write(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()
        except Exception:
            return

    return sink


def _exact_paths(validation_root: Path, state_path: Path) -> tuple[Path, Path]:
    try:
        validation = validation_root.resolve(strict=True)
        state = state_path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise _fail("path_invalid") from None
    if validation != VALIDATION_ROOT.resolve() or state != DEFAULT_STATE_PATH.resolve():
        raise _fail("path_not_exact_c5_authorized_location")
    return validation, state


async def execute_c5_live(
    *,
    validation_root: str | Path = VALIDATION_ROOT,
    state_path: str | Path = DEFAULT_STATE_PATH,
    run_id: str | None = None,
    resume_run_id: str | None = None,
    dependencies: C5LiveDependencies | None = None,
    progress_stream: Any | None = sys.stdout,
) -> dict[str, object]:
    deps = dependencies or C5LiveDependencies()
    if (run_id is None) == (resume_run_id is None):
        raise _fail("exactly_one_run_id_required")
    selected = run_id or resume_run_id
    if not isinstance(selected, str) or _RUN_ID_RE.fullmatch(selected) is None:
        raise _fail("run_id_invalid")
    validation, state_path_resolved = _exact_paths(
        Path(validation_root), Path(state_path)
    )
    deps.gate_checker(
        current_state_gate.LiveAction.NATIVE_CHARACTERIZATION_C5,
        state_path=state_path_resolved,
    )
    lease = _C5RunLease.acquire(validation / RUNS_ROOT, selected)
    try:
        return await _execute_c5_live_locked(
            validation_root=validation_root,
            state_path=state_path,
            run_id=run_id,
            resume_run_id=resume_run_id,
            dependencies=deps,
            progress_stream=progress_stream,
        )
    finally:
        lease.close()


async def _execute_c5_live_locked(
    *,
    validation_root: str | Path = VALIDATION_ROOT,
    state_path: str | Path = DEFAULT_STATE_PATH,
    run_id: str | None = None,
    resume_run_id: str | None = None,
    dependencies: C5LiveDependencies | None = None,
    progress_stream: Any | None = sys.stdout,
) -> dict[str, object]:
    deps = dependencies or C5LiveDependencies()
    if (run_id is None) == (resume_run_id is None):
        raise _fail("exactly_one_run_id_required")
    selected = run_id or resume_run_id
    if not isinstance(selected, str) or _RUN_ID_RE.fullmatch(selected) is None:
        raise _fail("run_id_invalid")
    validation, state_path_resolved = _exact_paths(Path(validation_root), Path(state_path))
    state = (
        deps.state_loader(state_path_resolved)
        if deps.state_loader is not None
        else _read_json(state_path_resolved, "state_invalid")
    )
    metadata = _validate_state(state)
    _validate_tcb(validation, metadata)
    _freeze_path, freeze = _exact_file(
        validation,
        metadata.get("freeze_path"),
        metadata.get("freeze_sha256"),
        metadata.get("freeze_payload_sha256"),
        "freeze",
    )
    expected_hashes = _validate_freeze(freeze, metadata)
    _c4_path, c4_value = _exact_file(
        validation,
        metadata.get("c4_summary_path"),
        metadata.get("c4_summary_sha256"),
        metadata.get("c4_summary_payload_sha256"),
        "c4_summary",
    )
    _validate_c4(c4_value)
    c4_closure_provenance = _validate_c4_closure(validation, metadata, c4_value)
    _judge_summary_path, judge_summary = _exact_file(
        validation,
        metadata.get("judge_qualification_summary_path"),
        metadata.get("judge_qualification_summary_sha256"),
        metadata.get("judge_qualification_summary_payload_sha256"),
        "judge_summary",
    )
    _judge_runtime_path, judge_runtime = _exact_file(
        validation,
        metadata.get("judge_runtime_identity_path"),
        metadata.get("judge_runtime_identity_sha256"),
        metadata.get("judge_runtime_identity_payload_sha256"),
        "judge_runtime",
    )
    _validate_judge(judge_summary, judge_runtime)
    judge_closure_provenance = _validate_judge_closure(
        validation,
        metadata,
        judge_summary,
        judge_runtime,
    )
    dataset_spec = freeze.get("dataset")
    source_sha = dataset_spec.get("source_sha256") if isinstance(dataset_spec, Mapping) else None
    if not isinstance(source_sha, str):
        raise _fail("dataset_source_hash_missing")
    instance, episodes = _load_dataset(
        FROZEN_DATASET_PATH, source_sha, deps, expected_hashes
    )
    schedule = core.load_frozen_e4_schedule(
        freeze,
        run_id=selected,
        episode_source_hashes=expected_hashes,
    )
    provenance = {
        "freeze_sha256": str(metadata["freeze_sha256"]),
        "freeze_payload_sha256": str(metadata["freeze_payload_sha256"]),
        "dataset_source_sha256": source_sha,
        "c4_summary_sha256": str(metadata["c4_summary_sha256"]),
        "c4_summary_payload_sha256": str(metadata["c4_summary_payload_sha256"]),
        "judge_qualification_summary_sha256": str(metadata["judge_qualification_summary_sha256"]),
        "judge_qualification_summary_payload_sha256": str(metadata["judge_qualification_summary_payload_sha256"]),
        "judge_runtime_identity_sha256": str(metadata["judge_runtime_identity_sha256"]),
        "judge_runtime_identity_payload_sha256": str(metadata["judge_runtime_identity_payload_sha256"]),
        **c4_closure_provenance,
        **judge_closure_provenance,
    }
    preflight = deps.namespace_preflight or _production_preflight(state_path_resolved)
    observed = await preflight(tuple(FROZEN_NAMESPACES))
    if set(observed) != set(FROZEN_NAMESPACES) or any(
        not isinstance(value, core.NamespaceCounts) for value in observed.values()
    ):
        raise _fail("namespace_preflight_invalid")

    resume_prefix: core.C5ResumePrefix | None = None
    run_root = validation / RUNS_ROOT / selected
    if run_id is not None:
        if any(not value.is_empty for value in observed.values()):
            raise _fail("namespace_not_empty")
        store = deps.store_factory(
            runs_root=validation / RUNS_ROOT,
            run_id=selected,
            schedule=schedule,
            provenance_hashes=provenance,
            command_argv=["native-characterization-c5-live", "--run-id", selected],
        )
    else:
        verification = artifacts.verify_c5_live_artifacts(run_root)
        try:
            if (
                verification.get("attempt_status")
                == artifacts.INCOMPLETE_NON_MERGEABLE
                and int(verification.get("failure_event_count", 0)) == 1
            ):
                artifacts.recover_c5_terminal_failure_to_resume_prefix(
                    run_dir=run_root,
                    schedule=schedule,
                    provenance_hashes=provenance,
                )
            else:
                artifacts.prepare_c5_running_resume_prefix(
                    run_dir=run_root,
                    schedule=schedule,
                    provenance_hashes=provenance,
                )
            inspection = artifacts.inspect_c5_resume_prefix(
                run_root,
                expected_run_id=selected,
                expected_schedule=schedule,
                expected_provenance_hashes=provenance,
            )
        except artifacts.C5LiveArtifactError as error:
            raise _fail("resume_artifact_recovery_failed") from error
        completed = set(inspection.completed_block_indices)
        partial = inspection.partial_block_index
        for index, namespace in enumerate(FROZEN_NAMESPACES):
            count = observed[namespace]
            if index in completed and count.is_empty:
                raise _fail("completed_namespace_missing")
            if index not in completed and index != partial and not count.is_empty:
                raise _fail("future_namespace_not_empty")
        reference = (
            core.serial_reference_from_artifact(inspection.serial_reference)
            if inspection.serial_reference is not None
            else None
        )
        resume_prefix = core.C5ResumePrefix(
            completed_block_indices=inspection.completed_block_indices,
            partial_block_index=partial,
            serial_reference=reference,
            completed_block_results=inspection.completed_block_results,
        )
        store = artifacts.C5LiveArtifactStore.open_existing(run_root)

    runtime_factory = deps.runtime_factory_builder(
        state_path=state_path_resolved,
        instance=instance,
        episodes=[item.payload for item in episodes],
        graph_namespaces=tuple(FROZEN_NAMESPACES),
    )
    qa = deps.qa_evaluator or _production_qa_evaluator()
    sink = _progress_sink(progress_stream)
    sink({"event": "c5_start", "run_id": selected, "resume": resume_run_id is not None})
    try:
        result = await deps.run_c5(
            schedule=schedule,
            episodes=episodes,
            episode_source_hashes=expected_hashes,
            runtime_factory=runtime_factory,
            store=store,
            now_ns=time.monotonic_ns,
            provenance_hashes=provenance,
            resume_prefix=resume_prefix,
            qa_evaluator=qa,
        )
    finally:
        try:
            close_qa = getattr(qa, "aclose", None)
            if callable(close_qa):
                closed = close_qa()
                if hasattr(closed, "__await__"):
                    await closed
        finally:
            close_store = getattr(store, "close", None)
            if callable(close_store):
                close_store()
    sink(
        {
            "event": "c5_terminal",
            "status": result.get("status"),
            "completed_block_indices": result.get("completed_block_indices", []),
        }
    )
    return dict(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the authorized frozen C5/E4 screening")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-id")
    group.add_argument("--resume-run-id")
    parser.add_argument("--validation-root", type=Path, default=VALIDATION_ROOT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = asyncio.run(
            execute_c5_live(
                validation_root=args.validation_root,
                state_path=args.state,
                run_id=args.run_id,
                resume_run_id=args.resume_run_id,
            )
        )
    except Exception as error:
        code = error.code if isinstance(error, C5LiveAdapterError) else "c5_live_failed"
        print(
            json.dumps(
                {"status": "error", "error_class": type(error).__name__, "code": code},
                ensure_ascii=True,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, default=str))
    return 0


__all__ = [
    "C5LiveAdapterError",
    "C5LiveDependencies",
    "DEFAULT_STATE_PATH",
    "FROZEN_DATASET_PATH",
    "VALIDATION_ROOT",
    "_progress_sink",
    "build_parser",
    "execute_c5_live",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
