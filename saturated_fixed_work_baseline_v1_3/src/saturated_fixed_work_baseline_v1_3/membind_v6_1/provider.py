"""V6.1 capture/replay wrapper with shared admission and response evidence."""

from __future__ import annotations

import contextvars
import hashlib
import inspect
import json
import math
import re
import time
from copy import deepcopy
from functools import wraps
from collections.abc import Sequence
from typing import Any, Callable, Mapping

from ..membind_v5.runtime.adapters.client_proxy import (
    CERTIFIED_CALLSITES,
    V5LLMClientProxy,
    proxy_source_scope,
)
from ..membind_v5.runtime.core.admission import AdmissionClass
from ..membind_v5.runtime.core._canonical import canonical_json
from ..membind_v5.runtime.core.provider_admission import (
    current_provider_scope,
    provider_request_scope,
)
from ..membind_v5.runtime.core.transcript import BindingMismatch, TranscriptStore
from ..membind_v6.request_observation import observe_request_identity
from .admission import ForegroundAdmissionArbiter
from .evidence import response_sha256
from .structured_output_recovery import (
    RECOVERY_POLICY_MAX_TRANSIENT_RETRIES,
    classify_exception_for_recovery,
    reliability_identity,
    recovery_policy_sha256,
)


class V61ProviderError(RuntimeError):
    pass


_IDENTITY: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "membind_v6_1_provider_identity", default=None
)
_OBSERVATION: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "membind_v6_1_provider_observation", default=None
)
_MANAGED_TRANSPORT_CALL: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar(
    "membind_v6_1_managed_transport_call", default=None
)
_MANAGED_TRANSPORT_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "membind_v6_1_managed_transport_depth", default=0
)
_RECOVERY_ATTEMPT: contextvars.ContextVar[int] = contextvars.ContextVar(
    "membind_v6_1_recovery_attempt", default=0
)
_PREVIOUS_CONTEXT_PATTERNS = (
    re.compile(
        r"(<PREVIOUS MESSAGES>)(?P<body>.*?)(</PREVIOUS MESSAGES>)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(<PREVIOUS_MESSAGES>)(?P<body>.*?)(</PREVIOUS_MESSAGES>)",
        re.IGNORECASE | re.DOTALL,
    ),
)
_INCREMENTAL_SUMMARY_CALLSITES = frozenset(
    {
        "extract_nodes.extract_summaries_batch",
        "extract_nodes.extract_entity_summaries_from_episodes",
    }
)
_SUMMARY_CONTEXT_TAGS = {
    "extract_nodes.extract_summaries_batch": "MESSAGES",
    "extract_nodes.extract_entity_summaries_from_episodes": "EPISODES",
}


def strip_certified_previous_context(
    messages: Sequence[Any],
) -> tuple[list[Any], dict[str, Any]]:
    """Remove non-authoritative history from current-evidence extraction prompts."""

    cloned = deepcopy(list(messages))
    removed_chars = 0
    block_count = 0
    for message in cloned:
        if isinstance(message, Mapping):
            content = message.get("content")
        else:
            content = getattr(message, "content", None)
        if not isinstance(content, str):
            continue
        updated = content
        for pattern in _PREVIOUS_CONTEXT_PATTERNS:
            matches = list(pattern.finditer(updated))
            removed_chars += sum(len(match.group("body")) for match in matches)
            block_count += len(matches)
            updated = pattern.sub(lambda match: f"{match.group(1)}\n[]\n{match.group(3)}", updated)
        if updated == content:
            continue
        if isinstance(message, Mapping):
            message["content"] = updated
        else:
            setattr(message, "content", updated)
    return cloned, {
        "schema_version": "membind.v6.1.context-selection.v1",
        "event": "CERTIFIED_CONTEXT_SELECTION",
        "policy": "current_evidence_only_certified_extraction_v1",
        "previous_context_block_count": block_count,
        "previous_context_chars_removed": removed_chars,
        "retained_previous_episode_count": 0,
    }


def _tagged_json_body(content: str, tag: str) -> tuple[re.Match[str], str]:
    pattern = re.compile(
        rf"(?P<open><{re.escape(tag)}>)(?P<body>.*?)(?P<close></{re.escape(tag)}>)",
        re.IGNORECASE | re.DOTALL,
    )
    matches = list(pattern.finditer(content))
    if len(matches) != 1:
        raise V61ProviderError(f"incremental summary requires one <{tag}> block")
    match = matches[0]
    return match, match.group("body")


def _decode_json_values(body: str, expected: int) -> list[tuple[Any, int, int]]:
    decoder = json.JSONDecoder()
    offset = 0
    values: list[tuple[Any, int, int]] = []
    for _ in range(expected):
        while offset < len(body) and body[offset].isspace():
            offset += 1
        try:
            value, end = decoder.raw_decode(body, offset)
        except json.JSONDecodeError as exc:
            raise V61ProviderError("incremental summary context is not structured JSON") from exc
        values.append((value, offset, end))
        offset = end
    if body[offset:].strip():
        raise V61ProviderError("incremental summary context has unexpected trailing payload")
    return values


