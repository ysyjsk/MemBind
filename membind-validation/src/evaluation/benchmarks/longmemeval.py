"""LongMemEval adapter using the pinned official task-specific rubric."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from evaluation.backends.base import BackendStatus, JudgeBackend
from evaluation.schemas import EvaluationItem, EvaluationResult, EvaluationStatus
from evaluation.vendor.longmemeval_evaluate_qa import get_anscheck_prompt


SCORER = "longmemeval_official_get_anscheck_prompt"
_YES_RE = re.compile(r"^\s*yes\s*[.!]?\s*$", re.IGNORECASE)
_NO_RE = re.compile(r"^\s*no\s*[.!]?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class AuditParseResult:
    label: bool | None
    normalized_output: str
    parse_status: str


def official_compatible_label(raw_output: str) -> bool:
    """Preserve pinned LongMemEval's headline 'yes' substring parser."""

    return "yes" in raw_output.lower()


def parse_audit_label(raw_output: str) -> AuditParseResult:
    """Strict audit view that never silently repairs malformed judge output."""

    if _YES_RE.fullmatch(raw_output):
        return AuditParseResult(True, "YES", "YES")
    if _NO_RE.fullmatch(raw_output):
        return AuditParseResult(False, "NO", "NO")
    normalized = " ".join(raw_output.strip().split())
    return AuditParseResult(None, normalized, "INVALID")


class LongMemEvalAdapter:
    """Convert frozen system output into one official-rubric judge request."""

    def __init__(self, backend: JudgeBackend) -> None:
        self._backend = backend

    async def evaluate(self, item: EvaluationItem) -> EvaluationResult:
        if item.benchmark != "longmemeval":
            raise ValueError("LongMemEvalAdapter received another benchmark")
        try:
            prompt = get_anscheck_prompt(
                item.question_type,
                item.question,
                item.reference_answer,
                item.hypothesis,
                item.abstention,
            )
        except NotImplementedError:
            raise ValueError("unsupported LongMemEval question type") from None
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        backend_result = await self._backend.judge(prompt)
        if backend_result.status is BackendStatus.SERVICE_ERROR:
            return EvaluationResult(
                item_id=item.item_id,
                benchmark=item.benchmark,
                scorer=SCORER,
                judge_model=self._backend.model,
                label=None,
                status=EvaluationStatus.SERVICE_ERROR,
                raw_output="",
                normalized_output="",
                parse_status="NOT_RUN",
                retry_count=backend_result.retry_count,
                error_class=backend_result.error_class,
                prompt_hash=prompt_hash,
                config_hash=self._backend.config_hash,
                metadata={
                    "rubric_source": "LongMemEval official get_anscheck_prompt",
                    "hypothesis_is_frozen_system_output": True,
                    "reader_reanswer_requested": False,
                },
            )

        raw_output = backend_result.raw_output or ""
        official_label = official_compatible_label(raw_output)
        audit = parse_audit_label(raw_output)
        status = (
            EvaluationStatus.SUCCESS
            if audit.label is not None
            else EvaluationStatus.INVALID_OUTPUT
        )
        parser_disagreement = audit.label is None or official_label is not audit.label
        return EvaluationResult(
            item_id=item.item_id,
            benchmark=item.benchmark,
            scorer=SCORER,
            judge_model=self._backend.model,
            # Preserve the official benchmark headline parser. Consumers must
            # aggregate only SUCCESS; INVALID_OUTPUT remains an excluded audit
            # state even when the official substring parser yields False.
            label=official_label,
            status=status,
            raw_output=raw_output,
            normalized_output=audit.normalized_output,
            parse_status=audit.parse_status,
            retry_count=backend_result.retry_count,
            error_class=None,
            prompt_hash=prompt_hash,
            config_hash=self._backend.config_hash,
            metadata={
                "rubric_source": "LongMemEval official get_anscheck_prompt",
                "official_compatible_label": official_label,
                "audit_label": audit.label,
                "parser_disagreement": parser_disagreement,
                "hypothesis_is_frozen_system_output": True,
                "reader_reanswer_requested": False,
            },
        )
