"""Offline factory tests for the v3.1 live Graphiti runtime."""

from __future__ import annotations

from types import SimpleNamespace

import asyncio
import pytest

from paper_eval.membind_v1.live_runtime import RuntimeComponents
from paper_eval.membind_v31 import AdmissionPolicy
from paper_eval.membind_v31.live_runtime import build_membind_v31_runtime
from paper_eval.membind_v31.request_runtime import (
    AdmittedChatCompletionsV31,
    AdmittedLLMClientV31,
    MemBindV31RequestRuntimeError,
    llm_request_scope,
)
from paper_eval.membind_v31 import RequestKind
from paper_eval.membind_v31.prefix_affinity import PrefixMetadata


def _env() -> dict[str, str]:
    return {
        "CONSTRUCTION_LLM_API_KEY": "construction-secret",
        "CONSTRUCTION_LLM_BASE_URL": "http://10.87.5.247:8000/v1",
        "CONSTRUCTION_LLM_MODEL": "qwen3-32b-fp8",
        "EMBEDDING_API_KEY": "embedding-secret",
        "EMBEDDING_BASE_URL": "http://10.87.5.247:8001/v1",
        "EMBEDDING_MODEL": "qwen3-embedding-0.6b",
        "EMBEDDING_DIM": "1024",
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "database-secret",
        "GRAPHITI_MAX_COROUTINES": "8",
        "CONSTRUCTION_CACHE_SALT": "9" * 64,
        "MEMBIND_V31_TRACE_HMAC_KEY": (b"k" * 32).hex(),
    }


class _Value:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _Completions:
    async def create(self, **_kwargs):
        return {"ok": True}


class _RawLLM(_Value):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.client = SimpleNamespace(
            chat=SimpleNamespace(completions=_Completions()),
            embeddings=SimpleNamespace(create=_Completions().create),
        )

    async def generate_response(self, *_args, **_kwargs):
        return {"ok": True}


class _Embedder:
    def __init__(self, config):
        self.config = config
        self.client = SimpleNamespace(
            embeddings=SimpleNamespace(create=_Completions().create)
        )


class _Graphiti:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.llm_client = kwargs["llm_client"]
        self.clients = SimpleNamespace(llm_client=self.llm_client)


def _components() -> RuntimeComponents:
    return RuntimeComponents(
        graphiti_type=_Graphiti,
        llm_config_type=_Value,
        qwen_client_type=_RawLLM,
        embedder_config_type=_Value,
        embedder_type=_Embedder,
        reranker_type=lambda config, client=None: _Value(config=config, client=client),
    )


def _prefix_encoder(*_args, **_kwargs):
    return PrefixMetadata.from_token_ids(
        [1, 2, 3, 4],
        prefix_match_unit=4,
        tokenizer_identity_sha256="a" * 64,
        cache_identity_sha256="9" * 64,
        trace_hmac_key=b"a" * 32,
    )


def test_v31_runtime_reuses_shared_envelope_and_replaces_v1_gate_at_both_refs() -> None:
    events: list[dict[str, object]] = []
    runtime = build_membind_v31_runtime(
        env=_env(),
        policy=AdmissionPolicy.FIFO,
        request_id_prefix="v31-runtime",
        observer=events.append,
        components=_components(),
        prefix_encoder=_prefix_encoder,
    )

    assert isinstance(runtime.admitted_llm, AdmittedLLMClientV31)
    assert runtime.graphiti.llm_client is runtime.raw_llm
    assert runtime.graphiti.clients.llm_client is runtime.raw_llm
    assert runtime.raw_llm is not runtime.admitted_llm
    assert runtime.transport_admission_installed is True
    assert isinstance(
        runtime.raw_llm.client.chat.completions,
        AdmittedChatCompletionsV31,
    )
    reranker_transport = runtime.graphiti.kwargs["cross_encoder"].kwargs["client"]
    assert reranker_transport is runtime.raw_llm.client
    assert reranker_transport.chat.completions is runtime.raw_llm.client.chat.completions
    assert runtime.shared_public_identity["global_llm_admission_k"] == 2
    assert runtime.graphiti.kwargs["max_coroutines"] == 8
    assert "secret" not in repr(runtime.shared_public_identity)
    assert "secret" not in repr(runtime.method_public_identity)


