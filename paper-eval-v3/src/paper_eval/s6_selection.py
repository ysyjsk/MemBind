"""Correctness-first selection over exactly 32 finalized S6 block results."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path

from . import PROTOCOL_VERSION
from .artifacts import payload_sha256
from .s6_block_result import verify_s6_block_result
from .s6_calibration_contract import (
    CELL_COUNT,
    CONCURRENCIES,
    DEVELOPMENT_HISTORIES,
    METHODS,
    verify_s6_cell_identity,
    verify_s6_matrix_freeze,
)


SCHEMA = "membind.paper-eval-v3.s6-method-selection.v1"
STAGE = "S6_DEVELOPMENT_ONLY_CONCURRENCY_CALIBRATION"
RUN_ID = "s6-method-selection-freeze-20260816-001"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_MATRIX_FIELDS = {"file_sha256", "payload_sha256", "matrix_sha256"}
_PROJECTION_FIELDS = {
    "cell",
    "block_file_sha256",
    "block_payload_sha256",
    "source_count",
    "source_manifest_sha256",
    "execution_identity_sha256",
    "runner_status",
    "successful_goodput_per_s",
    "p95_freshness_ns",
    "direct_hard_violation_count",
    "deterministic_correctness_gate",
    "hidden_fallback_count",
    "pstar_performance_eligible",
    "mstar_qualified",
    "mstar_disqualification_reasons",
}
_CANDIDATE_FIELDS = {
    "concurrency",
    "block_count",
    "median_successful_goodput_per_s",
    "median_p95_freshness_ns",
    "total_direct_hard_violations",
    "total_hidden_fallbacks",
    "correctness_gate_fail_count",
    "treatment_failure_block_count",
    "qualified",
    "disqualification_reasons",
}
_METHOD_RESULT_FIELDS = {
    "candidates",
    "qualified_concurrencies",
    "selected_concurrency",
    "selection_rule",
    "tie_break_rule",
}
_AUTHORITY = {
    "method_selection_frozen": True,
    "next_stage_authorized": False,
    "pilot_execution_authorized": False,
    "final_paper_test_execution_authorized": False,
}


def _median(values: Sequence[int | float]) -> int | float:
    """Return a deterministic median without an ambiguous top-level import."""

    if not values:
        raise ValueError("median requires at least one value")
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2
_PAYLOAD_FIELDS = {
    "schema_version",
    "stage",
    "status",
    "verdict",
    "stop_reason",
    "matrix",
    "block_count",
    "block_projections",
    "method_results",
    "selected_concurrency",
    "authority",
    "selection_sha256",
}


class S6SelectionError(ValueError):
    """The S6 block inventory or deterministic method selection is invalid."""


def _fail(code: str) -> S6SelectionError:
    return S6SelectionError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _count(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _matrix_binding(
    freeze: Mapping[str, object], matrix_file_sha256: str
) -> dict[str, str]:
    try:
        artifact = verify_s6_matrix_freeze(freeze)
    except Exception:
        raise _fail("matrix_freeze_invalid") from None
    return {
        "file_sha256": _sha(matrix_file_sha256, "matrix_file_sha256_invalid"),
        "payload_sha256": _sha(
            artifact.get("payload_sha256"), "matrix_payload_sha256_invalid"
        ),
        "matrix_sha256": _sha(
            artifact["payload"].get("matrix_sha256"), "matrix_sha256_invalid"
        ),
    }


def _verify_matrix(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _MATRIX_FIELDS:
        raise _fail("matrix_binding_invalid")
    return {key: _sha(value.get(key), f"matrix_{key}_invalid") for key in sorted(_MATRIX_FIELDS)}


def _projection(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _PROJECTION_FIELDS:
        raise _fail("block_projection_invalid")
    selected = deepcopy(dict(value))
    try:
        cell = verify_s6_cell_identity(selected.get("cell", {}))
    except Exception:
        raise _fail("block_projection_cell_invalid") from None
    source_count = _count(selected.get("source_count"), "source_count_invalid")
    goodput = selected.get("successful_goodput_per_s")
    p95 = selected.get("p95_freshness_ns")
    if (
        source_count < 1
        or isinstance(goodput, bool)
        or not isinstance(goodput, (int, float))
        or not math.isfinite(float(goodput))
        or float(goodput) < 0
        or (
            p95 is not None
            and (
                isinstance(p95, bool)
                or not isinstance(p95, int)
                or p95 < 0
            )
        )
    ):
        raise _fail("block_projection_metric_invalid")
    gate = selected.get("deterministic_correctness_gate")
    if gate not in {"PASS", "FAIL"}:
        raise _fail("block_projection_correctness_invalid")
    reasons = selected.get("mstar_disqualification_reasons")
    if (
        not isinstance(reasons, list)
        or any(not isinstance(item, str) or not item for item in reasons)
        or reasons != sorted(set(reasons))
    ):
        raise _fail("block_projection_qualification_invalid")
    method = str(cell["method"])
    p_eligible = selected.get("pstar_performance_eligible")
    m_qualified = selected.get("mstar_qualified")
    if method == "P*":
        if p_eligible is not True or m_qualified is not None or reasons:
            raise _fail("block_projection_qualification_invalid")
    elif (
        p_eligible is not False
        or not isinstance(m_qualified, bool)
        or m_qualified is (bool(reasons))
    ):
        raise _fail("block_projection_qualification_invalid")
    selected.update(
        cell=cell,
        block_file_sha256=_sha(
            selected.get("block_file_sha256"), "block_file_sha256_invalid"
        ),
        block_payload_sha256=_sha(
            selected.get("block_payload_sha256"), "block_payload_sha256_invalid"
        ),
        source_count=source_count,
        source_manifest_sha256=_sha(
            selected.get("source_manifest_sha256"), "source_manifest_invalid"
        ),
        execution_identity_sha256=_sha(
            selected.get("execution_identity_sha256"), "execution_identity_invalid"
        ),
        successful_goodput_per_s=float(goodput),
        direct_hard_violation_count=_count(
            selected.get("direct_hard_violation_count"), "violation_count_invalid"
        ),
        hidden_fallback_count=_count(
            selected.get("hidden_fallback_count"), "fallback_count_invalid"
        ),
    )
    if selected.get("runner_status") not in {"PASS", "SCIENTIFIC_OUTCOME_COMPLETE"}:
        raise _fail("runner_status_invalid")
    return selected


def _projection_from_block(
    artifact: Mapping[str, object], block_file_sha256: str
) -> dict[str, object]:
    try:
        result = verify_s6_block_result(artifact)
    except Exception:
        raise _fail("block_result_invalid") from None
    payload = result["payload"]
    selection = payload["selection_eligibility"]
    correctness = payload["correctness"]
    return _projection(
        {
            "cell": payload["cell"],
            "block_file_sha256": _sha(
                block_file_sha256, "block_file_sha256_invalid"
            ),
            "block_payload_sha256": result["payload_sha256"],
            "source_count": payload["workload"]["source_count"],
            "source_manifest_sha256": payload["workload"][
                "source_manifest_sha256"
            ],
            "execution_identity_sha256": payload[
                "execution_identity_sha256"
            ],
            "runner_status": payload["runner"]["status"],
            "successful_goodput_per_s": payload["metrics"][
                "successful_goodput_per_s"
            ],
            "p95_freshness_ns": payload["metrics"]["p95_freshness_ns"],
            "direct_hard_violation_count": correctness[
                "direct_hard_violation_count"
            ],
            "deterministic_correctness_gate": correctness[
                "deterministic_correctness_gate"
            ],
            "hidden_fallback_count": correctness["hidden_fallback_count"],
            "pstar_performance_eligible": selection[
                "pstar_performance_eligible"
            ],
            "mstar_qualified": selection["mstar_qualified"],
            "mstar_disqualification_reasons": sorted(
                selection["mstar_disqualification_reasons"]
            ),
        }
    )


def _validate_inventory(projections: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    if len(projections) != CELL_COUNT:
        raise _fail("block_inventory_incomplete")
    selected = [_projection(item) for item in projections]
    selected.sort(key=lambda item: int(item["cell"]["cell_index"]))
    if [item["cell"]["cell_index"] for item in selected] != list(range(CELL_COUNT)):
        raise _fail("block_inventory_duplicate_or_missing")
    workloads: dict[str, tuple[int, str]] = {}
    identities: dict[tuple[str, int], str] = {}
    for item in selected:
        cell = item["cell"]
        history = str(cell["history_id"])
        workload = (int(item["source_count"]), str(item["source_manifest_sha256"]))
        if history in workloads and workloads[history] != workload:
            raise _fail("history_workload_identity_mixed")
        workloads[history] = workload
        group = (str(cell["method"]), int(cell["configured_concurrency"]))
        identity = str(item["execution_identity_sha256"])
        if group in identities and identities[group] != identity:
            raise _fail("execution_identity_mixed")
        identities[group] = identity
    return selected


def _candidate(
    method: str, concurrency: int, blocks: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    if len(blocks) != len(DEVELOPMENT_HISTORIES):
        raise _fail("candidate_block_count_invalid")
    goodputs = [float(item["successful_goodput_per_s"]) for item in blocks]
    freshness = [item["p95_freshness_ns"] for item in blocks]
    if method == "P*":
        qualified = all(item["pstar_performance_eligible"] is True for item in blocks)
        reasons: list[str] = []
    else:
        qualified = all(item["mstar_qualified"] is True for item in blocks)
        reasons = sorted(
            {
                reason
                for item in blocks
                for reason in item["mstar_disqualification_reasons"]
            }
        )
    return {
        "concurrency": concurrency,
        "block_count": len(blocks),
        "median_successful_goodput_per_s": float(_median(goodputs)),
        "median_p95_freshness_ns": (
            int(_median([int(value) for value in freshness if value is not None]))
            if all(item is not None for item in freshness)
            else None
        ),
        "total_direct_hard_violations": sum(
            int(item["direct_hard_violation_count"]) for item in blocks
        ),
        "total_hidden_fallbacks": sum(
            int(item["hidden_fallback_count"]) for item in blocks
        ),
        "correctness_gate_fail_count": sum(
            item["deterministic_correctness_gate"] != "PASS" for item in blocks
        ),
        "treatment_failure_block_count": sum(
            item["runner_status"] == "SCIENTIFIC_OUTCOME_COMPLETE" for item in blocks
        ),
        "qualified": qualified,
        "disqualification_reasons": reasons,
    }


def _verify_candidate(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _CANDIDATE_FIELDS:
        raise _fail("candidate_invalid")
    selected = deepcopy(dict(value))
    concurrency = selected.get("concurrency")
    if concurrency not in CONCURRENCIES:
        raise _fail("candidate_invalid")
    _count(selected.get("block_count"), "candidate_invalid")
    for field in (
        "total_direct_hard_violations",
        "total_hidden_fallbacks",
        "correctness_gate_fail_count",
        "treatment_failure_block_count",
    ):
        _count(selected.get(field), "candidate_invalid")
    goodput = selected.get("median_successful_goodput_per_s")
    freshness = selected.get("median_p95_freshness_ns")
    if (
        isinstance(goodput, bool)
        or not isinstance(goodput, (int, float))
        or not math.isfinite(float(goodput))
        or float(goodput) < 0
        or (freshness is not None and _count(freshness, "candidate_invalid") < 0)
        or not isinstance(selected.get("qualified"), bool)
        or not isinstance(selected.get("disqualification_reasons"), list)
    ):
        raise _fail("candidate_invalid")
    return selected


def _select(candidates: Sequence[Mapping[str, object]]) -> int | None:
    eligible = [item for item in candidates if item["qualified"] is True]
    if not eligible:
        return None
    return int(
        max(
            eligible,
            key=lambda item: (
                float(item["median_successful_goodput_per_s"]),
                -int(item["concurrency"]),
            ),
        )["concurrency"]
    )


def _method_results(projections: Sequence[Mapping[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for method in METHODS:
        candidates = []
        for concurrency in CONCURRENCIES:
            blocks = [
                item
                for item in projections
                if item["cell"]["method"] == method
                and item["cell"]["configured_concurrency"] == concurrency
            ]
            candidates.append(_candidate(method, concurrency, blocks))
        selected = _select(candidates)
        result[method] = {
            "candidates": candidates,
            "qualified_concurrencies": [
                int(item["concurrency"])
                for item in candidates
                if item["qualified"] is True
            ],
            "selected_concurrency": selected,
            "selection_rule": "highest_median_successful_goodput_across_four_histories",
            "tie_break_rule": "exact_goodput_tie_select_smaller_concurrency",
        }
    return result


def build_s6_method_selection(
    *,
    matrix_freeze: Mapping[str, object],
    matrix_file_sha256: str,
    block_results: Sequence[Mapping[str, object]],
    block_file_sha256s: Mapping[str, str],
) -> dict[str, object]:
    if isinstance(block_results, (str, bytes)) or not isinstance(block_results, Sequence):
        raise _fail("block_results_invalid")
    try:
        freeze = verify_s6_matrix_freeze(matrix_freeze)
    except Exception:
        raise _fail("matrix_freeze_invalid") from None
    expected_run_ids = {str(cell["run_id"]) for cell in freeze["payload"]["cells"]}
    if not isinstance(block_file_sha256s, Mapping) or set(block_file_sha256s) != expected_run_ids:
        raise _fail("block_file_inventory_invalid")
    matrix = _matrix_binding(freeze, matrix_file_sha256)
    projections: list[dict[str, object]] = []
    for raw in block_results:
        try:
            result = verify_s6_block_result(raw)
        except Exception:
            raise _fail("block_result_invalid") from None
        payload = result["payload"]
        run_id = str(payload["cell"]["run_id"])
        if payload["matrix"] != matrix:
            raise _fail("block_matrix_binding_invalid")
        projections.append(
            _projection_from_block(result, str(block_file_sha256s.get(run_id, "")))
        )
    projections = _validate_inventory(projections)
    method_results = _method_results(projections)
    selected = {
        method: method_results[method]["selected_concurrency"] for method in METHODS
    }
    stop = selected["M*"] is None
    body: dict[str, object] = {
        "schema_version": SCHEMA,
        "stage": STAGE,
        "status": "METHOD_SELECTION_FROZEN",
        "verdict": "STOP_MSTAR_QUALIFIED_SET_EMPTY" if stop else "PASS",
        "stop_reason": "MSTAR_QUALIFIED_SET_EMPTY" if stop else None,
        "matrix": matrix,
        "block_count": CELL_COUNT,
        "block_projections": projections,
        "method_results": method_results,
        "selected_concurrency": selected,
        "authority": deepcopy(_AUTHORITY),
    }
    body["selection_sha256"] = payload_sha256(body)
    return verify_s6_method_selection_payload(body)


def verify_s6_method_selection_payload(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _PAYLOAD_FIELDS:
        raise _fail("selection_payload_shape_invalid")
    payload = deepcopy(dict(value))
    seal = payload.pop("selection_sha256", None)
    if (
        payload.get("schema_version") != SCHEMA
        or payload.get("stage") != STAGE
        or payload.get("status") != "METHOD_SELECTION_FROZEN"
        or seal != payload_sha256(payload)
        or payload.get("block_count") != CELL_COUNT
        or payload.get("authority") != _AUTHORITY
    ):
        raise _fail("selection_payload_identity_invalid")
    matrix = _verify_matrix(payload.get("matrix"))
    projections_raw = payload.get("block_projections")
    if isinstance(projections_raw, (str, bytes)) or not isinstance(projections_raw, Sequence):
        raise _fail("block_projections_invalid")
    projections = _validate_inventory(projections_raw)
    expected_methods = _method_results(projections)
    observed_methods = payload.get("method_results")
    if not isinstance(observed_methods, Mapping) or set(observed_methods) != set(METHODS):
        raise _fail("method_results_invalid")
    for method in METHODS:
        result = observed_methods.get(method)
        if not isinstance(result, Mapping) or set(result) != _METHOD_RESULT_FIELDS:
            raise _fail("method_results_invalid")
        candidates = result.get("candidates")
        if not isinstance(candidates, list):
            raise _fail("method_results_invalid")
        for candidate in candidates:
            _verify_candidate(candidate)
    if observed_methods != expected_methods:
        raise _fail("method_results_recomputation_invalid")
    selected = {method: expected_methods[method]["selected_concurrency"] for method in METHODS}
    stop = selected["M*"] is None
    if (
        payload.get("selected_concurrency") != selected
        or payload.get("verdict")
        != ("STOP_MSTAR_QUALIFIED_SET_EMPTY" if stop else "PASS")
        or payload.get("stop_reason")
        != ("MSTAR_QUALIFIED_SET_EMPTY" if stop else None)
    ):
        raise _fail("selection_verdict_invalid")
    payload.update(
        matrix=matrix,
        block_projections=projections,
        method_results=expected_methods,
        selected_concurrency=selected,
    )
    payload["selection_sha256"] = seal
    return payload


def verify_s6_method_selection(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise _fail("selection_envelope_shape_invalid")
    artifact = deepcopy(dict(value))
    payload = verify_s6_method_selection_payload(
        artifact.get("payload") if isinstance(artifact.get("payload"), Mapping) else {}
    )
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or _GIT_COMMIT.fullmatch(str(artifact.get("git_commit", ""))) is None
        or artifact.get("run_id") != RUN_ID
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise _fail("selection_envelope_invalid")
    artifact["payload"] = payload
    return artifact


def _write_exclusive(path: Path, value: Mapping[str, object]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("ascii")
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written == 0:
                raise OSError("short write while sealing S6 method selection")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(output.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def finalize_s6_method_selection(
    *, output_path: Path, payload: Mapping[str, object], git_commit: str
) -> dict[str, object]:
    selected = verify_s6_method_selection_payload(payload)
    artifact = verify_s6_method_selection(
        {
            "protocol_version": PROTOCOL_VERSION,
            "git_commit": str(git_commit),
            "run_id": RUN_ID,
            "status": "finalized",
            "payload": selected,
            "payload_sha256": payload_sha256(selected),
        }
    )
    _write_exclusive(Path(output_path), artifact)
    return artifact


__all__ = [
    "RUN_ID",
    "S6SelectionError",
    "build_s6_method_selection",
    "finalize_s6_method_selection",
    "verify_s6_method_selection",
    "verify_s6_method_selection_payload",
]
