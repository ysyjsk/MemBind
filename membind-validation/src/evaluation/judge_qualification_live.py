"""Minimal, secret-safe live wrapper for bounded Judge qualification.

The wrapper owns only private connection configuration, the model-identity
probe, and bounded sequential dispatch. Benchmark rubric and transport
semantics remain owned by the existing LongMemEval adapter and Qwen3 backend.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import stat
import sys
import unittest
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx

from evaluation.backends.openai_compatible import Qwen3JudgeBackend
from evaluation.benchmarks.longmemeval import LongMemEvalAdapter
from evaluation.judge_qualification import (
    JUDGE_QUALIFICATION_ONLY,
    PROTOCOL_ID,
    JudgeQualificationArtifactStore,
    canonical_json_bytes,
    run_judge_qualification,
    validate_strict_judge_qualification_freeze,
)
from evaluation.schemas import EvaluationItem, EvaluationResult, EvaluationStatus


_MODEL = "qwen3-32b-fp8"
_COMPLETE = "COMPLETE"
_INCOMPLETE = "INCOMPLETE_NON_MERGEABLE"
_CURRENT_REPOSITORY_REVISION = "aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df"
_HISTORICAL_C2_REVISION = "6e2312b85c2ae9a31f629f24493b79d8b02eab1a"
_CHAT_TEMPLATE_SHA256 = (
    "a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8"
)
_WORKPLAN_SHA256 = "a2a2d59c538131dae8cb412fed8a4e40ce339a6db321af7e554cf1c2f66f93d8"
_WORKPLAN_NAME = "MemBind_JUDGE_QUALIFICATION_WORKPLAN_v1.0.md"
_PRELIVE_SCHEMA = "membind.judge-prelive-evidence-manifest.v1"
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
_DEPLOYMENT_EVIDENCE_PATHS = {
    "serving_envelope": (
        "artifacts/environment/"
        "native_characterization_64k_serving_envelope_20260812.json"
    ),
    "reference_aligned_freeze": (
        "artifacts/native_characterization/freeze_reference_aligned_64k.json"
    ),
    "completed_c2_manifest": (
        "artifacts/native_characterization/runs/"
        "c2-17cdaabd562e9673/manifest.json"
    ),
    "restricted_remote_observation": (
        "artifacts/environment/judge_restricted_remote_observation_20260813.json"
    ),
}
_DEPLOYMENT_RUNTIME = {
    "served_model_name": _MODEL,
    "vllm_version": "0.26.0",
    "repository_revision": _CURRENT_REPOSITORY_REVISION,
    "dtype": "bfloat16",
    "quantization": "fp8",
    "max_model_len": 65536,
    "rope_parameters": {
        "rope_type": "yarn",
        "factor": 2.0,
        "original_max_position_embeddings": 32768,
        "rope_theta": 1000000,
    },
    "chat_template_sha256": _CHAT_TEMPLATE_SHA256,
}
_HISTORICAL_REVISION_MISMATCH = {
    "classification": "historical_c2_revision_differs_from_current_deployment",
    "historical_repository_revision": _HISTORICAL_C2_REVISION,
    "current_repository_revision": _CURRENT_REPOSITORY_REVISION,
}


class JudgeQualificationLiveError(RuntimeError):
    """Stable live-wrapper failure that never includes private response text."""


def _normalize_base_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Judge base URL is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
    ):
        raise ValueError("Judge base URL must be an absolute /v1 URL")
    return urlunsplit((parsed.scheme, parsed.netloc, "/v1/", "", ""))


def _endpoint_identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class JudgeLiveConfig:
    """Private connection values with an allowlisted public projection."""

    base_url: str = field(repr=False)
    api_key: str = field(repr=False)

    @property
    def public_identity(self) -> dict[str, object]:
        return {
            "endpoint_identity_sha256": _endpoint_identity(self.base_url),
            "credential_present": True,
            "credential_persisted": False,
        }


def load_judge_live_config(mapping: Mapping[str, object]) -> JudgeLiveConfig:
    """Load exactly two private values from an explicit caller-owned mapping."""

    if not isinstance(mapping, Mapping) or set(mapping) != {"base_url", "api_key"}:
        raise ValueError("Judge live configuration must contain exact required fields")
    api_key = mapping["api_key"]
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("Judge credential is missing")
    return JudgeLiveConfig(
        base_url=_normalize_base_url(mapping["base_url"]),
        api_key=api_key,
    )


def _stable_error_class(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__name__}"


def _parse_models_identity(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or payload.get("object") != "list":
        raise JudgeQualificationLiveError("Judge model identity response is invalid")
    data = payload.get("data")
    if not isinstance(data, list):
        raise JudgeQualificationLiveError("Judge model identity response is invalid")
    matches = [entry for entry in data if isinstance(entry, dict) and entry.get("id") == _MODEL]
    if len(matches) != 1:
        raise JudgeQualificationLiveError("Expected Judge model identity is not unique")
    model = matches[0]
    root = model.get("root")
    max_model_len = model.get("max_model_len")
    if (
        not isinstance(root, str)
        or not root
        or isinstance(max_model_len, bool)
        or not isinstance(max_model_len, int)
        or max_model_len < 1
    ):
        raise JudgeQualificationLiveError("Judge model identity fields are invalid")
    root_identity = root if root == _MODEL else hashlib.sha256(root.encode("utf-8")).hexdigest()
    return {
        "served_model_name": _MODEL,
        "model_root_identity": root_identity,
        "max_model_len": max_model_len,
    }


async def capture_judge_runtime_identity(
    config: JudgeLiveConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    """Capture the allowlisted model identity from one injected HTTP client."""

    if not isinstance(config, JudgeLiveConfig):
        raise TypeError("config must be JudgeLiveConfig")
    timeout = httpx.Timeout(30.0, connect=5.0)
    try:
        async with httpx.AsyncClient(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.api_key}"},
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.get("models")
            response.raise_for_status()
            model_identity = _parse_models_identity(response.json())
    except JudgeQualificationLiveError:
        raise
    except Exception as error:
        raise JudgeQualificationLiveError(
            f"Judge model identity request failed: {_stable_error_class(error)}"
        ) from None
    return config.public_identity | model_identity


@dataclass(frozen=True)
class BoundedJudgeQualificationRun:
    """Secret-free terminal view of one bounded, non-persistent wrapper run."""

    status: str
    planned_count: int
    attempted_count: int
    completed_count: int
    # Successful model text can contain echoed input. Keep it inspectable by
    # explicit field access without exposing it through routine object reprs.
    results: tuple[EvaluationResult, ...] = field(repr=False)
    runtime_identity: Mapping[str, object]
    failed_item_id: str | None = None
    error_class: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {_COMPLETE, _INCOMPLETE}:
            raise ValueError("bounded Judge run status is invalid")
        if not isinstance(self.results, tuple):
            raise TypeError("bounded Judge results must be immutable")
        if not isinstance(self.runtime_identity, Mapping):
            raise TypeError("runtime identity must be a mapping")


def _bounded_items(
    items: Iterable[EvaluationItem], max_items: int
) -> tuple[EvaluationItem, ...]:
    if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 1:
        raise ValueError("max_items must be a positive integer")
    materialized = tuple(items)
    if not materialized or len(materialized) > max_items:
        raise ValueError("Judge qualification item count exceeds its bound")
    if any(not isinstance(item, EvaluationItem) for item in materialized):
        raise TypeError("Judge qualification items must be EvaluationItem values")
    if any(item.benchmark != "longmemeval" for item in materialized):
        raise ValueError("Judge qualification supports LongMemEval items only")
    identifiers = [item.item_id for item in materialized]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Judge qualification item identifiers must be unique")
    return materialized


async def run_bounded_judge_qualification(
    *,
    config_mapping: Mapping[str, object],
    items: Iterable[EvaluationItem],
    max_items: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> BoundedJudgeQualificationRun:
    """Run one bounded sequence and stop at the first service failure."""

    bounded = _bounded_items(items, max_items)
    config = load_judge_live_config(config_mapping)
    runtime_identity = await capture_judge_runtime_identity(
        config,
        transport=transport,
    )
    backend = Qwen3JudgeBackend(
        base_url=config.base_url,
        api_key=config.api_key,
        thinking_control="client_request",
        max_attempts=1,
        transport=transport,
    )
    evaluator = LongMemEvalAdapter(backend)
    results: list[EvaluationResult] = []
    failed_item_id: str | None = None
    error_class: str | None = None
    try:
        for item in bounded:
            result = await evaluator.evaluate(item)
            results.append(result)
            if result.status is EvaluationStatus.SERVICE_ERROR:
                failed_item_id = item.item_id
                error_class = result.error_class
                break
    finally:
        await backend.aclose()

    terminal = tuple(results)
    completed_count = sum(
        result.status is not EvaluationStatus.SERVICE_ERROR for result in terminal
    )
    status = _INCOMPLETE if failed_item_id is not None else _COMPLETE
    return BoundedJudgeQualificationRun(
        status=status,
        planned_count=len(bounded),
        attempted_count=len(terminal),
        completed_count=completed_count,
        results=terminal,
        runtime_identity=MappingProxyType(dict(runtime_identity)),
        failed_item_id=failed_item_id,
        error_class=error_class,
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_regular_bytes(path: Path, label: str) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise JudgeQualificationLiveError(f"{label} is not a regular file")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            return handle.read()
    except JudgeQualificationLiveError:
        raise
    except OSError as error:
        raise JudgeQualificationLiveError(f"{label} is unreadable") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _rooted_regular_file(root: Path, path: Path, label: str) -> Path:
    """Resolve one evidence path while rejecting every symlink component."""

    root = Path(root).resolve(strict=True)
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            relative = candidate.relative_to(root)
        except ValueError as error:
            raise JudgeQualificationLiveError(f"{label} escapes validation root") from error
    else:
        relative = candidate
    if ".." in relative.parts:
        raise JudgeQualificationLiveError(f"{label} escapes validation root")
    cursor = root
    for component in relative.parts:
        cursor = cursor / component
        if cursor.is_symlink():
            raise JudgeQualificationLiveError(f"{label} may not use a symlink")
    try:
        resolved = cursor.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise JudgeQualificationLiveError(f"{label} is invalid") from error
    _read_regular_bytes(resolved, label)
    return resolved


def _sealed_payload(value: Mapping[str, object], label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise JudgeQualificationLiveError(f"{label} is invalid")
    candidate = dict(value)
    observed = candidate.pop("payload_sha256", None)
    expected = hashlib.sha256(canonical_json_bytes(candidate)).hexdigest()
    if observed != expected:
        raise JudgeQualificationLiveError(f"{label} payload seal mismatch")
    return dict(value)


def _read_canonical_sealed(path: Path, label: str) -> tuple[dict[str, object], bytes]:
    raw = _read_regular_bytes(path, label)
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise JudgeQualificationLiveError(f"{label} is unreadable") from error
    if not isinstance(value, dict) or raw != canonical_json_bytes(value) + b"\n":
        raise JudgeQualificationLiveError(f"{label} is not canonical")
    return _sealed_payload(value, label), raw


def _file_binding(root: Path, path: Path, label: str) -> dict[str, str]:
    resolved = _rooted_regular_file(root, path, label)
    return {
        "path": resolved.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(_read_regular_bytes(resolved, label)).hexdigest(),
    }


def _json_binding(root: Path, path: Path, label: str) -> dict[str, str]:
    binding = _file_binding(root, path, label)
    resolved = root / binding["path"]
    raw = _read_regular_bytes(resolved, label)
    try:
        value = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise JudgeQualificationLiveError(f"pre-live {label} is unreadable") from error
    if raw != canonical_json_bytes(value) + b"\n":
        raise JudgeQualificationLiveError(f"pre-live {label} is not canonical")
    if isinstance(value, Mapping) and isinstance(value.get("payload_sha256"), str):
        candidate = dict(value)
        observed = candidate.pop("payload_sha256")
        if observed != hashlib.sha256(canonical_json_bytes(candidate)).hexdigest():
            raise JudgeQualificationLiveError(f"pre-live {label} payload seal mismatch")
        payload_sha256 = observed
    else:
        payload_sha256 = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    binding["payload_sha256"] = payload_sha256
    return binding


def _expected_judge_tests(root: Path) -> list[Path]:
    return sorted(
        path.relative_to(root)
        for path in root.joinpath("tests").glob("test_judge*.py")
        if path.is_file() and not path.is_symlink()
    )


def _expected_test_evidence_sources(root: Path, suite_id: str) -> list[Path]:
    if suite_id == "focused":
        return _expected_judge_tests(root)
    if suite_id == "impact":
        return [Path(path) for path in _IMPACT_TEST_PATHS]
    if suite_id == "q3":
        return [Path(path) for path in _Q3_SOURCE_PATHS]
    raise JudgeQualificationLiveError("Judge test evidence suite is invalid")


def _test_module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root) if path.is_absolute() else path
    return ".".join(relative.with_suffix("").parts)


def _loaded_test_count(root: Path, suite_id: str, source_paths: list[Path]) -> int:
    test_paths = source_paths if suite_id != "q3" else source_paths[1:]
    original_path = list(sys.path)
    try:
        root_text = str(root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromNames(
            [_test_module_name(root, path) for path in test_paths]
        )
    finally:
        sys.path[:] = original_path
    if loader.errors:
        raise JudgeQualificationLiveError(
            "Judge test evidence inventory does not load"
        )
    count = suite.countTestCases()
    if count < 1:
        raise JudgeQualificationLiveError("Judge test evidence inventory is empty")
    return count


def _validate_raw_unittest_log(raw: bytes, expected_count: int) -> None:
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise JudgeQualificationLiveError(
            "Judge test evidence unittest log is not ASCII"
        ) from error
    observed = re.findall(r"(?m)^Ran ([0-9]+) tests? in [^\r\n]+\r?$", text)
    terminal_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if (
        len(observed) != 1
        or int(observed[0]) != expected_count
        or not terminal_lines
        or terminal_lines[-1] != "OK"
        or re.search(r"(?m)^FAILED(?: |$)", text) is not None
    ):
        raise JudgeQualificationLiveError(
            "Judge test evidence unittest log is not GREEN for the exact test count"
        )


def _validate_q3_summary(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    expected = {
        "schema_version": "membind.judge-q3-dry-run-summary.v1",
        "status": "GREEN",
        "scenarios": list(_Q3_SCENARIOS),
        "real_external_requests": 0,
        "live_authorization_created": False,
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != set(expected)
        or value.get("schema_version") != expected["schema_version"]
        or value.get("status") != "GREEN"
        or type(value.get("scenarios")) is not list
        or value.get("scenarios") != list(_Q3_SCENARIOS)
        or type(value.get("real_external_requests")) is not int
        or value.get("real_external_requests") != 0
        or type(value.get("live_authorization_created")) is not bool
        or value.get("live_authorization_created") is not False
    ):
        raise JudgeQualificationLiveError("Judge Q3 test evidence is invalid")
    return expected


def build_judge_test_evidence_report(
    *,
    validation_root: Path,
    suite_id: str,
    status: str,
    exit_code: int,
    test_count: int,
    source_paths: Iterable[Path],
    raw_log_path: Path,
    q3_summary: Mapping[str, object] | None,
) -> dict[str, object]:
    """Build one sealed, machine-verifiable offline test-suite report."""

    root = Path(validation_root).resolve(strict=True)
    expected_sources = _expected_test_evidence_sources(root, suite_id)
    supplied_sources = [Path(path) for path in source_paths]
    if supplied_sources != expected_sources:
        raise JudgeQualificationLiveError(
            "Judge test evidence source inventory is invalid"
        )
    source_inventory = [
        _file_binding(root, path, "Judge test evidence source")
        for path in supplied_sources
    ]
    expected_count = _loaded_test_count(root, suite_id, supplied_sources)
    if (
        status != "GREEN"
        or isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
        or exit_code != 0
        or isinstance(test_count, bool)
        or not isinstance(test_count, int)
        or test_count != expected_count
    ):
        raise JudgeQualificationLiveError(
            "Judge test evidence is not GREEN with the exact test count and exit code"
        )
    normalized_q3 = _validate_q3_summary(q3_summary)
    if (suite_id == "q3") != (normalized_q3 is not None):
        raise JudgeQualificationLiveError("Judge Q3 test evidence is invalid")
    raw_log = _file_binding(root, raw_log_path, "Judge test evidence unittest log")
    _validate_raw_unittest_log(
        _read_regular_bytes(root / raw_log["path"], "Judge test evidence unittest log"),
        expected_count,
    )
    report: dict[str, object] = {
        "schema_version": _TEST_EVIDENCE_SCHEMA,
        "protocol_id": PROTOCOL_ID,
        "scientific_surface": JUDGE_QUALIFICATION_ONLY,
        "suite_id": suite_id,
        "status": status,
        "exit_code": exit_code,
        "test_count": test_count,
        "source_inventory": source_inventory,
        "raw_log": raw_log,
        "q3_summary": normalized_q3,
    }
    report["payload_sha256"] = hashlib.sha256(
        canonical_json_bytes(report)
    ).hexdigest()
    return report


def validate_judge_test_evidence_report(
    value: Mapping[str, object], validation_root: Path
) -> Mapping[str, object]:
    """Deeply rebuild a test report from its bound sources and raw log."""

    root = Path(validation_root).resolve(strict=True)
    report = _sealed_payload(value, "Judge test evidence report")
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
    suite_id = report.get("suite_id")
    if (
        set(report) != expected_keys
        or report.get("schema_version") != _TEST_EVIDENCE_SCHEMA
        or report.get("protocol_id") != PROTOCOL_ID
        or report.get("scientific_surface") != JUDGE_QUALIFICATION_ONLY
        or not isinstance(suite_id, str)
    ):
        raise JudgeQualificationLiveError("Judge test evidence report is invalid")
    expected_sources = _expected_test_evidence_sources(root, suite_id)
    observed_inventory = report.get("source_inventory")
    rebuilt_inventory = [
        _file_binding(root, path, "Judge test evidence source")
        for path in expected_sources
    ]
    if observed_inventory != rebuilt_inventory:
        raise JudgeQualificationLiveError(
            "Judge test evidence source inventory drifted"
        )
    expected_count = _loaded_test_count(root, suite_id, expected_sources)
    if (
        report.get("status") != "GREEN"
        or type(report.get("exit_code")) is not int
        or report.get("exit_code") != 0
        or type(report.get("test_count")) is not int
        or report.get("test_count") != expected_count
    ):
        raise JudgeQualificationLiveError(
            "Judge test evidence is not GREEN with the exact test count and exit code"
        )
    normalized_q3 = _validate_q3_summary(report.get("q3_summary"))
    if (suite_id == "q3") != (normalized_q3 is not None):
        raise JudgeQualificationLiveError("Judge Q3 test evidence is invalid")
    raw_binding = report.get("raw_log")
    if not isinstance(raw_binding, Mapping) or set(raw_binding) != {"path", "sha256"}:
        raise JudgeQualificationLiveError("Judge test evidence log binding is invalid")
    rebuilt_log = _file_binding(
        root, Path(str(raw_binding.get("path"))), "Judge test evidence unittest log"
    )
    if dict(raw_binding) != rebuilt_log:
        raise JudgeQualificationLiveError("Judge test evidence log binding drifted")
    _validate_raw_unittest_log(
        _read_regular_bytes(root / rebuilt_log["path"], "Judge test evidence unittest log"),
        expected_count,
    )
    return value


def build_judge_prelive_evidence_manifest(
    *,
    validation_root: Path,
    authorized_run_id: str,
    workplan_path: Path,
    qualification_source_path: Path,
    qualification_live_source_path: Path,
    qualification_q3_source_path: Path,
    judge_test_paths: Iterable[Path],
    qualification_fixture_path: Path,
    offline_manifest_path: Path,
    deployment_evidence_path: Path,
    final_focused_report_path: Path,
    final_impact_report_path: Path,
    final_q3_dry_run_report_path: Path,
    strict_freeze_path: Path,
    live_run_limit: int,
) -> dict[str, object]:
    """Build the exact offline evidence closure required by one live grant."""

    root = Path(validation_root).resolve(strict=True)
    workplan = Path(workplan_path).resolve(strict=True)
    if (
        workplan != root.parent / _WORKPLAN_NAME
        or workplan.is_symlink()
        or hashlib.sha256(_read_regular_bytes(workplan, "Judge workplan")).hexdigest()
        != _WORKPLAN_SHA256
    ):
        raise JudgeQualificationLiveError("pre-live workplan binding is invalid")
    expected_tests = _expected_judge_tests(root)
    supplied_tests = [Path(path) for path in judge_test_paths]
    if supplied_tests != expected_tests:
        raise JudgeQualificationLiveError("pre-live Judge test set is incomplete")
    test_bindings = [
        _file_binding(root, path, "Judge test") for path in supplied_tests
    ]
    manifest = {
        "schema_version": _PRELIVE_SCHEMA,
        "protocol_id": PROTOCOL_ID,
        "scientific_surface": JUDGE_QUALIFICATION_ONLY,
        "authorized_run_id": authorized_run_id,
        "live_run_limit": live_run_limit,
        "workplan_sha256": _WORKPLAN_SHA256,
        "judge_tests_aggregate_sha256": hashlib.sha256(
            canonical_json_bytes(test_bindings)
        ).hexdigest(),
        "bindings": {
            "qualification_source": _file_binding(
                root, qualification_source_path, "qualification source"
            ),
            "qualification_live_source": _file_binding(
                root, qualification_live_source_path, "qualification live source"
            ),
            "qualification_q3_source": _file_binding(
                root, qualification_q3_source_path, "qualification Q3 source"
            ),
            "judge_tests": test_bindings,
            "qualification_fixture": _json_binding(
                root, qualification_fixture_path, "qualification fixture"
            ),
            "offline_manifest": _json_binding(
                root, offline_manifest_path, "offline manifest"
            ),
            "deployment_evidence": _json_binding(
                root, deployment_evidence_path, "deployment evidence"
            ),
            "final_focused_report": _json_binding(
                root, final_focused_report_path, "final focused test evidence report"
            ),
            "final_impact_report": _json_binding(
                root, final_impact_report_path, "final impact test evidence report"
            ),
            "final_q3_dry_run_report": _json_binding(
                root, final_q3_dry_run_report_path, "final Q3 dry-run test evidence report"
            ),
            "strict_freeze": _json_binding(
                root, strict_freeze_path, "strict qualification freeze"
            ),
        },
    }
    manifest["payload_sha256"] = hashlib.sha256(
        canonical_json_bytes(manifest)
    ).hexdigest()
    return manifest


def validate_judge_prelive_evidence_manifest(
    value: Mapping[str, object], validation_root: Path
) -> Mapping[str, object]:
    """Deeply re-read every pre-live binding so hash-only assertions fail closed."""

    root = Path(validation_root).resolve(strict=True)
    try:
        manifest = _sealed_payload(value, "pre-live evidence manifest")
    except (TypeError, ValueError) as error:
        raise JudgeQualificationLiveError("pre-live evidence manifest is invalid") from error
    expected_keys = {
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
    bindings = manifest.get("bindings")
    expected_binding_keys = {
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
    if (
        set(manifest) != expected_keys
        or manifest.get("schema_version") != _PRELIVE_SCHEMA
        or manifest.get("protocol_id") != PROTOCOL_ID
        or manifest.get("scientific_surface") != JUDGE_QUALIFICATION_ONLY
        or not isinstance(manifest.get("authorized_run_id"), str)
        or manifest.get("live_run_limit") != 1
        or manifest.get("workplan_sha256") != _WORKPLAN_SHA256
        or not isinstance(bindings, Mapping)
        or set(bindings) != expected_binding_keys
    ):
        raise JudgeQualificationLiveError("pre-live evidence manifest is invalid")
    workplan = root.parent / _WORKPLAN_NAME
    if (
        workplan.is_symlink()
        or hashlib.sha256(_read_regular_bytes(workplan, "Judge workplan")).hexdigest()
        != _WORKPLAN_SHA256
    ):
        raise JudgeQualificationLiveError("pre-live workplan binding drifted")

    expected_tests = _expected_judge_tests(root)
    observed_tests = bindings.get("judge_tests")
    if not isinstance(observed_tests, list) or len(observed_tests) != len(expected_tests):
        raise JudgeQualificationLiveError("pre-live Judge test set is incomplete")
    rebuilt_tests = [
        _file_binding(root, path, "Judge test") for path in expected_tests
    ]
    if observed_tests != rebuilt_tests or manifest.get(
        "judge_tests_aggregate_sha256"
    ) != hashlib.sha256(canonical_json_bytes(rebuilt_tests)).hexdigest():
        raise JudgeQualificationLiveError("pre-live Judge test binding drifted")

    file_names = {
        "qualification_source": "qualification source",
        "qualification_live_source": "qualification live source",
        "qualification_q3_source": "qualification Q3 source",
    }
    json_names = {
        "qualification_fixture": "qualification fixture",
        "offline_manifest": "offline manifest",
        "deployment_evidence": "deployment evidence",
        "final_focused_report": "final focused test evidence report",
        "final_impact_report": "final impact test evidence report",
        "final_q3_dry_run_report": "final Q3 dry-run test evidence report",
        "strict_freeze": "strict qualification freeze",
    }
    rebuilt: dict[str, dict[str, str]] = {}
    for name, label in file_names.items():
        binding = bindings.get(name)
        if not isinstance(binding, Mapping) or set(binding) != {"path", "sha256"}:
            raise JudgeQualificationLiveError(f"pre-live {label} binding is invalid")
        rebuilt[name] = _file_binding(root, Path(str(binding["path"])), label)
    for name, label in json_names.items():
        binding = bindings.get(name)
        if not isinstance(binding, Mapping) or set(binding) != {
            "path",
            "sha256",
            "payload_sha256",
        }:
            raise JudgeQualificationLiveError(f"pre-live {label} binding is invalid")
        rebuilt[name] = _json_binding(root, Path(str(binding["path"])), label)
    if any(bindings[name] != observed for name, observed in rebuilt.items()):
        raise JudgeQualificationLiveError("pre-live evidence binding drifted")

    fixed_paths = {
        "qualification_source": "src/evaluation/judge_qualification.py",
        "qualification_live_source": "src/evaluation/judge_qualification_live.py",
        "qualification_q3_source": "src/evaluation/judge_qualification_q3.py",
        "qualification_fixture": "fixtures/judge_qualification_14_v1.json",
        "offline_manifest": "artifacts/protocol/judge_upstream_manifest_20260812.json",
        "deployment_evidence": (
            "artifacts/environment/judge_deployment_evidence_20260813.json"
        ),
    }
    if any(rebuilt[name]["path"] != path for name, path in fixed_paths.items()):
        raise JudgeQualificationLiveError("pre-live frozen evidence path drifted")

    freeze_binding = rebuilt["strict_freeze"]
    freeze_path = root / freeze_binding["path"]
    freeze, _freeze_raw = _read_canonical_sealed(
        freeze_path, "strict qualification freeze"
    )
    try:
        validate_strict_judge_qualification_freeze(freeze, root)
    except Exception as error:
        raise JudgeQualificationLiveError("pre-live strict freeze is invalid") from error
    freeze_inputs = freeze.get("bindings")
    if not isinstance(freeze_inputs, Mapping) or any(
        freeze_inputs.get(freeze_name)
        != {
            "path": rebuilt[manifest_name]["path"],
            "sha256": rebuilt[manifest_name]["sha256"],
        }
        for freeze_name, manifest_name in {
            "qualification_source": "qualification_source",
            "qualification_live_source": "qualification_live_source",
            "qualification_fixture": "qualification_fixture",
            "offline_manifest": "offline_manifest",
        }.items()
    ):
        raise JudgeQualificationLiveError("pre-live strict freeze input binding drifted")
    if freeze.get("offline_manifest_payload_sha256") != rebuilt[
        "offline_manifest"
    ]["payload_sha256"]:
        raise JudgeQualificationLiveError("pre-live offline manifest payload drifted")
    deployment_binding = rebuilt["deployment_evidence"]
    load_verified_judge_deployment_evidence(
        root,
        Path(deployment_binding["path"]),
        deployment_binding["sha256"],
    )
    for binding_name, suite_id in {
        "final_focused_report": "focused",
        "final_impact_report": "impact",
        "final_q3_dry_run_report": "q3",
    }.items():
        report, _raw = _read_canonical_sealed(
            root / rebuilt[binding_name]["path"],
            f"{suite_id} test evidence report",
        )
        if report.get("suite_id") != suite_id:
            raise JudgeQualificationLiveError(
                f"pre-live {suite_id} test evidence report is invalid"
            )
        try:
            validate_judge_test_evidence_report(report, root)
        except JudgeQualificationLiveError as error:
            raise JudgeQualificationLiveError(
                f"pre-live {suite_id} test evidence is invalid: {error}"
            ) from error
    return value


def _historical_construction_identity(value: object) -> bool:
    return isinstance(value, Mapping) and dict(value) == {
        "served_model_id": _MODEL,
        "vllm_version": "0.26.0",
        "model_revision": _HISTORICAL_C2_REVISION,
        "dtype": "bfloat16",
        "quantization": "fp8",
        "max_model_len": 65536,
        "enable_thinking": False,
        "rope_type": "yarn",
        "yarn_factor": 2.0,
        "original_max_position_embeddings": 32768,
        "rope_theta": 1000000,
    }


def load_verified_judge_deployment_evidence(
    validation_root: Path,
    evidence_path: Path,
    evidence_sha256: str,
) -> Mapping[str, object]:
    """Derive the Judge identity from four mutually checked evidence files."""

    root = Path(validation_root).resolve(strict=True)
    outer_path = _rooted_regular_file(root, Path(evidence_path), "deployment evidence")
    evidence, outer_raw = _read_canonical_sealed(outer_path, "deployment evidence")
    if hashlib.sha256(outer_raw).hexdigest() != evidence_sha256:
        raise JudgeQualificationLiveError("deployment evidence file hash mismatch")
    if set(evidence) != {
        "schema_version",
        "scientific_surface",
        "runtime",
        "historical_c2_revision_mismatch",
        "evidence_bindings",
        "payload_sha256",
    } or (
        evidence.get("schema_version") != "membind.judge-deployment-evidence.v1"
        or evidence.get("scientific_surface") != JUDGE_QUALIFICATION_ONLY
        or evidence.get("runtime") != _DEPLOYMENT_RUNTIME
        or evidence.get("historical_c2_revision_mismatch")
        != _HISTORICAL_REVISION_MISMATCH
    ):
        raise JudgeQualificationLiveError("deployment evidence identity is invalid")

    raw_bindings = evidence.get("evidence_bindings")
    if not isinstance(raw_bindings, Mapping) or set(raw_bindings) != set(
        _DEPLOYMENT_EVIDENCE_PATHS
    ):
        raise JudgeQualificationLiveError("deployment evidence bindings are invalid")
    sources: dict[str, dict[str, object]] = {}
    normalized_bindings: dict[str, dict[str, str]] = {}
    for name, expected_relative in _DEPLOYMENT_EVIDENCE_PATHS.items():
        binding = raw_bindings.get(name)
        if not isinstance(binding, Mapping) or set(binding) != {"path", "sha256"}:
            raise JudgeQualificationLiveError("deployment evidence binding is invalid")
        if binding.get("path") != expected_relative:
            raise JudgeQualificationLiveError("deployment evidence path is not frozen")
        source_path = _rooted_regular_file(root, Path(expected_relative), name)
        source, source_raw = _read_canonical_sealed(source_path, name)
        observed_sha = hashlib.sha256(source_raw).hexdigest()
        if binding.get("sha256") != observed_sha:
            raise JudgeQualificationLiveError("deployment evidence source hash mismatch")
        sources[name] = source
        normalized_bindings[name] = {
            "path": expected_relative,
            "sha256": observed_sha,
        }

    envelope_runtime = sources["serving_envelope"].get("runtime")
    envelope_ok = (
        sources["serving_envelope"].get("schema_version")
        == "membind.native-characterization-64k-envelope.v1"
        and sources["serving_envelope"].get("qualification_status")
        == "64K_ENVELOPE_PASS"
        and isinstance(envelope_runtime, Mapping)
        and envelope_runtime.get("served_model_id") == _MODEL
        and envelope_runtime.get("vllm_version") == "0.26.0"
        and envelope_runtime.get("max_model_len") == 65536
        and envelope_runtime.get("rope_type") == "yarn"
        and envelope_runtime.get("yarn_factor") == 2.0
        and envelope_runtime.get("original_max_position_embeddings") == 32768
        and envelope_runtime.get("rope_theta") == 1000000
    )
    freeze_construction = (
        sources["reference_aligned_freeze"]
        .get("runtime_identities", {})
        .get("construction")
        if isinstance(sources["reference_aligned_freeze"].get("runtime_identities"), Mapping)
        else None
    )
    c2 = sources["completed_c2_manifest"]
    c2_provenance = c2.get("provenance")
    c2_sanitized = (
        c2_provenance.get("sanitized_runtime_identity")
        if isinstance(c2_provenance, Mapping)
        else None
    )
    c2_construction = (
        c2_sanitized.get("construction") if isinstance(c2_sanitized, Mapping) else None
    )
    telemetry = c2.get("telemetry_completeness")
    historical_ok = (
        sources["reference_aligned_freeze"].get("schema_version")
        == "membind.native-characterization-freeze.v1"
        and _historical_construction_identity(freeze_construction)
        and c2.get("schema_version")
        == "membind.native-characterization-c2-result.v1"
        and c2.get("status") == "completed"
        and c2.get("run_id") == "c2-17cdaabd562e9673"
        and isinstance(telemetry, Mapping)
        and telemetry.get("status") == "complete"
        and telemetry.get("missing_required_fields") == []
        and _historical_construction_identity(c2_construction)
    )
    remote = sources["restricted_remote_observation"]
    remote_ok = (
        remote.get("schema_version")
        == "membind.judge-restricted-remote-observation.v1"
        and remote.get("access_mode") == "ssh_forced_command_read_only"
        and remote.get("observation_scope") == "/home/lhx/liuyi/**"
        and remote.get("runtime") == _DEPLOYMENT_RUNTIME
        and remote.get("model_fingerprint_status")
        == "not_observed_no_actual_scan"
    )
    if not envelope_ok or not historical_ok or not remote_ok:
        raise JudgeQualificationLiveError(
            "deployment evidence sources do not support the frozen identity"
        )

    result = deepcopy(_DEPLOYMENT_RUNTIME)
    result["historical_c2_revision_mismatch"] = deepcopy(
        _HISTORICAL_REVISION_MISMATCH
    )
    result["evidence_bindings"] = normalized_bindings
    result["evidence_payload_sha256"] = evidence["payload_sha256"]
    return MappingProxyType(result)


def _consume_live_authorization(
    *,
    validation_root: Path,
    run_id: str,
    freeze: Mapping[str, object],
    deployment_evidence: Mapping[str, object],
    prelive_evidence_binding: Mapping[str, str],
    prelive_evidence: Mapping[str, object],
    authorization_binding: Mapping[str, object],
) -> dict[str, bytes]:
    """Validate and irreversibly consume the singleton live grant pre-network."""

    if not isinstance(authorization_binding, Mapping) or set(
        authorization_binding
    ) != {"path", "sha256"}:
        raise JudgeQualificationLiveError("live authorization binding is invalid")
    relative = Path(str(authorization_binding["path"]))
    expected_file_hash = authorization_binding["sha256"]
    if relative.is_absolute() or ".." in relative.parts:
        raise JudgeQualificationLiveError("live authorization path is invalid")
    unresolved = validation_root / relative
    if unresolved.is_symlink():
        raise JudgeQualificationLiveError("live authorization may not be a symlink")
    try:
        resolved = unresolved.resolve(strict=True)
        resolved.relative_to(validation_root)
    except (OSError, ValueError) as error:
        raise JudgeQualificationLiveError("live authorization path is invalid") from error
    raw = _read_regular_bytes(resolved, "live authorization")
    if expected_file_hash != hashlib.sha256(raw).hexdigest():
        raise JudgeQualificationLiveError("live authorization file hash mismatch")
    try:
        authorization = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise JudgeQualificationLiveError("live authorization is unreadable") from error
    if raw != canonical_json_bytes(authorization) + b"\n":
        raise JudgeQualificationLiveError("live authorization is not canonical")
    authorization = _sealed_payload(authorization, "live authorization")
    if set(authorization) != _LIVE_AUTHORIZATION_KEYS:
        raise JudgeQualificationLiveError("live authorization schema is invalid")

    forbidden_keys = ("api_key", "authorization", "base_url", "password", "secret")

    def reject_private_material(candidate: object) -> None:
        if isinstance(candidate, Mapping):
            for key, nested in candidate.items():
                rendered_key = str(key).casefold().replace("-", "_")
                if any(token in rendered_key for token in forbidden_keys):
                    raise JudgeQualificationLiveError(
                        "live authorization contains private material"
                    )
                reject_private_material(nested)
        elif isinstance(candidate, (list, tuple)):
            for nested in candidate:
                reject_private_material(nested)
        elif isinstance(candidate, str) and "bearer " in candidate.casefold():
            raise JudgeQualificationLiveError(
                "live authorization contains private material"
            )

    authorization_id = authorization.get("authorization_id")
    if not isinstance(authorization_id, str) or not authorization_id:
        raise JudgeQualificationLiveError("live authorization identity is invalid")
    # Top-level keys are the exact public schema. Inspect their values so a
    # nominal scalar field cannot smuggle a nested private key or token.
    for value in authorization.values():
        reject_private_material(value)
    deployment_hash = deployment_evidence.get("evidence_payload_sha256")
    if not isinstance(deployment_hash, str):
        raise JudgeQualificationLiveError("deployment evidence payload hash is missing")
    exact = (
        authorization.get("schema_version") == "membind.judge-live-authorization.v1"
        and authorization.get("protocol_id") == PROTOCOL_ID
        and authorization.get("scientific_surface") == JUDGE_QUALIFICATION_ONLY
        and authorization.get("authorized_run_id") == run_id
        and authorization.get("authorization_path")
        == resolved.relative_to(validation_root).as_posix()
        and authorization.get("live_run_limit") == 1
        and authorization.get("freeze_payload_sha256") == freeze.get("payload_sha256")
        and authorization.get("qualification_live_source_sha256")
        == _sha256_file(Path(__file__))
        and authorization.get("deployment_evidence_payload_sha256") == deployment_hash
        and authorization.get("prelive_evidence_manifest_file_sha256")
        == prelive_evidence_binding.get("sha256")
        and authorization.get("prelive_evidence_manifest_payload_sha256")
        == prelive_evidence.get("payload_sha256")
    )
    if not exact:
        raise JudgeQualificationLiveError("live authorization does not match formal run")

    receipt_path = resolved.with_name(resolved.name + ".consumed.json")
    receipt: dict[str, object] = {
        "schema_version": "membind.judge-live-authorization-consumption.v1",
        "status": "consumed_before_first_request",
        "protocol_id": PROTOCOL_ID,
        "scientific_surface": JUDGE_QUALIFICATION_ONLY,
        "authorized_run_id": run_id,
        "authorization_path": authorization["authorization_path"],
        "live_run_limit": 1,
        "authorization_file_sha256": hashlib.sha256(raw).hexdigest(),
        "authorization_payload_sha256": authorization["payload_sha256"],
        "prelive_evidence_manifest_file_sha256": prelive_evidence_binding["sha256"],
        "prelive_evidence_manifest_payload_sha256": prelive_evidence[
            "payload_sha256"
        ],
    }
    receipt["payload_sha256"] = hashlib.sha256(
        canonical_json_bytes(receipt)
    ).hexdigest()
    receipt_raw = canonical_json_bytes(receipt) + b"\n"
    try:
        descriptor = os.open(
            receipt_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
    except OSError as error:
        raise JudgeQualificationLiveError("live authorization is already consumed") from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(receipt_raw)
            handle.flush()
            os.fsync(handle.fileno())
        parent_descriptor = os.open(receipt_path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    except Exception:
        # Consumption is fail-closed and permanent even if later durability
        # work fails. Never unlink or overwrite this receipt.
        raise
    return {"authorization": raw, "consumption": receipt_raw}


def _rooted_evidence(
    validation_root: Path, evidence_bindings: object
) -> dict[str, dict[str, str]]:
    if not isinstance(evidence_bindings, Mapping) or not evidence_bindings:
        raise ValueError("deployment evidence bindings are required")
    result: dict[str, dict[str, str]] = {}
    for name, binding in evidence_bindings.items():
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(binding, Mapping)
            or set(binding) != {"path", "sha256"}
        ):
            raise ValueError("deployment evidence binding is invalid")
        relative = Path(str(binding["path"]))
        expected = binding["sha256"]
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("deployment evidence path escapes validation root")
        path = (validation_root / relative).resolve(strict=True)
        path.relative_to(validation_root)
        if not path.is_file() or path.is_symlink():
            raise ValueError("deployment evidence path is invalid")
        observed = _sha256_file(path)
        if expected != observed:
            raise ValueError("deployment evidence SHA256 mismatch")
        result[name] = {"path": relative.as_posix(), "sha256": observed}
    return result


def _evaluation_items(freeze: Mapping[str, Any]) -> tuple[EvaluationItem, ...]:
    return tuple(
        EvaluationItem(
            item_id=item["item_id"],
            benchmark=item["benchmark"],
            question_id=item["question_id"],
            question_type=item["question_type"],
            question=item["question"],
            reference_answer=item["reference_answer"],
            hypothesis=item["hypothesis"],
            abstention=item["abstention"],
        )
        for item in freeze["items"]
    )


def _formal_runtime_identity(
    *,
    observed: Mapping[str, object],
    deployment_evidence: Mapping[str, object],
    backend: Qwen3JudgeBackend,
    freeze: Mapping[str, Any],
    bindings: Mapping[str, object],
    deployment_evidence_binding: Mapping[str, object],
) -> dict[str, object]:
    """Combine one endpoint observation with the sealed static identity."""

    offline_binding = freeze["bindings"]["offline_manifest"]
    return {
        "served_model_name": observed["served_model_name"],
        "vllm_version": deployment_evidence["vllm_version"],
        "repository_revision": deployment_evidence["repository_revision"],
        "dtype": deployment_evidence["dtype"],
        "quantization": deployment_evidence["quantization"],
        "max_model_len": observed["max_model_len"],
        "rope_parameters": dict(deployment_evidence["rope_parameters"]),
        "chat_template_sha256": deployment_evidence["chat_template_sha256"],
        "endpoint_identity_sha256": observed["endpoint_identity_sha256"],
        "runtime_backend_config_hash": backend.config_hash,
        "backend_public_config": backend.public_config,
        "effective_enable_thinking": False,
        "temperature": 0,
        "max_tokens": 10,
        "n": 1,
        "python_version": platform.python_version(),
        "openai_sdk_version": importlib.metadata.version("openai"),
        "httpx_version": importlib.metadata.version("httpx"),
        "offline_manifest_file_sha256": offline_binding["sha256"],
        "offline_manifest_payload_sha256": freeze[
            "offline_manifest_payload_sha256"
        ],
        "model_root_identity": observed["model_root_identity"],
        "evidence_bindings": deepcopy(dict(bindings)),
        "deployment_evidence_binding": deepcopy(
            dict(deployment_evidence_binding)
        ),
    }


async def run_formal_judge_qualification(
    *,
    validation_root: Path,
    runs_root: Path,
    run_id: str,
    freeze: dict[str, Any],
    config_mapping: Mapping[str, object],
    deployment_evidence_binding: Mapping[str, object],
    authorization_binding: Mapping[str, object] | None = None,
    prelive_evidence_binding: Mapping[str, object] | None = None,
    models_transport: httpx.AsyncBaseTransport | None = None,
    chat_transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Run the exact frozen 14-item lane through the crash-consistent core."""

    root = Path(validation_root).resolve(strict=True)
    validate_strict_judge_qualification_freeze(freeze, root)
    config = load_judge_live_config(config_mapping)
    if not isinstance(deployment_evidence_binding, Mapping) or set(
        deployment_evidence_binding
    ) != {"path", "sha256"}:
        raise JudgeQualificationLiveError("deployment evidence binding is invalid")
    evidence_relative = Path(str(deployment_evidence_binding["path"]))
    evidence_sha256 = deployment_evidence_binding["sha256"]
    if (
        evidence_relative.is_absolute()
        or ".." in evidence_relative.parts
        or not isinstance(evidence_sha256, str)
    ):
        raise JudgeQualificationLiveError("deployment evidence binding is invalid")
    deployment_evidence = load_verified_judge_deployment_evidence(
        root,
        evidence_relative,
        evidence_sha256,
    )
    normalized_deployment_binding = {
        "path": evidence_relative.as_posix(),
        "sha256": evidence_sha256,
        "payload_sha256": deployment_evidence["evidence_payload_sha256"],
    }
    bindings = dict(deployment_evidence["evidence_bindings"])
    authorization_documents: dict[str, bytes] | None = None
    if authorization_binding is None:
        if not (
            isinstance(models_transport, httpx.MockTransport)
            and isinstance(chat_transport, httpx.MockTransport)
        ):
            raise JudgeQualificationLiveError(
                "formal Judge without live authorization requires double MockTransport dry-run"
            )
    else:
        if not isinstance(prelive_evidence_binding, Mapping) or set(
            prelive_evidence_binding
        ) != {"path", "sha256"}:
            raise JudgeQualificationLiveError(
                "pre-live evidence binding is required for live authorization"
            )
        prelive_relative = Path(str(prelive_evidence_binding["path"]))
        prelive_sha256 = prelive_evidence_binding["sha256"]
        if not isinstance(prelive_sha256, str):
            raise JudgeQualificationLiveError("pre-live evidence binding is invalid")
        prelive_path = _rooted_regular_file(
            root, prelive_relative, "pre-live evidence manifest"
        )
        prelive_evidence, prelive_raw = _read_canonical_sealed(
            prelive_path, "pre-live evidence manifest"
        )
        if hashlib.sha256(prelive_raw).hexdigest() != prelive_sha256:
            raise JudgeQualificationLiveError(
                "pre-live evidence manifest file hash mismatch"
            )
        validate_judge_prelive_evidence_manifest(prelive_evidence, root)
        if (
            prelive_evidence.get("authorized_run_id") != run_id
            or prelive_evidence.get("live_run_limit") != 1
            or prelive_evidence.get("bindings", {}).get("strict_freeze", {}).get(
                "payload_sha256"
            )
            != freeze.get("payload_sha256")
            or prelive_evidence.get("bindings", {}).get("deployment_evidence", {}).get(
                "payload_sha256"
            )
            != deployment_evidence.get("evidence_payload_sha256")
        ):
            raise JudgeQualificationLiveError(
                "pre-live evidence manifest does not match formal run"
            )
        normalized_prelive_binding = {
            "path": prelive_path.relative_to(root).as_posix(),
            "sha256": prelive_sha256,
        }
        authorization_documents = _consume_live_authorization(
            validation_root=root,
            run_id=run_id,
            freeze=freeze,
            deployment_evidence=deployment_evidence,
            prelive_evidence_binding=normalized_prelive_binding,
            prelive_evidence=prelive_evidence,
            authorization_binding=authorization_binding,
        )

    backend = Qwen3JudgeBackend(
        base_url=config.base_url,
        api_key=config.api_key,
        thinking_control="client_request",
        max_attempts=1,
        transport=chat_transport,
    )
    try:
        observed = await capture_judge_runtime_identity(
            config,
            transport=models_transport,
        )
        runtime_identity = _formal_runtime_identity(
            observed=observed,
            deployment_evidence=deployment_evidence,
            backend=backend,
            freeze=freeze,
            bindings=bindings,
            deployment_evidence_binding=normalized_deployment_binding,
        )

        async def read_runtime_identity() -> dict[str, object]:
            current = await capture_judge_runtime_identity(
                config,
                transport=models_transport,
            )
            return _formal_runtime_identity(
                observed=current,
                deployment_evidence=deployment_evidence,
                backend=backend,
                freeze=freeze,
                bindings=bindings,
                deployment_evidence_binding=normalized_deployment_binding,
            )

        store = JudgeQualificationArtifactStore.create(
            runs_root=runs_root,
            run_id=run_id,
            freeze=freeze,
            runtime_identity=runtime_identity,
            command_argv=["judge-qualification", "--formal-frozen-14", "--run-id", run_id],
            live_authorization_documents=authorization_documents,
            prelive_evidence_document=(
                prelive_raw if authorization_documents is not None else None
            ),
        )
        evaluator = LongMemEvalAdapter(backend)
        return await run_judge_qualification(
            freeze=freeze,
            items=_evaluation_items(freeze),
            evaluator=evaluator,
            store=store,
            runtime_identity_reader=read_runtime_identity,
        )
    finally:
        await backend.aclose()


__all__ = [
    "BoundedJudgeQualificationRun",
    "JudgeLiveConfig",
    "JudgeQualificationLiveError",
    "capture_judge_runtime_identity",
    "build_judge_prelive_evidence_manifest",
    "build_judge_test_evidence_report",
    "load_verified_judge_deployment_evidence",
    "load_judge_live_config",
    "run_bounded_judge_qualification",
    "run_formal_judge_qualification",
    "validate_judge_prelive_evidence_manifest",
    "validate_judge_test_evidence_report",
]
