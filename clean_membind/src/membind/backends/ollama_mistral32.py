"""Pinned Mistral Small 3.2 Ollama backend identity.

This module contains configuration only. The Native execution path remains
Graphiti 0.29.3's upstream ``OpenAIGenericClient`` and ``Graphiti.add_episode``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.contracts import canonical_sha256


@dataclass(frozen=True, slots=True)
class BackendConfig:
    model: str = "mistral-small3.2:24b-instruct-2506-q4_K_M"
    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = 768
    base_url: str = "http://127.0.0.1:11434/v1"
    api_key: str = "ollama"
    graphiti_version: str = "0.29.3"
    graphiti_commit: str = "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"
    structured_output_mode: str = "json_schema"
    max_tokens: int = 16384
    temperature: float = 0.0
    max_concurrency: int = 2
    runtime: str = "ollama-0.17.6"

    def __post_init__(self) -> None:
        if self.structured_output_mode not in {"json_schema", "json_object"}:
            raise ValueError("unsupported structured output mode")
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
            "runtime": self.runtime,
        }

    @property
    def identity_sha256(self) -> str:
        return canonical_sha256(self.to_dict())


__all__ = ["BackendConfig"]
