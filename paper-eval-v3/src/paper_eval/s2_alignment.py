"""Offline S2 protocol-alignment and C2 reuse decision helpers.

This module deliberately stops before construction or judge requests.  It
compares immutable dataset records, the pinned rubric, and sanitized C2
provenance so a live S2 qualification can only be authorized by an explicit
and reproducible decision.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .artifacts import atomic_write_json, finalize_envelope, payload_sha256


S2_SCHEMA = "membind.paper-eval-v3.s2-alignment.v1"
EXPECTED_C2_REVISION = "6e2312b85c2ae9a31f629f24493b79d8b02eab1a"
_SHA256_HEX = frozenset("0123456789abcdef")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(character in _SHA256_HEX for character in value)
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _record_signature(record: Mapping[str, Any]) -> dict[str, Any]:
    required = (
        "question_id",
        "question_type",
        "question",
        "answer",
        "haystack_session_ids",
        "haystack_dates",
        "haystack_sessions",
        "answer_session_ids",
    )
    missing = [key for key in required if key not in record]
    if missing:
        raise ValueError("missing_required_fields:" + ",".join(missing))
    sessions = record.get("haystack_sessions")
    dates = record.get("haystack_dates")
    session_ids = record.get("haystack_session_ids")
    answer_session_ids = record.get("answer_session_ids")
    if not isinstance(sessions, list) or not isinstance(dates, list):
        raise ValueError("record missing haystack sessions/dates")
    if not isinstance(session_ids, list) or len(session_ids) != len(sessions):
        raise ValueError("session ID count mismatch")
    if len(dates) != len(sessions):
        raise ValueError("timestamp/session count mismatch")
    if not isinstance(answer_session_ids, list):
        raise ValueError("answer_session_ids must be a list")
    return {
        "question_id": str(record.get("question_id", "")),
        "question_type": str(record.get("question_type", "")),
        "haystack_session_ids": [str(value) for value in session_ids],
        "haystack_dates": [str(value) for value in dates],
        "answer_session_ids": [str(value) for value in answer_session_ids],
        "question_sha256": hashlib.sha256(str(record.get("question", "")).encode()).hexdigest(),
        "answer_sha256": hashlib.sha256(str(record.get("answer", "")).encode()).hexdigest(),
        "haystack_sessions_sha256": sha256_json(sessions),
    }


def _index_selected_records(
    records: Iterable[Mapping[str, Any]],
    selected: set[str],
    *,
    side: str,
) -> tuple[dict[str, Mapping[str, Any]], list[dict[str, Any]]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    mismatches: list[dict[str, Any]] = []
    for record in records:
        question_id = str(record.get("question_id", ""))
        if question_id not in selected:
            continue
        if question_id in indexed:
            mismatches.append(
                {
                    "question_id": question_id,
                    "reason": f"duplicate_{side}_question_id",
                }
            )
        else:
            indexed[question_id] = record
    return indexed, mismatches


def dataset_parity(
    left_records: Iterable[Mapping[str, Any]],
    right_records: Iterable[Mapping[str, Any]],
    question_ids: Iterable[str],
) -> dict[str, Any]:
    """Compare only frozen protocol fields, returning a serializable report."""

    checked = [str(question_id) for question_id in question_ids]
    selected = set(checked)
    left, left_mismatches = _index_selected_records(
        left_records, selected, side="left"
    )
    right, right_mismatches = _index_selected_records(
        right_records, selected, side="right"
    )
    mismatches = [*left_mismatches, *right_mismatches]
    for question_id in sorted({value for value in checked if checked.count(value) > 1}):
        mismatches.append(
            {"question_id": question_id, "reason": "duplicate_selected_question_id"}
        )
    for question_id in checked:
        if question_id not in left or question_id not in right:
            mismatches.append({"question_id": question_id, "reason": "missing_record"})
            continue
        try:
            left_signature = _record_signature(left[question_id])
            right_signature = _record_signature(right[question_id])
        except ValueError as error:
            message = str(error)
            if message.startswith("missing_required_fields:"):
                mismatches.append(
                    {
                        "question_id": question_id,
                        "reason": "missing_required_fields",
                        "fields": message.split(":", 1)[1].split(","),
                    }
                )
            else:
                mismatches.append({"question_id": question_id, "reason": message})
            continue
        if left_signature != right_signature:
            mismatches.append(
                {
                    "question_id": question_id,
                    "reason": "field_or_hash_mismatch",
                    "left_signature": left_signature,
                    "right_signature": right_signature,
                }
            )
    return {
        "schema_version": S2_SCHEMA,
        "checked_question_ids": checked,
        "checked_count": len(checked),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "verdict": "PASS" if not mismatches else "FAIL",
    }


def dataset_projection_parity(
    source_records: Iterable[Mapping[str, Any]],
    projections: Iterable[Mapping[str, Any]],
    question_ids: Iterable[str],
    *,
    episode_builder: Callable[[Mapping[str, Any]], Iterable[Any]] | None = None,
) -> dict[str, Any]:
    """Verify source-to-runtime projection with an independently invoked builder."""

    source_rows = list(source_records)
    projection_rows = list(projections)
    selected_ids = [str(value) for value in question_ids]
    selected_id_set = set(selected_ids)
    source: dict[str, Mapping[str, Any]] = {}
    projected: dict[str, Mapping[str, Any]] = {}
    mismatches: list[dict[str, Any]] = []
    required_source_fields = (
        "question_id",
        "question_type",
        "question",
        "answer",
        "haystack_session_ids",
        "haystack_dates",
        "haystack_sessions",
        "answer_session_ids",
    )
    required_projection_fields = (
        "question_id",
        "question_type",
        "session_ids",
        "timestamps",
        "answer_session_ids",
        "question_sha256",
        "answer_sha256",
        "episode_source_hashes",
        "episode_body_hashes",
    )

    duplicate_selected_ids = sorted(
        {value for value in selected_ids if selected_ids.count(value) > 1}
    )
    for question_id in duplicate_selected_ids:
        mismatches.append(
            {"question_id": question_id, "reason": "duplicate_selected_question_id"}
        )
    for row in source_rows:
        question_id = str(row.get("question_id", ""))
        if question_id not in selected_id_set:
            continue
        if question_id in source:
            mismatches.append(
                {"question_id": question_id, "reason": "duplicate_source_question_id"}
            )
        else:
            source[question_id] = row
    for row in projection_rows:
        question_id = str(row.get("question_id", ""))
        if question_id not in selected_id_set:
            continue
        if question_id in projected:
            mismatches.append(
                {
                    "question_id": question_id,
                    "reason": "duplicate_projection_question_id",
                }
            )
        else:
            projected[question_id] = row

    for question_id in selected_ids:
        record = source.get(question_id)
        projection = projected.get(question_id)
        if record is None:
            mismatches.append({"question_id": question_id, "reason": "missing_source_record"})
            continue
        missing_source = [key for key in required_source_fields if key not in record]
        if missing_source:
            mismatches.append(
                {
                    "question_id": question_id,
                    "reason": "missing_required_fields",
                    "location": "source",
                    "fields": missing_source,
                }
            )
            continue
        if projection is None:
            mismatches.append(
                {"question_id": question_id, "reason": "missing_projection_record"}
            )
            continue
        missing_projection = [
            key for key in required_projection_fields if key not in projection
        ]
        try:
            signature = _record_signature(record)
        except ValueError as error:
            message = str(error)
            if message.startswith("missing_required_fields:"):
                mismatches.append(
                    {
                        "question_id": question_id,
                        "reason": "missing_required_fields",
                        "location": "source",
                        "fields": message.split(":", 1)[1].split(","),
                    }
                )
            else:
                mismatches.append({"question_id": question_id, "reason": message})
            continue
        expected = {
            "question_id": signature["question_id"],
            "question_type": signature["question_type"],
            "session_ids": signature["haystack_session_ids"],
            "timestamps": signature["haystack_dates"],
            "answer_session_ids": signature["answer_session_ids"],
            "question_sha256": signature["question_sha256"],
            "answer_sha256": signature["answer_sha256"],
        }
        observed = {key: projection.get(key) for key in expected}
        episode_hashes = projection.get("episode_source_hashes")
        if observed != expected:
            mismatches.append(
                {
                    "question_id": question_id,
                    "reason": "runtime_projection_mismatch",
                    "expected": expected,
                    "observed": observed,
                }
            )
        if not isinstance(episode_hashes, list) or len(episode_hashes) != len(
            expected["session_ids"]
        ):
            mismatches.append(
                {"question_id": question_id, "reason": "episode_projection_mismatch"}
            )
        elif any(not _is_sha256(value) for value in episode_hashes):
            mismatches.append(
                {"question_id": question_id, "reason": "episode_source_hash_invalid"}
            )
        body_hashes = projection.get("episode_body_hashes")
        if not isinstance(body_hashes, list) or len(body_hashes) != len(
            expected["session_ids"]
        ):
            mismatches.append(
                {"question_id": question_id, "reason": "episode_body_hash_invalid"}
            )
        elif any(not _is_sha256(value) for value in body_hashes):
            mismatches.append(
                {"question_id": question_id, "reason": "episode_body_hash_invalid"}
            )
        if missing_projection:
            mismatches.append(
                {
                    "question_id": question_id,
                    "reason": "missing_required_fields",
                    "location": "projection",
                    "fields": missing_projection,
                }
            )
        if episode_builder is None:
            mismatches.append(
                {"question_id": question_id, "reason": "episode_builder_missing"}
            )
            continue
        try:
            rebuilt = list(episode_builder(record))
        except Exception as error:
            mismatches.append(
                {
                    "question_id": question_id,
                    "reason": "episode_builder_error",
                    "error_class": type(error).__name__,
                }
            )
            continue
        rebuilt_source_hashes = [getattr(episode, "source_hash", None) for episode in rebuilt]
        rebuilt_body_hashes = [
            hashlib.sha256(str(getattr(episode, "body", "")).encode()).hexdigest()
            for episode in rebuilt
        ]
        if rebuilt_source_hashes != episode_hashes:
            mismatches.append(
                {"question_id": question_id, "reason": "episode_source_hash_mismatch"}
            )
        if rebuilt_body_hashes != body_hashes:
            mismatches.append(
                {"question_id": question_id, "reason": "episode_body_hash_mismatch"}
            )
    return {
        "schema_version": S2_SCHEMA,
        "checked_question_ids": selected_ids,
        "checked_count": len(selected_ids),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "episode_source_hash_shape_valid": all(
            question_id in source
            and question_id in projected
            and isinstance(
                projected.get(question_id, {}).get("episode_source_hashes"), list
            )
            and all(
                _is_sha256(value)
                for value in projected[question_id]["episode_source_hashes"]
            )
            and len(projected[question_id]["episode_source_hashes"])
            == len(source.get(question_id, {}).get("haystack_session_ids", []))
            for question_id in selected_ids
        ),
        "episode_hashes_recomputed": episode_builder is not None,
        "dataset_replication": "single_cleaned_source_to_runtime_projection",
        "verdict": "PASS" if not mismatches else "FAIL",
    }


def evaluator_parity(
    routes: Mapping[str, Mapping[str, Any]],
    *,
    expected_prompt_hashes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate fixed official/local route and prompt/hash fixture records."""

    mismatches: list[dict[str, Any]] = []
    checked: list[str] = []
    for fixture_id, route in routes.items():
        checked.append(str(fixture_id))
        required = (
            "question_type",
            "abstention",
            "official_prompt",
            "adapter_prompt",
            "official_label",
            "adapter_label",
        )
        missing = [key for key in required if key not in route]
        if missing:
            mismatches.append({"fixture_id": fixture_id, "reason": "missing_fields", "fields": missing})
            continue
        official_hash = hashlib.sha256(str(route["official_prompt"]).encode()).hexdigest()
        adapter_hash = hashlib.sha256(str(route["adapter_prompt"]).encode()).hexdigest()
        if expected_prompt_hashes is not None and expected_prompt_hashes.get(
            str(fixture_id)
        ) != official_hash:
            mismatches.append(
                {"fixture_id": fixture_id, "reason": "frozen_prompt_hash_mismatch"}
            )
        if official_hash != adapter_hash:
            mismatches.append({"fixture_id": fixture_id, "reason": "prompt_hash_mismatch"})
        if route["official_label"] != route["adapter_label"]:
            mismatches.append({"fixture_id": fixture_id, "reason": "label_semantics_mismatch"})
    official_positive_count = sum(
        route.get("official_label") is True for route in routes.values()
    )
    adapter_positive_count = sum(
        route.get("adapter_label") is True for route in routes.values()
    )
    if official_positive_count != adapter_positive_count:
        mismatches.append(
            {
                "reason": "aggregate_label_semantics_mismatch",
                "official_positive_count": official_positive_count,
                "adapter_positive_count": adapter_positive_count,
            }
        )
    if expected_prompt_hashes is not None and set(expected_prompt_hashes) != set(routes):
        mismatches.append(
            {
                "reason": "frozen_prompt_fixture_set_mismatch",
                "missing": sorted(set(routes) - set(expected_prompt_hashes)),
                "unexpected": sorted(set(expected_prompt_hashes) - set(routes)),
            }
        )
    if any("adapter_status" in route for route in routes.values()):
        aggregate = {
            "official_headline_positive_count": official_positive_count,
            "adapter_headline_positive_count": adapter_positive_count,
            "adapter_success_positive_count": sum(
                route.get("adapter_status") == "SUCCESS"
                and route.get("adapter_label") is True
                for route in routes.values()
            ),
            "adapter_invalid_count": sum(
                route.get("adapter_status") == "INVALID_OUTPUT"
                for route in routes.values()
            ),
            "total_count": len(routes),
        }
    else:
        aggregate = {
            "official_positive_count": official_positive_count,
            "adapter_positive_count": adapter_positive_count,
            "total_count": len(routes),
            "official_positive_rate": official_positive_count / len(routes) if routes else None,
            "adapter_positive_rate": adapter_positive_count / len(routes) if routes else None,
        }
    return {
        "schema_version": S2_SCHEMA,
        "checked_fixture_ids": checked,
        "checked_count": len(checked),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
        "verdict": "PASS" if not mismatches else "FAIL",
        "judge_backend_difference": "reported_separately",
        "aggregate_label_semantics": aggregate,
        "prompt_hashes": {
            str(fixture_id): hashlib.sha256(
                str(route.get("official_prompt", "")).encode()
            ).hexdigest()
            for fixture_id, route in routes.items()
        },
    }


