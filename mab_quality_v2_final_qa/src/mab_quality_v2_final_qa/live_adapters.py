"""Narrow live adapters around the frozen project runtime surfaces."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import assert_gold_blind, canonical_sha256


@dataclass(frozen=True)
class PublicEpisode:
    question_id: str
    group_id: str
    session_id: str
    source_sequence: int
    source_hash: str
    reference_time: str
    body: str

    @property
    def name(self) -> str:
        return f"{self.question_id}::episode::{self.source_sequence:04d}"

    def to_graphiti_kwargs(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "episode_body": self.body,
            "source_description": "MemoryAgentBench LongMemEval session",
            "reference_time": self.reference_time,
            "group_id": self.group_id,
        }


def render_public_episodes(
    public_context: Mapping[str, Any], *, namespace: str
) -> tuple[PublicEpisode, ...]:
    assert_gold_blind(public_context)
    context_id = str(public_context.get("context_id", "")).strip()
    sessions = public_context.get("sessions")
    if not context_id or not namespace or not isinstance(sessions, Sequence):
        raise ValueError("PUBLIC_EPISODE_INPUT_INVALID")
    episodes: list[PublicEpisode] = []
    for expected_sequence, session in enumerate(sessions):
        if not isinstance(session, Mapping):
            raise ValueError("PUBLIC_SESSION_INVALID")
        sequence = session.get("source_sequence")
        turns = session.get("turns")
        if sequence != expected_sequence or not isinstance(turns, Sequence):
            raise ValueError("PUBLIC_SESSION_SEQUENCE_INVALID")
        lines: list[str] = []
        for turn in turns:
            if not isinstance(turn, Mapping):
                raise ValueError("PUBLIC_TURN_INVALID")
            role = str(turn.get("role", "")).upper()
            content = str(turn.get("content", "")).strip()
            if role not in {"USER", "ASSISTANT"} or not content:
                raise ValueError("PUBLIC_TURN_INVALID")
            lines.append(f"[{role}] {content}")
        session_id = str(session.get("session_id", "")).strip()
        reference_time = str(session.get("timestamp", "")).strip()
        body = "\n".join(lines)
        if not session_id or not reference_time or not body:
            raise ValueError("PUBLIC_SESSION_INVALID")
        source_body = {
            "question_id": context_id,
            "session_id": session_id,
            "source_sequence": expected_sequence,
            "reference_time": reference_time,
            "body": body,
        }
        episodes.append(
            PublicEpisode(
                question_id=context_id,
                group_id=namespace,
                session_id=session_id,
                source_sequence=expected_sequence,
                source_hash=canonical_sha256(source_body),
                reference_time=reference_time,
                body=body,
            )
        )
    if not episodes:
        raise ValueError("PUBLIC_CONTEXT_HAS_NO_SESSIONS")
    return tuple(episodes)


def declared_arrival_offsets_ns(source_count: int) -> tuple[int, ...]:
    if isinstance(source_count, bool) or not isinstance(source_count, int) or source_count <= 0:
        raise ValueError("SOURCE_COUNT_INVALID")
    return (0,) * source_count


@dataclass(frozen=True)
class ReaderCompletion:
    content: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int


class LiveReaderTransport:
    """No-retry transport retaining the finish reason required by Reader v1."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 180.0,
        client: Any | None = None,
    ) -> None:
        normalized = base_url.rstrip("/")
        if not model or not normalized.endswith("/v1") or not api_key:
            raise ValueError("READER_TRANSPORT_CONFIG_INVALID")
        if timeout_seconds <= 0:
            raise ValueError("READER_TRANSPORT_TIMEOUT_INVALID")
        self.model = model
        self.timeout_seconds = float(timeout_seconds)
        self._http_client: Any | None = None
        if client is None:
            httpx = importlib.import_module("httpx")
            openai = importlib.import_module("openai")
            timeout = httpx.Timeout(
                connect=min(5.0, self.timeout_seconds),
                read=self.timeout_seconds,
                write=self.timeout_seconds,
                pool=self.timeout_seconds,
            )
            self._http_client = httpx.AsyncClient(
                timeout=timeout, follow_redirects=False, trust_env=False
            )
            client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=normalized,
                timeout=timeout,
                max_retries=0,
                http_client=self._http_client,
            )
        elif getattr(client, "max_retries", None) != 0:
            raise ValueError("READER_TRANSPORT_HIDDEN_RETRIES_ENABLED")
        self._client = client
        self.public_config = {
            "implementation": "mab_quality_v2_finish_reason_transport_v1",
            "served_model_name": model,
            "endpoint_identity_sha256": hashlib.sha256(
                normalized.encode("utf-8")
            ).hexdigest(),
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": 1,
            "sdk_hidden_retries": 0,
            "finish_reason_preserved": True,
        }
        self.config_sha256 = canonical_sha256(self.public_config)

    async def complete(self, request: dict[str, object]) -> ReaderCompletion:
        if request.get("model") != self.model:
            raise ValueError("READER_MODEL_IDENTITY_MISMATCH")
        response = await asyncio.wait_for(
            self._client.chat.completions.create(**deepcopy(request)),
            timeout=self.timeout_seconds,
        )
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("READER_RESPONSE_CHOICES_INVALID")
        choice = choices[0]
        content = getattr(getattr(choice, "message", None), "content", None)
        finish_reason = getattr(choice, "finish_reason", None)
        usage = getattr(response, "usage", None)
        if not isinstance(content, str) or not content.strip() or not isinstance(
            finish_reason, str
        ):
            raise ValueError("READER_RESPONSE_INVALID")
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (prompt_tokens, completion_tokens)
        ):
            raise ValueError("READER_USAGE_INVALID")
        return ReaderCompletion(
            content.strip(), finish_reason, prompt_tokens, completion_tokens
        )

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            value = close()
            if hasattr(value, "__await__"):
                await value


__all__ = [
    "LiveReaderTransport",
    "PublicEpisode",
    "ReaderCompletion",
    "declared_arrival_offsets_ns",
    "render_public_episodes",
]
