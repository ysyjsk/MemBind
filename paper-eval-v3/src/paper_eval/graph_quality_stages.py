"""Private, attempt-resumable checkpoints for graph-quality model outputs.

Reader and Judge generations are nondeterministic external work.  A successful
stage is sealed before the next stage starts and is reused by later attempts
only when every construction, retrieval, runtime, and model identity matches.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .artifacts import atomic_write_json, payload_sha256
from .temporal_fact_reader import TemporalFactReaderResult


READER_STAGE_SCHEMA = "membind.paper-eval-v3.graph-quality-reader-stage.v2"
JUDGE_STAGE_SCHEMA = "membind.paper-eval-v3.graph-quality-judge-stage.v2"

_ATTEMPT = re.compile(r"^attempt-([0-9]{3})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_READER_BINDING_FIELDS = {
    "overlay_run_id",
    "method",
    "history_id",
    "namespace_sha256",
    "construction_result_sha256",
    "runtime_identity_sha256",
    "retrieval_config_sha256",
    "evidence_sha256",
    "reader_config_sha256",
    "question_sha256",
    "question_date_sha256",
    "reader_prompt_sha256",
}
_JUDGE_BINDING_FIELDS = {
    *_READER_BINDING_FIELDS,
    "reader_stage_sha256",
    "judge_config_sha256",
    "question_type_sha256",
    "reference_answer_sha256",
    "reader_answer_sha256",
    "judge_prompt_sha256",
}
_HASH_FIELDS = {
    "namespace_sha256",
    "construction_result_sha256",
    "runtime_identity_sha256",
    "retrieval_config_sha256",
    "evidence_sha256",
    "reader_config_sha256",
    "question_sha256",
    "question_date_sha256",
    "reader_prompt_sha256",
    "reader_stage_sha256",
    "judge_config_sha256",
    "question_type_sha256",
    "reference_answer_sha256",
    "reader_answer_sha256",
    "judge_prompt_sha256",
}


class GraphQualityStageError(ValueError):
    """A private stage checkpoint is unreadable, inconsistent, or stale."""


def _read_object(path: Path) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise GraphQualityStageError("private stage has a duplicate field")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=no_duplicates,
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise GraphQualityStageError("private stage is unreadable") from None
    if not isinstance(value, dict):
        raise GraphQualityStageError("private stage is invalid")
    return value


def _binding(
    value: Mapping[str, Any], *, expected_fields: set[str]
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise GraphQualityStageError("private stage binding is invalid")
    result = dict(value)
    for field in ("overlay_run_id", "method", "history_id"):
        if not isinstance(result[field], str) or not result[field]:
            raise GraphQualityStageError("private stage binding is invalid")
    for field in expected_fields.intersection(_HASH_FIELDS):
        if (
            not isinstance(result[field], str)
            or _SHA256.fullmatch(result[field]) is None
        ):
            raise GraphQualityStageError("private stage binding is invalid")
    return result


def _verify_envelope(
    value: Mapping[str, Any],
    *,
    schema: str,
    expected_binding: Mapping[str, Any],
    binding_fields: set[str],
) -> dict[str, Any]:
    stage = dict(value)
    if stage.get("schema_version") != schema:
        raise GraphQualityStageError("private stage schema mismatch")
    observed_hash = stage.get("payload_sha256")
    if observed_hash != payload_sha256(
        {key: child for key, child in stage.items() if key != "payload_sha256"}
    ):
        raise GraphQualityStageError("private stage hash mismatch")
    observed_binding = _binding(
        stage.get("binding", {}), expected_fields=binding_fields
    )
    expected = _binding(expected_binding, expected_fields=binding_fields)
    if observed_binding != expected:
        raise GraphQualityStageError("private stage identity drift")
    return stage


def _reader_result(value: object) -> TemporalFactReaderResult:
    if not isinstance(value, Mapping) or set(value) != {
        "answer",
        "prompt_for_test",
        "prompt_tokens",
        "completion_tokens",
        "finish_reason",
        "model",
        "config_sha256",
    }:
        raise GraphQualityStageError("Reader stage result is invalid")
    try:
        result = TemporalFactReaderResult(**dict(value))
    except (TypeError, ValueError):
        raise GraphQualityStageError("Reader stage result is invalid") from None
    if (
        not result.answer
        or not result.prompt_for_test
        or result.finish_reason != "stop"
        or result.prompt_tokens < 0
        or result.completion_tokens < 0
        or _SHA256.fullmatch(result.config_sha256) is None
    ):
        raise GraphQualityStageError("Reader stage result is invalid")
    return result


def _judge_result(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GraphQualityStageError("Judge stage result is invalid")
    result = dict(value)
    raw_output = result.get("raw_output")
    output_sha = result.get("output_sha256")
    prompt_sha = result.get("prompt_sha256")
    if (
        not isinstance(raw_output, str)
        or not raw_output
        or not isinstance(output_sha, str)
        or output_sha
        != hashlib.sha256(raw_output.encode("utf-8")).hexdigest()
        or not isinstance(prompt_sha, str)
        or _SHA256.fullmatch(prompt_sha) is None
    ):
        raise GraphQualityStageError("Judge stage raw output binding is invalid")
    if result.get("status") not in {"SUCCESS", "INVALID_OUTPUT"}:
        raise GraphQualityStageError("Judge stage result is invalid")
    if type(result.get("label")) is not bool:
        raise GraphQualityStageError("Judge stage result is invalid")
    return result


class GraphQualityStageStore:
    """Seal private Reader/Judge stages and recover them across attempts."""

    def __init__(self, attempt_root: Path) -> None:
        self.attempt_root = Path(attempt_root)
        match = _ATTEMPT.fullmatch(self.attempt_root.name)
        if match is None or int(match.group(1)) < 1:
            raise GraphQualityStageError("private stage attempt root is invalid")
        self._attempt_ordinal = int(match.group(1))

    @staticmethod
    def _stage_path(root: Path, filename: str) -> Path:
        return root / "runtime" / "private" / filename

    def _candidate_paths(self, filename: str) -> list[Path]:
        candidates: list[Path] = []
        for ordinal in range(1, self._attempt_ordinal + 1):
            root = self.attempt_root.parent / f"attempt-{ordinal:03d}"
            path = self._stage_path(root, filename)
            if path.exists():
                if not path.is_file() or path.is_symlink():
                    raise GraphQualityStageError("private stage path is invalid")
                candidates.append(path)
        return candidates

    def _load(
        self,
        *,
        filename: str,
        schema: str,
        expected_binding: Mapping[str, Any],
        binding_fields: set[str],
    ) -> tuple[dict[str, Any], str] | None:
        candidates = self._candidate_paths(filename)
        if not candidates:
            return None
        verified = [
            _verify_envelope(
                _read_object(path),
                schema=schema,
                expected_binding=expected_binding,
                binding_fields=binding_fields,
            )
            for path in candidates
        ]
        hashes = {str(value["payload_sha256"]) for value in verified}
        if len(hashes) != 1:
            raise GraphQualityStageError("private stage output drift")
        return verified[-1], next(iter(hashes))

    def load_reader(
        self, binding: Mapping[str, Any]
    ) -> tuple[TemporalFactReaderResult, str] | None:
        loaded = self._load(
            filename="reader_stage.json",
            schema=READER_STAGE_SCHEMA,
            expected_binding=binding,
            binding_fields=_READER_BINDING_FIELDS,
        )
        if loaded is None:
            return None
        stage, stage_sha = loaded
        result = _reader_result(stage.get("result"))
        if hashlib.sha256(result.prompt_for_test.encode("utf-8")).hexdigest() != (
            binding["reader_prompt_sha256"]
        ):
            raise GraphQualityStageError("Reader stage prompt binding is invalid")
        return result, stage_sha

    def persist_reader(
        self,
        binding: Mapping[str, Any],
        result: TemporalFactReaderResult,
    ) -> str:
        normalized_binding = _binding(
            binding, expected_fields=_READER_BINDING_FIELDS
        )
        normalized_result = _reader_result(asdict(result))
        if hashlib.sha256(
            normalized_result.prompt_for_test.encode("utf-8")
        ).hexdigest() != normalized_binding["reader_prompt_sha256"]:
            raise GraphQualityStageError("Reader stage prompt binding is invalid")
        body: dict[str, Any] = {
            "schema_version": READER_STAGE_SCHEMA,
            "binding": normalized_binding,
            "result": asdict(normalized_result),
        }
        body["payload_sha256"] = payload_sha256(body)
        path = self._stage_path(self.attempt_root, "reader_stage.json")
        if path.exists():
            observed = _verify_envelope(
                _read_object(path),
                schema=READER_STAGE_SCHEMA,
                expected_binding=normalized_binding,
                binding_fields=_READER_BINDING_FIELDS,
            )
            if observed != body:
                raise GraphQualityStageError("existing Reader stage drift")
        else:
            atomic_write_json(path, body)
        return str(body["payload_sha256"])

    def load_judge(
        self, binding: Mapping[str, Any]
    ) -> tuple[dict[str, Any], str] | None:
        loaded = self._load(
            filename="judge_stage.json",
            schema=JUDGE_STAGE_SCHEMA,
            expected_binding=binding,
            binding_fields=_JUDGE_BINDING_FIELDS,
        )
        if loaded is None:
            return None
        stage, stage_sha = loaded
        result = _judge_result(stage.get("result"))
        if result["prompt_sha256"] != binding["judge_prompt_sha256"]:
            raise GraphQualityStageError("Judge stage prompt binding is invalid")
        return result, stage_sha

    def persist_judge(
        self,
        binding: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> str:
        normalized_binding = _binding(
            binding, expected_fields=_JUDGE_BINDING_FIELDS
        )
        normalized_result = _judge_result(result)
        if (
            normalized_result["prompt_sha256"]
            != normalized_binding["judge_prompt_sha256"]
        ):
            raise GraphQualityStageError("Judge stage prompt binding is invalid")
        body: dict[str, Any] = {
            "schema_version": JUDGE_STAGE_SCHEMA,
            "binding": normalized_binding,
            "result": normalized_result,
        }
        body["payload_sha256"] = payload_sha256(body)
        path = self._stage_path(self.attempt_root, "judge_stage.json")
        if path.exists():
            observed = _verify_envelope(
                _read_object(path),
                schema=JUDGE_STAGE_SCHEMA,
                expected_binding=normalized_binding,
                binding_fields=_JUDGE_BINDING_FIELDS,
            )
            if observed != body:
                raise GraphQualityStageError("existing Judge stage drift")
        else:
            atomic_write_json(path, body)
        return str(body["payload_sha256"])


__all__ = [
    "GraphQualityStageError",
    "GraphQualityStageStore",
    "JUDGE_STAGE_SCHEMA",
    "READER_STAGE_SCHEMA",
]
