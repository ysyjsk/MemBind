"""Validate a content-addressed H0 prior-phase terminal completion.

The validator is offline-only and accepts no implicit state, environment, or
service configuration.  It returns a sanitized binding only after the complete
checkpoint index and every indexed segment have been revalidated from bytes.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from h0_runtime import H0ManifestError, canonical_json_sha256


PROTOCOL_VERSION = "current-validation-v1.3"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_FORBIDDEN_PARTS = {".env", "gpt55_temporary"}
_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "credentials",
    "env_dump",
    "environment_dump",
    "environ",
    "messages",
    "process_environment",
    "prompt",
    "raw_prompt",
    "raw_prompts",
    "raw_response",
    "raw_responses",
    "request_headers",
    "response_body",
    "response_text",
    "secret",
}
_EXPECTED_SEGMENTS = (
    ("readiness_check", "000-vllm_version"),
    ("readiness_check", "001-served_model"),
    ("readiness_check", "002-health"),
    ("readiness_result", "ready"),
    ("logical_trial", "trial-000"),
    ("logical_trial", "trial-001"),
    ("logical_trial", "trial-002"),
    ("stage_result", "qualified"),
)
# This attempt is permanently ineligible despite internally consistent technical
# observations; its protocol disposition is bound by the v1.3 invalidation record.
_PROTOCOL_INVALIDATED_ATTEMPT_IDS = frozenset(
    {"h0-q1-a-20260809-attempt-001"}
)


class H0CompletionValidationError(H0ManifestError):
    """A sanitized denial of prior-phase completion qualification."""


def _fail(reason: str) -> H0CompletionValidationError:
    return H0CompletionValidationError(f"H0 completion validation denied: {reason}")


def _checkpoint_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")


def _require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _fail(f"{label}_invalid")
    return value


def _assert_safe(value: Any, *, location: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS:
                raise _fail(f"unsafe_field_at_{location}")
            _assert_safe(child, location=f"{location}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_safe(child, location=f"{location}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.casefold()
        if "bearer " in lowered or ".env" in lowered or "gpt55_temporary" in lowered:
            raise _fail(f"unsafe_value_at_{location}")


def _canonical_relative(value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise _fail(f"{label}_path_invalid")
    relative = Path(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
        or any(part in _FORBIDDEN_PARTS for part in relative.parts)
    ):
        raise _fail(f"{label}_path_noncanonical")
    return relative


def _bound_file(
    root: Path, relative_value: Any, digest_value: Any, *, label: str
) -> tuple[Path, Path, str, bytes]:
    relative = _canonical_relative(relative_value, label=label)
    digest = _require_sha256(digest_value, f"{label}_sha256")
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise _fail(f"{label}_symlink_forbidden")
    path = (root / relative).resolve()
    try:
        normalized = path.relative_to(root).as_posix()
    except ValueError:
        raise _fail(f"{label}_path_escape") from None
    if normalized != relative.as_posix() or not path.is_file():
        raise _fail(f"{label}_missing_or_noncanonical")
    try:
        encoded = path.read_bytes()
    except OSError:
        raise _fail(f"{label}_unreadable") from None
    if hashlib.sha256(encoded).hexdigest() != digest:
        raise _fail(f"{label}_hash_mismatch")
    return path, relative, digest, encoded


def _read_checkpoint_object(encoded: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(encoded.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError):
        raise _fail(f"{label}_invalid_json") from None
    if not isinstance(value, dict) or encoded != _checkpoint_json_bytes(value):
        raise _fail(f"{label}_not_canonical_json")
    _assert_safe(value, location=label)
    return value


def _require_exact_count(value: Any, expected: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise _fail(f"{label}_mismatch")


def _validate_terminal_payload(
    payload: Mapping[str, Any],
    *,
    stage_attempt_id: str,
    runtime_definition_sha256: str,
    terminal_result_sha256: str,
) -> str:
    required = {
        "phase_result",
        "phase_result_sha256",
        "attempt_ledger",
        "runtime_evidence",
        "runtime_definition_sha256",
    }
    if set(payload) != required:
        raise _fail("terminal_payload_fields_mismatch")
    phase_result = payload.get("phase_result")
    if not isinstance(phase_result, Mapping):
        raise _fail("terminal_phase_result_invalid")
    phase_result_sha256 = _require_sha256(
        payload.get("phase_result_sha256"), "terminal_phase_result"
    )
    calculated = canonical_json_sha256(phase_result)
    if not (
        calculated == phase_result_sha256 == terminal_result_sha256
        and phase_result.get("schema_version") == "membind.h0.phase-result.v1"
        and phase_result.get("stage_attempt_id") == stage_attempt_id
        and phase_result.get("phase") == "H0-A"
        and phase_result.get("question_id") == "07741c45"
        and phase_result.get("qualified") is True
        and phase_result.get("secrets_persisted") is False
    ):
        raise _fail("terminal_result_hash_or_identity_mismatch")
    for field, expected in (
        ("logical_call_count", 3),
        ("http_attempt_count", 3),
        ("semantic_record_count", 3),
        ("retry_count", 0),
    ):
        _require_exact_count(phase_result.get(field), expected, f"phase_result_{field}")

    expected_runtime = _require_sha256(
        runtime_definition_sha256, "expected_runtime_definition"
    )
    if payload.get("runtime_definition_sha256") != expected_runtime:
        raise _fail("runtime_definition_hash_mismatch")
    ledger = payload.get("attempt_ledger")
    if not isinstance(ledger, Mapping) or not (
        ledger.get("schema_version") == "membind.h0.attempt-ledger.v1"
        and ledger.get("stage_attempt_id") == stage_attempt_id
        and ledger.get("secrets_persisted") is False
        and ledger.get("raw_prompts_persisted") is False
        and ledger.get("raw_responses_persisted") is False
    ):
        raise _fail("terminal_ledger_identity_mismatch")
    trials = ledger.get("logical_trials")
    attempts = ledger.get("http_attempts")
    if not isinstance(trials, list) or not isinstance(attempts, list):
        raise _fail("terminal_ledger_lists_invalid")
    if len(trials) != 3 or len(attempts) != 3:
        raise _fail("terminal_ledger_counts_mismatch")
    if any(not isinstance(trial, Mapping) for trial in trials):
        raise _fail("terminal_logical_trials_mismatch")
    repeated_indices = [trial.get("repeated_trial_index") for trial in trials]
    if any(
        isinstance(value, bool) or not isinstance(value, int)
        for value in repeated_indices
    ) or repeated_indices != [0, 1, 2] or any(
        trial.get("candidate_id") != "Q1" for trial in trials
    ):
        raise _fail("terminal_logical_trials_mismatch")
    if any(
        not isinstance(attempt, Mapping)
        or attempt.get("candidate_id") != "Q1"
        or attempt.get("http_200") is not True
        or attempt.get("semantic_utility_success") is not True
        for attempt in attempts
    ):
        raise _fail("terminal_http_attempts_mismatch")
    for attempt in attempts:
        _require_exact_count(attempt.get("retry_index"), 0, "http_attempt_retry_index")

    evidence = payload.get("runtime_evidence")
    if not isinstance(evidence, Mapping) or not (
        evidence.get("secrets_persisted") is False
        and evidence.get("raw_prompts_persisted") is False
        and evidence.get("raw_responses_persisted") is False
    ):
        raise _fail("terminal_runtime_evidence_mismatch")
    for field, expected in (
        ("fresh_client_count", 3),
        ("db_calls", 0),
        ("embedding_calls", 0),
    ):
        _require_exact_count(evidence.get(field), expected, f"runtime_evidence_{field}")
    return expected_runtime


def validate_h0_prior_phase_terminal_completion(
    *,
    root: str | Path,
    stage_attempt_id: str,
    checkpoint_index_path: str,
    checkpoint_index_sha256: str,
    candidate_id: str,
    phase: str,
    runtime_definition_sha256: str,
) -> dict[str, Any]:
    """Return a safe Q1/H0-A completion binding or fail closed."""

    if (
        not isinstance(stage_attempt_id, str)
        or _IDENTIFIER_RE.fullmatch(stage_attempt_id) is None
        or candidate_id != "Q1"
        or phase != "H0-A"
    ):
        raise _fail("expected_candidate_phase_or_attempt_invalid")
    if stage_attempt_id in _PROTOCOL_INVALIDATED_ATTEMPT_IDS:
        raise _fail("protocol_invalidated_attempt")
    expected_runtime = _require_sha256(
        runtime_definition_sha256, "expected_runtime_definition"
    )
    root_path = Path(root).resolve()
    index_path, index_relative, index_digest, encoded = _bound_file(
        root_path,
        checkpoint_index_path,
        checkpoint_index_sha256,
        label="checkpoint_index",
    )
    expected_tail = ("h0", "checkpoints", stage_attempt_id, "index.json")
    parts = index_relative.parts
    if len(parts) < len(expected_tail) or tuple(parts[-len(expected_tail) :]) != expected_tail:
        raise _fail("checkpoint_index_path_unexpected")
    artifact_prefix = Path(*parts[: -len(expected_tail)])
    attempt_root = index_path.parent
    index = _read_checkpoint_object(encoded, label="checkpoint_index")
    terminal_result_sha256 = _require_sha256(
        index.get("terminal_result_sha256"), "terminal_result"
    )
    if not (
        index.get("schema_version") == "membind.h0.checkpoint-index.v1"
        and index.get("protocol_version") == PROTOCOL_VERSION
        and index.get("stage_attempt_id") == stage_attempt_id
        and index.get("candidate_id") == candidate_id
        and index.get("phase") == phase
        and index.get("status") == "stage_complete"
        and index.get("candidate_advance_allowed") is True
        and index.get("partial_qualification_reusable") is True
        and index.get("requires_whole_stage_rerun") is False
        and index.get("secrets_persisted") is False
        and index.get("raw_prompts_persisted") is False
        and index.get("raw_responses_persisted") is False
    ):
        raise _fail("checkpoint_index_terminal_contract_mismatch")
    entries = index.get("segments")
    if not isinstance(entries, list) or len(entries) != len(_EXPECTED_SEGMENTS):
        raise _fail("checkpoint_segment_count_mismatch")

    indexed_paths: set[Path] = set()
    terminal_artifact: dict[str, Any] | None = None
    terminal_relative: Path | None = None
    terminal_digest: str | None = None
    for ordinal, (entry, expected_key) in enumerate(zip(entries, _EXPECTED_SEGMENTS)):
        if not isinstance(entry, Mapping):
            raise _fail("checkpoint_segment_entry_invalid")
        kind, segment_id = expected_key
        digest = _require_sha256(entry.get("artifact_sha256"), "segment_artifact")
        entry_ordinal = entry.get("segment_ordinal")
        if not (
            not isinstance(entry_ordinal, bool)
            and isinstance(entry_ordinal, int)
            and entry_ordinal == ordinal
            and entry.get("segment_kind") == kind
            and entry.get("segment_id") == segment_id
        ):
            raise _fail("checkpoint_segment_order_mismatch")
        expected_filename = f"{ordinal:06d}.{kind}.{segment_id}.{digest}.json"
        expected_entry_relative = (
            Path("h0/checkpoints") / stage_attempt_id / expected_filename
        )
        if entry.get("artifact_path") != expected_entry_relative.as_posix():
            raise _fail("checkpoint_segment_path_mismatch")
        full_relative = artifact_prefix / expected_entry_relative
        path, relative, _, segment_encoded = _bound_file(
            root_path,
            full_relative.as_posix(),
            digest,
            label="checkpoint_segment",
        )
        try:
            path.relative_to(attempt_root)
        except ValueError:
            raise _fail("checkpoint_segment_attempt_escape") from None
        artifact = _read_checkpoint_object(
            segment_encoded, label=f"checkpoint_segment_{ordinal}"
        )
        artifact_ordinal = artifact.get("segment_ordinal")
        if not (
            artifact.get("schema_version") == "membind.h0.checkpoint-segment.v1"
            and artifact.get("protocol_version") == PROTOCOL_VERSION
            and artifact.get("stage_attempt_id") == stage_attempt_id
            and not isinstance(artifact_ordinal, bool)
            and isinstance(artifact_ordinal, int)
            and artifact_ordinal == ordinal
            and artifact.get("segment_kind") == kind
            and artifact.get("segment_id") == segment_id
            and artifact.get("secrets_persisted") is False
            and artifact.get("raw_prompts_persisted") is False
            and artifact.get("raw_responses_persisted") is False
            and isinstance(artifact.get("payload"), Mapping)
        ):
            raise _fail("checkpoint_segment_binding_mismatch")
        indexed_paths.add(path)
        if kind == "stage_result":
            if terminal_artifact is not None or ordinal != len(entries) - 1:
                raise _fail("terminal_segment_not_last_and_unique")
            terminal_artifact = artifact
            terminal_relative = relative
            terminal_digest = digest

    expected_children = {index_path, *indexed_paths}
    try:
        actual_children = set(attempt_root.iterdir())
    except OSError:
        raise _fail("checkpoint_attempt_directory_unreadable") from None
    if actual_children != expected_children or any(
        child.is_symlink() or not child.is_file() for child in actual_children
    ):
        raise _fail("checkpoint_attempt_contains_unindexed_entries")
    if terminal_artifact is None or terminal_relative is None or terminal_digest is None:
        raise _fail("terminal_segment_missing")
    validated_runtime = _validate_terminal_payload(
        terminal_artifact["payload"],
        stage_attempt_id=stage_attempt_id,
        runtime_definition_sha256=expected_runtime,
        terminal_result_sha256=terminal_result_sha256,
    )
    return {
        "schema_version": "membind.h0.prior-phase-terminal-completion.v1",
        "protocol_version": PROTOCOL_VERSION,
        "status": "qualified_terminal_completion",
        "qualified": True,
        "candidate_id": candidate_id,
        "phase": phase,
        "stage_attempt_id": stage_attempt_id,
        "checkpoint_index_path": index_relative.as_posix(),
        "checkpoint_index_sha256": index_digest,
        "terminal_segment_path": terminal_relative.as_posix(),
        "terminal_segment_sha256": terminal_digest,
        "terminal_result_sha256": terminal_result_sha256,
        "runtime_definition_sha256": validated_runtime,
        "candidate_advance_allowed": True,
        "partial_qualification_reusable": True,
        "requires_whole_stage_rerun": False,
        "secrets_persisted": False,
        "raw_prompts_persisted": False,
        "raw_responses_persisted": False,
    }