def incremental_native_summary_context(
    messages: Sequence[Any], prompt_name: str | None
) -> tuple[list[Any], dict[str, Any]] | None:
    """Use durable entity summaries plus current evidence for Native summary updates."""

    if prompt_name not in _INCREMENTAL_SUMMARY_CALLSITES:
        return None
    cloned = deepcopy(list(messages))
    user_messages: list[tuple[Any, str]] = []
    for message in cloned:
        content = message.get("content") if isinstance(message, Mapping) else getattr(message, "content", None)
        if isinstance(content, str) and "<ENTITIES>" in content.upper():
            user_messages.append((message, content))
    if len(user_messages) != 1:
        raise V61ProviderError("incremental summary requires one entity-bearing message")
    message, content = user_messages[0]

    context_tag = _SUMMARY_CONTEXT_TAGS[str(prompt_name)]
    context_match, context_body = _tagged_json_body(content, context_tag)
    context_values = _decode_json_values(context_body, 2)
    previous_episodes, previous_start, previous_end = context_values[0]
    current_episode, _, _ = context_values[1]
    if not isinstance(previous_episodes, list) or not isinstance(current_episode, str):
        raise V61ProviderError("incremental summary context has an invalid episode shape")
    if not current_episode.strip():
        raise V61ProviderError("incremental summary requires non-empty current evidence")
    if any(
        not isinstance(episode, Mapping) or not isinstance(episode.get("content"), str)
        for episode in previous_episodes
    ):
        raise V61ProviderError("incremental summary previous episodes are malformed")

    entity_match, entity_body = _tagged_json_body(content, "ENTITIES")
    entity_values = _decode_json_values(entity_body, 1)
    entities = entity_values[0][0]
    if not isinstance(entities, list) or not entities:
        raise V61ProviderError("incremental summary requires a non-empty entity flight")
    if any(
        not isinstance(entity, Mapping)
        or not isinstance(entity.get("name"), str)
        or not isinstance(entity.get("summary", ""), str)
        for entity in entities
    ):
        raise V61ProviderError("incremental summary entity flight is malformed")

    previous_serialized = context_body[previous_start:previous_end]
    transformed_context = (
        context_body[:previous_start] + "[]" + context_body[previous_end:]
    )
    transformed_content = (
        content[: context_match.start("body")]
        + transformed_context
        + content[context_match.end("body") :]
    )
    if isinstance(message, Mapping):
        message["content"] = transformed_content
    else:
        setattr(message, "content", transformed_content)

    retained_summaries = [
        {"name": str(entity["name"]), "summary": str(entity.get("summary", ""))}
        for entity in entities
    ]
    retained_summary_chars = sum(len(entity["summary"]) for entity in retained_summaries)
    event = {
        "schema_version": "membind.v6.1.summary-context-selection.v1",
        "event": "NATIVE_SUMMARY_CONTEXT_SELECTION",
        "policy": "durable_summary_plus_current_evidence_v1",
        "previous_episode_count": len(previous_episodes),
        "retained_previous_episode_count": 0,
        "previous_context_chars_removed": max(0, len(previous_serialized) - 2),
        "previous_context_sha256": hashlib.sha256(
            canonical_json(previous_episodes).encode("utf-8")
        ).hexdigest(),
        "current_episode_chars_retained": len(current_episode),
        "current_episode_sha256": hashlib.sha256(
            current_episode.encode("utf-8")
        ).hexdigest(),
        "entity_count": len(entities),
        "nonempty_existing_summary_count": sum(
            bool(entity["summary"].strip()) for entity in retained_summaries
        ),
        "existing_summary_chars_retained": retained_summary_chars,
        "existing_summaries_sha256": hashlib.sha256(
            canonical_json(retained_summaries).encode("utf-8")
        ).hexdigest(),
        "transformed_messages_sha256": hashlib.sha256(
            canonical_json(cloned).encode("utf-8")
        ).hexdigest(),
    }
    return cloned, event


def _managed_transport_counter() -> dict[str, int] | None:
    """Return the counter owned by the current V6.1 logical provider call."""

    return _MANAGED_TRANSPORT_CALL.get()


def _record_managed_transport_retry() -> bool:
    """Account for a retry performed inside the local context-budget adapter."""

    counter = _managed_transport_counter()
    if counter is None:
        return False
    counter["attempts"] += 1
    counter["retries"] += 1
    return True


def _ensure_physical_attempt_lower_bound(
    counter: dict[str, int], logical_attempts: int
) -> int:
    """Close the accounting gap when a fake delegate bypasses the transport guard.

    A logical provider invocation always performs at least one physical call.
    Real transports increment ``attempts`` in ``install_auxiliary_transport_guard``;
    provider-free delegates may not expose that seam, so retain the conservative
    lower bound without double-counting calls that were observed already.
    """

    observed = max(0, int(counter.get("attempts", 0)))
    required = max(1, int(logical_attempts) + 1)
    if observed < required:
        counter["attempts"] = required
    return int(counter["attempts"])


def _heuristic_transport_tokens(messages: Any) -> int:
    chars = 0
    for message in messages if isinstance(messages, (list, tuple)) else (messages,):
        if isinstance(message, Mapping):
            content = message.get("content")
        else:
            content = getattr(message, "content", None)
        if content is not None:
            chars += len(content) if isinstance(content, str) else len(str(content))
    return max(1, int(math.ceil(chars / 3.0)) + 16)


