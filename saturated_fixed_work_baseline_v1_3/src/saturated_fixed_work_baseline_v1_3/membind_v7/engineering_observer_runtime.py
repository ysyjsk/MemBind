"""Composite Bailian/SiliconFlow runtime for V7 engineering observation only.

This module is deliberately independent from the formal R1-R3 protocol and
from the treatment live runner.  Bailian supplies Graphiti construction calls;
SiliconFlow supplies embeddings.  The two authorities remain explicit in every
public identity and neither provider's evidence can authorize scientific gates.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.llm_client.client import LLMClient


CONSTRUCTION_AUTHORITY = (
    "alibaba-bailian-openai-compatible-engineering-json-object-v1"
)
CONSTRUCTION_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
CONSTRUCTION_MODEL = "qwen3.5-35b-a3b"
EMBEDDING_AUTHORITY = "siliconflow-openai-compatible-v1"
EMBEDDING_BASE_URL = "https://api.siliconflow.cn/v1"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
EMBEDDING_DIMENSION = 1024
NEO4J_URI = "bolt://localhost:7687"
DEFAULT_TIMEOUT_SECONDS = 900.0
MAX_COROUTINES = 8
LLM_ADMISSION_LIMIT = 2

_SHA256_LENGTH = 64
_CONSTRUCTION_FREEZE_SHA256 = (
    "05a79792a1ace075671c9c03d300ec510e84b38dc9bf37ee2932e5396db31bba"
)
_CONSTRUCTION_ARTIFACT_SHA256 = (
    "953d71abe978ff74ed549243ef5676b425b31b204c07223902a97a7fb6edba22"
)
_EMBEDDING_PROTOCOL_SHA256 = (
    "a3abb7e6ea481952ed868886bfd958bad9060812e42ca1eb3d96e46a1d77dd0a"
)
_METHOD_SELECTION_SHA256 = (
    "0a2958aeeedc2b7b8762247d5d6cf15252e2b6b737b5807a51a127461a632c53"
)


class CompositeEngineeringError(RuntimeError):
    """A composite engineering-only provider contract failed closed."""


def _fail(code: str) -> CompositeEngineeringError:
    return CompositeEngineeringError(code)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        _jsonable(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(child) for child in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(child) for child in value), key=repr)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    return value


def _require_equal(value: Any, expected: Any, field: str) -> None:
    if value != expected:
        raise _fail(f"composite engineering freeze field drifted: {field}")


def _read_freeze(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _fail("composite engineering freeze is unreadable") from error
    if not isinstance(value, dict):
        raise _fail("composite engineering freeze is invalid")
    return value


def load_composite_engineering_freeze(
    path: str | Path, *, verify_references: bool = True
) -> dict[str, Any]:
    """Load the exact composite identity and optionally verify bound artifacts."""

    selected = Path(path)
    value = _read_freeze(selected)
    for field, expected in {
        "schema_version": "membind.v7.composite-engineering-observer-freeze.v1",
        "status": "FROZEN_BEFORE_ENGINEERING_OBSERVER",
        "provider_identity_kind": "COMPOSITE_ENGINEERING_ONLY",
        "observer_only": True,
        "formal_r1_r3_eligible": False,
        "gate_a_e_evaluated": False,
        "gate_outcome": "NOT_EVALUATED",
        "treatment_authorized": False,
        "old_read_return_allowed": False,
        "native_demand_skip_allowed": False,
        "repair_apply_allowed": False,
        "response_replay_allowed": False,
        "scientific_method_selection_update_allowed": False,
        "raw_request_persistence_allowed": False,
        "raw_response_persistence_allowed": False,
        "raw_embedding_persistence_allowed": False,
        "credential_persistence_allowed": False,
    }.items():
        _require_equal(value.get(field), expected, field)

    construction = _mapping(value.get("construction"), "construction identity missing")
    for field, expected in {
        "authority": CONSTRUCTION_AUTHORITY,
        "base_url": CONSTRUCTION_BASE_URL,
        "model": CONSTRUCTION_MODEL,
        "api_key_env": "DASHSCOPE_API_KEY",
        "temperature": 0.0,
        "top_p": 1.0,
        "structured_output_mode": "json_object",
        "prompt_schema_injection": "graphiti-json-object-constrained-pydantic-v1",
        "response_validation": "pydantic-v2",
        "max_tokens_sent_for_structured_output": False,
        "enable_thinking": False,
        "sdk_max_retries": 0,
        "hard_attempt_limit_per_request": 1,
        "provider_freeze_path": "BAILIAN_ENGINEERING_PROVIDER_FREEZE_V2.json",
        "provider_freeze_sha256": _CONSTRUCTION_FREEZE_SHA256,
        "compatibility_artifact_path": (
            "artifacts/v7-bailian-engineering-json-object-20260826-003.json"
        ),
        "compatibility_artifact_sha256": _CONSTRUCTION_ARTIFACT_SHA256,
    }.items():
        _require_equal(construction.get(field), expected, f"construction.{field}")

    embedding = _mapping(value.get("embedding"), "embedding identity missing")
    for field, expected in {
        "authority": EMBEDDING_AUTHORITY,
        "base_url": EMBEDDING_BASE_URL,
        "model": EMBEDDING_MODEL,
        "dimension": EMBEDDING_DIMENSION,
        "api_key_env": "SILICONFLOW_API_KEY",
        "sdk_max_retries": 0,
        "hard_attempt_limit_per_request": 1,
        "source_protocol_path": "R1_R3_PROTOCOL_FREEZE_V5.json",
        "source_protocol_sha256": _EMBEDDING_PROTOCOL_SHA256,
        "runtime_dimension_policy": "EXACT_NO_TRUNCATION",
    }.items():
        _require_equal(embedding.get(field), expected, f"embedding.{field}")
    if construction["authority"] == embedding["authority"]:
        raise _fail("composite engineering provider authorities were mixed")

    backend = _mapping(value.get("backend"), "backend identity missing")
    for field, expected in {
        "provider": "neo4j",
        "uri": NEO4J_URI,
        "isolation": "fresh_group_namespace",
        "concurrent_external_writes_allowed": False,
    }.items():
        _require_equal(backend.get(field), expected, f"backend.{field}")
    runtime = _mapping(value.get("runtime"), "runtime identity missing")
    for field, expected in {
        "graphiti_version": "0.29.3",
        "graphiti_pin": "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        "max_coroutines": MAX_COROUTINES,
        "global_llm_admission": LLM_ADMISSION_LIMIT,
        "http_timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "logical_retry_policy": "single_attempt_direct_no_tenacity",
    }.items():
        _require_equal(runtime.get(field), expected, f"runtime.{field}")
    workload = _mapping(value.get("workload"), "engineering workload identity missing")
    for field, expected in {
        "dataset": "ai-hyz/MemoryAgentBench",
        "dataset_revision": "7ea066982b140a19337e17e60d45d4076e042faf",
        "local_file_sha256": (
            "97fd80207f3419fc57c3684db824334224546d6bdd62c17ef52cd116eec9ffc8"
        ),
        "context_index": 0,
        "source_start": 0,
        "source_count": 2,
    }.items():
        _require_equal(workload.get(field), expected, f"workload.{field}")
    method = _mapping(
        value.get("scientific_method_selection"),
        "scientific method-selection binding missing",
    )
    for field, expected in {
        "path": "METHOD_SELECTION.json",
        "sha256": _METHOD_SELECTION_SHA256,
        "update_allowed": False,
    }.items():
        _require_equal(method.get(field), expected, f"scientific_method_selection.{field}")

    harness = _mapping(value.get("observer_harness"), "observer harness binding missing")
    _require_equal(
        harness.get("schema_version"),
        "membind.v7.composite-engineering-observer-harness.v1",
        "observer_harness.schema_version",
    )
    sources = _mapping(harness.get("source_sha256"), "observer source bindings missing")
    expected_source_names = {
        "mab_quality_v2_final_qa/src/mab_quality_v2_final_qa/mab_main_dataset.py",
        "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/embedder/openai.py",
        "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/graphiti.py",
        "membind-validation/.venv/lib/python3.12/site-packages/graphiti_core/llm_client/openai_generic_client.py",
        "membind-validation/src/graphiti_native.py",
        "membind-validation/src/native_characterization_instrumentation.py",
        "membind-validation/src/native_characterization_tracing.py",
        "saturated_fixed_work_baseline_v1_3/scripts/run_v7_composite_engineering_observer.py",
        "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/engineering_observer_runtime.py",
        "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/graphiti_observer.py",
        "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_v7/observer_campaign.py",
    }
    if set(sources) != expected_source_names:
        raise _fail("observer harness source set drifted")

    if verify_references:
        root = selected.resolve().parent
        references = (
            (
                str(construction["provider_freeze_path"]),
                str(construction["provider_freeze_sha256"]),
            ),
            (
                str(construction["compatibility_artifact_path"]),
                str(construction["compatibility_artifact_sha256"]),
            ),
            (
                str(embedding["source_protocol_path"]),
                str(embedding["source_protocol_sha256"]),
            ),
            (str(method["path"]), str(method["sha256"])),
        )
        verify_source_bindings(root, dict(references))
        repository_root = selected.resolve().parents[2]
        verify_source_bindings(repository_root, sources)
        construction_artifact = _read_freeze(
            root / str(construction["compatibility_artifact_path"])
        )
        if (
            construction_artifact.get("status") != "PASS"
            or construction_artifact.get("classification")
            != "BAILIAN_CONSTRUCTION_COMPATIBLE"
            or construction_artifact.get("formal_r1_r3_eligible") is not False
        ):
            raise _fail("construction compatibility evidence is invalid")
        protocol = _read_freeze(root / str(embedding["source_protocol_path"]))
        provider = _mapping(protocol.get("provider"), "embedding source protocol invalid")
        if (
            provider.get("authority") != EMBEDDING_AUTHORITY
            or provider.get("base_url") != EMBEDDING_BASE_URL
            or provider.get("embedding_model") != EMBEDDING_MODEL
            or provider.get("embedding_dimension") != EMBEDDING_DIMENSION
        ):
            raise _fail("embedding source protocol identity drifted")
    return value


def verify_source_bindings(
    repository_root: str | Path, source_sha256: Mapping[str, str]
) -> dict[str, str]:
    """Verify relative source/artifact paths against explicit SHA-256 bindings."""

    if not isinstance(source_sha256, Mapping) or not source_sha256:
        raise _fail("source hash bindings are empty")
    root = Path(repository_root).resolve()
    actual: dict[str, str] = {}
    for name, expected in sorted(source_sha256.items()):
        relative = Path(name)
        if (
            not isinstance(name, str)
            or not name
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != name
            or not isinstance(expected, str)
            or len(expected) != _SHA256_LENGTH
            or any(character not in "0123456789abcdef" for character in expected)
        ):
            raise _fail("source hash binding is invalid")
        target = (root / relative).resolve()
        if root != target.parent and root not in target.parents:
            raise _fail("source hash path escaped its root")
        if not target.is_file():
            raise _fail("source hash target is missing")
        digest = _sha256(target)
        if digest != expected:
            raise _fail("source hash differs from freeze")
        actual[name] = digest
    return actual


def normalize_bailian_chat_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the frozen Bailian policy at the actual chat HTTP boundary."""

    normalized = dict(request)
    if normalized.get("model") != CONSTRUCTION_MODEL:
        raise _fail("Bailian construction model identity mismatch")
    response_format = normalized.get("response_format")
    structured = response_format is not None
    if structured:
        if response_format != {"type": "json_object"}:
            raise _fail("Bailian structured construction requires JSON Object mode")
        temperature = normalized.get("temperature", 0.0)
        if isinstance(temperature, bool) or float(temperature) != 0.0:
            raise _fail("Bailian structured construction temperature drifted")
        normalized.pop("max_tokens", None)
    top_p = normalized.get("top_p", 1.0)
    if isinstance(top_p, bool) or float(top_p) != 1.0:
        raise _fail("Bailian construction top_p drifted")
    normalized["top_p"] = 1.0
    extra = normalized.get("extra_body")
    selected_extra = dict(extra) if isinstance(extra, Mapping) else {}
    if selected_extra.get("enable_thinking") not in {None, False}:
        raise _fail("Bailian construction thinking policy drifted")
    if "chat_template_kwargs" in selected_extra:
        raise _fail("Bailian construction received an unsupported template extension")
    selected_extra["enable_thinking"] = False
    normalized["extra_body"] = selected_extra
    return normalized


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _usage_value(usage: Any, name: str) -> int:
    raw = _field(usage, name, 0)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise _fail("Bailian response usage metadata is invalid")
    return raw


