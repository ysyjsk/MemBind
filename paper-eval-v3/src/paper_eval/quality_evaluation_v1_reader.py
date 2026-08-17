"""Fixed concise Qwen Reader for Quality Evaluation v1."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Protocol

from .artifacts import payload_sha256
from .temporal_fact_reader import TemporalFactReaderResult


_INSTRUCTION = """\
Answer the question using only the provided memory evidence.
Consider evidence in chronological order.
When information changes, use the latest effective information before the
question date and distinguish current facts from future plans.
Return only a concise final answer."""

_TEMPLATE = """\
{instruction}

Memory evidence (JSON):
{context_json}

Question date: {question_date}
Question: {question}
Answer:"""

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class QualityEvaluationV1ReaderError(ValueError):
    """The frozen Reader input, request, or response is invalid."""


class QualityEvaluationV1ReaderInvalidOutput(QualityEvaluationV1ReaderError):
    """The provider returned a completion that cannot enter QA scoring."""


class QualityEvaluationV1Transport(Protocol):
    config_sha256: str

    async def complete(self, request: dict[str, object]) -> object: ...


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QualityEvaluationV1ReaderError(f"Quality v1 {field} is invalid")
    return value.strip()


def render_quality_v1_prompt(
    *, context_json: str, question_date: str, question: str
) -> str:
    """Render the one shared, question-type-independent Reader prompt."""

    context = _text(context_json, field="context")
    try:
        decoded = json.loads(context)
    except json.JSONDecodeError:
        raise QualityEvaluationV1ReaderError("Quality v1 context is not JSON") from None
    if not isinstance(decoded, list) or not decoded:
        raise QualityEvaluationV1ReaderError("Quality v1 context is invalid")
    return _TEMPLATE.format(
        instruction=_INSTRUCTION,
        context_json=context,
        question_date=_text(question_date, field="question_date"),
        question=_text(question, field="question"),
    )


def _count(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise QualityEvaluationV1ReaderError(f"Quality v1 {field} is invalid")
    return value


class QualityEvaluationV1Reader:
    """One no-thinking 256-token request; every non-stop finish is invalid."""

    def __init__(self, *, model: str, transport: QualityEvaluationV1Transport) -> None:
        self.model = _text(model, field="model")
        transport_hash = getattr(transport, "config_sha256", None)
        if not isinstance(transport_hash, str) or _SHA256.fullmatch(transport_hash) is None:
            raise QualityEvaluationV1ReaderError("Quality v1 transport identity is invalid")
        self._transport = transport
        self.public_config = {
            "schema_version": "membind.paper-eval-v3.quality-reader-v1",
            "implementation": "graphiti_fact_local_round_concise_reader_v1",
            "instruction_sha256": hashlib.sha256(_INSTRUCTION.encode()).hexdigest(),
            "template_sha256": hashlib.sha256(_TEMPLATE.encode()).hexdigest(),
            "messages": ["user"],
            "system_prompt": None,
            "temperature": 0,
            "max_tokens": 256,
            "n": 1,
            "effective_enable_thinking": False,
            "finish_reason_policy": "REQUIRE_STOP_OTHERWISE_INVALID",
            "model": self.model,
            "transport_config_sha256": transport_hash,
        }
        self.config_sha256 = payload_sha256(self.public_config)

    async def answer(
        self, *, context_json: str, question_date: str, question: str
    ) -> TemporalFactReaderResult:
        prompt = render_quality_v1_prompt(
            context_json=context_json,
            question_date=question_date,
            question=question,
        )
        request: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 256,
            "n": 1,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
        try:
            response = await self._transport.complete(request)
        except Exception as error:
            raise QualityEvaluationV1ReaderError(
                f"Quality v1 Reader request failed: {type(error).__name__}"
            ) from None
        content = getattr(response, "content", None)
        finish_reason = getattr(response, "finish_reason", None)
        if not isinstance(content, str) or not content.strip():
            raise QualityEvaluationV1ReaderError("Quality v1 Reader content is invalid")
        if finish_reason != "stop":
            raise QualityEvaluationV1ReaderInvalidOutput(
                "Quality v1 Reader finish_reason is not stop"
            )
        return TemporalFactReaderResult(
            answer=content.strip(),
            prompt_for_test=prompt,
            prompt_tokens=_count(
                getattr(response, "prompt_tokens", None), field="prompt_tokens"
            ),
            completion_tokens=_count(
                getattr(response, "completion_tokens", None),
                field="completion_tokens",
            ),
            finish_reason="stop",
            model=self.model,
            config_sha256=self.config_sha256,
        )


__all__ = [
    "QualityEvaluationV1Reader",
    "QualityEvaluationV1ReaderError",
    "QualityEvaluationV1ReaderInvalidOutput",
    "render_quality_v1_prompt",
]
