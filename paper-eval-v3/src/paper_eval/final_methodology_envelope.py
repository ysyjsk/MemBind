"""Seal the completed development methodology and its exact evidence files.

The envelope is documentation-only.  Building or verifying it performs no live
model, database, namespace, or held-out-data operation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from .artifacts import atomic_write_json, canonical_bytes, payload_sha256, sha256_file
from .methodology_document import MethodologyDocumentError, render_methodology_document


SCHEMA_VERSION = "membind.paper-eval-v3.final-methodology-envelope.v1"
AUTHORITY_EFFECT = "DOCUMENTATION_ONLY_NO_LIVE_AUTHORITY"
SCOPE = "DEVELOPMENT_EXPOSED_DESCRIPTIVE_ONLY"
KEY_SOURCE_FILES = (
    "paper-eval-v3/src/paper_eval/artifacts.py",
    "paper-eval-v3/src/paper_eval/development_baseline_report.py",
    "paper-eval-v3/src/paper_eval/graph_quality_overlay.py",
    "paper-eval-v3/src/paper_eval/methodology_decision.py",
    "paper-eval-v3/src/paper_eval/methodology_document.py",
    "paper-eval-v3/src/paper_eval/final_methodology_envelope.py",
    "paper-eval-v3/scripts/write_three_baseline_development_report.py",
    "paper-eval-v3/scripts/finalize_methodology_decision.py",
    "paper-eval-v3/scripts/finalize_main_methodology.py",
    "paper-eval-v3/scripts/finalize_final_methodology_envelope.py",
    "paper-eval-v3/tests/test_final_methodology_envelope.py",
)


class FinalMethodologyEnvelopeError(ValueError):
    """One or more final evidence files failed closed."""


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise FinalMethodologyEnvelopeError(
                    f"{label} contains duplicate fields"
                )
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates
        )
    except FinalMethodologyEnvelopeError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise FinalMethodologyEnvelopeError(f"{label} is unreadable") from None
    if not isinstance(value, dict):
        raise FinalMethodologyEnvelopeError(f"{label} is not a JSON object")
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise FinalMethodologyEnvelopeError(f"{field} is invalid")
    return value


def _sealed_json(
    path: Path,
    *,
    label: str,
    schema_version: str,
) -> tuple[dict[str, Any], str, str]:
    value = _load_object(path, label=label)
    stored = _text(value.get("payload_sha256"), field=f"{label} payload seal")
    body = {key: item for key, item in value.items() if key != "payload_sha256"}
    if stored != payload_sha256(body):
        raise FinalMethodologyEnvelopeError(f"{label} payload seal mismatch")
    if value.get("schema_version") != schema_version or value.get("status") != "PASS":
        raise FinalMethodologyEnvelopeError(f"{label} identity is invalid")
    file_hash = sha256_file(path)
    if file_hash == "missing":
        raise FinalMethodologyEnvelopeError(f"{label} is missing")
    return value, stored, file_hash


def _relative(path: Path, *, root: Path, label: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        raise FinalMethodologyEnvelopeError(
            f"{label} is outside the repository root"
        ) from None


def _source(
    *,
    path: Path,
    root: Path,
    file_sha256: str,
    payload_sha256_value: str | None = None,
    run_id: str | None = None,
    schema_version: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": _relative(path, root=root, label="source path"),
        "file_sha256": file_sha256,
    }
    if payload_sha256_value is not None:
        result["payload_sha256"] = payload_sha256_value
    if run_id is not None:
        result["run_id"] = run_id
    if schema_version is not None:
        result["schema_version"] = schema_version
    if status is not None:
        result["status"] = status
    return result


def _source_code_hashes(
    *,
    repository_root: Path,
    paths: Sequence[Path] | None,
) -> dict[str, str]:
    selected = (
        tuple(repository_root / item for item in KEY_SOURCE_FILES)
        if paths is None
        else tuple(paths)
    )
    if not selected:
        raise FinalMethodologyEnvelopeError("source code inventory is empty")
    result: dict[str, str] = {}
    for path in selected:
        relative = _relative(path, root=repository_root, label="source code path")
        if relative in result:
            raise FinalMethodologyEnvelopeError("source code inventory is duplicated")
        digest = sha256_file(path)
        if digest == "missing":
            raise FinalMethodologyEnvelopeError(f"source code is missing: {relative}")
        result[relative] = digest
    return dict(sorted(result.items()))


def _junit_summary(path: Path) -> dict[str, Any]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        raise FinalMethodologyEnvelopeError("final JUnit is unreadable") from None
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        raise FinalMethodologyEnvelopeError("final JUnit has no test suite")

    totals = {name: 0 for name in ("tests", "errors", "failures", "skipped")}
    for suite in suites:
        for name in totals:
            raw = suite.get(name, "0")
            try:
                value = int(raw)
            except (TypeError, ValueError):
                raise FinalMethodologyEnvelopeError(
                    f"final JUnit {name} is invalid"
                ) from None
            if value < 0:
                raise FinalMethodologyEnvelopeError(
                    f"final JUnit {name} is invalid"
                )
            totals[name] += value
    if totals["tests"] <= 0 or totals["errors"] or totals["failures"]:
        raise FinalMethodologyEnvelopeError("final JUnit is not green")
    totals["passed"] = (
        totals["tests"]
        - totals["errors"]
        - totals["failures"]
        - totals["skipped"]
    )
    return totals


def build_final_methodology_envelope(
    *,
    repository_root: Path,
    baseline_path: Path,
    overlay_path: Path,
    report_path: Path,
    decision_path: Path,
    methodology_path: Path,
    junit_path: Path,
    source_code_paths: Sequence[Path] | None = None,
) -> dict[str, Any]:
    """Build a deterministic sealed envelope after validating every input."""

    baseline, baseline_payload, baseline_file = _sealed_json(
        baseline_path,
        label="three-baseline report",
        schema_version="membind.paper-eval-v3.three-baseline-report.v1",
    )
    overlay, overlay_payload, overlay_file = _sealed_json(
        overlay_path,
        label="overlay",
        schema_version="membind.paper-eval-v3.graph-quality-report.v1",
    )
    report, report_payload, report_file = _sealed_json(
        report_path,
        label="development report",
        schema_version="membind.paper-eval-v3.development-baseline-report.v1",
    )
    decision, decision_payload, decision_file = _sealed_json(
        decision_path,
        label="methodology decision",
        schema_version="membind.paper-eval-v3.methodology-decision.v1",
    )

    report_run_id = _text(report.get("report_run_id"), field="report run id")
    decision_run_id = _text(decision.get("decision_run_id"), field="decision run id")
    if (
        report.get("data_role") != "DEVELOPMENT_EXPOSED"
        or report.get("heldout_data_accessed") is not False
    ):
        raise FinalMethodologyEnvelopeError("development report data boundary drift")
    if decision.get("scope") != SCOPE:
        raise FinalMethodologyEnvelopeError("methodology decision scope drift")
    if baseline.get("run_id") != report.get("suite_run_id"):
        raise FinalMethodologyEnvelopeError("three-baseline run identity drift")
    if overlay.get("overlay_run_id") != report.get("overlay_run_id"):
        raise FinalMethodologyEnvelopeError("overlay run identity drift")
    bindings = decision.get("input_bindings")
    if not isinstance(bindings, Mapping):
        raise FinalMethodologyEnvelopeError("decision bindings are invalid")
    if (
        bindings.get("report_run_id") != report_run_id
        or bindings.get("native_run_id") != report.get("native_run_id")
        or bindings.get("suite_run_id") != report.get("suite_run_id")
        or bindings.get("overlay_run_id") != report.get("overlay_run_id")
        or bindings.get("report_payload_sha256") != report_payload
        or bindings.get("report_file_sha256") != report_file
    ):
        raise FinalMethodologyEnvelopeError("decision/report binding drift")

    try:
        methodology = methodology_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise FinalMethodologyEnvelopeError("methodology document is unreadable") from None
    if (
        "> Status: `DESIGN_COMPLETE`" not in methodology
        or "EVIDENCE_PENDING" in methodology
        or "PENDING_SEALED_REPORT" in methodology
    ):
        raise FinalMethodologyEnvelopeError("methodology document is not finalized")
    if f"> Methodology decision run: `{decision_run_id}`" not in methodology:
        raise FinalMethodologyEnvelopeError("methodology decision run binding drift")
    if (
        f"> Methodology decision payload SHA256: `{decision_payload}`"
        not in methodology
    ):
        raise FinalMethodologyEnvelopeError("methodology decision payload binding drift")
    try:
        rerendered = render_methodology_document(methodology, report, decision)
    except MethodologyDocumentError as error:
        raise FinalMethodologyEnvelopeError(
            "methodology deterministic render verification failed"
        ) from error
    if rerendered != methodology:
        raise FinalMethodologyEnvelopeError(
            "methodology deterministic render verification failed"
        )
    methodology_file = sha256_file(methodology_path)
    if methodology_file == "missing":
        raise FinalMethodologyEnvelopeError("methodology document is missing")

    junit = _junit_summary(junit_path)
    junit_file = sha256_file(junit_path)
    if junit_file == "missing":
        raise FinalMethodologyEnvelopeError("final JUnit is missing")
    source_code = _source_code_hashes(
        repository_root=repository_root,
        paths=source_code_paths,
    )
    decision_summary = {
        field: _text(decision.get(field), field=f"decision {field}")
        for field in (
            "actual_decision_matrix_cell",
            "problem_verdict",
            "mechanism_status",
            "paper_claim_status",
            "live_method_status",
        )
    }

    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "envelope_run_id": decision_run_id,
        "scope": SCOPE,
        "authority_effect": AUTHORITY_EFFECT,
        "data_role": "DEVELOPMENT_EXPOSED",
        "heldout_data_accessed": False,
        "decision_summary": decision_summary,
        "sources": {
            "three_baselines": _source(
                path=baseline_path,
                root=repository_root,
                file_sha256=baseline_file,
                payload_sha256_value=baseline_payload,
                run_id=_text(baseline.get("run_id"), field="baseline run id"),
                schema_version=_text(
                    baseline.get("schema_version"), field="baseline schema"
                ),
                status="PASS",
            ),
            "graph_quality_overlay": _source(
                path=overlay_path,
                root=repository_root,
                file_sha256=overlay_file,
                payload_sha256_value=overlay_payload,
                run_id=_text(
                    overlay.get("overlay_run_id"), field="overlay run id"
                ),
                schema_version=_text(
                    overlay.get("schema_version"), field="overlay schema"
                ),
                status="PASS",
            ),
            "development_report": _source(
                path=report_path,
                root=repository_root,
                file_sha256=report_file,
                payload_sha256_value=report_payload,
                run_id=report_run_id,
                schema_version=_text(
                    report.get("schema_version"), field="report schema"
                ),
                status="PASS",
            ),
            "methodology_decision": _source(
                path=decision_path,
                root=repository_root,
                file_sha256=decision_file,
                payload_sha256_value=decision_payload,
                run_id=decision_run_id,
                schema_version=_text(
                    decision.get("schema_version"), field="decision schema"
                ),
                status="PASS",
            ),
        },
        "methodology_document": {
            **_source(
                path=methodology_path,
                root=repository_root,
                file_sha256=methodology_file,
            ),
            "status": "DESIGN_COMPLETE",
            "bound_decision_run_id": decision_run_id,
            "bound_decision_payload_sha256": decision_payload,
            "deterministic_render_verified": True,
        },
        "tdd": {
            **_source(
                path=junit_path,
                root=repository_root,
                file_sha256=junit_file,
            ),
            **junit,
            "warnings_evidence": "NOT_ENCODED_BY_JUNIT_XML",
        },
        "source_code_sha256": source_code,
        "cross_checks": {
            "json_payload_seals_verified": True,
            "run_identity_chain_verified": True,
            "report_decision_file_binding_verified": True,
            "methodology_decision_binding_verified": True,
            "junit_green_verified": True,
        },
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def finalize_final_methodology_envelope(
    *,
    repository_root: Path,
    baseline_path: Path,
    overlay_path: Path,
    report_path: Path,
    decision_path: Path,
    methodology_path: Path,
    junit_path: Path,
    source_code_paths: Sequence[Path] | None = None,
    output_path: Path,
) -> dict[str, Any]:
    """Atomically persist the envelope or verify an identical existing file."""

    envelope = build_final_methodology_envelope(
        repository_root=repository_root,
        baseline_path=baseline_path,
        overlay_path=overlay_path,
        report_path=report_path,
        decision_path=decision_path,
        methodology_path=methodology_path,
        junit_path=junit_path,
        source_code_paths=source_code_paths,
    )
    if output_path.exists():
        existing = _load_object(output_path, label="existing envelope")
        stored = existing.get("payload_sha256")
        existing_body = {
            key: value for key, value in existing.items() if key != "payload_sha256"
        }
        if (
            not isinstance(stored, str)
            or stored != payload_sha256(existing_body)
            or canonical_bytes(existing) != canonical_bytes(envelope)
        ):
            raise FinalMethodologyEnvelopeError(
                "existing envelope conflicts with final inputs"
            )
        return envelope
    atomic_write_json(output_path, envelope)
    return envelope
