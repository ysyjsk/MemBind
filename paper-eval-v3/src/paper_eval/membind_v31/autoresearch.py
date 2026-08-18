"""Bounded development-only autoresearch contracts for MemBind v3.1.

The module derives a fresh 12-source plan from the frozen formal plan and
reduces candidate results against a sealed U0 prefix.  It has no service or
database dependency and grants no merge authority.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from paper_eval.apc_aligned_baseline import build_apc_aligned_baseline_plan
from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.method_plan import (
    build_membind_v31_live_plan,
    verify_membind_v31_method_plan,
)


PROBE_HISTORY = "07741c45"
PROBE_SOURCE_COUNT = 12
MAX_CANDIDATES = 3
AUTHORIZATION_SCHEMA = "membind.paper-eval-v3.membind-v31-autoresearch-authorization.v1"
REFERENCE_SCHEMA = "membind.paper-eval-v3.membind-v31-autoresearch-u0-reference.v1"
DECISION_SCHEMA = "membind.paper-eval-v3.membind-v31-autoresearch-decision.v1"
MERGE_AUTHORITY = "NONE_NON_MERGEABLE_DEVELOPMENT_PROBE"
_CANDIDATE = re.compile(r"^c0[0-2]$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TSV_COLUMNS = (
    "candidate_id",
    "parent_code_sha256",
    "code_sha256",
    "status",
    "artifact_status",
    "semantic_status",
    "p95_freshness_ns",
    "makespan_ns",
    "observed_max_inflight",
    "direct_violations",
    "p95_ratio",
    "makespan_ratio",
    "engineering_review_required",
    "description",
    "payload_sha256",
)


class AutoresearchProbeError(ValueError):
    """A probe identity, comparator, decision, or append-only ledger failed."""


def _fail(code: str) -> AutoresearchProbeError:
    return AutoresearchProbeError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _fail(code)
    return value


def _sealed(value: Mapping[str, object], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    selected = deepcopy(dict(value))
    stored = _sha(selected.get("payload_sha256"), code)
    body = {key: child for key, child in selected.items() if key != "payload_sha256"}
    if payload_sha256(body) != stored:
        raise _fail(code)
    return selected


def _seal(body: Mapping[str, object]) -> dict[str, Any]:
    selected = deepcopy(dict(body))
    selected["payload_sha256"] = payload_sha256(selected)
    return selected


def _candidate(value: object) -> str:
    if not isinstance(value, str) or _CANDIDATE.fullmatch(value) is None:
        raise _fail("candidate identity invalid")
    return value


def _description(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 240
        or any(character in value for character in ("\t", "\n", "\r"))
    ):
        raise _fail("description invalid")
    return value.strip()


def build_autoresearch_probe_plan(
    *,
    verified_formal_plan: Mapping[str, object],
    probe_run_id: str,
    candidate_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive a verified fresh plan whose measured block is sources 0..11."""

    candidate = _candidate(candidate_id)
    try:
        formal = verify_membind_v31_method_plan(verified_formal_plan)
    except ValueError:
        raise _fail("formal plan invalid") from None
    if (
        not isinstance(probe_run_id, str)
        or not probe_run_id.endswith(f"-{candidate}")
        or formal.get("representative_history_id") != PROBE_HISTORY
        or formal.get("compile_workers") != 2
        or formal.get("lookahead") != 2
        or formal.get("global_llm_admission_k") != 2
        or formal.get("prefix_match_unit") != 16
        or formal.get("decode_context_parallel_size") != 1
    ):
        raise _fail("probe plan identity invalid")
    source_inventory = formal.get("history_source_sha256s")
    histories = formal.get("histories")
    if not isinstance(source_inventory, Mapping) or not isinstance(histories, list):
        raise _fail("formal source inventory invalid")
    sources = deepcopy(dict(source_inventory))
    representative = sources.get(PROBE_HISTORY)
    if not isinstance(representative, list) or len(representative) < PROBE_SOURCE_COUNT:
        raise _fail("formal source prefix unavailable")
    sources[PROBE_HISTORY] = representative[:PROBE_SOURCE_COUNT]
    try:
        baseline = build_apc_aligned_baseline_plan(
            run_id=str(formal["baseline_run_id"]),
            history_source_sha256s={history: sources[history] for history in histories},
            interarrival_ns=int(formal["interarrival_ns"]),
            execution_envelope_sha256=str(formal["shared_execution_envelope_sha256"]),
            service_reference_ns=int(formal["service_reference_ns"]),
            normalized_offered_load=float(formal["normalized_offered_load"]),
        )
        probe = build_membind_v31_live_plan(
            run_id=probe_run_id,
            verified_baseline_plan=baseline,
            methodology_sha256=str(formal["methodology_sha256"]),
            workplan_sha256=str(formal["workplan_sha256"]),
        )
        verify_membind_v31_method_plan(probe)
    except (KeyError, TypeError, ValueError):
        raise _fail("probe plan derivation failed") from None
    expected_offsets = formal["arrival_traces"][PROBE_HISTORY]["arrival_offsets_ns"][
        :PROBE_SOURCE_COUNT
    ]
    if (
        probe["arrival_traces"][PROBE_HISTORY]["arrival_offsets_ns"]
        != expected_offsets
        or probe["blocks"][0]["namespace"] == formal["blocks"][0]["namespace"]
        or probe["blocks"][0]["source_count"] != PROBE_SOURCE_COUNT
    ):
        raise _fail("probe plan derivation failed")
    authorization = _seal(
        {
            "schema_version": AUTHORIZATION_SCHEMA,
            "status": "AUTHORIZED",
            "candidate_id": candidate,
            "probe_run_id": probe_run_id,
            "history_id": PROBE_HISTORY,
            "source_sequences": list(range(PROBE_SOURCE_COUNT)),
            "source_count": PROBE_SOURCE_COUNT,
            "parent_formal_plan_payload_sha256": formal["payload_sha256"],
            "probe_method_plan_payload_sha256": probe["payload_sha256"],
            "methodology_sha256": formal["methodology_sha256"],
            "workplan_sha256": formal["workplan_sha256"],
            "compile_workers": 2,
            "lookahead": 2,
            "global_llm_admission_k": 2,
            "prefix_match_unit": 16,
            "decode_context_parallel_size": 1,
            "merge_authority": MERGE_AUTHORITY,
            "heldout_data_accessed": False,
        }
    )
    return probe, authorization


