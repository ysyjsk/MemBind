from __future__ import annotations

import io
import json
import os
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest import mock

from mab_quality_v2_final_qa.autoresearch import select_probe_qa
from mab_quality_v2_final_qa.live_adapters import (
    SiliconFlowChatCompletions,
    normalize_siliconflow_chat_request,
)
from mab_quality_v2_final_qa.live_workflow import (
    _execution_methods,
    _overlay_siliconflow_env,
    _prepare_execution_contexts,
    _select_execution_contexts,
)
from mab_quality_v2_final_qa.runtime_gate import (
    RuntimeTopology,
    check_embedding_endpoint,
    check_model_endpoint,
)
from mab_quality_v2_final_qa.siliconflow_runtime import (
    RuntimeComponents,
    SILICONFLOW_HTTP_TIMEOUT_SECONDS,
    build_siliconflow_u0_runtime,
)


SILICONFLOW_URL = "https://api.siliconflow.cn/v1"
CHAT_MODEL = "Qwen/Qwen3-32B"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"


class _Response:
    status = 200

    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class _Opener:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = iter(responses)
        self.requests: list[object] = []

    def open(self, request: object, *, timeout: float):
        self.requests.append(request)
        return next(self.responses)


class SiliconFlowTopologyTests(unittest.TestCase):
    def test_explicit_siliconflow_topology_uses_exact_qwen_models(self) -> None:
        env = {
            "MAB_RUNTIME_PROVIDER": "SILICONFLOW_QWEN",
            "CONSTRUCTION_LLM_BASE_URL": SILICONFLOW_URL,
            "CONSTRUCTION_LLM_MODEL": CHAT_MODEL,
            "QUALITY_LLM_BASE_URL": SILICONFLOW_URL,
            "QUALITY_LLM_MODEL": CHAT_MODEL,
            "EMBEDDING_BASE_URL": SILICONFLOW_URL,
            "EMBEDDING_MODEL": EMBEDDING_MODEL,
            "EMBEDDING_DIM": "1024",
            "NEO4J_URI": "bolt://localhost:7687",
        }
        topology = RuntimeTopology.from_env(env)
        self.assertEqual(topology.provider, "SILICONFLOW_QWEN")
        self.assertEqual(topology.construction.model, CHAT_MODEL)
        self.assertEqual(topology.quality.model, CHAT_MODEL)
        self.assertEqual(topology.embedding.model, EMBEDDING_MODEL)
        self.assertEqual(topology.embedding_dimension, 1024)

    def test_siliconflow_topology_rejects_model_drift(self) -> None:
        env = {
            "MAB_RUNTIME_PROVIDER": "SILICONFLOW_QWEN",
            "CONSTRUCTION_LLM_BASE_URL": SILICONFLOW_URL,
            "CONSTRUCTION_LLM_MODEL": "Qwen/Qwen3-14B",
            "QUALITY_LLM_BASE_URL": SILICONFLOW_URL,
            "QUALITY_LLM_MODEL": CHAT_MODEL,
            "EMBEDDING_BASE_URL": SILICONFLOW_URL,
            "EMBEDDING_MODEL": EMBEDDING_MODEL,
            "EMBEDDING_DIM": "1024",
            "NEO4J_URI": "bolt://localhost:7687",
        }
        with self.assertRaisesRegex(ValueError, "SILICONFLOW_CONSTRUCTION_MODEL_DRIFT"):
            RuntimeTopology.from_env(env)

    def test_process_secret_overlay_does_not_mutate_source_env(self) -> None:
        source = {"NEO4J_URI": "bolt://localhost:7687", "UNCHANGED": "yes"}
        selected = _overlay_siliconflow_env(
            source, {"SILICONFLOW_API_KEY": "test-secret"}
        )
        self.assertNotIn("SILICONFLOW_API_KEY", source)
        self.assertEqual(selected["CONSTRUCTION_LLM_API_KEY"], "test-secret")
        self.assertEqual(selected["QUALITY_LLM_API_KEY"], "test-secret")
        self.assertEqual(selected["EMBEDDING_API_KEY"], "test-secret")
        self.assertEqual(selected["CONSTRUCTION_LLM_MODEL"], CHAT_MODEL)
        self.assertEqual(selected["EMBEDDING_MODEL"], EMBEDDING_MODEL)