class BailianChatCompletions:
    """Single-pass request normalization and sanitized response observation."""

    def __init__(self, inner: Any, *, response_observer: Any | None = None) -> None:
        if inner is None or not callable(getattr(inner, "create", None)):
            raise _fail("Bailian chat completion endpoint is invalid")
        if response_observer is not None and not callable(response_observer):
            raise _fail("Bailian response observer is invalid")
        self._inner = inner
        self._response_observer = response_observer

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        request = normalize_bailian_chat_request(kwargs)
        structured = request.get("response_format") == {"type": "json_object"}
        response = self._inner.create(*args, **request)
        if not inspect.isawaitable(response):
            raise _fail("Bailian chat completion operation must be async")
        result = await response
        choices = _field(result, "choices", [])
        if not isinstance(choices, list) or len(choices) != 1:
            raise _fail("Bailian response choice shape is invalid")
        choice = choices[0]
        finish_reason = _field(choice, "finish_reason")
        message = _field(choice, "message")
        content = _field(message, "content")
        if not isinstance(content, str) or (structured and not content.strip()):
            raise _fail("Bailian structured response content is invalid")
        usage = _field(result, "usage")
        encoded = content.encode("utf-8")
        observation = {
            "lane": "construction",
            "structured": structured,
            "finish_reason": None if finish_reason is None else str(finish_reason),
            "prompt_tokens": _usage_value(usage, "prompt_tokens"),
            "completion_tokens": _usage_value(usage, "completion_tokens"),
            "content_bytes": len(encoded),
            "content_sha256": hashlib.sha256(encoded).hexdigest(),
        }
        if self._response_observer is not None:
            observed = self._response_observer(dict(observation))
            if inspect.isawaitable(observed):
                await observed
        if structured and finish_reason != "stop":
            raise _fail("Bailian structured response finish reason is not stop")
        return result


