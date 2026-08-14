"""Production-only adapters for the isolated one-history S2 live chain.

The OpenAI-compatible transport has no retry loop and never exposes its
credential or raw endpoint in public configuration.  The Judge wrapper lazily
loads the already-qualified legacy Qwen/LongMemEval implementation; raw
questions, prompts, references, hypotheses, and model output stay in memory.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from .artifacts import payload_sha256
from .s2_live import S2LiveInputs
from .s2_retrieval_contract import (
    EDGE_SURFACE_CONTRACT,
    validate_retrieval_identity,
)


class S2AdapterConfigurationError(ValueError):
    """Configuration failure whose message contains no private value."""


class S2ChatTransportError(RuntimeError):
    """Sanitized Reader transport/response failure."""


class S2JudgeAdapterError(RuntimeError):
    """Sanitized legacy Judge adapter/response failure."""


def _normalized_v1_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise S2AdapterConfigurationError("chat base URL is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
    ):
        raise S2AdapterConfigurationError(
            "chat base URL must be an absolute /v1 URL"
        )
    return urlunsplit((parsed.scheme, parsed.netloc, "/v1/", "", ""))


def _error_class(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__name__}"


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise S2ChatTransportError(f"chat completion response invalid: {field}")
    return value


@dataclass(frozen=True)
class ChatCompletionResult:
    """The minimum in-memory shape consumed by ``OfficialFactsReader``."""

    content: str
    prompt_tokens: int
    completion_tokens: int


class OpenAIChatCompletionsTransport:
    """One-attempt OpenAI-compatible Chat Completions transport for Reader."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 180.0,
        client: Any | None = None,
    ) -> None:
        if not isinstance(model, str) or not model:
            raise S2AdapterConfigurationError("chat model is invalid")
        if not isinstance(api_key, str) or not api_key:
            raise S2AdapterConfigurationError("chat API key is missing")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise S2AdapterConfigurationError("chat timeout must be positive")
        normalized = _normalized_v1_url(base_url)
        self.model = model
        self._timeout_seconds = float(timeout_seconds)
        self._http_client: Any | None = None
        if client is None:
            # Both imports are intentionally lazy.  Offline paper-eval tests do
            # not depend on the legacy live environment or its OpenAI SDK.
            httpx = importlib.import_module("httpx")
            openai = importlib.import_module("openai")
            timeout = httpx.Timeout(
                connect=min(5.0, self._timeout_seconds),
                read=self._timeout_seconds,
                write=self._timeout_seconds,
                pool=self._timeout_seconds,
            )
            self._http_client = httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            )
            client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=normalized,
                timeout=timeout,
                max_retries=0,
                http_client=self._http_client,
            )
            client._platform = "Linux"  # type: ignore[attr-defined]
        elif getattr(client, "max_retries", None) != 0:
            raise S2AdapterConfigurationError(
                "injected chat clients must disable hidden retries"
            )
        self._client = client
        self._public_config = {
            "implementation": "openai_compatible_chat_completions",
            "served_model_name": model,
            "endpoint_identity_sha256": hashlib.sha256(
                normalized.encode("utf-8")
            ).hexdigest(),
            "timeout_seconds": self._timeout_seconds,
            "max_attempts": 1,
            "sdk_hidden_retries": 0,
        }
        self.config_sha256 = payload_sha256(self._public_config)

    @property
    def public_config(self) -> dict[str, Any]:
        return deepcopy(self._public_config)

    async def complete(self, request: dict[str, object]) -> ChatCompletionResult:
        if not isinstance(request, dict):
            raise S2ChatTransportError("chat completion request is invalid")
        if request.get("model") != self.model:
            raise S2ChatTransportError("chat completion model identity mismatch")
        messages = request.get("messages")
        if not isinstance(messages, list) or not messages:
            raise S2ChatTransportError("chat completion messages are invalid")
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(**deepcopy(request)),
                timeout=self._timeout_seconds,
            )
        except Exception as error:
            raise S2ChatTransportError(
                f"chat completion failed: {_error_class(error)}"
            ) from None
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or len(choices) != 1:
            raise S2ChatTransportError("chat completion response invalid: choices")
        content = getattr(getattr(choices[0], "message", None), "content", None)
        if not isinstance(content, str) or not content.strip():
            raise S2ChatTransportError("chat completion response invalid: content")
        usage = getattr(response, "usage", None)
        return ChatCompletionResult(
            content=content,
            prompt_tokens=_nonnegative_int(
                getattr(usage, "prompt_tokens", None), "prompt_tokens"
            ),
            completion_tokens=_nonnegative_int(
                getattr(usage, "completion_tokens", None), "completion_tokens"
            ),
        )

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            result = close()
            if result is not None and hasattr(result, "__await__"):
                await result