class SiliconFlowRuntimeTests(unittest.TestCase):
    def test_u0_runtime_binds_exact_chat_and_embedding_models(self) -> None:
        class Config:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

        class Completions:
            async def create(self, **_kwargs: object) -> object:
                return object()

        class Transport:
            def __init__(self) -> None:
                self.chat = SimpleNamespace(completions=Completions())

        class Qwen:
            def __init__(self, *, config: Config, **kwargs: object) -> None:
                self.config = config
                self.kwargs = kwargs
                self.client = Transport()
                self.raw_calls = 0

            async def _generate_response(self, **_kwargs: object):
                self.raw_calls += 1
                return {}

            async def generate_response(self, *_args: object, **_kwargs: object):
                return {}

        class Embedder:
            def __init__(self, config: Config, client: object = None) -> None:
                self.config = config
                self.client = client

        class Graph:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs
                self.llm_client = kwargs["llm_client"]
                self.clients = SimpleNamespace(llm_client=kwargs["llm_client"])

        class Admission:
            def __init__(
                self,
                *,
                inner: object,
                admission: object,
                request_id_prefix: str,
            ) -> None:
                self.inner = inner
                self.admission = admission
                self.request_id_prefix = request_id_prefix

            async def generate_response(self, *_args: object, **_kwargs: object):
                return {}

        components = RuntimeComponents(
            graphiti_type=Graph,
            llm_config_type=Config,
            qwen_client_type=Qwen,
            embedder_config_type=Config,
            embedder_type=Embedder,
            reranker_type=lambda config, client=None: SimpleNamespace(
                config=config, client=client
            ),
            admitted_client_type=Admission,
            request_admission_type=lambda *, limit: SimpleNamespace(limit=limit),
            openai_client_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        )
        env = {
            "MAB_RUNTIME_PROVIDER": "SILICONFLOW_QWEN",
            "CONSTRUCTION_LLM_BASE_URL": SILICONFLOW_URL,
            "CONSTRUCTION_LLM_MODEL": CHAT_MODEL,
            "CONSTRUCTION_LLM_API_KEY": "test-secret",
            "QUALITY_LLM_BASE_URL": SILICONFLOW_URL,
            "QUALITY_LLM_MODEL": CHAT_MODEL,
            "EMBEDDING_BASE_URL": SILICONFLOW_URL,
            "EMBEDDING_MODEL": EMBEDDING_MODEL,
            "EMBEDDING_DIM": "1024",
            "EMBEDDING_API_KEY": "test-secret",
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "test-password",
            "GRAPHITI_MAX_COROUTINES": "8",
        }

        runtime = build_siliconflow_u0_runtime(
            env=env,
            request_id_prefix="mab-test-u0",
            components=components,
        )

        self.assertEqual(runtime.raw_llm.config.kwargs["model"], CHAT_MODEL)
        embedder = runtime.graphiti.kwargs["embedder"]
        self.assertEqual(embedder.config.kwargs["embedding_model"], EMBEDDING_MODEL)
        self.assertEqual(embedder.config.kwargs["embedding_dim"], 1024)
        self.assertEqual(runtime.public_identity["provider"], "SILICONFLOW_QWEN")
        self.assertEqual(
            runtime.public_identity["http_timeout_seconds"],
            SILICONFLOW_HTTP_TIMEOUT_SECONDS,
        )
        self.assertNotIn("test-secret", json.dumps(runtime.public_identity))
        self.assertIs(runtime.graphiti.llm_client, runtime.admitted_llm)

        observer_runtime = build_siliconflow_u0_runtime(
            env=env,
            request_id_prefix="mab-test-v7-observer",
            components=components,
            requested_max_tokens=8_192,
            http_timeout_seconds=600.0,
        )
        self.assertEqual(
            observer_runtime.raw_llm.config.kwargs["max_tokens"], 8_192
        )
        self.assertEqual(observer_runtime.raw_llm.kwargs["max_tokens"], 8_192)
        self.assertEqual(
            observer_runtime.public_identity["construction"]["requested_max_tokens"],
            8_192,
        )
        self.assertEqual(
            observer_runtime.public_identity["http_timeout_seconds"], 600.0
        )
        self.assertEqual(
            observer_runtime.raw_llm.kwargs["client"].timeout_seconds, 600.0
        )
        assert observer_runtime.public_identity["logical_retry_policy"] == (
            "single_attempt_direct_no_tenacity"
        )
        self.assertFalse(
            hasattr(observer_runtime.raw_llm._generate_response_with_retry, "retry")
        )
        import asyncio

        asyncio.run(
            observer_runtime.raw_llm._generate_response_with_retry(
                [], None, 1, None
            )
        )
        self.assertEqual(observer_runtime.raw_llm.raw_calls, 1)


