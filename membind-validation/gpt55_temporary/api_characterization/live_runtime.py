"""Live-only adapters for the bounded temporary API characterization.

All constructors accept injected dependencies so protocol behavior is covered
offline before any relay, GPU, or Neo4j access is possible.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping

from graphiti_core.llm_client.client import LLMClient, ModelSize
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

from gpt55_temporary.api_characterization.bounded_runner import (
    ApiAttemptCapExceeded,
    build_chat_request,
    interval_union_ns,
)
from gpt55_temporary.simple_judge.config_chat_judge import _atomic_write_json


VALIDATION_ROOT = Path(__file__).resolve().parents[2]
MAINLINE_SRC = VALIDATION_ROOT / "src"


def load_frozen_episode(
    *,
    dataset_path: str | Path,
    history_id: str,
    source_sequence: int,
    expected_sha256: str,
    records_loader: Callable[[str | Path], list[dict[str, Any]]],
    episode_builder: Callable[[dict[str, Any]], list[Any]],
) -> Any:
    """Select exactly one frozen development episode and verify its identity."""

    records = records_loader(dataset_path)
    selected = [
        record
        for record in records
        if isinstance(record, dict) and str(record.get("question_id")) == str(history_id)
    ]
    if len(selected) != 1:
        raise ValueError("frozen history is missing or non-unique")
    episodes = episode_builder(selected[0])
    if not 0 <= int(source_sequence) < len(episodes):
        raise ValueError("frozen source sequence is out of range")
    episode = episodes[int(source_sequence)]
    if str(getattr(episode, "question_id", "")) != str(history_id):
        raise ValueError("frozen episode history mismatch")
    if int(getattr(episode, "source_sequence", -1)) != int(source_sequence):
        raise ValueError("frozen episode source sequence mismatch")
    if str(getattr(episode, "source_hash", "")) != str(expected_sha256):
        raise ValueError("frozen episode SHA256 mismatch")
    return episode


def _response_format(response_model: type[Any] | None) -> dict[str, Any]:
    if response_model is None:
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": getattr(response_model, "__name__", "graphiti_response"),
            "schema": response_model.model_json_schema(),
        },
    }


def _strip_code_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1]).strip()
    return text


def _response_content(response: Any, expected_model: str) -> str:
    if isinstance(response, Mapping):
        returned_model = str(response.get("model") or "")
        choices = response.get("choices")
    else:
        returned_model = str(getattr(response, "model", "") or "")
        choices = getattr(response, "choices", None)
    if returned_model and returned_model != expected_model:
        raise ValueError("relay returned a different model")
    if not isinstance(choices, (list, tuple)) or not choices:
        raise ValueError("relay response has no choices")
    first = choices[0]
    message = first.get("message") if isinstance(first, Mapping) else getattr(first, "message", None)
    finish_reason = (
        first.get("finish_reason")
        if isinstance(first, Mapping)
        else getattr(first, "finish_reason", None)
    )
    if finish_reason not in {None, "stop"}:
        raise ValueError("relay response did not finish completely")
    content = (
        message.get("content")
        if isinstance(message, Mapping)
        else getattr(message, "content", None)
    )
    if not isinstance(content, str) or not content:
        raise ValueError("relay response has no assistant content")
    return _strip_code_fence(content)


class BoundedGraphitiLLMClient(LLMClient):
    """Graphiti client that performs one zero-retry Chat request per logical call."""

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str,
        transport: Any,
        max_api_attempts: int,
        max_tokens: int,
    ) -> None:
        super().__init__(
            LLMConfig(
                api_key=api_key,
                model=model,
                small_model=model,
                max_tokens=int(max_tokens),
            ),
            cache=False,
        )
        if max_api_attempts <= 0:
            raise ValueError("max_api_attempts must be positive")
        self.endpoint = str(endpoint)
        self._api_key = str(api_key)
        self.transport = transport
        self.max_api_attempts = int(max_api_attempts)
        self.attempt_count = 0
        # C1 discovers this attribute when the production transport exposes its
        # underlying AsyncOpenAI client.
        self.client = getattr(transport, "client", None)

    async def _generate_response(
        self,
        messages: list[Any],
        response_model: type[Any] | None = None,
        max_tokens: int = 2048,
        model_size: ModelSize = ModelSize.medium,
    ) -> dict[str, Any]:
        if self.attempt_count >= self.max_api_attempts:
            raise ApiAttemptCapExceeded("remote API attempt cap reached")
        payload = build_chat_request(
            model=str(self.model),
            messages=messages,
            max_tokens=int(max_tokens),
            response_format=_response_format(response_model),
        )
        self.attempt_count += 1
        response = await self.transport.post_json(
            url=self.endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "OpenAI/Python 1.0.0",
            },
            payload=payload,
            max_retries=0,
        )
        content = _response_content(response, str(self.model))
        if response_model is None:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("Graphiti response must be a JSON object")
            return parsed
        validated = response_model.model_validate_json(content)
        return dict(validated.model_dump())

    async def generate_response(
        self,
        messages: list[Any],
        response_model: type[Any] | None = None,
        max_tokens: int | None = None,
        model_size: ModelSize = ModelSize.medium,
        group_id: str | None = None,
        prompt_name: str | None = None,
        *,
        attribute_extraction: bool = False,
    ) -> dict[str, Any]:
        # Deliberately bypass LLMClient._generate_response_with_retry.  The
        # supplied Graphiti messages are forwarded without adding a message.
        return await self._generate_response(
            messages,
            response_model=response_model,
            max_tokens=int(max_tokens or self.max_tokens),
            model_size=model_size,
        )


class AsyncOpenAIChatTransport:
    """AsyncOpenAI facade with SDK retries, redirects, and env proxies disabled."""

    proxy_policy = "direct_openai_sdk"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        timeout_s: float,
        client: Any | None = None,
    ) -> None:
        self.endpoint = str(endpoint).rstrip("/")
        suffix = "/chat/completions"
        if not self.endpoint.endswith(suffix):
            raise ValueError("Chat endpoint must end with /chat/completions")
        self._owns_client = client is None
        if client is None:
            import httpx
            from openai import AsyncOpenAI

            http_client = httpx.AsyncClient(
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(float(timeout_s)),
            )
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=self.endpoint[: -len(suffix)],
                default_headers={"User-Agent": "OpenAI/Python 1.0.0"},
                max_retries=0,
                timeout=float(timeout_s),
                http_client=http_client,
            )
        self.client = client

    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        max_retries: int,
    ) -> dict[str, Any]:
        if max_retries != 0:
            raise ValueError("bounded Graphiti requires max_retries=0")
        if str(url).rstrip("/") != self.endpoint:
            raise ValueError("Chat transport endpoint mismatch")
        response = await self.client.chat.completions.create(**dict(payload))
        if isinstance(response, Mapping):
            return dict(response)
        dumped = response.model_dump()
        if not isinstance(dumped, dict):
            raise TypeError("Chat completion did not serialize to an object")
        return dumped

    async def close(self) -> None:
        if self._owns_client:
            await self.client.close()


class _RelevanceResult(BaseModel):
    relevant: bool


class BoundedRelayCrossEncoder:
    """Graphiti-compatible relevance ranking through the bounded LLM client."""

    def __init__(self, llm_client: BoundedGraphitiLLMClient) -> None:
        self.llm_client = llm_client

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        ranked: list[tuple[str, float]] = []
        for passage in passages:
            response = await self.llm_client.generate_response(
                [
                    Message(
                        role="system",
                        content=(
                            "You are an expert tasked with determining whether the "
                            "passage is relevant to the query."
                        ),
                    ),
                    Message(
                        role="user",
                        content=(
                            "Return JSON with relevant=true when PASSAGE is relevant "
                            f"to QUERY.\n<PASSAGE>\n{passage}\n</PASSAGE>\n"
                            f"<QUERY>\n{query}\n</QUERY>"
                        ),
                    ),
                ],
                response_model=_RelevanceResult,
                prompt_name="cross_encoder.relevance",
            )
            ranked.append((passage, 1.0 if response["relevant"] else 0.0))
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked


def _span_interval(span: Mapping[str, Any]) -> tuple[int, int]:
    try:
        start = int(span["start_ns"])
        end = int(span["end_ns"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("trace span requires closed start_ns/end_ns") from None
    if end < start:
        raise ValueError("trace span ends before it starts")
    return start, end


def _root_scoped_records(
    records: list[dict[str, Any]], root: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Return records whose parent chain reaches the unique add-episode root."""

    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        span_id = record.get("span_id")
        if not isinstance(span_id, str) or not span_id:
            raise ValueError("trace span requires a non-empty span_id")
        if span_id in by_id:
            raise ValueError("trace span ids must be unique")
        parent_span_id = record.get("parent_span_id")
        if parent_span_id is not None and (
            not isinstance(parent_span_id, str) or not parent_span_id
        ):
            raise ValueError("trace parent_span_id must be null or non-empty")
        by_id[span_id] = record

    root_id = str(root["span_id"])
    if root.get("parent_span_id") is not None:
        raise ValueError("add-episode root must not have a parent")

    membership: dict[str, bool] = {root_id: True}

    def belongs_to_root(span_id: str, visiting: set[str]) -> bool:
        if span_id in membership:
            return membership[span_id]
        if span_id in visiting:
            raise ValueError("trace parent graph contains a cycle")
        visiting.add(span_id)
        parent_span_id = by_id[span_id].get("parent_span_id")
        if parent_span_id is None:
            result = False
        elif parent_span_id not in by_id:
            raise ValueError("trace span references an unknown parent")
        else:
            result = belongs_to_root(parent_span_id, visiting)
        visiting.remove(span_id)
        membership[span_id] = result
        return result

    for span_id in by_id:
        belongs_to_root(span_id, set())
    return [record for record in records if membership[str(record["span_id"])]]


