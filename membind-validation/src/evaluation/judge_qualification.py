"""Crash-consistent artifacts for the bounded Qwen Judge qualification.

This module owns only the frozen synthetic fixture, durable per-item state,
verification, and offline analysis.  HTTP configuration and model transport
remain in ``judge_qualification_live``; characterization state is outside this
writer surface.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import re
import stat
import sys
import unittest
import uuid
import fcntl
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from evaluation.benchmarks.longmemeval import (
    SCORER,
    official_compatible_label,
    parse_audit_label,
)
from evaluation.provenance import validate_judge_upstream_manifest
from evaluation.schemas import EvaluationItem, EvaluationResult, EvaluationStatus
from evaluation.vendor.longmemeval_evaluate_qa import get_anscheck_prompt


JUDGE_QUALIFICATION_ONLY = "JUDGE_QUALIFICATION_ONLY"
PROTOCOL_ID = "judge-qualification-v1.0"
CANONICAL_INCOMPLETE = "incomplete_invalid_non_mergeable"
_RUN_ID_RE = re.compile(r"^jq-[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_WORKPLAN_NAME = "MemBind_JUDGE_QUALIFICATION_WORKPLAN_v1.0.md"
_WORKPLAN_SHA256 = "a2a2d59c538131dae8cb412fed8a4e40ce339a6db321af7e554cf1c2f66f93d8"
_TEST_EVIDENCE_SCHEMA = "membind.judge-test-evidence.v1"
_IMPACT_TEST_PATHS = (
    "tests/test_evaluator_registry.py",
    "tests/test_longmemeval_adapter.py",
    "tests/test_qwen3_judge_backend.py",
)
_Q3_SOURCE_PATHS = (
    "src/evaluation/judge_qualification_q3.py",
    "tests/test_judge_qualification_q3_dry_run.py",
)
_Q3_SCENARIOS = (
    "full_pass",
    "invalid_stop",
    "service_error_stop",
    "tamper",
    "ambiguous_inflight",
)
_LIVE_AUTHORIZATION_KEYS = {
    "schema_version",
    "protocol_id",
    "scientific_surface",
    "authorization_id",
    "authorized_run_id",
    "authorization_path",
    "live_run_limit",
    "freeze_payload_sha256",
    "qualification_live_source_sha256",
    "deployment_evidence_payload_sha256",
    "prelive_evidence_manifest_file_sha256",
    "prelive_evidence_manifest_payload_sha256",
    "payload_sha256",
}
_LIVE_CONSUMPTION_KEYS = {
    "schema_version",
    "status",
    "protocol_id",
    "scientific_surface",
    "authorized_run_id",
    "authorization_path",
    "live_run_limit",
    "authorization_file_sha256",
    "authorization_payload_sha256",
    "prelive_evidence_manifest_file_sha256",
    "prelive_evidence_manifest_payload_sha256",
    "payload_sha256",
}
_PRELIVE_EVIDENCE_KEYS = {
    "schema_version",
    "protocol_id",
    "scientific_surface",
    "authorized_run_id",
    "live_run_limit",
    "workplan_sha256",
    "judge_tests_aggregate_sha256",
    "bindings",
    "payload_sha256",
}
_PRELIVE_BINDING_KEYS = {
    "qualification_source",
    "qualification_live_source",
    "qualification_q3_source",
    "judge_tests",
    "qualification_fixture",
    "offline_manifest",
    "deployment_evidence",
    "final_focused_report",
    "final_impact_report",
    "final_q3_dry_run_report",
    "strict_freeze",
}
_ROUTES = (
    ("single-session-user", False, "single-session-user"),
    ("single-session-assistant", False, "single-session-assistant"),
    ("multi-session", False, "multi-session"),
    ("temporal-reasoning", False, "temporal-reasoning"),
    ("knowledge-update", False, "knowledge-update"),
    ("single-session-preference", False, "single-session-preference"),
    ("single-session-user", True, "abstention"),
)

STRICT_PASS_GATE: dict[str, Any] = {
    "planned_item_count": 14,
    "terminal_item_count": 14,
    "eligible_item_count": 14,
    "agreement_count": 14,
    "invalid_output_count": 0,
    "service_error_count": 0,
    "retry_count_total": 0,
    "confusion_matrix": {
        "true_positive": 7,
        "true_negative": 7,
        "false_positive": 0,
        "false_negative": 0,
    },
    "observed_agreement": 1.0,
    "cohens_kappa": 1.0,
}


class JudgeQualificationArtifactError(RuntimeError):
    """Raised when qualification evidence cannot be trusted or resumed."""


def canonical_json_bytes(value: object) -> bytes:
    """Return the single canonical ASCII representation used by all seals."""

    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise JudgeQualificationArtifactError("artifact is not canonical JSON") from error


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _sealed(value: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(value))
    result.pop("payload_sha256", None)
    result["payload_sha256"] = _sha256(canonical_json_bytes(result))
    return result


def _validate_seal(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise JudgeQualificationArtifactError(f"{label} must be an object")
    candidate = deepcopy(value)
    observed = candidate.pop("payload_sha256", None)
    if not _is_sha256(observed) or observed != _sha256(canonical_json_bytes(candidate)):
        raise JudgeQualificationArtifactError(f"{label} payload seal mismatch")
    return value


def _assert_secret_safe(value: object) -> None:
    """Reject private connection material before creating any run directory."""

    forbidden_keys = ("api_key", "authorization", "base_url", "password", "secret")

    def visit(candidate: object) -> None:
        if isinstance(candidate, Mapping):
            for key, nested in candidate.items():
                rendered_key = str(key).casefold().replace("-", "_")
                if any(token in rendered_key for token in forbidden_keys):
                    raise JudgeQualificationArtifactError("private field is not artifact-safe")
                visit(nested)
        elif isinstance(candidate, (list, tuple)):
            for nested in candidate:
                visit(nested)
        elif isinstance(candidate, str) and "bearer " in candidate.casefold():
            raise JudgeQualificationArtifactError("private value is not artifact-safe")

    visit(value)


def _validate_live_evidence_schemas(
    authorization: object,
    consumption: object,
    prelive_evidence: object,
) -> None:
    """Reject extended or private live evidence before copying or auditing it."""

    if (
        not isinstance(authorization, dict)
        or set(authorization) != _LIVE_AUTHORIZATION_KEYS
        or not isinstance(consumption, dict)
        or set(consumption) != _LIVE_CONSUMPTION_KEYS
        or not isinstance(prelive_evidence, dict)
        or set(prelive_evidence) != _PRELIVE_EVIDENCE_KEYS
    ):
        raise JudgeQualificationArtifactError("live authorization evidence schema is invalid")
    bindings = prelive_evidence.get("bindings")
    if not isinstance(bindings, dict) or set(bindings) != _PRELIVE_BINDING_KEYS:
        raise JudgeQualificationArtifactError("pre-live evidence schema is invalid")
    expected_binding_shapes = {
        "qualification_source": {"path", "sha256"},
        "qualification_live_source": {"path", "sha256"},
        "qualification_q3_source": {"path", "sha256"},
        "qualification_fixture": {"path", "sha256", "payload_sha256"},
        "offline_manifest": {"path", "sha256", "payload_sha256"},
        "deployment_evidence": {"path", "sha256", "payload_sha256"},
        "final_focused_report": {"path", "sha256", "payload_sha256"},
        "final_impact_report": {"path", "sha256", "payload_sha256"},
        "final_q3_dry_run_report": {"path", "sha256", "payload_sha256"},
        "strict_freeze": {"path", "sha256", "payload_sha256"},
    }
    tests = bindings.get("judge_tests")
    if not isinstance(tests, list) or any(
        not isinstance(binding, dict) or set(binding) != {"path", "sha256"}
        for binding in tests
    ):
        raise JudgeQualificationArtifactError("pre-live Judge test schema is invalid")
    if any(
        not isinstance(bindings.get(name), dict)
        or set(bindings[name]) != expected
        for name, expected in expected_binding_shapes.items()
    ):
        raise JudgeQualificationArtifactError("pre-live evidence binding schema is invalid")

    authorization_id = authorization.get("authorization_id")
    if not isinstance(authorization_id, str) or not authorization_id:
        raise JudgeQualificationArtifactError("live authorization identity is invalid")

    # Top-level keys are the exact public schema. Inspect nested keys and all
    # values so a nominal scalar field cannot carry private material.
    forbidden_keys = ("api_key", "authorization", "base_url", "password", "secret")

    def reject_private_value(candidate: object) -> None:
        if isinstance(candidate, Mapping):
            for key, nested in candidate.items():
                rendered_key = str(key).casefold().replace("-", "_")
                if any(token in rendered_key for token in forbidden_keys):
                    raise JudgeQualificationArtifactError(
                        "private field is not artifact-safe"
                    )
                reject_private_value(nested)
        elif isinstance(candidate, (list, tuple)):
            for nested in candidate:
                reject_private_value(nested)
        elif isinstance(candidate, str) and "bearer " in candidate.casefold():
            raise JudgeQualificationArtifactError("private value is not artifact-safe")

    for document in (authorization, consumption, prelive_evidence):
        for value in document.values():
            reject_private_value(value)


def _rooted_file(root: Path, relative: object) -> Path:
    if not isinstance(relative, (str, Path)):
        raise JudgeQualificationArtifactError("artifact binding path is invalid")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise JudgeQualificationArtifactError("artifact binding escapes validation root")
    unresolved = root / rel
    if unresolved.is_symlink():
        raise JudgeQualificationArtifactError("artifact binding may not be a symlink")
    resolved = unresolved.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise JudgeQualificationArtifactError("artifact binding escapes validation root") from error
    if not resolved.is_file():
        raise JudgeQualificationArtifactError("artifact binding is not a file")
    return resolved


def _read_canonical_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = _read_regular_bytes(path, label)
        value = json.loads(raw.decode("ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise JudgeQualificationArtifactError(f"{label} is unreadable") from error
    if not isinstance(value, dict) or raw != canonical_json_bytes(value) + b"\n":
        raise JudgeQualificationArtifactError(f"{label} is not canonical")
    return value


def _read_regular_bytes(path: Path, label: str) -> bytes:
    """Read one evidence file without following a substituted symlink."""

    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise JudgeQualificationArtifactError(f"{label} is not a regular file")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            return handle.read()
    except JudgeQualificationArtifactError:
        raise
    except OSError as error:
        raise JudgeQualificationArtifactError(f"{label} is unreadable") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _artifact_binding(root: Path, value: object, label: str) -> Path:
    """Rebuild one bound file hash without trusting the copied manifest."""

    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise JudgeQualificationArtifactError(f"{label} binding is invalid")
    path = _rooted_file(root, value["path"])
    if path.relative_to(root).as_posix() != value["path"] or not _is_sha256(
        value["sha256"]
    ) or _sha256(_read_regular_bytes(path, label)) != value["sha256"]:
        raise JudgeQualificationArtifactError(f"{label} binding drifted")
    return path


def _json_artifact_binding(root: Path, value: object, label: str) -> tuple[Path, dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != {
        "path",
        "sha256",
        "payload_sha256",
    }:
        raise JudgeQualificationArtifactError(f"{label} binding is invalid")
    path = _rooted_file(root, value["path"])
    raw = _read_regular_bytes(path, label)
    document = _read_canonical_json(path, label)
    if "payload_sha256" in document:
        _validate_seal(document, label)
        payload_sha256 = document["payload_sha256"]
    else:
        payload_sha256 = _sha256(canonical_json_bytes(document))
    if (
        path.relative_to(root).as_posix() != value["path"]
        or not _is_sha256(value["sha256"])
        or value["sha256"] != _sha256(raw)
        or value["payload_sha256"] != payload_sha256
    ):
        raise JudgeQualificationArtifactError(f"{label} binding drifted")
    return path, document


def _expected_test_sources(root: Path, suite_id: str) -> list[Path]:
    if suite_id == "focused":
        return sorted(
            path.relative_to(root)
            for path in root.joinpath("tests").glob("test_judge*.py")
            if path.is_file() and not path.is_symlink()
        )
    if suite_id == "impact":
        return [Path(path) for path in _IMPACT_TEST_PATHS]
    if suite_id == "q3":
        return [Path(path) for path in _Q3_SOURCE_PATHS]
    raise JudgeQualificationArtifactError("Judge test evidence suite is invalid")


def _loaded_test_count(root: Path, suite_id: str, sources: list[Path]) -> int:
    test_sources = sources[1:] if suite_id == "q3" else sources
    original_path = list(sys.path)
    try:
        root_text = str(root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromNames(
            [".".join(path.with_suffix("").parts) for path in test_sources]
        )
    finally:
        sys.path[:] = original_path
    if loader.errors or suite.countTestCases() < 1:
        raise JudgeQualificationArtifactError("Judge test evidence inventory does not load")
    return suite.countTestCases()


def _validate_raw_test_log(raw: bytes, expected_count: int) -> None:
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise JudgeQualificationArtifactError("Judge test evidence log is not ASCII") from error
    counts = re.findall(r"(?m)^Ran ([0-9]+) tests? in [^\r\n]+\r?$", text)
    terminal = [line.strip() for line in text.splitlines() if line.strip()]
    if (
        len(counts) != 1
        or int(counts[0]) != expected_count
        or not terminal
        or terminal[-1] != "OK"
        or re.search(r"(?m)^FAILED(?: |$)", text) is not None
    ):
        raise JudgeQualificationArtifactError("Judge test evidence log is not GREEN")


def _validate_test_evidence_report(root: Path, value: object, suite_id: str) -> None:
    """Independently reconstruct one run-local structured test report."""

    expected_keys = {
        "schema_version",
        "protocol_id",
        "scientific_surface",
        "suite_id",
        "status",
        "exit_code",
        "test_count",
        "source_inventory",
        "raw_log",
        "q3_summary",
        "payload_sha256",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise JudgeQualificationArtifactError("Judge test evidence report is invalid")
    _validate_seal(value, "Judge test evidence report")
    sources = _expected_test_sources(root, suite_id)
    observed_inventory = value.get("source_inventory")
    if not isinstance(observed_inventory, list) or len(observed_inventory) != len(sources):
        raise JudgeQualificationArtifactError("Judge test evidence inventory is invalid")
    for observed, expected in zip(observed_inventory, sources):
        path = _artifact_binding(root, observed, "Judge test evidence source")
        if path.relative_to(root) != expected:
            raise JudgeQualificationArtifactError("Judge test evidence inventory drifted")
    expected_count = _loaded_test_count(root, suite_id, sources)
    q3_summary = value.get("q3_summary")
    expected_q3 = {
        "schema_version": "membind.judge-q3-dry-run-summary.v1",
        "status": "GREEN",
        "scenarios": list(_Q3_SCENARIOS),
        "real_external_requests": 0,
        "live_authorization_created": False,
    }
    q3_summary_valid = q3_summary is None
    if suite_id == "q3":
        q3_summary_valid = (
            isinstance(q3_summary, dict)
            and set(q3_summary) == set(expected_q3)
            and q3_summary.get("schema_version") == expected_q3["schema_version"]
            and q3_summary.get("status") == "GREEN"
            and type(q3_summary.get("scenarios")) is list
            and q3_summary.get("scenarios") == expected_q3["scenarios"]
            and type(q3_summary.get("real_external_requests")) is int
            and q3_summary.get("real_external_requests") == 0
            and type(q3_summary.get("live_authorization_created")) is bool
            and q3_summary.get("live_authorization_created") is False
        )
    if (
        value.get("schema_version") != _TEST_EVIDENCE_SCHEMA
        or value.get("protocol_id") != PROTOCOL_ID
        or value.get("scientific_surface") != JUDGE_QUALIFICATION_ONLY
        or value.get("suite_id") != suite_id
        or value.get("status") != "GREEN"
        or type(value.get("exit_code")) is not int
        or value.get("exit_code") != 0
        or type(value.get("test_count")) is not int
        or value.get("test_count") != expected_count
        or not q3_summary_valid
    ):
        raise JudgeQualificationArtifactError("Judge test evidence semantics are invalid")
    log_path = _artifact_binding(root, value.get("raw_log"), "Judge test evidence log")
    _validate_raw_test_log(_read_regular_bytes(log_path, "Judge test evidence log"), expected_count)


def _validate_prelive_closure(root: Path, prelive: dict[str, Any]) -> None:
    """Verify the copied pre-live closure without importing the live wrapper."""

    if (
        prelive.get("schema_version") != "membind.judge-prelive-evidence-manifest.v1"
        or prelive.get("protocol_id") != PROTOCOL_ID
        or prelive.get("scientific_surface") != JUDGE_QUALIFICATION_ONLY
        or prelive.get("live_run_limit") != 1
        or prelive.get("workplan_sha256") != _WORKPLAN_SHA256
    ):
        raise JudgeQualificationArtifactError("pre-live evidence semantics are invalid")
    workplan = _rooted_file(root.parent, _WORKPLAN_NAME)
    if _sha256(_read_regular_bytes(workplan, "Judge workplan")) != _WORKPLAN_SHA256:
        raise JudgeQualificationArtifactError("Judge workplan binding drifted")
    bindings = prelive["bindings"]
    expected_tests = _expected_test_sources(root, "focused")
    observed_tests = bindings["judge_tests"]
    if not isinstance(observed_tests, list) or len(observed_tests) != len(expected_tests):
        raise JudgeQualificationArtifactError("pre-live Judge test set is incomplete")
    for observed, expected in zip(observed_tests, expected_tests):
        path = _artifact_binding(root, observed, "pre-live Judge test")
        if path.relative_to(root) != expected:
            raise JudgeQualificationArtifactError("pre-live Judge test set drifted")
    if prelive.get("judge_tests_aggregate_sha256") != _sha256(
        canonical_json_bytes(observed_tests)
    ):
        raise JudgeQualificationArtifactError("pre-live Judge test aggregate drifted")

    fixed_paths = {
        "qualification_source": "src/evaluation/judge_qualification.py",
        "qualification_live_source": "src/evaluation/judge_qualification_live.py",
        "qualification_q3_source": "src/evaluation/judge_qualification_q3.py",
        "qualification_fixture": "fixtures/judge_qualification_14_v1.json",
        "offline_manifest": "artifacts/protocol/judge_upstream_manifest_20260812.json",
        "deployment_evidence": "artifacts/environment/judge_deployment_evidence_20260813.json",
    }
    for name in ("qualification_source", "qualification_live_source", "qualification_q3_source"):
        path = _artifact_binding(root, bindings[name], f"pre-live {name}")
        if path.relative_to(root).as_posix() != fixed_paths[name]:
            raise JudgeQualificationArtifactError("pre-live frozen source path drifted")
    for name in ("qualification_fixture", "offline_manifest", "deployment_evidence"):
        path, _document = _json_artifact_binding(root, bindings[name], f"pre-live {name}")
        if path.relative_to(root).as_posix() != fixed_paths[name]:
            raise JudgeQualificationArtifactError("pre-live frozen evidence path drifted")
    for binding_name, suite_id in (
        ("final_focused_report", "focused"),
        ("final_impact_report", "impact"),
        ("final_q3_dry_run_report", "q3"),
    ):
        _path, report = _json_artifact_binding(
            root, bindings[binding_name], f"pre-live {binding_name}"
        )
        _validate_test_evidence_report(root, report, suite_id)
    _freeze_path, strict_freeze = _json_artifact_binding(
        root, bindings["strict_freeze"], "pre-live strict freeze"
    )
    validate_strict_judge_qualification_freeze(strict_freeze, root)


def _exclusive_bytes(path: Path, raw: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    _exclusive_bytes(path, canonical_json_bytes(value) + b"\n")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        _exclusive_json(temporary, value)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def _official_prompt_hash(record: Mapping[str, Any]) -> str:
    prompt = get_anscheck_prompt(
        record["question_type"],
        record["question"],
        record["reference_answer"],
        record["hypothesis"],
        record["abstention"],
    )
    return _sha256(prompt.encode("utf-8"))


def _normalize_fixture(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        raise JudgeQualificationArtifactError("qualification fixture must be an object")
    if (
        value.get("schema_version") != "membind.judge-qualification-fixture.v1"
        or value.get("scientific_surface") != JUDGE_QUALIFICATION_ONLY
        or not isinstance(value.get("items"), list)
        or len(value["items"]) != 14
    ):
        raise JudgeQualificationArtifactError("qualification fixture identity is invalid")
    expected_route_labels = [
        (question_type, abstention, route_id, human_label)
        for question_type, abstention, route_id in _ROUTES
        for human_label in (True, False)
    ]
    required_strings = (
        "item_id",
        "benchmark",
        "question_id",
        "candidate_answer_id",
        "question_type",
        "question",
        "reference_answer",
        "hypothesis",
        "route_id",
    )
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, (raw, expected) in enumerate(zip(value["items"], expected_route_labels)):
        if not isinstance(raw, dict):
            raise JudgeQualificationArtifactError("qualification fixture item is invalid")
        if any(not isinstance(raw.get(name), str) or not raw[name] for name in required_strings):
            raise JudgeQualificationArtifactError("qualification fixture string field is invalid")
        if raw["benchmark"] != "longmemeval":
            raise JudgeQualificationArtifactError("qualification fixture benchmark is invalid")
        question_type, abstention, route_id, human_label = expected
        if (
            raw["question_type"] != question_type
            or raw.get("abstention") is not abstention
            or raw["route_id"] != route_id
            or raw.get("human_label") is not human_label
        ):
            raise JudgeQualificationArtifactError("qualification fixture route/order is invalid")
        if raw["item_id"] in seen_ids:
            raise JudgeQualificationArtifactError("qualification fixture item IDs are duplicated")
        seen_ids.add(raw["item_id"])
        record = {name: deepcopy(raw[name]) for name in required_strings}
        record["abstention"] = abstention
        record["human_label"] = human_label
        record["item_index"] = index
        record["official_prompt_sha256"] = _official_prompt_hash(record)
        normalized.append(record)
    return normalized


def _binding(root: Path, relative: object, expected_sha256: object) -> dict[str, str]:
    if not _is_sha256(expected_sha256):
        raise JudgeQualificationArtifactError("artifact binding SHA256 is invalid")
    path = _rooted_file(root, relative)
    observed = _sha256(path.read_bytes())
    if observed != expected_sha256:
        raise JudgeQualificationArtifactError("artifact binding SHA256 mismatch")
    return {"path": Path(relative).as_posix(), "sha256": observed}


def build_judge_qualification_freeze(
    *,
    validation_root: Path,
    fixture_path: Path,
    fixture_sha256: str,
    offline_manifest_path: Path,
    offline_manifest_sha256: str,
    qualification_source_path: Path,
    qualification_source_sha256: str,
) -> dict[str, Any]:
    """Build the immutable 14-item freeze from content-addressed local inputs."""

    root = Path(validation_root).resolve(strict=True)
    fixture_binding = _binding(root, fixture_path, fixture_sha256)
    offline_binding = _binding(root, offline_manifest_path, offline_manifest_sha256)
    source_binding = _binding(root, qualification_source_path, qualification_source_sha256)
    fixture_value = _read_canonical_json(root / fixture_binding["path"], "fixture")
    items = _normalize_fixture(fixture_value)
    offline_value = _read_canonical_json(
        root / offline_binding["path"], "offline Judge manifest"
    )
    offline_payload = offline_value.get("payload_sha256")
    if not _is_sha256(offline_payload):
        raise JudgeQualificationArtifactError("offline Judge manifest seal is invalid")
    freeze = {
        "schema_version": "membind.judge-qualification-freeze.v1",
        "protocol_id": PROTOCOL_ID,
        "scientific_surface": JUDGE_QUALIFICATION_ONLY,
        "fixture_schema_version": fixture_value["schema_version"],
        "items": items,
        "strict_pass_gate": deepcopy(STRICT_PASS_GATE),
        "offline_manifest_payload_sha256": offline_payload,
        "bindings": {
            "offline_manifest": offline_binding,
            "qualification_fixture": fixture_binding,
            "qualification_source": source_binding,
        },
        "creation_contract": {
            "human_labels_precede_dispatch": True,
            "live_response_visible": False,
            "builder": "build_judge_qualification_freeze",
        },
    }
    return _sealed(freeze)


def _validate_freeze_structure(value: object) -> dict[str, Any]:
    freeze = _validate_seal(value, "qualification freeze")
    if (
        freeze.get("schema_version") != "membind.judge-qualification-freeze.v1"
        or freeze.get("protocol_id") != PROTOCOL_ID
        or freeze.get("scientific_surface") != JUDGE_QUALIFICATION_ONLY
        or freeze.get("strict_pass_gate") != STRICT_PASS_GATE
        or freeze.get("creation_contract")
        != {
            "human_labels_precede_dispatch": True,
            "live_response_visible": False,
            "builder": "build_judge_qualification_freeze",
        }
        or not _is_sha256(freeze.get("offline_manifest_payload_sha256"))
    ):
        raise JudgeQualificationArtifactError("qualification freeze contract is invalid")
    items = freeze.get("items")
    if not isinstance(items, list) or len(items) != 14:
        raise JudgeQualificationArtifactError("qualification freeze item count is invalid")
    expected = [
        (question_type, abstention, route_id, label)
        for question_type, abstention, route_id in _ROUTES
        for label in (True, False)
    ]
    for index, (item, route) in enumerate(zip(items, expected)):
        question_type, abstention, route_id, label = route
        if (
            not isinstance(item, dict)
            or item.get("item_index") != index
            or item.get("question_type") != question_type
            or item.get("abstention") is not abstention
            or item.get("route_id") != route_id
            or item.get("human_label") is not label
            or item.get("benchmark") != "longmemeval"
            or not _is_sha256(item.get("official_prompt_sha256"))
            or item.get("official_prompt_sha256") != _official_prompt_hash(item)
        ):
            raise JudgeQualificationArtifactError("qualification freeze item is invalid")
    return freeze


def validate_judge_qualification_freeze(
    value: dict[str, Any], validation_root: Path
) -> dict[str, Any]:
    """Reject a re-sealed mutation by rebuilding from all three bound files."""

    freeze = _validate_freeze_structure(value)
    bindings = freeze.get("bindings")
    if not isinstance(bindings, dict) or set(bindings) != {
        "offline_manifest",
        "qualification_fixture",
        "qualification_source",
    }:
        raise JudgeQualificationArtifactError("qualification freeze bindings are invalid")
    try:
        rebuilt = build_judge_qualification_freeze(
            validation_root=validation_root,
            fixture_path=Path(bindings["qualification_fixture"]["path"]),
            fixture_sha256=bindings["qualification_fixture"]["sha256"],
            offline_manifest_path=Path(bindings["offline_manifest"]["path"]),
            offline_manifest_sha256=bindings["offline_manifest"]["sha256"],
            qualification_source_path=Path(bindings["qualification_source"]["path"]),
            qualification_source_sha256=bindings["qualification_source"]["sha256"],
        )
    except (KeyError, TypeError) as error:
        raise JudgeQualificationArtifactError("qualification freeze bindings are invalid") from error
    if freeze != rebuilt:
        raise JudgeQualificationArtifactError("qualification freeze differs from bound inputs")
    return value


def build_strict_judge_qualification_freeze(
    *,
    validation_root: Path,
    fixture_path: Path,
    offline_manifest_path: Path,
    qualification_source_path: Path,
    qualification_live_source_path: Path,
) -> dict[str, Any]:
    """Build the production freeze after strict upstream provenance validation."""

    root = Path(validation_root).resolve(strict=True)
    fixture = _rooted_file(root, fixture_path)
    offline = _rooted_file(root, offline_manifest_path)
    source = _rooted_file(root, qualification_source_path)
    live_source = _rooted_file(root, qualification_live_source_path)
    offline_value = _read_canonical_json(offline, "offline Judge manifest")
    validate_judge_upstream_manifest(offline_value, root)
    freeze = build_judge_qualification_freeze(
        validation_root=root,
        fixture_path=fixture_path,
        fixture_sha256=_sha256(fixture.read_bytes()),
        offline_manifest_path=offline_manifest_path,
        offline_manifest_sha256=_sha256(offline.read_bytes()),
        qualification_source_path=qualification_source_path,
        qualification_source_sha256=_sha256(source.read_bytes()),
    )
    freeze["bindings"]["qualification_live_source"] = {
        "path": Path(qualification_live_source_path).as_posix(),
        "sha256": _sha256(live_source.read_bytes()),
    }
    freeze["upstream_manifest_validation"] = "strict_regeneration_match"
    return _sealed(freeze)


def validate_strict_judge_qualification_freeze(
    value: dict[str, Any], validation_root: Path
) -> dict[str, Any]:
    """Rebuild the production freeze so re-sealed source drift fails closed."""

    freeze = _validate_freeze_structure(value)
    bindings = freeze.get("bindings")
    if (
        not isinstance(bindings, dict)
        or set(bindings)
        != {
            "offline_manifest",
            "qualification_fixture",
            "qualification_source",
            "qualification_live_source",
        }
        or freeze.get("upstream_manifest_validation") != "strict_regeneration_match"
    ):
        raise JudgeQualificationArtifactError("strict qualification freeze is invalid")
    try:
        rebuilt = build_strict_judge_qualification_freeze(
            validation_root=validation_root,
            fixture_path=Path(bindings["qualification_fixture"]["path"]),
            offline_manifest_path=Path(bindings["offline_manifest"]["path"]),
            qualification_source_path=Path(bindings["qualification_source"]["path"]),
            qualification_live_source_path=Path(
                bindings["qualification_live_source"]["path"]
            ),
        )
    except (KeyError, TypeError) as error:
        raise JudgeQualificationArtifactError("strict freeze bindings are invalid") from error
    if rebuilt != freeze:
        raise JudgeQualificationArtifactError("strict freeze differs from bound inputs")
    return value


def _audit_label(result: EvaluationResult) -> bool | None:
    value = result.metadata.get("audit_label")
    return value if type(value) is bool else None


def _validate_result_contract(result: EvaluationResult) -> EvaluationResult:
    """Bind persisted labels to the official parser and independent audit view."""

    if result.scorer != SCORER or result.judge_model != "qwen3-32b-fp8":
        raise JudgeQualificationArtifactError("terminal result scorer/model is invalid")
    if result.status is EvaluationStatus.SERVICE_ERROR:
        if (
            result.raw_output != ""
            or result.normalized_output != ""
            or result.parse_status != "NOT_RUN"
            or result.label is not None
        ):
            raise JudgeQualificationArtifactError("terminal service result is inconsistent")
        return result

    parsed = parse_audit_label(result.raw_output)
    official = official_compatible_label(result.raw_output)
    metadata_audit = result.metadata.get("audit_label")
    if (
        result.normalized_output != parsed.normalized_output
        or result.parse_status != parsed.parse_status
        or result.label is not official
    ):
        raise JudgeQualificationArtifactError("terminal result parser binding is invalid")
    if result.status is EvaluationStatus.SUCCESS:
        if (
            parsed.label is None
            or metadata_audit is not parsed.label
            or result.label is not parsed.label
        ):
            raise JudgeQualificationArtifactError("terminal result audit label is invalid")
    elif (
        result.status is not EvaluationStatus.INVALID_OUTPUT
        or parsed.label is not None
        or metadata_audit is not None
    ):
        raise JudgeQualificationArtifactError("terminal invalid result audit state is invalid")
    return result


def _cohens_kappa(human: Sequence[bool], predicted: Sequence[bool]) -> float:
    count = len(human)
    if count == 0:
        return 0.0
    observed = sum(left is right for left, right in zip(human, predicted)) / count
    human_yes = sum(human) / count
    predicted_yes = sum(predicted) / count
    expected = human_yes * predicted_yes + (1 - human_yes) * (1 - predicted_yes)
    if math.isclose(expected, 1.0):
        return 1.0 if math.isclose(observed, 1.0) else 0.0
    return (observed - expected) / (1 - expected)


def analyze_judge_qualification(
    freeze: Mapping[str, Any], results: Iterable[EvaluationResult]
) -> dict[str, Any]:
    """Compute the frozen SUCCESS-only agreement gate without coercing errors."""

    frozen = _validate_freeze_structure(dict(freeze))
    materialized = list(results)
    if any(not isinstance(result, EvaluationResult) for result in materialized):
        raise JudgeQualificationArtifactError("qualification result type is invalid")
    item_by_id = {item["item_id"]: item for item in frozen["items"]}
    if len({result.item_id for result in materialized}) != len(materialized):
        raise JudgeQualificationArtifactError("qualification results are duplicated")
    if any(result.item_id not in item_by_id for result in materialized):
        raise JudgeQualificationArtifactError("qualification result is not frozen")

    eligible: list[tuple[bool, bool]] = []
    invalid_count = 0
    service_count = 0
    retry_total = 0
    for result in materialized:
        _validate_result_contract(result)
        retry_total += result.retry_count
        if result.status is EvaluationStatus.SUCCESS:
            audit = _audit_label(result)
            if audit is None:
                raise JudgeQualificationArtifactError("successful result lacks strict audit label")
            eligible.append((bool(item_by_id[result.item_id]["human_label"]), audit))
        elif result.status is EvaluationStatus.INVALID_OUTPUT:
            invalid_count += 1
        elif result.status is EvaluationStatus.SERVICE_ERROR:
            service_count += 1

    true_positive = sum(human and predicted for human, predicted in eligible)
    true_negative = sum(not human and not predicted for human, predicted in eligible)
    false_positive = sum(not human and predicted for human, predicted in eligible)
    false_negative = sum(human and not predicted for human, predicted in eligible)
    agreement = true_positive + true_negative
    eligible_count = len(eligible)
    observed = agreement / eligible_count if eligible_count else 0.0
    confusion = {
        "true_positive": true_positive,
        "true_negative": true_negative,
        "false_positive": false_positive,
        "false_negative": false_negative,
    }
    summary: dict[str, Any] = {
        "scientific_surface": JUDGE_QUALIFICATION_ONLY,
        "planned_item_count": len(frozen["items"]),
        "terminal_item_count": len(materialized),
        "eligible_item_count": eligible_count,
        "agreement_count": agreement,
        "invalid_output_count": invalid_count,
        "service_error_count": service_count,
        "retry_count_total": retry_total,
        "confusion_matrix": confusion,
        "observed_agreement": observed,
        "cohens_kappa": _cohens_kappa(
            [human for human, _ in eligible], [predicted for _, predicted in eligible]
        ),
    }
    failed: list[str] = []
    for field, expected in STRICT_PASS_GATE.items():
        if summary[field] != expected:
            failed.append(field)
    summary["failed_gate_fields"] = failed
    summary["qualification_status"] = "PASS" if not failed else "FAIL"
    return summary


def _validate_runtime_identity(identity: object, freeze: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(identity, dict):
        raise JudgeQualificationArtifactError("runtime identity must be an object")
    _assert_secret_safe(identity)
    required_strings = (
        "served_model_name",
        "vllm_version",
        "dtype",
        "quantization",
        "python_version",
        "openai_sdk_version",
        "httpx_version",
    )
    if any(not isinstance(identity.get(name), str) or not identity[name] for name in required_strings):
        raise JudgeQualificationArtifactError("runtime identity string field is invalid")
    digest_fields = (
        "chat_template_sha256",
        "endpoint_identity_sha256",
        "runtime_backend_config_hash",
        "offline_manifest_file_sha256",
        "offline_manifest_payload_sha256",
    )
    if any(not _is_sha256(identity.get(name)) for name in digest_fields):
        raise JudgeQualificationArtifactError("runtime identity digest field is invalid")
    repository_revision = identity.get("repository_revision")
    model_fingerprint = identity.get("model_fingerprint")
    model_identity_valid = (
        isinstance(repository_revision, str)
        and _REVISION_RE.fullmatch(repository_revision) is not None
        and model_fingerprint is None
    ) or (
        repository_revision is None and _is_sha256(model_fingerprint)
    )
    if not model_identity_valid:
        raise JudgeQualificationArtifactError(
            "runtime identity requires exactly one verified model identity"
        )
    rope = identity.get("rope_parameters")
    exact = (
        identity["served_model_name"] == "qwen3-32b-fp8"
        and identity["vllm_version"] == "0.26.0"
        and identity["max_model_len"] == 65536
        and identity["effective_enable_thinking"] is False
        and identity["temperature"] == 0
        and identity["max_tokens"] == 10
        and identity["n"] == 1
        and isinstance(rope, dict)
        and rope
        == {
            "rope_type": "yarn",
            "factor": 2.0,
            "original_max_position_embeddings": 32768,
            "rope_theta": 1000000,
        }
        and identity["offline_manifest_file_sha256"]
        == freeze["bindings"]["offline_manifest"]["sha256"]
        and identity["offline_manifest_payload_sha256"]
        == freeze["offline_manifest_payload_sha256"]
    )
    if not exact:
        raise JudgeQualificationArtifactError("runtime identity does not match frozen envelope")
    if freeze.get("upstream_manifest_validation") == "strict_regeneration_match":
        backend_config = identity.get("backend_public_config")
        evidence = identity.get("evidence_bindings")
        deployment_binding = identity.get("deployment_evidence_binding")
        if (
            not isinstance(backend_config, dict)
            or _sha256(canonical_json_bytes(backend_config))
            != identity["runtime_backend_config_hash"]
            or backend_config
            != {
                "backend": "openai_compatible_chat_completions",
                "served_model_name": "qwen3-32b-fp8",
                "endpoint_identity_sha256": identity["endpoint_identity_sha256"],
                "temperature": 0,
                "max_tokens": 10,
                "n": 1,
                "thinking_control": "client_request",
                "effective_enable_thinking": False,
                "max_attempts": 1,
                "timeout_seconds": 30.0,
                "retry_delays_seconds": [0.0],
                "sdk_hidden_retries": 0,
            }
            or not isinstance(evidence, dict)
            or not evidence
            or not isinstance(deployment_binding, dict)
            or set(deployment_binding) != {"path", "sha256", "payload_sha256"}
            or not isinstance(deployment_binding.get("path"), str)
            or not deployment_binding["path"]
            or Path(deployment_binding["path"]).is_absolute()
            or ".." in Path(deployment_binding["path"]).parts
            or not _is_sha256(deployment_binding.get("sha256"))
            or not _is_sha256(deployment_binding.get("payload_sha256"))
        ):
            raise JudgeQualificationArtifactError(
                "strict runtime backend/evidence identity is invalid"
            )
    return deepcopy(identity)


def _result_dict(result: EvaluationResult) -> dict[str, Any]:
    value = asdict(result)
    value["status"] = result.status.value
    # Round-trip now so later caller mutation of metadata cannot alter evidence.
    return json.loads(canonical_json_bytes(value).decode("ascii"))


def _result_from_dict(value: object) -> EvaluationResult:
    if not isinstance(value, dict):
        raise JudgeQualificationArtifactError("terminal result is invalid")
    try:
        candidate = deepcopy(value)
        candidate["status"] = EvaluationStatus(candidate["status"])
        return EvaluationResult(**candidate)
    except (KeyError, TypeError, ValueError) as error:
        raise JudgeQualificationArtifactError("terminal result is invalid") from error


class JudgeQualificationArtifactStore:
    """Exclusive run directory with hash-chained events and terminal checkpoints."""

    def __init__(
        self,
        *,
        run_dir: Path,
        freeze: dict[str, Any],
        runtime_identity: dict[str, Any],
        manifest: dict[str, Any],
    ) -> None:
        self.run_dir = run_dir
        self.freeze = deepcopy(freeze)
        self.runtime_identity = deepcopy(runtime_identity)
        self.manifest = deepcopy(manifest)
        self.manifest_path = run_dir / "manifest.json"
        self.freeze_path = run_dir / "fixture_freeze.json"
        self.runtime_identity_path = run_dir / "runtime_identity.json"
        self.events_path = run_dir / "events.jsonl"
        self.checkpoint_path = run_dir / "checkpoint.json"
        self.summary_path = run_dir / "qualification_summary.json"
        self.authorization_path = run_dir / "live_authorization.json"
        self.authorization_consumption_path = (
            run_dir / "live_authorization_consumption.json"
        )
        self.prelive_evidence_path = run_dir / "prelive_evidence_manifest.json"
        self.lock_path = run_dir / "run.lock"
        self._lock_descriptor: int | None = None

    @classmethod
    def create(
        cls,
        *,
        runs_root: Path,
        run_id: str,
        freeze: dict[str, Any],
        runtime_identity: dict[str, Any],
        command_argv: Sequence[str],
        live_authorization_documents: Mapping[str, bytes] | None = None,
        prelive_evidence_document: bytes | None = None,
    ) -> "JudgeQualificationArtifactStore":
        frozen = _validate_freeze_structure(freeze)
        if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
            raise JudgeQualificationArtifactError("qualification run ID is invalid")
        identity = _validate_runtime_identity(runtime_identity, frozen)
        if (
            not isinstance(command_argv, (list, tuple))
            or not command_argv
            or any(not isinstance(value, str) or not value for value in command_argv)
        ):
            raise JudgeQualificationArtifactError("qualification command is invalid")
        _assert_secret_safe(command_argv)
        root = Path(runs_root)
        if root.is_symlink():
            raise JudgeQualificationArtifactError("qualification runs root may not be a symlink")
        run_dir = root / run_id
        if run_dir.parent != root or run_dir.is_symlink():
            raise JudgeQualificationArtifactError("qualification run path is invalid")

        runtime_payload = _sealed(
            {
                "schema_version": "membind.judge-runtime-identity.v1",
                "run_id": run_id,
                "identity": identity,
            }
        )
        freeze_raw = canonical_json_bytes(frozen) + b"\n"
        runtime_raw = canonical_json_bytes(runtime_payload) + b"\n"
        authorization_raw: bytes | None = None
        consumption_raw: bytes | None = None
        authorization_binding: dict[str, str] | None = None
        prelive_raw: bytes | None = None
        prelive_binding: dict[str, str] | None = None
        if live_authorization_documents is not None:
            if (
                not isinstance(live_authorization_documents, Mapping)
                or set(live_authorization_documents)
                != {"authorization", "consumption"}
                or not isinstance(
                    live_authorization_documents.get("authorization"), bytes
                )
                or not isinstance(live_authorization_documents.get("consumption"), bytes)
            ):
                raise JudgeQualificationArtifactError(
                    "live authorization documents are invalid"
                )
            authorization_raw = live_authorization_documents["authorization"]
            consumption_raw = live_authorization_documents["consumption"]
            try:
                authorization = json.loads(authorization_raw.decode("ascii"))
                consumption = json.loads(consumption_raw.decode("ascii"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise JudgeQualificationArtifactError(
                    "live authorization documents are unreadable"
                ) from error
            if (
                authorization_raw != canonical_json_bytes(authorization) + b"\n"
                or consumption_raw != canonical_json_bytes(consumption) + b"\n"
            ):
                raise JudgeQualificationArtifactError(
                    "live authorization documents are not canonical"
                )
            _validate_seal(authorization, "live authorization")
            _validate_seal(consumption, "live authorization consumption")
            if not isinstance(prelive_evidence_document, bytes):
                raise JudgeQualificationArtifactError(
                    "pre-live evidence document is required for live authorization"
                )
            prelive_raw = prelive_evidence_document
            try:
                prelive_evidence = json.loads(prelive_raw.decode("ascii"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise JudgeQualificationArtifactError(
                    "pre-live evidence document is unreadable"
                ) from error
            if prelive_raw != canonical_json_bytes(prelive_evidence) + b"\n":
                raise JudgeQualificationArtifactError(
                    "pre-live evidence document is not canonical"
                )
            _validate_seal(prelive_evidence, "pre-live evidence document")
            _validate_live_evidence_schemas(
                authorization, consumption, prelive_evidence
            )
            prelive_binding = {
                "manifest_file_sha256": _sha256(prelive_raw),
                "manifest_payload_sha256": prelive_evidence["payload_sha256"],
            }
            if (
                authorization.get("authorized_run_id") != run_id
                or consumption.get("authorized_run_id") != run_id
                or consumption.get("authorization_payload_sha256")
                != authorization["payload_sha256"]
                or consumption.get("authorization_file_sha256")
                != _sha256(authorization_raw)
                or consumption.get("authorization_path")
                != authorization.get("authorization_path")
                or authorization.get("prelive_evidence_manifest_file_sha256")
                != prelive_binding["manifest_file_sha256"]
                or authorization.get("prelive_evidence_manifest_payload_sha256")
                != prelive_binding["manifest_payload_sha256"]
                or consumption.get("prelive_evidence_manifest_file_sha256")
                != prelive_binding["manifest_file_sha256"]
                or consumption.get("prelive_evidence_manifest_payload_sha256")
                != prelive_binding["manifest_payload_sha256"]
            ):
                raise JudgeQualificationArtifactError(
                    "live authorization documents differ from run"
                )
            authorization_binding = {
                "authorization_file_sha256": _sha256(authorization_raw),
                "authorization_payload_sha256": authorization["payload_sha256"],
                "consumption_file_sha256": _sha256(consumption_raw),
                "consumption_payload_sha256": consumption["payload_sha256"],
            }
        elif prelive_evidence_document is not None:
            raise JudgeQualificationArtifactError(
                "pre-live evidence document may not exist without live authorization"
            )

        manifest_body: dict[str, Any] = {
                "schema_version": "membind.judge-qualification-run.v1",
                "protocol_id": PROTOCOL_ID,
                "scientific_surface": JUDGE_QUALIFICATION_ONLY,
                "run_id": run_id,
                "freeze_payload_sha256": frozen["payload_sha256"],
                "freeze_file_sha256": _sha256(freeze_raw),
                "runtime_identity": identity,
                "runtime_identity_payload_sha256": runtime_payload["payload_sha256"],
                "runtime_identity_file_sha256": _sha256(runtime_raw),
                "command_argv": list(command_argv),
            }
        if authorization_binding is not None:
            manifest_body["live_authorization_binding"] = authorization_binding
            manifest_body["prelive_evidence_binding"] = prelive_binding
        manifest = _sealed(manifest_body)

        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            run_dir.mkdir(mode=0o700)
        except FileExistsError as error:
            raise JudgeQualificationArtifactError("qualification run already exists") from error
        try:
            items_dir = run_dir / "items"
            items_dir.mkdir(mode=0o700)
            for index in range(14):
                (items_dir / f"{index:03d}").mkdir(mode=0o700)
            _exclusive_bytes(run_dir / "fixture_freeze.json", freeze_raw)
            _exclusive_bytes(run_dir / "runtime_identity.json", runtime_raw)
            _exclusive_json(run_dir / "manifest.json", manifest)
            _exclusive_bytes(run_dir / "events.jsonl", b"")
            _exclusive_bytes(run_dir / "run.lock", b"")
            if authorization_raw is not None and consumption_raw is not None:
                _exclusive_bytes(run_dir / "live_authorization.json", authorization_raw)
                _exclusive_bytes(
                    run_dir / "live_authorization_consumption.json",
                    consumption_raw,
                )
                if prelive_raw is None:
                    raise JudgeQualificationArtifactError(
                        "pre-live evidence document is missing"
                    )
                _exclusive_bytes(
                    run_dir / "prelive_evidence_manifest.json",
                    prelive_raw,
                )
            store = cls(
                run_dir=run_dir,
                freeze=frozen,
                runtime_identity=identity,
                manifest=manifest,
            )
            store._write_root_checkpoint(
                status="in_progress",
                phase="planned",
                failure_class=None,
                failed_item_id=None,
            )
            _fsync_directory(run_dir)
            return store
        except Exception:
            # Evidence creation is all-or-nothing before any request can occur.
            # Leave no misleading partial run directory on pre-dispatch failure.
            for path in sorted(run_dir.rglob("*"), reverse=True):
                try:
                    path.rmdir() if path.is_dir() else path.unlink()
                except OSError:
                    pass
            try:
                run_dir.rmdir()
            except OSError:
                pass
            raise

    @classmethod
    def resume(
        cls, *, run_dir: Path, freeze: dict[str, Any]
    ) -> "JudgeQualificationArtifactStore":
        frozen = _validate_freeze_structure(freeze)
        audit = _audit_run(Path(run_dir), frozen)
        store = cls(
            run_dir=Path(run_dir),
            freeze=frozen,
            runtime_identity=audit["runtime_identity"],
            manifest=audit["manifest"],
        )
        terminal_failure = next(
            (
                result
                for result in audit["results"]
                if result.status
                in {EvaluationStatus.INVALID_OUTPUT, EvaluationStatus.SERVICE_ERROR}
            ),
            None,
        )
        if terminal_failure is not None:
            failure_class = (
                "invalid_output"
                if terminal_failure.status is EvaluationStatus.INVALID_OUTPUT
                else "service_error"
            )
            store._mark_failure(
                failure_class=failure_class,
                failed_item_id=terminal_failure.item_id,
            )
            raise JudgeQualificationArtifactError(
                f"terminal {failure_class} makes qualification non-mergeable"
            )
        if audit["attempt_status"] == CANONICAL_INCOMPLETE:
            raise JudgeQualificationArtifactError("qualification attempt is non-mergeable")
        if audit["attempt_status"] == "complete":
            raise JudgeQualificationArtifactError("completed qualification cannot resume")
        if audit["ambiguous_dispatch"]:
            store._mark_failure(
                failure_class="ambiguous_dispatch_intent",
                failed_item_id=audit["ambiguous_item_id"],
            )
            raise JudgeQualificationArtifactError("ambiguous dispatch intent cannot resume")
        return store

    def acquire_dispatch_lock(self) -> None:
        if self._lock_descriptor is not None:
            raise JudgeQualificationArtifactError("qualification run lock is already held")
        if self.lock_path.is_symlink() or not self.lock_path.is_file():
            raise JudgeQualificationArtifactError("qualification run lock is invalid")
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as error:
            os.close(descriptor)
            raise JudgeQualificationArtifactError(
                "qualification run is already executing"
            ) from error
        self._lock_descriptor = descriptor

    def release_dispatch_lock(self) -> None:
        descriptor = self._lock_descriptor
        if descriptor is None:
            return
        self._lock_descriptor = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    @property
    def completed_item_ids(self) -> tuple[str, ...]:
        return tuple(record["item_id"] for record in self._terminal_records())

    @property
    def pending_item_ids(self) -> tuple[str, ...]:
        completed = len(self.completed_item_ids)
        return tuple(item["item_id"] for item in self.freeze["items"][completed:])

    def _events(self) -> list[dict[str, Any]]:
        return _read_events(self.events_path)

    def _terminal_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for index in range(14):
            path = self.run_dir / "items" / f"{index:03d}" / "checkpoint.json"
            if not path.exists():
                break
            records.append(_read_canonical_json(path, "item checkpoint"))
        return records

    def _append_event(self, body: Mapping[str, Any]) -> dict[str, Any]:
        events = self._events()
        event = dict(body)
        event.update(
            {
                "schema_version": "membind.judge-qualification-event.v1",
                "run_id": self.manifest["run_id"],
                "event_sequence": len(events),
                "previous_event_sha256": events[-1]["payload_sha256"] if events else None,
            }
        )
        sealed = _sealed(event)
        descriptor: int | None = None
        try:
            descriptor = os.open(
                self.events_path,
                os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW,
            )
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise JudgeQualificationArtifactError(
                    "qualification events are not a regular file"
                )
            with os.fdopen(descriptor, "ab", buffering=0) as handle:
                descriptor = None
                handle.write(canonical_json_bytes(sealed) + b"\n")
                os.fsync(handle.fileno())
        except JudgeQualificationArtifactError:
            raise
        except OSError as error:
            raise JudgeQualificationArtifactError(
                "qualification events are not appendable"
            ) from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        return sealed

    def _write_root_checkpoint(
        self,
        *,
        status: str,
        phase: str,
        failure_class: str | None,
        failed_item_id: str | None,
    ) -> dict[str, Any]:
        events = self._events()
        terminal_count = len(self._terminal_records())
        value = _sealed(
            {
                "schema_version": "membind.judge-qualification-checkpoint.v1",
                "run_id": self.manifest["run_id"],
                "status": status,
                "phase": phase,
                "failure_class": failure_class,
                "failed_item_id": failed_item_id,
                "terminal_item_count": terminal_count,
                "next_item_index": terminal_count,
                "event_count": len(events),
                "last_event_payload_sha256": events[-1]["payload_sha256"] if events else None,
                "freeze_payload_sha256": self.freeze["payload_sha256"],
                "runtime_identity_payload_sha256": self.manifest[
                    "runtime_identity_payload_sha256"
                ],
            }
        )
        _atomic_json(self.checkpoint_path, value)
        return value

    def _mark_failure(self, *, failure_class: str, failed_item_id: str | None) -> dict[str, Any]:
        return self._write_root_checkpoint(
            status=CANONICAL_INCOMPLETE,
            phase="stopped",
            failure_class=failure_class,
            failed_item_id=failed_item_id,
        )

    def _expected_record(self, item: EvaluationItem) -> tuple[int, dict[str, Any]]:
        completed = len(self._terminal_records())
        if completed >= 14:
            raise JudgeQualificationArtifactError("qualification has no pending items")
        expected = self.freeze["items"][completed]
        exact = (
            item.item_id == expected["item_id"]
            and item.benchmark == expected["benchmark"]
            and item.question_id == expected["question_id"]
            and item.question_type == expected["question_type"]
            and item.question == expected["question"]
            and item.reference_answer == expected["reference_answer"]
            and item.hypothesis == expected["hypothesis"]
            and item.abstention is expected["abstention"]
        )
        if not exact:
            raise JudgeQualificationArtifactError("qualification item differs from frozen order")
        return completed, expected

    def write_dispatch_intent(
        self,
        *,
        item: EvaluationItem,
        candidate_answer_id: str,
        human_label: bool,
        prompt_hash: str,
        config_hash: str,
    ) -> dict[str, Any]:
        index, expected = self._expected_record(item)
        events = self._events()
        if events and events[-1]["event_type"] == "dispatch_intent_durable":
            raise JudgeQualificationArtifactError("a dispatch intent is already in flight")
        if (
            candidate_answer_id != expected["candidate_answer_id"]
            or human_label is not expected["human_label"]
            or prompt_hash != expected["official_prompt_sha256"]
            or config_hash != self.runtime_identity["runtime_backend_config_hash"]
        ):
            raise JudgeQualificationArtifactError("dispatch intent differs from frozen identity")
        event = self._append_event(
            {
                "event_type": "dispatch_intent_durable",
                "item_index": index,
                "item_id": item.item_id,
                "route_id": expected["route_id"],
                "candidate_answer_id": candidate_answer_id,
                "human_label": human_label,
                "prompt_hash": prompt_hash,
                "config_hash": config_hash,
            }
        )
        self._write_root_checkpoint(
            status="in_progress",
            phase="dispatch_intent_durable",
            failure_class=None,
            failed_item_id=None,
        )
        return event

    def write_terminal_result(
        self,
        *,
        item: EvaluationItem,
        candidate_answer_id: str,
        human_label: bool,
        result: EvaluationResult,
        dispatch_intent_payload_sha256: str,
    ) -> Path:
        _validate_result_contract(result)
        index, expected = self._expected_record(item)
        events = self._events()
        if not events:
            raise JudgeQualificationArtifactError("terminal result lacks dispatch intent")
        intent = events[-1]
        if (
            intent.get("event_type") != "dispatch_intent_durable"
            or intent.get("item_index") != index
            or intent.get("item_id") != item.item_id
            or intent.get("payload_sha256") != dispatch_intent_payload_sha256
        ):
            raise JudgeQualificationArtifactError("terminal result intent binding is invalid")
        if (
            candidate_answer_id != expected["candidate_answer_id"]
            or human_label is not expected["human_label"]
            or result.item_id != item.item_id
            or result.benchmark != "longmemeval"
            or result.judge_model != "qwen3-32b-fp8"
            or result.prompt_hash != expected["official_prompt_sha256"]
            or result.config_hash != self.runtime_identity["runtime_backend_config_hash"]
        ):
            raise JudgeQualificationArtifactError("terminal result differs from frozen identity")
        event_type = {
            EvaluationStatus.SUCCESS: "terminal_success",
            EvaluationStatus.INVALID_OUTPUT: "terminal_invalid",
            EvaluationStatus.SERVICE_ERROR: "terminal_service_error",
        }[result.status]
        result_value = _result_dict(result)
        terminal_event = self._append_event(
            {
                "event_type": event_type,
                "item_index": index,
                "item_id": item.item_id,
                "route_id": expected["route_id"],
                "candidate_answer_id": candidate_answer_id,
                "human_label": human_label,
                "prompt_hash": result.prompt_hash,
                "config_hash": result.config_hash,
                "dispatch_intent_payload_sha256": dispatch_intent_payload_sha256,
                "result": result_value,
            }
        )
        checkpoint = _sealed(
            {
                "schema_version": "membind.judge-qualification-item.v1",
                "run_id": self.manifest["run_id"],
                "item_index": index,
                "item_id": item.item_id,
                "route_id": expected["route_id"],
                "candidate_answer_id": candidate_answer_id,
                "human_label": human_label,
                "terminal_event_type": event_type,
                "dispatch_intent_payload_sha256": dispatch_intent_payload_sha256,
                "terminal_event_payload_sha256": terminal_event["payload_sha256"],
                "result": result_value,
            }
        )
        target = self.run_dir / "items" / f"{index:03d}" / "checkpoint.json"
        _exclusive_json(target, checkpoint)
        _fsync_directory(target.parent)
        self._write_root_checkpoint(
            status="in_progress",
            phase=event_type,
            failure_class=None,
            failed_item_id=None,
        )
        return target

    def write_item_result(
        self,
        *,
        item: EvaluationItem,
        candidate_answer_id: str,
        human_label: bool,
        result: EvaluationResult,
    ) -> Path:
        """Compatibility helper for offline fixtures; still records both states."""

        _validate_result_contract(result)
        intent = self.write_dispatch_intent(
            item=item,
            candidate_answer_id=candidate_answer_id,
            human_label=human_label,
            prompt_hash=result.prompt_hash,
            config_hash=result.config_hash,
        )
        return self.write_terminal_result(
            item=item,
            candidate_answer_id=candidate_answer_id,
            human_label=human_label,
            result=result,
            dispatch_intent_payload_sha256=intent["payload_sha256"],
        )

    def finalize(self) -> dict[str, Any]:
        records = self._terminal_records()
        if len(records) != 14 or len(self._events()) != 28:
            raise JudgeQualificationArtifactError("qualification cannot finalize before 14 items")
        results = [_result_from_dict(record["result"]) for record in records]
        analysis = analyze_judge_qualification(self.freeze, results)
        mergeable = analysis["qualification_status"] == "PASS"
        summary = _sealed(
            {
                "schema_version": "membind.judge-qualification-summary.v1",
                "protocol_id": PROTOCOL_ID,
                "run_id": self.manifest["run_id"],
                "attempt_status": "complete",
                "mergeable": mergeable,
                "freeze_payload_sha256": self.freeze["payload_sha256"],
                "runtime_identity_payload_sha256": self.manifest[
                    "runtime_identity_payload_sha256"
                ],
                **analysis,
            }
        )
        _exclusive_json(self.summary_path, summary)
        self._write_root_checkpoint(
            status="complete",
            phase="finalized",
            failure_class=None,
            failed_item_id=None,
        )
        return summary


def _read_events(path: Path) -> list[dict[str, Any]]:
    raw = _read_regular_bytes(path, "qualification events")
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise JudgeQualificationArtifactError("qualification events are truncated")
    events: list[dict[str, Any]] = []
    previous: str | None = None
    for sequence, line in enumerate(raw.splitlines()):
        try:
            event = json.loads(line.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise JudgeQualificationArtifactError("qualification event is invalid") from error
        if line != canonical_json_bytes(event):
            raise JudgeQualificationArtifactError("qualification event is not canonical")
        _validate_seal(event, "qualification event")
        if (
            event.get("schema_version") != "membind.judge-qualification-event.v1"
            or event.get("event_sequence") != sequence
            or event.get("previous_event_sha256") != previous
        ):
            raise JudgeQualificationArtifactError("qualification event chain is invalid")
        previous = event["payload_sha256"]
        events.append(event)
    return events


def _audit_run(run_dir: Path, freeze: dict[str, Any]) -> dict[str, Any]:
    root = Path(run_dir)
    if root.is_symlink() or not root.is_dir():
        raise JudgeQualificationArtifactError("qualification run directory is invalid")
    required_directories = [root / "items"] + [
        root / "items" / f"{index:03d}" for index in range(14)
    ]
    if any(path.is_symlink() or not path.is_dir() for path in required_directories):
        raise JudgeQualificationArtifactError("qualification artifact directory is invalid")
    lock_path = root / "run.lock"
    _read_regular_bytes(lock_path, "qualification run lock")

    manifest = _read_canonical_json(root / "manifest.json", "run manifest")
    _validate_seal(manifest, "run manifest")
    if (
        manifest.get("schema_version") != "membind.judge-qualification-run.v1"
        or manifest.get("protocol_id") != PROTOCOL_ID
        or manifest.get("scientific_surface") != JUDGE_QUALIFICATION_ONLY
        or manifest.get("run_id") != root.name
        or _RUN_ID_RE.fullmatch(root.name) is None
        or manifest.get("freeze_payload_sha256") != freeze["payload_sha256"]
    ):
        raise JudgeQualificationArtifactError("run manifest identity is invalid")
    freeze_raw = _read_regular_bytes(root / "fixture_freeze.json", "run fixture freeze")
    if (
        freeze_raw != canonical_json_bytes(freeze) + b"\n"
        or manifest.get("freeze_file_sha256") != _sha256(freeze_raw)
    ):
        raise JudgeQualificationArtifactError("run fixture freeze binding is invalid")
    runtime_payload = _read_canonical_json(root / "runtime_identity.json", "runtime identity")
    _validate_seal(runtime_payload, "runtime identity")
    runtime_raw = _read_regular_bytes(root / "runtime_identity.json", "runtime identity")
    runtime_identity = _validate_runtime_identity(runtime_payload.get("identity"), freeze)
    if (
        runtime_payload.get("schema_version") != "membind.judge-runtime-identity.v1"
        or runtime_payload.get("run_id") != root.name
        or manifest.get("runtime_identity") != runtime_identity
        or manifest.get("runtime_identity_payload_sha256")
        != runtime_payload["payload_sha256"]
        or manifest.get("runtime_identity_file_sha256") != _sha256(runtime_raw)
    ):
        raise JudgeQualificationArtifactError("runtime identity binding is invalid")

    authorization_binding = manifest.get("live_authorization_binding")
    prelive_binding = manifest.get("prelive_evidence_binding")
    authorization_path = root / "live_authorization.json"
    consumption_path = root / "live_authorization_consumption.json"
    prelive_path = root / "prelive_evidence_manifest.json"
    if authorization_binding is None:
        if (
            authorization_path.exists()
            or consumption_path.exists()
            or prelive_path.exists()
            or prelive_binding is not None
        ):
            raise JudgeQualificationArtifactError(
                "unbound live authorization evidence is present"
            )
    else:
        expected_keys = {
            "authorization_file_sha256",
            "authorization_payload_sha256",
            "consumption_file_sha256",
            "consumption_payload_sha256",
        }
        if not isinstance(authorization_binding, dict) or set(
            authorization_binding
        ) != expected_keys or any(
            not _is_sha256(authorization_binding.get(key)) for key in expected_keys
        ):
            raise JudgeQualificationArtifactError(
                "live authorization binding is invalid"
            )
        expected_prelive_keys = {
            "manifest_file_sha256",
            "manifest_payload_sha256",
        }
        if (
            not isinstance(prelive_binding, dict)
            or set(prelive_binding) != expected_prelive_keys
            or any(
                not _is_sha256(prelive_binding.get(key))
                for key in expected_prelive_keys
            )
        ):
            raise JudgeQualificationArtifactError(
                "pre-live evidence binding is invalid"
            )
        authorization_raw = _read_regular_bytes(
            authorization_path, "live authorization"
        )
        consumption_raw = _read_regular_bytes(
            consumption_path, "live authorization consumption"
        )
        prelive_raw = _read_regular_bytes(
            prelive_path, "pre-live evidence manifest"
        )
        try:
            authorization = json.loads(authorization_raw.decode("ascii"))
            consumption = json.loads(consumption_raw.decode("ascii"))
            prelive_evidence = json.loads(prelive_raw.decode("ascii"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise JudgeQualificationArtifactError(
                "live authorization evidence is unreadable"
            ) from error
        if (
            authorization_raw != canonical_json_bytes(authorization) + b"\n"
            or consumption_raw != canonical_json_bytes(consumption) + b"\n"
            or prelive_raw != canonical_json_bytes(prelive_evidence) + b"\n"
        ):
            raise JudgeQualificationArtifactError(
                "live authorization evidence is not canonical"
            )
        _validate_seal(authorization, "live authorization")
        _validate_seal(consumption, "live authorization consumption")
        _validate_seal(prelive_evidence, "pre-live evidence manifest")
        _validate_live_evidence_schemas(
            authorization, consumption, prelive_evidence
        )
        validation_root = Path(__file__).resolve().parents[2]
        _validate_prelive_closure(validation_root, prelive_evidence)
        prelive_inputs = prelive_evidence["bindings"]
        freeze_inputs = freeze.get("bindings")
        runtime_deployment = runtime_identity.get("deployment_evidence_binding")
        semantic_identity_valid = (
            authorization.get("schema_version")
            == "membind.judge-live-authorization.v1"
            and authorization.get("protocol_id") == PROTOCOL_ID
            and authorization.get("scientific_surface") == JUDGE_QUALIFICATION_ONLY
            and authorization.get("authorized_run_id") == root.name
            and authorization.get("live_run_limit") == 1
            and authorization.get("freeze_payload_sha256") == freeze["payload_sha256"]
            and authorization.get("qualification_live_source_sha256")
            == prelive_inputs.get("qualification_live_source", {}).get("sha256")
            and authorization.get("deployment_evidence_payload_sha256")
            == prelive_inputs.get("deployment_evidence", {}).get("payload_sha256")
            and authorization.get("deployment_evidence_payload_sha256")
            == runtime_deployment.get("payload_sha256")
            and consumption.get("schema_version")
            == "membind.judge-live-authorization-consumption.v1"
            and consumption.get("status") == "consumed_before_first_request"
            and consumption.get("protocol_id") == PROTOCOL_ID
            and consumption.get("scientific_surface") == JUDGE_QUALIFICATION_ONLY
            and consumption.get("authorized_run_id") == root.name
            and consumption.get("live_run_limit") == 1
            and prelive_evidence.get("schema_version")
            == "membind.judge-prelive-evidence-manifest.v1"
            and prelive_evidence.get("protocol_id") == PROTOCOL_ID
            and prelive_evidence.get("scientific_surface")
            == JUDGE_QUALIFICATION_ONLY
            and prelive_evidence.get("authorized_run_id") == root.name
            and prelive_evidence.get("live_run_limit") == 1
            and isinstance(freeze_inputs, dict)
            and all(
                prelive_inputs.get(name)
                == {
                    "path": freeze_inputs[name]["path"],
                    "sha256": freeze_inputs[name]["sha256"],
                    **(
                        {"payload_sha256": freeze["offline_manifest_payload_sha256"]}
                        if name == "offline_manifest"
                        else {}
                    ),
                }
                for name in (
                    "qualification_source",
                    "qualification_live_source",
                    "offline_manifest",
                )
            )
            and prelive_inputs.get("qualification_fixture", {}).get("path")
            == freeze_inputs.get("qualification_fixture", {}).get("path")
            and prelive_inputs.get("qualification_fixture", {}).get("sha256")
            == freeze_inputs.get("qualification_fixture", {}).get("sha256")
            and isinstance(runtime_deployment, dict)
            and prelive_inputs.get("deployment_evidence", {}).get("path")
            == runtime_deployment.get("path")
            and prelive_inputs.get("deployment_evidence", {}).get("sha256")
            == runtime_deployment.get("sha256")
            and prelive_inputs.get("deployment_evidence", {}).get("payload_sha256")
            == runtime_deployment.get("payload_sha256")
        )
        if not semantic_identity_valid:
            raise JudgeQualificationArtifactError(
                "live authorization evidence semantic identity is invalid"
            )
        observed_binding = {
            "authorization_file_sha256": _sha256(authorization_raw),
            "authorization_payload_sha256": authorization["payload_sha256"],
            "consumption_file_sha256": _sha256(consumption_raw),
            "consumption_payload_sha256": consumption["payload_sha256"],
        }
        observed_prelive_binding = {
            "manifest_file_sha256": _sha256(prelive_raw),
            "manifest_payload_sha256": prelive_evidence["payload_sha256"],
        }
        if (
            observed_binding != authorization_binding
            or observed_prelive_binding != prelive_binding
            or authorization.get("authorized_run_id") != root.name
            or consumption.get("authorized_run_id") != root.name
            or consumption.get("authorization_file_sha256")
            != observed_binding["authorization_file_sha256"]
            or consumption.get("authorization_payload_sha256")
            != observed_binding["authorization_payload_sha256"]
            or consumption.get("authorization_path")
            != authorization.get("authorization_path")
            or authorization.get("prelive_evidence_manifest_file_sha256")
            != observed_prelive_binding["manifest_file_sha256"]
            or authorization.get("prelive_evidence_manifest_payload_sha256")
            != observed_prelive_binding["manifest_payload_sha256"]
            or consumption.get("prelive_evidence_manifest_file_sha256")
            != observed_prelive_binding["manifest_file_sha256"]
            or consumption.get("prelive_evidence_manifest_payload_sha256")
            != observed_prelive_binding["manifest_payload_sha256"]
        ):
            raise JudgeQualificationArtifactError(
                "live authorization evidence differs from manifest"
            )
    events = _read_events(root / "events.jsonl")
    for event in events:
        if event.get("run_id") != root.name:
            raise JudgeQualificationArtifactError("qualification event run ID is invalid")

    checkpoints: list[dict[str, Any]] = []
    results: list[EvaluationResult] = []
    ambiguous = False
    ambiguous_item_id: str | None = None
    cursor = 0
    for index, frozen_item in enumerate(freeze["items"]):
        if cursor >= len(events):
            break
        intent = events[cursor]
        expected_intent = (
            intent.get("event_type") == "dispatch_intent_durable"
            and intent.get("item_index") == index
            and intent.get("item_id") == frozen_item["item_id"]
            and intent.get("route_id") == frozen_item["route_id"]
            and intent.get("candidate_answer_id") == frozen_item["candidate_answer_id"]
            and intent.get("human_label") is frozen_item["human_label"]
            and intent.get("prompt_hash") == frozen_item["official_prompt_sha256"]
            and intent.get("config_hash")
            == runtime_identity["runtime_backend_config_hash"]
        )
        if not expected_intent:
            raise JudgeQualificationArtifactError("dispatch intent differs from freeze")
        cursor += 1
        if cursor >= len(events):
            ambiguous = True
            ambiguous_item_id = frozen_item["item_id"]
            break
        terminal = events[cursor]
        if terminal.get("event_type") not in {
            "terminal_success",
            "terminal_invalid",
            "terminal_service_error",
        }:
            raise JudgeQualificationArtifactError("terminal event type is invalid")
        result = _result_from_dict(terminal.get("result"))
        _validate_result_contract(result)
        expected_terminal_type = {
            EvaluationStatus.SUCCESS: "terminal_success",
            EvaluationStatus.INVALID_OUTPUT: "terminal_invalid",
            EvaluationStatus.SERVICE_ERROR: "terminal_service_error",
        }[result.status]
        if (
            terminal.get("event_type") != expected_terminal_type
            or terminal.get("item_index") != index
            or terminal.get("item_id") != frozen_item["item_id"]
            or terminal.get("route_id") != frozen_item["route_id"]
            or terminal.get("candidate_answer_id") != frozen_item["candidate_answer_id"]
            or terminal.get("human_label") is not frozen_item["human_label"]
            or terminal.get("dispatch_intent_payload_sha256") != intent["payload_sha256"]
            or terminal.get("previous_event_sha256") != intent["payload_sha256"]
            or terminal.get("prompt_hash") != result.prompt_hash
            or terminal.get("config_hash") != result.config_hash
            or result.item_id != frozen_item["item_id"]
            or result.prompt_hash != frozen_item["official_prompt_sha256"]
            or result.config_hash != runtime_identity["runtime_backend_config_hash"]
        ):
            raise JudgeQualificationArtifactError("terminal event binding is invalid")
        checkpoint_path = root / "items" / f"{index:03d}" / "checkpoint.json"
        checkpoint = _read_canonical_json(checkpoint_path, "item checkpoint")
        _validate_seal(checkpoint, "item checkpoint")
        expected_checkpoint = _sealed(
            {
                "schema_version": "membind.judge-qualification-item.v1",
                "run_id": root.name,
                "item_index": index,
                "item_id": frozen_item["item_id"],
                "route_id": frozen_item["route_id"],
                "candidate_answer_id": frozen_item["candidate_answer_id"],
                "human_label": frozen_item["human_label"],
                "terminal_event_type": expected_terminal_type,
                "dispatch_intent_payload_sha256": intent["payload_sha256"],
                "terminal_event_payload_sha256": terminal["payload_sha256"],
                "result": _result_dict(result),
            }
        )
        if checkpoint != expected_checkpoint:
            raise JudgeQualificationArtifactError("item checkpoint differs from event")
        checkpoints.append(checkpoint)
        results.append(result)
        cursor += 1
    if cursor != len(events):
        raise JudgeQualificationArtifactError("qualification event suffix is invalid")
    for index in range(len(checkpoints), 14):
        checkpoint_path = root / "items" / f"{index:03d}" / "checkpoint.json"
        if checkpoint_path.exists():
            raise JudgeQualificationArtifactError("item checkpoints are not a prefix")

    root_checkpoint = _read_canonical_json(root / "checkpoint.json", "root checkpoint")
    _validate_seal(root_checkpoint, "root checkpoint")
    if (
        root_checkpoint.get("schema_version")
        != "membind.judge-qualification-checkpoint.v1"
        or root_checkpoint.get("run_id") != root.name
        or root_checkpoint.get("terminal_item_count") != len(checkpoints)
        or root_checkpoint.get("next_item_index") != len(checkpoints)
        or root_checkpoint.get("event_count") != len(events)
        or root_checkpoint.get("last_event_payload_sha256")
        != (events[-1]["payload_sha256"] if events else None)
        or root_checkpoint.get("freeze_payload_sha256") != freeze["payload_sha256"]
        or root_checkpoint.get("runtime_identity_payload_sha256")
        != manifest["runtime_identity_payload_sha256"]
    ):
        raise JudgeQualificationArtifactError("root checkpoint binding is invalid")

    status = root_checkpoint.get("status")
    failure_class = root_checkpoint.get("failure_class")
    summary_path = root / "qualification_summary.json"
    if status == "complete":
        if ambiguous or len(checkpoints) != 14 or not summary_path.is_file():
            raise JudgeQualificationArtifactError("completed qualification is incomplete")
        summary = _read_canonical_json(summary_path, "qualification summary")
        _validate_seal(summary, "qualification summary")
        analysis = analyze_judge_qualification(freeze, results)
        mergeable = analysis["qualification_status"] == "PASS"
        expected_summary = _sealed(
            {
                "schema_version": "membind.judge-qualification-summary.v1",
                "protocol_id": PROTOCOL_ID,
                "run_id": root.name,
                "attempt_status": "complete",
                "mergeable": mergeable,
                "freeze_payload_sha256": freeze["payload_sha256"],
                "runtime_identity_payload_sha256": manifest[
                    "runtime_identity_payload_sha256"
                ],
                **analysis,
            }
        )
        if summary != expected_summary:
            raise JudgeQualificationArtifactError("qualification summary is invalid")
        attempt_status = "complete"
    elif status == CANONICAL_INCOMPLETE:
        if summary_path.exists() or not isinstance(failure_class, str) or not failure_class:
            raise JudgeQualificationArtifactError("failed qualification state is invalid")
        attempt_status = CANONICAL_INCOMPLETE
    elif status == "in_progress":
        if summary_path.exists():
            raise JudgeQualificationArtifactError("in-progress qualification has a summary")
        attempt_status = "in_progress"
    else:
        raise JudgeQualificationArtifactError("root checkpoint status is invalid")

    return {
        "attempt_status": attempt_status,
        "failure_class": failure_class,
        "failed_item_id": root_checkpoint.get("failed_item_id"),
        "completed_item_count": len(checkpoints),
        "duplicate_item_count": 0,
        "invalid_output_count": sum(
            result.status is EvaluationStatus.INVALID_OUTPUT for result in results
        ),
        "service_error_count": sum(
            result.status is EvaluationStatus.SERVICE_ERROR for result in results
        ),
        "qualification_status": (
            summary.get("qualification_status") if status == "complete" else None
        ),
        "mergeable": summary.get("mergeable") if status == "complete" else False,
        "ambiguous_dispatch": ambiguous,
        "ambiguous_item_id": ambiguous_item_id,
        "runtime_identity": runtime_identity,
        "live_authorization_binding": deepcopy(authorization_binding),
        "prelive_evidence_binding": deepcopy(prelive_binding),
        "manifest": manifest,
        "results": results,
    }


def verify_judge_qualification_artifacts(
    run_dir: Path, freeze: dict[str, Any]
) -> dict[str, Any]:
    """Return a secret-free verdict; artifact corruption never raises outward."""

    try:
        frozen = _validate_freeze_structure(freeze)
        audit = _audit_run(Path(run_dir), frozen)
        if audit["ambiguous_dispatch"]:
            return {
                **{key: value for key, value in audit.items() if key not in {"results", "manifest", "runtime_identity"}},
                "attempt_status": CANONICAL_INCOMPLETE,
                "failure_class": "ambiguous_dispatch_intent",
            }
        return {
            key: value
            for key, value in audit.items()
            if key not in {"results", "manifest", "runtime_identity", "ambiguous_dispatch", "ambiguous_item_id"}
        }
    except Exception as error:
        return {
            "attempt_status": CANONICAL_INCOMPLETE,
            "failure_class": "artifact_verification_error",
            "error_class": f"{type(error).__module__}.{type(error).__name__}",
            "completed_item_count": 0,
            "duplicate_item_count": 0,
            "invalid_output_count": 0,
            "service_error_count": 0,
        }


def _failure_view(
    store: JudgeQualificationArtifactStore,
    *,
    failure_class: str,
    failed_item_id: str | None,
) -> dict[str, Any]:
    store._mark_failure(failure_class=failure_class, failed_item_id=failed_item_id)
    verification = verify_judge_qualification_artifacts(store.run_dir, store.freeze)
    return {
        "attempt_status": CANONICAL_INCOMPLETE,
        "failure_class": failure_class,
        "failed_item_id": failed_item_id,
        "completed_item_count": verification.get("completed_item_count", 0),
        "invalid_output_count": verification.get("invalid_output_count", 0),
        "service_error_count": verification.get("service_error_count", 0),
    }


async def run_judge_qualification(
    *,
    freeze: dict[str, Any],
    items: Iterable[EvaluationItem],
    evaluator: Any,
    store: JudgeQualificationArtifactStore,
    runtime_identity_reader: Callable[
        [], Mapping[str, Any] | Awaitable[Mapping[str, Any]]
    ]
    | None = None,
) -> dict[str, Any]:
    """Run the pending suffix once, checkpointing intent before every request."""

    frozen = _validate_freeze_structure(freeze)
    if frozen != store.freeze:
        raise JudgeQualificationArtifactError("runner freeze differs from store")
    materialized = list(items)
    if len(materialized) != 14 or any(not isinstance(item, EvaluationItem) for item in materialized):
        raise JudgeQualificationArtifactError("runner requires the exact 14 frozen items")
    for item, expected in zip(materialized, frozen["items"]):
        exact = (
            item.item_id == expected["item_id"]
            and item.benchmark == expected["benchmark"]
            and item.question_id == expected["question_id"]
            and item.question_type == expected["question_type"]
            and item.question == expected["question"]
            and item.reference_answer == expected["reference_answer"]
            and item.hypothesis == expected["hypothesis"]
            and item.abstention is expected["abstention"]
        )
        if not exact:
            raise JudgeQualificationArtifactError("runner item differs from freeze")

    store.acquire_dispatch_lock()
    try:
        completed = len(store.completed_item_ids)
        if tuple(item.item_id for item in materialized[:completed]) != store.completed_item_ids:
            raise JudgeQualificationArtifactError("runner prefix differs from durable prefix")

        for index in range(completed, 14):
            item = materialized[index]
            expected = frozen["items"][index]
            if runtime_identity_reader is not None:
                try:
                    observed = runtime_identity_reader()
                    if inspect.isawaitable(observed):
                        observed = await observed
                    observed_identity = dict(observed)
                except Exception:
                    return _failure_view(
                        store,
                        failure_class="runtime_identity_unavailable",
                        failed_item_id=item.item_id,
                    )
                if observed_identity != store.runtime_identity:
                    return _failure_view(
                        store,
                        failure_class="runtime_identity_drift",
                        failed_item_id=item.item_id,
                    )
            intent = store.write_dispatch_intent(
                item=item,
                candidate_answer_id=expected["candidate_answer_id"],
                human_label=expected["human_label"],
                prompt_hash=expected["official_prompt_sha256"],
                config_hash=store.runtime_identity["runtime_backend_config_hash"],
            )
            try:
                result = await evaluator.evaluate(item)
            except Exception:
                store._mark_failure(
                    failure_class="ambiguous_dispatch_intent",
                    failed_item_id=item.item_id,
                )
                raise JudgeQualificationArtifactError("evaluator failed after durable dispatch") from None
            store.write_terminal_result(
                item=item,
                candidate_answer_id=expected["candidate_answer_id"],
                human_label=expected["human_label"],
                result=result,
                dispatch_intent_payload_sha256=intent["payload_sha256"],
            )
            if result.status is EvaluationStatus.INVALID_OUTPUT:
                return _failure_view(
                    store,
                    failure_class="invalid_output",
                    failed_item_id=item.item_id,
                )
            if result.status is EvaluationStatus.SERVICE_ERROR:
                return _failure_view(
                    store,
                    failure_class="service_error",
                    failed_item_id=item.item_id,
                )
        return store.finalize()
    finally:
        store.release_dispatch_lock()


__all__ = [
    "JUDGE_QUALIFICATION_ONLY",
    "STRICT_PASS_GATE",
    "JudgeQualificationArtifactError",
    "JudgeQualificationArtifactStore",
    "analyze_judge_qualification",
    "build_judge_qualification_freeze",
    "build_strict_judge_qualification_freeze",
    "canonical_json_bytes",
    "run_judge_qualification",
    "validate_judge_qualification_freeze",
    "validate_strict_judge_qualification_freeze",
    "verify_judge_qualification_artifacts",
]
