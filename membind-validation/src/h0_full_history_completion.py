"""Validate the complete Q1/H0-B terminal before authorizing H0-C.

The validator is offline-only.  It reopens the content-addressed checkpoint
graph, verifies every source checkpoint and final stage projection, and returns
only the immutable binding needed by the next state transition.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import re
import json
from pathlib import Path
from typing import Any, Mapping

from h0_runtime import (
    H0CheckpointStore,
    H0ManifestError,
    canonical_json_sha256,
    sha256_file,
)


PROTOCOL_VERSION = "current-validation-v1.3"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_READINESS_CHECKS = (
    "vllm_version",
    "served_model",
    "health",
    "construction_ready",
    "embedding_ready",
    "neo4j_ready",
    "authorization_recheck",
)
_PREWORKLOAD_STAGES = (
    "corpus_ready",
    "history_factory_ready",
    "graph_construction_started",
    "graph_construction_ready",
)
_QUESTION_ID = "07741c45"
_SOURCE_COUNT = 49


def _checkpoint_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")


class H0FullHistoryCompletionValidationError(H0ManifestError):
    """A sanitized denial of full-history terminal qualification."""


def _fail(reason: str) -> H0FullHistoryCompletionValidationError:
    return H0FullHistoryCompletionValidationError(
        f"H0 full-history completion validation denied: {reason}"
    )


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _fail(f"{label}_sha256_invalid")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(f"{label}_not_object")
    return value


def _count(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _fail(f"{label}_invalid")
    return value


def _validate_ledger(
    value: Any,
    *,
    stage_attempt_id: str,
    previous_count: int | None = None,
) -> int:
    ledger = _mapping(value, "attempt_ledger")
    if not (
        ledger.get("schema_version") == "membind.h0.attempt-ledger.v1"
        and ledger.get("protocol_version") == PROTOCOL_VERSION
        and ledger.get("stage_attempt_id") == stage_attempt_id
        and ledger.get("secrets_persisted") is False
        and ledger.get("raw_prompts_persisted") is False
        and ledger.get("raw_responses_persisted") is False
    ):
        raise _fail("attempt_ledger_identity_mismatch")
    trials = ledger.get("logical_trials")
    attempts = ledger.get("http_attempts")
    if not isinstance(trials, list) or not isinstance(attempts, list):
        raise _fail("attempt_ledger_lists_invalid")
    count = len(trials)
    if count < 1 or len(attempts) != count:
        raise _fail("attempt_ledger_counts_mismatch")
    if previous_count is not None and count <= previous_count:
        raise _fail("source_ledger_not_strictly_cumulative")
    trial_by_id: dict[str, Mapping[str, Any]] = {}
    for trial in trials:
        item = _mapping(trial, "logical_trial")
        logical_id = item.get("logical_trial_id")
        attempt_ids = item.get("attempt_ids")
        if not (
            isinstance(logical_id, str)
            and logical_id
            and logical_id not in trial_by_id
            and item.get("candidate_id") == "Q1"
            and item.get("repeated_trial_index") == 0
            and item.get("statistically_independent") is False
            and isinstance(attempt_ids, list)
            and len(attempt_ids) == 1
            and isinstance(attempt_ids[0], str)
        ):
            raise _fail("logical_trial_contract_mismatch")
        trial_by_id[logical_id] = item
    seen_http: set[str] = set()
    for attempt in attempts:
        item = _mapping(attempt, "http_attempt")
        logical_id = item.get("logical_trial_id")
        http_id = item.get("http_attempt_id")
        if not (
            isinstance(http_id, str)
            and http_id
            and http_id not in seen_http
            and logical_id in trial_by_id
            and trial_by_id[str(logical_id)].get("attempt_ids") == [http_id]
            and item.get("retry_index") == 0
            and item.get("retry_same_logical_trial") is False
            and item.get("completed") is True
            and item.get("http_status") == 200
            and item.get("http_200") is True
            and item.get("finish_reason") != "length"
            and item.get("finish_non_length") is True
            and item.get("json_parse_success") is True
            and item.get("pydantic_validation_success") is True
            and item.get("semantic_utility_success") is True
            and item.get("failure_class") is None
        ):
            raise _fail("http_attempt_contract_mismatch")
        seen_http.add(http_id)
    return count


def _validate_runtime_evidence(value: Any, *, final: bool) -> None:
    evidence = _mapping(value, "runtime_evidence")
    if not (
        evidence.get("fresh_graph_count") == 1
        and evidence.get("cross_encoder_rank_call_count") == 0
        and evidence.get("secrets_persisted") is False
        and evidence.get("raw_prompts_persisted") is False
        and evidence.get("raw_responses_persisted") is False
    ):
        raise _fail("runtime_evidence_contract_mismatch")
    closed = evidence.get("closed_graph_count")
    if isinstance(closed, bool) or not isinstance(closed, int) or closed not in {0, 1}:
        raise _fail("runtime_evidence_closed_count_invalid")
    if final and closed != 1:
        raise _fail("runtime_evidence_final_graph_not_closed")
    _count(
        evidence.get("embedding_workload_request_count"),
        "embedding_workload_request_count",
    )


def _artifact_payload(artifact_root: Path, entry: Mapping[str, Any]) -> Mapping[str, Any]:
    relative = entry.get("artifact_path")
    digest = _sha(entry.get("artifact_sha256"), "segment")
    if not isinstance(relative, str):
        raise _fail("segment_path_invalid")
    expected_name = (
        f"{entry.get('segment_ordinal'):06d}.{entry.get('segment_kind')}."
        f"{entry.get('segment_id')}.{digest}.json"
    )
    if Path(relative).name != expected_name:
        raise _fail("segment_content_address_name_mismatch")
    path = (artifact_root / relative).resolve()
    try:
        path.relative_to(artifact_root.resolve())
    except ValueError:
        raise _fail("segment_path_escape") from None
    try:
        artifact = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail("segment_artifact_invalid") from None
    return _mapping(artifact.get("payload"), "segment_payload")


def validate_h0_b_terminal_completion(
    *,
    root: str | Path,
    stage_attempt_id: str,
    checkpoint_index_path: str,
    checkpoint_index_sha256: str,
    candidate_id: str,
    runtime_definition_sha256: str,
) -> dict[str, Any]:
    """Return a safe H0-B terminal binding or fail closed."""

    if (
        not isinstance(stage_attempt_id, str)
        or _IDENTIFIER_RE.fullmatch(stage_attempt_id) is None
        or candidate_id != "Q1"
    ):
        raise _fail("candidate_or_attempt_invalid")
    runtime_sha = _sha(runtime_definition_sha256, "runtime_definition")
    expected_index_sha = _sha(checkpoint_index_sha256, "checkpoint_index")
    root_path = Path(root).resolve()
    relative = Path(checkpoint_index_path)
    expected_tail = Path("h0/checkpoints") / stage_attempt_id / "index.json"
    if (
        relative.is_absolute()
        or relative.as_posix() != checkpoint_index_path
        or any(part in {"", ".", "..", ".env", "gpt55_temporary"} for part in relative.parts)
        or len(relative.parts) <= len(expected_tail.parts)
        or tuple(relative.parts[-len(expected_tail.parts) :]) != expected_tail.parts
    ):
        raise _fail("checkpoint_index_path_invalid")
    artifact_prefix = Path(*relative.parts[: -len(expected_tail.parts)])
    artifact_root = (root_path / artifact_prefix).resolve()
    index_path = (root_path / relative).resolve()
    try:
        index_path.relative_to(root_path)
    except ValueError:
        raise _fail("checkpoint_index_path_escape") from None
    if index_path.is_symlink() or not index_path.is_file():
        raise _fail("checkpoint_index_missing")
    if sha256_file(index_path) != expected_index_sha:
        raise _fail("checkpoint_index_hash_mismatch")

    try:
        store = H0CheckpointStore.open_existing(artifact_root, stage_attempt_id)
    except (H0ManifestError, OSError, ValueError) as exc:
        raise _fail("checkpoint_graph_invalid") from exc
    if store.index_path.resolve() != index_path:
        raise _fail("checkpoint_index_root_mismatch")
    if index_path.read_bytes() != _checkpoint_json_bytes(store.index):
        raise _fail("checkpoint_index_not_canonical")
    index = store.index
    if not (
        index.get("candidate_id") == "Q1"
        and index.get("phase") == "H0-B"
        and index.get("status") == "stage_complete"
        and index.get("candidate_advance_allowed") is True
        and index.get("partial_qualification_reusable") is True
        and index.get("requires_whole_stage_rerun") is False
    ):
        raise _fail("checkpoint_terminal_contract_mismatch")

    expected_keys = [
        ("prior_phase_completion", "qualified"),
        *(
            ("stage_readiness_check", f"{ordinal:03d}-{check}")
            for ordinal, check in enumerate(_READINESS_CHECKS)
        ),
        ("stage_readiness_result", "ready"),
        *(("preworkload_progress", stage) for stage in _PREWORKLOAD_STAGES),
        *(
            ("source_sequence", f"{_QUESTION_ID}-{sequence:03d}")
            for sequence in range(_SOURCE_COUNT)
        ),
        ("history_result", _QUESTION_ID),
        ("stage_result", "qualified"),
    ]
    entries = index.get("segments")
    if not isinstance(entries, list) or len(entries) != len(expected_keys):
        raise _fail("checkpoint_segment_count_mismatch")
    payloads: list[Mapping[str, Any]] = []
    for ordinal, (entry, expected_key) in enumerate(zip(entries, expected_keys)):
        item = _mapping(entry, "segment_entry")
        if not (
            item.get("segment_ordinal") == ordinal
            and (item.get("segment_kind"), item.get("segment_id")) == expected_key
        ):
            raise _fail("checkpoint_segment_order_mismatch")
        payloads.append(_artifact_payload(artifact_root, item))

    prior = payloads[0]
    if not (
        prior.get("schema_version")
        == "membind.h0.prior-phase-terminal-completion.v1"
        and prior.get("qualified") is True
        and prior.get("candidate_id") == "Q1"
        and prior.get("phase") == "H0-A"
        and prior.get("secrets_persisted") is False
    ):
        raise _fail("prior_phase_completion_mismatch")
    _sha(prior.get("terminal_result_sha256"), "prior_terminal_result")
    prior_sha = canonical_json_sha256(prior)

    for check, payload in zip(_READINESS_CHECKS, payloads[1:8]):
        if not (
            payload.get("schema_version") == "membind.h0.stage-readiness-event.v1"
            and payload.get("stage_attempt_id") == stage_attempt_id
            and payload.get("candidate_id") == "Q1"
            and payload.get("phase") == "H0-B"
            and payload.get("check") == check
            and payload.get("qualified") is True
            and payload.get("secrets_persisted") is False
        ):
            raise _fail("stage_readiness_event_mismatch")
    readiness = payloads[8]
    if not (
        readiness.get("schema_version") == "membind.h0.stage-readiness.v1"
        and readiness.get("stage_attempt_id") == stage_attempt_id
        and readiness.get("candidate_id") == "Q1"
        and readiness.get("phase") == "H0-B"
        and readiness.get("status") == "ready"
        and readiness.get("construction_readiness_count") == 1
        and readiness.get("embedding_readiness_count") == 1
        and readiness.get("neo4j_readiness_count") == 1
        and readiness.get("authorization_recheck_count") == 1
        and readiness.get("generation_requests") == 0
        and readiness.get("embedding_request_count") == 0
        and readiness.get("per_history_warmup_count") == 0
        and readiness.get("secrets_persisted") is False
    ):
        raise _fail("stage_readiness_result_mismatch")
    readiness_sha = canonical_json_sha256(readiness)

    for stage, payload in zip(_PREWORKLOAD_STAGES, payloads[9:13]):
        expected_question_id = (
            _QUESTION_ID if stage.startswith("graph_construction_") else None
        )
        if not (
            payload.get("schema_version")
            == "membind.h0.preworkload-progress.v1"
            and payload.get("protocol_version") == PROTOCOL_VERSION
            and payload.get("stage_attempt_id") == stage_attempt_id
            and payload.get("candidate_id") == "Q1"
            and payload.get("phase") == "H0-B"
            and payload.get("stage") == stage
            and payload.get("question_id") == expected_question_id
            and payload.get("generation_request_count") == 0
            and payload.get("embedding_request_count") == 0
            and payload.get("secrets_persisted") is False
        ):
            raise _fail("preworkload_progress_mismatch")

    previous_ledger_count: int | None = None
    final_ledger: Mapping[str, Any] | None = None
    source_offset = 9 + len(_PREWORKLOAD_STAGES)
    for sequence, payload in enumerate(
        payloads[source_offset : source_offset + _SOURCE_COUNT]
    ):
        checkpoint = _mapping(payload.get("phase_checkpoint"), "phase_checkpoint")
        if not (
            checkpoint.get("schema_version") == "membind.h0.phase-checkpoint.v1"
            and checkpoint.get("stage_attempt_id") == stage_attempt_id
            and checkpoint.get("question_id") == _QUESTION_ID
            and checkpoint.get("source_sequence") == sequence
            and checkpoint.get("retry_count") == 0
            and checkpoint.get("final_stage_checks_passed") is (sequence == _SOURCE_COUNT - 1)
            and checkpoint.get("secrets_persisted") is False
            and payload.get("runtime_definition_sha256") == runtime_sha
            and payload.get("prior_phase_completion_sha256") == prior_sha
        ):
            raise _fail("source_checkpoint_contract_mismatch")
        previous_ledger_count = _validate_ledger(
            payload.get("attempt_ledger"),
            stage_attempt_id=stage_attempt_id,
            previous_count=previous_ledger_count,
        )
        _validate_runtime_evidence(payload.get("runtime_evidence"), final=False)
        final_ledger = _mapping(payload.get("attempt_ledger"), "source_attempt_ledger")

    history_payload = payloads[-2]
    history_result = _mapping(history_payload.get("history_result"), "history_result")
    history_hash = _sha(history_payload.get("history_result_sha256"), "history_result")
    if not (
        canonical_json_sha256(history_result) == history_hash
        and history_result.get("schema_version")
        == "membind.h0.full-history-evidence.v1"
        and history_result.get("stage_attempt_id") == stage_attempt_id
        and history_result.get("question_id") == _QUESTION_ID
        and history_result.get("qualified") is True
        and history_payload.get("runtime_definition_sha256") == runtime_sha
        and history_payload.get("attempt_ledger") == final_ledger
    ):
        raise _fail("history_result_contract_mismatch")
    _validate_runtime_evidence(history_payload.get("runtime_evidence"), final=True)

    terminal_payload = payloads[-1]
    phase_result = _mapping(terminal_payload.get("phase_result"), "phase_result")
    terminal_hash = _sha(index.get("terminal_result_sha256"), "terminal_result")
    if not (
        terminal_payload.get("phase_result_sha256") == terminal_hash
        and canonical_json_sha256(phase_result) == terminal_hash
        and phase_result.get("schema_version")
        == "membind.h0.full-history-phase-result.v1"
        and phase_result.get("stage_attempt_id") == stage_attempt_id
        and phase_result.get("phase") == "H0-B"
        and phase_result.get("qualified") is True
        and phase_result.get("completed_history_count") == 1
        and phase_result.get("completed_histories")
        == [{"question_id": _QUESTION_ID, "evidence_sha256": history_hash}]
        and phase_result.get("partial_qualification_reusable") is True
        and terminal_payload.get("attempt_ledger") == final_ledger
        and terminal_payload.get("runtime_definition_sha256") == runtime_sha
        and terminal_payload.get("prior_phase_completion_sha256") == prior_sha
        and terminal_payload.get("stage_readiness_sha256") == readiness_sha
    ):
        raise _fail("terminal_phase_result_contract_mismatch")
    _validate_runtime_evidence(terminal_payload.get("runtime_evidence"), final=True)

    terminal_entry = _mapping(entries[-1], "terminal_entry")
    return {
        "schema_version": "membind.h0.full-history-terminal-completion.v1",
        "protocol_version": PROTOCOL_VERSION,
        "status": "qualified_terminal_completion",
        "qualified": True,
        "candidate_id": "Q1",
        "phase": "H0-B",
        "stage_attempt_id": stage_attempt_id,
        "checkpoint_index_path": relative.as_posix(),
        "checkpoint_index_sha256": expected_index_sha,
        "terminal_segment_path": (
            artifact_prefix / str(terminal_entry["artifact_path"])
        ).as_posix(),
        "terminal_segment_sha256": terminal_entry["artifact_sha256"],
        "terminal_result_sha256": terminal_hash,
        "runtime_definition_sha256": runtime_sha,
        "source_checkpoint_count": _SOURCE_COUNT,
        "completed_history_count": 1,
        "candidate_advance_allowed": True,
        "partial_qualification_reusable": True,
        "requires_whole_stage_rerun": False,
        "secrets_persisted": False,
        "raw_prompts_persisted": False,
        "raw_responses_persisted": False,
    }
