"""Pinned backend identities, kept separate from the core method."""

from .ollama_qwen25 import BackendConfig, BackendConfig as Qwen25BackendConfig
from .ollama_mistral32 import BackendConfig as Mistral32BackendConfig
from .ollama_qwen35 import BackendConfig as Qwen35BackendConfig, ReasoningDisabledOpenAIClient

__all__ = ["BackendConfig", "Qwen25BackendConfig", "Qwen35BackendConfig", "Mistral32BackendConfig", "ReasoningDisabledOpenAIClient"]
