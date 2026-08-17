"""Frozen offline contract for the S6 development-only concurrency sweep.

S6 deliberately owns only matrix identity and metric-shape validation.  It does
not construct Graphiti, contact a model service, mutate Neo4j, select a method,
or grant live authority.  The live runner is a later, separately qualified
layer that must bind each cell to this exact matrix.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from numbers import Real
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import payload_sha256


SCHEMA = "membind.paper-eval-v3.s6-calibration-contract.v1"
MATRIX_FREEZE_RUN_ID = "s6-calibration-matrix-freeze-20260816-001"
DEVELOPMENT_HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
DEVELOPMENT_HISTORIES_PAYLOAD_SHA256 = payload_sha256(list(DEVELOPMENT_HISTORIES))
METHODS = ("P*", "M*")
CONCURRENCIES = (1, 2, 4, 8)
CELL_COUNT = len(DEVELOPMENT_HISTORIES) * len(METHODS) * len(CONCURRENCIES)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_BINDING_FIELDS = {
    "s6_development_histories_payload_sha256",
    "parent_protocol_sha256",
    "s5_pstar_result_file_sha256",
    "s5_pstar_result_payload_sha256",
    "s5_mstar_result_file_sha256",
    "s5_mstar_result_payload_sha256",
}
_MATRIX_FIELDS = {
    "schema_version",
    "stage",
    "status",
    "histories",
    "methods",
    "concurrencies",
    "cell_count",
    "cells",
    "input_bindings",
    "selection_rule",
    "m_qualified_rule",
    "tie_break_rule",
    "stop_rule",
    "matrix_sha256",
}
_CELL_FIELDS = {
    "cell_index",
    "history_id",
    "data_role",
    "method",
    "configured_concurrency",
    "run_id",
    "namespace",
    "attempt_ordinal",
    "status",
}
_OUTCOME_FIELDS = {
    "source_sequence",
    "status",
    "arrival_timestamp_ns",
    "service_start_timestamp_ns",
    "publication_timestamp_ns",
    "terminal_timestamp_ns",
}
_OUTCOME_STATUSES = {"PUBLISHED", "FAILED", "CENSORED"}


class S6CalibrationContractError(ValueError):
    """The frozen S6 matrix or a block metric projection is invalid."""


def _fail(code: str) -> S6CalibrationContractError:
    return S6CalibrationContractError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _fail(code)
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _validate_bindings(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _BINDING_FIELDS:
        raise _fail("input_bindings_shape_invalid")
    result = {str(key): str(item) for key, item in value.items()}
    for key, item in result.items():
        _sha(item, f"{key}_invalid")
    if (
        result["s6_development_histories_payload_sha256"]
        != DEVELOPMENT_HISTORIES_PAYLOAD_SHA256
    ):
        raise _fail("s6_development_histories_payload_binding_invalid")
    return result


def _cell_run_id(history_id: str, method: str, concurrency: int) -> str:
    method_slug = "pstar" if method == "P*" else "mstar"
    return f"s6-{history_id}-{method_slug}-c{concurrency}-001"


def _expected_cells() -> list[dict[str, object]]:
    cells: list[dict[str, object]] = []
    index = 0
    # Keep methods adjacent within each history/concurrency pair.  This makes
    # the execution ledger easy to scan while preserving the preregistered
    # history and concurrency order.
    for history_id in DEVELOPMENT_HISTORIES:
        for concurrency in CONCURRENCIES:
            for method in METHODS:
                run_id = _cell_run_id(history_id, method, concurrency)
                cells.append(
                    {
                        "cell_index": index,
                        "history_id": history_id,
                        "data_role": "DEVELOPMENT_EXPOSED",
                        "method": method,
                        "configured_concurrency": concurrency,
                        "run_id": run_id,
                        "namespace": f"pev3-{run_id}",
                        "attempt_ordinal": 1,
                        "status": "NOT_STARTED",
                    }
                )
                index += 1
    return cells


def verify_s6_cell_identity(value: Mapping[str, object]) -> dict[str, object]:
    """Return one exact frozen cell and reject any detached identity drift."""

    if not isinstance(value, Mapping):
        raise _fail("cell_identity_invalid")
    cell = deepcopy(dict(value))
    index = cell.get("cell_index")
    expected = _expected_cells()
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or index < 0
        or index >= len(expected)
        or cell != expected[index]
    ):
        raise _fail("cell_identity_invalid")
    return cell


def build_s6_matrix(*, input_bindings: Mapping[str, object]) -> dict[str, object]:
    """Build the immutable 32-cell S6 matrix from hash-only prerequisites."""

    bindings = _validate_bindings(input_bindings)
    payload: dict[str, object] = {
        "schema_version": SCHEMA,
        "stage": "S6_DEVELOPMENT_ONLY_CONCURRENCY_CALIBRATION",
        "status": "FROZEN_DEVELOPMENT_CALIBRATION_MATRIX",
        "histories": list(DEVELOPMENT_HISTORIES),
        "methods": list(METHODS),
        "concurrencies": list(CONCURRENCIES),
        "cell_count": CELL_COUNT,
        "cells": _expected_cells(),
        "input_bindings": bindings,
        "selection_rule": "highest_median_successful_goodput_per_method",
        "m_qualified_rule": (
            "zero_direct_hard_violation_and_correctness_gate_pass_and_zero_hidden_fallback"
        ),
        "tie_break_rule": "exact_goodput_tie_select_smaller_concurrency",
        "stop_rule": "STOP_IF_MSTAR_QUALIFIED_SET_EMPTY",
    }
    payload["matrix_sha256"] = payload_sha256(payload)
    return verify_s6_matrix(payload)


def verify_s6_matrix(value: Mapping[str, object]) -> dict[str, object]:
    """Recompute every S6 inventory field and reject any matrix drift."""

    if not isinstance(value, Mapping):
        raise _fail("matrix_not_mapping")
    matrix = deepcopy(dict(value))
    if set(matrix) != _MATRIX_FIELDS:
        raise _fail("matrix_shape_invalid")
    if (
        matrix.get("schema_version") != SCHEMA
        or matrix.get("stage") != "S6_DEVELOPMENT_ONLY_CONCURRENCY_CALIBRATION"
        or matrix.get("status") != "FROZEN_DEVELOPMENT_CALIBRATION_MATRIX"
        or matrix.get("histories") != list(DEVELOPMENT_HISTORIES)
        or matrix.get("methods") != list(METHODS)
        or matrix.get("concurrencies") != list(CONCURRENCIES)
        or matrix.get("cell_count") != CELL_COUNT
        or matrix.get("selection_rule")
        != "highest_median_successful_goodput_per_method"
        or matrix.get("m_qualified_rule")
        != "zero_direct_hard_violation_and_correctness_gate_pass_and_zero_hidden_fallback"
        or matrix.get("tie_break_rule")
        != "exact_goodput_tie_select_smaller_concurrency"
        or matrix.get("stop_rule") != "STOP_IF_MSTAR_QUALIFIED_SET_EMPTY"
    ):
        raise _fail("matrix_binding_invalid")
    bindings = _validate_bindings(matrix.get("input_bindings"))
    cells = matrix.get("cells")
    if not isinstance(cells, list) or cells != _expected_cells():
        raise _fail("cell_inventory_invalid")
    if matrix.get("matrix_sha256") != payload_sha256(
        {key: item for key, item in matrix.items() if key != "matrix_sha256"}
    ):
        raise _fail("matrix_hash_invalid")
    matrix["input_bindings"] = bindings
    return matrix


def deterministic_nearest_rank_p95(values: Sequence[Real]) -> Real:
    """Return P95 using the preregistered nearest-rank ceil(0.95*N) rule."""

    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or not values:
        raise _fail("p95_samples_invalid")
    selected: list[Real] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise _fail("p95_sample_invalid")
        if not math.isfinite(float(value)) or value < 0:
            raise _fail("p95_sample_invalid")
        selected.append(value)
    selected.sort()
    rank = max(1, math.ceil(0.95 * len(selected)))
    return selected[rank - 1]


def _validate_outcome(value: object, expected_sequence: int) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _OUTCOME_FIELDS:
        raise _fail("source_outcome_shape_invalid")
    outcome = deepcopy(dict(value))
    if outcome.get("source_sequence") != expected_sequence:
        raise _fail("source_sequence_invalid")
    status = outcome.get("status")
    if status not in _OUTCOME_STATUSES:
        raise _fail("source_status_invalid")
    arrival = _nonnegative_int(outcome.get("arrival_timestamp_ns"), "arrival_invalid")
    service_value = outcome.get("service_start_timestamp_ns")
    terminal_value = outcome.get("terminal_timestamp_ns")
    if status == "CENSORED" and service_value is None and terminal_value is None:
        service = None
        terminal = None
    else:
        service = _nonnegative_int(service_value, "service_start_invalid")
        terminal = _nonnegative_int(terminal_value, "terminal_invalid")
        if service < arrival or terminal < service:
            raise _fail("source_time_order_invalid")
    publication = outcome.get("publication_timestamp_ns")
    if status == "PUBLISHED":
        if service is None or terminal is None:
            raise _fail("published_source_not_started")
        publication = _nonnegative_int(publication, "publication_invalid")
        if publication < service:
            raise _fail("publication_time_order_invalid")
    elif publication is not None:
        raise _fail("nonpublished_source_has_publication")
    outcome.update(
        arrival_timestamp_ns=arrival,
        service_start_timestamp_ns=service,
        publication_timestamp_ns=publication,
        terminal_timestamp_ns=terminal,
    )
    return outcome


def compute_s6_block_metrics(
    *, expected_source_count: int, source_outcomes: Sequence[Mapping[str, object]]
) -> dict[str, object]:
    """Compute only metrics that can be derived from a complete block ledger."""

    count = _positive_int(expected_source_count, "expected_source_count_invalid")
    if isinstance(source_outcomes, (str, bytes)) or not isinstance(
        source_outcomes, Sequence
    ):
        raise _fail("source_outcomes_invalid")
    if len(source_outcomes) != count:
        raise _fail("source_outcomes_incomplete")
    outcomes = [
        _validate_outcome(item, expected_sequence=index)
        for index, item in enumerate(source_outcomes)
    ]
    published = [item for item in outcomes if item["status"] == "PUBLISHED"]
    started = [
        item
        for item in outcomes
        if item["service_start_timestamp_ns"] is not None
        and item["terminal_timestamp_ns"] is not None
    ]
    if not started:
        raise _fail("no_started_source")
    finished_at = [
        max(
            int(item["terminal_timestamp_ns"]),
            int(item["publication_timestamp_ns"])
            if item["publication_timestamp_ns"] is not None
            else int(item["terminal_timestamp_ns"]),
        )
        for item in started
    ]
    first_service = min(int(item["service_start_timestamp_ns"]) for item in started)
    last_terminal_or_publication = max(finished_at)
    if last_terminal_or_publication <= first_service:
        raise _fail("makespan_invalid")
    makespan_ns = last_terminal_or_publication - first_service
    freshness = [
        int(item["publication_timestamp_ns"]) - int(item["arrival_timestamp_ns"])
        for item in published
    ]
    if any(value < 0 for value in freshness):
        raise _fail("freshness_invalid")
    p95 = deterministic_nearest_rank_p95(freshness) if freshness else None
    return {
        "expected_source_count": count,
        "published_source_count": len(published),
        "failed_source_count": sum(item["status"] == "FAILED" for item in outcomes),
        "censored_source_count": sum(
            item["status"] == "CENSORED" for item in outcomes
        ),
        "makespan_ns": makespan_ns,
        "successful_goodput_per_s": len(published) / (makespan_ns / 1_000_000_000),
        "freshness_ns": freshness,
        "p95_freshness_ns": p95,
        "p95_quantile_convention": "NEAREST_RANK_CEIL_0_95_N",
    }


def _write_exclusive(path: Path, artifact: Mapping[str, object]) -> str:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = (
        json.dumps(artifact, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    try:
        descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    except FileExistsError:
        raise _fail("matrix_output_exists") from None
    try:
        offset = 0
        while offset < len(serialized):
            written = os.write(descriptor, serialized[offset:])
            if written == 0:
                raise OSError("short write while freezing S6 matrix")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(output.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return hashlib.sha256(serialized).hexdigest()


def verify_s6_matrix_freeze(value: Mapping[str, object]) -> dict[str, object]:
    """Verify the finalized envelope and its exact pure matrix payload."""

    if not isinstance(value, Mapping) or set(value) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise _fail("freeze_envelope_shape_invalid")
    artifact = deepcopy(dict(value))
    payload = artifact.get("payload")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or not isinstance(artifact.get("git_commit"), str)
        or _GIT_COMMIT.fullmatch(str(artifact.get("git_commit"))) is None
        or artifact.get("run_id") != MATRIX_FREEZE_RUN_ID
        or artifact.get("status") != "finalized"
        or not isinstance(payload, Mapping)
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise _fail("freeze_envelope_invalid")
    artifact["payload"] = verify_s6_matrix(payload)
    return artifact


def finalize_s6_matrix_freeze(
    *, output_path: Path, matrix: Mapping[str, object], git_commit: str
) -> dict[str, object]:
    """Seal and exclusively persist the finalized S6 matrix artifact."""

    payload = verify_s6_matrix(matrix)
    artifact = verify_s6_matrix_freeze(
        {
            "protocol_version": PROTOCOL_VERSION,
            "git_commit": str(git_commit),
            "run_id": MATRIX_FREEZE_RUN_ID,
            "status": "finalized",
            "payload": payload,
            "payload_sha256": payload_sha256(payload),
        }
    )
    file_sha256 = _write_exclusive(Path(output_path), artifact)
    return {
        "artifact": artifact,
        "matrix_payload_sha256": artifact["payload_sha256"],
        "matrix_file_sha256": file_sha256,
    }


__all__ = [
    "CELL_COUNT",
    "CONCURRENCIES",
    "DEVELOPMENT_HISTORIES",
    "DEVELOPMENT_HISTORIES_PAYLOAD_SHA256",
    "MATRIX_FREEZE_RUN_ID",
    "METHODS",
    "S6CalibrationContractError",
    "build_s6_matrix",
    "compute_s6_block_metrics",
    "deterministic_nearest_rank_p95",
    "finalize_s6_matrix_freeze",
    "verify_s6_matrix",
    "verify_s6_cell_identity",
    "verify_s6_matrix_freeze",
]