def decide_c2_u0_reuse(
    *,
    c2_manifest: Mapping[str, Any],
    current_runtime: Mapping[str, Any],
    u0_contract: Mapping[str, Any],
    c2_verification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return CASE_A only when all relevant identity/provenance fields match."""

    provenance = c2_manifest.get("provenance", {})
    runtime = provenance.get("sanitized_runtime_identity", {})
    c2_revision = runtime.get("construction", {}).get("model_revision")
    current_revision = current_runtime.get("construction", {}).get("repository_revision")
    reasons: list[str] = []
    if not c2_revision or not current_revision:
        reasons.append("runtime_identity_missing:construction_model_revision")
    elif c2_revision != current_revision:
        reasons.append("construction_model_revision_drift")
    comparable = {
        "graphiti_version": (
            runtime.get("graphiti", {}).get("version"),
            current_runtime.get("graphiti", {}).get("version"),
        ),
        "graphiti_commit": (
            runtime.get("graphiti", {}).get("commit"),
            current_runtime.get("graphiti", {}).get("repository_commit"),
        ),
        "embedding_fingerprint": (
            runtime.get("embedding", {}).get("deployment_fingerprint"),
            current_runtime.get("embedding", {}).get("deployment_fingerprint"),
        ),
        "embedding_model": (
            runtime.get("embedding", {}).get("served_model_id"),
            current_runtime.get("embedding", {}).get("served_model_id"),
        ),
        "construction_model": (
            runtime.get("construction", {}).get("served_model_id"),
            current_runtime.get("construction", {}).get("served_model_id"),
        ),
        "vllm_version": (
            runtime.get("construction", {}).get("vllm_version"),
            current_runtime.get("construction", {}).get("vllm_version"),
        ),
        "context_limit": (
            runtime.get("construction", {}).get("max_model_len"),
            current_runtime.get("construction", {}).get("max_model_len"),
        ),
    }
    for field, (observed, expected) in comparable.items():
        if observed is None or expected is None or observed == "" or expected == "":
            reasons.append(f"runtime_identity_missing:{field}")
        elif observed != expected:
            reasons.append(f"runtime_identity_mismatch:{field}")
    required_contract_hashes = u0_contract.get("source_hashes", {})
    for field, expected in required_contract_hashes.items():
        if provenance.get(field) != expected:
            reasons.append(f"execution_path_mismatch:{field}")
    if c2_manifest.get("status") != "completed":
        reasons.append("c2_not_completed")
    if int(c2_manifest.get("episode_count", 0)) != 188:
        reasons.append("c2_episode_count_not_188")
    verification = dict(c2_verification or {})
    integrity_valid = (
        verification.get("status") == "verified"
        and verification.get("run_id") == c2_manifest.get("run_id")
        and isinstance(verification.get("indexed_file_count"), int)
        and verification.get("indexed_file_count", 0) > 0
        and isinstance(verification.get("jsonl_line_count"), int)
        and verification.get("jsonl_line_count", 0) >= 188
        and all(
            _is_sha256(verification.get(field))
            for field in (
                "manifest_sha256",
                "checkpoint_sha256",
                "e1_breakdown_sha256",
                "top_level_e1_breakdown_sha256",
            )
        )
        and c2_manifest.get("telemetry_completeness", {}).get("status") == "complete"
        and isinstance(c2_manifest.get("artifact_inventory"), Mapping)
        and bool(c2_manifest.get("artifact_inventory"))
        and isinstance(c2_manifest.get("artifact_sha256"), Mapping)
        and bool(c2_manifest.get("artifact_sha256"))
        and verification.get("checkpoint_sha256") == c2_manifest.get("checkpoint_sha256")
        and verification.get("e1_breakdown_sha256") == c2_manifest.get("e1_breakdown_sha256")
        and verification.get("top_level_e1_breakdown_sha256")
        == c2_manifest.get("top_level_e1_breakdown_sha256")
    )
    if not integrity_valid:
        reasons.append("c2_integrity_evidence_missing")
    case = "CASE_A_REUSE_C2" if not reasons else "CASE_B_1_HISTORY_U0_QUALIFICATION"
    return {
        "schema_version": S2_SCHEMA,
        "c2_run_id": c2_manifest.get("run_id"),
        "case": case,
        "reasons": reasons,
        "numeric_reuse": "ALLOWED" if case.startswith("CASE_A") else "NOT_ALLOWED",
        "live_authorization": "reuse_4_history" if case.startswith("CASE_A") else "authorize_1_history_only",
        "c2_integrity_verified": integrity_valid,
    }


def write_s2_artifact(path: Path, payload: Mapping[str, Any], *, git_commit: str, run_id: str) -> dict[str, Any]:
    envelope = finalize_envelope(
        payload={**dict(payload), "payload_sha256": payload_sha256(dict(payload))},
        protocol_version="paper-eval-v3",
        git_commit=git_commit,
        run_id=run_id,
    )
    atomic_write_json(path, envelope)
    return envelope