def analyze_trace_spans(spans: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize closed spans in the unique add-episode root subtree."""

    records = [dict(span) for span in spans]
    roots = [span for span in records if span.get("phase") == "add-episode"]
    if len(roots) != 1:
        raise ValueError("exactly one add-episode span is required")
    root_start, root_end = _span_interval(roots[0])
    wall_ns = root_end - root_start
    if wall_ns <= 0:
        raise ValueError("add-episode span must have positive duration")
    scoped_records = _root_scoped_records(records, roots[0])
    for span in scoped_records:
        interval = _span_interval(span)
        if interval[0] < root_start or interval[1] > root_end:
            raise ValueError("trace span falls outside add-episode root")

    api_spans = [
        span for span in scoped_records if span.get("phase") == "llm-transport"
    ]
    api_intervals = [_span_interval(span) for span in api_spans]
    api_union = interval_union_ns(api_intervals)
    api_sum = sum(end - start for start, end in api_intervals)
    api_fraction = api_union / wall_ns
    if not 0.0 <= api_fraction <= 1.0:
        raise ValueError("remote API wait union exceeds add-episode wall time")

    phases: dict[str, dict[str, int]] = {}
    for phase in sorted({str(span.get("phase")) for span in scoped_records}):
        phase_records = [
            span for span in scoped_records if str(span.get("phase")) == phase
        ]
        intervals = [_span_interval(span) for span in phase_records]
        phases[phase] = {
            "count": len(phase_records),
            "interval_union_ns": interval_union_ns(intervals),
            "request_or_span_ns_sum": sum(end - start for start, end in intervals),
        }
    return {
        "schema_version": "membind.temporary-api-characterization.analysis.v1",
        "add_episode_wall_ns": wall_ns,
        "client_observed_remote_api_wait_union_ns": api_union,
        "client_observed_remote_api_request_ns_sum": api_sum,
        "api_wait_wall_fraction": api_fraction,
        "api_latency_semantics": "black_box_caller_observed_wait_not_model_execution",
        "phases": phases,
    }


class C1TraceInstrumentor:
    """Bind the qualified C1 wrappers to one temporary episode and trace file."""

    def __init__(
        self,
        *,
        run_id: str,
        episode_id: str,
        source_sequence: int,
        run_dir: str | Path,
    ) -> None:
        if str(MAINLINE_SRC) not in sys.path:
            sys.path.insert(0, str(MAINLINE_SRC))
        from native_characterization_tracing import TraceRecorder

        self.run_id = str(run_id)
        self.episode_id = str(episode_id)
        self.source_sequence = int(source_sequence)
        self.run_dir = Path(run_dir)
        self.recorder = TraceRecorder()
        self._handle: Any | None = None

    def install(self, graphiti: Any) -> None:
        from native_characterization_instrumentation import (
            install_native_characterization_instrumentation,
        )

        if self._handle is not None:
            raise RuntimeError("C1 instrumentation is already installed")
        self._handle = install_native_characterization_instrumentation(
            graphiti,
            self.recorder,
        )

    def episode_scope(self) -> Any:
        return self.recorder.episode_scope(
            self.run_id,
            self.episode_id,
            self.source_sequence,
        )

    def restore(self) -> None:
        if self._handle is not None:
            self._handle.restore()
            self._handle = None

    def finalize(self) -> dict[str, Any]:
        from native_characterization_tracing import (
            DurableJsonlEnvelopeWriter,
            critical_path_ns,
        )

        envelope = self.recorder.episode_envelope(
            self.run_id,
            self.episode_id,
            self.source_sequence,
        )
        DurableJsonlEnvelopeWriter(self.run_dir / "trace.jsonl").write(envelope)
        analysis = analyze_trace_spans(envelope["spans"])
        records = [
            record
            for record in self.recorder.records
            if record.run_id == self.run_id
            and record.episode_id == self.episode_id
            and record.source_sequence == self.source_sequence
        ]
        roots = [record for record in records if record.phase == "add-episode"]
        if len(roots) == 1:
            analysis["critical_path_ns"] = critical_path_ns(roots[0].span_id, records)

        llm = [record for record in records if record.phase == "llm"]
        transport = [record for record in records if record.phase == "llm-transport"]
        embeddings = [record for record in records if record.phase == "embedding"]
        database = [record for record in records if record.phase == "database"]
        analysis["llm"] = {
            "logical_call_count": len(llm),
            "transport_attempt_count": len(transport),
            "retry_count": sum(int(record.metadata.get("retry_count", 0)) for record in llm),
            "input_tokens": sum(int(record.metadata.get("input_tokens", 0)) for record in llm),
            "output_tokens": sum(int(record.metadata.get("output_tokens", 0)) for record in llm),
            "calls_by_prompt_name": {
                prompt: sum(
                    1
                    for record in llm
                    if str(record.metadata.get("prompt_name", "unknown")) == prompt
                )
                for prompt in sorted(
                    {str(record.metadata.get("prompt_name", "unknown")) for record in llm}
                )
            },
        }
        analysis["embedding"] = {
            "call_count": len(embeddings),
            "text_count": sum(int(record.metadata.get("text_count", 0)) for record in embeddings),
            "dimensions": sorted(
                {
                    int(record.metadata.get("dimension", 0))
                    for record in embeddings
                    if int(record.metadata.get("dimension", 0)) > 0
                }
            ),
        }
        analysis["database"] = {
            "operation_count": len(database),
            "query_count": sum(record.operation_class == "query" for record in database),
            "write_count": sum(record.operation_class == "write" for record in database),
        }
        _atomic_write_json(self.run_dir / "trace_analysis.json", analysis)
        return analysis


class PreflightRejected(RuntimeError):
    def __init__(self, status_code: int | None, classification: str) -> None:
        self.status_code = int(status_code) if status_code is not None else None
        self.classification = str(classification)
        super().__init__("relay preflight rejected the bounded live run")


def read_successful_preflight(
    *,
    attempt_dir: str | Path,
    expected_model: str,
) -> dict[str, Any]:
    """Require one complete immutable simple-judge success before live setup."""

    root = Path(attempt_dir)
    try:
        summary = json.loads((root / "04_summary.json").read_text(encoding="utf-8"))
        transport = json.loads((root / "02_transport.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise PreflightRejected(None, "preflight_artifact_unreadable") from None
    status_code = transport.get("http_status")
    valid = (
        summary.get("status") == "success"
        and summary.get("returned_model") == expected_model
        and summary.get("attempt_count") == 1
        and status_code == 200
        and transport.get("attempt_count") == 1
    )
    if not valid:
        raise PreflightRejected(status_code, "preflight_artifact_not_successful")
    return {
        "ok": True,
        "status_code": 200,
        "classification": "single_request_chat_compatible",
        "model": expected_model,
        "attempt_count": 1,
    }


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def require_preflight_then_create(
    *,
    preflight: Callable[[], Awaitable[Mapping[str, Any]] | Mapping[str, Any]],
    graphiti_factory: Callable[[], Any],
) -> Any:
    """Prevent every Graphiti/GPU/DB constructor after a failed relay probe."""

    report = await _maybe_await(preflight())
    if not bool(report.get("ok")):
        raise PreflightRejected(
            report.get("status_code"),
            str(report.get("classification") or "preflight_failed"),
        )
    return await _maybe_await(graphiti_factory())
