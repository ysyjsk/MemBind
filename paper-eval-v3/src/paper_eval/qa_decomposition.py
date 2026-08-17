"""Pure contracts for a read-only Top-10 versus gold-only QA diagnosis.

The module owns no live clients. Raw benchmark text and model output may enter
private artifacts, while public artifacts contain only hashes, counts, and
scores. Construction namespaces are inputs and are never mutated here.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .artifacts import payload_sha256
from .baseline_suite import DEVELOPMENT_HISTORIES
from .s2_session_reader import MaterializedSession, materialize_ranked_sessions


VARIANTS = ("top10", "gold_only")
STAGES = ("retrieval", "reader", "judge")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BINDING_FIELDS = {
    "overlay_run_id",
    "source_run_id",
    "history_id",
    "namespace_sha256",
    "construction_result_sha256",
    "variant",
    "selected_session_ids_sha256",
    "reader_config_sha256",
    "judge_config_sha256",
}


@dataclass(frozen=True)
class VariantSessions:
    """Selected identities in retrieval order and values in Reader order."""

    variant: str
    selected_session_ids: tuple[str, ...]
    sessions: tuple[MaterializedSession, ...]


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"QA decomposition {field} is invalid")
    return value


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"QA decomposition {field} is invalid")
    return value


def _binding(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _BINDING_FIELDS:
        raise ValueError("QA decomposition binding is invalid")
    result = dict(value)
    for field in ("overlay_run_id", "source_run_id", "history_id"):
        _text(result[field], field=field)
    if result["variant"] not in VARIANTS:
        raise ValueError("QA decomposition binding variant is invalid")
    for field in _BINDING_FIELDS:
        if field.endswith("_sha256"):
            _sha(result[field], field=field)
    return result


def select_variant_sessions(
    *,
    record: Mapping[str, Any],
    variant: str,
    retrieved_session_ids: Sequence[str],
    top_k: int = 10,
) -> VariantSessions:
    """Select one frozen Reader context without altering the source record."""

    if variant not in VARIANTS:
        raise ValueError("QA decomposition variant is invalid")
    if isinstance(retrieved_session_ids, (str, bytes)) or not isinstance(
        retrieved_session_ids, Sequence
    ):
        raise ValueError("QA decomposition retrieved sessions are invalid")
    retrieved = tuple(str(value) for value in retrieved_session_ids)
    if variant == "top10":
        if (
            isinstance(top_k, bool)
            or not isinstance(top_k, int)
            or top_k < 1
            or len(retrieved) < top_k
        ):
            raise ValueError("QA decomposition top-k inventory is invalid")
        selected = retrieved[:top_k]
    else:
        raw_gold = record.get("answer_session_ids")
        if not isinstance(raw_gold, list) or not raw_gold:
            raise ValueError("QA decomposition gold sessions are invalid")
        selected = tuple(str(value) for value in raw_gold)
    if any(not value for value in selected) or len(set(selected)) != len(selected):
        raise ValueError("QA decomposition selected sessions are invalid")
    sessions = materialize_ranked_sessions(
        record=record,
        ranked_session_ids=selected,
        top_k=len(selected),
    )
    return VariantSessions(
        variant=variant,
        selected_session_ids=selected,
        sessions=sessions,
    )


def make_stage(
    *, stage: str, binding: Mapping[str, Any], result: Mapping[str, Any]
) -> dict[str, Any]:
    """Create one hash-sealed private checkpoint for resumable model work."""

    if stage not in STAGES:
        raise ValueError("QA decomposition stage is invalid")
    if not isinstance(result, Mapping) or not result:
        raise ValueError("QA decomposition stage result is invalid")
    body = {
        "schema_version": "membind.paper-eval-v3.qa-decomposition-stage.v1",
        "stage": stage,
        "binding": _binding(binding),
        "result": dict(result),
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def verify_stage(
    value: Mapping[str, Any], *, stage: str, binding: Mapping[str, Any]
) -> dict[str, Any]:
    """Reject stage tampering or reuse under another experiment identity."""

    if not isinstance(value, Mapping):
        raise ValueError("QA decomposition stage is invalid")
    observed = dict(value)
    observed_hash = observed.pop("payload_sha256", None)
    if observed_hash != payload_sha256(observed):
        raise ValueError("QA decomposition stage hash mismatch")
    if (
        observed.get("schema_version")
        != "membind.paper-eval-v3.qa-decomposition-stage.v1"
        or observed.get("stage") != stage
    ):
        raise ValueError("QA decomposition stage identity mismatch")
    if _binding(observed.get("binding", {})) != _binding(binding):
        raise ValueError("QA decomposition stage binding mismatch")
    if not isinstance(observed.get("result"), dict) or not observed["result"]:
        raise ValueError("QA decomposition stage result is invalid")
    observed["payload_sha256"] = observed_hash
    return observed


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_final_artifacts(
    *,
    binding: Mapping[str, Any],
    question: str,
    reference_answer: str,
    selected_session_ids: Sequence[str],
    reader_result: Mapping[str, Any],
    judge_result: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a private audit bundle and a content-free public projection."""

    bound = _binding(binding)
    question_value = _text(question, field="question")
    reference_value = _text(reference_answer, field="reference_answer")
    selected = tuple(str(value) for value in selected_session_ids)
    if not selected or any(not value for value in selected):
        raise ValueError("QA decomposition selected sessions are invalid")
    reader = dict(reader_result)
    judge = dict(judge_result)
    prompt = _text(reader.get("prompt"), field="Reader prompt")
    answer = _text(reader.get("answer"), field="Reader answer")
    if (
        isinstance(reader.get("prompt_tokens"), bool)
        or not isinstance(reader.get("prompt_tokens"), int)
        or reader["prompt_tokens"] < 0
        or isinstance(reader.get("completion_tokens"), bool)
        or not isinstance(reader.get("completion_tokens"), int)
        or reader["completion_tokens"] < 0
    ):
        raise ValueError("QA decomposition Reader token counts are invalid")
    if judge.get("status") != "SUCCESS" or type(judge.get("label")) is not bool:
        raise ValueError("QA decomposition Judge result is invalid")
    raw_judge = _text(judge.get("raw_output"), field="Judge output")

    private: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v3.qa-decomposition-private.v1",
        "binding": bound,
        "question": question_value,
        "reference_answer": reference_value,
        "selected_session_ids": list(selected),
        "reader_result": reader,
        "judge_result": judge,
    }
    private["payload_sha256"] = payload_sha256(private)
    public: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v3.qa-decomposition-public.v1",
        "overlay_run_id": bound["overlay_run_id"],
        "source_run_id": bound["source_run_id"],
        "history_id": bound["history_id"],
        "variant": bound["variant"],
        "namespace_sha256": bound["namespace_sha256"],
        "construction_result_sha256": bound["construction_result_sha256"],
        "selected_session_ids_sha256": bound["selected_session_ids_sha256"],
        "selected_session_count": len(selected),
        "reader_config_sha256": bound["reader_config_sha256"],
        "judge_config_sha256": bound["judge_config_sha256"],
        "question_sha256": _hash_text(question_value),
        "reference_answer_sha256": _hash_text(reference_value),
        "reader_prompt_sha256": _hash_text(prompt),
        "reader_answer_sha256": _hash_text(answer),
        "judge_output_sha256": _hash_text(raw_judge),
        "reader_prompt_tokens": reader["prompt_tokens"],
        "reader_completion_tokens": reader["completion_tokens"],
        "judge_label": judge["label"],
        "qa_accuracy": 1.0 if judge["label"] else 0.0,
        "private_payload_sha256": private["payload_sha256"],
        "claim_scope": "DEVELOPMENT_DIAGNOSTIC_NOT_PAPER_SIGNIFICANCE",
    }
    public["payload_sha256"] = payload_sha256(public)
    return private, public


