"""Read-only canonical discovery for serial S6 matrix execution."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .artifacts import sha256_file
from .s6_block_result import verify_s6_block_result
from .s6_calibration_contract import verify_s6_matrix_freeze


class S6MatrixError(ValueError):
    """The frozen matrix or its canonical run inventory is not serially valid."""


def _fail(code: str) -> S6MatrixError:
    return S6MatrixError(code)


def _load(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def inspect_s6_matrix_progress(
    *, matrix_freeze_path: Path, matrix_file_sha256: str, runs_root: Path
) -> dict[str, object]:
    """Return the next frozen cell, or stop on any unfinalized canonical root."""

    try:
        freeze = verify_s6_matrix_freeze(
            _load(matrix_freeze_path, "matrix_freeze_invalid")
        )
    except Exception:
        raise _fail("matrix_freeze_invalid") from None
    if sha256_file(Path(matrix_freeze_path)) != matrix_file_sha256:
        raise _fail("matrix_file_identity_mismatch")
    expected_matrix = {
        "file_sha256": matrix_file_sha256,
        "payload_sha256": freeze["payload_sha256"],
        "matrix_sha256": freeze["payload"]["matrix_sha256"],
    }
    root = Path(runs_root)
    if not root.is_dir():
        raise _fail("runs_root_invalid")
    expected_cells = freeze["payload"]["cells"]
    expected_names = {str(cell["run_id"]) for cell in expected_cells}
    unexpected = sorted(
        item.name for item in root.iterdir() if item.name not in expected_names
    )
    cells: list[dict[str, object]] = []
    finalized_indices: list[int] = []
    started_indices: list[int] = []
    for cell in expected_cells:
        index = int(cell["cell_index"])
        run_id = str(cell["run_id"])
        run_root = root / run_id
        result_path = run_root / "S6_BLOCK_RESULT.json"
        row: dict[str, object] = {
            "cell_index": index,
            "run_id": run_id,
            "status": "NOT_STARTED",
            "result_file_sha256": None,
            "result_payload_sha256": None,
        }
        if result_path.is_file():
            try:
                result = verify_s6_block_result(
                    _load(result_path, "canonical_block_result_invalid")
                )
            except Exception:
                raise _fail("canonical_block_result_invalid") from None
            if (
                result["payload"]["cell"] != cell
                or result["payload"]["matrix"] != expected_matrix
            ):
                raise _fail("canonical_block_result_binding_invalid")
            row.update(
                status="FINALIZED",
                result_file_sha256=sha256_file(result_path),
                result_payload_sha256=result["payload_sha256"],
            )
            finalized_indices.append(index)
        elif run_root.exists():
            if not run_root.is_dir():
                raise _fail("canonical_run_root_invalid")
            row["status"] = "STARTED_UNFINALIZED"
            started_indices.append(index)
        cells.append(row)
    if finalized_indices != list(range(len(finalized_indices))):
        raise _fail("finalized_block_order_invalid")
    if len(started_indices) > 1 or (
        started_indices and started_indices[0] != len(finalized_indices)
    ):
        raise _fail("multiple_or_out_of_order_unfinalized_blocks")
    finalized_count = len(finalized_indices)
    if started_indices:
        status = "STOP_ACTIVE_OR_INCOMPLETE_BLOCK"
        next_cell = None
        active = str(expected_cells[started_indices[0]]["run_id"])
    elif finalized_count == len(expected_cells):
        status = "MATRIX_COMPLETE"
        next_cell = None
        active = None
    else:
        status = "NEXT_BLOCK_READY"
        next_cell = deepcopy(expected_cells[finalized_count])
        active = None
    return {
        "status": status,
        "matrix_file_sha256": matrix_file_sha256,
        "matrix_payload_sha256": freeze["payload_sha256"],
        "matrix_sha256": freeze["payload"]["matrix_sha256"],
        "cell_count": len(expected_cells),
        "finalized_block_count": finalized_count,
        "active_or_incomplete_run_id": active,
        "next_cell": next_cell,
        "cells": cells,
        "unexpected_run_entries": unexpected,
    }


__all__ = ["S6MatrixError", "inspect_s6_matrix_progress"]
