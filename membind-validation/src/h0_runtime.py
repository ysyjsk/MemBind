"""Protocol v1.3 H0-only qualification controls.

This module is deliberately separate from ``graphiti_native``.  The latter is
part of the preserved V2/V3 evidence path, while H0 needs stricter state,
request, and evidence rules that must not reinterpret historical artifacts.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import hashlib
import asyncio
import json
import os
import re
import tempfile
import unicodedata
import fcntl
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

from current_state_gate import LiveAction, evaluate_live_action
from dataset import build_episodes
from instrumentation import current_episode_key
from structured_output import constrain_single_episode_indices


PROTOCOL_VERSION = "current-validation-v1.3"
H0_LIVE_SCOPE = "h0_q1_a_live_only"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_BASE_SPEC_SHA256 = "8738531ca312657e9e9954a8cfb858be30409283af495c7a40bb16fdf4430ebe"
_CANDIDATE_SPEC_SHA256 = {
    "Q1": "e9646d53b24f25f594bb2de6367838297787da2cf6f7970fa240dfd1df5684ee",
    "Q2": "5a8096419ec05eee799a78454e4d0f7ae34d1d43de774cd0fc2706939852f0ad",
    "Q3": "736c64114a1aec5bcb5ef76d461aac609c711ea0ce010bb037f11c61ba58bdd2",
}
_CANDIDATE_ORDER = ("Q1", "Q2", "Q3")


class H0StateGateError(RuntimeError):
    """Raised before dependencies are touched when live H0 is not authorized."""


class H0ManifestError(RuntimeError):
    """Raised when an H0 content-addressed manifest cannot be proven."""


class H0DataScopeError(RuntimeError):
    """Raised before service access for non-calibration input."""


class H0BudgetError(RuntimeError):
    """Raised when a complete prompt leaves no safe completion budget."""

    def __init__(self, message: str, evidence: Mapping[str, Any]):
        super().__init__(message)
        self.evidence = dict(evidence)


class H0SemanticError(RuntimeError):
    """Raised when a parsed response is structurally valid but degenerate."""


class H0QualificationError(RuntimeError):
    """Raised for a candidate failure that cannot be rescued by retry."""


class H0InfrastructureError(RuntimeError):
    """Raised when vLLM connectivity requires an immediate operator stop."""


def canonical_json_bytes(value: Any) -> bytes:
    """Encode content deterministically for protocol hashes."""

    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("ascii")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class H0BudgetDecision:
    requested_max_tokens: int
    effective_max_tokens: int
    context_limit: int
    prompt_tokens: int
    safety_margin_tokens: int


def compute_effective_budget(
    *,
    requested_max_tokens: int,
    context_limit: int,
    prompt_tokens: int,
    safety_margin_tokens: int,
) -> H0BudgetDecision:
    """Compute the completion budget before any completion request is sent."""

    values = {
        "requested_max_tokens": int(requested_max_tokens),
        "context_limit": int(context_limit),
        "prompt_tokens": int(prompt_tokens),
        "safety_margin_tokens": int(safety_margin_tokens),
    }
    if values["requested_max_tokens"] <= 0 or values["context_limit"] <= 0:
        raise ValueError("requested_max_tokens and context_limit must be positive")
    if values["prompt_tokens"] < 0 or values["safety_margin_tokens"] < 0:
        raise ValueError("prompt_tokens and safety_margin_tokens cannot be negative")
    effective = max(
        0,
        min(
            values["requested_max_tokens"],
            values["context_limit"]
            - values["prompt_tokens"]
            - values["safety_margin_tokens"],
        ),
    )
    evidence = {**values, "effective_max_tokens": effective}
    if effective <= 0:
        raise H0BudgetError("context_budget_insufficient", evidence)
    return H0BudgetDecision(effective_max_tokens=effective, **values)


def _require_explicit_episode_indices(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove the Pydantic default and require explicit ``[0]`` attribution."""

    constrained = constrain_single_episode_indices(schema)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict) and isinstance(
                properties.get("episode_indices"), dict
            ):
                properties["episode_indices"].pop("default", None)
                required = value.get("required")
                required = list(required) if isinstance(required, list) else []
                if "episode_indices" not in required:
                    required.append("episode_indices")
                value["required"] = required
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(constrained)
    return constrained


@dataclass(frozen=True)
class H0SchemaEvidence:
    upstream_schema: dict[str, Any]
    effective_schema: dict[str, Any]
    upstream_schema_json: str
    effective_schema_json: str
    upstream_schema_sha256: str
    effective_schema_sha256: str


@dataclass(frozen=True)
class H0PreparedPrompt:
    messages: tuple[dict[str, str], ...]
    structured_output_mode: str
    schema: H0SchemaEvidence
    injected_schema_sha256: str | None


def _message_dict(message: Any) -> dict[str, str]:
    role = message.get("role") if isinstance(message, dict) else getattr(message, "role", None)
    content = (
        message.get("content")
        if isinstance(message, dict)
        else getattr(message, "content", None)
    )
    if role not in {"system", "user"} or not isinstance(content, str):
        raise TypeError("H0 messages must contain system/user text")
    return {"role": str(role), "content": content}


def prepare_h0_prompt(
    messages: Sequence[Any],
    response_model: Any,
    structured_output_mode: str,
) -> H0PreparedPrompt:
    """Freeze final messages and the per-call upstream/effective schema pair."""

    if structured_output_mode not in {"json_schema", "json_object"}:
        raise ValueError("unsupported H0 structured output mode")
    upstream = deepcopy(response_model.model_json_schema())
    effective = _require_explicit_episode_indices(upstream)
    upstream_json = canonical_json_bytes(upstream).decode("ascii")
    effective_json = canonical_json_bytes(effective).decode("ascii")
    prepared = [_message_dict(message) for message in messages]
    injected_hash: str | None = None
    if structured_output_mode == "json_object":
        if not prepared:
            raise ValueError("Q3 schema injection requires at least one message")
        prepared[-1]["content"] += (
            "\n\nRespond with a JSON object in the following format:\n\n"
            + effective_json
        )
        injected_hash = hashlib.sha256(effective_json.encode("ascii")).hexdigest()
    schema = H0SchemaEvidence(
        upstream_schema=upstream,
        effective_schema=effective,
        upstream_schema_json=upstream_json,
        effective_schema_json=effective_json,
        upstream_schema_sha256=hashlib.sha256(upstream_json.encode("ascii")).hexdigest(),
        effective_schema_sha256=hashlib.sha256(effective_json.encode("ascii")).hexdigest(),
    )
    return H0PreparedPrompt(
        messages=tuple(prepared),
        structured_output_mode=structured_output_mode,
        schema=schema,
        injected_schema_sha256=injected_hash,
    )


@dataclass(frozen=True)
class H0CandidateConfig:
    candidate_id: str
    model: str
    structured_output_mode: str
    temperature: float
    top_p: float
    top_k: int | None
    min_p: float | int | None
    seed: int
    requested_max_tokens: int
    context_limit: int
    safety_margin_tokens: int

    def __post_init__(self) -> None:
        if self.candidate_id not in _CANDIDATE_ORDER:
            raise ValueError("H0 candidate must be Q1, Q2, or Q3")
        if self.structured_output_mode not in {"json_schema", "json_object"}:
            raise ValueError("invalid structured output mode")
        if self.candidate_id == "Q1" and (self.top_k is not None or self.min_p is not None):
            raise ValueError("Q1 must omit top_k and min_p")
        if self.candidate_id in {"Q2", "Q3"} and (self.top_k != 20 or self.min_p != 0):
            raise ValueError("Q2/Q3 require top_k=20 and min_p=0")


@dataclass(frozen=True)
class H0RequestPlan:
    payload: dict[str, Any]
    evidence: dict[str, Any]
    budget: H0BudgetDecision


def _message_evidence(messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    contents = [str(message["content"]) for message in messages]
    return {
        "message_count": len(messages),
        "message_roles": [str(message["role"]) for message in messages],
        "message_content_sha256": [
            hashlib.sha256(content.encode("utf-8")).hexdigest() for content in contents
        ],
        "message_content_lengths": [len(content) for content in contents],
        "message_content_byte_lengths": [len(content.encode("utf-8")) for content in contents],
    }


def _safe_payload_evidence(
    payload: Mapping[str, Any],
    *,
    budget: H0BudgetDecision | None = None,
    observed: bool = False,
) -> dict[str, Any]:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise TypeError("H0 request messages must be a list")
    extra_body = payload.get("extra_body")
    extra_body = extra_body if isinstance(extra_body, dict) else {}
    response_format = payload.get("response_format")
    response_format = response_format if isinstance(response_format, dict) else {}
    schema_wrapper = response_format.get("json_schema")
    schema_wrapper = schema_wrapper if isinstance(schema_wrapper, dict) else {}
    schema = schema_wrapper.get("schema")
    schema = schema if isinstance(schema, dict) else {}
    evidence = {
        "model": str(payload.get("model") or ""),
        "temperature": payload.get("temperature"),
        "top_p": payload.get("top_p"),
        "top_k": payload.get("top_k", extra_body.get("top_k")),
        "min_p": payload.get("min_p", extra_body.get("min_p")),
        "max_tokens": payload.get("max_tokens"),
        "server_request_seed": payload.get("seed"),
        "response_format_type": response_format.get("type"),
        "response_format_sha256": canonical_json_sha256(response_format),
        "json_schema_sha256": canonical_json_sha256(schema),
        "credentials_persisted": False,
        **_message_evidence(messages),
    }
    if budget is not None:
        evidence.update(asdict(budget))
    key = (
        "observed_request_payload_sha256"
        if observed
        else "requested_request_payload_sha256"
    )
    evidence[key] = canonical_json_sha256(payload)
    return evidence


def build_h0_completion_request(
    candidate: H0CandidateConfig,
    prepared: H0PreparedPrompt,
    *,
    prompt_tokens: int,
) -> H0RequestPlan:
    """Build one completion payload; no retry or prompt truncation is permitted."""

    if candidate.structured_output_mode != prepared.structured_output_mode:
        raise H0ManifestError("candidate mode does not match prepared prompt")
    budget = compute_effective_budget(
        requested_max_tokens=candidate.requested_max_tokens,
        context_limit=candidate.context_limit,
        prompt_tokens=prompt_tokens,
        safety_margin_tokens=candidate.safety_margin_tokens,
    )
    if candidate.structured_output_mode == "json_schema":
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_response",
                "schema": prepared.schema.effective_schema,
            },
        }
    else:
        if prepared.injected_schema_sha256 != prepared.schema.effective_schema_sha256:
            raise H0ManifestError("Q3 injected schema is not the effective shim schema")
        response_format = {"type": "json_object"}
    extra_body: dict[str, Any] = {
        "chat_template_kwargs": {"enable_thinking": False}
    }
    if candidate.top_k is not None:
        extra_body["top_k"] = candidate.top_k
    if candidate.min_p is not None:
        extra_body["min_p"] = candidate.min_p
    payload: dict[str, Any] = {
        "model": candidate.model,
        "messages": [dict(message) for message in prepared.messages],
        "temperature": candidate.temperature,
        "top_p": candidate.top_p,
        "max_tokens": budget.effective_max_tokens,
        "response_format": response_format,
        "seed": candidate.seed,
        "extra_body": extra_body,
    }
    evidence = _safe_payload_evidence(payload, budget=budget)
    evidence.update(
        {
            "candidate_id": candidate.candidate_id,
            "upstream_schema_sha256": prepared.schema.upstream_schema_sha256,
            "effective_schema_sha256": prepared.schema.effective_schema_sha256,
            "injected_schema_sha256": prepared.injected_schema_sha256,
        }
    )
    return H0RequestPlan(payload=payload, evidence=evidence, budget=budget)