def test_shared_graphiti_and_reranker_transport_calls_use_the_same_fail_closed_gate() -> None:
    runtime = build_membind_v31_runtime(
        env=_env(),
        policy=AdmissionPolicy.FIFO,
        request_id_prefix="v31-shared-transport",
        components=_components(),
        prefix_encoder=_prefix_encoder,
    )
    shared = runtime.raw_llm.client.chat.completions
    reranker = runtime.graphiti.kwargs["cross_encoder"].kwargs["client"].chat.completions

    async def scenario() -> None:
        with pytest.raises(
            MemBindV31RequestRuntimeError,
            match="llm_request_scope_missing",
        ):
            await reranker.create(messages=[{"role": "user", "content": "private"}])
        with llm_request_scope(
            kind=RequestKind.COMPILE,
            stream_id="history-a",
            source_sequence=0,
        ):
            assert await shared.create(
                messages=[{"role": "user", "content": "private"}]
            ) == {"ok": True}

    asyncio.run(scenario())
    assert runtime.admitted_llm.observation()["completed_count"] == 1


def test_live_runtime_observer_receives_content_safe_admission_snapshots() -> None:
    events: list[dict[str, object]] = []
    runtime = build_membind_v31_runtime(
        env=_env(),
        policy=AdmissionPolicy.FIFO,
        request_id_prefix="v31-live-snapshots",
        observer=events.append,
        admission_observer=events.append,
        components=_components(),
        prefix_encoder=_prefix_encoder,
    )

    async def scenario() -> None:
        with llm_request_scope(
            kind=RequestKind.COMPILE,
            stream_id="history-a",
            source_sequence=0,
        ):
            await runtime.raw_llm.client.chat.completions.create(
                messages=[{"role": "user", "content": "private prompt"}]
            )

    asyncio.run(scenario())
    snapshots = [event for event in events if event["event_type"] == "admission_snapshot"]
    assert snapshots
    assert snapshots[0]["active_compile_count"] == 1
    assert snapshots[-1]["active_count"] == 0
    assert "private prompt" not in repr(snapshots)


def test_live_runtime_wires_response_observer_and_xgrammar_identity() -> None:
    events: list[dict[str, object]] = []
    runtime = build_membind_v31_runtime(
        env=_env(),
        policy=AdmissionPolicy.FIFO,
        request_id_prefix="v31-live-response-events",
        response_observer=events.append,
        components=_components(),
        prefix_encoder=_prefix_encoder,
    )

    async def scenario() -> None:
        with llm_request_scope(
            kind=RequestKind.FRONTIER,
            stream_id="history-a",
            source_sequence=0,
        ):
            await runtime.raw_llm.client.chat.completions.create(
                messages=[{"role": "user", "content": "private prompt"}],
                max_tokens=16_384,
            )

    asyncio.run(scenario())
    assert len(events) == 1
    assert events[0]["event_type"] == "llm_transport_response"
    assert events[0]["structured_backend_identity"] == "xgrammar"
    assert events[0]["requested_max_tokens"] == 16_384
    assert "private prompt" not in repr(events)


def test_production_prefix_key_is_loaded_from_private_env_not_api_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_eval.membind_v31.live_runtime as module

    observed: dict[str, object] = {}

    def builder(**kwargs):
        observed.update(kwargs)
        return _prefix_encoder

    monkeypatch.setattr(module, "build_production_qwen_prefix_encoder", builder)
    env = _env()
    env["CONSTRUCTION_LLM_API_KEY"] = "unrelated-api-credential"
    runtime = build_membind_v31_runtime(
        env=env,
        policy=AdmissionPolicy.FIFO,
        request_id_prefix="v31-private-trace-key",
        components=_components(),
    )

    assert observed["trace_hmac_key"] == b"k" * 32
    assert "unrelated-api-credential" not in repr(runtime.method_public_identity)
    assert "unrelated-api-credential" not in repr(observed)


@pytest.mark.parametrize(
    "value",
    (None, "", "00", "z" * 64),
)
def test_production_prefix_key_missing_or_invalid_fails_closed(value: str | None) -> None:
    env = _env()
    if value is None:
        env.pop("MEMBIND_V31_TRACE_HMAC_KEY")
    else:
        env["MEMBIND_V31_TRACE_HMAC_KEY"] = value
    with pytest.raises(
        Exception,
        match="trace_hmac_key_(missing|invalid)",
    ):
        build_membind_v31_runtime(
            env=env,
            policy=AdmissionPolicy.FIFO,
            request_id_prefix="v31-invalid-trace-key",
            components=_components(),
        )
