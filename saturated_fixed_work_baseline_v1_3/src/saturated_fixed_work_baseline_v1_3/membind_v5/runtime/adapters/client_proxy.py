"""Thin logical capture/replay proxy around the pinned LLM client."""

from __future__ import annotations

import copy
import hashlib
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable

from ..core.binder import BindingScopeError, NativeBindingScope
from ..core.request_identity import build_request_identity
from ..core.transcript import CaptureSession, TranscriptStore


CERTIFIED_CALLSITES = frozenset(
    {
        "extract_nodes.extract_message",
        "extract_nodes.extract_text",
        "extract_nodes.extract_json",
        "extract_edges.edge",
    }
)


def _client_identity(client: Any) -> dict[str, Any]:
    source = inspect.getsourcefile(type(client)) or "unknown"
    try:
        source_hash = hashlib.sha256(open(source, "rb").read()).hexdigest() if source != "unknown" else "unknown"
    except OSError:
        source_hash = "unknown"
    return {"class": f"{type(client).__module__}.{type(client).__qualname__}", "source_hash": source_hash}


@dataclass
class V5LLMClientProxy:
    delegate: Any
    store: TranscriptStore
    source_sequence: int
    mode: str
    client_identity: dict[str, Any] | None = None
    transport_identity: dict[str, Any] | None = None
    cache_salt: str = ""
    previous_context_digest: str = ""

    def __post_init__(self) -> None:
        if self.mode not in {"capture", "replay"}:
            raise ValueError("proxy mode must be capture or replay")
        self._ordinals: dict[tuple[int, str], int] = {}
        if self.client_identity is None:
            self.client_identity = _client_identity(self.delegate)
        if self.transport_identity is None:
            self.transport_identity = {"top_p": 1.0, "seed": 20260806}

    def _identity(self, messages: Any, response_model: Any, max_tokens: int | None, model_size: Any, group_id: str | None, prompt_name: str | None, attribute_extraction: bool) -> Any:
        callsite = str(prompt_name or "unknown")
        key = (int(self.source_sequence), callsite)
        ordinal = self._ordinals.get(key, 0)
        self._ordinals[key] = ordinal + 1
        return build_request_identity(
            source_sequence=self.source_sequence,
            callsite=callsite,
            ordinal=ordinal,
            messages=copy.deepcopy(messages),
            response_model=response_model,
            max_tokens=max_tokens,
            model_size=getattr(model_size, "value", str(model_size)),
            group_id=group_id,
            prompt_name=prompt_name,
            flags={"attribute_extraction": bool(attribute_extraction)},
            client_identity=self.client_identity,
            transport_identity=self.transport_identity,
            cache_salt=self.cache_salt,
            previous_context_digest=self.previous_context_digest,
        )

    async def generate_response(
        self,
        messages: list[Any],
        response_model: Any = None,
        max_tokens: int | None = None,
        model_size: Any = None,
        group_id: str | None = None,
        prompt_name: str | None = None,
        *,
        attribute_extraction: bool = False,
    ) -> Any:
        identity = self._identity(messages, response_model, max_tokens, model_size, group_id, prompt_name, attribute_extraction)
        immutable_messages = copy.deepcopy(messages)

        async def delegate() -> Any:
            return await self.delegate.generate_response(
                immutable_messages,
                response_model=response_model,
                max_tokens=max_tokens,
                model_size=model_size,
                group_id=group_id,
                prompt_name=prompt_name,
                attribute_extraction=attribute_extraction,
            )

        certified = prompt_name in CERTIFIED_CALLSITES
        if self.mode == "capture":
            response = await delegate()
            self.store.capture(identity, response, transport_attempts=1)
            return copy.deepcopy(response)
        scope = NativeBindingScope.current()
        if scope is None and certified:
            raise BindingScopeError("certified Graphiti call outside V5 native scope")
        if scope is None:
            return await delegate()
        return await scope.invoke(identity, delegate, certified=certified)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)
