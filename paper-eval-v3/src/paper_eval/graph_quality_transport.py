"""One-attempt OpenAI-compatible transport for graph-quality Readers.

Unlike the historical shared transport, this response shape preserves the
provider's finish reason so a max-token truncation cannot be reported as a
successful Reader answer. Credentials and raw endpoints are never public.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .artifacts import payload_sha256


class GraphQualityTransportError(RuntimeError):
    """The graph-quality transport configuration or response is invalid."""


def _normalized_v1_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise GraphQualityTransportError("chat base URL is invalid")
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
        raise GraphQualityTransportError("chat base URL must be an absolute /v1 URL")
    return urlunsplit((parsed.scheme, parsed.netloc, "/v1/", "", ""))


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GraphQualityTransportError(f"chat response invalid: {field}")
    return value


@dataclass(frozen=True)
class GraphQualityChatResult:
    content: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str


class GraphQualityTransport:
    """Perform one explicit chat completion with SDK retries disabled."""

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
            raise GraphQualityTransportError("chat model is invalid")
        if not isinstance(api_key, str) or not api_key:
            raise GraphQualityTransportError("chat API key is missing")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise GraphQualityTransportError("chat timeout is invalid")
        normalized = _normalized_v1_url(base_url)
        self.model = model
        self._timeout_seconds = float(timeout_seconds)
        self._http_client: Any | None = None
        if client is None:
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
            raise GraphQualityTransportError(
                "injected chat clients must disable hidden retries"
            )
        self._client = client
        self._public_config = {
            "implementation": "graph_quality_openai_chat_completions_v1",
            "served_model_name": model,
            "endpoint_identity_sha256": hashlib.sha256(
                normalized.encode("utf-8")
            ).hexdigest(),
            "timeout_seconds": self._timeout_seconds,
            "max_attempts": 1,
            "sdk_hidden_retries": 0,
            "finish_reason_preserved": True,
        }
        self.config_sha256 = payload_sha256(self._public_config)

    @property
    def public_config(self) -> dict[str, Any]:
        return deepcopy(self._public_config)

    async def complete(self, request: dict[str, object]) -> GraphQualityChatResult:
        if not isinstance(request, dict) or request.get("model") != self.model:
            raise GraphQualityTransportError("chat request model identity mismatch")
        messages = request.get("messages")
        if not isinstance(messages, list) or not messages:
            raise GraphQualityTransportError("chat request messages are invalid")
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(**deepcopy(request)),
                timeout=self._timeout_seconds,
            )
        except Exception as error:
            raise GraphQualityTransportError(
                f"chat completion failed: {type(error).__module__}.{type(error).__name__}"
            ) from None
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or len(choices) != 1:
            raise GraphQualityTransportError("chat response invalid: choices")
        choice = choices[0]
        content = getattr(getattr(choice, "message", None), "content", None)
        finish_reason = getattr(choice, "finish_reason", None)
        if not isinstance(content, str) or not content.strip():
            raise GraphQualityTransportError("chat response invalid: content")
        if not isinstance(finish_reason, str) or not finish_reason:
            raise GraphQualityTransportError("chat response invalid: finish_reason")
        usage = getattr(response, "usage", None)
        return GraphQualityChatResult(
            content=content,
            prompt_tokens=_nonnegative_int(
                getattr(usage, "prompt_tokens", None), field="prompt_tokens"
            ),
            completion_tokens=_nonnegative_int(
                getattr(usage, "completion_tokens", None),
                field="completion_tokens",
            ),
            finish_reason=finish_reason,
        )

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            result = close()
            if result is not None and hasattr(result, "__await__"):
                await result


__all__ = [
    "GraphQualityChatResult",
    "GraphQualityTransport",
    "GraphQualityTransportError",
]

