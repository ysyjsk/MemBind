"""First-valid formal attempt selection and derived resource conformance seal."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import ArtifactError, AttemptStore
from .contracts import ResumeIdentity
from .dataset import EXPECTED_EPISODE_COUNTS, EXPECTED_SOURCE_TOKENS
from .live import build_formal_plan, derive_cache_salt, derive_namespace
from .schedules import Method


class FormalRunSealError(ValueError):
    """Formal attempts are incomplete, drifted, mutable, or ambiguously selected."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ATTEMPT = re.compile(r"^attempt-(?P<ordinal>[0-9]{3,})$")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_object(path: Path, *, code: str) -> dict[str, Any]:
    if path.is_symlink():
        raise FormalRunSealError(code)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise FormalRunSealError(code) from None
    if not isinstance(value, dict):
        raise FormalRunSealError(code)
    return value


def _verify_self_hash(value: Mapping[str, Any], *, code: str) -> str:
    candidate = dict(value)
    observed = candidate.pop("payload_sha256", None)
    if not isinstance(observed, str) or observed != _payload_hash(candidate):
        raise FormalRunSealError(code)
    return observed


def _identity(value: Any) -> ResumeIdentity:
    if not isinstance(value, Mapping):
        raise FormalRunSealError("FORMAL_RESUME_IDENTITY_INVALID")
    try:
        return ResumeIdentity(
            project_sha256=value["project_sha256"],
            data_sha256=value["data_sha256"],
            provider_sha256=value["provider_sha256"],
            resource_sha256=value["resource_sha256"],
            config_sha256=value["config_sha256"],
            cache_sha256=value["cache_sha256"],
            namespace=value["namespace"],
        )
    except (KeyError, TypeError, ValueError):
        raise FormalRunSealError("FORMAL_RESUME_IDENTITY_INVALID") from None


