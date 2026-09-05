"""Official Graphiti local-provider configuration.

The configuration mirrors the Graphiti v0.29.3 Ollama example and the
independent graphiti-mcp-ollama deployment.  It does not start services or
hide provider retries; the experiment runner records the exact identity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..core.contracts import canonical_sha256


@dataclass(frozen=True, slots=True)
class BackendConfig:
    model: str = "qwen2.5:14b"
    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = 768
    base_url: str = "http://127.0.0.1:11434/v1"
    api_key: str = "ollama"
    graphiti_version: str = "0.29.3"
    graphiti_commit: str = "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"
    structured_output_mode: str = "json_schema"
    # Mirrors the independent graphiti-mcp-ollama deployment.  A larger limit
    # was diagnostic-only and caused an unbounded 51-minute local generation.
    max_tokens: int = 2048
    temperature: float = 0.0
    max_concurrency: int = 2

    def __post_init__(self) -> None:
        if self.structured_output_mode not in {"json_schema", "json_object"}:
            raise ValueError("unsupported structured output mode")
        if self.embedding_dimensions < 1 or self.max_tokens < 1 or self.max_concurrency < 1:
            raise ValueError("numeric backend settings must be positive")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def identity_sha256(self) -> str:
        return canonical_sha256(self.to_dict())
