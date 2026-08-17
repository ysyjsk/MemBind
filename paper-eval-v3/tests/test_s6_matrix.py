"""Offline RED/GREEN tests for canonical serial S6 block discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s6_matrix import S6MatrixError, inspect_s6_matrix_progress
from test_s6_selection import _blocks, _freeze


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _setup(
    tmp_path: Path,
) -> tuple[Path, str, Path, list[dict[str, object]]]:
    matrix_path = tmp_path / "S6_MATRIX_FREEZE.json"
    _write(matrix_path, _freeze())
    matrix_file_sha256 = sha256_file(matrix_path)
    runs = tmp_path / "runs"
    runs.mkdir()
    blocks, _hashes = _blocks()
    for block in blocks:
        block["payload"]["matrix"]["file_sha256"] = matrix_file_sha256
        block["payload"]["block_result_sha256"] = payload_sha256(
            {
                key: item
                for key, item in block["payload"].items()
                if key != "block_result_sha256"
            }
        )
        block["payload_sha256"] = payload_sha256(block["payload"])
    return matrix_path, matrix_file_sha256, runs, blocks


def test_empty_matrix_selects_exact_first_frozen_cell(tmp_path: Path) -> None:
    matrix, matrix_sha, runs, _blocks = _setup(tmp_path)

    progress = inspect_s6_matrix_progress(
        matrix_freeze_path=matrix,
        matrix_file_sha256=matrix_sha,
        runs_root=runs,
    )

    assert progress["status"] == "NEXT_BLOCK_READY"
    assert progress["finalized_block_count"] == 0
    assert progress["next_cell"]["cell_index"] == 0
    assert progress["next_cell"]["run_id"] == "s6-07741c45-pstar-c1-001"


def test_finalized_prefix_is_never_rerun_and_next_cell_is_serial(tmp_path: Path) -> None:
    matrix, matrix_sha, runs, blocks = _setup(tmp_path)
    for block in blocks[:2]:
        run_id = str(block["payload"]["cell"]["run_id"])
        _write(runs / run_id / "S6_BLOCK_RESULT.json", block)

    progress = inspect_s6_matrix_progress(
        matrix_freeze_path=matrix,
        matrix_file_sha256=matrix_sha,
        runs_root=runs,
    )

    assert progress["finalized_block_count"] == 2
    assert [row["status"] for row in progress["cells"][:3]] == [
        "FINALIZED",
        "FINALIZED",
        "NOT_STARTED",
    ]
    assert progress["next_cell"]["cell_index"] == 2


def test_any_started_unfinalized_canonical_root_stops_matrix(tmp_path: Path) -> None:
    matrix, matrix_sha, runs, blocks = _setup(tmp_path)
    first_run = str(blocks[0]["payload"]["cell"]["run_id"])
    (runs / first_run / "controller").mkdir(parents=True)
    _write(
        runs / first_run / "controller/checkpoint.json",
        {"status": "incomplete_non_mergeable"},
    )

    progress = inspect_s6_matrix_progress(
        matrix_freeze_path=matrix,
        matrix_file_sha256=matrix_sha,
        runs_root=runs,
    )

    assert progress["status"] == "STOP_ACTIVE_OR_INCOMPLETE_BLOCK"
    assert progress["active_or_incomplete_run_id"] == first_run
    assert progress["next_cell"] is None
    assert progress["cells"][0]["status"] == "STARTED_UNFINALIZED"


def test_corrupt_or_cell_mismatched_final_result_fails_closed(tmp_path: Path) -> None:
    matrix, matrix_sha, runs, blocks = _setup(tmp_path)
    first_run = str(blocks[0]["payload"]["cell"]["run_id"])
    corrupted = dict(blocks[0])
    corrupted["payload_sha256"] = "0" * 64
    _write(runs / first_run / "S6_BLOCK_RESULT.json", corrupted)

    with pytest.raises(S6MatrixError, match="canonical_block_result_invalid"):
        inspect_s6_matrix_progress(
            matrix_freeze_path=matrix,
            matrix_file_sha256=matrix_sha,
            runs_root=runs,
        )


def test_all_32_finalized_blocks_complete_matrix(tmp_path: Path) -> None:
    matrix, matrix_sha, runs, blocks = _setup(tmp_path)
    for block in blocks:
        run_id = str(block["payload"]["cell"]["run_id"])
        _write(runs / run_id / "S6_BLOCK_RESULT.json", block)
    (runs / "debug-unregistered").mkdir()

    progress = inspect_s6_matrix_progress(
        matrix_freeze_path=matrix,
        matrix_file_sha256=matrix_sha,
        runs_root=runs,
    )

    assert progress["status"] == "MATRIX_COMPLETE"
    assert progress["finalized_block_count"] == 32
    assert progress["next_cell"] is None
    assert progress["unexpected_run_entries"] == ["debug-unregistered"]


def test_discovery_rejects_caller_supplied_matrix_file_hash_drift(
    tmp_path: Path,
) -> None:
    matrix, _matrix_sha, runs, _blocks = _setup(tmp_path)

    with pytest.raises(S6MatrixError, match="matrix_file_identity_mismatch"):
        inspect_s6_matrix_progress(
            matrix_freeze_path=matrix,
            matrix_file_sha256="f" * 64,
            runs_root=runs,
        )