class H0WireObserver:
    """Capture only a sanitized projection of actual serialized chat bodies."""

    _MATCH_FIELDS = (
        "model",
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "max_tokens",
        "server_request_seed",
        "response_format_type",
        "response_format_sha256",
        "json_schema_sha256",
        "message_count",
        "message_roles",
        "message_content_sha256",
        "message_content_lengths",
        "message_content_byte_lengths",
    )

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._consumed_event_indexes: set[int] = set()

    async def __call__(self, request: httpx.Request) -> None:
        if not request.url.path.endswith("/chat/completions"):
            return
        payload = json.loads(request.content)
        if not isinstance(payload, dict):
            raise TypeError("serialized H0 request body must be an object")
        self.events.append(_safe_payload_evidence(payload, observed=True))

    def take_event_for_request(
        self,
        planned_evidence: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Return one unconsumed wire event matching the planned safe projection."""

        requested_payload_sha256 = str(
            planned_evidence.get("requested_request_payload_sha256") or ""
        )
        if _SHA256_RE.fullmatch(str(requested_payload_sha256)) is None:
            raise ValueError("planned request payload SHA-256 is required")
        for index, event in enumerate(self.events):
            if index in self._consumed_event_indexes:
                continue
            if event.get("observed_request_payload_sha256") == requested_payload_sha256:
                self._consumed_event_indexes.add(index)
                return deepcopy(event)
        for index, event in enumerate(self.events):
            if index in self._consumed_event_indexes:
                continue
            if all(
                planned_evidence.get(field) == event.get(field)
                for field in self._MATCH_FIELDS
            ):
                self._consumed_event_indexes.add(index)
                return deepcopy(event)
        return None


def build_h0_openai_client(
    *,
    api_key: str,
    base_url: str,
    observer: H0WireObserver,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncOpenAI:
    """Construct the H0 transport with every SDK retry explicitly disabled."""

    timeout = httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0)
    limits = httpx.Limits(
        max_connections=1000,
        max_keepalive_connections=100,
        keepalive_expiry=5.0,
    )
    http_client = httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        transport=transport,
        event_hooks={"request": [observer]},
        follow_redirects=False,
        trust_env=False,
    )
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=0,
        timeout=timeout,
        http_client=http_client,
    )
    # The pinned deployment is Linux. Avoid the SDK's blocking first-request
    # platform probe, which is unrelated to the frozen request semantics.
    client._platform = "Linux"  # type: ignore[attr-defined]
    client._membind_h0_observer = observer  # type: ignore[attr-defined]
    return client


class VLLMChatTokenCounter:
    """Count the finalized chat prompt through vLLM's non-generation endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        split = urlsplit(base_url)
        path = split.path.rstrip("/")
        if path.endswith("/v1"):
            path = path[:-3]
        root = urlunsplit((split.scheme, split.netloc, path.rstrip("/"), "", ""))
        if not split.scheme or not split.netloc:
            raise ValueError("vLLM base URL must be absolute")
        self.url = root + "/tokenize"
        self.model = model
        self.events: list[dict[str, Any]] = []
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    async def __call__(
        self,
        model: str,
        messages: list[dict[str, str]],
    ) -> int:
        if model != self.model:
            raise H0ManifestError("token counter model does not match candidate model")
        body = {
            "model": model,
            "messages": deepcopy(messages),
            "add_special_tokens": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        safe = {
            "model": model,
            "endpoint": "/tokenize",
            "request_sha256": canonical_json_sha256(body),
            **_message_evidence(messages),
            "credentials_persisted": False,
            "raw_prompt_persisted": False,
        }
        try:
            response = await self._client.post(self.url, json=body)
        except Exception as exc:
            if _is_vllm_connectivity_error(exc):
                self.events.append({**safe, "failure_class": "vllm_unreachable"})
                raise H0InfrastructureError("vllm_unreachable: stop_and_report") from exc
            self.events.append({**safe, "failure_class": "tokenize_transport_failure"})
            raise H0QualificationError("tokenize_transport_failure") from exc
        if response.status_code != 200:
            infrastructure_status = (
                response.status_code == 429 or 500 <= response.status_code <= 599
            )
            self.events.append(
                {
                    **safe,
                    "http_status": response.status_code,
                    "failure_class": (
                        "vllm_unreachable"
                        if infrastructure_status
                        else "tokenize_http_failure"
                    ),
                }
            )
            if infrastructure_status:
                raise H0InfrastructureError("vllm_unreachable: stop_and_report")
            raise H0QualificationError("tokenize_http_failure")
        try:
            payload = response.json()
            count = int(payload["count"])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self.events.append({**safe, "http_status": 200, "failure_class": "tokenize_invalid"})
            raise H0QualificationError("tokenize_invalid") from exc
        if count <= 0:
            self.events.append({**safe, "http_status": 200, "failure_class": "tokenize_invalid"})
            raise H0QualificationError("tokenize_invalid")
        self.events.append(
            {
                **safe,
                "http_status": 200,
                "prompt_tokens": count,
                "reported_max_model_len": payload.get("max_model_len"),
                "failure_class": None,
            }
        )
        return count

    async def close(self) -> None:
        await self._client.aclose()


def _load_h0_state(state_path: str | Path) -> Mapping[str, Any]:
    try:
        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise H0StateGateError("live H0 is not authorized: unreadable machine state") from exc
    if not isinstance(state, dict):
        raise H0StateGateError("live H0 is not authorized: invalid machine state")
    return state


def authorize_h0_live_entry(
    *,
    state_path: str | Path,
    candidate_id: str,
    phase: str,
) -> Mapping[str, Any]:
    """Return an exact live authorization or fail closed.

    The current offline state intentionally fails this gate. Every later state
    transition must name one exact candidate, phase, and resolved-manifest hash;
    a generic boolean or candidate-only authorization is insufficient.
    """

    state = _load_h0_state(state_path)
    if candidate_id not in _CANDIDATE_ORDER or phase not in {"H0-A", "H0-B", "H0-C"}:
        raise H0StateGateError(
            "live H0 is not authorized for the requested candidate and phase"
        )
    progress = state.get("stage_progress")
    authorization = state.get("live_h0_authorization")
    phase_suffix = phase.removeprefix("H0-").casefold()
    expected_scope = f"h0_{candidate_id.casefold()}_{phase_suffix}_live_only"
    global_decision = evaluate_live_action(
        state,
        LiveAction.H0_CANDIDATE,
        candidate_id=candidate_id,
    )
    required_paths = (
        "resolved_manifest_index_path",
        "resolved_candidate_manifest_path",
        "resolved_shared_base_manifest_path",
    )
    required_hashes = (
        "resolved_manifest_index_sha256",
        "resolved_candidate_manifest_sha256",
        "resolved_shared_base_manifest_sha256",
    )

    def valid_relative_path(field: str) -> bool:
        value = authorization.get(field) if isinstance(authorization, dict) else None
        if not isinstance(value, str) or not value.strip():
            return False
        relative = Path(value)
        return (
            not relative.is_absolute()
            and relative.as_posix() == value
            and all(part not in {"", ".", ".."} for part in relative.parts)
            and ".env" not in relative.parts
            and "gpt55_temporary" not in relative.parts
        )

    exact_state = (
        global_decision.allowed
        and state.get("current_stage") == "H0"
        and state.get("status") == expected_scope
        and state.get("current_action_scope") == expected_scope
        and state.get("live_h0_candidate_authorized") is True
        and isinstance(progress, dict)
        and progress.get("h0_live_gate") == expected_scope
        and isinstance(authorization, dict)
        and authorization.get("candidate_id") == candidate_id
        and authorization.get("phase") == phase
        and all(valid_relative_path(field) for field in required_paths)
        and all(
            isinstance(authorization.get(field), str)
            and _SHA256_RE.fullmatch(str(authorization.get(field))) is not None
            for field in required_hashes
        )
    )
    if not exact_state:
        raise H0StateGateError(
            "live H0 is not authorized for the requested candidate and phase"
        )
    return authorization


def enter_h0_runtime(
    *,
    state_path: str | Path,
    candidate_id: str,
    phase: str,
    env_loader: Callable[[], Mapping[str, Any]],
    service_factory: Callable[[Mapping[str, Any]], Any],
) -> Any:
    """Gate first, then load credentials/configuration and construct services."""

    authorize_h0_live_entry(
        state_path=state_path,
        candidate_id=candidate_id,
        phase=phase,
    )
    runtime_config = env_loader()
    return service_factory(runtime_config)


class H0AttemptLedger:
    """Separate public logical trials from actual completion HTTP attempts."""

    def __init__(self, *, stage_attempt_id: str) -> None:
        if not stage_attempt_id.strip():
            raise ValueError("stage_attempt_id is required")
        self.stage_attempt_id = stage_attempt_id
        self.trials: dict[str, dict[str, Any]] = {}
        self.attempts: list[dict[str, Any]] = []

    def start_trial(
        self,
        candidate_id: str,
        call_key: str,
        repeated_trial_index: int,
    ) -> str:
        ordinal = len(self.trials)
        logical_id = hashlib.sha256(
            f"{self.stage_attempt_id}|logical|{ordinal}|{candidate_id}|{call_key}|"
            f"{repeated_trial_index}".encode("utf-8")
        ).hexdigest()[:24]
        self.trials[logical_id] = {
            "logical_trial_id": logical_id,
            "candidate_id": candidate_id,
            "call_key": call_key,
            "repeated_trial_index": int(repeated_trial_index),
            "statistically_independent": False,
            "attempt_ids": [],
        }
        return logical_id

    def start_attempt(
        self,
        logical_trial_id: str,
        request_evidence: Mapping[str, Any],
    ) -> str:
        trial = self.trials.get(logical_trial_id)
        if trial is None:
            raise KeyError(f"unknown logical trial: {logical_trial_id}")
        retry_index = len(trial["attempt_ids"])
        http_attempt_id = hashlib.sha256(
            f"{self.stage_attempt_id}|http|{len(self.attempts)}|{logical_trial_id}|"
            f"{retry_index}".encode("utf-8")
        ).hexdigest()[:24]
        event = {
            "http_attempt_id": http_attempt_id,
            "logical_trial_id": logical_trial_id,
            "retry_index": retry_index,
            "retry_same_logical_trial": retry_index > 0,
            **deepcopy(dict(request_evidence)),
            "completed": False,
        }
        forbidden = json.dumps(event, sort_keys=True).lower()
        for name in ("raw_prompt", "raw_response", "authorization", "api_key"):
            if name in forbidden:
                raise ValueError(f"unsafe H0 evidence field: {name}")
        trial["attempt_ids"].append(http_attempt_id)
        self.attempts.append(event)
        return http_attempt_id

    def finish_attempt(
        self,
        http_attempt_id: str,
        *,
        http_status: int | None,
        finish_reason: str | None,
        response_text: str,
        response_prompt_tokens: int | None,
        json_parse_success: bool,
        pydantic_validation_success: bool,
        semantic_utility_success: bool,
        failure_class: str | None = None,
    ) -> None:
        event = next(
            (item for item in self.attempts if item["http_attempt_id"] == http_attempt_id),
            None,
        )
        if event is None:
            raise KeyError(f"unknown HTTP attempt: {http_attempt_id}")
        if event["completed"]:
            raise RuntimeError("HTTP attempt is already complete")
        event.update(
            {
                "completed": True,
                "http_status": http_status,
                "http_200": http_status == 200,
                "finish_reason": finish_reason,
                "finish_non_length": finish_reason != "length",
                "response_sha256": hashlib.sha256(
                    response_text.encode("utf-8")
                ).hexdigest(),
                "response_length": len(response_text),
                "response_byte_length": len(response_text.encode("utf-8")),
                "response_prompt_tokens": response_prompt_tokens,
                "json_parse_success": bool(json_parse_success),
                "pydantic_validation_success": bool(pydantic_validation_success),
                "semantic_utility_success": bool(semantic_utility_success),
                "failure_class": failure_class,
            }
        )

    def attach_observed_request(
        self,
        http_attempt_id: str,
        observed_evidence: Mapping[str, Any],
    ) -> None:
        """Bind the sanitized SDK wire observation to its planned attempt."""

        event = next(
            (item for item in self.attempts if item["http_attempt_id"] == http_attempt_id),
            None,
        )
        if event is None:
            raise KeyError(f"unknown HTTP attempt: {http_attempt_id}")
        if "observed_request_payload_sha256" in event:
            raise RuntimeError("wire request is already attached")
        observed = dict(observed_evidence)
        for field in ("model", "temperature", "top_p", "top_k", "min_p", "max_tokens"):
            if event.get(field) != observed.get(field):
                raise H0ManifestError(f"planned/observed request mismatch: {field}")
        event.update(
            {
                "observed_request_payload_sha256": observed.get(
                    "observed_request_payload_sha256"
                ),
                "observed_server_request_seed": observed.get("server_request_seed"),
                "observed_response_format_sha256": observed.get(
                    "response_format_sha256"
                ),
                "observed_message_content_sha256": observed.get(
                    "message_content_sha256"
                ),
            }
        )

    def trial_verdict(self, logical_trial_id: str) -> dict[str, Any]:
        trial = self.trials.get(logical_trial_id)
        if trial is None:
            raise KeyError(f"unknown logical trial: {logical_trial_id}")
        attempts = [
            item for item in self.attempts if item["logical_trial_id"] == logical_trial_id
        ]
        reasons: list[str] = []
        if len(attempts) != 1 or any(item["retry_index"] > 0 for item in attempts):
            reasons.append("candidate_induced_retry")
        if not attempts:
            reasons.append("missing_http_attempt")
        for attempt in attempts:
            if not attempt["completed"]:
                reasons.append("incomplete_http_attempt")
                continue
            if not attempt["http_200"]:
                reasons.append("http_failure")
            if not attempt["finish_non_length"]:
                reasons.append("length_finish")
            if not attempt["json_parse_success"]:
                reasons.append("json_parse_failure")
            if not attempt["pydantic_validation_success"]:
                reasons.append("pydantic_validation_failure")
            if not attempt["semantic_utility_success"]:
                reasons.append("semantic_utility_failure")
            planned = attempt.get("prompt_tokens")
            observed = attempt.get("response_prompt_tokens")
            if planned is None or observed is None or int(planned) != int(observed):
                reasons.append("prompt_token_count_mismatch")
            if _SHA256_RE.fullmatch(
                str(attempt.get("observed_request_payload_sha256") or "")
            ) is None:
                reasons.append("wire_request_observation_missing")
            if attempt.get("observed_server_request_seed") != attempt.get(
                "server_request_seed"
            ):
                reasons.append("server_request_seed_mismatch")
        reasons = list(dict.fromkeys(reasons))
        return {
            "logical_trial_id": logical_trial_id,
            "qualified": not reasons,
            "failure_reasons": reasons,
            "http_attempt_count": len(attempts),
        }

    def safe_artifact(self) -> dict[str, Any]:
        return {
            "schema_version": "membind.h0.attempt-ledger.v1",
            "protocol_version": PROTOCOL_VERSION,
            "stage_attempt_id": self.stage_attempt_id,
            "logical_trials": [deepcopy(value) for value in self.trials.values()],
            "http_attempts": deepcopy(self.attempts),
            "secrets_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
        }


@dataclass(frozen=True)
class H0WorkItem:
    phase: str
    question_id: str
    source_sequence: int
    repeated_trial_index: int
    statistically_independent: bool = False


@dataclass(frozen=True)
class H0CalibrationCorpus:
    split: dict[str, Any]
    records: Mapping[str, dict[str, Any]]
    episodes: Mapping[str, tuple[Any, ...]]

    @property
    def question_ids(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.split["calibration_question_ids"])

    def require(self, question_id: str) -> dict[str, Any]:
        if question_id not in self.question_ids:
            raise H0DataScopeError(
                f"H0 calibration-only scope rejects question_id={question_id}"
            )
        record = self.records.get(question_id)
        if record is None:
            raise H0DataScopeError(f"calibration question is missing: {question_id}")
        return record


def load_h0_calibration_corpus(
    split_path: str | Path,
    data_path: str | Path,
) -> H0CalibrationCorpus:
    """Load a view that can expose only the frozen calibration IDs."""

    split = json.loads(Path(split_path).read_text(encoding="utf-8"))
    if not isinstance(split, dict) or split.get("protocol_version") != PROTOCOL_VERSION:
        raise H0DataScopeError("H0 requires the Protocol v1.3 split")
    calibration = tuple(str(value) for value in split.get("calibration_question_ids", []))
    evaluation = {str(value) for value in split.get("evaluation_question_ids", [])}
    quarantine = {
        str(value)
        for value in split.get("compatibility_development_question_ids", [])
    }
    if not calibration or len(calibration) != len(set(calibration)):
        raise H0DataScopeError("calibration IDs must be nonempty and unique")
    if set(calibration) & (evaluation | quarantine):
        raise H0DataScopeError("calibration, evaluation, and quarantine IDs must be disjoint")
    data_path = Path(data_path)
    if sha256_file(data_path) != split.get("source_sha256"):
        raise H0DataScopeError("dataset hash does not match the frozen split")
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise H0DataScopeError("H0 source dataset must be a JSON list")
    selected: dict[str, dict[str, Any]] = {}
    for record in payload:
        if not isinstance(record, dict):
            continue
        question_id = str(record.get("question_id") or "")
        if question_id in calibration:
            if question_id in selected:
                raise H0DataScopeError(f"duplicate calibration question: {question_id}")
            selected[question_id] = record
    missing = [question_id for question_id in calibration if question_id not in selected]
    if missing:
        raise H0DataScopeError(f"missing calibration questions: {missing}")
    episodes = {
        question_id: tuple(build_episodes(selected[question_id]))
        for question_id in calibration
    }
    return H0CalibrationCorpus(split=dict(split), records=selected, episodes=episodes)


def enter_h0_case(
    corpus: H0CalibrationCorpus,
    question_id: str,
    service_factory: Callable[[dict[str, Any]], Any],
) -> Any:
    """Enforce data scope before constructing any per-case service."""

    return service_factory(corpus.require(question_id))


def build_h0_workload(
    corpus: H0CalibrationCorpus,
    phase: str,
) -> tuple[H0WorkItem, ...]:
    """Build only the preregistered calibration workload for one H0 phase."""

    if phase not in {"H0-A", "H0-B", "H0-C"}:
        raise ValueError("H0 phase must be H0-A, H0-B, or H0-C")
    primary = "07741c45"
    corpus.require(primary)
    if phase == "H0-A":
        return tuple(
            H0WorkItem(phase, primary, 0, repeated_trial_index=index)
            for index in range(3)
        )
    if phase == "H0-B":
        return tuple(
            H0WorkItem(phase, primary, episode.source_sequence, 0)
            for episode in corpus.episodes[primary]
        )
    return tuple(
        H0WorkItem(phase, question_id, episode.source_sequence, 0)
        for question_id in corpus.question_ids
        if question_id != primary
        for episode in corpus.episodes[question_id]
    )


def normalize_entity_name(value: str) -> str:
    """Frozen semantic normalization: NFKC, strip, collapse space, casefold."""

    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.strip().split()).casefold()


def _semantic_failure(reason: str) -> None:
    raise H0SemanticError(reason)


def _validate_episode_indices(item: Mapping[str, Any]) -> None:
    if "episode_indices" not in item:
        _semantic_failure("episode_indices_missing_not_explicit")
    if item.get("episode_indices") != [0]:
        _semantic_failure("episode_indices_must_equal_single_zero")


def evaluate_semantic_call(
    guardrail: Mapping[str, Any],
    call_key: str,
    response_model_name: str,
    parsed: Mapping[str, Any],
) -> dict[str, Any]:
    """Reject valid-but-degenerate output while retaining only safe statistics."""

    semantic_payload_sha256 = canonical_json_sha256(parsed)
    forbidden_defaults = guardrail.get("forbidden_default_payload_sha256", [])
    if not isinstance(forbidden_defaults, list) or any(
        not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None
        for value in forbidden_defaults
    ):
        raise H0ManifestError("invalid forbidden default payload hash manifest")
    if semantic_payload_sha256 in forbidden_defaults:
        _semantic_failure("forbidden_schema_default_payload")

    expected = guardrail.get("expected_nonempty_call_ids")
    expected = expected if isinstance(expected, dict) else {}
    minimums = expected.get(call_key)
    entity_count = 0
    distinct_count = 0
    if response_model_name == "ExtractedEntities":
        entities = parsed.get("extracted_entities")
        if not isinstance(entities, list):
            _semantic_failure("extracted_entities_must_be_a_list")
        names: list[str] = []
        for entity in entities:
            if not isinstance(entity, dict):
                _semantic_failure("entity_must_be_an_object")
            name = entity.get("name")
            if not isinstance(name, str) or not normalize_entity_name(name):
                _semantic_failure("entity_names_must_be_nonblank")
            _validate_episode_indices(entity)
            names.append(normalize_entity_name(name))
        entity_count = len(entities)
        distinct_count = len(set(names))
        if distinct_count != entity_count:
            _semantic_failure("duplicate_normalized_entity_names")
        if isinstance(minimums, dict):
            if entity_count < int(minimums.get("minimum_entity_count", 0)):
                _semantic_failure("expected_nonempty_extraction_is_empty")
            if distinct_count < int(
                minimums.get("minimum_distinct_normalized_entity_name_count", 0)
            ):
                _semantic_failure("minimum_distinct_entity_count_not_met")
    elif response_model_name == "ExtractedEdges":
        edges = parsed.get("edges")
        if not isinstance(edges, list):
            _semantic_failure("edges_must_be_a_list")
        for edge in edges:
            if not isinstance(edge, dict):
                _semantic_failure("edge_must_be_an_object")
            _validate_episode_indices(edge)
            for field in ("source_entity_name", "target_entity_name", "fact"):
                value = edge.get(field)
                if not isinstance(value, str) or not value.strip():
                    _semantic_failure(f"{field}_must_be_nonblank")
    elif not parsed:
        _semantic_failure("constant_or_schema_default_only_output")
    return {
        "call_key": call_key,
        "response_model_name": response_model_name,
        "entity_count": entity_count,
        "distinct_normalized_entity_name_count": distinct_count,
        "semantic_payload_sha256": semantic_payload_sha256,
        "failure_codes": [],
        "qualified": True,
    }


def validate_semantic_stage(
    guardrail: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Detect a constant payload across distinct preregistered source calls."""

    groups = guardrail.get("cross_call_constant_detection_groups")
    groups = groups if isinstance(groups, list) else []
    by_call: dict[str, set[str]] = {}
    for result in results:
        call_key = str(result.get("call_key") or "")
        payload_hash = str(result.get("semantic_payload_sha256") or "")
        if call_key and _SHA256_RE.fullmatch(payload_hash):
            by_call.setdefault(call_key, set()).add(payload_hash)
    for group in groups:
        if not isinstance(group, list):
            continue
        distinct_calls = list(dict.fromkeys(str(value) for value in group))
        observed = [by_call[call] for call in distinct_calls if call in by_call]
        if len(observed) >= 2:
            union = set().union(*observed)
            if len(union) == 1:
                raise H0SemanticError("constant payload across distinct semantic calls")
    return {
        "qualified": True,
        "observed_call_count": len(by_call),
        "constant_output_detected": False,
    }


@dataclass(frozen=True)
class H0CandidateSpec:
    candidate_id: str
    path: str
    sha256: str
    spec: dict[str, Any]


@dataclass(frozen=True)
class ArtifactBinding:
    path: str
    sha256: str


@dataclass(frozen=True)
class H0Registry:
    root: Path
    base_spec_path: str
    base_spec_sha256: str
    base_spec: dict[str, Any]
    candidates: tuple[H0CandidateSpec, ...]

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(candidate.candidate_id for candidate in self.candidates)

    @property
    def unresolved_fields(self) -> tuple[str, ...]:
        return tuple(str(value) for value in self.base_spec["unresolved_fields"])

    def resolve(
        self,
        bindings: Mapping[str, ArtifactBinding | Mapping[str, str]],
    ) -> dict[str, Any]:
        """Resolve every shared artifact without mutating source delta specs."""

        expected = set(self.unresolved_fields)
        supplied = set(bindings)
        missing = sorted(expected - supplied)
        extra = sorted(supplied - expected)
        if missing or extra:
            raise H0ManifestError(
                f"unresolved artifact bindings: missing={missing}, extra={extra}"
            )
        resolved: dict[str, dict[str, str]] = {}
        root = self.root.resolve()
        for name in self.unresolved_fields:
            raw = bindings[name]
            if isinstance(raw, ArtifactBinding):
                binding = raw
            elif isinstance(raw, Mapping):
                binding = ArtifactBinding(
                    path=str(raw.get("path") or ""),
                    sha256=str(raw.get("sha256") or ""),
                )
            else:
                raise H0ManifestError(f"invalid artifact binding: {name}")
            if not binding.path or _SHA256_RE.fullmatch(binding.sha256) is None:
                raise H0ManifestError(f"invalid path/hash binding: {name}")
            path = (root / binding.path).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise H0ManifestError(f"binding escapes project root: {name}") from exc
            if ".env" in path.parts or "gpt55_temporary" in path.parts:
                raise H0ManifestError(f"forbidden binding path: {name}")
            if not path.is_file() or sha256_file(path) != binding.sha256:
                raise H0ManifestError(f"artifact path/hash mismatch: {name}")
            resolved[name] = {"path": binding.path, "sha256": binding.sha256}
        shared = {
            "schema_version": "membind.h0.resolved-shared-host-base.v1",
            "protocol_version": PROTOCOL_VERSION,
            "status": "offline_resolved_not_live_authorized",
            "live_eligible": False,
            "source_base_spec": {
                "path": self.base_spec_path,
                "sha256": self.base_spec_sha256,
            },
            "source_base": deepcopy(self.base_spec),
            "resolved_artifacts": resolved,
            "unresolved_fields": [],
        }
        shared_hash = canonical_json_sha256(shared)
        candidates = {}
        for candidate in self.candidates:
            manifest = {
                "schema_version": "membind.h0.resolved-candidate.v1",
                "protocol_version": PROTOCOL_VERSION,
                "status": "offline_resolved_not_live_authorized",
                "live_eligible": False,
                "candidate_id": candidate.candidate_id,
                "source_delta_spec": {
                    "path": candidate.path,
                    "sha256": candidate.sha256,
                },
                "resolved_shared_base_sha256": shared_hash,
                "resolved_shared_artifacts": deepcopy(resolved),
                "candidate_configuration": deepcopy(candidate.spec),
            }
            candidates[candidate.candidate_id] = {
                "manifest": manifest,
                "sha256": canonical_json_sha256(manifest),
            }
        return {
            "shared_base": {"manifest": shared, "sha256": shared_hash},
            "candidates": candidates,
        }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise H0ManifestError(f"unreadable H0 manifest: {path}") from exc
    if not isinstance(value, dict):
        raise H0ManifestError(f"H0 manifest is not an object: {path}")
    return value


def load_h0_registry(root: str | Path) -> H0Registry:
    """Load and verify the immutable Q1/Q2/Q3 delta registry."""

    root = Path(root).resolve()
    base_rel = "configs/h0/shared_host_base_v1_3.json"
    base_path = root / base_rel
    if sha256_file(base_path) != _BASE_SPEC_SHA256:
        raise H0ManifestError("shared host base spec hash mismatch")
    base = _read_json_object(base_path)
    if base.get("status") != "offline_spec_unresolved_not_runnable":
        raise H0ManifestError("source base spec must remain unresolved and non-runnable")
    unresolved = base.get("unresolved_fields")
    if not isinstance(unresolved, list) or not unresolved or len(unresolved) != len(set(unresolved)):
        raise H0ManifestError("base unresolved_fields must be unique and nonempty")
    candidates: list[H0CandidateSpec] = []
    for selection_order, candidate_id in enumerate(_CANDIDATE_ORDER, start=1):
        rel = f"configs/h0/{candidate_id}.json"
        path = root / rel
        actual_hash = sha256_file(path)
        expected_hash = _CANDIDATE_SPEC_SHA256[candidate_id]
        if actual_hash != expected_hash:
            raise H0ManifestError(f"{candidate_id} delta spec hash mismatch")
        spec = _read_json_object(path)
        valid = (
            spec.get("candidate_id") == candidate_id
            and spec.get("selection_order") == selection_order
            and spec.get("manifest_kind") == "candidate_delta_spec"
            and spec.get("live_eligible") is False
            and spec.get("shared_host_base_spec") == base_rel
            and spec.get("shared_host_base_spec_sha256") == _BASE_SPEC_SHA256
        )
        if not valid:
            raise H0ManifestError(f"invalid {candidate_id} delta contract")
        candidates.append(H0CandidateSpec(candidate_id, rel, actual_hash, spec))
    return H0Registry(
        root=root,
        base_spec_path=base_rel,
        base_spec_sha256=_BASE_SPEC_SHA256,
        base_spec=base,
        candidates=tuple(candidates),
    )


@dataclass(frozen=True)
class H0CandidateResult:
    candidate_id: str
    qualified: bool
    shared_invariant_failure: bool = False
    failure_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class H0SelectionResult:
    selected_candidate_id: str | None
    terminal_status: str
    outcomes: Mapping[str, str]


def run_first_passing_candidates(
    candidate_ids: Iterable[str],
    execute: Callable[[str], H0CandidateResult],
) -> H0SelectionResult:
    """Apply the preregistered performance-blind first-passing rule."""

    order = tuple(candidate_ids)
    if order != _CANDIDATE_ORDER:
        raise H0ManifestError("candidate order must be exactly Q1, Q2, Q3")
    outcomes: dict[str, str] = {}
    selected: str | None = None
    for index, candidate_id in enumerate(order):
        result = execute(candidate_id)
        if result.candidate_id != candidate_id:
            raise H0ManifestError("candidate executor returned a mismatched ID")
        if result.qualified:
            outcomes[candidate_id] = "qualified_first_pass"
            selected = candidate_id
            for later in order[index + 1 :]:
                outcomes[later] = "not_executed_first_pass_selected"
            return H0SelectionResult(selected, "H0_QUALIFIED", outcomes)
        outcomes[candidate_id] = "failed_qualification"
        if result.shared_invariant_failure:
            for later in order[index + 1 :]:
                outcomes[later] = "not_executed_shared_invariant_failure"
            return H0SelectionResult(
                None,
                "H0_BLOCKED_ALL_PREREGISTERED_CANDIDATES_FAILED",
                outcomes,
            )
    return H0SelectionResult(
        None,
        "H0_BLOCKED_ALL_PREREGISTERED_CANDIDATES_FAILED",
        outcomes,
    )


class H0QwenVLLMClient:
    """Construct a pinned Graphiti client with H0-only request semantics.

    ``__new__`` returns an ``OpenAIGenericClient`` subclass so Graphiti sees its
    normal LLM interface.  The public method finalizes the prompt exactly once,
    counts it before generation, and sends one explicitly observed wire request.
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        from graphiti_core.llm_client.client import get_extraction_language_instruction
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

        candidate: H0CandidateConfig = kwargs.pop("candidate")
        token_counter = kwargs.pop("token_counter")
        semantic_guardrail = kwargs.pop("semantic_guardrail")
        semantic_evidence_sink = kwargs.pop("semantic_evidence_sink", None)
        ledger: H0AttemptLedger = kwargs.pop("ledger")
        repeated_trial_index = int(kwargs.pop("repeated_trial_index", 0))
        if semantic_evidence_sink is not None and not callable(semantic_evidence_sink):
            raise TypeError("semantic_evidence_sink must be callable")

        class Client(OpenAIGenericClient):  # type: ignore[misc]
            def __init__(self, *client_args: Any, **client_kwargs: Any) -> None:
                super().__init__(
                    *client_args,
                    max_tokens=candidate.requested_max_tokens,
                    structured_output_mode=candidate.structured_output_mode,
                    **client_kwargs,
                )
                if getattr(self.client, "max_retries", None) != 0:
                    raise H0ManifestError("H0 OpenAI SDK max_retries must equal zero")
                self.h0_candidate = candidate
                self.h0_token_counter = token_counter
                self.h0_semantic_guardrail = deepcopy(dict(semantic_guardrail))
                self.h0_ledger = ledger
                self.h0_repeated_trial_index = repeated_trial_index
                self._h0_call_ordinals: dict[str, int] = {}

            def _h0_call_key(self, group_id: str | None, prompt_name: str | None) -> str:
                episode = current_episode_key()
                source_sequence = episode[1] if episode is not None else -1
                effective_group_id = group_id or (
                    episode[0] if episode is not None else "ungrouped"
                )
                base = (
                    f"{effective_group_id}:{source_sequence}:"
                    f"{prompt_name or 'unknown'}"
                )
                ordinal = self._h0_call_ordinals.get(base, 0)
                self._h0_call_ordinals[base] = ordinal + 1
                return base if ordinal == 0 else f"{base}#{ordinal}"

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
            ) -> dict[str, Any]:
                if response_model is None:
                    raise H0ManifestError("H0 requires a response model for every call")
                working_messages = deepcopy(messages)
                self._apply_attribute_extraction_preamble(
                    working_messages, attribute_extraction
                )
                if not working_messages:
                    raise H0ManifestError("H0 requires a nonempty prompt")
                working_messages[0].content += get_extraction_language_instruction(group_id)
                prepared = prepare_h0_prompt(
                    working_messages,
                    response_model,
                    candidate.structured_output_mode,
                )
                prompt_messages = [dict(message) for message in prepared.messages]
                prompt_tokens = int(
                    await self.h0_token_counter(candidate.model, prompt_messages)
                )
                call_key = self._h0_call_key(group_id, prompt_name)
                logical_id = self.h0_ledger.start_trial(
                    candidate.candidate_id,
                    call_key,
                    self.h0_repeated_trial_index,
                )
                plan = build_h0_completion_request(
                    candidate,
                    prepared,
                    prompt_tokens=prompt_tokens,
                )
                attempt_id = self.h0_ledger.start_attempt(logical_id, plan.evidence)
                observer = getattr(self.client, "_membind_h0_observer", None)
                try:
                    response = await self.client.chat.completions.create(**plan.payload)
                except asyncio.CancelledError:
                    observed_event = (
                        observer.take_event_for_request(plan.evidence)
                        if observer is not None
                        else None
                    )
                    if observed_event is not None:
                        self.h0_ledger.attach_observed_request(
                            attempt_id, observed_event
                        )
                    self.h0_ledger.finish_attempt(
                        attempt_id,
                        http_status=None,
                        finish_reason=None,
                        response_text="",
                        response_prompt_tokens=None,
                        json_parse_success=False,
                        pydantic_validation_success=False,
                        semantic_utility_success=False,
                        failure_class="concurrent_attempt_cancelled",
                    )
                    raise
                except Exception as exc:
                    observed_event = (
                        observer.take_event_for_request(plan.evidence)
                        if observer is not None
                        else None
                    )
                    if observed_event is not None:
                        self.h0_ledger.attach_observed_request(
                            attempt_id, observed_event
                        )
                    failure_class = (
                        "vllm_unreachable"
                        if _is_vllm_connectivity_error(exc)
                        else "completion_transport_failure"
                    )
                    self.h0_ledger.finish_attempt(
                        attempt_id,
                        http_status=None,
                        finish_reason=None,
                        response_text="",
                        response_prompt_tokens=None,
                        json_parse_success=False,
                        pydantic_validation_success=False,
                        semantic_utility_success=False,
                        failure_class=failure_class,
                    )
                    if failure_class == "vllm_unreachable":
                        raise H0InfrastructureError("vllm_unreachable: stop_and_report") from exc
                    raise H0QualificationError("completion_transport_failure") from exc
                observed_event = (
                    observer.take_event_for_request(plan.evidence)
                    if observer is not None
                    else None
                )
                if observed_event is None:
                    self.h0_ledger.finish_attempt(
                        attempt_id,
                        http_status=200,
                        finish_reason=getattr(response.choices[0], "finish_reason", None),
                        response_text="",
                        response_prompt_tokens=_usage_prompt_tokens(response),
                        json_parse_success=False,
                        pydantic_validation_success=False,
                        semantic_utility_success=False,
                        failure_class="wire_request_observation_failure",
                    )
                    raise H0QualificationError("wire_request_observation_failure")
                self.h0_ledger.attach_observed_request(attempt_id, observed_event)
                finish_reason = getattr(response.choices[0], "finish_reason", None)
                response_text = str(response.choices[0].message.content or "")
                json_ok = False
                pydantic_ok = False
                semantic_ok = False
                parsed: dict[str, Any] | None = None
                semantic_record: dict[str, Any] | None = None
                try:
                    value = json.loads(self._strip_code_fences(response_text))
                    if not isinstance(value, dict):
                        raise TypeError("structured response must be an object")
                    parsed = value
                    json_ok = True
                    response_model(**parsed)
                    pydantic_ok = True
                    semantic_record = evaluate_semantic_call(
                        self.h0_semantic_guardrail,
                        call_key,
                        getattr(response_model, "__name__", "structured_response"),
                        parsed,
                    )
                    semantic_ok = True
                except (json.JSONDecodeError, TypeError, ValueError, H0SemanticError):
                    pass
                self.h0_ledger.finish_attempt(
                    attempt_id,
                    http_status=200,
                    finish_reason=finish_reason,
                    response_text=response_text,
                    response_prompt_tokens=_usage_prompt_tokens(response),
                    json_parse_success=json_ok,
                    pydantic_validation_success=pydantic_ok,
                    semantic_utility_success=semantic_ok,
                )
                verdict = self.h0_ledger.trial_verdict(logical_id)
                if not verdict["qualified"] or parsed is None:
                    reasons = ",".join(verdict["failure_reasons"])
                    raise H0QualificationError(f"candidate_qualification_failure:{reasons}")
                if semantic_record is None:
                    raise H0ManifestError("qualified H0 call has no semantic evidence")
                if semantic_evidence_sink is not None:
                    safe_semantic_record = {
                        **deepcopy(semantic_record),
                        "repeated_trial_index": self.h0_repeated_trial_index,
                    }
                    try:
                        semantic_evidence_sink(safe_semantic_record)
                    except Exception as exc:
                        raise H0ManifestError(
                            "H0 semantic evidence sink failed"
                        ) from exc
                return parsed

        return Client(*args, **kwargs)


def _usage_prompt_tokens(response: Any) -> int | None:
    usage = getattr(response, "usage", None)
    value = (
        usage.get("prompt_tokens")
        if isinstance(usage, dict)
        else getattr(usage, "prompt_tokens", None)
    )
    return int(value) if value is not None else None


def _is_vllm_connectivity_error(error: BaseException) -> bool:
    current: BaseException | None = error
    for _ in range(4):
        status_code = getattr(current, "status_code", None)
        if isinstance(status_code, int) and (
            status_code == 429 or 500 <= status_code <= 599
        ):
            return True
        if isinstance(
            current,
            (
                APIConnectionError,
                APITimeoutError,
                httpx.TransportError,
            ),
        ):
            return True
        current = current.__cause__ or current.__context__
        if current is None:
            break
    return False


class H0CheckpointStore:
    """Persist detailed H0 progress in small content-addressed segments."""

    _FORBIDDEN_KEYS = {
        "authorization",
        "api_key",
        "credentials",
        "env_dump",
        "environment_dump",
        "environ",
        "messages",
        "process_environment",
        "prompt",
        "raw_prompt",
        "raw_prompts",
        "raw_response",
        "raw_responses",
        "request_headers",
        "response_body",
        "response_text",
        "secret",
    }
    _IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
    _H0_B_HARNESS_REPAIR_FIELDS = {
        "schema_version",
        "protocol_version",
        "candidate_id",
        "phase",
        "decision_path",
        "decision_sha256",
        "decision_result_blind",
        "prior_model_workload_output_observed",
        "repair_required_independent_of_model_output",
        "scientific_configuration_unchanged",
        "one_shot_whole_stage_replacement",
        "replacement_attempt_id",
        "invalidated_stage_attempt_id",
        "invalidated_checkpoint_index_sha256",
        "failure_report_sha256",
        "old_attempt_qualification_reusable",
        "old_and_new_trial_counts_mergeable",
        "prior_manifest_index_sha256",
        "repaired_manifest_index_sha256",
        "secrets_persisted",
    }
    _H0_B_INFRASTRUCTURE_RERUN_FIELDS = {
        "schema_version",
        "protocol_version",
        "candidate_id",
        "phase",
        "decision_path",
        "decision_sha256",
        "interrupted_stage_attempt_id",
        "interrupted_checkpoint_index_sha256",
        "interrupted_stop_reason",
        "prior_harness_repair_admission_sha256",
        "replacement_attempt_id",
        "one_shot_whole_stage_replacement",
        "resume_interrupted_attempt_allowed",
        "prior_attempt_qualification_reusable",
        "old_and_new_trial_counts_mergeable",
        "scientific_configuration_unchanged",
        "prior_manifest_index_sha256",
        "recovered_manifest_index_sha256",
        "secrets_persisted",
    }
    _H0_B_POST_WORKLOAD_REPAIR_FIELDS = {
        "schema_version",
        "protocol_version",
        "candidate_id",
        "phase",
        "decision_path",
        "decision_sha256",
        "decision_result_blind",
        "prior_model_workload_output_observed",
        "repair_required_independent_of_model_response_content",
        "scientific_configuration_unchanged",
        "one_shot_whole_stage_replacement",
        "replacement_attempt_id",
        "invalidated_stage_attempt_id",
        "invalidated_checkpoint_index_sha256",
        "failure_segment_sha256",
        "source_checkpoint_sha256",
        "live_log_sha256",
        "offline_probe_sha256",
        "prior_harness_repair_admission_sha256",
        "prior_infrastructure_rerun_admission_sha256",
        "old_attempt_qualification_reusable",
        "old_and_new_trial_counts_mergeable",
        "resume_failed_attempt_allowed",
        "prior_manifest_index_sha256",
        "repaired_manifest_index_sha256",
        "secrets_persisted",
    }
    _H0_B_R6_RECOVERY_FIELDS = {
        "schema_version",
        "protocol_version",
        "candidate_id",
        "phase",
        "invalidated_stage_attempt_id",
        "invalidated_checkpoint_index_sha256",
        "failure_segment_sha256",
        "live_log_sha256",
        "misclassification_report_sha256",
        "root_cause_report_sha256",
        "prior_manifest_index_sha256",
        "repaired_manifest_index_sha256",
        "scientific_failure_class",
        "interrupted_stop_reason",
        "replacement_attempt_id",
        "one_shot_whole_stage_replacement",
        "resume_interrupted_attempt_allowed",
        "old_attempt_qualification_reusable",
        "old_and_new_trial_counts_mergeable",
        "source_checkpoints_reusable",
        "fresh_checkpoint_namespace_required",
        "scientific_configuration_unchanged",
        "live_authorized_by_this_admission",
        "secrets_persisted",
    }

    def __init__(
        self,
        *,
        root: str | Path,
        stage_attempt_id: str,
        candidate_id: str,
        phase: str,
        progress_sink: Callable[[dict[str, Any]], Any] | None = None,
        repair_admission: Mapping[str, Any] | None = None,
        infrastructure_rerun_admission: Mapping[str, Any] | None = None,
        post_workload_repair_admission: Mapping[str, Any] | None = None,
        r6_recovery_admission: Mapping[str, Any] | None = None,
    ) -> None:
        if (
            not isinstance(stage_attempt_id, str)
            or self._IDENTIFIER_RE.fullmatch(stage_attempt_id) is None
            or candidate_id not in _CANDIDATE_ORDER
        ):
            raise ValueError("checkpoint requires a valid attempt and candidate")
        if phase not in {"H0-A", "H0-B", "H0-C"}:
            raise ValueError("checkpoint requires an H0 phase")
        self.root = Path(root).resolve()
        self.stage_attempt_id = stage_attempt_id
        checkpoint_root = self._prepare_checkpoint_root()
        self.directory = checkpoint_root / stage_attempt_id
        self.index_path = self.directory / "index.json"
        self.progress_sink = progress_sink
        lock_path = checkpoint_root / f".admission.{candidate_id}.{phase}.lock"
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            lock_fd = os.open(lock_path, flags, 0o600)
        except OSError:
            raise H0ManifestError("checkpoint admission lock is invalid") from None
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            admission = self._validate_attempt_admission(
                checkpoint_root=checkpoint_root,
                candidate_id=candidate_id,
                phase=phase,
                repair_admission=repair_admission,
                infrastructure_rerun_admission=infrastructure_rerun_admission,
                post_workload_repair_admission=post_workload_repair_admission,
                r6_recovery_admission=r6_recovery_admission,
            )
            self.directory.mkdir(exist_ok=False)
            self.index = {
                "schema_version": "membind.h0.checkpoint-index.v1",
                "protocol_version": PROTOCOL_VERSION,
                "stage_attempt_id": stage_attempt_id,
                "candidate_id": candidate_id,
                "phase": phase,
                "status": "running",
                "segments": [],
                **admission,
                "partial_evidence_preserved": True,
                "partial_qualification_reusable": False,
                "requires_whole_stage_rerun": False,
                "secrets_persisted": False,
                "raw_prompts_persisted": False,
                "raw_responses_persisted": False,
            }
            self._write_index()
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def _prepare_checkpoint_root(self) -> Path:
        h0_root = self.root / "h0"
        checkpoint_root = h0_root / "checkpoints"
        if h0_root.is_symlink() or checkpoint_root.is_symlink():
            raise H0ManifestError("checkpoint namespace symlink is forbidden")
        h0_root.mkdir(parents=True, exist_ok=True)
        checkpoint_root.mkdir(exist_ok=True)
        try:
            checkpoint_root.resolve().relative_to(self.root)
        except ValueError:
            raise H0ManifestError("checkpoint namespace escapes root") from None
        return checkpoint_root

    @classmethod
    def _valid_h0_b_harness_repair_contract(
        cls,
        repair: Mapping[str, Any],
        *,
        stage_attempt_id: str,
    ) -> bool:
        decision_path = repair.get("decision_path")
        return (
            set(repair) == cls._H0_B_HARNESS_REPAIR_FIELDS
            and repair.get("schema_version")
            == "membind.h0.harness-repair-admission.v1"
            and repair.get("protocol_version") == PROTOCOL_VERSION
            and repair.get("candidate_id") == "Q1"
            and repair.get("phase") == "H0-B"
            and repair.get("decision_result_blind") is False
            and repair.get("prior_model_workload_output_observed") is False
            and repair.get("repair_required_independent_of_model_output") is True
            and repair.get("scientific_configuration_unchanged") is True
            and repair.get("one_shot_whole_stage_replacement") is True
            and repair.get("replacement_attempt_id") == stage_attempt_id
            and repair.get("old_attempt_qualification_reusable") is False
            and repair.get("old_and_new_trial_counts_mergeable") is False
            and repair.get("secrets_persisted") is False
            and all(
                _SHA256_RE.fullmatch(str(repair.get(field) or "")) is not None
                for field in (
                    "decision_sha256",
                    "invalidated_checkpoint_index_sha256",
                    "failure_report_sha256",
                    "prior_manifest_index_sha256",
                    "repaired_manifest_index_sha256",
                )
            )
            and isinstance(decision_path, str)
            and ".env" not in Path(decision_path).parts
            and "gpt55_temporary" not in Path(decision_path).parts
        )

    @classmethod
    def _valid_h0_b_infrastructure_rerun_contract(
        cls,
        admission: Mapping[str, Any],
        *,
        stage_attempt_id: str,
        interrupted_stage_attempt_id: str,
        interrupted_checkpoint_index_sha256: str,
        interrupted_stop_reason: str,
        repair_admission: Mapping[str, Any],
    ) -> bool:
        """Validate the independent one-shot grant after a stopped repair run."""

        decision_path = admission.get("decision_path")
        prior_manifest = admission.get("prior_manifest_index_sha256")
        recovered_manifest = admission.get("recovered_manifest_index_sha256")
        return (
            set(admission) == cls._H0_B_INFRASTRUCTURE_RERUN_FIELDS
            and admission.get("schema_version")
            == "membind.h0.infrastructure-rerun-admission.v1"
            and admission.get("protocol_version") == PROTOCOL_VERSION
            and admission.get("candidate_id") == "Q1"
            and admission.get("phase") == "H0-B"
            and admission.get("interrupted_stage_attempt_id")
            == interrupted_stage_attempt_id
            and admission.get("interrupted_checkpoint_index_sha256")
            == interrupted_checkpoint_index_sha256
            and admission.get("interrupted_stop_reason")
            == interrupted_stop_reason
            and admission.get("prior_harness_repair_admission_sha256")
            == canonical_json_sha256(repair_admission)
            and admission.get("replacement_attempt_id") == stage_attempt_id
            and admission.get("one_shot_whole_stage_replacement") is True
            and admission.get("resume_interrupted_attempt_allowed") is False
            and admission.get("prior_attempt_qualification_reusable") is False
            and admission.get("old_and_new_trial_counts_mergeable") is False
            and admission.get("scientific_configuration_unchanged") is True
            and prior_manifest
            == repair_admission.get("repaired_manifest_index_sha256")
            and prior_manifest != recovered_manifest
            and admission.get("secrets_persisted") is False
            and all(
                _SHA256_RE.fullmatch(str(admission.get(field) or "")) is not None
                for field in (
                    "decision_sha256",
                    "interrupted_checkpoint_index_sha256",
                    "prior_harness_repair_admission_sha256",
                    "prior_manifest_index_sha256",
                    "recovered_manifest_index_sha256",
                )
            )
            and isinstance(decision_path, str)
            and ".env" not in Path(decision_path).parts
            and "gpt55_temporary" not in Path(decision_path).parts
        )

    @classmethod
    def _valid_h0_b_post_workload_repair_contract(
        cls,
        admission: Mapping[str, Any],
        *,
        stage_attempt_id: str,
        failed_stage_attempt_id: str,
        failed_checkpoint_index_sha256: str,
        repair_admission: Mapping[str, Any],
        infrastructure_rerun_admission: Mapping[str, Any],
    ) -> bool:
        """Validate the independent non-blind grant after replacement-002."""

        decision_path = admission.get("decision_path")
        prior_manifest = admission.get("prior_manifest_index_sha256")
        repaired_manifest = admission.get("repaired_manifest_index_sha256")
        return (
            set(admission) == cls._H0_B_POST_WORKLOAD_REPAIR_FIELDS
            and admission.get("schema_version")
            == "membind.h0.post-workload-harness-repair-admission.v1"
            and admission.get("protocol_version") == PROTOCOL_VERSION
            and admission.get("candidate_id") == "Q1"
            and admission.get("phase") == "H0-B"
            and admission.get("decision_result_blind") is False
            and admission.get("prior_model_workload_output_observed") is True
            and admission.get(
                "repair_required_independent_of_model_response_content"
            )
            is True
            and admission.get("scientific_configuration_unchanged") is True
            and admission.get("one_shot_whole_stage_replacement") is True
            and admission.get("replacement_attempt_id") == stage_attempt_id
            and stage_attempt_id == "h0-q1-b-20260810-replacement-003"
            and admission.get("invalidated_stage_attempt_id")
            == failed_stage_attempt_id
            and admission.get("invalidated_checkpoint_index_sha256")
            == failed_checkpoint_index_sha256
            and admission.get("prior_harness_repair_admission_sha256")
            == canonical_json_sha256(repair_admission)
            and admission.get("prior_infrastructure_rerun_admission_sha256")
            == canonical_json_sha256(infrastructure_rerun_admission)
            and admission.get("old_attempt_qualification_reusable") is False
            and admission.get("old_and_new_trial_counts_mergeable") is False
            and admission.get("resume_failed_attempt_allowed") is False
            and prior_manifest
            == infrastructure_rerun_admission.get("recovered_manifest_index_sha256")
            and prior_manifest != repaired_manifest
            and admission.get("secrets_persisted") is False
            and all(
                _SHA256_RE.fullmatch(str(admission.get(field) or "")) is not None
                for field in (
                    "decision_sha256",
                    "invalidated_checkpoint_index_sha256",
                    "failure_segment_sha256",
                    "source_checkpoint_sha256",
                    "live_log_sha256",
                    "offline_probe_sha256",
                    "prior_harness_repair_admission_sha256",
                    "prior_infrastructure_rerun_admission_sha256",
                    "prior_manifest_index_sha256",
                    "repaired_manifest_index_sha256",
                )
            )
            and isinstance(decision_path, str)
            and decision_path
            == "artifacts/h0_protocol_repair/decisions/"
            "q1_h0_b_post_workload_harness_repair.json"
            and ".env" not in Path(decision_path).parts
            and "gpt55_temporary" not in Path(decision_path).parts
        )

    @classmethod
    def _valid_h0_b_r6_recovery_contract(
        cls,
        admission: Mapping[str, Any],
        *,
        stage_attempt_id: str,
        post_workload_repair_admission: Mapping[str, Any],
    ) -> bool:
        """Validate the independent R5->R6 recovery projection."""

        return (
            set(admission) == cls._H0_B_R6_RECOVERY_FIELDS
            and admission.get("schema_version")
            == "membind.h0.r6-recovery-admission.v1"
            and admission.get("protocol_version") == PROTOCOL_VERSION
            and admission.get("candidate_id") == "Q1"
            and admission.get("phase") == "H0-B"
            and admission.get("invalidated_stage_attempt_id")
            == "h0-q1-b-20260810-replacement-003"
            and admission.get("invalidated_checkpoint_index_sha256")
            == "0b813ee7c9f4940e6981398520bf823ced3544ff540f66e03a8181ead5622a76"
            and admission.get("failure_segment_sha256")
            == "d1fad184dec05c3e32907c142382d9d1dd3b5655f2042205b201da3b21d2b732"
            and admission.get("live_log_sha256")
            == "adf687a3a73f8acf100b5be561b2b471878b4e7fe696bf2c3200878501fea24e"
            and admission.get("misclassification_report_sha256")
            == "218b062834ed66e4bbdf6b65ecb405c5c17ce7c3889360534f2bec484c43a6ac"
            and admission.get("root_cause_report_sha256")
            == "153d480e4af93a38a5305bcf2b35d4e19a99d9c59860c20455e27e9a3e44430b"
            and admission.get("prior_manifest_index_sha256")
            == post_workload_repair_admission.get("repaired_manifest_index_sha256")
            and admission.get("scientific_failure_class")
            == "infrastructure_interruption"
            and admission.get("interrupted_stop_reason") == "vllm_unreachable"
            and admission.get("replacement_attempt_id") == stage_attempt_id
            and stage_attempt_id == "h0-q1-b-20260810-replacement-004"
            and admission.get("one_shot_whole_stage_replacement") is True
            and admission.get("resume_interrupted_attempt_allowed") is False
            and admission.get("old_attempt_qualification_reusable") is False
            and admission.get("old_and_new_trial_counts_mergeable") is False
            and admission.get("source_checkpoints_reusable") is False
            and admission.get("fresh_checkpoint_namespace_required") is True
            and admission.get("scientific_configuration_unchanged") is True
            and admission.get("live_authorized_by_this_admission") is False
            and admission.get("secrets_persisted") is False
            and all(
                _SHA256_RE.fullmatch(str(admission.get(field) or "")) is not None
                for field in cls._H0_B_R6_RECOVERY_FIELDS
                if field.endswith("sha256")
            )
        )

    def _validate_attempt_admission(
        self,
        *,
        checkpoint_root: Path,
        candidate_id: str,
        phase: str,
        repair_admission: Mapping[str, Any] | None,
        infrastructure_rerun_admission: Mapping[str, Any] | None,
        post_workload_repair_admission: Mapping[str, Any] | None,
        r6_recovery_admission: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        prior_matching = 0
        infrastructure_interrupted = 0
        terminal_attempts: list[tuple[Path, H0CheckpointStore]] = []
        interrupted_attempts: list[tuple[Path, H0CheckpointStore]] = []
        for path in sorted(checkpoint_root.iterdir()):
            if path.name.startswith(".admission.") and path.name.endswith(".lock"):
                if path.is_symlink() or not path.is_file():
                    raise H0ManifestError("checkpoint admission lock is invalid")
                continue
            if path.is_symlink() or not path.is_dir():
                raise H0ManifestError("checkpoint namespace contains an invalid entry")
            prior = self.open_existing(self.root, path.name)
            if (
                prior.index.get("candidate_id") != candidate_id
                or prior.index.get("phase") != phase
            ):
                continue
            prior_matching += 1
            status = prior.index.get("status")
            if status == "infrastructure_interrupted":
                infrastructure_interrupted += 1
                interrupted_attempts.append((path, prior))
                continue
            if status == "running":
                raise H0StateGateError("H0 stage already has an active attempt")
            terminal_attempts.append((path, prior))
        if terminal_attempts:
            repair = dict(repair_admission) if isinstance(repair_admission, Mapping) else {}
            protocol_repair_required = {
                "schema_version",
                "protocol_version",
                "candidate_id",
                "phase",
                "decision_path",
                "decision_sha256",
                "decision_result_blind",
                "one_shot_replacement",
                "replacement_attempt_id",
                "invalidated_stage_attempt_id",
                "invalidated_checkpoint_index_sha256",
                "old_attempt_qualification_reusable",
                "old_and_new_trial_counts_mergeable",
                "candidate_spec_projection_sha256",
                "repaired_manifest_index_sha256",
                "secrets_persisted",
            }
            exact_protocol_repair = (
                len(terminal_attempts) == 1
                and infrastructure_interrupted == 0
                and set(repair) == protocol_repair_required
                and repair.get("schema_version") == "membind.h0.repair-admission.v1"
                and repair.get("protocol_version") == PROTOCOL_VERSION
                and repair.get("candidate_id") == candidate_id == "Q1"
                and repair.get("phase") == phase == "H0-A"
                and repair.get("decision_result_blind") is False
                and repair.get("one_shot_replacement") is True
                and repair.get("replacement_attempt_id") == self.stage_attempt_id
                and repair.get("old_attempt_qualification_reusable") is False
                and repair.get("old_and_new_trial_counts_mergeable") is False
                and repair.get("secrets_persisted") is False
                and all(
                    _SHA256_RE.fullmatch(str(repair.get(field) or "")) is not None
                    for field in (
                        "decision_sha256",
                        "invalidated_checkpoint_index_sha256",
                        "candidate_spec_projection_sha256",
                        "repaired_manifest_index_sha256",
                    )
                )
            )
            old_path, old = terminal_attempts[0]
            exact_protocol_repair = exact_protocol_repair and (
                old.index.get("status") == "stage_complete"
                and old_path.name == repair.get("invalidated_stage_attempt_id")
                and sha256_file(old.index_path)
                == repair.get("invalidated_checkpoint_index_sha256")
                and isinstance(repair.get("decision_path"), str)
                and ".env" not in Path(str(repair.get("decision_path"))).parts
                and "gpt55_temporary"
                not in Path(str(repair.get("decision_path"))).parts
            )
            exact_harness_repair = (
                len(terminal_attempts) == 1
                and infrastructure_interrupted == 0
                and candidate_id == "Q1"
                and phase == "H0-B"
                and self._valid_h0_b_harness_repair_contract(
                    repair,
                    stage_attempt_id=self.stage_attempt_id,
                )
                and old.index.get("status") == "candidate_failed"
                and old.index.get("failure_code") == "manifest_contract_failure"
                and old_path.name == repair.get("invalidated_stage_attempt_id")
                and sha256_file(old.index_path)
                == repair.get("invalidated_checkpoint_index_sha256")
            )
            infrastructure = (
                dict(infrastructure_rerun_admission)
                if isinstance(infrastructure_rerun_admission, Mapping)
                else {}
            )
            exact_infrastructure_rerun = False
            if (
                len(terminal_attempts) == 1
                and len(interrupted_attempts) == 1
                and prior_matching == 2
                and infrastructure_interrupted == 1
                and candidate_id == "Q1"
                and phase == "H0-B"
            ):
                interrupted_path, interrupted = interrupted_attempts[0]
                exact_infrastructure_rerun = (
                    old.index.get("status") == "candidate_failed"
                    and old.index.get("failure_code")
                    == "manifest_contract_failure"
                    and old_path.name == repair.get("invalidated_stage_attempt_id")
                    and sha256_file(old.index_path)
                    == repair.get("invalidated_checkpoint_index_sha256")
                    and self._valid_h0_b_harness_repair_contract(
                        repair,
                        stage_attempt_id=interrupted_path.name,
                    )
                    and interrupted.index.get("status")
                    == "infrastructure_interrupted"
                    and interrupted.index.get("repair_admission") == repair
                    and interrupted.index.get("harness_repair_replacement") is True
                    and self._valid_h0_b_infrastructure_rerun_contract(
                        infrastructure,
                        stage_attempt_id=self.stage_attempt_id,
                        interrupted_stage_attempt_id=interrupted_path.name,
                        interrupted_checkpoint_index_sha256=sha256_file(
                            interrupted.index_path
                        ),
                        interrupted_stop_reason=str(
                            interrupted.index.get("stop_reason") or ""
                        ),
                        repair_admission=repair,
                    )
                )
            post_workload = (
                dict(post_workload_repair_admission)
                if isinstance(post_workload_repair_admission, Mapping)
                else {}
            )
            exact_post_workload_replacement = False
            if (
                len(terminal_attempts) == 2
                and len(interrupted_attempts) == 1
                and prior_matching == 3
                and infrastructure_interrupted == 1
                and candidate_id == "Q1"
                and phase == "H0-B"
            ):
                by_name = {path.name: (path, prior) for path, prior in terminal_attempts}
                original_entry = by_name.get(str(repair.get("invalidated_stage_attempt_id")))
                failed_entry = by_name.get(
                    str(post_workload.get("invalidated_stage_attempt_id"))
                )
                interrupted_path, interrupted = interrupted_attempts[0]
                if original_entry is not None and failed_entry is not None:
                    original_path, original = original_entry
                    failed_path, failed = failed_entry
                    failed_segments = failed.index.get("segments")
                    failure_entries = (
                        [
                            entry
                            for entry in failed_segments
                            if isinstance(entry, Mapping)
                            and entry.get("segment_kind") == "candidate_failure"
                        ]
                        if isinstance(failed_segments, list)
                        else []
                    )
                    source_entries = (
                        [
                            entry
                            for entry in failed_segments
                            if isinstance(entry, Mapping)
                            and entry.get("segment_kind") == "source_sequence"
                        ]
                        if isinstance(failed_segments, list)
                        else []
                    )
                    exact_post_workload_replacement = (
                        original.index.get("status") == "candidate_failed"
                        and original.index.get("failure_code")
                        == "manifest_contract_failure"
                        and sha256_file(original.index_path)
                        == repair.get("invalidated_checkpoint_index_sha256")
                        and self._valid_h0_b_harness_repair_contract(
                            repair,
                            stage_attempt_id=interrupted_path.name,
                        )
                        and interrupted.index.get("status")
                        == "infrastructure_interrupted"
                        and interrupted.index.get("repair_admission") == repair
                        and self._valid_h0_b_infrastructure_rerun_contract(
                            infrastructure,
                            stage_attempt_id=failed_path.name,
                            interrupted_stage_attempt_id=interrupted_path.name,
                            interrupted_checkpoint_index_sha256=sha256_file(
                                interrupted.index_path
                            ),
                            interrupted_stop_reason=str(
                                interrupted.index.get("stop_reason") or ""
                            ),
                            repair_admission=repair,
                        )
                        and failed.index.get("status") == "candidate_failed"
                        and failed.index.get("failure_code")
                        == "manifest_contract_failure"
                        and failed.index.get("repair_admission") == repair
                        and failed.index.get("infrastructure_rerun_admission")
                        == infrastructure
                        and failed.index.get("prior_matching_attempt_count") == 2
                        and failed.index.get(
                            "infrastructure_interrupted_attempt_count"
                        )
                        == 1
                        and failed.index.get("partial_qualification_reusable")
                        is False
                        and sha256_file(failed.index_path)
                        == post_workload.get(
                            "invalidated_checkpoint_index_sha256"
                        )
                        and len(failure_entries) == 1
                        and failure_entries[0].get("artifact_sha256")
                        == post_workload.get("failure_segment_sha256")
                        and len(source_entries) == 1
                        and source_entries[0].get("artifact_sha256")
                        == post_workload.get("source_checkpoint_sha256")
                        and self._valid_h0_b_post_workload_repair_contract(
                            post_workload,
                            stage_attempt_id=self.stage_attempt_id,
                            failed_stage_attempt_id=failed_path.name,
                            failed_checkpoint_index_sha256=sha256_file(
                                failed.index_path
                            ),
                            repair_admission=repair,
                            infrastructure_rerun_admission=infrastructure,
                        )
                    )
            r6 = (
                dict(r6_recovery_admission)
                if isinstance(r6_recovery_admission, Mapping)
                else {}
            )
            exact_r6_recovery = False
            if (
                len(terminal_attempts) == 3
                and len(interrupted_attempts) == 1
                and prior_matching == 4
                and infrastructure_interrupted == 1
                and candidate_id == "Q1"
                and phase == "H0-B"
                and self.stage_attempt_id == "h0-q1-b-20260810-replacement-004"
            ):
                by_name = {path.name: (path, prior) for path, prior in terminal_attempts}
                original_entry = by_name.get(str(repair.get("invalidated_stage_attempt_id")))
                failed_entry = by_name.get(str(post_workload.get("invalidated_stage_attempt_id")))
                misclassified_entry = by_name.get(
                    "h0-q1-b-20260810-replacement-003"
                )
                interrupted_path, interrupted = interrupted_attempts[0]
                if (
                    original_entry is not None
                    and failed_entry is not None
                    and misclassified_entry is not None
                ):
                    original_path, original = original_entry
                    failed_path, failed = failed_entry
                    misclassified_path, misclassified = misclassified_entry
                    mis_segments = misclassified.index.get("segments")
                    mis_sources = (
                        [
                            entry
                            for entry in mis_segments
                            if isinstance(entry, Mapping)
                            and entry.get("segment_kind") == "source_sequence"
                        ]
                        if isinstance(mis_segments, list)
                        else []
                    )
                    mis_failures = (
                        [
                            entry
                            for entry in mis_segments
                            if isinstance(entry, Mapping)
                            and entry.get("segment_kind") == "candidate_failure"
                        ]
                        if isinstance(mis_segments, list)
                        else []
                    )
                    exact_r6_recovery = (
                        original.index.get("status") == "candidate_failed"
                        and original.index.get("failure_code") == "manifest_contract_failure"
                        and sha256_file(original.index_path)
                        == repair.get("invalidated_checkpoint_index_sha256")
                        and self._valid_h0_b_harness_repair_contract(
                            repair, stage_attempt_id=interrupted_path.name
                        )
                        and interrupted.index.get("status") == "infrastructure_interrupted"
                        and interrupted.index.get("repair_admission") == repair
                        and self._valid_h0_b_infrastructure_rerun_contract(
                            infrastructure,
                            stage_attempt_id=failed_path.name,
                            interrupted_stage_attempt_id=interrupted_path.name,
                            interrupted_checkpoint_index_sha256=sha256_file(
                                interrupted.index_path
                            ),
                            interrupted_stop_reason=str(
                                interrupted.index.get("stop_reason") or ""
                            ),
                            repair_admission=repair,
                        )
                        and failed.index.get("status") == "candidate_failed"
                        and failed.index.get("failure_code") == "manifest_contract_failure"
                        and failed.index.get("repair_admission") == repair
                        and failed.index.get("infrastructure_rerun_admission") == infrastructure
                        and self._valid_h0_b_post_workload_repair_contract(
                            post_workload,
                            stage_attempt_id=misclassified_path.name,
                            failed_stage_attempt_id=failed_path.name,
                            failed_checkpoint_index_sha256=sha256_file(
                                failed.index_path
                            ),
                            repair_admission=repair,
                            infrastructure_rerun_admission=infrastructure,
                        )
                        and misclassified.index.get("status") == "candidate_failed"
                        and misclassified.index.get("failure_code")
                        == "candidate_qualification_failure"
                        and misclassified.index.get("repair_admission") == repair
                        and misclassified.index.get("infrastructure_rerun_admission")
                        == infrastructure
                        and misclassified.index.get("post_workload_repair_admission")
                        == post_workload
                        and len(mis_sources) == 6
                        and len(mis_failures) == 1
                        and mis_failures[0].get("artifact_sha256")
                        == r6.get("failure_segment_sha256")
                        and sha256_file(misclassified.index_path)
                        == r6.get("invalidated_checkpoint_index_sha256")
                        and self._valid_h0_b_r6_recovery_contract(
                            r6,
                            stage_attempt_id=self.stage_attempt_id,
                            post_workload_repair_admission=post_workload,
                        )
                    )
            if not (
                exact_protocol_repair
                or exact_harness_repair
                or exact_infrastructure_rerun
                or exact_post_workload_replacement
                or exact_r6_recovery
            ):
                raise H0StateGateError("H0 stage already has a non-rerunnable terminal")
            return {
                "prior_matching_attempt_count": prior_matching,
                "infrastructure_interrupted_attempt_count": (
                    1
                    if exact_infrastructure_rerun
                    or exact_post_workload_replacement
                    or exact_r6_recovery
                    else 0
                ),
                "whole_stage_rerun": True,
                "protocol_repair_replacement": True,
                "harness_repair_replacement": (
                    exact_harness_repair
                    or exact_infrastructure_rerun
                    or exact_post_workload_replacement
                    or exact_r6_recovery
                ),
                "infrastructure_rerun_replacement": (
                    exact_infrastructure_rerun
                    or exact_post_workload_replacement
                    or exact_r6_recovery
                ),
                "post_workload_harness_replacement": (
                    exact_post_workload_replacement or exact_r6_recovery
                ),
                "r6_recovery_replacement": exact_r6_recovery,
                "historically_misclassified_infrastructure_attempt_count": (
                    1 if exact_r6_recovery else 0
                ),
                "repair_admission": repair,
                **(
                    {"infrastructure_rerun_admission": infrastructure}
                    if exact_infrastructure_rerun
                    or exact_post_workload_replacement
                    or exact_r6_recovery
                    else {}
                ),
                **(
                    {"post_workload_repair_admission": post_workload}
                    if exact_post_workload_replacement or exact_r6_recovery
                    else {}
                ),
                **({"r6_recovery_admission": r6} if exact_r6_recovery else {}),
            }
        if infrastructure_rerun_admission is not None:
            raise H0StateGateError("H0 infrastructure rerun admission is not applicable")
        if post_workload_repair_admission is not None:
            raise H0StateGateError("H0 post-workload repair admission is not applicable")
        if r6_recovery_admission is not None:
            raise H0StateGateError("H0 R6 recovery admission is not applicable")
        return {
            "prior_matching_attempt_count": prior_matching,
            "infrastructure_interrupted_attempt_count": infrastructure_interrupted,
            "whole_stage_rerun": prior_matching > 0,
            "protocol_repair_replacement": False,
            "harness_repair_replacement": False,
            "infrastructure_rerun_replacement": False,
            "post_workload_harness_replacement": False,
            "r6_recovery_replacement": False,
            "historically_misclassified_infrastructure_attempt_count": 0,
        }

    @classmethod
    def open_existing(
        cls,
        root: str | Path,
        stage_attempt_id: str,
        *,
        progress_sink: Callable[[dict[str, Any]], Any] | None = None,
    ) -> "H0CheckpointStore":
        if (
            not isinstance(stage_attempt_id, str)
            or cls._IDENTIFIER_RE.fullmatch(stage_attempt_id) is None
        ):
            raise H0ManifestError("checkpoint attempt ID is invalid")
        instance = cls.__new__(cls)
        instance.root = Path(root).resolve()
        instance.stage_attempt_id = stage_attempt_id
        instance.directory = instance.root / "h0" / "checkpoints" / stage_attempt_id
        instance.index_path = instance.directory / "index.json"
        instance.progress_sink = progress_sink
        try:
            instance.index = json.loads(instance.index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise H0ManifestError("checkpoint index is missing or invalid") from exc
        if instance.index.get("stage_attempt_id") != stage_attempt_id:
            raise H0ManifestError("checkpoint attempt ID mismatch")
        instance._verify_existing()
        return instance

    @classmethod
    def _assert_safe(cls, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).strip().casefold().replace("-", "_")
                if normalized in cls._FORBIDDEN_KEYS:
                    raise ValueError(f"unsafe checkpoint field: {key}")
                cls._assert_safe(child)
        elif isinstance(value, list | tuple):
            for child in value:
                cls._assert_safe(child)
        elif isinstance(value, str):
            lowered = value.casefold()
            if (
                "bearer " in lowered
                or ".env" in lowered
                or "gpt55_temporary" in lowered
            ):
                raise ValueError("unsafe checkpoint string value")

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _atomic_json(self, path: Path, value: Mapping[str, Any]) -> None:
        payload = json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        ) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_name = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary_name).replace(path)
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)

    def _verify_existing(self) -> None:
        required = {
            "schema_version": "membind.h0.checkpoint-index.v1",
            "protocol_version": PROTOCOL_VERSION,
            "stage_attempt_id": self.stage_attempt_id,
            "secrets_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
        }
        for field, expected in required.items():
            if self.index.get(field) != expected:
                raise H0ManifestError(f"checkpoint index field mismatch: {field}")
        if self.index.get("candidate_id") not in _CANDIDATE_ORDER:
            raise H0ManifestError("checkpoint candidate is invalid")
        if self.index.get("phase") not in {"H0-A", "H0-B", "H0-C"}:
            raise H0ManifestError("checkpoint phase is invalid")
        prior_count = self.index.get("prior_matching_attempt_count")
        interrupted_count = self.index.get(
            "infrastructure_interrupted_attempt_count"
        )
        protocol_repair = self.index.get("protocol_repair_replacement") is True
        harness_repair = self.index.get("harness_repair_replacement") is True
        infrastructure_rerun = (
            self.index.get("infrastructure_rerun_replacement") is True
        )
        post_workload_replacement = (
            self.index.get("post_workload_harness_replacement") is True
        )
        r6_recovery = self.index.get("r6_recovery_replacement") is True
        standard_admission = (
            not harness_repair
            and not infrastructure_rerun
            and not post_workload_replacement
            and not r6_recovery
            and interrupted_count == prior_count
            and self.index.get("whole_stage_rerun") is (prior_count > 0)
        )
        repair_admission = self.index.get("repair_admission")
        protocol_repair_fields_valid = (
            protocol_repair
            and not harness_repair
            and prior_count == 1
            and interrupted_count == 0
            and self.index.get("whole_stage_rerun") is True
            and isinstance(repair_admission, Mapping)
            and repair_admission.get("schema_version")
            == "membind.h0.repair-admission.v1"
            and repair_admission.get("replacement_attempt_id")
            == self.stage_attempt_id
        )
        harness_repair_fields_valid = (
            protocol_repair
            and harness_repair
            and prior_count == 1
            and interrupted_count == 0
            and self.index.get("whole_stage_rerun") is True
            and isinstance(repair_admission, Mapping)
            and self._valid_h0_b_harness_repair_contract(
                repair_admission,
                stage_attempt_id=self.stage_attempt_id,
            )
        )
        infrastructure_admission = self.index.get(
            "infrastructure_rerun_admission"
        )
        infrastructure_rerun_fields_valid = False
        if (
            protocol_repair
            and harness_repair
            and infrastructure_rerun
            and prior_count == 2
            and interrupted_count == 1
            and self.index.get("whole_stage_rerun") is True
            and isinstance(repair_admission, Mapping)
            and isinstance(infrastructure_admission, Mapping)
        ):
            interrupted_id = infrastructure_admission.get(
                "interrupted_stage_attempt_id"
            )
            interrupted_path = (
                self.root / "h0" / "checkpoints" / str(interrupted_id) / "index.json"
            )
            try:
                interrupted_index = json.loads(
                    interrupted_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                interrupted_index = None
            infrastructure_rerun_fields_valid = (
                isinstance(interrupted_index, Mapping)
                and interrupted_index.get("stage_attempt_id") == interrupted_id
                and interrupted_index.get("candidate_id")
                == self.index.get("candidate_id")
                and interrupted_index.get("phase") == self.index.get("phase")
                and interrupted_index.get("status")
                == "infrastructure_interrupted"
                and interrupted_index.get("repair_admission") == repair_admission
                and self._valid_h0_b_infrastructure_rerun_contract(
                    infrastructure_admission,
                    stage_attempt_id=self.stage_attempt_id,
                    interrupted_stage_attempt_id=str(interrupted_id or ""),
                    interrupted_checkpoint_index_sha256=(
                        sha256_file(interrupted_path)
                        if interrupted_path.is_file()
                        else ""
                    ),
                    interrupted_stop_reason=str(
                        interrupted_index.get("stop_reason") or ""
                    ),
                    repair_admission=repair_admission,
                )
            )
        post_workload_admission = self.index.get(
            "post_workload_repair_admission"
        )
        post_workload_fields_valid = False
        if (
            protocol_repair
            and harness_repair
            and infrastructure_rerun
            and post_workload_replacement
            and prior_count == 3
            and interrupted_count == 1
            and self.index.get("whole_stage_rerun") is True
            and isinstance(repair_admission, Mapping)
            and isinstance(infrastructure_admission, Mapping)
            and isinstance(post_workload_admission, Mapping)
        ):
            failed_id = post_workload_admission.get(
                "invalidated_stage_attempt_id"
            )
            failed_path = (
                self.root / "h0" / "checkpoints" / str(failed_id) / "index.json"
            )
            try:
                failed_index = json.loads(failed_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                failed_index = None
            failed_segments = (
                failed_index.get("segments")
                if isinstance(failed_index, Mapping)
                else None
            )
            failure_entries = (
                [
                    entry
                    for entry in failed_segments
                    if isinstance(entry, Mapping)
                    and entry.get("segment_kind") == "candidate_failure"
                ]
                if isinstance(failed_segments, list)
                else []
            )
            source_entries = (
                [
                    entry
                    for entry in failed_segments
                    if isinstance(entry, Mapping)
                    and entry.get("segment_kind") == "source_sequence"
                ]
                if isinstance(failed_segments, list)
                else []
            )
            post_workload_fields_valid = (
                isinstance(failed_index, Mapping)
                and failed_index.get("stage_attempt_id") == failed_id
                and failed_index.get("candidate_id") == self.index.get("candidate_id")
                and failed_index.get("phase") == self.index.get("phase")
                and failed_index.get("status") == "candidate_failed"
                and failed_index.get("failure_code")
                == "manifest_contract_failure"
                and failed_index.get("repair_admission") == repair_admission
                and failed_index.get("infrastructure_rerun_admission")
                == infrastructure_admission
                and len(failure_entries) == 1
                and failure_entries[0].get("artifact_sha256")
                == post_workload_admission.get("failure_segment_sha256")
                and len(source_entries) == 1
                and source_entries[0].get("artifact_sha256")
                == post_workload_admission.get("source_checkpoint_sha256")
                and self._valid_h0_b_post_workload_repair_contract(
                    post_workload_admission,
                    stage_attempt_id=self.stage_attempt_id,
                    failed_stage_attempt_id=str(failed_id or ""),
                    failed_checkpoint_index_sha256=(
                        sha256_file(failed_path) if failed_path.is_file() else ""
                    ),
                    repair_admission=repair_admission,
                    infrastructure_rerun_admission=infrastructure_admission,
                )
            )
        r6_recovery_admission = self.index.get("r6_recovery_admission")
        r6_recovery_fields_valid = False
        historical_misclassified_count = self.index.get(
            "historically_misclassified_infrastructure_attempt_count"
        )
        if (
            protocol_repair
            and harness_repair
            and infrastructure_rerun
            and post_workload_replacement
            and r6_recovery
            and prior_count == 4
            and interrupted_count == 1
            and historical_misclassified_count == 1
            and self.index.get("whole_stage_rerun") is True
            and isinstance(repair_admission, Mapping)
            and isinstance(infrastructure_admission, Mapping)
            and isinstance(post_workload_admission, Mapping)
            and isinstance(r6_recovery_admission, Mapping)
        ):
            misclassified_id = r6_recovery_admission.get(
                "invalidated_stage_attempt_id"
            )
            misclassified_path = (
                self.root / "h0" / "checkpoints" / str(misclassified_id) / "index.json"
            )
            try:
                misclassified_index = json.loads(
                    misclassified_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                misclassified_index = None
            mis_segments = (
                misclassified_index.get("segments")
                if isinstance(misclassified_index, Mapping)
                else None
            )
            mis_failures = (
                [
                    entry
                    for entry in mis_segments
                    if isinstance(entry, Mapping)
                    and entry.get("segment_kind") == "candidate_failure"
                ]
                if isinstance(mis_segments, list)
                else []
            )
            mis_sources = (
                [
                    entry
                    for entry in mis_segments
                    if isinstance(entry, Mapping)
                    and entry.get("segment_kind") == "source_sequence"
                ]
                if isinstance(mis_segments, list)
                else []
            )
            r6_recovery_fields_valid = (
                isinstance(misclassified_index, Mapping)
                and misclassified_index.get("stage_attempt_id") == misclassified_id
                and misclassified_index.get("candidate_id")
                == self.index.get("candidate_id")
                and misclassified_index.get("phase") == self.index.get("phase")
                and misclassified_index.get("status") == "candidate_failed"
                and misclassified_index.get("failure_code")
                == "candidate_qualification_failure"
                and misclassified_index.get("repair_admission") == repair_admission
                and misclassified_index.get("infrastructure_rerun_admission")
                == infrastructure_admission
                and misclassified_index.get("post_workload_repair_admission")
                == post_workload_admission
                and len(mis_sources) == 6
                and len(mis_failures) == 1
                and mis_failures[0].get("artifact_sha256")
                == r6_recovery_admission.get("failure_segment_sha256")
                and self._valid_h0_b_r6_recovery_contract(
                    r6_recovery_admission,
                    stage_attempt_id=self.stage_attempt_id,
                    post_workload_repair_admission=post_workload_admission,
                )
            )
        if (
            isinstance(prior_count, bool)
            or not isinstance(prior_count, int)
            or prior_count < 0
            or isinstance(interrupted_count, bool)
            or not isinstance(interrupted_count, int)
            or not (
                standard_admission
                if not protocol_repair
                else (
                    protocol_repair_fields_valid
                    or harness_repair_fields_valid
                    or infrastructure_rerun_fields_valid
                    or post_workload_fields_valid
                    or r6_recovery_fields_valid
                )
            )
        ):
            raise H0ManifestError("checkpoint attempt admission fields are invalid")
        status = self.index.get("status")
        if status not in {
            "running",
            "stage_complete",
            "candidate_failed",
            "infrastructure_interrupted",
        }:
            raise H0ManifestError("checkpoint status is invalid")
        if status == "stage_complete" and not (
            _SHA256_RE.fullmatch(str(self.index.get("terminal_result_sha256") or ""))
            and self.index.get("candidate_advance_allowed") is True
            and self.index.get("partial_qualification_reusable") is True
            and self.index.get("requires_whole_stage_rerun") is False
        ):
            raise H0ManifestError("completed checkpoint terminal fields are invalid")
        if status == "candidate_failed" and not (
            isinstance(self.index.get("failure_code"), str)
            and self._IDENTIFIER_RE.fullmatch(self.index["failure_code"]) is not None
            and _SHA256_RE.fullmatch(
                str(self.index.get("failure_evidence_sha256") or "")
            )
            and self.index.get("candidate_advance_allowed") is False
            and self.index.get("candidate_selection_may_continue") is True
            and self.index.get("partial_qualification_reusable") is False
            and self.index.get("requires_whole_stage_rerun") is False
        ):
            raise H0ManifestError("failed checkpoint terminal fields are invalid")
        if status == "infrastructure_interrupted" and not (
            self.index.get("stop_reason")
            in {"vllm_unreachable", "embedding_unreachable", "neo4j_unreachable"}
            and self.index.get("candidate_advance_allowed") is False
            and self.index.get("candidate_selection_may_continue") is False
            and self.index.get("partial_qualification_reusable") is False
            and self.index.get("requires_whole_stage_rerun") is True
        ):
            raise H0ManifestError("interrupted checkpoint terminal fields are invalid")
        segments = self.index.get("segments")
        if not isinstance(segments, list):
            raise H0ManifestError("checkpoint segment index is invalid")
        self._assert_safe(self.index)
        indexed_paths: set[Path] = set()
        seen_keys: set[tuple[str, str]] = set()
        for ordinal, entry in enumerate(segments):
            if not isinstance(entry, dict) or entry.get("segment_ordinal") != ordinal:
                raise H0ManifestError("checkpoint segment ordinal mismatch")
            kind = entry.get("segment_kind")
            segment_id = entry.get("segment_id")
            digest = entry.get("artifact_sha256")
            relative = entry.get("artifact_path")
            if (
                not isinstance(kind, str)
                or self._IDENTIFIER_RE.fullmatch(kind) is None
                or not isinstance(segment_id, str)
                or self._IDENTIFIER_RE.fullmatch(segment_id) is None
                or not isinstance(digest, str)
                or _SHA256_RE.fullmatch(digest) is None
                or not isinstance(relative, str)
            ):
                raise H0ManifestError("checkpoint segment reference is invalid")
            key = (kind, segment_id)
            if key in seen_keys:
                raise H0ManifestError("checkpoint segment key is duplicated")
            seen_keys.add(key)
            path = (self.root / relative).resolve()
            try:
                path.relative_to(self.directory.resolve())
            except ValueError:
                raise H0ManifestError("checkpoint segment path escapes attempt") from None
            if path.is_symlink() or not path.is_file():
                raise H0ManifestError("checkpoint segment artifact is missing")
            if sha256_file(path) != digest:
                raise H0ManifestError("checkpoint segment artifact hash mismatch")
            indexed_paths.add(path)
            try:
                artifact = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise H0ManifestError("checkpoint segment artifact is invalid") from exc
            if (
                not isinstance(artifact, dict)
                or artifact.get("schema_version")
                != "membind.h0.checkpoint-segment.v1"
                or artifact.get("protocol_version") != PROTOCOL_VERSION
                or artifact.get("stage_attempt_id") != self.stage_attempt_id
                or artifact.get("segment_ordinal") != ordinal
                or artifact.get("segment_kind") != kind
                or artifact.get("segment_id") != segment_id
            ):
                raise H0ManifestError("checkpoint segment artifact binding mismatch")
            self._assert_safe(artifact)
        actual_paths = {
            path.resolve()
            for path in self.directory.glob("*.json")
            if path.resolve() != self.index_path.resolve()
        }
        if actual_paths != indexed_paths:
            raise H0ManifestError("checkpoint attempt contains unindexed artifacts")

    def _write_index(self) -> None:
        self._assert_safe(self.index)
        self._atomic_json(self.index_path, self.index)

    def _emit(self, event: dict[str, Any]) -> None:
        if self.progress_sink is not None:
            self.progress_sink(deepcopy(event))

    def record_segment(
        self,
        segment_kind: str,
        segment_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self.index.get("status") != "running":
            raise RuntimeError("cannot append to a terminal checkpoint attempt")
        if (
            not isinstance(segment_kind, str)
            or self._IDENTIFIER_RE.fullmatch(segment_kind) is None
            or not isinstance(segment_id, str)
            or self._IDENTIFIER_RE.fullmatch(segment_id) is None
        ):
            raise ValueError("segment kind and ID are required")
        self._assert_safe(payload)
        if any(
            entry.get("segment_kind") == segment_kind
            and entry.get("segment_id") == segment_id
            for entry in self.index["segments"]
        ):
            raise H0ManifestError("checkpoint segment key is duplicated")
        ordinal = len(self.index["segments"])
        artifact = {
            "schema_version": "membind.h0.checkpoint-segment.v1",
            "protocol_version": PROTOCOL_VERSION,
            "stage_attempt_id": self.stage_attempt_id,
            "segment_ordinal": ordinal,
            "segment_kind": segment_kind,
            "segment_id": segment_id,
            "payload": deepcopy(dict(payload)),
            "secrets_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
        }
        encoded = json.dumps(
            artifact,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        ) + "\n"
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        path = self.directory / f"{ordinal:06d}.{segment_kind}.{segment_id}.{digest}.json"
        try:
            with path.open("xb") as handle:
                handle.write(encoded.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise H0ManifestError("checkpoint content-address collision") from None
        entry = {
            "segment_ordinal": ordinal,
            "segment_kind": segment_kind,
            "segment_id": segment_id,
            "artifact_path": self._relative(path),
            "artifact_sha256": digest,
        }
        self.index["segments"].append(entry)
        self._write_index()
        event = {"status": "segment_persisted", **entry}
        self._emit(event)
        return event

    def _require_running(self) -> None:
        if self.index.get("status") != "running":
            raise RuntimeError("checkpoint attempt is already terminal")

    def _terminal_event(self) -> dict[str, Any]:
        event = {
            "status": self.index["status"],
            "candidate_advance_allowed": self.index["candidate_advance_allowed"],
            "partial_qualification_reusable": self.index[
                "partial_qualification_reusable"
            ],
            "requires_whole_stage_rerun": self.index["requires_whole_stage_rerun"],
            "checkpoint_index_path": self._relative(self.index_path),
            "checkpoint_index_sha256": sha256_file(self.index_path),
        }
        if "candidate_selection_may_continue" in self.index:
            event["candidate_selection_may_continue"] = self.index[
                "candidate_selection_may_continue"
            ]
        return event

    def mark_stage_complete(self, terminal_result_sha256: str) -> dict[str, Any]:
        """Close a qualified stage only after its final result is durable."""

        self._require_running()
        if _SHA256_RE.fullmatch(str(terminal_result_sha256)) is None:
            raise ValueError("stage completion requires a result SHA-256")
        self.index.update(
            {
                "status": "stage_complete",
                "terminal_result_sha256": terminal_result_sha256,
                "candidate_advance_allowed": True,
                "partial_qualification_reusable": True,
                "requires_whole_stage_rerun": False,
            }
        )
        self._write_index()
        event = {
            **self._terminal_event(),
            "terminal_result_sha256": terminal_result_sha256,
        }
        self._emit(event)
        return event

    def mark_candidate_failure(
        self,
        failure_code: str,
        failure_evidence_sha256: str,
    ) -> dict[str, Any]:
        """Close a candidate stage without granting an automatic live advance."""

        self._require_running()
        if (
            not isinstance(failure_code, str)
            or self._IDENTIFIER_RE.fullmatch(failure_code) is None
        ):
            raise ValueError("candidate failure requires a stable failure code")
        if _SHA256_RE.fullmatch(str(failure_evidence_sha256)) is None:
            raise ValueError("candidate failure requires an evidence SHA-256")
        self.index.update(
            {
                "status": "candidate_failed",
                "failure_code": failure_code,
                "failure_evidence_sha256": failure_evidence_sha256,
                "candidate_advance_allowed": False,
                "candidate_selection_may_continue": True,
                "partial_qualification_reusable": False,
                "requires_whole_stage_rerun": False,
            }
        )
        self._write_index()
        event = {
            **self._terminal_event(),
            "failure_code": failure_code,
            "failure_evidence_sha256": failure_evidence_sha256,
        }
        self._emit(event)
        return event

    def mark_infrastructure_interruption(self, reason_code: str) -> dict[str, Any]:
        self._require_running()
        if reason_code not in {
            "vllm_unreachable",
            "embedding_unreachable",
            "neo4j_unreachable",
        }:
            raise ValueError("infrastructure interruption requires a stable reason code")
        self.index.update(
            {
                "status": "infrastructure_interrupted",
                "stop_reason": reason_code,
                "candidate_advance_allowed": False,
                "candidate_selection_may_continue": False,
                "partial_evidence_preserved": True,
                "partial_qualification_reusable": False,
                "requires_whole_stage_rerun": True,
            }
        )
        self._write_index()
        event = {
            **self._terminal_event(),
            "stop_reason": reason_code,
            "partial_evidence_preserved": True,
        }
        self._emit(event)
        return event