def derive_u0_prefix_reference(
    baseline_block_result: Mapping[str, object],
) -> dict[str, Any]:
    """Derive the fixed first-12 U0 comparator from its sealed full result."""

    result = _sealed(baseline_block_result, "baseline result invalid")
    performance = result.get("performance")
    rows = performance.get("per_source") if isinstance(performance, Mapping) else None
    if (
        result.get("status") != "PASS"
        or result.get("method") != "U0-aligned"
        or result.get("history_id") != PROBE_HISTORY
        or isinstance(rows, (str, bytes))
        or not isinstance(rows, Sequence)
        or len(rows) < PROBE_SOURCE_COUNT
    ):
        raise _fail("baseline prefix reference invalid")
    selected: list[dict[str, int]] = []
    for sequence, row in enumerate(rows[:PROBE_SOURCE_COUNT]):
        if not isinstance(row, Mapping) or row.get("source_sequence") != sequence:
            raise _fail("baseline prefix reference invalid")
        arrival = row.get("arrival_timestamp_ns")
        publication = row.get("publication_timestamp_ns")
        freshness = row.get("freshness_ns")
        if (
            isinstance(arrival, bool)
            or not isinstance(arrival, int)
            or arrival < 0
            or isinstance(publication, bool)
            or not isinstance(publication, int)
            or publication <= arrival
            or isinstance(freshness, bool)
            or not isinstance(freshness, int)
            or freshness != publication - arrival
        ):
            raise _fail("baseline prefix reference invalid")
        selected.append(
            {
                "source_sequence": sequence,
                "arrival_timestamp_ns": arrival,
                "publication_timestamp_ns": publication,
                "freshness_ns": freshness,
            }
        )
    ordered = sorted(row["freshness_ns"] for row in selected)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    makespan = max(row["publication_timestamp_ns"] for row in selected) - min(
        row["arrival_timestamp_ns"] for row in selected
    )
    return _seal(
        {
            "schema_version": REFERENCE_SCHEMA,
            "status": "PASS",
            "method": "U0-aligned",
            "history_id": PROBE_HISTORY,
            "source_sequences": list(range(PROBE_SOURCE_COUNT)),
            "source_count": PROBE_SOURCE_COUNT,
            "p95_freshness_ns": p95,
            "makespan_ns": makespan,
            "parent_baseline_block_result_payload_sha256": result["payload_sha256"],
            "merge_authority": "COMPARATOR_ONLY",
            "heldout_data_accessed": False,
        }
    )