def classify_reader_judge(human_correct: bool, judge_label: bool) -> str:
    """Name the four human-versus-Judge outcomes without repairing scores."""

    if type(human_correct) is not bool or type(judge_label) is not bool:
        raise ValueError("QA decomposition audit labels must be boolean")
    return {
        (True, True): "READER_CORRECT_JUDGE_YES",
        (True, False): "READER_CORRECT_JUDGE_FALSE_NEGATIVE",
        (False, False): "READER_WRONG_JUDGE_NO",
        (False, True): "READER_WRONG_JUDGE_FALSE_POSITIVE",
    }[(human_correct, judge_label)]


def summarize_results(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate exactly four histories for each predeclared Reader variant."""

    values = [dict(value) for value in rows]
    expected = [
        (history_id, variant)
        for history_id in DEVELOPMENT_HISTORIES
        for variant in VARIANTS
    ]
    observed = [(value.get("history_id"), value.get("variant")) for value in values]
    if observed != expected:
        raise ValueError("QA decomposition result inventory drift")
    by_variant: dict[str, Any] = {}
    for variant in VARIANTS:
        selected = [value for value in values if value["variant"] == variant]
        scores = [value.get("qa_accuracy") for value in selected]
        tokens = [value.get("reader_prompt_tokens") for value in selected]
        if any(type(value) not in (int, float) or value not in (0, 0.0, 1, 1.0) for value in scores):
            raise ValueError("QA decomposition score is invalid")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in tokens):
            raise ValueError("QA decomposition token count is invalid")
        if any(_SHA256.fullmatch(str(value.get("payload_sha256", ""))) is None for value in selected):
            raise ValueError("QA decomposition result hash is invalid")
        by_variant[variant] = {
            "history_count": len(selected),
            "qa_accuracy_macro": sum(float(value) for value in scores) / len(scores),
            "reader_prompt_tokens_total": sum(tokens),
        }
    by_variant["oracle_gain"] = (
        by_variant["gold_only"]["qa_accuracy_macro"]
        - by_variant["top10"]["qa_accuracy_macro"]
    )
    return by_variant


__all__ = [
    "STAGES",
    "VARIANTS",
    "VariantSessions",
    "build_final_artifacts",
    "classify_reader_judge",
    "make_stage",
    "select_variant_sessions",
    "summarize_results",
    "verify_stage",
]