class _BailianChat:
    def __init__(self, inner: Any, *, response_observer: Any | None = None) -> None:
        self._inner = inner
        self.completions = BailianChatCompletions(
            getattr(inner, "completions", None),
            response_observer=response_observer,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class BailianOpenAITransport:
    def __init__(self, inner: Any, *, response_observer: Any | None = None) -> None:
        self._inner = inner
        self.chat = _BailianChat(
            getattr(inner, "chat", None), response_observer=response_observer
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class ResponseModelValidatingLLMClient(LLMClient):
    """Require every declared Graphiti response model before returning data."""

    def __init__(self, inner: Any) -> None:
        if inner is None or not callable(getattr(inner, "generate_response", None)):
            raise _fail("Graphiti construction client is invalid")
        self._inner = inner
        super().__init__(getattr(inner, "config", None), cache=False)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def generate_response(self, *args: Any, **kwargs: Any) -> Any:
        response_model = kwargs.get("response_model")
        if response_model is None and len(args) >= 2:
            response_model = args[1]
        value = self._inner.generate_response(*args, **kwargs)
        if not inspect.isawaitable(value):
            raise _fail("Graphiti construction operation must be async")
        result = await value
        if response_model is None:
            if not isinstance(result, Mapping):
                raise _fail("Graphiti untyped construction response is invalid")
            return dict(result)
        try:
            validated = response_model.model_validate(result)
            dumped = validated.model_dump(mode="python")
        except Exception as error:
            raise _fail("Graphiti response failed Pydantic validation") from error
        if not isinstance(dumped, dict):
            raise _fail("Graphiti validated response shape is invalid")
        return dumped

    async def _generate_response(
        self,
        messages: Any,
        response_model: Any = None,
        max_tokens: int = 4096,
        model_size: Any = None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "response_model": response_model,
            "max_tokens": max_tokens,
        }
        if model_size is not None:
            kwargs["model_size"] = model_size
        return await self.generate_response(messages, **kwargs)

    def set_tracer(self, tracer: Any) -> None:
        super().set_tracer(tracer)
        setter = getattr(self._inner, "set_tracer", None)
        if callable(setter):
            setter(tracer)


@dataclass(frozen=True, slots=True)
class _EmbeddingConfig:
    embedding_model: str
    embedding_dim: int
    base_url: str = EMBEDDING_BASE_URL


class ExactDimensionEmbedder(EmbedderClient):
    """OpenAI-compatible embedder that rejects dimension drift before Graphiti."""

    def __init__(self, embeddings: Any, *, model: str, dimension: int) -> None:
        if embeddings is None or not callable(getattr(embeddings, "create", None)):
            raise _fail("SiliconFlow embedding endpoint is invalid")
        if model != EMBEDDING_MODEL or dimension != EMBEDDING_DIMENSION:
            raise _fail("SiliconFlow embedding identity drifted")
        self._embeddings = embeddings
        self.model = model
        self.dimension = dimension
        self.config = _EmbeddingConfig(model, dimension)

    def _vectors(self, response: Any, expected_count: int) -> list[list[float]]:
        data = _field(response, "data")
        if not isinstance(data, list) or len(data) != expected_count:
            raise _fail("SiliconFlow embedding response count is invalid")
        indexed: list[tuple[int, list[float]]] = []
        for fallback, item in enumerate(data):
            index = _field(item, "index", fallback)
            raw_vector = _field(item, "embedding")
            if (
                isinstance(index, bool)
                or not isinstance(index, int)
                or not isinstance(raw_vector, list)
                or len(raw_vector) != self.dimension
            ):
                raise _fail("SiliconFlow embedding dimension mismatch")
            vector: list[float] = []
            for raw in raw_vector:
                if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                    raise _fail("SiliconFlow embedding value is invalid")
                value = float(raw)
                if not math.isfinite(value):
                    raise _fail("SiliconFlow embedding value is non-finite")
                vector.append(value)
            indexed.append((index, vector))
        indexed.sort(key=lambda item: item[0])
        if [index for index, _vector in indexed] != list(range(expected_count)):
            raise _fail("SiliconFlow embedding indexes are invalid")
        return [vector for _index, vector in indexed]

    async def create(self, input_data: Any) -> list[float]:
        response = self._embeddings.create(input=input_data, model=self.model)
        if not inspect.isawaitable(response):
            raise _fail("SiliconFlow embedding operation must be async")
        return self._vectors(await response, 1)[0]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        if not isinstance(input_data_list, list) or not input_data_list:
            raise _fail("SiliconFlow embedding batch input is invalid")
        response = self._embeddings.create(input=input_data_list, model=self.model)
        if not inspect.isawaitable(response):
            raise _fail("SiliconFlow embedding operation must be async")
        return self._vectors(await response, len(input_data_list))


@dataclass(frozen=True, slots=True)
class CompositeRuntimeComponents:
    graphiti_type: Any
    llm_config_type: Any
    qwen_client_type: Any
    reranker_type: Any
    admitted_client_type: Any
    request_admission_type: Any
    openai_client_factory: Any


@dataclass(slots=True)
class CompositeEngineeringRuntime:
    graphiti: Any
    raw_llm: Any
    validated_llm: Any
    admitted_llm: Any
    embedder: ExactDimensionEmbedder
    construction_transport: Any
    embedding_transport: Any
    public_identity: dict[str, Any]
    execution_envelope_sha256: str
    shared_public_identity: dict[str, Any]
    shared_execution_envelope_sha256: str


def _production_components() -> CompositeRuntimeComponents:
    import httpx
    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
    from graphiti_core.llm_client.config import LLMConfig
    from openai import AsyncOpenAI

    from graphiti_native import QwenVLLMClient
    from paper_eval.membind_v1.admission import AdmittedLLMClient, RequestAdmission

    def openai_client_factory(
        *, api_key: str, base_url: str, timeout_seconds: float
    ) -> Any:
        timeout = httpx.Timeout(
            connect=min(10.0, timeout_seconds),
            read=timeout_seconds,
            write=timeout_seconds,
            pool=timeout_seconds,
        )
        http_client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
        )
        client._platform = "Linux"  # type: ignore[attr-defined]
        return client

    return CompositeRuntimeComponents(
        graphiti_type=Graphiti,
        llm_config_type=LLMConfig,
        qwen_client_type=QwenVLLMClient,
        reranker_type=OpenAIRerankerClient,
        admitted_client_type=AdmittedLLMClient,
        request_admission_type=RequestAdmission,
        openai_client_factory=openai_client_factory,
    )


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not isinstance(value, str) or not value:
        raise _fail(f"required runtime environment is missing: {name}")
    return value


def build_composite_engineering_runtime(
    *,
    env: Mapping[str, str],
    request_id_prefix: str,
    composite_freeze_path: str | Path,
    response_observer: Any | None = None,
    components: CompositeRuntimeComponents | None = None,
) -> CompositeEngineeringRuntime:
    """Build Graphiti with split construction/embedding provider authorities."""

    frozen = load_composite_engineering_freeze(composite_freeze_path)
    if not request_id_prefix:
        raise _fail("engineering observer request identity is missing")
    construction_key = _required(env, "DASHSCOPE_API_KEY")
    embedding_key = _required(env, "SILICONFLOW_API_KEY")
    neo4j_user = _required(env, "NEO4J_USER")
    neo4j_password = _required(env, "NEO4J_PASSWORD")
    neo4j_uri = str(env.get("NEO4J_URI", NEO4J_URI))
    if neo4j_uri != NEO4J_URI:
        raise _fail("engineering observer Neo4j endpoint drifted")
    try:
        max_coroutines = int(env.get("GRAPHITI_MAX_COROUTINES", str(MAX_COROUTINES)))
    except (TypeError, ValueError):
        raise _fail("engineering observer concurrency is invalid") from None
    if max_coroutines != MAX_COROUTINES:
        raise _fail("engineering observer concurrency drifted")
    timeout_seconds = float(frozen["runtime"]["http_timeout_seconds"])
    selected = components or _production_components()

    raw_construction_transport = selected.openai_client_factory(
        api_key=construction_key,
        base_url=CONSTRUCTION_BASE_URL,
        timeout_seconds=timeout_seconds,
    )
    construction_transport = BailianOpenAITransport(
        raw_construction_transport,
        response_observer=response_observer,
    )
    llm_config = selected.llm_config_type(
        api_key=construction_key,
        model=CONSTRUCTION_MODEL,
        small_model=CONSTRUCTION_MODEL,
        base_url=CONSTRUCTION_BASE_URL,
        temperature=0.0,
        max_tokens=16_384,
    )
    raw_llm = selected.qwen_client_type(
        config=llm_config,
        max_tokens=16_384,
        structured_output_mode="json_object",
        vllm_options_enabled=False,
        client=construction_transport,
    )
    native_generate_response = raw_llm._generate_response

    async def _single_attempt_generate_response(
        messages: Any,
        response_model: Any = None,
        max_tokens: int = 4096,
        model_size: Any = None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "messages": messages,
            "response_model": response_model,
            "max_tokens": max_tokens,
        }
        if model_size is not None:
            kwargs["model_size"] = model_size
        return await native_generate_response(**kwargs)

    raw_llm._generate_response_with_retry = _single_attempt_generate_response
    validated_llm = ResponseModelValidatingLLMClient(raw_llm)
    admitted_llm = selected.admitted_client_type(
        inner=validated_llm,
        admission=selected.request_admission_type(limit=LLM_ADMISSION_LIMIT),
        request_id_prefix=request_id_prefix,
    )

    embedding_transport = selected.openai_client_factory(
        api_key=embedding_key,
        base_url=EMBEDDING_BASE_URL,
        timeout_seconds=timeout_seconds,
    )
    embedder = ExactDimensionEmbedder(
        getattr(embedding_transport, "embeddings", None),
        model=EMBEDDING_MODEL,
        dimension=EMBEDDING_DIMENSION,
    )
    reranker = selected.reranker_type(llm_config, client=raw_llm.client)
    graphiti = selected.graphiti_type(
        uri=neo4j_uri,
        user=neo4j_user,
        password=neo4j_password,
        llm_client=validated_llm,
        embedder=embedder,
        cross_encoder=reranker,
        max_coroutines=max_coroutines,
    )
    graphiti.llm_client = admitted_llm
    graphiti.clients.llm_client = admitted_llm
    graphiti.embedder = embedder
    graphiti.clients.embedder = embedder
    public_identity = {
        "schema_version": "membind.v7.composite-engineering-runtime.v1",
        "provider_identity_kind": "COMPOSITE_ENGINEERING_ONLY",
        "construction": {
            "authority": CONSTRUCTION_AUTHORITY,
            "base_url": CONSTRUCTION_BASE_URL,
            "served_model_id": CONSTRUCTION_MODEL,
            "structured_output_mode": "json_object",
            "prompt_schema_injection": "graphiti-json-object-constrained-pydantic-v1",
            "response_validation": "pydantic-v2",
            "max_tokens_sent_for_structured_output": False,
            "enable_thinking": False,
            "sdk_max_retries": 0,
        },
        "embedding": {
            "authority": EMBEDDING_AUTHORITY,
            "base_url": EMBEDDING_BASE_URL,
            "served_model_id": EMBEDDING_MODEL,
            "dimension": EMBEDDING_DIMENSION,
            "dimension_policy": "EXACT_NO_TRUNCATION",
            "sdk_max_retries": 0,
        },
        "backend": {"provider": "neo4j", "uri": neo4j_uri},
        "graphiti_max_coroutines": max_coroutines,
        "global_llm_admission": LLM_ADMISSION_LIMIT,
        "logical_retry_policy": "single_attempt_direct_no_tenacity",
        "observer_only": True,
        "formal_r1_r3_eligible": False,
        "gate_outcome": "NOT_EVALUATED",
        "treatment_authorized": False,
    }
    identity_hash = _canonical_sha256(public_identity)
    return CompositeEngineeringRuntime(
        graphiti=graphiti,
        raw_llm=raw_llm,
        validated_llm=validated_llm,
        admitted_llm=admitted_llm,
        embedder=embedder,
        construction_transport=raw_construction_transport,
        embedding_transport=embedding_transport,
        public_identity=public_identity,
        execution_envelope_sha256=identity_hash,
        shared_public_identity=public_identity,
        shared_execution_envelope_sha256=identity_hash,
    )


def build_embedding_preflight_evidence(
    *, duration_ns: int, vector: Sequence[float]
) -> dict[str, Any]:
    """Return only shape evidence for a content-neutral embedding preflight."""

    if duration_ns < 0 or len(vector) != EMBEDDING_DIMENSION:
        raise _fail("embedding preflight evidence is invalid")
    return {
        "schema_version": "membind.v7.engineering-embedding-preflight.v1",
        "status": "PASS",
        "authority": EMBEDDING_AUTHORITY,
        "model": EMBEDDING_MODEL,
        "dimension": len(vector),
        "duration_ns": duration_ns,
        "vector_persisted": False,
        "input_persisted": False,
        "formal_r1_r3_eligible": False,
        "gate_outcome": "NOT_EVALUATED",
    }


def summarize_provider_observations(
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reduce construction responses to aggregate, content-free accounting."""

    structured_count = 0
    nonstructured_count = 0
    all_structured_stop = True
    prompt_tokens = 0
    completion_tokens = 0
    content_bytes = 0
    for row in observations:
        if row.get("lane") != "construction" or type(row.get("structured")) is not bool:
            raise _fail("construction response observation is invalid")
        for field in ("prompt_tokens", "completion_tokens", "content_bytes"):
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise _fail("construction response accounting is invalid")
        digest = row.get("content_sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != _SHA256_LENGTH
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise _fail("construction response digest is invalid")
        if row["structured"]:
            structured_count += 1
            all_structured_stop = (
                all_structured_stop and row.get("finish_reason") == "stop"
            )
        else:
            nonstructured_count += 1
        prompt_tokens += int(row["prompt_tokens"])
        completion_tokens += int(row["completion_tokens"])
        content_bytes += int(row["content_bytes"])
    return {
        "construction_call_count": len(observations),
        "structured_call_count": structured_count,
        "nonstructured_call_count": nonstructured_count,
        "all_structured_finish_reason_stop": all_structured_stop,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "response_content_bytes": content_bytes,
        "response_content_hashes_persisted": False,
    }


def build_engineering_observer_artifact(
    *,
    run_id: str,
    composite_freeze_path: str | Path,
    block_result: Mapping[str, Any],
    source_sha256: Mapping[str, str],
    method_selection_path: str | Path,
    embedding_preflight: Mapping[str, Any] | None = None,
    provider_observation_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize one real block without persisting raw observer/provider data."""

    if not run_id or "/" in run_id or "\\" in run_id:
        raise _fail("engineering observer run identity is invalid")
    frozen_path = Path(composite_freeze_path)
    frozen = load_composite_engineering_freeze(frozen_path)
    method_path = Path(method_selection_path)
    before = _sha256(method_path)
    if before != frozen["scientific_method_selection"]["sha256"]:
        raise _fail("scientific method selection changed before engineering observer")
    if (
        block_result.get("status") != "OBSERVER_ONLY"
        or block_result.get("real_graphiti_evidence") is not True
        or block_result.get("source_count") != 2
        or block_result.get("shadow_publication_calls") != 0
        or block_result.get("native_publication_calls") != 2
        or block_result.get("treatment_calls") != 0
    ):
        raise _fail("engineering observer block contract failed")
    pairs = block_result.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != 1:
        raise _fail("engineering observer pair count is invalid")
    identity = _mapping(
        block_result.get("provider_identity"),
        "engineering observer runtime identity is missing",
    )
    construction = _mapping(identity.get("construction"), "construction identity missing")
    embedding = _mapping(identity.get("embedding"), "embedding identity missing")
    if (
        identity.get("provider_identity_kind") != "COMPOSITE_ENGINEERING_ONLY"
        or construction.get("authority") != CONSTRUCTION_AUTHORITY
        or embedding.get("authority") != EMBEDDING_AUTHORITY
        or "provider" in identity
    ):
        raise _fail("engineering observer provider identity was mixed")
    if not isinstance(source_sha256, Mapping) or not source_sha256 or any(
        not isinstance(name, str)
        or not name
        or not isinstance(digest, str)
        or len(digest) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in digest)
        for name, digest in source_sha256.items()
    ):
        raise _fail("engineering observer source binding is invalid")
    if embedding_preflight is not None and (
        embedding_preflight.get("status") != "PASS"
        or embedding_preflight.get("dimension") != EMBEDDING_DIMENSION
        or embedding_preflight.get("vector_persisted") is not False
        or embedding_preflight.get("input_persisted") is not False
    ):
        raise _fail("engineering embedding preflight failed")
    if provider_observation_summary is not None and (
        provider_observation_summary.get("construction_call_count", 0) <= 0
        or provider_observation_summary.get("structured_call_count", 0) <= 0
        or provider_observation_summary.get("all_structured_finish_reason_stop")
        is not True
        or provider_observation_summary.get("response_content_hashes_persisted")
        is not False
    ):
        raise _fail("engineering construction response summary failed")
    after = _sha256(method_path)
    if after != before:
        raise _fail("scientific method selection changed during engineering observer")
    return {
        "schema_version": "membind.v7.composite-engineering-observer-artifact.v1",
        "status": "PASS",
        "mode": "ENGINEERING_OBSERVER",
        "run_id": run_id,
        "provider_identity_kind": "COMPOSITE_ENGINEERING_ONLY",
        "provider_identity": {
            "provider_identity_kind": "COMPOSITE_ENGINEERING_ONLY",
            "construction": {
                "authority": CONSTRUCTION_AUTHORITY,
                "model": CONSTRUCTION_MODEL,
            },
            "embedding": {
                "authority": EMBEDDING_AUTHORITY,
                "model": EMBEDDING_MODEL,
                "dimension": EMBEDDING_DIMENSION,
            },
        },
        "composite_freeze_sha256": _sha256(frozen_path),
        "source_sha256": dict(sorted(source_sha256.items())),
        "block_sha256": _canonical_sha256(block_result),
        "source_count": 2,
        "pair_count": 1,
        "shadow_publication_calls": 0,
        "native_publication_calls": 2,
        "treatment_calls": 0,
        "response_replay_calls": 0,
        "embedding_preflight": dict(embedding_preflight or {}),
        "provider_observation_summary": dict(provider_observation_summary or {}),
        "observer_only": True,
        "formal_r1_r3_eligible": False,
        "gate_a_e_evaluated": False,
        "gates": {name: "NOT_EVALUATED" for name in "ABCDE"},
        "gate_outcome": "NOT_EVALUATED",
        "selected_method": None,
        "treatment_authorized": False,
        "scientific_method_selection_updated": False,
        "method_selection_sha256_before": before,
        "method_selection_sha256_after": after,
        "raw_block_persisted": False,
        "raw_request_persisted": False,
        "raw_response_persisted": False,
        "raw_embedding_persisted": False,
        "credentials_recorded": False,
    }


__all__ = [
    "BailianChatCompletions",
    "BailianOpenAITransport",
    "CompositeEngineeringError",
    "CompositeEngineeringRuntime",
    "CompositeRuntimeComponents",
    "ExactDimensionEmbedder",
    "ResponseModelValidatingLLMClient",
    "build_composite_engineering_runtime",
    "build_embedding_preflight_evidence",
    "build_engineering_observer_artifact",
    "load_composite_engineering_freeze",
    "normalize_bailian_chat_request",
    "summarize_provider_observations",
    "verify_source_bindings",
]
