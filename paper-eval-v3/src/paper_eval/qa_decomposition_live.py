"""Resumable Reader/Judge execution for the read-only QA diagnosis.

Each unit seals the Reader before invoking the Judge. A restart verifies and
reuses completed stages, so a model outage cannot resample an earlier answer.
The module does not construct Graphiti or issue database operations.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .artifacts import atomic_write_json, payload_sha256
from .qa_decomposition import (
    VariantSessions,
    build_final_artifacts,
    make_stage,
    verify_stage,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class QADecompositionUnit:
    """All immutable inputs for one history/Reader-context variant."""

    overlay_run_id: str
    source_run_id: str
    history_id: str
    namespace: str
    construction_result_sha256: str
    record: Mapping[str, Any]
    selection: VariantSessions


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"QA decomposition live {field} is invalid")
    return value


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"QA decomposition live {field} is invalid")
    return value


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_unit_binding(
    *,
    unit: QADecompositionUnit,
    reader_config_sha256: str,
    judge_config_sha256: str,
) -> dict[str, Any]:
    """Bind a model checkpoint without exposing namespace or session IDs."""

    for field in ("overlay_run_id", "source_run_id", "history_id", "namespace"):
        _text(getattr(unit, field), field=field)
    _sha(unit.construction_result_sha256, field="construction_result_sha256")
    _sha(reader_config_sha256, field="reader_config_sha256")
    _sha(judge_config_sha256, field="judge_config_sha256")
    selected = unit.selection.selected_session_ids
    if not selected or any(not isinstance(value, str) or not value for value in selected):
        raise ValueError("QA decomposition live selected sessions are invalid")
    return {
        "overlay_run_id": unit.overlay_run_id,
        "source_run_id": unit.source_run_id,
        "history_id": unit.history_id,
        "namespace_sha256": _text_sha256(unit.namespace),
        "construction_result_sha256": unit.construction_result_sha256,
        "variant": unit.selection.variant,
        "selected_session_ids_sha256": payload_sha256(list(selected)),
        "reader_config_sha256": reader_config_sha256,
        "judge_config_sha256": judge_config_sha256,
    }


def _read_object(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("QA decomposition private stage path is invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("QA decomposition private stage is unreadable") from None
    if not isinstance(value, dict):
        raise ValueError("QA decomposition private stage is invalid")
    return value


def _stage_or_none(
    path: Path, *, stage: str, binding: Mapping[str, Any]
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return verify_stage(_read_object(path), stage=stage, binding=binding)["result"]


def _reader_payload(result: object, *, expected_config: str) -> dict[str, Any]:
    answer = _text(getattr(result, "answer", None), field="Reader answer")
    prompt = _text(getattr(result, "prompt_for_test", None), field="Reader prompt")
    config = _sha(getattr(result, "config_sha256", None), field="Reader config")
    if config != expected_config:
        raise ValueError("QA decomposition live Reader config drift")
    prompt_tokens = getattr(result, "prompt_tokens", None)
    completion_tokens = getattr(result, "completion_tokens", None)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (prompt_tokens, completion_tokens)
    ):
        raise ValueError("QA decomposition live Reader token counts are invalid")
    return {
        "answer": answer,
        "prompt": prompt,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "model": _text(getattr(result, "model", None), field="Reader model"),
        "config_sha256": config,
    }


def _judge_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("QA decomposition live Judge result is invalid")
    result = dict(value)
    if (
        result.get("status") != "SUCCESS"
        or type(result.get("label")) is not bool
        or not isinstance(result.get("raw_output"), str)
        or not result["raw_output"]
    ):
        raise ValueError("QA decomposition live Judge result is invalid")
    return result


async def execute_qa_decomposition_unit(
    *,
    unit: QADecompositionUnit,
    reader: Any,
    judge: Any,
    unit_root: Path,
) -> dict[str, Any]:
    """Run or restore one Reader/Judge pair and return its public artifact."""

    reader_hash = _sha(
        getattr(reader, "config_sha256", None), field="reader_config_sha256"
    )
    judge_hash = _sha(
        getattr(judge, "config_sha256", None), field="judge_config_sha256"
    )
    binding = build_unit_binding(
        unit=unit,
        reader_config_sha256=reader_hash,
        judge_config_sha256=judge_hash,
    )
    root = Path(unit_root)
    reader_path = root / "runtime/private/reader_stage.json"
    judge_path = root / "runtime/private/judge_stage.json"

    reader_result = _stage_or_none(reader_path, stage="reader", binding=binding)
    if reader_result is None:
        generated = await reader.answer(
            unit.selection.sessions,
            question_date=_text(unit.record.get("question_date"), field="question_date"),
            question=_text(unit.record.get("question"), field="question"),
        )
        reader_result = _reader_payload(generated, expected_config=reader_hash)
        atomic_write_json(
            reader_path,
            make_stage(stage="reader", binding=binding, result=reader_result),
        )

    judge_result = _stage_or_none(judge_path, stage="judge", binding=binding)
    if judge_result is None:
        evaluated = await judge.evaluate(
            hypothesis=_text(reader_result.get("answer"), field="Reader answer"),
            inputs=SimpleNamespace(
                run_id=unit.overlay_run_id,
                history_id=unit.history_id,
                question_type=_text(
                    unit.record.get("question_type"), field="question_type"
                ),
                question=_text(unit.record.get("question"), field="question"),
                reference_answer=_text(
                    str(unit.record.get("answer", "")), field="reference_answer"
                ),
            ),
        )
        judge_result = _judge_payload(evaluated)
        atomic_write_json(
            judge_path,
            make_stage(stage="judge", binding=binding, result=judge_result),
        )

    private, public = build_final_artifacts(
        binding=binding,
        question=_text(unit.record.get("question"), field="question"),
        reference_answer=_text(
            str(unit.record.get("answer", "")), field="reference_answer"
        ),
        selected_session_ids=unit.selection.selected_session_ids,
        reader_result=reader_result,
        judge_result=judge_result,
    )
    atomic_write_json(root / "private_bundle.json", private)
    atomic_write_json(root / "public.json", public)
    return public


__all__ = [
    "QADecompositionUnit",
    "build_unit_binding",
    "execute_qa_decomposition_unit",
]
