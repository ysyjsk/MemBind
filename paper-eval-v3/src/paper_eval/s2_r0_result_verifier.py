"""Pure verification of one sealed S2-R0 terminal artifact chain.

The verifier reads only caller-supplied JSON files.  It has no environment,
database, model, network, authorization, or artifact-writing entry point.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import payload_sha256, sha256_file
from .s2_retrieval_contract import classify_surface_comparison


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]+$")
_OFFLINE_SCHEMA = "membind.paper-eval-v3.s2-r0-offline-qualification.v1"
_AUTHORIZATION_SCHEMA = "membind.paper-eval-v3.s2-r0-authorization.v1"
_CONSUMPTION_SCHEMA = "membind.paper-eval-v3.s2-r0-consumption.v1"
_RESULT_SCHEMA = "membind.paper-eval-v3.s2-r0-episode-probe.v2"
_FAILURE_SCHEMA = "membind.paper-eval-v3.s2-r0-failure.v1"
_EXPECTED_HISTORY_ID = "07741c45"
_EXPECTED_NAMESPACE = "pev3-s1-20260814-001"
_EXPECTED_EPISODE_COUNT = 49

_FORBIDDEN_COUNTERS = (
    "construction_llm_requests",
    "embedding_requests",
    "cross_encoder_requests",
    "reader_requests",
    "judge_requests",
    "database_mutation_attempts",
    "database_mutations",
    "namespace_cleanup_calls",
    "retry_count",
)
_CHAIN_FIELDS = (
    "dataset_sha256",
    "frozen_split_sha256",
    "frozen_corpus_identity_sha256",
    "ordered_session_ids_sha256",
    "gold_session_ids_sha256",
    "episode_names_sha256",
    "episode_content_hash_sequence_sha256",
    "gold_session_count",
    "retrieval_config",
)


class S2R0VerificationError(ValueError):
    """A fail-closed, content-free artifact verification error."""


@dataclass(frozen=True)
class S2R0AttemptPaths:
    qualification: Path
    authorization: Path
    consumption: Path
    result: Path
    failure: Path


@dataclass(frozen=True)
class VerifiedS2R0Outcome:
    run_id: str
    history_id: str
    namespace: str
    terminal_kind: str
    terminal_status: str
    interpretation: str
    qualification_sha256: str
    authorization_sha256: str
    consumption_sha256: str
    terminal_sha256: str
    graphiti_search_calls: int
    neo4j_read_requests: int
    forbidden_call_counts: Mapping[str, int]
    retrieval_policy_selected: bool
    s3_authorized: bool


@dataclass(frozen=True)
class _Envelope:
    body: dict[str, Any]
    payload: dict[str, Any]
    file_sha256: str


def _sha(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise S2R0VerificationError(f"{field} is not a SHA256")
    return value


def _nonempty(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise S2R0VerificationError(f"{field} is invalid")
    return value


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise S2R0VerificationError(f"{field} counter is invalid")
    return value


def _load_envelope(path: Path, *, label: str) -> _Envelope:
    selected = Path(path)
    try:
        body = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        raise S2R0VerificationError(f"{label} envelope is unreadable") from None
    if not isinstance(body, dict) or set(body) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise S2R0VerificationError(f"{label} envelope shape is invalid")
    payload = body.get("payload")
    if (
        not isinstance(payload, dict)
        or body.get("protocol_version") != PROTOCOL_VERSION
        or body.get("status") != "finalized"
        or body.get("payload_sha256") != payload_sha256(payload)
    ):
        raise S2R0VerificationError(f"{label} envelope seal is invalid")
    _nonempty(body.get("git_commit"), field=f"{label} git commit")
    _nonempty(body.get("run_id"), field=f"{label} run ID")
    file_hash = sha256_file(selected)
    _sha(file_hash, field=f"{label} file")
    return _Envelope(body=body, payload=payload, file_sha256=file_hash)


def _binding_map(value: Any, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise S2R0VerificationError(f"{field} binding is incomplete")
    bindings: dict[str, str] = {}
    for key, digest in value.items():
        name = _nonempty(key, field=f"{field} binding name")
        bindings[name] = _sha(digest, field=f"{field} binding")
    return bindings


def _require_s3_false(payload: Mapping[str, Any], *, label: str) -> None:
    if payload.get("s3_authorized") is not False:
        raise S2R0VerificationError(f"{label} must leave S3 unauthorized")


def _require_identity(
    payload: Mapping[str, Any], *, run_id: str | None, label: str
) -> None:
    if (
        payload.get("history_id") != _EXPECTED_HISTORY_ID
        or payload.get("namespace") != _EXPECTED_NAMESPACE
        or (run_id is not None and payload.get("run_id") != run_id)
    ):
        raise S2R0VerificationError(f"{label} identity drift")


def _validate_qualification(value: _Envelope) -> dict[str, str]:
    payload = value.payload
    _require_identity(payload, run_id=None, label="qualification")
    _require_s3_false(payload, label="qualification")
    if (
        payload.get("schema_version") != _OFFLINE_SCHEMA
        or payload.get("stage") != "S2-R0-OFFLINE"
        or payload.get("verdict") != "PASS"
        or payload.get("live_authorized") is not False
        or payload.get("expected_episode_count") != _EXPECTED_EPISODE_COUNT
        or payload.get("gold_session_count") != 2
        or payload.get("historical_s2_mutation_count") != 0
    ):
        raise S2R0VerificationError("qualification contract drift")
    bindings = _binding_map(payload.get("binding_sha256"), field="qualification")
    for field in _CHAIN_FIELDS[:-2]:
        _sha(payload.get(field), field=f"qualification {field}")
    config = payload.get("retrieval_config")
    if not isinstance(config, dict) or payload.get("retrieval_config_sha256") != payload_sha256(config):
        raise S2R0VerificationError("qualification retrieval config drift")
    return bindings


def _validate_authorization(
    value: _Envelope, *, qualification: _Envelope, bindings: Mapping[str, str]
) -> str:
    payload = value.payload
    run_id = _nonempty(value.body.get("run_id"), field="authorization run ID")
    _require_identity(payload, run_id=run_id, label="authorization")
    _require_s3_false(payload, label="authorization")
    if (
        payload.get("schema_version") != _AUTHORIZATION_SCHEMA
        or payload.get("stage") != "S2-R0"
        or payload.get("authorization") != "RUN_S2_R0_EPISODE_BM25_ONCE"
        or payload.get("qualification_sha256") != qualification.file_sha256
        or payload.get("qualification_payload_sha256")
        != qualification.body.get("payload_sha256")
        or _binding_map(payload.get("binding_sha256"), field="authorization")
        != dict(bindings)
        or payload.get("neo4j_auto_schema_initialization") is not False
        or payload.get("driver_routing_policy") != "read_only"
        or payload.get("raw_content_persistence") is not False
    ):
        raise S2R0VerificationError("authorization hash or contract drift")
    for field in _CHAIN_FIELDS:
        if payload.get(field) != qualification.payload.get(field):
            raise S2R0VerificationError("authorization qualification binding drift")
    if payload.get("retry_lineage") != qualification.payload.get("retry_lineage"):
        raise S2R0VerificationError("authorization retry lineage drift")

    limits = payload.get("limits")
    expected_limits = {
        "graphiti_search_calls": 1,
        "construction_llm_requests": 0,
        "embedding_requests": 0,
        "cross_encoder_requests": 0,
        "reader_requests": 0,
        "judge_requests": 0,
        "database_mutation_attempts": 0,
        "namespace_cleanup_calls": 0,
        "retry_count": 0,
    }
    if limits != expected_limits:
        raise S2R0VerificationError("authorization counter limits drift")
    for field, expected_name in (
        ("expected_output_path", "S2_R0_EPISODE_PROBE.json"),
        ("consumption_path", "S2_R0_AUTHORIZATION_CONSUMPTION.json"),
    ):
        path = Path(str(payload.get(field, "")))
        if path.name != expected_name or path.parent.name != run_id:
            raise S2R0VerificationError("authorization path identity drift")
    return run_id


def _validate_consumption(
    value: _Envelope,
    *,
    authorization: _Envelope,
    run_id: str,
    bindings: Mapping[str, str],
) -> None:
    payload = value.payload
    _require_identity(payload, run_id=run_id, label="consumption")
    _require_s3_false(payload, label="consumption")
    if (
        value.body.get("run_id") != run_id
        or payload.get("schema_version") != _CONSUMPTION_SCHEMA
        or payload.get("stage") != "S2-R0"
        or payload.get("status") != "CONSUMED_BEFORE_LIVE_IO"
        or payload.get("live_io_performed_at_consumption") is not False
        or payload.get("authorization_sha256") != authorization.file_sha256
        or _binding_map(payload.get("binding_sha256"), field="consumption")
        != dict(bindings)
    ):
        raise S2R0VerificationError("consumption hash or binding drift")


def _validate_counters(payload: Mapping[str, Any]) -> tuple[int, int, dict[str, int]]:
    forbidden = {
        field: _nonnegative_int(payload.get(field), field=field)
        for field in _FORBIDDEN_COUNTERS
    }
    search_calls = _nonnegative_int(
        payload.get("graphiti_search_calls"), field="graphiti_search_calls"
    )
    read_calls = _nonnegative_int(
        payload.get("neo4j_read_requests"), field="neo4j_read_requests"
    )
    if any(forbidden.values()) or search_calls != 1 or read_calls < 1:
        raise S2R0VerificationError("terminal counter contract violated")
    return search_calls, read_calls, forbidden


def _validate_terminal_common(
    value: _Envelope,
    *,
    authorization: _Envelope,
    consumption: _Envelope,
    run_id: str,
) -> tuple[int, int, dict[str, int]]:
    payload = value.payload
    if value.body.get("run_id") != run_id or value.body.get("git_commit") != authorization.body.get("git_commit"):
        raise S2R0VerificationError("terminal envelope identity drift")
    _require_identity(payload, run_id=None, label="terminal")
    _require_s3_false(payload, label="terminal")
    if (
        payload.get("stage") != "S2-R0"
        or payload.get("authorization_sha256") != authorization.file_sha256
        or payload.get("consumption_sha256") != consumption.file_sha256
    ):
        raise S2R0VerificationError("terminal hash chain drift")
    return _validate_counters(payload)


def _session_ids(payload: Mapping[str, Any], *, prefix: str) -> list[str]:
    value = payload.get(f"{prefix}_session_ids")
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
        or payload.get(f"{prefix}_session_count") != len(value)
        or payload.get(f"{prefix}_session_ids_sha256") != payload_sha256(value)
    ):
        raise S2R0VerificationError(f"terminal {prefix} session identity drift")
    return value


def _validate_result(
    value: _Envelope,
    *,
    authorization: _Envelope,
    bindings: Mapping[str, str],
) -> str:
    payload = value.payload
    if (
        payload.get("schema_version") != _RESULT_SCHEMA
        or payload.get("status") != "READ_ONLY_RETRIEVAL_SURFACE_DIAGNOSTIC"
        or payload.get("retrieval_policy_selected") is not False
        or payload.get("whole_graph_quality_conclusion") != "NOT_INFERRED"
        or payload.get("result_sealed_before_policy_freeze") is not True
        or payload.get("corpus_completeness_pass") is not True
        or payload.get("observed_session_count") != _EXPECTED_EPISODE_COUNT
        or payload.get("expected_name_content_map_sha256")
        != payload.get("observed_name_content_map_sha256")
        or payload.get("driver_routing_policy") != "read_only"
        or payload.get("neo4j_auto_schema_initialization") is not False
        or payload.get("driver_init_task_present") is not False
        or payload.get("top_k") != 10
        or payload.get("top_k_unit") != "session"
    ):
        raise S2R0VerificationError("terminal result contract drift")
    if _binding_map(payload.get("source_sha256"), field="terminal source") != dict(bindings):
        raise S2R0VerificationError("terminal source binding drift")
    for field in (
        "dataset_sha256",
        "frozen_split_sha256",
        "frozen_corpus_identity_sha256",
        "gold_session_ids_sha256",
    ):
        if payload.get(field) != authorization.payload.get(field):
            raise S2R0VerificationError("terminal authorization binding drift")
    config = payload.get("retrieval_config")
    if (
        config != authorization.payload.get("retrieval_config")
        or payload.get("retrieval_config_sha256") != payload_sha256(config)
    ):
        raise S2R0VerificationError("terminal retrieval config drift")
    retrieved = _session_ids(payload, prefix="retrieved")
    gold = _session_ids(payload, prefix="gold")
    if payload.get("gold_session_count") != authorization.payload.get("gold_session_count"):
        raise S2R0VerificationError("terminal gold session count drift")
    covered = len(set(retrieved).intersection(gold))
    fraction = covered / len(gold)
    recall_any = 1.0 if covered else 0.0
    recall_all = 1.0 if covered == len(gold) else 0.0
    comparison = classify_surface_comparison(
        edge_attributed_source_session_coverage=payload.get(
            "edge_attributed_source_session_coverage"
        ),
        episode_session_recall_any=recall_any,
        episode_session_recall_all=recall_all,
    )
    if (
        payload.get("covered_gold_session_count") != covered
        or payload.get("session_gold_coverage_fraction_at_10") != fraction
        or payload.get("session_recall_any_at_10") != recall_any
        or payload.get("session_recall_all_at_10") != recall_all
        or payload.get("classification") != comparison["classification"]
        or payload.get("node_surface_status") != comparison["node_surface_status"]
        or payload.get("multi_surface_status") != comparison["multi_surface_status"]
    ):
        raise S2R0VerificationError("terminal result metric drift")
    for field in (
        "expected_name_content_map_sha256",
        "observed_name_content_map_sha256",
        "query_sha256",
        "reference_sanity_sha256",
    ):
        _sha(payload.get(field), field=f"terminal {field}")
    return _nonempty(payload.get("classification"), field="terminal classification")


def _validate_failure(value: _Envelope) -> str:
    payload = value.payload
    if (
        payload.get("schema_version") != _FAILURE_SCHEMA
        or payload.get("status") != "FAILED_STOPPED"
        or payload.get("result_mergeable") is not False
        or payload.get("retrieval_conclusion") != "NOT_PRODUCED"
    ):
        raise S2R0VerificationError("terminal failure contract drift")
    error_class = payload.get("error_class")
    if not isinstance(error_class, str) or _IDENTIFIER.fullmatch(error_class) is None:
        raise S2R0VerificationError("terminal failure class is invalid")
    return "NOT_PRODUCED"


def verify_s2r0_attempt(paths: S2R0AttemptPaths) -> VerifiedS2R0Outcome:
    """Verify one qualification-to-terminal chain without any side effect."""

    if not isinstance(paths, S2R0AttemptPaths):
        raise S2R0VerificationError("attempt paths are invalid")
    result_exists = Path(paths.result).is_file()
    failure_exists = Path(paths.failure).is_file()
    if result_exists == failure_exists:
        raise S2R0VerificationError("exactly one terminal artifact is required")

    qualification = _load_envelope(paths.qualification, label="qualification")
    bindings = _validate_qualification(qualification)
    authorization = _load_envelope(paths.authorization, label="authorization")
    run_id = _validate_authorization(
        authorization,
        qualification=qualification,
        bindings=bindings,
    )
    if authorization.body.get("git_commit") != qualification.body.get("git_commit"):
        raise S2R0VerificationError("authorization git identity drift")
    consumption = _load_envelope(paths.consumption, label="consumption")
    _validate_consumption(
        consumption,
        authorization=authorization,
        run_id=run_id,
        bindings=bindings,
    )
    if consumption.body.get("git_commit") != authorization.body.get("git_commit"):
        raise S2R0VerificationError("consumption git identity drift")

    terminal_kind = "SUCCESS" if result_exists else "FAILURE"
    terminal_path = paths.result if result_exists else paths.failure
    terminal = _load_envelope(terminal_path, label="terminal")
    search_calls, read_calls, forbidden = _validate_terminal_common(
        terminal,
        authorization=authorization,
        consumption=consumption,
        run_id=run_id,
    )
    if terminal_kind == "SUCCESS":
        interpretation = _validate_result(
            terminal,
            authorization=authorization,
            bindings=bindings,
        )
    else:
        interpretation = _validate_failure(terminal)

    return VerifiedS2R0Outcome(
        run_id=run_id,
        history_id=_EXPECTED_HISTORY_ID,
        namespace=_EXPECTED_NAMESPACE,
        terminal_kind=terminal_kind,
        terminal_status=str(terminal.payload["status"]),
        interpretation=interpretation,
        qualification_sha256=qualification.file_sha256,
        authorization_sha256=authorization.file_sha256,
        consumption_sha256=consumption.file_sha256,
        terminal_sha256=terminal.file_sha256,
        graphiti_search_calls=search_calls,
        neo4j_read_requests=read_calls,
        forbidden_call_counts=MappingProxyType(forbidden),
        retrieval_policy_selected=False,
        s3_authorized=False,
    )