def install_routed_physical_admission(
    router: Any,
    *,
    arbiter: ForegroundAdmissionArbiter,
    durable_frontier: Callable[[], int],
    token_counter: Callable[[Sequence[Any]], int] | None = None,
) -> Callable[[], None]:
    """Install endpoint-aware physical admission at ``RoutedOpenAIClient``.

    The router selects an endpoint before invoking this hook.  Consequently a
    physical permit is charged to the actual endpoint dispatch rather than to
    the logical Graphiti wrapper that may expand into several transports.
    """

    if not hasattr(router, "_membind_physical_admission_enabled"):
        raise V61ProviderError("routed client physical admission seam is unavailable")
    if getattr(router, "_membind_physical_admission_enabled", False):
        raise V61ProviderError("routed client physical admission is already installed")

    def classify(region: str | None, source_sequence: int | None) -> AdmissionClass:
        if region == "NATIVE":
            return AdmissionClass.NATIVE_FRONTIER
        if region != "PREPARE" or source_sequence is None:
            raise V61ProviderError("routed physical call outside V6.1 source scope")
        frontier = int(durable_frontier())
        return (
            AdmissionClass.FRONTIER_PREPARE
            if int(source_sequence) == frontier + 1
            else AdmissionClass.FUTURE_PREPARE
        )

    async def acquire(**context: Any) -> Any:
        region = context.get("region")
        source_sequence = context.get("source_sequence")
        admission_class = classify(region, source_sequence)
        kwargs = context.get("kwargs")
        if not isinstance(kwargs, Mapping):
            kwargs = {}
        messages = kwargs.get("messages")
        try:
            prompt_tokens = (
                int(current_provider_request_tokens())
                if current_provider_request_tokens() is not None
                else int(token_counter(messages))
                if token_counter is not None
                else _heuristic_transport_tokens(messages)
            )
        except Exception:
            prompt_tokens = _heuristic_transport_tokens(messages)
        requested = int(kwargs.get("max_tokens") or 1)
        decode_reserve = min(max(1, requested), arbiter.policy.STRUCTURED_DECODE_RESERVE_TOKENS)
        request_tokens = max(1, prompt_tokens + decode_reserve)
        return await arbiter.acquire_physical(
            admission_class,
            source_sequence=int(source_sequence),
            request_tokens=request_tokens,
            prompt_tokens=prompt_tokens,
            decode_reserve_tokens=decode_reserve,
            endpoint_id=str(context.get("endpoint_id"))
            if context.get("endpoint_id") is not None
            else None,
            class_resolver=(
                lambda: classify(region, source_sequence)
                if region == "PREPARE"
                else AdmissionClass.NATIVE_FRONTIER
            ),
        )

    async def release(permit: Any) -> None:
        await arbiter.release_physical(permit)

    router._membind_physical_admission_acquire = acquire
    router._membind_physical_admission_release = release
    router._membind_physical_admission_enabled = True

    restored = False

    def restore() -> None:
        nonlocal restored
        if restored:
            return
        restored = True
        router._membind_physical_admission_enabled = False
        router._membind_physical_admission_acquire = None
        router._membind_physical_admission_release = None

    return restore


