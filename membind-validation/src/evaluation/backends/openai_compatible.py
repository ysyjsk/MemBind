"""Dependency-injected OpenAI-compatible judge transport.

The SDK's hidden retry is disabled. A small explicit loop retries only
infrastructure failures, while benchmark outputs (including NO or malformed
text) are returned exactly once to the benchmark-owned parser.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
)

from evaluation.backends.base import JudgeBackendResult


Sleep = Callable[[float], Awaitable[None]]


class JudgeBackendConfigurationError(ValueError):
    """Raised without echoing credentials or private endpoint contents."""


def canonical_json_sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _normalized_v1_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise JudgeBackendConfigurationError("judge base URL is invalid")
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
        raise JudgeBackendConfigurationError("judge base URL must be an absolute /v1 URL")
    return urlunsplit((parsed.scheme, parsed.netloc, "/v1/", "", ""))


def _endpoint_identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _error_class(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__name__}"


def _status_code(error: BaseException) -> int | None:
    value = getattr(error, "status_code", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _retryable(error: BaseException) -> bool:
    if isinstance(
        error,
        (
            asyncio.TimeoutError,
            TimeoutError,
            ConnectionError,
            APIConnectionError,
            APITimeoutError,
            httpx.TransportError,
        ),
    ):
        return True
    status = _status_code(error)
    return status == 429 or (status is not None and 500 <= status <= 599)


class OpenAICompatibleJudgeBackend:
    """Minimal async Chat Completions backend with explicit retry semantics."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        temperature: int | float = 0,
        max_tokens: int = 10,
        n: int = 1,
        enable_thinking: bool | None = None,
        thinking_control: str = "not_applicable",
        max_attempts: int = 1,
        timeout_seconds: float = 30.0,
        retry_delays: tuple[float, ...] = (0.0,),
        sleep: Sleep = asyncio.sleep,
        client: Any | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not isinstance(model, str) or not model:
            raise JudgeBackendConfigurationError("judge model is invalid")
        if not isinstance(api_key, str) or not api_key:
            raise JudgeBackendConfigurationError("judge API key is missing")
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or max_attempts < 1
        ):
            raise JudgeBackendConfigurationError("max_attempts must be positive")
        if (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or max_tokens < 1
            or isinstance(n, bool)
            or not isinstance(n, int)
            or n != 1
        ):
            raise JudgeBackendConfigurationError("judge request limits are invalid")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise JudgeBackendConfigurationError("timeout_seconds must be positive")
        if not retry_delays or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value < 0
            for value in retry_delays
        ):
            raise JudgeBackendConfigurationError("retry_delays are invalid")
        if enable_thinking not in {None, False, True}:
            raise JudgeBackendConfigurationError("enable_thinking is invalid")

        normalized = _normalized_v1_url(base_url)
        self.model = model
        self._max_attempts = max_attempts
        self._timeout_seconds = float(timeout_seconds)
        self._retry_delays = tuple(float(value) for value in retry_delays)
        self._sleep = sleep
        self._request: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "n": n,
        }
        if enable_thinking is not None:
            self._request["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": enable_thinking}
            }
        public_config = {
            "backend": "openai_compatible_chat_completions",
            "served_model_name": model,
            "endpoint_identity_sha256": _endpoint_identity(normalized),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "n": n,
            "thinking_control": thinking_control,
            "effective_enable_thinking": enable_thinking,
            "max_attempts": max_attempts,
            "timeout_seconds": float(timeout_seconds),
            "retry_delays_seconds": list(self._retry_delays),
            "sdk_hidden_retries": 0,
        }
        self._public_config = public_config
        self.config_hash = canonical_json_sha256(self._public_config)
        self._http_client: httpx.AsyncClient | None = None
        if client is None:
            timeout = httpx.Timeout(
                connect=min(5.0, float(timeout_seconds)),
                read=float(timeout_seconds),
                write=float(timeout_seconds),
                pool=float(timeout_seconds),
            )
            self._http_client = httpx.AsyncClient(
                transport=transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            )
            self._client = AsyncOpenAI(
                api_key=api_key,
                base_url=normalized,
                timeout=timeout,
                max_retries=0,
                http_client=self._http_client,
            )
            # Avoid the SDK's first-request worker-thread platform probe in
            # restricted CI; the deployment and validation host are Linux.
            self._client._platform = "Linux"  # type: ignore[attr-defined]
        else:
            hidden_retries = getattr(client, "max_retries", None)
            if hidden_retries != 0:
                raise JudgeBackendConfigurationError(
                    "injected judge clients must disable hidden retries"
                )
            self._client = client

    @property
    def public_config(self) -> dict[str, Any]:
        """Return an artifact-safe copy bound by ``config_hash``."""

        return deepcopy(self._public_config)

    async def judge(self, prompt: str) -> JudgeBackendResult:
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("judge prompt must be a non-empty string")
        request = deepcopy(self._request)
        request["messages"] = [{"role": "user", "content": prompt}]
        for attempt in range(self._max_attempts):
            try:
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(**request),
                    timeout=self._timeout_seconds,
                )
                choices = getattr(response, "choices", None)
                if not isinstance(choices, list) or len(choices) != 1:
                    raise ValueError("judge response shape invalid")
                raw = getattr(getattr(choices[0], "message", None), "content", None)
                if not isinstance(raw, str):
                    raise ValueError("judge response content invalid")
                return JudgeBackendResult.success(
                    raw_output=raw,
                    retry_count=attempt,
                )
            except Exception as error:
                if _retryable(error) and attempt + 1 < self._max_attempts:
                    delay = self._retry_delays[min(attempt, len(self._retry_delays) - 1)]
                    await self._sleep(delay)
                    continue
                return JudgeBackendResult.service_error(
                    retry_count=attempt,
                    error_class=_error_class(error),
                )
        raise AssertionError("unreachable retry loop")

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            result = close()
            if result is not None and hasattr(result, "__await__"):
                await result


class Qwen3JudgeBackend(OpenAICompatibleJudgeBackend):
    """Frozen Qwen3-32B-FP8 configuration over the generic transport."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        thinking_control: str = "client_request",
        **kwargs: Any,
    ) -> None:
        if thinking_control == "client_request":
            enable_thinking: bool | None = False
        elif thinking_control == "server_side":
            enable_thinking = None
        else:
            raise JudgeBackendConfigurationError("thinking_control is invalid")
        super().__init__(
            model="qwen3-32b-fp8",
            base_url=base_url,
            api_key=api_key,
            temperature=0,
            max_tokens=10,
            n=1,
            enable_thinking=enable_thinking,
            thinking_control=thinking_control,
            **kwargs,
        )
        if thinking_control == "server_side":
            self._public_config["effective_enable_thinking"] = False
            self.config_hash = canonical_json_sha256(self._public_config)