def assess_probe_candidate(
    *,
    candidate_id: str,
    candidate_result: Mapping[str, object],
    comparator: Mapping[str, object],
    code_sha256: str,
    parent_code_sha256: str,
    description: str,
) -> dict[str, Any]:
    """Apply the frozen correctness-first, two-metric keep/discard rule."""

    candidate = _candidate(candidate_id)
    code = _sha(code_sha256, "code identity invalid")
    parent = _sha(parent_code_sha256, "parent code identity invalid")
    detail = _description(description)
    result = _sealed(candidate_result, "candidate result invalid")
    comparator_p95 = _positive_int(
        comparator.get("p95_freshness_ns"), "comparator invalid"
    )
    comparator_makespan = _positive_int(
        comparator.get("makespan_ns"), "comparator invalid"
    )
    comparator_sha = _sha(comparator.get("payload_sha256"), "comparator invalid")
    performance = result.get("performance")
    admission = result.get("request_admission")
    checkpoint = result.get("checkpoint")
    p95 = (
        performance.get("p95_freshness_ns")
        if isinstance(performance, Mapping)
        else None
    )
    makespan = performance.get("makespan_ns") if isinstance(performance, Mapping) else None
    inflight = (
        admission.get("observed_max_inflight") if isinstance(admission, Mapping) else None
    )
    violations = result.get("direct_violation_count")
    artifact_valid = (
        result.get("status") == "PASS"
        and result.get("method") == "MemBind"
        and result.get("history_id") == PROBE_HISTORY
        and result.get("source_count") == PROBE_SOURCE_COUNT
        and isinstance(checkpoint, Mapping)
        and checkpoint.get("complete_coverage") is True
        and checkpoint.get("terminal_status") == "COMPLETED"
        and not isinstance(inflight, bool)
        and isinstance(inflight, int)
        and 0 <= inflight <= 2
        and not isinstance(p95, bool)
        and isinstance(p95, int)
        and p95 > 0
        and not isinstance(makespan, bool)
        and isinstance(makespan, int)
        and makespan > 0
        and not isinstance(violations, bool)
        and isinstance(violations, int)
        and violations >= 0
    )
    semantic_safe = artifact_valid and violations == 0
    p95_ratio = float(p95) / comparator_p95 if artifact_valid else None
    makespan_ratio = float(makespan) / comparator_makespan if artifact_valid else None
    non_regression = bool(
        artifact_valid and p95_ratio <= 1.05 and makespan_ratio <= 1.05
    )
    material_improvement = bool(
        artifact_valid and (p95_ratio <= 0.95 or makespan_ratio <= 0.95)
    )
    keep = semantic_safe and non_regression and material_improvement
    return _seal(
        {
            "schema_version": DECISION_SCHEMA,
            "candidate_id": candidate,
            "parent_code_sha256": parent,
            "code_sha256": code,
            "status": "keep" if keep else "discard",
            "artifact_status": "SEALED_VALID" if artifact_valid else "INVALID_INFRA",
            "semantic_status": (
                "SAFE"
                if semantic_safe
                else "VIOLATION_OBSERVED"
                if artifact_valid and violations > 0
                else "NOT_APPLICABLE"
            ),
            "candidate_result_payload_sha256": result["payload_sha256"],
            "comparator_payload_sha256": comparator_sha,
            "p95_freshness_ns": p95 if artifact_valid else None,
            "makespan_ns": makespan if artifact_valid else None,
            "observed_max_inflight": inflight if artifact_valid else None,
            "direct_violations": violations if artifact_valid else None,
            "p95_ratio": p95_ratio,
            "makespan_ratio": makespan_ratio,
            "non_regression": non_regression,
            "material_improvement": material_improvement,
            "engineering_review_required": not (semantic_safe and material_improvement),
            "description": detail,
            "merge_authority": MERGE_AUTHORITY,
        }
    )