def install_auxiliary_transport_guard(
    transport: Any,
    *,
    arbiter: ForegroundAdmissionArbiter,
    token_counter: Callable[[Sequence[Any]], int] | None = None,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> Callable[[], None]:
    """Admit direct shared-transport calls (notably Graphiti reranker calls).

    ``V61ProviderClient`` owns normal Graphiti LLM calls.  The pinned
    ``OpenAIRerankerClient`` instead calls ``AsyncOpenAI.chat.completions``
    directly, which otherwise bypasses the arbiter while still consuming the
    same vLLM queue.  This narrow transport seam accounts and admits those
    auxiliary calls.  Calls made under the logical wrapper are marked in a
    ContextVar and pass through without a second permit.
    """

    completions = getattr(getattr(transport, "chat", None), "completions", None)
    original = getattr(completions, "create", None)
    if completions is None or not callable(original):
        raise V61ProviderError("shared transport completion seam is unavailable")
    restored = False

    @wraps(original)
    async def guarded_create(*args: Any, **kwargs: Any) -> Any:
        managed_counter = _managed_transport_counter()
        if managed_counter is not None:
            depth = _MANAGED_TRANSPORT_DEPTH.get()
            if depth == 0:
                managed_counter["attempts"] += 1
            depth_token = _MANAGED_TRANSPORT_DEPTH.set(depth + 1)
            try:
                return await original(*args, **kwargs)
            finally:
                _MANAGED_TRANSPORT_DEPTH.reset(depth_token)
        routed_physical = bool(
            getattr(transport, "_membind_physical_admission_enabled", False)
        )
        region, source_sequence = current_provider_scope()
        if region is None or source_sequence is None:
            raise V61ProviderError("auxiliary provider call outside V6.1 source scope")
        messages = kwargs.get("messages")
        try:
            prompt_tokens = int(token_counter(messages)) if token_counter is not None else _heuristic_transport_tokens(messages)
        except Exception:
            prompt_tokens = _heuristic_transport_tokens(messages)
        requested = int(kwargs.get("max_tokens") or 1)
        decode_reserve = min(max(1, requested), arbiter.policy.STRUCTURED_DECODE_RESERVE_TOKENS)
        request_tokens = max(1, prompt_tokens + decode_reserve)
        if routed_physical:
            # The router owns the endpoint-aware physical permit.  Keep this
            # wrapper only for auxiliary-call evidence and token context; the
            # normal V61 provider path is marked managed above and bypasses
            # this branch entirely.
            started_ns = time.monotonic_ns()
            with provider_request_scope(request_tokens=request_tokens):
                try:
                    result = await original(*args, **kwargs)
                except BaseException as exc:
                    ended_ns = time.monotonic_ns()
                    if event_sink is not None:
                        event_sink(
                            {
                                "event": "V61_PROVIDER_CALL",
                                "schema_version": "membind.v6.1.provider-call.v2",
                                "mode": "auxiliary",
                                "region": region,
                                "source_sequence": int(source_sequence),
                                "prompt_name": "cross_encoder.rank",
                                "callsite": "cross_encoder.rank",
                                "ordinal": 0,
                                "admission_class": None,
                                "replay": False,
                                "auxiliary": True,
                                "routed_physical": True,
                                "status": "failure",
                                "response_sha256": None,
                                "arbiter_instance_id": arbiter.instance_id,
                                "start_ns": started_ns,
                                "end_ns": ended_ns,
                                "duration_ns": ended_ns - started_ns,
                                "queue_wait_ns": 0,
                                "service_ns": ended_ns - started_ns,
                                "request_tokens": request_tokens,
                                "prompt_tokens": prompt_tokens,
                                "decode_reserve_tokens": decode_reserve,
                                "transport_attempt_count": 1,
                                "transport_retry_count": 0,
                                "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                            }
                        )
                    raise
            ended_ns = time.monotonic_ns()
            if event_sink is not None:
                event_sink(
                    {
                        "event": "V61_PROVIDER_CALL",
                        "schema_version": "membind.v6.1.provider-call.v2",
                        "mode": "auxiliary",
                        "region": region,
                        "source_sequence": int(source_sequence),
                        "prompt_name": "cross_encoder.rank",
                        "callsite": "cross_encoder.rank",
                        "ordinal": 0,
                        "admission_class": None,
                        "replay": False,
                        "auxiliary": True,
                        "routed_physical": True,
                        "status": "success",
                        "response_sha256": response_sha256(result),
                        "arbiter_instance_id": arbiter.instance_id,
                        "start_ns": started_ns,
                        "end_ns": ended_ns,
                        "duration_ns": ended_ns - started_ns,
                        "queue_wait_ns": 0,
                        "service_ns": ended_ns - started_ns,
                        "request_tokens": request_tokens,
                        "prompt_tokens": prompt_tokens,
                        "decode_reserve_tokens": decode_reserve,
                        "transport_attempt_count": 1,
                        "transport_retry_count": 0,
                    }
                )
            return result
        started_ns = time.monotonic_ns()
        admitted = await arbiter.acquire_physical(
            AdmissionClass.NATIVE_FRONTIER,
            source_sequence=int(source_sequence),
            request_tokens=request_tokens,
            prompt_tokens=prompt_tokens,
            decode_reserve_tokens=decode_reserve,
        )
        service_start_ns = time.monotonic_ns()
        transport_counter = {"attempts": 1, "retries": 0}
        managed_token = _MANAGED_TRANSPORT_CALL.set(transport_counter)
        try:
            with provider_request_scope(request_tokens=request_tokens):
                result = await original(*args, **kwargs)
        except BaseException as exc:
            ended_ns = time.monotonic_ns()
            row = {
                "event": "V61_PROVIDER_CALL",
                "schema_version": "membind.v6.1.provider-call.v2",
                "mode": "auxiliary",
                "region": region,
                "source_sequence": int(source_sequence),
                "prompt_name": "cross_encoder.rank",
                "callsite": "cross_encoder.rank",
                "ordinal": 0,
                "admission_class": admitted.admission_class.value,
                "replay": False,
                "auxiliary": True,
                "status": "failure",
                "response_sha256": None,
                "arbiter_instance_id": arbiter.instance_id,
                "start_ns": started_ns,
                "end_ns": ended_ns,
                "duration_ns": ended_ns - started_ns,
                "queue_wait_ns": max(0, service_start_ns - started_ns),
                "service_ns": max(0, ended_ns - service_start_ns),
                "request_tokens": request_tokens,
                "prompt_tokens": prompt_tokens,
                "decode_reserve_tokens": decode_reserve,
                "transport_attempt_count": int(transport_counter["attempts"]),
                "transport_retry_count": int(transport_counter["retries"]),
                "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
            }
            if event_sink is not None:
                event_sink(row)
            raise
        else:
            ended_ns = time.monotonic_ns()
            row = {
                "event": "V61_PROVIDER_CALL",
                "schema_version": "membind.v6.1.provider-call.v2",
                "mode": "auxiliary",
                "region": region,
                "source_sequence": int(source_sequence),
                "prompt_name": "cross_encoder.rank",
                "callsite": "cross_encoder.rank",
                "ordinal": 0,
                "admission_class": admitted.admission_class.value,
                "replay": False,
                "auxiliary": True,
                "status": "success",
                "response_sha256": response_sha256(result),
                "arbiter_instance_id": arbiter.instance_id,
                "start_ns": started_ns,
                "end_ns": ended_ns,
                "duration_ns": ended_ns - started_ns,
                "queue_wait_ns": max(0, service_start_ns - started_ns),
                "service_ns": max(0, ended_ns - service_start_ns),
                "request_tokens": request_tokens,
                "prompt_tokens": prompt_tokens,
                "decode_reserve_tokens": decode_reserve,
                "transport_attempt_count": int(transport_counter["attempts"]),
                "transport_retry_count": int(transport_counter["retries"]),
            }
            if event_sink is not None:
                event_sink(row)
            return result
        finally:
            _MANAGED_TRANSPORT_CALL.reset(managed_token)
            await arbiter.release_physical(admitted)

    setattr(completions, "create", guarded_create)

    def restore() -> None:
        nonlocal restored
        if restored:
            return
        restored = True
        setattr(completions, "create", original)

    return restore


class V61ProviderClient:
    """Replay certified extraction calls and admit all real calls once."""

    def __init__(
        self,
        delegate: Any,
        *,
        store: TranscriptStore,
        arbiter: ForegroundAdmissionArbiter,
        mode: str,
        durable_frontier: Callable[[], int],
        client_identity: Mapping[str, Any] | None = None,
        certified_callsites: frozenset[str] = CERTIFIED_CALLSITES,
        token_counter: Callable[[Sequence[Any]], int] | None = None,
        certified_message_transform: Callable[
            [Sequence[Any]], tuple[list[Any], dict[str, Any]]
        ]
        | None = None,
        native_message_transform: Callable[
            [Sequence[Any], str | None], tuple[list[Any], dict[str, Any]] | None
        ]
        | None = None,
        binding_strict: bool = True,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if mode not in {"capture", "replay"}:
            raise ValueError("V6.1 provider mode must be capture or replay")
        self.mode = mode
        self.store = store
        self.arbiter = arbiter
        self.durable_frontier = durable_frontier
        self.event_sink = event_sink
        self.certified_callsites = frozenset(certified_callsites)
        self.token_counter = token_counter
        self.certified_message_transform = certified_message_transform
        self.native_message_transform = native_message_transform
        self.binding_strict = bool(binding_strict)
        self._routed_physical_admission = bool(
            getattr(
                getattr(delegate, "client", None),
                "_membind_physical_admission_enabled",
                False,
            )
        )
        self.observations: list[dict[str, Any]] = []
        self.provider_calls: list[dict[str, Any]] = []
        self.context_selection_events: list[dict[str, Any]] = []
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
            "response_sha256": None,
            "arbiter_instance_id": self.arbiter.instance_id,
            "recovery_policy_sha256": recovery_policy_sha256(),
            "semantic_operation_id": (
                f"{int(source) if isinstance(source, int) else 'unknown'}:"
                f"{observation.public_summary.get('callsite', 'unknown')}:"
                f"{observation.public_summary.get('ordinal', 0)}"
            ),
            "request_variant_id": observation.public_summary.get("digest"),
            "physical_attempt_id": (
                f"{int(source) if isinstance(source, int) else 'unknown'}:"
                f"{observation.public_summary.get('callsite', 'unknown')}:"
                f"{observation.public_summary.get('ordinal', 0)}:0"
            ),
        }
        self.observations.append(row)
        _IDENTITY.set(identity)
        _OBSERVATION.set(row)

    def _class(self, source_sequence: int, region: str) -> AdmissionClass:
        if region == "NATIVE":
            return AdmissionClass.NATIVE_FRONTIER
        frontier = int(self.durable_frontier())
        return (
            AdmissionClass.FRONTIER_PREPARE
            if int(source_sequence) == frontier + 1
            else AdmissionClass.FUTURE_PREPARE
        )

    def _record(
        self,
        *,
        region: str,
        source_sequence: int,
        prompt_name: Any,
        admission_class: AdmissionClass | None,
        replay: bool,
        status: str,
        start_ns: int,
        service_start_ns: int | None = None,
        request_tokens: int | None = None,
        prompt_tokens: int | None = None,
        decode_reserve_tokens: int | None = None,
        transport_attempt_count: int = 0,
        transport_retry_count: int = 0,
        logical_attempt_count: int = 1,
        transport_attempts_observed: bool | None = None,
        result: Any = None,
        error: BaseException | None = None,
        fallback_type: str | None = None,
    ) -> None:
        identity = _IDENTITY.get()
        observation = _OBSERVATION.get()
        digest = response_sha256(result) if result is not None else None
        if observation is not None:
            observation["response_sha256"] = digest
        row: dict[str, Any] = {
            "schema_version": "membind.v6.1.provider-call.v2",
            "mode": self.mode,
            "region": region,
            "source_sequence": int(source_sequence),
            "prompt_name": prompt_name,
            "admission_class": admission_class.value if admission_class else None,
            "replay": bool(replay),
            "status": status,
            "response_sha256": digest,
            "arbiter_instance_id": self.arbiter.instance_id,
            "start_ns": int(start_ns),
            "end_ns": time.monotonic_ns(),
            "transport_attempt_count": int(transport_attempt_count),
            "transport_retry_count": int(transport_retry_count),
            "logical_attempt_count": max(1, int(logical_attempt_count)),
            "physical_attempt_count": max(0, int(transport_attempt_count)),
            "transport_attempts_observed": (
                bool(transport_attempts_observed)
                if transport_attempts_observed is not None
                else int(transport_attempt_count) > 0
            ),
            "transport_evidence": (
                "OBSERVED"
                if (
                    bool(transport_attempts_observed)
                    if transport_attempts_observed is not None
                    else int(transport_attempt_count) > 0
                )
                else "UNVERIFIED_PROVIDER_FREE"
            ),
            "recovery_policy_sha256": recovery_policy_sha256(),
            **reliability_identity(),
            "semantic_operation_id": f"{int(source_sequence)}:{prompt_name or 'unknown'}",
        }
        if request_tokens is not None:
            row["request_tokens"] = int(request_tokens)
        if prompt_tokens is not None:
            row["prompt_tokens"] = int(prompt_tokens)
        if decode_reserve_tokens is not None:
            row["decode_reserve_tokens"] = int(decode_reserve_tokens)
        if identity is not None:
            semantic_operation_id = (
                f"{int(source_sequence)}:{identity.callsite}:{identity.ordinal}"
            )
            row.update(
                {
                    "request_digest_prefix": identity.digest[:16],
                    "callsite": identity.callsite,
                    "ordinal": identity.ordinal,
                    "semantic_operation_id": semantic_operation_id,
                    "request_variant_id": identity.digest,
                    "physical_attempt_id": (
                        f"{semantic_operation_id}:"
                        f"{max(0, int(transport_attempt_count) - 1)}"
                    ),
                }
            )
        if error is not None:
            row["error_type"] = f"{type(error).__module__}.{type(error).__qualname__}"
            row["error_message_digest"] = hashlib.sha256(
                str(error).encode("utf-8", errors="backslashreplace")
            ).hexdigest()[:16]
            row["failure_class"] = classify_exception_for_recovery(error)
        if fallback_type is not None:
            row["fallback_type"] = str(fallback_type)
        self.provider_calls.append(row)
        row["duration_ns"] = int(row["end_ns"]) - int(row["start_ns"])
        service_start = int(service_start_ns if service_start_ns is not None else start_ns)
        row["queue_wait_ns"] = max(0, service_start - int(start_ns))
        row["service_ns"] = max(0, int(row["end_ns"]) - service_start)
        if self.event_sink is not None:
            self.event_sink({"event": "V61_PROVIDER_CALL", **row})
        _IDENTITY.set(None)
        _OBSERVATION.set(None)

    def _estimate_request_tokens(
        self, messages: Sequence[Any], kwargs: Mapping[str, Any]
    ) -> tuple[int, int, int]:
        """Estimate KV pressure before admission.

        The local runtime supplies the exact chat-template tokenizer.  Tests
        and alternate runtimes may omit it, so the fallback deliberately
        rounds character count upward instead of pretending request count is a
        useful proxy for long-context pressure.
        """

        try:
            prompt_tokens = (
                int(self.token_counter(messages))
                if self.token_counter is not None
                else self._heuristic_prompt_tokens(messages)
            )
        except Exception:
            prompt_tokens = self._heuristic_prompt_tokens(messages)
        requested = int(kwargs.get("max_tokens") or getattr(self._proxy, "max_tokens", 0) or 0)
        if requested <= 0:
            requested = 2_048
        decode_reserve = min(requested, self.arbiter.policy.STRUCTURED_DECODE_RESERVE_TOKENS)
        return max(1, prompt_tokens + decode_reserve), prompt_tokens, decode_reserve

    @staticmethod
    def _retryable_native_connection(error: BaseException) -> bool:
        """Recognize transport disconnects without retrying semantic failures."""

        name = type(error).__name__
        return name in {
            "APIConnectionError",
            "ConnectError",
            "ReadError",
            "RemoteProtocolError",
        } or "connection error" in str(error).casefold()

    @staticmethod
    def _heuristic_prompt_tokens(messages: Sequence[Any]) -> int:
        chars = 0
        for message in messages:
            if isinstance(message, Mapping):
                content = message.get("content")
            else:
                content = getattr(message, "content", None)
            if isinstance(content, str):
                chars += len(content)
            elif content is not None:
                chars += len(str(content))
        # 3 chars/token is intentionally conservative for mixed CJK/English
        # prompts and includes a small framing allowance per message.
        return max(1, int(math.ceil(chars / 3.0)) + 16 * len(messages))

    async def _run_fresh_delegate(
        self,
        delegate: Callable[[], Any],
        *,
        region: str,
        source_sequence: int,
        prompt_name: Any,
        effective_messages: Sequence[Any],
        kwargs: Mapping[str, Any],
        start_ns: int,
        fallback_type: str,
    ) -> Any:
        """Execute a binding fallback through the normal admission path.

        The proxy has already computed and observed the Native identity.  Its
        delegate closure is therefore the exact immutable Native request; this
        helper supplies the admission, transport accounting and provider-call
        row that certified replay deliberately skips.
        """

        initial_class = self._class(source_sequence, region)
        request_tokens, prompt_tokens, decode_reserve = self._estimate_request_tokens(
            effective_messages, kwargs
        )
        admitted = None
        if not self._routed_physical_admission:
            admitted = await self.arbiter.acquire_physical(
                initial_class,
                source_sequence=source_sequence,
                request_tokens=request_tokens,
                prompt_tokens=prompt_tokens,
                decode_reserve_tokens=decode_reserve,
                class_resolver=lambda: self._class(source_sequence, region),
            )
        service_start_ns = time.monotonic_ns()
        transport_counter = {"attempts": 0, "retries": 0}
        managed_token = _MANAGED_TRANSPORT_CALL.set(transport_counter)
        try:
            attempts = 0
            while True:
                try:
                    with provider_request_scope(request_tokens=request_tokens):
                        with proxy_source_scope(source_sequence):
                            result = await delegate()
                    break
                except BaseException as exc:
                    failure_class = classify_exception_for_recovery(exc)
                    retryable = failure_class in {
                        "SERVER_TRANSIENT",
                        "TRANSPORT_INCOMPLETE",
                    } or self._retryable_native_connection(exc)
                    if retryable and attempts < RECOVERY_POLICY_MAX_TRANSIENT_RETRIES:
                        attempts += 1
                        transport_counter["retries"] += 1
                        continue
                    raise
        except BaseException as exc:
            observed_transport_attempts = int(transport_counter["attempts"]) > 0
            _ensure_physical_attempt_lower_bound(transport_counter, attempts)
            self._record(
                region=region,
                source_sequence=source_sequence,
                prompt_name=prompt_name,
                admission_class=(
                    admitted.admission_class if admitted is not None else initial_class
                ),
                replay=False,
                status="failure",
                service_start_ns=service_start_ns,
                request_tokens=request_tokens,
                prompt_tokens=prompt_tokens,
                decode_reserve_tokens=decode_reserve,
                transport_attempt_count=transport_counter["attempts"],
                transport_retry_count=transport_counter["retries"],
                logical_attempt_count=attempts + 1,
                transport_attempts_observed=observed_transport_attempts,
                error=exc,
                start_ns=start_ns,
                fallback_type=fallback_type,
            )
            raise
        else:
            observed_transport_attempts = int(transport_counter["attempts"]) > 0
            _ensure_physical_attempt_lower_bound(transport_counter, attempts)
            self._record(
                region=region,
                source_sequence=source_sequence,
                prompt_name=prompt_name,
                admission_class=(
                    admitted.admission_class if admitted is not None else initial_class
                ),
                replay=False,
                status="success",
                service_start_ns=service_start_ns,
                request_tokens=request_tokens,
                prompt_tokens=prompt_tokens,
                decode_reserve_tokens=decode_reserve,
                transport_attempt_count=transport_counter["attempts"],
                transport_retry_count=transport_counter["retries"],
                logical_attempt_count=attempts + 1,
                transport_attempts_observed=observed_transport_attempts,
                result=result,
                start_ns=start_ns,
                fallback_type=fallback_type,
            )
            return result
        finally:
            _MANAGED_TRANSPORT_CALL.reset(managed_token)
            if admitted is not None:
                await self.arbiter.release_physical(admitted)

    async def generate_response(self, messages: list[Any], **kwargs: Any) -> Any:
        start_ns = time.monotonic_ns()
        region, source_sequence = current_provider_scope()
        if region is None or source_sequence is None:
            raise V61ProviderError("provider call outside V6.1 source scope")
        prompt_name = kwargs.get("prompt_name")
        certified = prompt_name in self.certified_callsites
        effective_messages = list(messages)
        if certified and self.certified_message_transform is not None:
            effective_messages, context_event = self.certified_message_transform(messages)
            context_event = {
                **dict(context_event),
                "mode": self.mode,
                "region": region,
                "source_sequence": int(source_sequence),
                "prompt_name": prompt_name,
            }
            self.context_selection_events.append(context_event)
            if self.event_sink is not None:
                self.event_sink(dict(context_event))
        elif self.mode == "replay" and self.native_message_transform is not None:
            transformed = self.native_message_transform(messages, prompt_name)
            if transformed is not None:
                effective_messages, context_event = transformed
                context_event = {
                    **dict(context_event),
                    "mode": self.mode,
                    "region": region,
                    "source_sequence": int(source_sequence),
                    "prompt_name": prompt_name,
                }
                self.context_selection_events.append(context_event)
                if self.event_sink is not None:
                    self.event_sink(dict(context_event))
        if self.mode == "replay" and certified:
            transport_counter = {"attempts": 0, "retries": 0}
            fallback_state = {"used": False}

            async def binding_fallback(
                mismatch: BindingMismatch, delegate: Callable[[], Any]
            ) -> Any:
                fallback_state["used"] = True
                fallback_type = "missing" if mismatch.reason == "missing" else "mismatch"
                return await self._run_fresh_delegate(
                    delegate,
                    region=region,
                    source_sequence=int(source_sequence),
                    prompt_name=prompt_name,
                    effective_messages=effective_messages,
                    kwargs=kwargs,
                    start_ns=start_ns,
                    fallback_type=fallback_type,
                )

            try:
                managed_token = _MANAGED_TRANSPORT_CALL.set(transport_counter)
                try:
                    with proxy_source_scope(source_sequence):
                        result = await self._proxy.generate_response(
                            effective_messages,
                            binding_fallback=binding_fallback,
                            **kwargs,
                        )
                finally:
                    _MANAGED_TRANSPORT_CALL.reset(managed_token)
            except BaseException as exc:
                self._record(
                    region=region,
                    source_sequence=source_sequence,
                    prompt_name=prompt_name,
                    admission_class=None,
                    replay=True,
                    status="failure",
                    request_tokens=None,
                    transport_attempt_count=transport_counter["attempts"],
                    transport_retry_count=transport_counter["retries"],
                    logical_attempt_count=1,
                    error=exc,
                    start_ns=start_ns,
                )
                raise
            if fallback_state["used"]:
                return result
            self._record(
                region=region,
                source_sequence=source_sequence,
                prompt_name=prompt_name,
                admission_class=None,
                replay=True,
                status="success",
                request_tokens=None,
                transport_attempt_count=transport_counter["attempts"],
                transport_retry_count=transport_counter["retries"],
                logical_attempt_count=1,
                result=result,
                start_ns=start_ns,
            )
            return result

        initial_class = self._class(source_sequence, region)
        request_tokens, prompt_tokens, decode_reserve = self._estimate_request_tokens(
            effective_messages, kwargs
        )
        admitted = None
        if not self._routed_physical_admission:
            admitted = await self.arbiter.acquire_physical(
                initial_class,
                source_sequence=source_sequence,
                request_tokens=request_tokens,
                prompt_tokens=prompt_tokens,
                decode_reserve_tokens=decode_reserve,
                class_resolver=lambda: self._class(source_sequence, region),
            )
        service_start_ns = time.monotonic_ns()
        transport_counter = {"attempts": 0, "retries": 0}
        managed_token = _MANAGED_TRANSPORT_CALL.set(transport_counter)
        try:
            attempts = 0
            while True:
                try:
                    with provider_request_scope(request_tokens=request_tokens):
                        with proxy_source_scope(source_sequence):
                            result = await self._proxy.generate_response(effective_messages, **kwargs)
                    break
                except BaseException as exc:
                    failure_class = classify_exception_for_recovery(exc)
                    retryable = failure_class in {
                        "SERVER_TRANSIENT",
                        "TRANSPORT_INCOMPLETE",
                    } or self._retryable_native_connection(exc)
                    if retryable and attempts < RECOVERY_POLICY_MAX_TRANSIENT_RETRIES:
                        attempts += 1
                        transport_counter["retries"] += 1
                        if self.event_sink is not None:
                            self.event_sink(
                                {
                                    "event": "V61_PROVIDER_TRANSIENT_RETRY",
                                    "mode": self.mode,
                                    "region": region,
                                    "source_sequence": int(source_sequence),
                                    "prompt_name": prompt_name,
                                    "failure_class": failure_class,
                                    "retry_index": attempts,
                                    "max_extra_retries": RECOVERY_POLICY_MAX_TRANSIENT_RETRIES,
                                    "recovery_policy_sha256": recovery_policy_sha256(),
                                    "arbiter_instance_id": self.arbiter.instance_id,
                                }
                            )
                        continue
                    raise
            observed_transport_attempts = int(transport_counter["attempts"]) > 0
            _ensure_physical_attempt_lower_bound(transport_counter, attempts)
            self._record(
                region=region,
                source_sequence=source_sequence,
                prompt_name=prompt_name,
                admission_class=(
                    admitted.admission_class if admitted is not None else initial_class
                ),
                replay=False,
                status="success",
                service_start_ns=service_start_ns,
                request_tokens=request_tokens,
                prompt_tokens=prompt_tokens,
                decode_reserve_tokens=decode_reserve,
                transport_attempt_count=transport_counter["attempts"],
                transport_retry_count=transport_counter["retries"],
                logical_attempt_count=attempts + 1,
                transport_attempts_observed=observed_transport_attempts,
                result=result,
                start_ns=start_ns,
            )
            return result
        except BaseException as exc:
            observed_transport_attempts = int(transport_counter["attempts"]) > 0
            _ensure_physical_attempt_lower_bound(transport_counter, attempts)
            self._record(
                region=region,
                source_sequence=source_sequence,
                prompt_name=prompt_name,
                admission_class=(
                    admitted.admission_class if admitted is not None else initial_class
                ),
                replay=False,
                status="failure",
                service_start_ns=service_start_ns,
                request_tokens=request_tokens,
                prompt_tokens=prompt_tokens,
                decode_reserve_tokens=decode_reserve,
                transport_attempt_count=transport_counter["attempts"],
                transport_retry_count=transport_counter["retries"],
                logical_attempt_count=attempts + 1,
                transport_attempts_observed=observed_transport_attempts,
                error=exc,
                start_ns=start_ns,
            )
            raise
        finally:
            _MANAGED_TRANSPORT_CALL.reset(managed_token)
            if admitted is not None:
                await self.arbiter.release_physical(admitted)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._proxy, name)


__all__ = [
    "V61ProviderClient",
    "V61ProviderError",
    "incremental_native_summary_context",
    "install_auxiliary_transport_guard",
    "install_routed_physical_admission",
    "strip_certified_previous_context",
]
