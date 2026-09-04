"""Exact upstream Graphiti capture/replay with resource-credit admission.

This facade preserves the upstream prompt, response model, and call graph. It
captures only the certified node/edge extraction responses and consumes each
of them exactly once during Native publication. Every other Graphiti call is
executed normally through the shared upstream client.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import time
from typing import Any, Callable, Mapping

from ..membind_v5.runtime.adapters.client_proxy import (
    CERTIFIED_CALLSITES,
    V5LLMClientProxy,
    proxy_source_scope,
)
from ..membind_v5.runtime.core.admission import AdmissionClass
from ..membind_v5.runtime.core.provider_admission import current_provider_scope
from ..membind_v5.runtime.core.request_identity import RequestIdentity
from ..membind_v5.runtime.core.transcript import TranscriptStore
from .admission import ForegroundAdmissionArbiter


_CURRENT_IDENTITY: contextvars.ContextVar[RequestIdentity | None] = (
    contextvars.ContextVar("membind_upstream_replay_identity", default=None)
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(child) for child in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            _jsonable(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _public_identity(identity: RequestIdentity) -> dict[str, Any]:
    return {
        "schema_version": "membind.upstream-logical-request.v1",
        "source_sequence": identity.source_sequence,
        "callsite": identity.callsite,
        "ordinal": identity.ordinal,
        "prompt_name": identity.prompt_name,
        "max_tokens": identity.max_tokens,
        "messages_sha256": _sha256(identity.payload()["messages"]),
        "response_model_sha256": _sha256(identity.payload()["response_model"]),
        "request_identity_sha256": identity.digest,
    }


class UpstreamReplayClient:
    """Single-attempt exact replay facade for the formal upstream C arm."""

    def __init__(
        self,
        delegate: Any,
        *,
        store: TranscriptStore,
        admission: ForegroundAdmissionArbiter,
        mode: str,
        durable_frontier: Callable[[], int],
        client_identity: Mapping[str, Any] | None = None,
        transport_identity: Mapping[str, Any] | None = None,
        certified_callsites: frozenset[str] = CERTIFIED_CALLSITES,
    ) -> None:
        if mode not in {"capture", "replay"}:
            raise ValueError("upstream replay mode must be capture or replay")
        self.delegate = delegate
        self.store = store
        self.admission = admission
        self.mode = mode
        self.durable_frontier = durable_frontier
        self.certified_callsites = frozenset(certified_callsites)
        self.provider_calls: list[dict[str, Any]] = []
        self.request_identities: list[dict[str, Any]] = []
        self._proxy = V5LLMClientProxy(
            delegate=delegate,
            store=store,
            source_sequence=0,
            mode=mode,
            client_identity=dict(client_identity) if client_identity is not None else None,
            transport_identity=(
                dict(transport_identity) if transport_identity is not None else None
            ),
            identity_sink=self._observe_identity,
            certified_callsites=self.certified_callsites,
        )

    def _observe_identity(self, identity: RequestIdentity) -> None:
        _CURRENT_IDENTITY.set(identity)
        self.request_identities.append(_public_identity(identity))

    def _admission_class(self, *, region: str, source_sequence: int) -> AdmissionClass:
        if region == "NATIVE":
            return AdmissionClass.NATIVE_FRONTIER
        return (
            AdmissionClass.FRONTIER_PREPARE
            if source_sequence == int(self.durable_frontier()) + 1
            else AdmissionClass.FUTURE_PREPARE
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
        region, source = current_provider_scope()
        if region not in {"PREPARE", "NATIVE"} or not isinstance(source, int):
            raise RuntimeError("upstream replay requires explicit provider source scope")
        certified = prompt_name in self.certified_callsites
        replay = self.mode == "replay" and certified
        admission_class = self._admission_class(
            region=region, source_sequence=source
        )
        source_lease = None
        permit = None
        start_ns = time.monotonic_ns()
        identity_token = _CURRENT_IDENTITY.set(None)
        result: Any = None
        error: BaseException | None = None
        try:
            if not replay:
                if region == "PREPARE":
                    source_lease = await self.admission.acquire_source_lease(
                        admission_class,
                        source_sequence=source,
                        class_resolver=lambda: self._admission_class(
                            region=region, source_sequence=source
                        ),
                    )
                permit = await self.admission.acquire_physical(
                    admission_class,
                    source_sequence=source,
                    request_tokens=1,
                    class_resolver=lambda: self._admission_class(
                        region=region, source_sequence=source
                    ),
                )
            with proxy_source_scope(source):
                result = await self._proxy.generate_response(
                    messages,
                    response_model=response_model,
                    max_tokens=max_tokens,
                    model_size=model_size,
                    group_id=group_id,
                    prompt_name=prompt_name,
                    attribute_extraction=attribute_extraction,
                )
            return result
        except BaseException as exc:
            error = exc
            raise
        finally:
            if permit is not None:
                await self.admission.release_physical(permit)
            if source_lease is not None:
                await self.admission.release_source_lease(source_lease)
            identity = _CURRENT_IDENTITY.get()
            self.provider_calls.append(
                {
                    "schema_version": "membind.upstream-provider-call.v1",
                    "mode": self.mode,
                    "region": region,
                    "source_sequence": source,
                    "prompt_name": prompt_name,
                    "admission_class": admission_class.value,
                    "replay": replay,
                    "status": "failure" if error is not None else "success",
                    "request_identity_sha256": (
                        identity.digest if identity is not None else None
                    ),
                    "callsite": identity.callsite if identity is not None else None,
                    "ordinal": identity.ordinal if identity is not None else None,
                    "response_sha256": _sha256(result) if error is None else None,
                    "physical_attempt_count": 0 if replay else 1,
                    "transport_retry_count": 0,
                    "start_ns": start_ns,
                    "end_ns": time.monotonic_ns(),
                    "exception_type": (
                        f"{type(error).__module__}.{type(error).__qualname__}"
                        if error is not None
                        else None
                    ),
                }
            )
            _CURRENT_IDENTITY.reset(identity_token)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)


__all__ = ["UpstreamReplayClient"]