_BACKEND_PUBLIC_KEYS = (
    "backend",
    "served_model_name",
    "endpoint_identity_sha256",
    "temperature",
    "max_tokens",
    "n",
    "thinking_control",
    "effective_enable_thinking",
    "max_attempts",
    "timeout_seconds",
    "retry_delays_seconds",
    "sdk_hidden_retries",
)


def _status_value(value: Any) -> str:
    candidate = getattr(value, "value", value)
    if candidate not in {"SUCCESS", "INVALID_OUTPUT", "SERVICE_ERROR"}:
        raise S2JudgeAdapterError("judge response invalid: status")
    return str(candidate)


def _safe_backend_config(backend: Any) -> dict[str, Any]:
    public = getattr(backend, "public_config", None)
    if callable(public):
        public = public()
    if not isinstance(public, Mapping):
        raise S2AdapterConfigurationError("judge public configuration is missing")
    projected = {key: deepcopy(public[key]) for key in _BACKEND_PUBLIC_KEYS if key in public}
    required = {
        "backend",
        "served_model_name",
        "endpoint_identity_sha256",
        "temperature",
        "max_tokens",
        "n",
        "thinking_control",
        "effective_enable_thinking",
        "max_attempts",
        "sdk_hidden_retries",
    }
    if not required.issubset(projected):
        raise S2AdapterConfigurationError("judge public configuration is incomplete")
    return projected