def _artifact_hashes(root: Path, attempt_root: Path) -> dict[str, str]:
    paths = sorted(attempt_root.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise FormalRunSealError("FORMAL_SELECTED_ARTIFACT_SYMLINK")
    files = [path for path in paths if path.is_file()]
    if not files:
        raise FormalRunSealError("FORMAL_SELECTED_ARTIFACT_MISSING")
    return {str(path.relative_to(root)): _file_hash(path) for path in files}


def _validate_protocol(root: Path) -> tuple[str, tuple[dict[str, Any], ...]]:
    protocol = _read_object(
        root / "protocol_manifest.json", code="FORMAL_PROTOCOL_MANIFEST_INVALID"
    )
    run_id = protocol.get("run_id")
    if not isinstance(run_id, str):
        raise FormalRunSealError("FORMAL_PROTOCOL_MANIFEST_INVALID")
    try:
        plan = build_formal_plan(run_id)
    except ValueError:
        raise FormalRunSealError("FORMAL_PROTOCOL_MANIFEST_INVALID") from None
    rows = protocol.get("formal_order")
    if (
        protocol.get("selection_rule") != "FIRST_VALID_ATTEMPT"
        or not isinstance(rows, list)
        or len(rows) != len(plan)
    ):
        raise FormalRunSealError("FORMAL_PROTOCOL_ORDER_INVALID")
    normalized: list[dict[str, Any]] = []
    for observed, expected in zip(rows, plan, strict=True):
        if not isinstance(observed, Mapping):
            raise FormalRunSealError("FORMAL_PROTOCOL_ORDER_INVALID")
        expected_row = {
            "ordinal": expected.ordinal,
            "block_id": expected.block_id,
            "history_id": expected.history_id,
            "method": expected.method.value,
            "attempt_ordinal": 1,
            "namespace": expected.namespace,
            "cache_salt_sha256": hashlib.sha256(
                expected.cache_salt.encode("ascii")
            ).hexdigest(),
        }
        if any(observed.get(key) != value for key, value in expected_row.items()):
            raise FormalRunSealError("FORMAL_PROTOCOL_ORDER_INVALID")
        normalized.append(expected_row)
    return run_id, tuple(normalized)


def _validate_schedule(metrics: Mapping[str, Any], method: Method, count: int) -> None:
    if (
        metrics.get("created_sequences") != list(range(count))
        or metrics.get("application_gate_count") != 0
        or metrics.get("artificial_sleep_count") != 0
        or metrics.get("configured_max_inflight") is not None
    ):
        raise FormalRunSealError("FORMAL_SCHEDULE_CONTRACT_INVALID")
    expected_awaits = count if method is Method.B0_NATIVE_SERIAL else 0
    if metrics.get("feeder_workload_await_count") != expected_awaits:
        raise FormalRunSealError("FORMAL_SCHEDULE_CONTRACT_INVALID")


def _load_valid_attempt(
    *,
    root: Path,
    attempt_root: Path,
    block: Mapping[str, Any],
    run_id: str,
    resource_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    match = _ATTEMPT.fullmatch(attempt_root.name)
    if match is None:
        raise FormalRunSealError("FORMAL_ATTEMPT_DIRECTORY_INVALID")
    attempt_ordinal = int(match.group("ordinal"))
    method = Method(str(block["method"]))
    expected_namespace = derive_namespace(
        run_id,
        method,
        str(block["history_id"]),
        attempt_ordinal=attempt_ordinal,
    )
    expected_salt = derive_cache_salt(
        run_id,
        str(block["block_id"]),
        attempt_ordinal=attempt_ordinal,
    )
    expected_cache_sha = hashlib.sha256(expected_salt.encode("ascii")).hexdigest()
    authority = _read_object(
        attempt_root / "live_authority.json", code="FORMAL_LIVE_AUTHORITY_INVALID"
    )
    _verify_self_hash(authority, code="FORMAL_LIVE_AUTHORITY_HASH_INVALID")
    authority_identity = _identity(authority.get("resume_identity"))
    if (
        authority.get("run_id") != run_id
        or authority.get("block_id") != block["block_id"]
        or authority.get("method") != method.value
        or authority.get("history_id") != block["history_id"]
        or authority.get("namespace") != expected_namespace
        or authority.get("attempt_ordinal") != attempt_ordinal
        or authority.get("cache_salt_sha256") != expected_cache_sha
        or authority_identity.namespace != expected_namespace
        or authority_identity.cache_sha256 != expected_cache_sha
    ):
        raise FormalRunSealError("FORMAL_LIVE_AUTHORITY_MISMATCH")
    if authority_identity.resource_sha256 != resource_id:
        raise FormalRunSealError("FORMAL_RESOURCE_ENVELOPE_MISMATCH")
    try:
        store = AttemptStore.open_existing(attempt_root, authority_identity)
        seal = store.verify_seal()
        recovered = store.recover_journal()
    except (ArtifactError, ValueError):
        raise FormalRunSealError("FORMAL_BLOCK_SEAL_INVALID") from None
    journal_tail = (
        recovered.events[-1]["payload_sha256"]
        if recovered.events
        else "0" * 64
    )
    if (
        recovered.truncated_tail
        or seal.get("resume_identity") != authority.get("resume_identity")
        or seal.get("journal_tail_sha256") != journal_tail
    ):
        raise FormalRunSealError("FORMAL_BLOCK_SEAL_INVALID")
    metrics = _read_object(
        attempt_root / "block_metrics.json", code="FORMAL_BLOCK_METRICS_INVALID"
    )
    graph = _read_object(
        attempt_root / "canonical_graph.json", code="FORMAL_CANONICAL_GRAPH_INVALID"
    )
    history = str(block["history_id"])
    count = EXPECTED_EPISODE_COUNTS[history]
    if (
        metrics.get("valid") is not True
        or metrics.get("block_id") != block["block_id"]
        or metrics.get("attempt_id") != attempt_root.name
        or metrics.get("attempt_ordinal") != attempt_ordinal
        or metrics.get("method") != method.value
        or metrics.get("history_id") != history
        or metrics.get("namespace") != expected_namespace
        or metrics.get("episode_count") != count
        or metrics.get("source_tokens") != EXPECTED_SOURCE_TOKENS[history]
        or metrics.get("seal_payload_sha256") != seal.get("payload_sha256")
        or metrics.get("resource_envelope_id") != resource_id
        or metrics.get("resource_availability") != "MEASURED"
        or metrics.get("canonical_graph_hash") != _payload_hash(graph)
    ):
        if metrics.get("resource_envelope_id") != resource_id:
            raise FormalRunSealError("FORMAL_RESOURCE_ENVELOPE_MISMATCH")
        raise FormalRunSealError("FORMAL_BLOCK_METRICS_MISMATCH")
    evidence = seal.get("evidence")
    if (
        not isinstance(evidence, Mapping)
        or evidence.get("episode_task_count") != count
        or evidence.get("terminal_episode_task_count") != count
    ):
        raise FormalRunSealError("FORMAL_BLOCK_SEAL_COUNT_MISMATCH")
    _validate_schedule(metrics, method, count)
    selected = {
        "ordinal": block["ordinal"],
        "block_id": block["block_id"],
        "method": method.value,
        "history_id": history,
        "attempt_id": attempt_root.name,
        "attempt_ordinal": attempt_ordinal,
        "namespace": expected_namespace,
        "resource_envelope_id": resource_id,
        "seal_payload_sha256": seal["payload_sha256"],
        "artifact_hashes": _artifact_hashes(root, attempt_root),
    }
    row = {**metrics, "attempt_root": str(attempt_root.resolve())}
    return selected, row


def _derive_formal_state(root: Path) -> dict[str, Any]:
    run_id, blocks = _validate_protocol(root)
    try:
        resource_id = (root / "RESOURCE_ENVELOPE_ID").read_text(
            encoding="ascii"
        ).strip()
    except (OSError, UnicodeError):
        raise FormalRunSealError("FORMAL_RESOURCE_ENVELOPE_ID_INVALID") from None
    if _SHA256.fullmatch(resource_id) is None:
        raise FormalRunSealError("FORMAL_RESOURCE_ENVELOPE_ID_INVALID")
    selected_attempts: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    attempt_ledger: list[dict[str, Any]] = []
    for block in blocks:
        block_root = root / "blocks" / str(block["block_id"])
        if block_root.is_symlink() or not block_root.is_dir():
            raise FormalRunSealError("FORMAL_BLOCK_DIRECTORY_MISSING")
        attempts = sorted(
            path
            for path in block_root.iterdir()
            if path.is_dir() and _ATTEMPT.fullmatch(path.name)
        )
        if not attempts:
            raise FormalRunSealError("FORMAL_BLOCK_ATTEMPT_MISSING")
        selected: tuple[dict[str, Any], dict[str, Any]] | None = None
        for attempt in attempts:
            terminal_files = [
                name
                for name in ("seal.json", "failure.json", "timeout_diagnosis.json")
                if (attempt / name).is_file()
            ]
            if len(terminal_files) != 1:
                raise FormalRunSealError("FORMAL_ATTEMPT_NONTERMINAL")
            artifact_hashes = _artifact_hashes(root, attempt)
            if terminal_files[0] == "seal.json":
                loaded = _load_valid_attempt(
                    root=root,
                    attempt_root=attempt,
                    block=block,
                    run_id=run_id,
                    resource_id=resource_id,
                )
                status = "VALID_SELECTED" if selected is None else "VALID_NOT_SELECTED"
                if selected is None:
                    selected = loaded
            else:
                terminal = _read_object(
                    attempt / terminal_files[0], code="FORMAL_FAILED_ATTEMPT_INVALID"
                )
                _verify_self_hash(terminal, code="FORMAL_FAILED_ATTEMPT_HASH_INVALID")
                status = "FAILED"
            attempt_ledger.append(
                {
                    "block_id": block["block_id"],
                    "attempt_id": attempt.name,
                    "status": status,
                    "artifact_hashes": artifact_hashes,
                }
            )
        if selected is None:
            raise FormalRunSealError("FORMAL_BLOCK_HAS_NO_VALID_ATTEMPT")
        selected_attempts.append(selected[0])
        selected_rows.append(selected[1])
    resource_ids = {
        selected["resource_envelope_id"] for selected in selected_attempts
    }
    if resource_ids != {resource_id}:
        raise FormalRunSealError("FORMAL_RESOURCE_ENVELOPE_MISMATCH")
    return {
        "run_id": run_id,
        "resource_envelope_id": resource_id,
        "valid_construction_blocks": len(selected_attempts),
        "formal_construction_calls": len(selected_attempts),
        "all_formal_blocks_share_one_resource_envelope": True,
        "selected_attempts": selected_attempts,
        "attempt_ledger": attempt_ledger,
        "rows": selected_rows,
    }


def write_formal_run_seal(run_root: Path) -> dict[str, Any]:
    root = run_root.resolve()
    path = root / "formal/formal_run_seal.json"
    if path.exists():
        raise FormalRunSealError("FORMAL_RUN_SEAL_ALREADY_EXISTS")
    state = _derive_formal_state(root)
    body = {
        "schema_version": "membind.saturated-fixed-work.formal-run-seal.v1",
        "status": "FORMAL_RUN_SEALED",
        **{key: value for key, value in state.items() if key != "rows"},
        "protocol_manifest_sha256": _file_hash(root / "protocol_manifest.json"),
        "resource_envelope_id_file_sha256": _file_hash(
            root / "RESOURCE_ENVELOPE_ID"
        ),
    }
    body["payload_sha256"] = _payload_hash(body)
    payload = json.dumps(
        body, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise FormalRunSealError("FORMAL_RUN_SEAL_ALREADY_EXISTS") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return body


def verify_formal_run_seal(run_root: Path) -> dict[str, Any]:
    root = run_root.resolve()
    seal = _read_object(
        root / "formal/formal_run_seal.json", code="FORMAL_RUN_SEAL_UNREADABLE"
    )
    observed_hash = _verify_self_hash(seal, code="FORMAL_RUN_SEAL_HASH_INVALID")
    if seal.get("status") != "FORMAL_RUN_SEALED":
        raise FormalRunSealError("FORMAL_RUN_SEAL_INVALID")
    if (
        seal.get("protocol_manifest_sha256")
        != _file_hash(root / "protocol_manifest.json")
        or seal.get("resource_envelope_id_file_sha256")
        != _file_hash(root / "RESOURCE_ENVELOPE_ID")
    ):
        raise FormalRunSealError("FORMAL_RUN_SEAL_BASE_MISMATCH")
    selected = seal.get("selected_attempts")
    if not isinstance(selected, list):
        raise FormalRunSealError("FORMAL_RUN_SEAL_INVALID")
    for attempt in selected:
        files = attempt.get("artifact_hashes") if isinstance(attempt, Mapping) else None
        if not isinstance(files, Mapping) or any(
            not isinstance(name, str)
            or not isinstance(expected, str)
            or not (root / name).is_file()
            or (root / name).is_symlink()
            or _file_hash(root / name) != expected
            for name, expected in (files.items() if isinstance(files, Mapping) else ())
        ):
            raise FormalRunSealError("FORMAL_SELECTED_ARTIFACT_HASH_MISMATCH")
    state = _derive_formal_state(root)
    for key, value in state.items():
        if key != "rows" and seal.get(key) != value:
            if key in {"selected_attempts", "attempt_ledger"}:
                raise FormalRunSealError(
                    "FORMAL_SELECTED_ARTIFACT_HASH_MISMATCH"
                )
            raise FormalRunSealError("FORMAL_RUN_SEAL_STATE_MISMATCH")
    return {
        "schema_version": "membind.saturated-fixed-work.formal-run-verification.v1",
        "verified": True,
        "payload_sha256": observed_hash,
        "resource_envelope_id": state["resource_envelope_id"],
        "valid_construction_blocks": state["valid_construction_blocks"],
        "formal_construction_calls": state["formal_construction_calls"],
        "all_formal_blocks_share_one_resource_envelope": True,
        "selected_attempts": state["selected_attempts"],
        "attempt_ledger": state["attempt_ledger"],
        "rows": state["rows"],
    }


__all__ = [
    "FormalRunSealError",
    "verify_formal_run_seal",
    "write_formal_run_seal",
]
