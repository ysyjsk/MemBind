"""V6 provider wrapper over the already-qualified V5 client seam."""

from __future__ import annotations

import contextvars
import hashlib
import inspect
from typing import Any, Callable, Mapping

from ..membind_v5.runtime.adapters.client_proxy import (
    CERTIFIED_CALLSITES,
    V5LLMClientProxy,
    proxy_source_scope,
)
from ..membind_v5.runtime.core.admission import AdmissionArbiter, AdmissionClass
from ..membind_v5.runtime.core.provider_admission import current_provider_scope
from ..membind_v5.runtime.core.transcript import TranscriptStore
from .request_observation import RequestObservation, observe_request_identity


class V6ProviderError(RuntimeError):
    pass


_IDENTITY: contextvars.ContextVar[Any | None] = contextvars.ContextVar("membind_v6_provider_identity", default=None)


class V6ProviderClient:
    """Admit every non-certified provider call and observe every request.

    ``mode=capture`` is used by matched control and shadow preparation.  In
    ``mode=replay`` only the explicitly certified callsite set bypasses the
    provider; all other native calls remain real provider work.
    """

    def __init__(
        self,
        delegate: Any,
        *,
        store: TranscriptStore,
        arbiter: AdmissionArbiter,
        mode: str,
        durable_frontier: Callable[[], int],
        client_identity: Mapping[str, Any] | None = None,
        certified_callsites: frozenset[str] = CERTIFIED_CALLSITES,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if mode not in {"capture", "replay"}:
            raise ValueError("V6 provider mode must be capture or replay")
        self.mode = mode
        self.store = store
        self.arbiter = arbiter
        self.durable_frontier = durable_frontier
        self.event_sink = event_sink
        self.certified_callsites = frozenset(certified_callsites)
        self.observations: list[dict[str, Any]] = []
        self.provider_calls: list[dict[str, Any]] = []
        identity = client_identity or {
            "class": f"{type(delegate).__module__}.{type(delegate).__qualname__}",
            "source_hash": hashlib.sha256(inspect.getsource(type(delegate)).encode()).hexdigest()
            if inspect.isclass(type(delegate))
            else "unknown",
        }
        self._proxy = V5LLMClientProxy(
            delegate,
            store,
            source_sequence=0,
            mode=mode,
            client_identity=dict(identity),
            identity_sink=self._observe_identity,
            certified_callsites=self.certified_callsites,
        )

    def _observe_identity(self, identity: Any) -> None:
        observation = observe_request_identity(identity)
        region, source = current_provider_scope()
        row = {
            "region": region,
            "source_sequence": source,
            "mode": self.mode,
            "public_summary": dict(observation.public_summary),
            "observation": observation,
        }
        self.observations.append(row)
        _IDENTITY.set(identity)

    def _class(self, source_sequence: int, region: str) -> AdmissionClass:
        if region == "NATIVE":
            return AdmissionClass.NATIVE_FRONTIER
        frontier = int(self.durable_frontier())
        return AdmissionClass.FRONTIER_PREPARE if int(source_sequence) == frontier + 1 else AdmissionClass.FUTURE_PREPARE

    def _record(self, *, region: str, source_sequence: int, prompt_name: Any, admission_class: AdmissionClass | None, replay: bool, status: str, error: BaseException | None = None) -> None:
        identity = _IDENTITY.get()
        row: dict[str, Any] = {
            "mode": self.mode,
            "region": region,
            "source_sequence": int(source_sequence),
            "prompt_name": prompt_name,
            "admission_class": admission_class.value if admission_class else None,
            "replay": bool(replay),
            "status": status,
        }
        if identity is not None:
            row.update({"request_digest_prefix": identity.digest[:16], "callsite": identity.callsite, "ordinal": identity.ordinal})
        if error is not None:
            row["error_type"] = f"{type(error).__module__}.{type(error).__qualname__}"
            row["error_message_digest"] = hashlib.sha256(str(error).encode("utf-8", errors="backslashreplace")).hexdigest()[:16]
        self.provider_calls.append(row)
        if self.event_sink is not None:
            self.event_sink({"event": "V6_PROVIDER_CALL", **row})
        _IDENTITY.set(None)

    async def generate_response(self, messages: list[Any], **kwargs: Any) -> Any:
        region, source_sequence = current_provider_scope()
        if region is None or source_sequence is None:
            raise V6ProviderError("provider call outside V6 source scope")
        prompt_name = kwargs.get("prompt_name")
        certified = prompt_name in self.certified_callsites
        if self.mode == "replay" and certified:
            try:
                with proxy_source_scope(source_sequence):
                    result = await self._proxy.generate_response(messages, **kwargs)
            except BaseException as exc:
                self._record(region=region, source_sequence=source_sequence, prompt_name=prompt_name, admission_class=None, replay=True, status="failure", error=exc)
                raise
            self._record(region=region, source_sequence=source_sequence, prompt_name=prompt_name, admission_class=None, replay=True, status="success")
            return result
        admission_class = self._class(source_sequence, region)
        admitted = await self.arbiter.acquire(admission_class, source_sequence=source_sequence, class_resolver=lambda: self._class(source_sequence, region))
        try:
            with proxy_source_scope(source_sequence):
                result = await self._proxy.generate_response(messages, **kwargs)
            self._record(region=region, source_sequence=source_sequence, prompt_name=prompt_name, admission_class=admitted, replay=False, status="success")
            return result
        except BaseException as exc:
            self._record(region=region, source_sequence=source_sequence, prompt_name=prompt_name, admission_class=admitted, replay=False, status="failure", error=exc)
            raise
        finally:
            await self.arbiter.release(admitted)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._proxy, name)


__all__ = ["V6ProviderClient", "V6ProviderError"]