def record_probe_crash(
    *,
    candidate_id: str,
    code_sha256: str,
    parent_code_sha256: str,
    error_class: str,
    description: str,
) -> dict[str, Any]:
    """Create a content-safe crash row without persisting private exception text."""

    candidate = _candidate(candidate_id)
    code = _sha(code_sha256, "code identity invalid")
    parent = _sha(parent_code_sha256, "parent code identity invalid")
    detail = _description(description)
    if (
        not isinstance(error_class, str)
        or not error_class
        or len(error_class) > 200
        or any(character in error_class for character in ("\t", "\n", "\r"))
    ):
        raise _fail("error class invalid")
    return _seal(
        {
            "schema_version": DECISION_SCHEMA,
            "candidate_id": candidate,
            "parent_code_sha256": parent,
            "code_sha256": code,
            "status": "crash",
            "artifact_status": "INCOMPLETE",
            "semantic_status": "NOT_APPLICABLE",
            "candidate_result_payload_sha256": None,
            "comparator_payload_sha256": None,
            "p95_freshness_ns": None,
            "makespan_ns": None,
            "observed_max_inflight": None,
            "direct_violations": None,
            "p95_ratio": None,
            "makespan_ratio": None,
            "non_regression": False,
            "material_improvement": False,
            "engineering_review_required": True,
            "description": f"{detail}; error_class={error_class}",
            "merge_authority": MERGE_AUTHORITY,
        }
    )


def append_results_tsv(path: Path, decision: Mapping[str, object]) -> None:
    """Durably append one unique, content-safe candidate decision."""

    selected = _sealed(decision, "decision invalid")
    candidate = _candidate(selected.get("candidate_id"))
    _description(selected.get("description"))
    if selected.get("status") not in {"keep", "discard", "crash"}:
        raise _fail("decision status invalid")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    header = "\t".join(_TSV_COLUMNS)
    if target.exists():
        try:
            existing = target.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            raise _fail("results ledger invalid") from None
        if not existing or existing[0] != header:
            raise _fail("results ledger invalid")
        if candidate in {line.split("\t", 1)[0] for line in existing[1:] if line}:
            raise _fail("candidate already recorded")
        prefix = ""
    else:
        prefix = header + "\n"
    values = []
    for column in _TSV_COLUMNS:
        value = selected.get(column)
        if isinstance(value, bool):
            values.append("true" if value else "false")
        elif value is None:
            values.append("NA")
        else:
            rendered = str(value)
            if any(character in rendered for character in ("\t", "\n", "\r")):
                raise _fail("results ledger value invalid")
            values.append(rendered)
    try:
        with target.open("a", encoding="utf-8") as handle:
            handle.write(prefix + "\t".join(values) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        raise _fail("results ledger append failed") from None


__all__ = [
    "MAX_CANDIDATES",
    "PROBE_HISTORY",
    "PROBE_SOURCE_COUNT",
    "AutoresearchProbeError",
    "append_results_tsv",
    "assess_probe_candidate",
    "build_autoresearch_probe_plan",
    "derive_u0_prefix_reference",
    "record_probe_crash",
]
