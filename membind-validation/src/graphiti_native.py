"""Native Graphiti runners for M0 and M1."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from current_state_gate import LiveAction, require_live_action

from dataset import Episode
from instrumentation import apply_episode_metrics, current_episode_key, episode_scope
from structured_output import constrain_single_episode_indices
from tracing import EpisodeTrace, JsonlTraceWriter, now_ns


M0_NATIVE_SERIAL = "M0"
M1_WHOLE_PARALLEL_C8 = "M1"
DEFAULT_CONSTRUCTION_MODEL = "qwen3-32b-fp8"
CONSTRUCTION_MODEL_REVISION = "6e2312b85c2ae9a31f629f24493b79d8b02eab1a"

_LONGMEMEVAL_DATETIME_RE = re.compile(
    r"(?P<date>\d{4}/\d{2}/\d{2}) \((?P<weekday>Mon|Tue|Wed|Thu|Fri|Sat|Sun)\) "
    r"(?P<time>\d{2}:\d{2})"
)
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_CONTEXT_ERROR_RE = re.compile(
    r"maximum context length is\s+(?P<context>\d+)\s+tokens.*?"
    r"prompt contains at least\s+(?P<input>\d+)\s+input tokens",
    re.IGNORECASE | re.DOTALL,
)


def load_env_file(path: str | Path | None = None) -> dict[str, str]:
    """Load project-local KEY=VALUE settings without overriding the process env."""
    path = Path(path) if path is not None else Path(__file__).resolve().parents[1] / ".env"
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            loaded[key] = value
            if key.lower() == "no_proxy":
                existing = [item.strip() for item in os.environ.get(key, "").split(",") if item.strip()]
                configured = [item.strip() for item in value.split(",") if item.strip()]
                os.environ[key] = ",".join(dict.fromkeys([*existing, *configured]))
            else:
                os.environ.setdefault(key, value)
    return loaded


def parse_datetime(value: str) -> datetime:
    text = str(value).strip()
    longmemeval_match = _LONGMEMEVAL_DATETIME_RE.fullmatch(text)
    if longmemeval_match is not None:
        dt = datetime.strptime(
            f"{longmemeval_match['date']} {longmemeval_match['time']}",
            "%Y/%m/%d %H:%M",
        )
        supplied_weekday = longmemeval_match["weekday"]
        actual_weekday = _WEEKDAYS[dt.weekday()]
        if supplied_weekday != actual_weekday:
            raise ValueError(
                f"LongMemEval weekday mismatch: supplied {supplied_weekday}, "
                f"date is {actual_weekday}"
            )
        return dt.replace(tzinfo=timezone.utc)

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _episode_type_message() -> Any:
    from graphiti_core.nodes import EpisodeType

    return EpisodeType.message


def graphiti_episode_kwargs(episode: Episode) -> dict[str, Any]:
    return {
        "name": episode.name,
        "episode_body": episode.body,
        "source_description": "LongMemEval-S haystack session",
        "reference_time": parse_datetime(episode.reference_time),
        "source": _episode_type_message(),
        "group_id": episode.group_id,
    }


async def add_episode(graphiti: Any, episode: Episode) -> Any:
    return await graphiti.add_episode(**graphiti_episode_kwargs(episode))


async def run_native_serial(
    graphiti: Any,
    episodes: list[Episode],
    run_id: str,
    repeat: int,
    arrival_interval_ms: int,
    trace_writer: JsonlTraceWriter,
) -> None:
    queue: asyncio.Queue[tuple[Episode | None, EpisodeTrace | None]] = asyncio.Queue()
    start = now_ns()

    async def arrivals() -> None:
        for ep in episodes:
            target = start + int(ep.source_sequence * arrival_interval_ms * 1_000_000)
            delay = max(0, (target - now_ns()) / 1_000_000_000)
            if delay:
                await asyncio.sleep(delay)
            trace = EpisodeTrace(run_id, ep.question_id, M0_NATIVE_SERIAL, repeat, ep.source_sequence, now_ns())
            trace.queue_enter_time = now_ns()
            await queue.put((ep, trace))
        await queue.put((None, None))

    async def worker() -> None:
        while True:
            ep, trace = await queue.get()
            if ep is None or trace is None:
                break
            try:
                trace.add_episode_start = now_ns()
                with episode_scope(run_id, ep.source_sequence):
                    await add_episode(graphiti, ep)
                trace.add_episode_end = now_ns()
                trace.publish_time = trace.add_episode_end
            except Exception as exc:
                trace.error = repr(exc)
                trace.publish_time = now_ns()
                apply_episode_metrics(graphiti, trace)
                trace_writer.write(trace)
                raise
            apply_episode_metrics(graphiti, trace)
            trace_writer.write(trace)

    await asyncio.gather(arrivals(), worker())


async def run_whole_parallel(
    graphiti: Any,
    episodes: list[Episode],
    run_id: str,
    repeat: int,
    arrival_interval_ms: int,
    trace_writer: JsonlTraceWriter,
    max_concurrency: int = 8,
) -> None:
    sem = asyncio.Semaphore(max_concurrency)
    start = now_ns()

    async def one(ep: Episode) -> None:
        target = start + int(ep.source_sequence * arrival_interval_ms * 1_000_000)
        delay = max(0, (target - now_ns()) / 1_000_000_000)
        if delay:
            await asyncio.sleep(delay)
        trace = EpisodeTrace(run_id, ep.question_id, M1_WHOLE_PARALLEL_C8, repeat, ep.source_sequence, now_ns())
        trace.queue_enter_time = now_ns()
        async with sem:
            try:
                trace.add_episode_start = now_ns()
                with episode_scope(run_id, ep.source_sequence):
                    await add_episode(graphiti, ep)
                trace.add_episode_end = now_ns()
                trace.publish_time = trace.add_episode_end
            except Exception as exc:
                trace.error = repr(exc)
                trace.publish_time = now_ns()
                apply_episode_metrics(graphiti, trace)
                trace_writer.write(trace)
                raise
            apply_episode_metrics(graphiti, trace)
            trace_writer.write(trace)

    await asyncio.gather(*(one(ep) for ep in episodes))


def decoding_config_from_env() -> dict[str, Any]:
    return {
        "temperature": float(os.environ.get("CONSTRUCTION_TEMPERATURE", "0.0")),
        "top_p": float(os.environ.get("CONSTRUCTION_TOP_P", "1.0")),
        "max_tokens": int(os.environ.get("CONSTRUCTION_MAX_TOKENS", "2048")),
        "seed": int(os.environ.get("CONSTRUCTION_SEED", "20260806")),
    }


def wrap_prompt_cache(inner: Any, prompt_cache: Any, model_revision: str | None = None) -> Any:
    from response_cache import GraphitiPromptCacheLLM

    return GraphitiPromptCacheLLM(
        inner,
        prompt_cache,
        model_revision
        or os.environ.get("CONSTRUCTION_MODEL_REVISION", CONSTRUCTION_MODEL_REVISION),
        decoding_config_from_env(),
    )


def token_usage_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    result = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        if value is not None:
            result[name] = int(value)
    return result


def safe_structured_request_evidence(request: dict[str, Any]) -> dict[str, Any]:
    """Fingerprint a structured request without retaining any message content."""

    messages = request.get("messages")
    if not isinstance(messages, list):
        raise TypeError("structured request messages must be a list")
    roles: list[str] = []
    content_hashes: list[str] = []
    content_lengths: list[int] = []
    content_byte_lengths: list[int] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise TypeError(f"structured request message {index} must be an object")
        content = message.get("content")
        if not isinstance(content, str):
            raise TypeError(f"structured request message {index} content must be text")
        encoded = content.encode("utf-8")
        roles.append(str(message.get("role") or ""))
        content_hashes.append(hashlib.sha256(encoded).hexdigest())
        content_lengths.append(len(content))
        content_byte_lengths.append(len(encoded))

    response_format = request.get("response_format")
    if not isinstance(response_format, dict):
        raise TypeError("structured request response_format must be an object")
    json_schema_wrapper = response_format.get("json_schema")
    json_schema_wrapper = (
        json_schema_wrapper if isinstance(json_schema_wrapper, dict) else {}
    )
    schema = json_schema_wrapper.get("schema")
    schema = schema if isinstance(schema, dict) else {}
    structured_outputs = request.get("structured_outputs")
    backend_requested = (
        structured_outputs.get("backend")
        if isinstance(structured_outputs, dict)
        else None
    )
    extra_body = request.get("extra_body")
    extra_body = extra_body if isinstance(extra_body, dict) else {}
    chat_template_kwargs = extra_body.get("chat_template_kwargs")
    chat_template_kwargs = (
        dict(chat_template_kwargs) if isinstance(chat_template_kwargs, dict) else {}
    )

    canonical_request = json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    canonical_format = json.dumps(
        response_format,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("ascii")
    canonical_schema = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("ascii")
    return {
        "request_envelope_sha256": hashlib.sha256(canonical_request).hexdigest(),
        "model": str(request.get("model") or ""),
        "temperature": request.get("temperature"),
        "top_p": request.get("top_p"),
        "max_tokens": request.get("max_tokens"),
        "seed": request.get("seed"),
        "message_count": len(messages),
        "message_roles": roles,
        "message_content_sha256": content_hashes,
        "message_content_lengths": content_lengths,
        "message_content_byte_lengths": content_byte_lengths,
        "response_format_type": response_format.get("type"),
        "response_format_sha256": hashlib.sha256(canonical_format).hexdigest(),
        "json_schema_name": json_schema_wrapper.get("name"),
        "json_schema_sha256": hashlib.sha256(canonical_schema).hexdigest(),
        "structured_output_backend_requested": backend_requested,
        "chat_template_kwargs": chat_template_kwargs,
    }


def clamp_max_tokens(requested: int | None, frozen_limit: int) -> int:
    if frozen_limit <= 0:
        raise ValueError("frozen max_tokens must be positive")
    if requested is None:
        return int(frozen_limit)
    if requested <= 0:
        raise ValueError("requested max_tokens must be positive")
    return min(int(requested), int(frozen_limit))


def structured_retry_budgets(
    requested: int | None,
    frozen_limit: int,
    overflow_limit: int = 8_192,
) -> tuple[int, ...]:
    """Return the frozen request and one bounded overflow retry budget.

    Graphiti 0.29.3 asks for 16,384 tokens for edge extraction. The protocol
    request is clamped to 2,048, but a long structured edge list can be cut in
    the middle of a JSON string. One retry at most 8,192 tokens allows that
    response to finish while keeping the deviation explicit and bounded.
    """
    primary = clamp_max_tokens(requested, frozen_limit)
    overflow = min(int(overflow_limit), 8_192)
    if overflow <= 0:
        raise ValueError("overflow max_tokens must be positive")
    if overflow <= primary:
        return (primary,)
    return (primary, overflow)


def context_window_from_error(error: BaseException) -> int | None:
    """Extract the server context window from a standard vLLM context error."""
    match = _CONTEXT_ERROR_RE.search(str(error))
    if match is None:
        return None
    return int(match["context"])


def llm_metrics(llm_client: Any) -> dict[str, Any]:
    inner = getattr(llm_client, "inner", llm_client)
    usage = getattr(inner, "usage_totals", {}) or {}
    cache = getattr(llm_client, "cache", None)
    return {
        "llm_call_count": int(getattr(inner, "call_count", 0)),
        "llm_input_tokens": int(usage.get("prompt_tokens", 0)),
        "llm_output_tokens": int(usage.get("completion_tokens", 0)),
        "llm_total_tokens": int(usage.get("total_tokens", 0)),
        "structured_parse_failures": int(getattr(inner, "parse_failure_count", 0)),
        "structured_request_count": int(getattr(inner, "structured_request_count", 0)),
        "structured_response_failures": int(
            getattr(inner, "structured_response_failure_count", 0)
        ),
        "unexpected_prompt": bool(getattr(cache, "unexpected_prompt", False)),
    }


def llm_failure_records(llm_client: Any) -> list[dict[str, Any]]:
    inner = getattr(llm_client, "inner", llm_client)
    return list(getattr(inner, "failure_events", []) or [])


def unexpected_prompt_records(llm_client: Any) -> list[dict[str, Any]]:
    cache = getattr(llm_client, "cache", None)
    return list(getattr(cache, "unexpected_prompt_diagnostics", []) or [])


def build_qwen_graphiti_from_env(
    prompt_cache: Any | None = None,
    embedding_cache: Any | None = None,
    *,
    authorization_checker: Any = require_live_action,
) -> Any:
    authorization_checker(LiveAction.NEO4J_INTEGRATION)
    load_env_file()
    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.config import LLMConfig
    from deterministic_search import (
        install_edge_query_stabilizer,
        install_edge_search_stabilizer,
        install_node_query_stabilizer,
        install_node_resolution_stabilizer,
    )
    from embedding_cache import CachingCountingEmbedder
    from model_oracle_audit import CrossEncoderAuditWrapper

    install_edge_search_stabilizer()
    install_node_resolution_stabilizer()

    llm_key = os.environ.get("CONSTRUCTION_LLM_API_KEY") or os.environ.get("VLLM_API_KEY")
    if not llm_key:
        raise RuntimeError("Set CONSTRUCTION_LLM_API_KEY or VLLM_API_KEY")
    llm_base_url = os.environ.get("CONSTRUCTION_LLM_BASE_URL", "http://10.87.5.247:8000/v1/")
    llm_model = os.environ.get("CONSTRUCTION_LLM_MODEL", DEFAULT_CONSTRUCTION_MODEL)
    decoding = decoding_config_from_env()
    llm_config = LLMConfig(
        api_key=llm_key,
        model=llm_model,
        small_model=llm_model,
        base_url=llm_base_url,
        temperature=decoding["temperature"],
        max_tokens=decoding["max_tokens"],
    )
    llm_client = QwenVLLMClient(
        config=llm_config,
        max_tokens=decoding["max_tokens"],
        structured_output_mode="json_schema",
    )
    if prompt_cache is not None:
        llm_client = wrap_prompt_cache(
            llm_client,
            prompt_cache,
        )

    embed_base_url = os.environ.get("EMBEDDING_BASE_URL", "http://10.87.5.247:8001/v1")
    if not embed_base_url:
        raise RuntimeError("Set EMBEDDING_BASE_URL to the local Qwen embedding endpoint")
    embedder = CachingCountingEmbedder(
        OpenAIEmbedder(
            OpenAIEmbedderConfig(
                api_key=os.environ.get("EMBEDDING_API_KEY") or os.environ.get("VLLM_API_KEY"),
                base_url=embed_base_url,
                embedding_model=os.environ.get("EMBEDDING_MODEL", "qwen3-embedding-0.6b"),
                embedding_dim=int(os.environ.get("EMBEDDING_DIM", "1024")),
            )
        ),
        persistent_cache=embedding_cache,
    )
    reranker = CrossEncoderAuditWrapper(OpenAIRerankerClient(config=llm_config))
    graphiti = Graphiti(
        uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        user=os.environ.get("NEO4J_USER", "neo4j"),
        password=os.environ.get("NEO4J_PASSWORD", "password"),
        llm_client=llm_client,
        embedder=embedder,
        cross_encoder=reranker,
        max_coroutines=int(os.environ.get("GRAPHITI_MAX_COROUTINES", "8")),
    )
    install_edge_query_stabilizer(graphiti.driver)
    install_node_query_stabilizer(graphiti.driver)
    return graphiti


class QwenVLLMClient:
    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

        vllm_options_enabled = bool(kwargs.pop("vllm_options_enabled", True))

        class Client(OpenAIGenericClient):  # type: ignore[misc]
            def __init__(self, *client_args: Any, **client_kwargs: Any) -> None:
                super().__init__(*client_args, **client_kwargs)
                self.vllm_options_enabled = vllm_options_enabled
                self.call_count = 0
                self.parse_failure_count = 0
                self.structured_request_count = 0
                self.structured_response_failure_count = 0
                self.usage_totals = {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                }
                self.call_events: list[dict[str, Any]] = []
                self.failure_events: list[dict[str, Any]] = []
                self._last_call_record: contextvars.ContextVar[dict[str, Any] | None] = (
                    contextvars.ContextVar(f"qwen_last_call_{id(self)}", default=None)
                )

            def consume_last_call_record(self) -> dict[str, Any] | None:
                record = self._last_call_record.get()
                self._last_call_record.set(None)
                return record

            async def _generate_response(self, messages, response_model=None, max_tokens=2048, model_size=None):
                self.structured_request_count += 1
                openai_messages = []
                for m in messages:
                    content = self._clean_input(m.content)
                    if m.role in {"user", "system"}:
                        openai_messages.append({"role": m.role, "content": content})
                budgets = structured_retry_budgets(
                    max_tokens,
                    self.max_tokens,
                    int(os.environ.get("CONSTRUCTION_OVERFLOW_MAX_TOKENS", "8192")),
                )
                response_format = constrain_single_episode_indices(
                    self._build_response_format(response_model)
                )
                last_error: Exception | None = None
                context_budget: int | None = None
                context_limit: int | None = None
                attempted_budgets: set[int] = set()
                request_evidence_by_budget: dict[int, dict[str, Any]] = {}

                async def create_response(budget: int) -> Any:
                    request: dict[str, Any] = {
                        "model": self.model,
                        "messages": openai_messages,
                        "temperature": self.temperature,
                        "top_p": float(os.environ.get("CONSTRUCTION_TOP_P", "1.0")),
                        "max_tokens": budget,
                        "response_format": response_format,
                    }
                    if self.vllm_options_enabled:
                        request["seed"] = int(os.environ.get("CONSTRUCTION_SEED", "20260806"))
                        request["extra_body"] = {
                            "chat_template_kwargs": {"enable_thinking": False}
                        }
                    request_evidence_by_budget[budget] = safe_structured_request_evidence(
                        request
                    )
                    return await self.client.chat.completions.create(**request)

                for configured_budget in budgets:
                    budget = min(configured_budget, context_budget) if context_budget else configured_budget
                    if budget in attempted_budgets:
                        continue
                    is_context_probe = False
                    try:
                        response = await create_response(budget)
                    except Exception as exc:
                        attempted_budgets.add(budget)
                        context_limit = context_window_from_error(exc)
                        if context_limit is None or 1 in attempted_budgets:
                            self.structured_response_failure_count += 1
                            raise
                        budget = 1
                        is_context_probe = True
                        response = await create_response(budget)
                    attempted_budgets.add(budget)
                    self.call_count += 1
                    usage = token_usage_dict(response.usage)
                    finish_reason = getattr(response.choices[0], "finish_reason", None)
                    result = self._strip_code_fences(response.choices[0].message.content or "")
                    self.call_events.append(
                        {
                            "episode_key": current_episode_key(),
                            "token_usage": usage,
                            "max_tokens": budget,
                            "finish_reason": finish_reason,
                        }
                    )
                    for key in self.usage_totals:
                        self.usage_totals[key] += int(usage.get(key, 0))
                    if context_limit is not None and usage.get("prompt_tokens") is not None:
                        safety = int(
                            os.environ.get("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
                        )
                        context_budget = (
                            context_limit - int(usage["prompt_tokens"]) - max(0, safety)
                        )
                        if context_budget <= 0:
                            failure = RuntimeError(
                                "complete prompt leaves no usable construction response budget: "
                                f"prompt_tokens={int(usage['prompt_tokens'])}, "
                                f"context_limit={context_limit}, safety_tokens={max(0, safety)}, "
                                f"usable_completion_tokens={context_budget}"
                            )
                            self.failure_events.append(
                                {
                                    "failure_type": "context_budget_exhausted",
                                    "episode_key": current_episode_key(),
                                    "request_evidence": request_evidence_by_budget[budget],
                                    "max_tokens": budget,
                                    "finish_reason": finish_reason,
                                    "raw_response": result,
                                    "raw_response_sha256": hashlib.sha256(
                                        result.encode()
                                    ).hexdigest(),
                                    "raw_response_length": len(result),
                                    "token_usage": usage,
                                    "context_limit": context_limit,
                                    "prompt_tokens": int(usage["prompt_tokens"]),
                                    "safety_tokens": max(0, safety),
                                    "usable_completion_tokens": context_budget,
                                    "minimum_context_for_primary_budget": (
                                        int(usage["prompt_tokens"])
                                        + max(0, safety)
                                        + budgets[0]
                                    ),
                                    "minimum_context_for_overflow_budget": (
                                        int(usage["prompt_tokens"])
                                        + max(0, safety)
                                        + budgets[-1]
                                    ),
                                    "error": repr(failure),
                                }
                            )
                            self.structured_response_failure_count += 1
                            raise failure
                    try:
                        parsed = json.loads(result)
                        if response_model is not None:
                            response_model(**parsed)
                    except Exception as exc:
                        if not is_context_probe:
                            self.parse_failure_count += 1
                        self.failure_events.append(
                            {
                                "failure_type": (
                                    "context_probe" if is_context_probe else "structured_parse"
                                ),
                                "episode_key": current_episode_key(),
                                "request_evidence": request_evidence_by_budget[budget],
                                "max_tokens": budget,
                                "finish_reason": finish_reason,
                                "raw_response": result,
                                "raw_response_sha256": hashlib.sha256(result.encode()).hexdigest(),
                                "raw_response_length": len(result),
                                "token_usage": usage,
                                "error": repr(exc),
                            }
                        )
                        last_error = exc
                        if any(
                            (
                                min(next_budget, context_budget)
                                if context_budget
                                else next_budget
                            )
                            not in attempted_budgets
                            for next_budget in budgets
                        ):
                            continue
                        self.structured_response_failure_count += 1
                        raise
                    self._last_call_record.set(
                        {"raw_response": result, "token_usage": usage, "max_tokens": budget}
                    )
                    return parsed
                if last_error is not None:
                    self.structured_response_failure_count += 1
                    raise last_error
                self.structured_response_failure_count += 1
                raise RuntimeError("structured response generation produced no attempt")

        return Client(*args, **kwargs)