class SiliconFlowRequestTests(unittest.TestCase):
    def test_chat_transport_observer_records_only_finish_usage_size_and_digest(
        self,
    ) -> None:
        class Completion:
            async def create(self, **_kwargs: object) -> object:
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason="length",
                            message=SimpleNamespace(content="private response body"),
                        )
                    ],
                    usage=SimpleNamespace(prompt_tokens=123, completion_tokens=8192),
                )

        observations: list[dict[str, object]] = []
        transport = SiliconFlowChatCompletions(
            Completion(), response_observer=observations.append
        )
        import asyncio

        asyncio.run(
            transport.create(
                model=CHAT_MODEL,
                messages=[{"role": "user", "content": "private prompt"}],
            )
        )
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["finish_reason"], "length")
        self.assertEqual(observations[0]["prompt_tokens"], 123)
        self.assertEqual(observations[0]["completion_tokens"], 8192)
        self.assertEqual(observations[0]["content_bytes"], 21)
        self.assertEqual(len(str(observations[0]["content_sha256"])), 64)
        encoded = json.dumps(observations[0], sort_keys=True)
        self.assertNotIn("private prompt", encoded)
        self.assertNotIn("private response body", encoded)

    def test_chat_request_maps_thinking_and_removes_vllm_cache_salt(self) -> None:
        request = normalize_siliconflow_chat_request(
            {
                "model": CHAT_MODEL,
                "messages": [{"role": "user", "content": "hello"}],
                "extra_body": {
                    "cache_salt": "not-supported-remotely",
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            }
        )
        self.assertEqual(request["extra_body"], {"enable_thinking": False})
        self.assertNotIn("cache_salt", json.dumps(request))
        self.assertNotIn("chat_template_kwargs", json.dumps(request))

    def test_authenticated_model_probe_accepts_both_exact_ids(self) -> None:
        opener = _Opener(
            [
                _Response(
                    {
                        "data": [
                            {"id": CHAT_MODEL},
                            {"id": EMBEDDING_MODEL},
                        ]
                    }
                )
            ]
        )
        status = check_model_endpoint(
            SILICONFLOW_URL,
            expected_models=(CHAT_MODEL, EMBEDDING_MODEL),
            api_key="test-secret",
            opener=opener,
        )
        self.assertTrue(status.available)
        request = opener.requests[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")

    def test_embedding_probe_requires_exact_model_and_1024_dimensions(self) -> None:
        opener = _Opener(
            [
                _Response(
                    {
                        "model": EMBEDDING_MODEL,
                        "data": [{"embedding": [0.0] * 1024, "index": 0}],
                        "usage": {"prompt_tokens": 2, "total_tokens": 2},
                    }
                )
            ]
        )
        status = check_embedding_endpoint(
            SILICONFLOW_URL,
            model=EMBEDDING_MODEL,
            expected_dimension=1024,
            api_key="test-secret",
            opener=opener,
        )
        self.assertTrue(status.available)
        self.assertEqual(status.dimension, 1024)
        body = json.loads(opener.requests[0].data)
        self.assertEqual(body["model"], EMBEDDING_MODEL)


class StageSelectionTests(unittest.TestCase):
    def test_smoke_runs_only_u0(self) -> None:
        self.assertEqual(_execution_methods("smoke"), ("U0",))
        self.assertEqual(_execution_methods("full"), ("U0", "MEMBIND_V31"))

    def test_smoke_selects_one_context_and_six_deterministic_qa(self) -> None:
        @dataclass(frozen=True)
        class Context:
            context_id: str
            qa_items: tuple[object, ...]

        qa_items = tuple(
            SimpleNamespace(
                qa_pair_id=f"qa-{index:02d}",
                question_type=("single-session" if index % 2 else "knowledge-update"),
            )
            for index in range(10)
        )
        context = Context(context_id="context-1", qa_items=qa_items)
        with mock.patch(
            "mab_quality_v2_final_qa.live_workflow.select_probe_contexts",
            return_value=(context,),
        ), mock.patch(
            "mab_quality_v2_final_qa.live_workflow.select_probe_qa",
            return_value=qa_items[:6],
        ):
            selected = _select_execution_contexts((context,), mode="smoke")
        self.assertEqual(len(selected), 1)
        self.assertEqual(len(selected[0].qa_items), 6)

    def test_smoke_selection_precedes_history_limit(self) -> None:
        @dataclass(frozen=True)
        class Context:
            context_id: str
            qa_items: tuple[object, ...]

        contexts = tuple(
            Context(context_id=f"context-{index}", qa_items=tuple(range(8)))
            for index in range(4)
        )
        selected_context = contexts[2]
        with mock.patch(
            "mab_quality_v2_final_qa.live_workflow.select_probe_contexts",
            side_effect=lambda candidates, count: (
                self.assertEqual(tuple(candidates), contexts),
                (selected_context,),
            )[1],
        ), mock.patch(
            "mab_quality_v2_final_qa.live_workflow.select_probe_qa",
            return_value=tuple(range(6)),
        ):
            selected = _prepare_execution_contexts(
                contexts, history_limit=1, mode="smoke"
            )
        self.assertEqual(selected[0].context_id, "context-2")
        self.assertEqual(selected[0].qa_items, tuple(range(6)))

    def test_full_selection_applies_history_limit_after_mode_choice(self) -> None:
        @dataclass(frozen=True)
        class Context:
            context_id: str
            qa_items: tuple[object, ...]

        contexts = tuple(
            Context(context_id=f"context-{index}", qa_items=tuple())
            for index in range(4)
        )
        selected = _prepare_execution_contexts(
            contexts, history_limit=1, mode="full"
        )
        self.assertEqual(tuple(item.context_id for item in selected), ("context-0",))


if __name__ == "__main__":
    unittest.main()
