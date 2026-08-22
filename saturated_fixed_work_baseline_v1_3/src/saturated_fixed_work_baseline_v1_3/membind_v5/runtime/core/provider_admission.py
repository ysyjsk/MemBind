"""Provider-call-level admission around the pinned Graphiti LLM client seam."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Awaitable, Callable

from .admission import AdmissionArbiter, AdmissionClass
from .transcript import TranscriptStore
from ..adapters.client_proxy import V5LLMClientProxy, CERTIFIED_CALLSITES, proxy_source_scope


class ProviderAdmissionError(RuntimeError):
    pass


_region: contextvars.ContextVar[str | None] = contextvars.ContextVar("membind_v5_provider_region", default=None)
_source: contextvars.ContextVar[int | None] = contextvars.ContextVar("membind_v5_provider_source", default=None)


@contextmanager
def provider_scope(*, region: str, source_sequence: int) -> Any:
    if region not in {"PREPARE", "NATIVE"}:
        raise ProviderAdmissionError("invalid provider region")
    region_token = _region.set(region)
    source_token = _source.set(int(source_sequence))
    try:
        yield
    finally:
        _source.reset(source_token)
        _region.reset(region_token)


def current_provider_scope() -> tuple[str | None, int | None]:
    return _region.get(), _source.get()


class FrontierAwareLLMClient:
    """Wrap the exact Graphiti logical client, admitting every real provider call.

    Certified replay hits consume a transcript and never acquire a provider permit.
    Capture calls and uncertified native calls acquire permits immediately around the
    delegated provider execution.  The classifier reads the live durable frontier,
    so ``FRONTIER_PREPARE`` is always the currently blocking source ``d+1``.
    """

    def __init__(
        self,
        delegate: Any,
        *,
        store: TranscriptStore,
        arbiter: AdmissionArbiter,
        mode: str,
        durable_frontier: Callable[[], int],
        client_identity: dict[str, Any] | None = None,
    ) -> None:
        if mode not in {"capture", "replay"}:
            raise ValueError("mode must be capture or replay")
        self.store = store
        self.arbiter = arbiter
        self.mode = mode
        self.durable_frontier = durable_frontier
        self._proxy = V5LLMClientProxy(
            delegate,
            store,
            source_sequence=0,
            mode=mode,
            client_identity=client_identity,
        )
        self.provider_calls: list[dict[str, Any]] = []

    def source_scope(self, source_sequence: int):
        return provider_scope(region="PREPARE" if self.mode == "capture" else "NATIVE", source_sequence=source_sequence)

    def _admission_class(self, source_sequence: int, region: str) -> AdmissionClass:
        if region == "NATIVE":
            return AdmissionClass.NATIVE_FRONTIER
        frontier = int(self.durable_frontier())
        return AdmissionClass.FRONTIER_PREPARE if int(source_sequence) == frontier + 1 else AdmissionClass.FUTURE_PREPARE

    async def generate_response(self, messages: list[Any], **kwargs: Any) -> Any:
        region, source_sequence = current_provider_scope()
        if region is None or source_sequence is None:
            raise ProviderAdmissionError("Graphiti provider call outside source scope")
        prompt_name = kwargs.get("prompt_name")
        certified = prompt_name in CERTIFIED_CALLSITES
        # Replay certified calls are provider-free exact transcript hits.
        if self.mode == "replay" and certified:
            with proxy_source_scope(source_sequence):
                result = await self._proxy.generate_response(messages, **kwargs)
            self.provider_calls.append({"mode": self.mode, "region": region, "source_sequence": source_sequence, "prompt_name": prompt_name, "admitted": False, "replay": True})
            return result

        admission_class = self._admission_class(source_sequence, region)
        await self.arbiter.acquire(admission_class, source_sequence=source_sequence)
        try:
            with proxy_source_scope(source_sequence):
                result = await self._proxy.generate_response(messages, **kwargs)
            self.provider_calls.append({"mode": self.mode, "region": region, "source_sequence": source_sequence, "prompt_name": prompt_name, "admission_class": admission_class.value, "admitted": True, "replay": False})
            return result
        finally:
            await self.arbiter.release(admission_class)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._proxy, name)
