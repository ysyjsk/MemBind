"""Protocol-owned cache isolation with no request admission behavior."""

from __future__ import annotations

import inspect
import re
from typing import Any


_CACHE_SALT = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


class CacheSaltError(ValueError):
    """A fixed cache salt cannot be installed transparently."""


def _validate_cache_salt(cache_salt: str) -> str:
    if not isinstance(cache_salt, str) or _CACHE_SALT.fullmatch(cache_salt) is None:
        raise CacheSaltError("CACHE_SALT_INVALID")
    return cache_salt


class _SaltedCreate:
    def __init__(self, inner: Any, cache_salt: str) -> None:
        create = getattr(inner, "create", None)
        if not callable(create):
            raise CacheSaltError("CACHE_SALT_CREATE_SURFACE_MISSING")
        self._inner = inner
        self._cache_salt = cache_salt

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def create(self, *args: object, **kwargs: object) -> Any:
        request = dict(kwargs)
        extra_body = dict(request.get("extra_body") or {})
        extra_body["cache_salt"] = self._cache_salt
        request["extra_body"] = extra_body
        result = self._inner.create(*args, **request)
        if not inspect.isawaitable(result):
            raise CacheSaltError("CACHE_SALT_TRANSPORT_NOT_ASYNC")
        return await result


class _SaltedChat:
    def __init__(self, inner: Any, cache_salt: str) -> None:
        self._inner = inner
        self.completions = _SaltedCreate(inner.completions, cache_salt)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class SaltedOpenAITransport:
    """Delegate an OpenAI client unchanged except for ``extra_body.cache_salt``."""

    def __init__(self, inner: Any, cache_salt: str) -> None:
        self._inner = inner
        self.cache_salt = _validate_cache_salt(cache_salt)
        if hasattr(inner, "chat"):
            self.chat = _SaltedChat(inner.chat, self.cache_salt)
        if hasattr(inner, "embeddings"):
            self.embeddings = _SaltedCreate(inner.embeddings, self.cache_salt)
        if not hasattr(self, "chat") and not hasattr(self, "embeddings"):
            raise CacheSaltError("CACHE_SALT_TRANSPORT_SURFACE_MISSING")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _wrap_client(component: Any, cache_salt: str, code: str) -> None:
    client = getattr(component, "client", None)
    if client is None:
        raise CacheSaltError(code)
    if isinstance(client, SaltedOpenAITransport):
        if client.cache_salt != cache_salt:
            raise CacheSaltError("CACHE_SALT_REINSTALL_MISMATCH")
        return
    component.client = SaltedOpenAITransport(client, cache_salt)


def install_runtime_cache_salt(runtime: Any, cache_salt: str) -> Any:
    """Install salt on construction, embedding, and reranker transports only."""

    salt = _validate_cache_salt(cache_salt)
    _wrap_client(
        getattr(runtime, "llm_client", None),
        salt,
        "CONSTRUCTION_CACHE_SALT_TRANSPORT_UNAVAILABLE",
    )
    _wrap_client(
        getattr(runtime, "embedder", None),
        salt,
        "EMBEDDING_CACHE_SALT_TRANSPORT_UNAVAILABLE",
    )
    reranker = getattr(runtime, "reranker", None)
    if reranker is not None:
        _wrap_client(reranker, salt, "RERANKER_CACHE_SALT_TRANSPORT_UNAVAILABLE")
    return runtime


__all__ = [
    "CacheSaltError",
    "SaltedOpenAITransport",
    "install_runtime_cache_salt",
]
