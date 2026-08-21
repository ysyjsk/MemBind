"""L4 driver deriving its complete read-only namespace inventory from L3 seals."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .dataset import EXPECTED_EPISODE_COUNTS, load_and_validate_qa_inventory
from .formal_run_seal import verify_formal_run_seal
from .production_qa import ProductionQADependencies, execute_production_qa
from .qa_lane import NamespaceSeal


class QAStageError(ValueError):
    """Formal namespace derivation or L4 read-only evidence failed closed."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise QAStageError("QA_STAGE_ARTIFACT_UNREADABLE") from None


def _read_graph(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise QAStageError("QA_STAGE_CANONICAL_GRAPH_INVALID")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise QAStageError("QA_STAGE_CANONICAL_GRAPH_INVALID") from None
    if not isinstance(value, dict):
        raise QAStageError("QA_STAGE_CANONICAL_GRAPH_INVALID")
    return value


def _namespace_seals(root: Path, formal: Mapping[str, Any]) -> tuple[NamespaceSeal, ...]:
    selected = formal.get("selected_attempts")
    if (
        formal.get("verified") is not True
        or formal.get("valid_construction_blocks") != 8
        or formal.get("formal_construction_calls") != 8
        or not isinstance(selected, list)
        or len(selected) != 8
    ):
        raise QAStageError("FORMAL_RUN_NOT_VERIFIED")
    seals: list[NamespaceSeal] = []
    for expected_ordinal, row in enumerate(selected, start=1):
        if not isinstance(row, Mapping) or row.get("ordinal") != expected_ordinal:
            raise QAStageError("QA_STAGE_FORMAL_SELECTION_INVALID")
        block_id = row.get("block_id")
        attempt_id = row.get("attempt_id")
        if not all(isinstance(value, str) and value for value in (block_id, attempt_id)):
            raise QAStageError("QA_STAGE_FORMAL_SELECTION_INVALID")
        graph = _read_graph(
            root / "blocks" / str(block_id) / str(attempt_id) / "canonical_graph.json"
        )
        canonical_hash = _hash(graph)
        recorded = row.get("canonical_graph_hash")
        if recorded is not None and recorded != canonical_hash:
            raise QAStageError("QA_STAGE_CANONICAL_GRAPH_HASH_MISMATCH")
        try:
            seal = NamespaceSeal(
                method=str(row["method"]),
                history_id=str(row["history_id"]),
                namespace=str(row["namespace"]),
                canonical_hash=canonical_hash,
                construction_call_ordinal=expected_ordinal,
            )
        except (KeyError, TypeError, ValueError):
            raise QAStageError("QA_STAGE_FORMAL_SELECTION_INVALID") from None
        seals.append(seal)
    return tuple(seals)


def _write_new(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    selected = dict(value)
    selected["payload_sha256"] = _hash(selected)
    payload = json.dumps(
        selected, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise QAStageError("QA_STAGE_ARTIFACT_ALREADY_EXISTS") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return selected


async def execute_qa_stage(
    *,
    repository_root: Path,
    run_root: Path,
    dependencies: ProductionQADependencies | object,
    formal_verifier: Callable[[Path], Mapping[str, Any]] = verify_formal_run_seal,
    qa_executor: Callable[..., Any] = execute_production_qa,
    inventory_loader: Callable[[Path], Mapping[str, Any]] = load_and_validate_qa_inventory,
) -> dict[str, Any]:
    root = run_root.resolve()
    formal = dict(formal_verifier(root))
    seals = _namespace_seals(root, formal)
    inventory = inventory_loader(repository_root)
    questions = inventory.get("questions")
    if not isinstance(questions, list) or len(questions) != 16:
        raise QAStageError("QA_STAGE_INVENTORY_INVALID")
    output_path = root / "qa/qa_rows.jsonl"
    rows = await qa_executor(
        seals=seals,
        questions=questions,
        expected_histories=tuple(EXPECTED_EPISODE_COUNTS),
        construction_calls=8,
        output_path=output_path,
        dependencies=dependencies,
    )
    if not isinstance(rows, list) or len(rows) != 32:
        raise QAStageError("QA_STAGE_ROW_COVERAGE_INVALID")
    writes = sum(int(row.get("graph_write_attempts", -1)) for row in rows)
    constructions = sum(int(row.get("construction_calls", -1)) for row in rows)
    if writes != 0 or constructions != 0 or any(
        row.get("graph_hash_before") != row.get("graph_hash_after") for row in rows
    ):
        raise QAStageError("QA_STAGE_READ_ONLY_GATE_FAILED")
    return _write_new(
        root / "qa/read_only_evidence.json",
        {
            "schema_version": "membind.saturated-fixed-work.qa-read-only-evidence.v1",
            "status": "PASS",
            "qa_rows": len(rows),
            "qa_graph_write_attempts": writes,
            "qa_extra_construction_calls": constructions,
            "formal_run_seal_payload_sha256": formal.get("payload_sha256"),
            "namespace_count": len(seals),
            "qa_rows_file_sha256": _file_hash(output_path),
        },
    )


__all__ = ["QAStageError", "execute_qa_stage"]
