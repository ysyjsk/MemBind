"""Pinned Ollama qwen3.5 transport configuration.

qwen3.5 is a thinking model. Ollama's OpenAI-compatible endpoint otherwise
returns the reasoning channel while leaving ``message.content`` empty, which
the upstream Graphiti client correctly rejects. ``reasoning_effort=none`` is
an Ollama-supported request option that selects the normal answer channel; it
does not alter Graphiti prompts, extraction, retries, or publication.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.contracts import canonical_sha256


@dataclass(frozen=True, slots=True)
class BackendConfig:
    model: str = "qwen3.5:latest"
    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = 768
    base_url: str = "http://127.0.0.1:11434/v1"
    api_key: str = "ollama"
    graphiti_version: str = "0.29.3"
    graphiti_commit: str = "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"
    structured_output_mode: str = "json_object"
    max_tokens: int = 4096
    temperature: float = 0.0
    max_concurrency: int = 2
    reasoning_effort: str = "none"

    def __post_init__(self) -> None:
        if self.structured_output_mode not in {"json_schema", "json_object"}:
            raise ValueError("unsupported structured output mode")
        if self.reasoning_effort not in {"none", "low", "medium", "high", "max"}:
            raise ValueError("unsupported reasoning effort")
        if self.embedding_dimensions < 1 or self.max_tokens < 1 or self.max_concurrency < 1:
            raise ValueError("numeric backend settings must be positive")

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "embedding_model": self.embedding_model,
            "embedding_dimensions": self.embedding_dimensions,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "graphiti_version": self.graphiti_version,
            "graphiti_commit": self.graphiti_commit,
            "structured_output_mode": self.structured_output_mode,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "max_concurrency": self.max_concurrency,
            "reasoning_effort": self.reasoning_effort,
        }

    @property
    def identity_sha256(self) -> str:
        return canonical_sha256(self.to_dict())


class _CompletionsProxy:
    def __init__(self, completions: Any, reasoning_effort: str) -> None:
        self._completions = completions
        self._reasoning_effort = reasoning_effort

    async def create(self, **kwargs: Any) -> Any:
        extra_body = dict(kwargs.pop("extra_body", {}) or {})
        extra_body["reasoning_effort"] = self._reasoning_effort
        return await self._completions.create(**kwargs, extra_body=extra_body)


class _ChatProxy:
    def __init__(self, chat: Any, reasoning_effort: str) -> None:
        self.completions = _CompletionsProxy(chat.completions, reasoning_effort)


class ReasoningDisabledOpenAIClient:
    """Transport decorator for a standard ``AsyncOpenAI`` client."""

    def __init__(self, client: Any, *, reasoning_effort: str = "none") -> None:
        if not hasattr(client, "chat") or not hasattr(client.chat, "completions"):
            raise TypeError("client must expose async chat completions")
        self.chat = _ChatProxy(client.chat, reasoning_effort)


__all__ = ["BackendConfig", "ReasoningDisabledOpenAIClient"]
