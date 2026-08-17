"""Read Graphiti temporal facts using a pinned, Zep-shaped context.

This module is deliberately separate from the live baseline suite.  It ports
the public Zep LongMemEval FACTS/ENTITIES presentation while making every
Graphiti temporal field explicit and requiring an untruncated one-shot Reader
response.  It never receives a gold answer or gold session identifier.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .artifacts import payload_sha256


ZEP_REPOSITORY = "getzep/zep"
ZEP_COMMIT = "be263ee23085410185835e0d8508b47fd35e9abb"
ZEP_SOURCE_PATH = "benchmarks/longmemeval/zep_longmem_eval.py"
ZEP_SOURCE_SHA256 = (
    "785eacdfd9a388ea00f636074579f7409e04a48d0c1bf5685022f3830a6b72d4"
)

_CONTEXT_TEMPLATE = """\
FACTS and ENTITIES represent relevant context to the current conversation.

# These are the most relevant facts and their temporal metadata.
<FACTS>
{facts}
</FACTS>

# These are the most relevant entities.
<ENTITIES>
{entities}
</ENTITIES>"""

_SYSTEM_PROMPT = (
    "You are a helpful expert assistant answering questions from a user "
    "based on the provided context."
)

_QUESTION_TEMPLATE = """\
Your task is to briefly answer the question. You are given relevant context
from previous conversations. If the context does not contain the answer,
abstain from answering.

Context:
{context}