class S2LongMemEvalJudge:
    """S2 protocol adapter around the qualified legacy LongMemEval Judge."""

    def __init__(
        self,
        *,
        backend: Any,
        evaluator: Any,
        evaluation_item_type: type,
    ) -> None:
        self._backend = backend
        self._evaluator = evaluator
        self._evaluation_item_type = evaluation_item_type
        backend_config = _safe_backend_config(backend)
        config_hash = getattr(backend, "config_hash", None)
        if not isinstance(config_hash, str) or len(config_hash) != 64:
            raise S2AdapterConfigurationError("judge configuration hash is invalid")
        self._public_config = {
            "implementation": "qualified_legacy_longmemeval_adapter",
            "judge_model": getattr(backend, "model", None),
            "judge_config_sha256": config_hash,
            "backend_public_config": backend_config,
            "raw_prompt_persisted": False,
            "raw_response_persisted": False,
        }
        self.config_sha256 = payload_sha256(self._public_config)

    @property
    def public_config(self) -> dict[str, Any]:
        return deepcopy(self._public_config)

    async def evaluate(
        self, *, hypothesis: str, inputs: S2LiveInputs
    ) -> dict[str, Any]:
        if not isinstance(hypothesis, str) or not hypothesis:
            raise S2JudgeAdapterError("judge hypothesis is invalid")
        item = self._evaluation_item_type(
            item_id=f"{inputs.run_id}:{inputs.history_id}",
            benchmark="longmemeval",
            question_id=inputs.history_id,
            question_type=inputs.question_type,
            question=inputs.question,
            reference_answer=inputs.reference_answer,
            hypothesis=hypothesis,
            abstention=False,
        )
        try:
            result = await self._evaluator.evaluate(item)
        except Exception as error:
            raise S2JudgeAdapterError(
                f"judge evaluation failed: {_error_class(error)}"
            ) from None
        status = _status_value(getattr(result, "status", None))
        label = getattr(result, "label", None)
        if status == "SERVICE_ERROR":
            error_class = getattr(result, "error_class", None)
            if not isinstance(error_class, str) or not error_class or not all(
                character.isalnum() or character in "_." for character in error_class
            ):
                error_class = "unknown"
            raise S2JudgeAdapterError(
                f"judge service failed: {error_class}"
            )
        if status in {"SUCCESS", "INVALID_OUTPUT"} and type(label) is not bool:
            raise S2JudgeAdapterError("judge response invalid: label")
        raw_output = getattr(result, "raw_output", None)
        if not isinstance(raw_output, str):
            raise S2JudgeAdapterError("judge response invalid: output")
        prompt_hash = getattr(result, "prompt_hash", None)
        config_hash = getattr(result, "config_hash", None)
        if not all(
            isinstance(value, str) and len(value) == 64
            for value in (prompt_hash, config_hash)
        ):
            raise S2JudgeAdapterError("judge response invalid: hashes")
        output_bytes = raw_output.encode("utf-8")
        return {
            "status": status,
            "label": label,
            "model": str(getattr(result, "judge_model", "")),
            "prompt_sha256": prompt_hash,
            "config_sha256": config_hash,
            "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
            "output_character_count": len(raw_output),
            "output_byte_count": len(output_bytes),
            "parse_status": str(getattr(result, "parse_status", "")),
            "retry_count": getattr(result, "retry_count", None),
            "error_class": getattr(result, "error_class", None),
        }

    async def aclose(self) -> None:
        close = getattr(self._backend, "aclose", None)
        if callable(close):
            result = close()
            if result is not None and hasattr(result, "__await__"):
                await result


def build_qualified_qwen_judge(
    *, base_url: str, api_key: str
) -> S2LongMemEvalJudge:
    """Lazily bind the exact Qwen backend and official LongMemEval adapter."""

    backend_module = importlib.import_module(
        "evaluation.backends.openai_compatible"
    )
    benchmark_module = importlib.import_module(
        "evaluation.benchmarks.longmemeval"
    )
    schema_module = importlib.import_module("evaluation.schemas")
    backend = backend_module.Qwen3JudgeBackend(
        base_url=base_url,
        api_key=api_key,
        thinking_control="client_request",
        max_attempts=1,
    )
    evaluator = benchmark_module.LongMemEvalAdapter(backend)
    return S2LongMemEvalJudge(
        backend=backend,
        evaluator=evaluator,
        evaluation_item_type=schema_module.EvaluationItem,
    )


def project_s2_adapter_identity(
    *, reader_transport: Any, reader: Any, judge: S2LongMemEvalJudge
) -> dict[str, Any]:
    """Return a hash-bound whitelist projection safe for durable artifacts."""

    transport_config = getattr(reader_transport, "public_config", None)
    reader_config = getattr(reader, "public_config", None)
    judge_config = getattr(judge, "public_config", None)
    if callable(transport_config):
        transport_config = transport_config()
    if callable(reader_config):
        reader_config = reader_config()
    if callable(judge_config):
        judge_config = judge_config()
    if not all(
        isinstance(value, Mapping)
        for value in (transport_config, reader_config, judge_config)
    ):
        raise S2AdapterConfigurationError("S2 adapter identity is incomplete")
    retrieval = validate_retrieval_identity(
        {
            **EDGE_SURFACE_CONTRACT.to_identity(),
            "retriever_type": "graphiti-basic-edge",
        }
    )
    body = {
        "schema_version": "membind.paper-eval-v3.s2-adapter-identity.v2",
        "retrieval": retrieval,
        "reader_transport": deepcopy(dict(transport_config)),
        "reader": deepcopy(dict(reader_config)),
        "judge": deepcopy(dict(judge_config)),
    }
    return {**body, "identity_sha256": payload_sha256(body)}