Question date: {question_date}
Question: {question}"""


class TemporalFactReaderError(ValueError):
    """The temporal context or one-shot Reader contract failed safely."""


class TemporalFactReaderTransport(Protocol):
    config_sha256: str

    async def complete(self, request: dict[str, object]) -> object: ...


_SHA256 = re.compile(r"[0-9a-f]{64}")


def _transport_config_sha256(transport: object) -> str:
    value = getattr(transport, "config_sha256", None)
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise TemporalFactReaderError(
            "Reader transport config_sha256 must be lowercase SHA-256"
        )
    return value


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TemporalFactReaderError(f"{field} must be nonempty")
    return value.strip()


def _optional_time(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field=field)


def _rank(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TemporalFactReaderError(f"{field} must be a positive integer")
    return value


@dataclass(frozen=True)
class TemporalFactEvidence:
    """One ranked EntityEdge projection with source-session provenance."""

    retrieval_rank: int
    edge_uuid: str
    fact: str
    source_session_ids: tuple[str, ...]
    valid_at: str | None
    invalid_at: str | None
    expired_at: str | None
    reference_time: str | None

    def __post_init__(self) -> None:
        _rank(self.retrieval_rank, field="fact retrieval_rank")
        _required_text(self.edge_uuid, field="edge_uuid")
        _required_text(self.fact, field="fact")
        if (
            not isinstance(self.source_session_ids, tuple)
            or not self.source_session_ids
            or len(set(self.source_session_ids)) != len(self.source_session_ids)
            or any(
                not isinstance(value, str) or not value
                for value in self.source_session_ids
            )
        ):
            raise TemporalFactReaderError("source_session_ids are invalid")
        for name in ("valid_at", "invalid_at", "expired_at", "reference_time"):
            _optional_time(getattr(self, name), field=name)


@dataclass(frozen=True)
class GraphEntityEvidence:
    """One ranked EntityNode name/summary projection."""

    retrieval_rank: int
    node_uuid: str
    name: str
    summary: str

    def __post_init__(self) -> None:
        _rank(self.retrieval_rank, field="entity retrieval_rank")
        _required_text(self.node_uuid, field="node_uuid")
        _required_text(self.name, field="entity name")
        if not isinstance(self.summary, str):
            raise TemporalFactReaderError("entity summary must be a string")


def _validate_contiguous_ranks(values: Sequence[object], *, field: str) -> None:
    ranks = [getattr(value, "retrieval_rank", None) for value in values]
    if ranks != list(range(1, len(values) + 1)):
        raise TemporalFactReaderError(f"{field} ranks must be contiguous")


def _time(value: str | None, *, absent: str) -> str:
    return value if value is not None else absent


def render_zep_temporal_context(
    facts: Sequence[TemporalFactEvidence],
    entities: Sequence[GraphEntityEvidence],
) -> str:
    """Render deterministic ranked FACTS/ENTITIES without label information."""

    if isinstance(facts, (str, bytes)) or isinstance(entities, (str, bytes)):
        raise TemporalFactReaderError("graph evidence must be a sequence")
    fact_values = tuple(facts)
    entity_values = tuple(entities)
    if not fact_values or any(
        not isinstance(value, TemporalFactEvidence) for value in fact_values
    ):
        raise TemporalFactReaderError("at least one temporal fact is required")
    if any(not isinstance(value, GraphEntityEvidence) for value in entity_values):
        raise TemporalFactReaderError("entity evidence is invalid")
    _validate_contiguous_ranks(fact_values, field="fact")
    _validate_contiguous_ranks(entity_values, field="entity")

    fact_lines = []
    for value in fact_values:
        fact_lines.append(
            "  - "
            f"[rank {value.retrieval_rank}] {value.fact} "
            "("
            f"valid_at={_time(value.valid_at, absent='unknown')}; "
            f"invalid_at={_time(value.invalid_at, absent='present')}; "
            f"expired_at={_time(value.expired_at, absent='not-expired')}; "
            f"reference_time={_time(value.reference_time, absent='unknown')}"
            ")"
        )
    entity_lines = [
        f"  - [rank {value.retrieval_rank}] {value.name}: {value.summary}"
        for value in entity_values
    ]
    return _CONTEXT_TEMPLATE.format(
        facts="\n".join(fact_lines),
        entities="\n".join(entity_lines),
    )


def render_temporal_fact_reader_prompt(
    facts: Sequence[TemporalFactEvidence],
    entities: Sequence[GraphEntityEvidence],
    *,
    question_date: str,
    question: str,
) -> str:
    """Render the exact deterministic user prompt sent to the Reader."""

    context = render_zep_temporal_context(facts, entities)
    date = _required_text(question_date, field="question_date")
    question_value = _required_text(question, field="question")
    return _QUESTION_TEMPLATE.format(
        context=context,
        question_date=date,
        question=question_value,
    )


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TemporalFactReaderError(f"Reader response invalid: {field}")
    return value


@dataclass(frozen=True)
class TemporalFactReaderResult:
    """In-memory answer plus a content-free public artifact projection."""

    answer: str
    prompt_for_test: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str
    model: str
    config_sha256: str

    def to_public_artifact(self) -> dict[str, object]:
        prompt_bytes = self.prompt_for_test.encode("utf-8")
        answer_bytes = self.answer.encode("utf-8")
        return {
            "status": "SUCCESS",
            "model": self.model,
            "config_sha256": self.config_sha256,
            "prompt_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
            "prompt_character_count": len(self.prompt_for_test),
            "prompt_byte_count": len(prompt_bytes),
            "output_sha256": hashlib.sha256(answer_bytes).hexdigest(),
            "output_character_count": len(self.answer),
            "output_byte_count": len(answer_bytes),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "finish_reason": self.finish_reason,
            "truncation_count": 0,
        }


class TemporalFactReader:
    """Issue one fixed Zep-shaped Reader request and reject truncation."""

    def __init__(self, *, model: str, transport: TemporalFactReaderTransport) -> None:
        self.model = _required_text(model, field="Reader model")
        self._transport = transport
        transport_config_sha256 = _transport_config_sha256(transport)
        self.public_config = {
            "implementation": "graphiti_temporal_fact_reader_v1",
            "alignment": "zep_longmemeval_context_shape_adapted_to_oss_graphiti",
            "upstream_repository": ZEP_REPOSITORY,
            "upstream_commit": ZEP_COMMIT,
            "upstream_source_path": ZEP_SOURCE_PATH,
            "upstream_source_sha256": ZEP_SOURCE_SHA256,
            "context_shape": "zep_facts_entities_with_validity",
            "top_k_edges": 20,
            "top_k_nodes": 20,
            "fact_order": "retrieval_rank",
            "entity_order": "retrieval_rank",
            "temporal_fields": [
                "valid_at",
                "invalid_at",
                "expired_at",
                "reference_time",
            ],
            "system_prompt_sha256": hashlib.sha256(
                _SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "question_template_sha256": hashlib.sha256(
                _QUESTION_TEMPLATE.encode("utf-8")
            ).hexdigest(),
            "messages": ["system", "user"],
            "temperature": 0,
            "max_tokens": 500,
            "n": 1,
            "thinking_control": "client_request",
            "effective_enable_thinking": False,
            "max_attempts": 1,
            "sdk_hidden_retries": 0,
            "finish_reason_policy": "REQUIRE_STOP",
            "model": self.model,
            "transport_config_sha256": transport_config_sha256,
        }
        self.config_sha256 = payload_sha256(self.public_config)

    async def answer(
        self,
        facts: Sequence[TemporalFactEvidence],
        entities: Sequence[GraphEntityEvidence],
        *,
        question_date: str,
        question: str,
    ) -> TemporalFactReaderResult:
        user_prompt = render_temporal_fact_reader_prompt(
            facts,
            entities,
            question_date=question_date,
            question=question,
        )
        request: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": 500,
            "n": 1,
            "extra_body": {
                "chat_template_kwargs": {"enable_thinking": False}
            },
        }
        try:
            response = await self._transport.complete(request)
        except Exception as error:
            if isinstance(error, TemporalFactReaderError):
                raise
            raise TemporalFactReaderError(
                f"Reader request failed: {type(error).__name__}"
            ) from None
        content = getattr(response, "content", None)
        finish_reason = getattr(response, "finish_reason", None)
        if not isinstance(content, str) or not content.strip():
            raise TemporalFactReaderError("Reader response invalid: content")
        if finish_reason == "length":
            raise TemporalFactReaderError("Reader response was truncated")
        if finish_reason != "stop":
            raise TemporalFactReaderError("Reader response invalid: finish_reason")
        return TemporalFactReaderResult(
            answer=content.strip(),
            prompt_for_test=user_prompt,
            prompt_tokens=_nonnegative_int(
                getattr(response, "prompt_tokens", None), field="prompt_tokens"
            ),
            completion_tokens=_nonnegative_int(
                getattr(response, "completion_tokens", None),
                field="completion_tokens",
            ),
            finish_reason=finish_reason,
            model=self.model,
            config_sha256=self.config_sha256,
        )


__all__ = [
    "GraphEntityEvidence",
    "TemporalFactEvidence",
    "TemporalFactReader",
    "TemporalFactReaderError",
    "TemporalFactReaderResult",
    "render_temporal_fact_reader_prompt",
    "render_zep_temporal_context",
]
